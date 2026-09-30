"""Orchestratore principale della pipeline Call Transcriber.

Replica process_call.ps1: stabilizza, estrae audio, trascrive, riassume,
classifica, comprime, pulisce e rigenera gli indici.
"""
from __future__ import annotations

import argparse
import logging
import json
import math
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console as _Console

from scripts.audio import ffmpeg as _ffmpeg
from scripts.llm import common as llm_common
from scripts.llm.providers.base import LlmProvider
from scripts.filesystem import atomic_write_text, safe_name, unique_path
from scripts import jobs, tasks
from scripts.obsidian import frontmatter as fm
from scripts.obsidian import indexes as obs_indexes
from scripts import task_scoring
import scripts.settings as _cfg

_con = _Console()
_classification_logger = logging.getLogger(__name__)

def _step(msg: str) -> None:
    _con.print(f"  [cyan]·[/cyan] {msg}")

def _ok(msg: str, elapsed: float = 0.0) -> None:
    t = f" [dim]{elapsed:.0f}s[/dim]" if elapsed >= 1 else ""
    _con.print(f"  [green]✓[/green] {msg}{t}")

def _warn(msg: str) -> None:
    _con.print(f"  [yellow]![/yellow] {msg}")


def _classification_event(message: str, level: int = logging.INFO) -> None:
    """Scrive gli eventi di classificazione sia in console sia nel log watcher."""
    _classification_logger.log(level, "[classification] %s", message)
    if level >= logging.WARNING:
        _warn(message)
    else:
        print(f"[classification] {message}")

_UTF8 = "utf-8"
_AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".webm", ".wma"}
_VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm"}

# ---------------------------------------------------------------------------
# Provider loading
# ---------------------------------------------------------------------------

def _load_claude() -> LlmProvider:
    from scripts.llm import get_provider
    provider = get_provider()
    if not provider.is_available():
        raise RuntimeError("Provider LLM configurato non disponibile.")
    return provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    return safe_name(name)


def _unique_path(path: Path) -> Path:
    return unique_path(path)


def _archive_processed_source(
    source_path: Path,
    root: Path,
    timestamp: datetime,
    keep_source: bool,
    video_call_dir: Path | None = None,
    video_name: str = "",
) -> Path | None:
    if not source_path.exists():
        return None

    if source_path.suffix.lower() in _VIDEO_EXT and video_call_dir is not None:
        target = _unique_path(video_call_dir / f"{_safe_name(video_name)}{source_path.suffix.lower()}")
        shutil.move(str(source_path), str(target))
        os.utime(target, None)
        return target

    archive_dir = root / "completate" / "archivio"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_name = f"{timestamp.strftime('%Y-%m-%d %H.%M')} - {_safe_name(source_path.name)}"
    target = _unique_path(archive_dir / archived_name)

    if keep_source:
        shutil.copy2(source_path, target)
    else:
        shutil.move(str(source_path), str(target))
    os.utime(target, None)
    return target


def _cleanup_source_archive(root: Path, days: int | None = None) -> int:
    if days is None:
        days = _cfg.SOURCE_ARCHIVE_DAYS
    if days <= 0:
        return 0

    archive_dir = root / "completate" / "archivio"
    if not archive_dir.exists():
        return 0

    cutoff = datetime.now() - timedelta(days=days)
    deleted = 0
    for item in archive_dir.iterdir():
        if not item.is_file():
            continue
        # I video legacy non associati restano disponibili per una migrazione
        # o una verifica manuale; i video nuovi non passano più da qui.
        if item.suffix.lower() in _VIDEO_EXT:
            continue
        if datetime.fromtimestamp(item.stat().st_mtime) >= cutoff:
            continue
        item.unlink()
        deleted += 1
    return deleted


def _read_cached_transcript(transcript_path: Path) -> str:
    if not transcript_path.exists():
        return ""
    transcript = transcript_path.read_text(encoding=_UTF8).strip()
    return transcript


