"""Orchestratore principale della pipeline Call Transcriber.

Replica process_call.ps1: stabilizza, estrae audio, trascrive, riassume,
classifica, comprime, pulisce e rigenera gli indici.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console as _Console

from scripts.audio import ffmpeg as _ffmpeg
from scripts.llm import common as llm_common
from scripts.llm.providers.claude import ClaudeProvider
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

def _load_claude() -> ClaudeProvider:
    provider = ClaudeProvider()
    if not provider.is_available():
        raise RuntimeError("Claude CLI non disponibile.")
    return provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    invalid = set(r'\/:*?"<>|')
    safe = "".join('-' if c in invalid else c for c in name)
    return re.sub(r'\s+', ' ', safe).strip()


def _to_kebab(value: str) -> str:
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', value.lower())).strip('-')


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    idx = 2
    while True:
        candidate = path.parent / f"{path.name} ({idx})"
        if not candidate.exists():
            return candidate
        idx += 1


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
    same = 0
    last_size = -1
    last_mtime = 0.0
    while same < stable_checks:
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
    my_parts = {p.lower() for p in _cfg.MY_NAME.split()} if _cfg.MY_NAME else set()
    first_names: list[str] = []
    for p in people:
        if {w.lower() for w in p.split()} & my_parts:
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
    return f"{', '.join(missing)}, {title}"


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
        "ora": f'"{timestamp.strftime("%H:%M")}"',
    }
    if task_name:
        ordered["task"] = f'"[[{task_name}]]"'
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
    return Path(__file__).resolve().parent.parent / "README.md"


def _read_text(path: Path, limit: int = 12000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding=_UTF8).strip()[:limit]


def _is_task_group(path: Path) -> bool:
    name = path.name.lower()
    if name in {"archivio", "archiviati", "progetti_archiviati"}:
        return True
    return any(child.is_dir() and (child / "README.md").is_file() for child in path.iterdir())


def _is_call_directory(path: Path) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}\.\d{2}\s+-\s+", path.name))


def _is_unassigned_subtree(path: Path, task_root: Path) -> bool:
    try:
        relative_parts = path.relative_to(task_root).parts
    except ValueError:
        return False
    return any(
        part.casefold() == _cfg.UNASSIGNED_CALLS_DIR_NAME.casefold()
        for part in relative_parts
    )


def _discover_task_dirs(root: Path) -> list[Path]:
    """Trova task attive e archiviate, incluse eventuali task annidate."""
    task_root = root / "completate" / "Task"
    if not task_root.exists():
        return []

    candidates: set[Path] = set()
    for readme in task_root.rglob("README.md"):
        task_dir = readme.parent
        if (
            task_dir == task_root
            or _is_unassigned_subtree(task_dir, task_root)
            or _is_task_group(task_dir)
        ):
            continue
        if _is_call_directory(task_dir):
            continue
        candidates.add(task_dir)

    # Mantiene assegnabili anche task nuove il cui README non è ancora stato creato.
    for task_dir in task_root.iterdir():
        if (
            not task_dir.is_dir()
            or _is_unassigned_subtree(task_dir, task_root)
            or _is_task_group(task_dir)
        ):
            continue
        candidates.add(task_dir)
        for nested in task_dir.iterdir():
            if (
                nested.is_dir()
                and not _is_unassigned_subtree(nested, task_root)
                and (nested / "README.md").is_file()
                and not _is_task_group(nested)
            ):
                candidates.add(nested)

    return sorted(candidates, key=lambda path: (path.name.lower(), str(path).lower()))


def _task_contexts(task_dirs: list[Path]) -> dict[str, str]:
    return {
        task_dir.name: _read_text(task_dir / "README.md", limit=6000)
        for task_dir in task_dirs
        if (task_dir / "README.md").is_file()
    }


def _log_task_scores(scores: list[task_scoring.TaskScore]) -> None:
    for line in task_scoring.format_scores(scores).splitlines():
        print(f"[task-score] {line}")


def _recognize_task(
    root: Path,
    transcript: str,
    provider: ClaudeProvider,
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
        [task_dir.name for task_dir in task_dirs],
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
    provider: ClaudeProvider,
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

    tasks = _discover_task_dirs(root)
    if not tasks:
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
    task_names = [task.name for task in tasks]
    profiles = task_scoring.load_profiles(tasks)
    scoring_text = task_scoring.classification_text(title, summary, transcript)
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
        task_contexts=_task_contexts(tasks),
        preliminary_task=preliminary_task.name if preliminary_task else "",
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
        selected = llm_common.select_task(tasks, answer)
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
    provider: ClaudeProvider,
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
        raw = provider.invoke_summary(current_prompt, model or provider.default_summary_model())
        try:
            if not raw:
                raise RuntimeError("Il provider non ha restituito un riassunto.")
            clean = llm_common.clean_markdown(raw)
            if not clean:
                raise RuntimeError("Il provider non ha restituito Markdown valido.")
            llm_common.validate_summary(clean)
            output_path.write_text(clean, encoding=_UTF8)
            return
        except Exception as exc:
            last_error = exc
            retry_note = (
                "La risposta precedente non rispetta il formato richiesto.\n"
                f"Errore di validazione: {exc}\n\n"
                "Rigenera il riassunto completo rispettando esattamente il formato richiesto. "
                "Restituisci solo Markdown valido, senza spiegazioni o blocchi di codice.\n\n"
                f"Risposta precedente:\n{raw or ''}"
            )

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
    if archive_max_mb is None:
        archive_max_mb = _cfg.ARCHIVE_MAX_MB
    if root is None:
        root = _cfg.VAULT_ROOT

    resolved = input_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"File non trovato: {input_path}")

    t0 = time.perf_counter()
    size_mb = resolved.stat().st_size / 1_000_000
    _con.rule(f"[bold]Call Transcriber[/bold]  [dim]{resolved.name} ({size_mb:.1f} MB)[/dim]")

    with _con.status("  Attesa stabilizzazione file..."):
        wait_stable(resolved)
    _ok("File stabile")

    ext = resolved.suffix.lower()
    if ext not in _AUDIO_EXT and ext not in _VIDEO_EXT:
        raise ValueError(f"Estensione non supportata: {ext}")

    is_video = ext in _VIDEO_EXT
    timestamp = datetime.fromtimestamp(resolved.stat().st_mtime)
    call_name = f"{timestamp.strftime('%Y-%m-%d %H.%M')} - {_safe_name(resolved.stem)}"
    call_dir = root / "completate" / call_name
    call_dir.mkdir(parents=True, exist_ok=True)

    audio_path = call_dir / "audio.m4a"
    archive_audio_path = call_dir / "audio_compresso.m4a"

    if is_video:
        with _con.status("  Estrazione audio dal video..."):
            _ffmpeg.extract_audio(resolved, audio_path)
        _ok("Audio estratto")
    elif ext == ".m4a":
        shutil.copy2(resolved, call_dir / f"audio_originale{ext}")
        shutil.copy2(resolved, audio_path)
    else:
        with _con.status(f"  Conversione {ext} → m4a..."):
            shutil.copy2(resolved, call_dir / f"audio_originale{ext}")
            _ffmpeg.convert_to_m4a(resolved, audio_path)
        _ok(f"Audio convertito")

    transcript_path = call_dir / "trascrizione.txt"
    summary_path = call_dir / "riassunto.md"
    prompt_path = Path(__file__).parent / "prompt_riassunto_call.md"
    project_instructions_path = _project_instructions_path()
    global_index_path = _global_index_path(root)

    t = time.perf_counter()
    transcript_text = _read_cached_transcript(transcript_path)
    if transcript_text:
        _ok(f"Trascrizione già presente ({len(transcript_text)} caratteri)")
    else:
        from scripts.transcribe_with_groq import transcribe
        with _con.status(f"  Trascrizione Whisper ({_cfg.GROQ_WHISPER_MODEL})..."):
            transcript_text = transcribe(audio_path, transcript_path)
        _ok(f"Trascrizione completata ({len(transcript_text)} caratteri)", time.perf_counter() - t)

    claude = _load_claude()

    t = time.perf_counter()
    with _con.status("  Riconoscimento preliminare task..."):
        preliminary_task = _recognize_task(
            root,
            transcript_text,
            claude,
            task_model,
        )
    if preliminary_task:
        _ok(f"Task preliminare: [bold]{preliminary_task.name}[/bold]")
    else:
        _ok("Task preliminare non riconosciuta", time.perf_counter() - t)

    t = time.perf_counter()
    with _con.status("  Generazione riassunto (Claude)..."):
        _invoke_summary(
            provider=claude,
            transcript_path=transcript_path,
            output_path=summary_path,
            prompt_path=prompt_path,
            model=summary_model,
            project_instructions_path=project_instructions_path,
            global_index_path=global_index_path,
            task_readme_path=(preliminary_task / "README.md") if preliminary_task else None,
        )
    _ok("Riassunto generato", time.perf_counter() - t)

    context_title = _extract_title_from_summary(summary_path)
    if not context_title:
        context_title = _safe_name(resolved.stem)
    people = fm.get_people(summary_path)
    context_title = _add_people_to_title(context_title, people)
    _step(f"Titolo: [italic]{context_title}[/italic]")

    _set_summary_title(summary_path, context_title)

    final_call_name = f"{timestamp.strftime('%Y-%m-%d %H.%M')} - {context_title}"
    t = time.perf_counter()
    with _con.status("  Classificazione task..."):
        task_dir = _get_task_dir(
            root,
            summary_path,
            context_title,
            claude,
            task_model,
            transcript=transcript_text,
            preliminary_task=preliminary_task,
        )
    task_name = (
        task_dir.name
        if task_dir not in {root / "completate" / "Task", _unassigned_calls_dir(root)}
        else ""
    )
    _ok(f"Task: [bold]{task_dir.name}[/bold]", time.perf_counter() - t)

    final_call_dir = _unique_path(task_dir / final_call_name)
    call_dir.rename(final_call_dir)
    call_dir = final_call_dir
    audio_path = call_dir / "audio.m4a"
    archive_audio_path = call_dir / "audio_compresso.m4a"
    transcript_path = call_dir / "trascrizione.txt"
    summary_path = call_dir / "riassunto.md"

    _set_summary_frontmatter(summary_path, timestamp, task_name)

    title_from_dir = re.sub(r'^\d{4}-\d{2}-\d{2}\s+\d{2}\.\d{2}\s+-\s+', '', call_dir.name).strip()
    summary_path = _rename_summary(summary_path, title_from_dir)

    t = time.perf_counter()
    with _con.status(f"  Compressione audio (max {archive_max_mb} MB)..."):
        duration = _ffmpeg.get_duration(audio_path)
        _ffmpeg.compress_audio(audio_path, archive_audio_path, archive_max_mb, duration)
    compressed_mb = archive_audio_path.stat().st_size / 1_000_000
    _ok(f"Audio compresso: {compressed_mb:.1f} MB", time.perf_counter() - t)

    keep_names = {archive_audio_path.name, summary_path.name, transcript_path.name}
    for item in list(call_dir.iterdir()):
        if item.name not in keep_names:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    source_archive_path = _archive_processed_source(
        resolved,
        root,
        timestamp,
        keep_source=(is_video and keep_video),
        video_call_dir=call_dir if is_video else None,
        video_name=summary_path.stem if is_video else "",
    )
    if source_archive_path:
        if is_video:
            _ok(f"Video salvato nella call: {source_archive_path.name}")
        else:
            _ok(f"Sorgente archiviato: {source_archive_path.name}")

    deleted_sources = _cleanup_source_archive(root)
    if deleted_sources:
        _ok(f"Sorgenti archiviati rimossi: {deleted_sources}")

    with _con.status("  Archiviazione call vecchie..."):
        from scripts.archive_old_calls import archive as archive_old_calls
        archive_result = archive_old_calls(root)
    if archive_result["archived"]:
        _ok(f"Call archiviate: {archive_result['archived']}")

    with _con.status("  Rigenerazione indici Obsidian..."):
        obs_indexes.rebuild(root)
    _ok("Indici aggiornati")

    kanban_ok = False
    with _con.status("  Aggiornamento Kanban..."):
        try:
            from scripts.update_project_kanban import update_from_summary
            if task_dir.exists() and task_dir != _unassigned_calls_dir(root):
                update_from_summary(summary_path, task_dir)
                kanban_ok = True
            else:
                _step("Kanban non applicabile: call senza progetto")
        except Exception as exc:
            _warn(f"Kanban non aggiornata (non bloccante): {exc}")
    if kanban_ok:
        _ok("Kanban aggiornata")

    _con.rule(f"[bold green]Completato[/bold green]  [dim]{call_dir.name}[/dim]  [dim]{time.perf_counter() - t0:.0f}s totali[/dim]")

    return {
        "call_directory": str(call_dir),
        "task_directory": str(task_dir),
        "audio": str(archive_audio_path),
        "transcript": str(transcript_path),
        "summary": str(summary_path),
        "source_archive": str(source_archive_path) if source_archive_path else "",
        "provider": "claude",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Processa una registrazione audio/video.")
    parser.add_argument("--input-path", required=True, type=Path)
    parser.add_argument("--root-path", type=Path, default=None)
    parser.add_argument("--keep-video", action="store_true")
    parser.add_argument("--archive-max-mb", type=float, default=19.0)
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
