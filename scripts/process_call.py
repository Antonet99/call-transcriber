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
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from rich.text import Text

from scripts.audio import ffmpeg as _ffmpeg
from scripts.llm import common as llm_common
from scripts.llm.providers.base import LlmProvider
from scripts.filesystem import atomic_write_text, safe_name, unique_path
from scripts import jobs, tasks
from scripts.obsidian import frontmatter as fm
from scripts.obsidian import indexes as obs_indexes
from scripts import task_scoring
import scripts.settings as _cfg
from scripts.terminal import console as _con, panel, phase, status, completion_details

_classification_logger = logging.getLogger(__name__)

def _step(msg: str) -> None:
    _con.print(f"  [cyan]·[/cyan] {msg}")

def _ok(msg: str, elapsed: float = 0.0) -> None:
    t = f" {elapsed:.0f}s" if elapsed >= 1 else ""
    _con.print(Text.assemble(("  [OK] ", "bold #86efac"), (msg, "#e2e8f0"), (t, "#93c5fd")))

def _warn(msg: str) -> None:
    _con.print(f"  [yellow]![/yellow] {msg}")


def _classification_event(message: str, level: int = logging.INFO) -> None:
    """Scrive gli eventi di classificazione sia in console sia nel log watcher."""
    _classification_logger.log(level, "[classification] %s", message)
    if level >= logging.WARNING:
        _warn("Classificazione incerta: uso Senza progetto. Dettagli nel log." if message.startswith("Classificazione finale") else "Riconoscimento preliminare non disponibile; proseguo con la classificazione finale.")

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
    same = 0
    last_size = -1
    last_mtime = 0.0
    with status(f"Attendo che il file si stabilizzi: {path.name}"):
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
    return [path for path in tasks.discover_task_dirs(root) if not tasks.is_archived_task(path)]


def _task_contexts(task_dirs: list[Path]) -> dict[str, str]:
    return {
        tasks.task_labels(task_dirs)[task_dir]: _read_text(task_dir / "README.md", limit=6000)
        for task_dir in task_dirs
        if (task_dir / "README.md").is_file()
    }


def _log_task_scores(scores: list[task_scoring.TaskScore]) -> None:
    for line in task_scoring.format_scores(scores).splitlines():
        _classification_logger.info("[task-score] %s", line)


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


_RECORDING_ID = re.compile(r"^[0-9a-f]{24}$")
_RECORDING_HASH = re.compile(r"^[0-9a-f]{64}$")
_CALL_DIRECTORY = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}\.\d{2}\s+-\s+(.+)$")


def _context_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Percorso {label} del contesto registrazione mancante.")
    path = Path(os.path.abspath(value))
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Il percorso {label} è fuori dalla root.") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Il percorso {label} contiene un collegamento simbolico.")
    if path.resolve(strict=False) != path:
        raise ValueError(f"Il percorso {label} non è canonico.")
    return path


def _read_recording_json(path: Path, label: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} mancante o non valido.")
    try:
        value = json.loads(path.read_text(encoding=_UTF8))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} non leggibile.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} non valido.")
    return value