def wait_stable(path: Path, stable_checks: int = 3, delay_seconds: int = 3) -> None:
    deadline = time.monotonic() + _cfg.STABLE_TIMEOUT_SECONDS
    same = 0
    last_size = -1
    last_mtime = 0.0
    while same < stable_checks:
        if time.monotonic() >= deadline:
            raise TimeoutError("Il file non si è stabilizzato entro il timeout.")
        time.sleep(delay_seconds)
        stat = path.stat()
        if stat.st_size == last_size and stat.st_mtime == last_mtime:
            same += 1
        else:
            same = 0
            last_size = stat.st_size
            last_mtime = stat.st_mtime


def _extract_title_from_summary(summary_path: Path) -> str:
    _GENERIC = {
        'contesto', 'decisioni prese', 'punti discussi', 'task e action item',
        'blocchi, dubbi o rischi', 'prossimi passi', 'passaggi ambigui da verificare',
    }
    _, body = fm.parse_frontmatter(summary_path.read_text(encoding=_UTF8))
    for line in body.splitlines():
        m = re.match(r'^##\s+(.+?)\s*$', line)
        if m:
            title = _safe_name(re.sub(r'[#*_`]', '', m.group(1)).strip())
            words = title.split()
            if len(words) > 6:
                title = " ".join(words[:6])
            if title and title.lower() not in _GENERIC:
                return title
    return ""


def _add_people_to_title(title: str, people: list[str]) -> str:
    """Prefissa al titolo i primi due partecipanti (primo nome, esclude MY_NAME)."""
    my_name = " ".join(_cfg.MY_NAME.casefold().split())
    first_names: list[str] = []
    for p in people:
        if not isinstance(p, str) or not p.strip() or " ".join(p.casefold().split()) == my_name:
            continue
        first_names.append(p.split()[0])
        if len(first_names) >= 2:
            break
    missing = [
        fn for fn in first_names
        if not re.search(r'(?i)(^|[\s,;-])' + re.escape(fn) + r'($|[\s,;-])', title)
    ]
    if not missing:
        return title
    return _safe_name(f"{', '.join(missing)}, {title}")


def _set_summary_title(summary_path: Path, title: str) -> None:
    content = summary_path.read_text(encoding=_UTF8).strip()
    fields, body = fm.parse_frontmatter(content)

    body = body.lstrip("\n")
    lines = body.splitlines()
    # rimuovi eventuale # riassunto e ## Titolo preesistenti
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r'^#\s+', lines[0]):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r'^##\s+', lines[0]):
        lines.pop(0)

    new_body = f"# riassunto\n## {title}\n\n" + "\n".join(lines)
    fm.write_with_frontmatter(summary_path, fields, new_body)


def _set_summary_frontmatter(summary_path: Path, timestamp: datetime, task_name: str) -> None:
    fields, body = fm.parse_frontmatter(summary_path.read_text(encoding=_UTF8))
    fields.pop("data", None)
    fields.pop("ora", None)
    fields.pop("task", None)

    if "tags" not in fields:
        fields["tags"] = ["call"]

    ordered: dict = {
        "data": timestamp.strftime("%Y-%m-%d"),
        "ora": timestamp.strftime("%H:%M"),
    }
    if task_name:
        ordered["task"] = f"[[{task_name}]]"
    ordered.update(fields)
    fm.write_with_frontmatter(summary_path, ordered, body)


def _rename_summary(summary_path: Path, title: str) -> Path:
    target = summary_path.parent / (_safe_name(title) + ".md")
    if summary_path != target:
        summary_path.rename(target)
    return target


def _global_index_path(root: Path) -> Path:
    return root / "completate" / "README.md"


def _unassigned_calls_dir(root: Path) -> Path:
    return root / "completate" / _cfg.UNASSIGNED_CALLS_DIR_NAME


def _project_instructions_path() -> Path:
    return Path(__file__).resolve().parent / "summary_context.md"


def _read_text(path: Path, limit: int = 12000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding=_UTF8).strip()[:limit]


def _discover_task_dirs(root: Path) -> list[Path]:
    return tasks.discover_task_dirs(root)


