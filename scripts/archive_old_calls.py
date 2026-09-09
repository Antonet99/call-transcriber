"""Archivia automaticamente le call più vecchie di N giorni.

Sposta le cartelle in completate/Task/<task>/archivio/<cartella> o
completate/Senza progetto/archivio/<cartella>,
aggiorna il frontmatter (archived: true, archived_at) e corregge
i wikilink nella Kanban.md del task.
"""
from __future__ import annotations

import argparse
import re
from datetime import date, datetime
from pathlib import Path

import scripts.settings as _cfg
from scripts.obsidian import frontmatter as fm

_DATE_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2})\s+\d{2}\.\d{2}\s+-\s+')
_UTF8 = "utf-8"
_VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm"}


def _parse_call_date(dir_name: str) -> date | None:
    m = _DATE_PATTERN.match(dir_name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _update_kanban_links(kanban_path: Path, call_dir_name: str) -> None:
    if not kanban_path.exists():
        return
    content = kanban_path.read_text(encoding=_UTF8)
    escaped = re.escape(call_dir_name)
    pattern = r'\[\[' + escaped + r'/'
    replacement = f'[[archivio/{call_dir_name}/'
    if not re.search(pattern, content):
        return
    updated = re.sub(pattern, replacement, content)
    kanban_path.write_text(updated, encoding=_UTF8)


def _remove_video_recordings(call_dir: Path) -> int:
    deleted = 0
    for item in call_dir.iterdir():
        if item.is_file() and item.suffix.casefold() in _VIDEO_EXT:
            item.unlink()
            deleted += 1
    return deleted


def _cleanup_archived_videos(root: Path, days: int) -> int:
    if days <= 0:
        return 0
    completed_root = root / "completate"
    containers = [completed_root / "Task", completed_root / _cfg.UNASSIGNED_CALLS_DIR_NAME]
    cutoff = date.today()
    deleted = 0
    call_dirs = {
        call_dir
        for container in containers
        if container.exists()
        for call_dir in container.rglob("*")
        if call_dir.is_dir()
        and _DATE_PATTERN.match(call_dir.name)
        and any(part.casefold() == "archivio" for part in call_dir.relative_to(container).parts)
    }
    for call_dir in call_dirs:
        call_date = _parse_call_date(call_dir.name)
        if call_date is None or (cutoff - call_date).days < days:
            continue
        deleted += _remove_video_recordings(call_dir)
    return deleted


def _archive_direct_calls(
    container: Path,
    archive_dir: Path,
    label: str,
    days: int,
    kanban_path: Path | None = None,
) -> tuple[int, int, int]:
    cutoff = date.today()
    archived = 0
    skipped = 0
    videos_deleted = 0
    call_dirs = [
        d for d in container.iterdir()
        if d.is_dir() and d.name.casefold() != "archivio" and _DATE_PATTERN.match(d.name)
    ]

    for call_dir in sorted(call_dirs):
        call_date = _parse_call_date(call_dir.name)
        if call_date is None or (cutoff - call_date).days < days:
            skipped += 1
            continue

        dest = archive_dir / call_dir.name
        if dest.exists():
            skipped += 1
            continue

        videos_deleted += _remove_video_recordings(call_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        call_dir.rename(dest)

        for md in dest.glob("*.md"):
            if md.name != "README.md":
                fm.add_archived_fields(md)

        if kanban_path is not None:
            _update_kanban_links(kanban_path, call_dir.name)
        print(f"Archiviata: {label} / {call_dir.name}")
        archived += 1

    return archived, skipped, videos_deleted


def archive(root: Path, days: int | None = None) -> dict[str, int]:
    if days is None:
        days = _cfg.ARCHIVE_DAYS
    completed_root = root / "completate"
    task_root = completed_root / "Task"
    archived = 0
    skipped = 0
    videos_deleted = 0

    if task_root.exists():
        active_tasks = [
            d for d in task_root.iterdir()
            if d.is_dir()
            and d.name.casefold() not in {
                _cfg.UNASSIGNED_CALLS_DIR_NAME.casefold(),
                "progetti_archiviati",
            }
        ]
        archived_tasks_root = task_root / "progetti_archiviati"
        archived_tasks = (
            [d for d in archived_tasks_root.iterdir() if d.is_dir()]
            if archived_tasks_root.exists()
            else []
        )
        for task_dir in sorted(active_tasks + archived_tasks):
            moved, ignored, deleted = _archive_direct_calls(
                task_dir,
                task_dir / "archivio",
                task_dir.name,
                days,
                task_dir / "Kanban.md",
            )
            archived += moved
            skipped += ignored
            videos_deleted += deleted

    unassigned_dir = completed_root / _cfg.UNASSIGNED_CALLS_DIR_NAME
    if unassigned_dir.exists():
        moved, ignored, deleted = _archive_direct_calls(
            unassigned_dir,
            unassigned_dir / "archivio",
            unassigned_dir.name,
            days,
        )
        archived += moved
        skipped += ignored
        videos_deleted += deleted

    videos_deleted += _cleanup_archived_videos(root, days)
    return {"archived": archived, "skipped": skipped, "videos_deleted": videos_deleted}


def main() -> None:
    parser = argparse.ArgumentParser(description="Archivia call più vecchie di N giorni.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--root-path", type=Path, default=None)
    args = parser.parse_args()

    root = args.root_path or _cfg.VAULT_ROOT
    result = archive(root, args.days)
    print(
        f"Archiviate: {result['archived']}  "
        f"Saltate: {result['skipped']}  "
        f"Video rimossi: {result['videos_deleted']}"
    )


if __name__ == "__main__":
    main()