def _validate_recording_context(root: Path, input_path: Path, context: dict) -> dict:
    if not isinstance(context, dict):
        raise ValueError("Il contesto registrazione non è valido.")
    recording_id = context.get("recording_id")
    file_hash = context.get("sha256")
    category = context.get("category")
    timestamp = context.get("timestamp")
    if not isinstance(recording_id, str) or not _RECORDING_ID.fullmatch(recording_id):
        raise ValueError("recording_id deve contenere 24 caratteri esadecimali minuscoli.")
    if not isinstance(file_hash, str) or not _RECORDING_HASH.fullmatch(file_hash):
        raise ValueError("sha256 del contesto registrazione non valido.")
    if category not in {"saved", "trash"}:
        raise ValueError("La categoria registrazione deve essere saved o trash.")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
        raise ValueError("Timestamp del contesto registrazione non valido.")
    try:
        datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("Timestamp del contesto registrazione non valido.") from exc

    source = _context_path(root, str(Path(os.path.abspath(input_path))), "sorgente")
    if source.suffix.lower() not in _AUDIO_EXT | _VIDEO_EXT:
        raise ValueError(f"Estensione non supportata: {source.suffix}")
    if source.exists() and (not source.is_file() or source.is_symlink()):
        raise ValueError("La sorgente deve essere un file registrazione regolare.")

    if category == "saved":
        existing_dir = _context_path(root, context.get("existing_dir"), "existing_dir")
        task_dir = _context_path(root, context.get("task_dir"), "task_dir")
        title = context.get("title")
        task_name = context.get("task_name", "")
        if not isinstance(title, str) or not title.strip() or not isinstance(task_name, str):
            raise ValueError("Titolo o task_name del contesto saved non validi.")
        match = _CALL_DIRECTORY.fullmatch(existing_dir.name)
        if not match or match.group(1) != title or not existing_dir.is_dir():
            raise ValueError("Titolo e cartella della registrazione salvata non corrispondono.")
        if source.parent != existing_dir:
            raise ValueError("La sorgente saved deve essere direttamente nella cartella call.")
        unassigned = root / "completate" / _cfg.UNASSIGNED_CALLS_DIR_NAME
        valid_tasks = set(tasks.discover_task_dirs(root))
        if task_dir != unassigned and task_dir not in valid_tasks:
            raise ValueError("task_dir non identifica una task del vault.")
        if task_dir == unassigned:
            if task_name:
                raise ValueError("Senza progetto deve avere task_name vuoto.")
        elif tasks.task_labels(list(valid_tasks)).get(task_dir) != task_name:
            raise ValueError("task_name non corrisponde alla label del progetto.")
        if existing_dir.parent != task_dir and existing_dir.parent != task_dir / "archivio":
            raise ValueError("La cartella saved non appartiene al task indicato.")
        marker_path = existing_dir / ".recording-job.json"
        expected_actions = {"save", "processed"}
        marker = _read_recording_json(marker_path, "Marker della registrazione salvata")
        if marker.get("id") != recording_id or marker.get("sha256") != file_hash or marker.get("action") not in expected_actions:
            raise ValueError("Il marker non corrisponde alla registrazione salvata.")
        result = dict(context)
        result.update({
            "recording_id": recording_id,
            "sha256": file_hash,
            "category": category,
            "timestamp": float(timestamp),
            "source": str(source),
            "existing_dir": str(existing_dir),
            "task_dir": str(task_dir),
            "title": title,
            "task_name": task_name,
            "origin_processed": marker.get("action") == "processed",
            "archive_audio_name": (
                "audio_elaborato.m4a"
                if source.name.casefold() == "audio_compresso.m4a"
                else "audio_compresso.m4a"
            ),
        })
        return result

    trash_dir = _context_path(root, str(root / "cestino_registrazioni" / recording_id), "cestino")
    if source.parent != trash_dir or not trash_dir.is_dir():
        raise ValueError("La sorgente trash non appartiene alla cartella del suo id.")
    marker = _read_recording_json(trash_dir / ".recording-job.json", "Marker della registrazione scartata")
    metadata = _read_recording_json(trash_dir / "metadata.json", "Metadati della registrazione scartata")
    if (
        marker.get("id") != recording_id
        or marker.get("sha256") != file_hash
        or marker.get("action") not in {"discard", "processed"}
        or metadata.get("id") != recording_id
        or metadata.get("sha256") != file_hash
        or metadata.get("name") != source.name
    ):
        raise ValueError("Marker o metadati non corrispondono alla registrazione scartata.")
    return {
        "recording_id": recording_id,
        "sha256": file_hash,
        "category": category,
        "timestamp": float(timestamp),
        "source": str(source),
        "origin_processed": marker.get("action") == "processed",
        "archive_audio_name": "audio_compresso.m4a",
    }


def _saved_summary_path(context: dict) -> Path:
    return Path(context["existing_dir"]) / (_safe_name(context["title"]) + ".md")