def _task_contexts(task_dirs: list[Path]) -> dict[str, str]:
    return {
        tasks.task_labels(task_dirs)[task_dir]: _read_text(task_dir / "README.md", limit=6000)
        for task_dir in task_dirs
        if (task_dir / "README.md").is_file()
    }


def _log_task_scores(scores: list[task_scoring.TaskScore]) -> None:
    for line in task_scoring.format_scores(scores).splitlines():
        print(f"[task-score] {line}")


def _recognize_task(
    root: Path,
    transcript: str,
    provider: LlmProvider,
    model: str,
) -> Path | None:
    task_dirs = _discover_task_dirs(root)
    if not task_dirs:
        return None

    profiles = task_scoring.load_profiles(task_dirs)
    scores = task_scoring.score_tasks(transcript, profiles)
    _log_task_scores(scores)
    automatic = task_scoring.automatic_winner(scores)
    if automatic:
        return automatic.profile.path

    global_index = _read_text(_global_index_path(root))
    prompt = llm_common.build_task_prompt(
        list(tasks.task_labels(task_dirs).values()),
        transcript=transcript,
        global_index=global_index,
        task_contexts=_task_contexts(task_dirs),
        scoring_evidence=task_scoring.format_scores(scores),
    )
    try:
        answer = provider.invoke_task_classification(prompt, model or provider.default_task_model())
        if llm_common.is_no_task_answer(answer):
            _classification_event(
                f"Scelta semantica: {llm_common.NO_TASK_TOKEN} (fase preliminare)."
            )
            return None
        selected = llm_common.select_task(task_dirs, answer)
        if selected:
            return selected
        _classification_event(
            "Riconoscimento preliminare: output task non riconoscibile; "
            "nessuna assegnazione preliminare, si procede alla classificazione finale.",
            logging.WARNING,
        )
        return None
    except Exception as exc:
        _classification_event(
            "Riconoscimento preliminare task fallito; "
            "nessuna assegnazione preliminare, si procede alla classificazione finale: "
            f"{exc}",
            logging.WARNING,
        )
        return None


def _get_task_dir(
    root: Path,
    summary_path: Path,
    title: str,
    provider: LlmProvider,
    model: str,
    transcript: str = "",
    preliminary_task: Path | None = None,
) -> Path:
    task_root = root / "completate" / "Task"
    if not task_root.exists():
        unassigned_dir = _unassigned_calls_dir(root)
        unassigned_dir.mkdir(parents=True, exist_ok=True)
        _classification_event(
            "Classificazione finale senza task disponibili; "
            f"fallback sicuro in {unassigned_dir.name}.",
            logging.WARNING,
        )
        return unassigned_dir

    task_dirs = _discover_task_dirs(root)
    if not task_dirs:
        unassigned_dir = _unassigned_calls_dir(root)
        unassigned_dir.mkdir(parents=True, exist_ok=True)
        _classification_event(
            "Classificazione finale senza task disponibili; "
            f"fallback sicuro in {unassigned_dir.name}.",
            logging.WARNING,
        )
        return unassigned_dir

    summary = summary_path.read_text(encoding=_UTF8)
    global_index = _read_text(_global_index_path(root))
    task_names = list(tasks.task_labels(task_dirs).values())
    profiles = task_scoring.load_profiles(task_dirs)
    scoring_text = transcript or task_scoring.classification_text(title, summary)
    scores = task_scoring.score_tasks(scoring_text, profiles)
    _log_task_scores(scores)
    automatic = task_scoring.automatic_winner(scores)
    if automatic:
        return automatic.profile.path

    prompt = llm_common.build_task_prompt(
        task_names,
        title,
        summary,
        transcript=transcript,
        global_index=global_index,
        task_contexts=_task_contexts(task_dirs),
        preliminary_task=tasks.task_labels(task_dirs).get(preliminary_task, ""),
        scoring_evidence=task_scoring.format_scores(scores),
    )
    try:
        answer = provider.invoke_task_classification(prompt, model or provider.default_task_model())
        if llm_common.is_no_task_answer(answer):
            unassigned_dir = _unassigned_calls_dir(root)
            unassigned_dir.mkdir(parents=True, exist_ok=True)
            _classification_event(
                f"Scelta semantica: {llm_common.NO_TASK_TOKEN} (fase finale)."
            )
            return unassigned_dir
        selected = llm_common.select_task(task_dirs, answer)
        if selected:
            return selected
        _classification_event(
            "Classificazione finale: output non riconoscibile; "
            f"fallback sicuro in {_cfg.UNASSIGNED_CALLS_DIR_NAME}.",
            logging.WARNING,
        )
    except Exception as exc:
        _classification_event(
            "Classificazione finale task fallita; "
            f"fallback sicuro in {_cfg.UNASSIGNED_CALLS_DIR_NAME}: {exc}",
            logging.WARNING,
        )
    unassigned_dir = _unassigned_calls_dir(root)
    unassigned_dir.mkdir(parents=True, exist_ok=True)
    return unassigned_dir


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

