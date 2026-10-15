"""Helpers for keeping original recordings without running the call pipeline."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Callable

import scripts.settings as cfg
from scripts.filesystem import atomic_write_text, safe_name
from scripts.obsidian import frontmatter as fm
from scripts.tasks import discover_task_dirs, is_archived_task

_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MARKER = ".recording-job.json"
_TRASH = "cestino_registrazioni"


def _root_path(root: Path) -> Path:
    raw_path = Path(os.path.abspath(Path(root)))
    if raw_path.is_symlink():
        raise ValueError("La root del vault non può essere un collegamento simbolico.")
    path = raw_path.resolve(strict=False)
    if not raw_path.is_dir():
        raise ValueError("La root del vault non è una cartella.")
    return path


def _entry_id(entry: dict) -> str:
    job_id = entry.get("id")
    if not isinstance(job_id, str) or not _ID_PATTERN.fullmatch(job_id):
        raise ValueError("L'id della registrazione deve contenere 24 caratteri esadecimali minuscoli.")
    return job_id


def _entry_title(entry: dict) -> str:
    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Il titolo della registrazione è mancante.")
    return safe_name(title)


def _entry_timestamp(entry: dict) -> datetime:
    timestamp = entry.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise ValueError("Il timestamp della registrazione non è valido.")
    if not math.isfinite(timestamp):
        raise ValueError("Il timestamp della registrazione non è valido.")
    try:
        return datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("Il timestamp della registrazione non è valido.") from exc


def _entry_path(root: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Il percorso {field} della registrazione è mancante.")
    path = Path(value)
    if not path.is_absolute():
        if ".." in path.parts:
            raise ValueError(f"Il percorso {field} non è valido.")
        path = root / path
    return Path(os.path.abspath(path))


def _is_child(path: Path, parent: Path) -> bool:
    return path != parent and parent in path.parents


def _has_symlink_between(path: Path, parent: Path) -> bool:
    try:
        relative = path.relative_to(parent)
    except ValueError:
        return True
    current = parent
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _task_dir(root: Path, entry: dict) -> tuple[Path, bool]:
    task_dir = _entry_path(root, entry.get("task_dir"), "task_dir")
    completed = root / "completate"
    unassigned = completed / cfg.UNASSIGNED_CALLS_DIR_NAME
    task_root = completed / "Task"

    if task_dir.resolve(strict=False) == unassigned.resolve(strict=False):
        resolved_completed = completed.resolve(strict=False)
        if (
            _has_symlink_between(task_dir, root)
            or _has_symlink_between(unassigned, root)
            or completed != resolved_completed
            or unassigned != unassigned.resolve(strict=False)
            or resolved_completed.parent != root
            or unassigned.resolve(strict=False).parent != resolved_completed
        ):
            raise ValueError("La cartella Senza progetto contiene un collegamento simbolico.")
        return unassigned, False

    if _has_symlink_between(task_dir, root):
        raise ValueError("Il percorso del progetto contiene un collegamento simbolico.")
    resolved_completed = completed.resolve(strict=False)
    resolved_task_root = task_root.resolve(strict=False)
    if (
        completed != resolved_completed
        or task_root != resolved_task_root
        or resolved_completed.parent != root
        or resolved_task_root.parent != resolved_completed
        or task_dir != task_dir.resolve(strict=False)
    ):
        raise ValueError("Il percorso completate/Task contiene un collegamento simbolico.")
    resolved_task_dir = task_dir.resolve(strict=False)
    active = {
        path.resolve(strict=False)
        for path in discover_task_dirs(root)
        if not is_archived_task(path)
    }
    if resolved_task_dir not in active or not _is_child(resolved_task_dir, resolved_task_root):
        raise ValueError("Il progetto indicato non è una cartella attiva sotto completate/Task.")
    return resolved_task_dir, True


def _source_path(root: Path, source: Path) -> Path:
    incoming = root / "da_processare"
    if (
        incoming.is_symlink()
        or not incoming.is_dir()
        or incoming != incoming.resolve(strict=False)
        or incoming.resolve(strict=False).parent != root
    ):
        raise ValueError("La cartella da_processare non è disponibile o contiene un collegamento simbolico.")
    source = Path(os.path.abspath(Path(source)))
    if source.is_symlink():
        raise ValueError("La registrazione sorgente non può essere un collegamento simbolico.")
    try:
        source.stat()
        exists = True
    except OSError:
        exists = False
    if source.parent != incoming or (exists and not source.is_file()):
        raise ValueError("La registrazione deve essere un file diretto in da_processare.")
    return source


def _signature(entry: dict) -> tuple[int, int]:
    signature = entry.get("signature")
    if not isinstance(signature, (list, tuple)) or len(signature) != 2:
        raise ValueError("La signature della registrazione non è valida.")
    size, mtime_ns = signature
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (size, mtime_ns)):
        raise ValueError("La signature della registrazione non è valida.")
    return size, mtime_ns


def _file_hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Il file della registrazione non è disponibile: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_source(source: Path, signature: tuple[int, int]) -> str:
    stat = source.stat()
    if (stat.st_size, stat.st_mtime_ns) != signature:
        raise ValueError("La registrazione sorgente è cambiata dopo la selezione.")
    file_hash = _file_hash(source)
    stat = source.stat()
    if (stat.st_size, stat.st_mtime_ns) != signature:
        raise ValueError("La registrazione sorgente è cambiata durante il controllo.")
    return file_hash


def _checkpoint(entry: dict, checkpoint: Callable[..., None], **values: object) -> None:
    checkpoint(**values)
    entry.update(values)


def _marker_data(job_id: str, file_hash: str, action: str) -> dict[str, str]:
    return {"id": job_id, "sha256": file_hash, "action": action}


def _read_json(path: Path) -> dict | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _prepare_directory(directory: Path, job_id: str, file_hash: str, action: str) -> None:
    marker = directory / _MARKER
    if directory.is_symlink():
        raise ValueError("La cartella di destinazione è un collegamento simbolico.")
    if not directory.exists():
        directory.mkdir()
    elif not directory.is_dir():
        raise FileExistsError(f"La destinazione esiste e non è una cartella: {directory}")

    expected = _marker_data(job_id, file_hash, action)
    if marker.exists() or marker.is_symlink():
        if marker.is_symlink() or _read_json(marker) != expected:
            raise ValueError("La cartella di destinazione appartiene a un'altra registrazione.")
        return
    entries = list(directory.iterdir())
    marker_text = json.dumps(expected, ensure_ascii=False, sort_keys=True) + "\n"
    pending_markers = [
        path for path in entries
        if path.name.startswith(f".{_MARKER}.") and path.name.endswith(".tmp")
    ]
    if pending_markers:
        if len(entries) != 1 or pending_markers[0].is_symlink():
            raise ValueError("La cartella di destinazione contiene file non riconosciuti.")
        try:
            recoverable = pending_markers[0].read_text(encoding="utf-8") == marker_text
        except (OSError, UnicodeError):
            recoverable = False
        if not recoverable:
            raise ValueError("Il marker temporaneo della registrazione non è valido.")
        os.rename(pending_markers[0], marker)
        return
    if entries:
        raise ValueError("La cartella di destinazione contiene già file non riconosciuti.")
    atomic_write_text(marker, marker_text, encoding="utf-8")


def _move_no_overwrite(source: Path, target: Path) -> None:
    if os.name == "nt":
        os.rename(source, target)
    else:
        os.link(source, target, follow_symlinks=False)
        source.unlink()


def _ensure_recording_at_target(
    source: Path,
    target: Path,
    file_hash: str,
    signature: tuple[int, int],
) -> None:
    source_exists = source.exists()
    target_exists = target.exists() or target.is_symlink()
    if target_exists:
        if target.is_symlink() or _file_hash(target) != file_hash:
            raise FileExistsError(f"La registrazione di destinazione è diversa: {target}")
        if source_exists:
            if source.is_symlink() or _hash_source(source, signature) != file_hash:
                raise ValueError("La registrazione sorgente è cambiata dopo la selezione.")
            source.unlink()
        return
    if not source_exists:
        raise FileNotFoundError("Mancano sia la sorgente sia la registrazione salvata.")
    if source.is_symlink() or _hash_source(source, signature) != file_hash:
        raise ValueError("La registrazione sorgente è cambiata dopo la selezione.")
    _move_no_overwrite(source, target)


def _valid_destination_name(destination: Path, title: str, timestamp: datetime) -> bool:
    base = f"{timestamp:%Y-%m-%d %H.%M} - {title}"
    if destination.name == base:
        return True
    match = re.fullmatch(re.escape(base) + r" \((\d+)\)", destination.name)
    return bool(match and int(match.group(1)) >= 2)


def _destination_paths(
    root: Path,
    project_dir: Path,
    title: str,
    timestamp: datetime,
    entry: dict,
    source: Path,
) -> tuple[Path, Path]:
    has_destination = entry.get("destination") not in (None, "")
    has_target = entry.get("recording_target") not in (None, "")
    if has_destination != has_target:
        raise ValueError("Il checkpoint della destinazione è incompleto.")
    if has_destination:
        destination = _entry_path(root, entry.get("destination"), "destination")
        target = _entry_path(root, entry.get("recording_target"), "recording_target")
    else:
        base = f"{timestamp:%Y-%m-%d %H.%M} - {title}"
        destination = project_dir / base
        index = 2
        while destination.exists() or destination.is_symlink():
            destination = project_dir / f"{base} ({index})"
            index += 1
        target = destination / f"{_title_from_destination(destination, timestamp)}{source.suffix}"

    if destination.parent.resolve(strict=False) != project_dir.resolve(strict=False):
        raise ValueError("La destinazione deve essere una cartella diretta del progetto attivo.")
    if not _valid_destination_name(destination, title, timestamp):
        raise ValueError("Il nome della cartella di destinazione non corrisponde al titolo e al timestamp.")
    if destination.is_symlink() or _has_symlink_between(destination, project_dir):
        raise ValueError("La destinazione contiene un collegamento simbolico.")
    if (
        destination != destination.resolve(strict=False)
        or destination.resolve(strict=False).parent != project_dir.resolve(strict=False)
    ):
        raise ValueError("La destinazione non è una cartella diretta del progetto attivo.")
    if target.parent.resolve(strict=False) != destination.resolve(strict=False):
        raise ValueError("Il file registrazione deve trovarsi direttamente nella destinazione.")
    recording_title = _title_from_destination(destination, timestamp)
    if target.stem != recording_title or (source.exists() and target.suffix != source.suffix):
        raise ValueError("Il nome del file registrazione non corrisponde al titolo.")
    if target.is_symlink():
        raise ValueError("Il file registrazione di destinazione è un collegamento simbolico.")
    if not _is_child(destination, project_dir.resolve(strict=False)):
        raise ValueError("La destinazione non è contenuta nel progetto attivo.")
    return destination, target


def _title_from_destination(destination: Path, timestamp: datetime) -> str:
    prefix = f"{timestamp:%Y-%m-%d %H.%M} - "
    return destination.name[len(prefix):]


def _write_summary(destination: Path, target: Path, title: str, timestamp: datetime, task_dir: Path, project: bool) -> bool:
    summary_path = destination / f"{title}.md"
    if summary_path.is_symlink():
        raise ValueError("La scheda Markdown è un collegamento simbolico.")
    if project:
        task_root = next((parent for parent in task_dir.parents if parent.name == "Task"), None)
        task_value = "/".join((task_dir.relative_to(task_root).parts if task_root else (task_dir.name,)))
        task_value = f"[[{task_value}]]"
    else:
        task_value = cfg.UNASSIGNED_CALLS_DIR_NAME
    fields = {
        "data": timestamp.strftime("%Y-%m-%d"),
        "ora": timestamp.strftime("%H:%M"),
        "task": task_value,
        "persone": [],
        "sistemi": [],
        "tags": ["call"],
        "modalita": "solo_registrazione",
    }
    body = f"# {title}\n\n[[{target.name}]]\n\nRegistrazione presente, ma non trascritta.\n"
    content = fm.render_frontmatter(fields, body)
    if summary_path.exists():
        if summary_path.read_text(encoding="utf-8") == content:
            return True
        existing_fields, existing_body = fm.parse_frontmatter(summary_path.read_text(encoding="utf-8"))
        if (
            existing_fields.get("modalita") == "solo_registrazione"
            and existing_fields.get("data") == fields["data"]
            and existing_fields.get("ora") == fields["ora"]
            and "call" in fm.normalise_string_list(existing_fields.get("tags", []))
            and f"[[{target.name}]]" in existing_body
            and f"# {title}" in existing_body
            and "Registrazione presente, ma non trascritta." in existing_body
        ):
            return True
        raise FileExistsError(f"La scheda Markdown esiste già con contenuti diversi: {summary_path}")
    atomic_write_text(summary_path, content, encoding="utf-8")
    return False


def save_recording(
    root: Path,
    source: Path,
    entry: dict,
    checkpoint: Callable[..., None],
) -> Path:
    """Move one original recording into its active task folder and create a note."""
    root = _root_path(root)
    job_id = _entry_id(entry)
    title = _entry_title(entry)
    timestamp = _entry_timestamp(entry)
    signature = _signature(entry)
    project_dir, project = _task_dir(root, entry)
    source_path = _source_path(root, source)

    source_exists = source_path.exists()
    if source_exists:
        file_hash = _hash_source(source_path, signature)
        prior_hash = entry.get("sha256")
        if prior_hash is not None and prior_hash != file_hash:
            raise ValueError("L'hash della registrazione sorgente non coincide con il checkpoint.")
    else:
        file_hash = entry.get("sha256")
        if not isinstance(file_hash, str) or not _HASH_PATTERN.fullmatch(file_hash):
            raise FileNotFoundError("La sorgente manca e il checkpoint non contiene un hash valido.")

    destination, target = _destination_paths(root, project_dir, title, timestamp, entry, source_path)
    _checkpoint(
        entry,
        checkpoint,
        destination=str(destination),
        recording_target=str(target),
        sha256=file_hash,
    )

    if not project_dir.exists():
        project_dir.mkdir(parents=True)
    _prepare_directory(destination, job_id, file_hash, "save")
    _ensure_recording_at_target(source_path, target, file_hash, signature)
    _write_summary(destination, target, target.stem, timestamp, project_dir, project)
    _checkpoint(entry, checkpoint, note_ready=True)
    return destination


def _trash_paths(root: Path, entry: dict, source: Path) -> tuple[Path, Path]:
    trash_root = root / _TRASH
    if (
        trash_root.is_symlink()
        or trash_root != trash_root.resolve(strict=False)
        or trash_root.resolve(strict=False).parent != root
    ):
        raise ValueError("Il cestino delle registrazioni è un collegamento simbolico.")
    has_destination = bool(entry.get("destination"))
    has_target = bool(entry.get("recording_target"))
    if has_destination != has_target:
        raise ValueError("Il checkpoint della destinazione di scarto è incompleto.")
    destination = _entry_path(root, entry.get("destination"), "destination") if has_destination else trash_root / entry["id"]
    target = _entry_path(root, entry.get("recording_target"), "recording_target") if has_target else destination / source.name
    expected = (trash_root / entry["id"]).resolve(strict=False)
    if destination != expected or destination.parent.resolve(strict=False) != trash_root.resolve(strict=False):
        raise ValueError("La destinazione di scarto non corrisponde all'id della registrazione.")
    if target.parent.resolve(strict=False) != destination or target.name in {"", ".", "..", _MARKER, "metadata.json"}:
        raise ValueError("Il percorso della registrazione nel cestino non è valido.")
    expected_name = source.name if source.exists() else entry.get("original_name")
    if expected_name and target.name != expected_name:
        raise ValueError("Il nome della registrazione nel cestino non corrisponde all'originale.")
    if _has_symlink_between(destination, root) or destination.is_symlink() or target.is_symlink():
        raise ValueError("Il cestino contiene un collegamento simbolico.")
    return destination, target


def _discard_metadata(entry: dict, target: Path, source_path: Path) -> dict:
    original_path = entry.get("original_path") or str(source_path)
    discarded_at = entry.get("discarded_at")
    if isinstance(discarded_at, bool) or not isinstance(discarded_at, (int, float)) or not math.isfinite(discarded_at):
        discarded_at = time.time()
    return {
        "id": entry["id"],
        "name": target.name,
        "original_path": str(original_path),
        "discarded_at": float(discarded_at),
        "sha256": entry["sha256"],
    }


def discard_recording(
    root: Path,
    source: Path,
    entry: dict,
    checkpoint: Callable[..., None],
) -> Path:
    """Move one original recording to the private, retention-managed trash."""
    root = _root_path(root)
    job_id = _entry_id(entry)
    signature = _signature(entry)
    source_path = _source_path(root, source)
    source_exists = source_path.exists()

    if source_exists:
        file_hash = _hash_source(source_path, signature)
        prior_hash = entry.get("sha256")
        if prior_hash is not None and prior_hash != file_hash:
            raise ValueError("L'hash della registrazione sorgente non coincide con il checkpoint.")
    else:
        file_hash = entry.get("sha256")
        if not isinstance(file_hash, str) or not _HASH_PATTERN.fullmatch(file_hash):
            raise FileNotFoundError("La sorgente manca e il checkpoint non contiene un hash valido.")

    destination, target = _trash_paths(root, entry, source_path)
    if target.name.casefold() in {_MARKER.casefold(), "metadata.json"}:
        raise ValueError("Il nome della registrazione è riservato nel cestino.")
    target_preexists = target.exists() or target.is_symlink()
    if source_exists and target_preexists:
        marker = _read_json(destination / _MARKER)
        if (
            target.is_symlink()
            or marker != _marker_data(job_id, file_hash, "discard")
            or _file_hash(target) != file_hash
        ):
            raise FileExistsError("La destinazione di scarto contiene un file non riconosciuto.")
    discarded_at = entry.get("discarded_at")
    if source_exists and not target_preexists:
        discarded_at = time.time()
    elif isinstance(discarded_at, bool) or not isinstance(discarded_at, (int, float)) or not math.isfinite(discarded_at):
        discarded_at = time.time()
    original_path = entry.get("original_path") or str(source_path)
    _checkpoint(
        entry,
        checkpoint,
        destination=str(destination),
        recording_target=str(target),
        sha256=file_hash,
        original_path=str(original_path),
        original_name=source_path.name,
        discarded_at=float(discarded_at),
    )

    if destination.parent != root / _TRASH:
        raise ValueError("Il cestino delle registrazioni non è nella root del vault.")
    if not destination.parent.exists():
        destination.parent.mkdir()
    _prepare_directory(destination, job_id, file_hash, "discard")
    _ensure_recording_at_target(source_path, target, file_hash, signature)
    metadata = _discard_metadata(entry, target, source_path)
    metadata_path = destination / "metadata.json"
    if metadata_path.is_symlink():
        raise ValueError("I metadati dello scarto sono un collegamento simbolico.")
    if metadata_path.exists():
        if _read_json(metadata_path) != metadata:
            raise FileExistsError("Esistono metadati diversi per questa registrazione scartata.")
    else:
        atomic_write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return destination


def _valid_trash_item(directory: Path, trash_root: Path) -> tuple[Path | None, dict] | None:
    if directory.is_symlink() or not directory.is_dir() or directory.parent.resolve(strict=False) != trash_root.resolve(strict=False):
        return None
    if directory != directory.resolve(strict=False) or directory.resolve(strict=False).parent != trash_root.resolve(strict=False):
        return None
    job_id = directory.name
    if not _ID_PATTERN.fullmatch(job_id):
        return None
    metadata = _read_json(directory / "metadata.json")
    marker = _read_json(directory / _MARKER)
    if not metadata or marker != _marker_data(job_id, metadata.get("sha256", ""), "discard"):
        return None
    if metadata.get("id") != job_id:
        return None
    file_hash = metadata.get("sha256")
    name = metadata.get("name")
    discarded_at = metadata.get("discarded_at")
    if not isinstance(file_hash, str) or not _HASH_PATTERN.fullmatch(file_hash):
        return None
    if (
        not isinstance(name, str)
        or not name
        or Path(name).name != name
        or name in {".", "..", _MARKER, "metadata.json"}
        or isinstance(discarded_at, bool)
        or not isinstance(discarded_at, (int, float))
        or not math.isfinite(discarded_at)
    ):
        return None
    recording = directory / name
    if recording.is_symlink():
        return None
    return (recording if recording.exists() else None, metadata)


def cleanup_trash(root: Path, days: int = 30, now: float | None = None,
                  excluded_dirs: set[Path] | None = None) -> int:
    """Remove expired, verified trash recordings and their own metadata files."""
    if days <= 0:
        return 0
    try:
        root = _root_path(root)
        trash_root = root / _TRASH
        if (
            trash_root.is_symlink()
            or not trash_root.is_dir()
            or trash_root != trash_root.resolve(strict=False)
            or trash_root.resolve(strict=False).parent != root
        ):
            return 0
        current_time = time.time() if now is None else float(now)
        if not math.isfinite(current_time):
            return 0
        entries = list(trash_root.iterdir())
    except (OSError, TypeError, ValueError):
        return 0

    cutoff_seconds = days * 24 * 60 * 60
    deleted = 0
    excluded = {path.resolve() for path in (excluded_dirs or set())}
    for directory in entries:
        try:
            if directory.resolve() in excluded:
                continue
            validated = _valid_trash_item(directory, trash_root)
            if validated is None:
                continue
            recording, metadata = validated
            if current_time - float(metadata["discarded_at"]) < cutoff_seconds:
                continue
            if recording is not None:
                if _file_hash(recording) != metadata["sha256"]:
                    continue
                recording.unlink()
                deleted += 1
            (directory / "metadata.json").unlink()
            (directory / _MARKER).unlink()
            try:
                directory.rmdir()
            except OSError:
                pass
        except (OSError, TypeError, ValueError, KeyError):
            continue
    return deleted