def _validate_saved_targets(context: dict, job_id: str) -> dict:
    call_dir = Path(context["existing_dir"])
    archive_audio_name = context.get("archive_audio_name", "audio_compresso.m4a")
    if archive_audio_name not in {"audio_compresso.m4a", "audio_elaborato.m4a"}:
        raise ValueError("Nome dell'audio archivio non valido.")
    marker_path = call_dir / ".call-job.json"
    if marker_path.is_symlink():
        raise ValueError("Il marker pipeline della destinazione è un collegamento simbolico.")
    owner = _read_recording_json(marker_path, "Marker pipeline") if marker_path.exists() else None
    if owner and owner.get("id") != job_id:
        raise RuntimeError("La cartella call appartiene a un'altra lavorazione.")

    summary_path = _saved_summary_path(context)
    if summary_path.is_symlink():
        raise ValueError("La scheda di destinazione è un collegamento simbolico.")
    if owner:
        promotion = owner.get("promotion")
        if not isinstance(promotion, dict):
            raise ValueError("Il marker pipeline non contiene il manifest di promozione.")
        files = promotion.get("files")
        placeholder = promotion.get("placeholder")
        required = {summary_path.name, "trascrizione.txt", archive_audio_name}
        if (
            not isinstance(files, dict)
            or set(files) != required
            or not isinstance(placeholder, dict)
            or placeholder.get("name") != summary_path.name
            or not isinstance(placeholder.get("sha256"), str)
            or not _RECORDING_HASH.fullmatch(placeholder["sha256"])
            or any(not isinstance(value, str) or not _RECORDING_HASH.fullmatch(value) for value in files.values())
        ):
            raise ValueError("Manifest di promozione non valido.")
        for name, expected_hash in files.items():
            target = call_dir / name
            if target.is_symlink():
                raise ValueError(f"Artefatto di destinazione simbolico: {name}")
            if not target.exists():
                continue
            actual_hash = jobs.fingerprint(target)
            allowed = {expected_hash}
            if name == summary_path.name:
                allowed.add(placeholder["sha256"])
            if actual_hash not in allowed:
                raise FileExistsError(f"Artefatto di destinazione modificato o estraneo: {name}")
    else:
        if not summary_path.is_file():
            raise FileNotFoundError("La scheda placeholder della registrazione non è presente.")
        fields, body = fm.parse_frontmatter(summary_path.read_text(encoding=_UTF8))
        source_name = Path(context.get("source", "")).name
        if (
            fields.get("modalita") != "solo_registrazione"
            or not source_name
            or f"[[{source_name}]]" not in body
            or "non trascritta" not in body.casefold()
        ):
            raise FileExistsError("La scheda esistente non è un placeholder di sola registrazione.")
        for name in (archive_audio_name, "trascrizione.txt"):
            target = call_dir / name
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"Artefatto estraneo già presente: {target.name}")
    return {"owner": owner, "summary_path": summary_path}


def _promote_saved_outputs(
    state: dict,
    context: dict,
    outputs: list[tuple[Path, Path]],
    checkpoint,
) -> Path:
    call_dir = Path(context["existing_dir"])
    ownership = _validate_saved_targets(context, state["id"])
    marker_path = call_dir / ".call-job.json"
    file_hashes = {target.name: jobs.fingerprint(source) for source, target in outputs}
    if not ownership["owner"]:
        placeholder_hash = jobs.fingerprint(ownership["summary_path"])
        manifest = {
            "id": state["id"],
            "promotion": {
                "files": file_hashes,
                "placeholder": {"name": ownership["summary_path"].name, "sha256": placeholder_hash},
            },
        }
        atomic_write_text(marker_path, json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        ownership["owner"] = manifest
    elif ownership["owner"].get("promotion", {}).get("files") != file_hashes:
        raise RuntimeError("Gli artefatti di lavoro non corrispondono al manifest di promozione.")

    promoted = list(state.get("promoted_files", []))
    for source, target in outputs:
        if source.is_symlink() or not source.is_file() or target.parent != call_dir:
            raise RuntimeError("File di promozione non valido.")
        if target.is_symlink():
            raise ValueError(f"Artefatto di destinazione simbolico: {target.name}")
        if target.exists():
            actual_hash = jobs.fingerprint(target)
            allowed = {file_hashes[target.name]}
            if target.name == ownership["summary_path"].name:
                allowed.add(ownership["owner"]["promotion"]["placeholder"]["sha256"])
            if actual_hash not in allowed:
                raise FileExistsError(f"Artefatto di destinazione modificato o estraneo: {target.name}")
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=call_dir, prefix=f".{target.name}.{state['id']}.", suffix=".part", delete=False
        ) as handle:
            temp_path = Path(handle.name)
        try:
            shutil.copy2(source, temp_path)
            os.replace(temp_path, target)
        finally:
            temp_path.unlink(missing_ok=True)
        if target.name not in promoted:
            promoted.append(target.name)
            checkpoint(promoted_files=promoted)
    return call_dir