def _invoke_summary(
    provider: LlmProvider,
    transcript_path: Path,
    output_path: Path,
    prompt_path: Path,
    model: str,
    project_instructions_path: Path | None = None,
    global_index_path: Path | None = None,
    task_readme_path: Path | None = None,
) -> None:
    transcript = transcript_path.read_text(encoding=_UTF8)
    prompt = llm_common.build_summary_prompt(
        prompt_path,
        transcript,
        project_instructions_path=project_instructions_path,
        global_index_path=global_index_path,
        task_readme_path=task_readme_path,
    )
    attempts = max(1, _cfg.CLAUDE_SUMMARY_RETRIES + 1)
    retry_note = ""
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        current_prompt = prompt if not retry_note else f"{prompt}\n\n---\n\n{retry_note}"
        raw = ""
        try:
            raw = provider.invoke_summary(current_prompt, model or provider.default_summary_model())
            if not raw:
                raise ValueError("Il provider non ha restituito un riassunto.")
            llm_common.validate_summary(raw)
            clean = llm_common.clean_markdown(raw)
        except (RuntimeError, TimeoutError, ConnectionError, ValueError) as exc:
            last_error = exc
            if isinstance(exc, ValueError):
                retry_note = (
                    f"Correggi il formato: {exc}. Restituisci il riassunto completo.\n"
                    f"Risposta precedente:\n{raw}"
                )
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), _cfg.LLM_RETRY_DELAY_SECONDS))
            continue
        atomic_write_text(output_path, clean)
        return

    raise last_error or RuntimeError("Generazione riassunto fallita.")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process(
    input_path: Path,
    root: Path | None = None,
    keep_video: bool = False,
    archive_max_mb: float | None = None,
    summary_model: str = "",
    task_model: str = "",
) -> dict:
    root = (root or _cfg.VAULT_ROOT).resolve()
    resolved = input_path.resolve()
    max_mb = _cfg.ARCHIVE_MAX_MB if archive_max_mb is None else archive_max_mb
    if not math.isfinite(max_mb) or max_mb <= 0:
        raise ValueError("archive_max_mb deve essere positivo e finito.")
    if resolved.suffix.lower() not in _AUDIO_EXT | _VIDEO_EXT:
        raise ValueError(f"Estensione non supportata: {resolved.suffix}")
    if resolved.exists():
        wait_stable(resolved)
    config = {
        "archive_max_mb": max_mb, "summary_model": summary_model or _cfg.CLAUDE_SUMMARY_MODEL,
        "task_model": task_model or _cfg.CLAUDE_TASK_MODEL,
        "provider": _cfg.LLM_PROVIDER, "transcription_model": _cfg.GROQ_WHISPER_MODEL,
    }
    with jobs.vault_lock(root):
        state = jobs.load_job(root, resolved, config)
        try:
            return _process_job(root, state)
        except Exception as exc:
            state["last_error"] = type(exc).__name__
            jobs.save_job(root, state)
            raise


