"""Archivia automaticamente le call più vecchie di N giorni.

Sposta le cartelle in completate/Task/<task>/archivio/<cartella>,
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


def archive(root: Path, days: int | None = None) -> dict[str, int]:
    if days is None:
        days = _cfg.ARCHIVE_DAYS
    task_root = root / "completate" / "Task"
    if not task_root.exists():
        return {"archived": 0, "skipped": 0}

    cutoff = date.today()
    archived = 0
    skipped = 0

    for task_dir in sorted(task_root.iterdir()):
        if not task_dir.is_dir():
            continue

        archive_dir = task_dir / "archivio"
        kanban_path = task_dir / "Kanban.md"

        call_dirs = [
            d for d in task_dir.iterdir()
            if d.is_dir() and d.name != "archivio" and _DATE_PATTERN.match(d.name)
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

            archive_dir.mkdir(parents=True, exist_ok=True)
            call_dir.rename(dest)

            for md in dest.glob("*.md"):
                if md.name != "README.md":
                    fm.add_archived_fields(md)

            _update_kanban_links(kanban_path, call_dir.name)
            print(f"Archiviata: {task_dir.name} / {call_dir.name}")
            archived += 1

    return {"archived": archived, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description="Archivia call più vecchie di N giorni.")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--root-path", type=Path, default=None)
    args = parser.parse_args()

    root = args.root_path or Path(__file__).parent.parent
    result = archive(root, args.days)
    print(f"Archiviate: {result['archived']}  Saltate: {result['skipped']}")


if __name__ == "__main__":
    main()