def _recording_job(
    root: Path,
    source: Path,
    config: dict,
    context: dict,
    expected_signature: list[int] | None,
) -> tuple[dict, bool]:
    source_exists = source.is_file()
    source_key = os.path.normcase(str(source.resolve(strict=False)))
    recording_candidates = [
        state for state in jobs.iter_jobs(root)
        if state.get("recording_id") == context["recording_id"]
        and state.get("sha256") == context["sha256"]
    ]
    candidates = [
        state for state in recording_candidates
        if os.path.normcase(str(Path(state.get("source", "")).resolve(strict=False))) == source_key
        and (
            expected_signature is None
            or [state.get("size"), state.get("mtime_ns")] == expected_signature
        )
    ]
    relocated = False
    if not candidates and source_exists and context["category"] == "saved":
        previous_sources = [
            Path(state.get("source", ""))
            for state in recording_candidates
            if state.get("recording_context", {}).get("category") == "saved"
            and os.path.normcase(str(Path(state.get("source", "")).resolve(strict=False))) != source_key
            and Path(state.get("source", "")).is_file()
        ]
        if previous_sources:
            raise RuntimeError("La registrazione esiste anche nel percorso precedente; impossibile scegliere il job.")
        candidates = [
            state for state in recording_candidates
            if state.get("recording_context", {}).get("category") == "saved"
            and not Path(state.get("source", "")).exists()
        ]
        if len(candidates) > 1:
            raise RuntimeError("Più checkpoint corrispondono alla registrazione spostata.")
        relocated = bool(candidates)

    if source_exists:
        before = source.stat()
        signature = [before.st_size, before.st_mtime_ns]
        if expected_signature is not None and signature != expected_signature:
            raise RuntimeError("La registrazione è cambiata dopo la scelta; selezionare nuovamente l'azione.")
        digest = jobs.fingerprint(source)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("La sorgente è cambiata durante il controllo di integrità.")
        if digest != context["sha256"]:
            raise RuntimeError("L'hash della sorgente non coincide con il marker della registrazione.")
    elif expected_signature is not None:
        raise FileNotFoundError("La sorgente manca ma è stata fornita una signature attesa.")

    if candidates:
        state = max(candidates, key=lambda item: float(item.get("timestamp", 0)))
        if not jobs.compatible_config(state.get("config", {}), config) and not state.get("complete"):
            raise ValueError("Configurazione diversa dal checkpoint: ripristinare i parametri originali per riprendere.")
    elif source_exists:
        state = jobs.load_job(root, source, config, expected_signature=expected_signature)
        if state.get("recording_id") not in (None, context["recording_id"]):
            raise ValueError("La sorgente è già associata a un'altra registrazione.")
        if state.get("sha256") != context["sha256"]:
            raise RuntimeError("L'hash del checkpoint non coincide con la registrazione selezionata.")
    else:
        raise FileNotFoundError(f"Registrazione non trovata e nessun checkpoint per id {context['recording_id']}.")

    state["recording_id"] = context["recording_id"]
    state["recording_context"] = context
    state["timestamp"] = context["timestamp"]
    state["archive_audio_name"] = context.get("archive_audio_name", "audio_compresso.m4a")
    if context["category"] == "saved":
        if relocated:
            owner_path = Path(context["existing_dir"]) / ".call-job.json"
            owner = _read_recording_json(owner_path, "Marker pipeline") if owner_path.exists() else None
            if owner and owner.get("id") != state["id"]:
                raise RuntimeError("La cartella call appartiene a un'altra lavorazione.")
            old_source = state.get("source", "")
            old_target = state.get("source_target", "")
            state.update({
                "source": str(source),
                "size": source.stat().st_size,
                "mtime_ns": source.stat().st_mtime_ns,
            })
            if old_target and os.path.normcase(str(Path(old_target).resolve(strict=False))) == os.path.normcase(str(Path(old_source).resolve(strict=False))):
                state["source_target"] = str(source)
        state["final_dir"] = context["existing_dir"]
        state["task_dir"] = context["task_dir"]
        state["task_name"] = context["task_name"]
        state["title"] = context["title"]
        marker = Path(context["existing_dir"]) / ".call-job.json"
        owner = _read_recording_json(marker, "Marker pipeline") if marker.exists() else None
        if owner and owner.get("id") != state["id"]:
            raise RuntimeError("La cartella call appartiene a un'altra lavorazione.")
        _validate_saved_targets(context, state["id"])
        summary = _saved_summary_path(context)
        if not state.get("recording_archived_fields") and summary.is_file():
            fields = fm.read_fields(summary)
            preserved = {
                key: fields[key]
                for key in ("archived", "archived_at")
                if key in fields
            }
            if preserved.get("archived") is True:
                state["recording_archived_fields"] = preserved
    if context.get("origin_processed") and not state.get("complete"):
        raise RuntimeError("Il marker indica una registrazione già processata ma il job non è completo.")
    result = state.get("result")
    if state.get("complete"):
        if not isinstance(result, dict):
            raise ValueError("Il checkpoint completo non contiene un risultato valido.")
        result = dict(result)
        result["recording_id"] = context["recording_id"]
        result["call_directory"] = result.get("call_directory") or state.get("final_dir", "")
        if relocated:
            call_dir = Path(context["existing_dir"])
            result.update({
                "call_directory": str(call_dir),
                "task_directory": context["task_dir"],
                "audio": str(call_dir / state["archive_audio_name"]),
                "transcript": str(call_dir / "trascrizione.txt"),
                "summary": str(call_dir / state["summary_name"]),
                "source_archive": str(source),
            })
        state["result"] = result
    jobs.save_job(root, state)
    return state, bool(state.get("complete"))


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
    expected_signature: list[int] | None = None,
    recording_context: dict | None = None,
    allow_unverified_config: bool = False,
    use_current_config: bool = False,
) -> dict:
    configured_root = root or _cfg.VAULT_ROOT
    if recording_context is None:
        root = Path(configured_root).resolve()
        resolved = input_path.resolve()
        context = None
    else:
        root_path = Path(os.path.abspath(configured_root))
        if root_path.is_symlink():
            raise ValueError("La root del vault non può essere un collegamento simbolico.")
        root = root_path.resolve()
        context = _validate_recording_context(root, input_path, recording_context)
        resolved = Path(os.path.abspath(input_path))
    max_mb = _cfg.ARCHIVE_MAX_MB if archive_max_mb is None else archive_max_mb
    if not math.isfinite(max_mb) or max_mb <= 0:
        raise ValueError("archive_max_mb deve essere positivo e finito.")
    if resolved.suffix.lower() not in _AUDIO_EXT | _VIDEO_EXT:
        raise ValueError(f"Estensione non supportata: {resolved.suffix}")
    panel("Nuova call", resolved.name)
    if resolved.exists():
        wait_stable(resolved)
        stat = resolved.stat()
        if expected_signature is not None and [stat.st_size, stat.st_mtime_ns] != expected_signature:
            raise RuntimeError("La registrazione è cambiata dopo la scelta; selezionare nuovamente l'azione.")
    config = {
        "archive_max_mb": max_mb, "summary_model": summary_model or _cfg.CLAUDE_SUMMARY_MODEL,
        "task_model": task_model or _cfg.CLAUDE_TASK_MODEL,
        "provider": _cfg.LLM_PROVIDER, "transcription_model": _cfg.GROQ_WHISPER_MODEL,
    }
    from scripts.user_settings import snapshot
    config["runtime_settings"] = snapshot()
    with jobs.vault_lock(root):
        if context is None:
            state = jobs.load_job(root, resolved, config, expected_signature=expected_signature)
            complete = False
        else:
            context = _validate_recording_context(root, input_path, recording_context)
            state, complete = _recording_job(
                root, resolved, config, context, expected_signature
            )
            if complete:
                return state["result"]
        if state.get("last_error") == "ConfigurationNotReady" and not state.get("audio_ready") and not state.get("committed"):
            from scripts.config_verification import pending_config
            state["config"] = pending_config(state["config"], config, use_current=use_current_config)
            jobs.save_job(root, state)
        elif use_current_config and state["config"] != config:
            raise ValueError("La lavorazione ha gia' iniziato l'elaborazione: mantenere la configurazione originale del checkpoint.")
        for key, explicit in (("summary_model", summary_model), ("task_model", task_model)):
            if explicit and state["config"].get(key) != explicit and not state.get("complete"):
                raise ValueError("Configurazione diversa dal checkpoint: modello esplicito incompatibile.")
        try:
            return _process_job(root, state, allow_unverified_config=allow_unverified_config)
        except Exception as exc:
            state["last_error"] = type(exc).__name__
            jobs.save_job(root, state)
            raise