def _process_job(root: Path, state: dict) -> dict:
    source = Path(state["source"])
    work_dir = root / ".pipeline" / "work" / state["id"]
    final_dir = Path(state["final_dir"]) if state.get("final_dir") else None
    call_dir = final_dir if final_dir and final_dir.exists() else work_dir
    if final_dir and final_dir.exists():
        marker = final_dir / ".call-job.json"
        if not marker.is_file() or json.loads(marker.read_text(encoding=_UTF8)).get("id") != state["id"]:
            raise RuntimeError("La destinazione appartiene a un'altra lavorazione.")
        state["committed"] = True
    call_dir.mkdir(parents=True, exist_ok=True)
    config = state["config"]
    timestamp = datetime.fromtimestamp(state["timestamp"])
    is_video = source.suffix.lower() in _VIDEO_EXT

    def checkpoint(**values):
        state.update(values)
        state.pop("last_error", None)
        jobs.save_job(root, state)

    if not state.get("committed"):
        provider = _load_claude()
        audio = call_dir / "audio.m4a"
        if not state.get("audio_ready") or not audio.is_file():
            temp_audio = call_dir / "audio.part.m4a"
            if is_video:
                _ffmpeg.extract_audio(source, temp_audio)
            elif source.suffix.lower() == ".m4a":
                shutil.copy2(source, temp_audio)
            else:
                _ffmpeg.convert_to_m4a(source, temp_audio)
            if temp_audio.stat().st_size == 0:
                raise ValueError("Audio estratto vuoto.")
            temp_audio.replace(audio)
            checkpoint(audio_ready=True)

        transcript_path = call_dir / "trascrizione.txt"
        if not state.get("transcript_ready"):
            from scripts.transcribe_with_groq import transcribe
            transcript = transcribe(audio, transcript_path, model=config["transcription_model"])
            if not transcript.strip():
                raise ValueError("Trascrizione vuota: riassunto non generato.")
            atomic_write_text(transcript_path, transcript)
            checkpoint(transcript_ready=True, transcript_sha256=jobs.fingerprint(transcript_path))
        transcript = _read_cached_transcript(transcript_path)
        if not transcript:
            raise ValueError("Checkpoint trascrizione mancante o vuoto.")
        if state.get("transcript_sha256") != jobs.fingerprint(transcript_path):
            raise ValueError("La trascrizione del checkpoint è stata alterata.")

        if "preliminary_task" not in state:
            preliminary = _recognize_task(root, transcript, provider, config["task_model"])
            checkpoint(preliminary_task=str(preliminary) if preliminary else "")
        preliminary = Path(state["preliminary_task"]) if state["preliminary_task"] else None
        summary_path = call_dir / state.get("summary_name", "riassunto.md")
        if not summary_path.exists() and state.get("summary_name"):
            summary_path = call_dir / "riassunto.md"
        if not state.get("summary_ready"):
            _invoke_summary(provider, transcript_path, summary_path,
                            Path(__file__).parent / "prompt_riassunto_call.md", config["summary_model"],
                            _project_instructions_path(), _global_index_path(root),
                            preliminary / "README.md" if preliminary else None)
            checkpoint(summary_ready=True)
        if not summary_path.is_file():
            raise ValueError("Checkpoint riassunto mancante.")
        if not state.get("task_dir"):
            title = _extract_title_from_summary(summary_path) or _safe_name(source.stem)
            title = _safe_name(_add_people_to_title(title, fm.get_people(summary_path)))
            task_dir = _get_task_dir(root, summary_path, title, provider, config["task_model"],
                                     transcript=transcript, preliminary_task=preliminary)
            labels = tasks.task_labels(_discover_task_dirs(root))
            task_name = labels.get(task_dir, "")
            checkpoint(task_dir=str(task_dir), title=title, task_name=task_name)
        task_dir = Path(state["task_dir"])
        if not state.get("final_dir"):
            final_name = _safe_name(f"{timestamp.strftime('%Y-%m-%d %H.%M')} - {state['title']}")
            # An explicit suffix-free directory search avoids treating title dots as file extensions.
            candidate = task_dir / final_name
            index = 2
            while candidate.exists():
                candidate = task_dir / f"{final_name} ({index})"
                index += 1
            checkpoint(final_dir=str(candidate))
        final_dir = Path(state["final_dir"])
        title = re.sub(r'^\d{4}-\d{2}-\d{2}\s+\d{2}\.\d{2}\s+-\s+', '', final_dir.name)
        checkpoint(summary_name=_safe_name(title) + ".md")
        _set_summary_title(summary_path, title)
        _set_summary_frontmatter(summary_path, timestamp, state["task_name"])
        summary_path = _rename_summary(summary_path, title)
        archive_audio = call_dir / "audio_compresso.m4a"
        if not state.get("compressed") or not archive_audio.is_file():
            _ffmpeg.compress_audio(audio, archive_audio, config["archive_max_mb"], _ffmpeg.get_duration(audio))
            checkpoint(compressed=True)
        if not 0 < archive_audio.stat().st_size <= config["archive_max_mb"] * 1_000_000:
            raise ValueError("Audio archivio vuoto o oltre la soglia.")
        atomic_write_text(call_dir / ".call-job.json", json.dumps({"id": state["id"]}))
        task_dir.mkdir(parents=True, exist_ok=True)
        call_dir.rename(final_dir)
        call_dir = final_dir
        checkpoint(committed=True)

    summary_path = call_dir / state["summary_name"]
    archive_audio = call_dir / "audio_compresso.m4a"
    transcript_path = call_dir / "trascrizione.txt"
    for path in (summary_path, archive_audio, transcript_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Artefatto confermato mancante: {path.name}")
    task_dir = Path(state["task_dir"])
    if not state.get("source_target"):
        if is_video:
            target = unique_path(call_dir / f"{summary_path.stem}{source.suffix.lower()}")
        else:
            target = unique_path(root / "completate" / "archivio" /
                                 f"{timestamp.strftime('%Y-%m-%d %H.%M')} - {_safe_name(source.name)}")
        checkpoint(source_target=str(target))
    target = Path(state["source_target"])
    if not state.get("source_archived"):
        if source.exists():
            if target.exists():
                raise FileExistsError("Destinazione sorgente già occupata.")
            if jobs.fingerprint(source) != state["sha256"]:
                raise RuntimeError("La sorgente è cambiata durante l'elaborazione; non verrà spostata.")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        elif not target.is_file() or jobs.fingerprint(target) != state["sha256"]:
            raise RuntimeError("Sorgente non trovata né nella destinazione prevista.")
        os.utime(target, None)
        checkpoint(source_archived=True)
    (call_dir / "audio.m4a").unlink(missing_ok=True)
    if not state.get("indexed"):
        obs_indexes.rebuild(root)
        checkpoint(indexed=True)
    if not state.get("kanban_done"):
        if task_dir != _unassigned_calls_dir(root):
            from scripts.update_project_kanban import update_from_summary
            update_from_summary(summary_path, task_dir)
        checkpoint(kanban_done=True)
    result = {
        "call_directory": str(call_dir), "task_directory": str(task_dir),
        "audio": str(archive_audio), "transcript": str(transcript_path),
        "summary": str(summary_path), "source_archive": str(target),
        "provider": config["provider"], "job_id": state["id"],
    }
    checkpoint(complete=True, result=result)
    # Maintenance is independent; it never moves the call returned by this operation.
    try:
        from scripts.archive_old_calls import archive
        archived = archive(root, excluded_call_dirs=jobs.pending_call_dirs(root) | {call_dir})
        _cleanup_source_archive(root)
        if archived["archived"]:
            obs_indexes.rebuild(root)
    except Exception:
        logging.exception("Manutenzione differita; call %s completata", state["id"])
    _ok(f"Completato: {call_dir.name}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Processa una registrazione audio/video.")
    parser.add_argument("--input-path", required=True, type=Path)
    parser.add_argument("--root-path", type=Path, default=None)
    parser.add_argument("--keep-video", action="store_true")
    parser.add_argument("--archive-max-mb", type=float, default=None)
    parser.add_argument("--summary-model", default="")
    parser.add_argument("--task-model", default="")
    args = parser.parse_args()

    result = process(
        input_path=args.input_path,
        root=args.root_path,
        keep_video=args.keep_video,
        archive_max_mb=args.archive_max_mb,
        summary_model=args.summary_model,
        task_model=args.task_model,
    )
    for k, v in result.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
