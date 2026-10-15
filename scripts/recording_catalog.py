"""Catalog and on-demand processing for saved or discarded recordings."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts import jobs, recordings, tasks

_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_CALL_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2})\.(\d{2})\s+-\s+(.+)$")
_SOURCE_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T_](\d{2})[.\-:](\d{2})(?:[.\-:](\d{2}))?")
_MEDIA_EXTENSIONS = {
    ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".webm", ".wma",
    ".mp4", ".mkv", ".mov", ".avi",
}
_TRASH_RETENTION_SECONDS = 30 * 24 * 60 * 60
_TRASH_NAME = "cestino_registrazioni"
_CALL_JOB_MARKER = ".call-job.json"


def _root_path(root: Path) -> Path:
    try:
        return recordings._root_path(Path(root))
    except (OSError, TypeError, ValueError):
        raise ValueError("La root del vault non è disponibile o contiene un collegamento simbolico.")


def _safe_under(root: Path, path: Path) -> bool:
    candidate = Path(os.path.abspath(os.fspath(path)))
    try:
        if candidate.resolve(strict=False) != candidate:
            return False
        relative = candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
    return True


def _read_jobs(root: Path) -> list[dict[str, Any]]:
    directory = root / ".pipeline" / "jobs"
    if not _safe_under(root, directory) or not directory.is_dir():
        return []
    result = []
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return []
    for path in paths:
        if path.is_symlink() or not _safe_under(root, path):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if isinstance(value, dict) and value.get("id") == path.stem:
            result.append(value)
    return result


def _context(job: dict[str, Any]) -> dict[str, Any]:
    value = job.get("recording_context")
    context = dict(value) if isinstance(value, dict) else {}
    context.setdefault("recording_id", job.get("recording_id") or job.get("origin_id"))
    context.setdefault("category", job.get("recording_category"))
    context.setdefault("sha256", job.get("recording_sha256"))
    return context


def _job_recording_id(job: dict[str, Any]) -> str | None:
    context = _context(job)
    value = context.get("recording_id") or context.get("origin_id") or job.get("origin_id")
    return value if isinstance(value, str) and _ID_RE.fullmatch(value) else None


def _matching_jobs(jobs_list: list[dict[str, Any]], job_id: str, category: str, sha256: str) -> tuple[bool, dict | None]:
    complete = any(job.get("complete") is True and _job_recording_id(job) == job_id for job in jobs_list)
    if complete:
        return True, None
    matches = []
    for job in jobs_list:
        if job.get("complete") is True or _job_recording_id(job) != job_id:
            continue
        context = _context(job)
        if context.get("category") == category and context.get("sha256") == sha256:
            matches.append(job)
    if not matches:
        return False, None
    return False, max(matches, key=lambda job: _finite_timestamp(job.get("timestamp")) or 0.0)


def _finite_timestamp(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _job_source(root: Path, job: dict[str, Any] | None) -> Path | None:
    if job is None:
        return None
    value = job.get("source") or _context(job).get("source")
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        return None
    path = Path(os.path.abspath(path))
    if not _safe_under(root, path):
        return None
    return path


def _media_candidates(root: Path, directory: Path, title: str) -> list[Path]:
    try:
        children = sorted(directory.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return []
    result = []
    for path in children:
        name = path.name.casefold()
        if (path.is_symlink() or not path.is_file() or not _safe_under(root, path)
                or path.suffix.casefold() not in _MEDIA_EXTENSIONS):
            continue
        stem = path.stem.casefold()
        if (".part." in name and stem != title.casefold()) or (name == "audio.m4a" and stem != title.casefold()):
            continue
        if name.startswith(("audio_compresso.", "audio_elaborato.")) and stem != title.casefold():
            continue
        result.append(path)
    return result


def _saved_directories(root: Path):
    completed = root / "completate"
    roots = [completed / "Task", completed / "Senza progetto"]
    stack = [path for path in roots if _safe_under(root, path) and path.is_dir()]
    visited: set[str] = set()
    while stack:
        directory = stack.pop()
        key = os.path.normcase(os.path.abspath(directory))
        if key in visited or directory.is_symlink() or not _safe_under(root, directory):
            continue
        visited.add(key)
        marker = directory / recordings._MARKER
        if marker.is_file() and not marker.is_symlink():
            yield directory
            continue
        try:
            children = list(directory.iterdir())
        except OSError:
            continue
        stack.extend(
            child for child in children
            if child.is_dir() and not child.is_symlink() and _safe_under(root, child)
        )


def _timestamp_from_name(name: str) -> float | None:
    match = _CALL_RE.match(name)
    if match:
        try:
            return datetime.strptime(
                f"{match.group(1)} {match.group(2)}:{match.group(3)}", "%Y-%m-%d %H:%M"
            ).timestamp()
        except ValueError:
            return None
    match = _SOURCE_TIME_RE.search(name)
    if match:
        try:
            second = match.group(4) or "0"
            return datetime.strptime(
                f"{match.group(1)} {match.group(2)}:{match.group(3)}:{second}", "%Y-%m-%d %H:%M:%S"
            ).timestamp()
        except ValueError:
            return None
    return None


def _timestamp_from_stat(path: Path | None) -> float | None:
    if path is None:
        return None
    try:
        if path.is_file() and not path.is_symlink():
            return float(path.stat().st_mtime)
    except OSError:
        pass
    return None


def _task_location(root: Path, existing_dir: Path, labels: dict[Path, str]) -> tuple[Path | None, str]:
    completed = root / "completate"
    if existing_dir.parent == completed / "Senza progetto" or existing_dir.parent == completed / "Senza progetto" / "archivio":
        return existing_dir.parent.parent if existing_dir.parent.name == "archivio" else existing_dir.parent, ""
    base = existing_dir.parent.parent if existing_dir.parent.name.casefold() == "archivio" else existing_dir.parent
    label = labels.get(base)
    return (base, label) if label is not None and _safe_under(root, base) else (None, "")


def _saved_item(root: Path, directory: Path, labels: dict[Path, str], jobs_list: list[dict[str, Any]]) -> dict | None:
    if directory.is_symlink() or not _safe_under(root, directory):
        return None
    marker = recordings._read_json(directory / recordings._MARKER)
    if not marker or marker.get("action") != "save":
        return None
    job_id, sha256 = marker.get("id"), marker.get("sha256")
    if not isinstance(job_id, str) or not _ID_RE.fullmatch(job_id) or not isinstance(sha256, str) or not _SHA_RE.fullmatch(sha256):
        return None
    complete, pending = _matching_jobs(jobs_list, job_id, "saved", sha256)
    if complete:
        return None
    if (directory / _CALL_JOB_MARKER).is_symlink():
        return None
    has_call_job = (directory / _CALL_JOB_MARKER).exists()
    if has_call_job and pending is None:
        return None

    task_dir, task_name = _task_location(root, directory, labels)
    if task_dir is None:
        return None
    match = _CALL_RE.match(directory.name)
    title = match.group(4) if match else ""
    candidates = _media_candidates(root, directory, title)
    pipeline_source = _job_source(root, pending)
    if pending is not None and pipeline_source is None:
        return None
    moved_source = False
    if pipeline_source is not None and pipeline_source.parent != directory:
        if pipeline_source.exists() or len(candidates) != 1 or candidates[0].stem.casefold() != title.casefold():
            return None
        moved_source = True
        source = candidates[0]
    elif pending is not None:
        source = pipeline_source
    elif len(candidates) == 1:
        source = candidates[0]
    else:
        return None
    if source is None or not _safe_under(root, source):
        return None

    title = match.group(4) if match else (candidates[0].stem if candidates else source.stem)
    context = _context(pending) if pending else {}
    timestamp = _timestamp_from_name(directory.name)
    if timestamp is None:
        timestamp = _timestamp_from_stat(source)
    if timestamp is None:
        timestamp = _finite_timestamp(context.get("timestamp"))
    if timestamp is None:
        return None
    return {
        "id": job_id,
        "category": "saved",
        "source": str(source),
        "sha256": sha256,
        "timestamp": timestamp,
        "title": title,
        "task_dir": str(task_dir),
        "task_name": task_name,
        "existing_dir": str(directory),
        "status": "interrupted" if pending is not None or moved_source else "ready",
    }


def _trash_item(root: Path, directory: Path, jobs_list: list[dict[str, Any]], now: float) -> dict | None:
    trash_root = root / _TRASH_NAME
    validated = recordings._valid_trash_item(directory, trash_root)
    if validated is None or not _safe_under(root, directory):
        return None
    recording, metadata = validated
    job_id, sha256 = metadata.get("id"), metadata.get("sha256")
    if not isinstance(job_id, str) or not _ID_RE.fullmatch(job_id) or not isinstance(sha256, str) or not _SHA_RE.fullmatch(sha256):
        return None
    complete, pending = _matching_jobs(jobs_list, job_id, "trash", sha256)
    if complete:
        return None
    discarded_at = float(metadata["discarded_at"])
    if now - discarded_at >= _TRASH_RETENTION_SECONDS and pending is None:
        return None
    if recording is not None and (not recording.is_file() or not _safe_under(root, recording)):
        recording = None
    pipeline_source = _job_source(root, pending)
    if pending is not None and pipeline_source is None:
        return None
    if pipeline_source is not None and pipeline_source.parent != directory:
        return None
    if pending is not None:
        source = pipeline_source
    elif recording is not None:
        source = recording
    else:
        return None
    if source is None:
        return None

    context = _context(pending) if pending else {}
    timestamp = _timestamp_from_stat(recording)
    if timestamp is None:
        timestamp = _finite_timestamp(context.get("timestamp"))
    if timestamp is None:
        timestamp = _timestamp_from_name(str(metadata.get("name", "")))
    if timestamp is None:
        original_path = Path(str(metadata.get("original_path", "")))
        timestamp = _timestamp_from_name(original_path.name)
    if timestamp is None:
        return None
    original_path = Path(str(metadata.get("original_path", "")))
    title = original_path.stem if original_path.name else Path(str(metadata["name"])).stem
    return {
        "id": job_id,
        "category": "trash",
        "source": str(source),
        "sha256": sha256,
        "timestamp": timestamp,
        "title": title,
        "task_dir": "",
        "task_name": "",
        "existing_dir": str(directory),
        "status": "interrupted" if pending is not None else "ready",
        "discarded_at": discarded_at,
    }


def list_unprocessed(root: Path) -> list[dict]:
    """List saved or discarded source recordings without reading their contents."""
    try:
        root = _root_path(root)
    except ValueError:
        return []
    jobs_list = _read_jobs(root)
    try:
        labels = tasks.task_labels(tasks.discover_task_dirs(root))
    except (OSError, ValueError):
        labels = {}
    items = []
    for directory in _saved_directories(root):
        item = _saved_item(root, directory, labels, jobs_list)
        if item is not None:
            items.append(item)
    trash_root = root / _TRASH_NAME
    if _safe_under(root, trash_root) and trash_root.is_dir():
        try:
            trash_directories = sorted(trash_root.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            trash_directories = []
        now = time.time()
        for directory in trash_directories:
            if directory.is_symlink() or not directory.is_dir():
                continue
            item = _trash_item(root, directory, jobs_list, now)
            if item is not None:
                items.append(item)
    return sorted(items, key=lambda item: (item["timestamp"], item["id"], item["category"]))


def _hash_source(path: Path) -> tuple[str, list[int]]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    signature = [before.st_size, before.st_mtime_ns]
    if signature != [after.st_size, after.st_mtime_ns]:
        raise ValueError("La registrazione è cambiata durante il controllo d'integrità.")
    return digest.hexdigest(), signature


def process_recording(root: Path, item: dict, **pipeline_kwargs) -> dict:
    """Validate a catalog item under the vault lock and process it on demand."""
    if not isinstance(item, dict) or item.get("category") not in {"saved", "trash"}:
        raise ValueError("Registrazione del catalogo non valida.")
    if any(key in pipeline_kwargs for key in {"recording_context", "expected_signature", "input_path", "root"}):
        raise ValueError("I parametri del catalogo non possono essere sovrascritti.")
    root = _root_path(root)
    job_id, category, expected_hash = item.get("id"), item["category"], item.get("sha256")
    if not isinstance(job_id, str) or not _ID_RE.fullmatch(job_id) or not isinstance(expected_hash, str) or not _SHA_RE.fullmatch(expected_hash):
        raise ValueError("Identità della registrazione non valida.")
    supplied_source = item.get("source")
    if not isinstance(supplied_source, str) or not supplied_source:
        raise ValueError("Percorso sorgente della registrazione mancante.")
    supplied_path = Path(os.path.abspath(supplied_source))
    if not _safe_under(root, supplied_path):
        raise ValueError("Il percorso sorgente non appartiene al vault.")

    with jobs.vault_lock(root):
        current = [
            candidate for candidate in list_unprocessed(root)
            if candidate["id"] == job_id and candidate["category"] == category
            and candidate["sha256"] == expected_hash
        ]
        if len(current) != 1:
            raise ValueError("La registrazione selezionata non è più disponibile nel catalogo.")
        candidate = current[0]
        source = Path(candidate["source"])
        if os.path.normcase(os.path.abspath(source)) != os.path.normcase(os.path.abspath(supplied_path)):
            raise ValueError("Il percorso della registrazione è cambiato dopo la selezione.")

        from scripts.watch_calls import _file_available

        expected_signature = None
        if source.exists():
            if source.is_symlink() or not source.is_file() or not _safe_under(root, source) or not _file_available(source):
                raise ValueError("Il file sorgente non è disponibile in sicurezza.")
            actual_hash, expected_signature = _hash_source(source)
            if actual_hash != expected_hash:
                raise ValueError("Il contenuto della registrazione è cambiato dopo la selezione.")
        elif candidate["status"] != "interrupted":
            raise FileNotFoundError(f"La registrazione sorgente non è disponibile: {source}")

        context = {
            "recording_id": job_id,
            "sha256": expected_hash,
            "category": category,
            "timestamp": candidate["timestamp"],
        }
        if category == "saved":
            context.update({
                "existing_dir": candidate["existing_dir"],
                "title": candidate["title"],
                "task_dir": candidate["task_dir"],
                "task_name": candidate["task_name"],
            })
        else:
            context["discarded_at"] = candidate["discarded_at"]

        from scripts.process_call import process

        return process(
            input_path=source,
            root=root,
            expected_signature=expected_signature,
            recording_context=context,
            **pipeline_kwargs,
        )


def protected_trash_dirs(root: Path) -> set[Path]:
    """Return trash item folders referenced by incomplete processing jobs."""
    try:
        root = _root_path(root)
    except ValueError:
        return set()
    trash_root = root / _TRASH_NAME
    protected = set()
    for job in _read_jobs(root):
        if job.get("complete") is True:
            continue
        context = _context(job)
        if context.get("category") != "trash":
            continue
        source = _job_source(root, job)
        if source is None:
            continue
        parent = source.parent
        if (parent != trash_root and trash_root not in parent.parents) or not _safe_under(root, parent):
            continue
        protected.add(parent)
    return protected