def _process_job(root: Path, state: dict, *, allow_unverified_config: bool = False) -> dict:
    from scripts.user_settings import snapshot, use
    config = state["config"]
    if "runtime_settings" not in config:
        # Legacy checkpoints did not record effort and auxiliary models.
        config["runtime_settings"] = snapshot()
        jobs.save_job(root, state)
    with use(config["runtime_settings"]):
        if not state.get("complete") and (not state.get("committed") or not state.get("kanban_done")):
            from scripts.config_verification import require_ready
            if config["provider"] != _cfg.LLM_PROVIDER:
                raise ValueError("Provider diverso dal checkpoint: ripristinare il provider originale prima della ripresa.")
            effective = snapshot()
            effective.update(CLAUDE_SUMMARY_MODEL=config["summary_model"],
                             CLAUDE_TASK_MODEL=config["task_model"],
                             GROQ_WHISPER_MODEL=config["transcription_model"])
            require_ready(effective, allow_unverified=allow_unverified_config)
        return _process_configured_job(root, state)


def _process_configured_job(root: Path, state: dict) -> dict:
    source = Path(state["source"])
    work_dir = root / ".pipeline" / "work" / state["id"]
    context = state.get("recording_context") or {}
    saved_recording = context.get("category") == "saved"
    final_dir = Path(state["final_dir"]) if state.get("final_dir") else None
    promotion_marker = final_dir / ".call-job.json" if saved_recording and final_dir else None
    promotion_started = bool(
        promotion_marker
        and promotion_marker.is_file()
        and _read_recording_json(promotion_marker, "Marker pipeline").get("id") == state["id"]
        and "promotion" in _read_recording_json(promotion_marker, "Marker pipeline")
    )
    call_dir = (
        final_dir if saved_recording and state.get("committed")
        else work_dir if saved_recording
        else final_dir if final_dir and final_dir.exists()
        else work_dir
    )
    if saved_recording and state.get("committed"):
        marker = final_dir / ".call-job.json"
        owner = _read_recording_json(marker, "Marker pipeline")
        if owner.get("id") != state["id"]:
            raise RuntimeError("La destinazione appartiene a un'altra lavorazione.")
    elif not saved_recording and final_dir and final_dir.exists():
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
            with phase("1/6 · Preparazione audio"):
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
            with phase("2/6 · Trascrizione"):
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

        if saved_recording:
            preliminary = Path(context["task_dir"])
            if "preliminary_task" not in state:
                checkpoint(preliminary_task=str(preliminary))
        elif "preliminary_task" not in state:
            with phase("3/6 · Riconoscimento progetto"):
                preliminary = _recognize_task(root, transcript, provider, config["task_model"])
            checkpoint(preliminary_task=str(preliminary) if preliminary else "")
        preliminary = Path(state["preliminary_task"]) if state["preliminary_task"] else None
        summary_path = call_dir / state.get("summary_name", "riassunto.md")
        if not summary_path.exists() and state.get("summary_name"):
            summary_path = call_dir / "riassunto.md"
        if not state.get("summary_ready"):
            with phase("4/6 · Riassunto e revisione"):
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
            with phase("5/6 · Classificazione finale"):
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
        if not promotion_started:
            _set_summary_title(summary_path, title)
            _set_summary_frontmatter(summary_path, timestamp, state["task_name"])
            if saved_recording and state.get("recording_archived_fields"):
                fields, body = fm.parse_frontmatter(summary_path.read_text(encoding=_UTF8))
                fields.update(state["recording_archived_fields"])
                fm.write_with_frontmatter(summary_path, fields, body)
            summary_path = _rename_summary(summary_path, title)
        archive_audio = call_dir / state.get("archive_audio_name", "audio_compresso.m4a")
        if not state.get("compressed") or not archive_audio.is_file():
            with phase("6/6 · Compressione audio"):
                _ffmpeg.compress_audio(audio, archive_audio, config["archive_max_mb"], _ffmpeg.get_duration(audio))
            checkpoint(compressed=True)
        if not 0 < archive_audio.stat().st_size <= config["archive_max_mb"] * 1_000_000:
            raise ValueError("Audio archivio vuoto o oltre la soglia.")
        if saved_recording:
            call_dir = _promote_saved_outputs(
                state,
                context,
                [
                    (transcript_path, final_dir / "trascrizione.txt"),
                    (archive_audio, final_dir / state.get("archive_audio_name", "audio_compresso.m4a")),
                    (summary_path, final_dir / state["summary_name"]),
                ],
                checkpoint,
            )
            checkpoint(committed=True)
        else:
            atomic_write_text(call_dir / ".call-job.json", json.dumps({"id": state["id"]}))
            task_dir.mkdir(parents=True, exist_ok=True)
            call_dir.rename(final_dir)
            call_dir = final_dir
            checkpoint(committed=True)

    summary_path = call_dir / state["summary_name"]
    archive_audio = call_dir / state.get("archive_audio_name", "audio_compresso.m4a")
    transcript_path = call_dir / "trascrizione.txt"
    for path in (summary_path, archive_audio, transcript_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Artefatto confermato mancante: {path.name}")
    task_dir = Path(state["task_dir"])
    if saved_recording:
        target = source
        if not state.get("source_target"):
            checkpoint(source_target=str(target), source_archived=True)
    elif not state.get("source_target"):
        if is_video:
            target = unique_path(call_dir / f"{summary_path.stem}{source.suffix.lower()}")
        else:
            target = unique_path(root / "completate" / "archivio" /
                                 f"{timestamp.strftime('%Y-%m-%d %H.%M')} - {_safe_name(source.name)}")
        checkpoint(source_target=str(target))
    target = Path(state["source_target"])
    if not saved_recording and not state.get("source_archived"):
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
    if not saved_recording:
        (call_dir / "audio.m4a").unlink(missing_ok=True)
    if not state.get("indexed"):
        with phase("Aggiornamento indici"):
            obs_indexes.rebuild(root)
        checkpoint(indexed=True)
    if not state.get("kanban_done"):
        if task_dir != _unassigned_calls_dir(root):
            from scripts.update_project_kanban import update_from_summary
            with phase("Aggiornamento Kanban"):
                update_from_summary(summary_path, task_dir)
        checkpoint(kanban_done=True)
    result = {
        "call_directory": str(call_dir), "task_directory": str(task_dir),
        "audio": str(archive_audio), "transcript": str(transcript_path),
        "summary": str(summary_path), "source_archive": str(target),
        "provider": config["provider"], "job_id": state["id"],
    }
    if state.get("recording_id"):
        result["recording_id"] = state["recording_id"]
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
    panel("Call completata", completion_details(call_dir.name, state['task_name'] or "Senza progetto", str(call_dir)), "green")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Processa una registrazione audio/video.")
    parser.add_argument("--input-path", required=True, type=Path)
    parser.add_argument("--root-path", type=Path, default=None)
    parser.add_argument("--keep-video", action="store_true")
    parser.add_argument("--archive-max-mb", type=float, default=None)
    parser.add_argument("--summary-model", default="")
    parser.add_argument("--task-model", default="")
    parser.add_argument("--allow-unverified-config", action="store_true",
                        help="Accetta esplicitamente il rischio delle impostazioni non verificate per questa esecuzione.")
    parser.add_argument("--use-current-config", action="store_true",
                        help="Applica preferenze corrette solo a un checkpoint bloccato dalla verifica prima di elaborare l'audio.")
    args = parser.parse_args()

    result = process(
        input_path=args.input_path,
        root=args.root_path,
        keep_video=args.keep_video,
        archive_max_mb=args.archive_max_mb,
        summary_model=args.summary_model,
        task_model=args.task_model,
        allow_unverified_config=args.allow_unverified_config,
        use_current_config=args.use_current_config,
    )
    for k, v in result.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
