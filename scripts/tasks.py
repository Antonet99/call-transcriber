"""Shared task discovery and unambiguous classification labels."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re

import scripts.settings as cfg

_CALL = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}\.\d{2}\s+-\s+")
_GROUPS = {"archivio", "archiviati", "archiviate", "progetti_archiviati"}


def discover_task_dirs(root: Path) -> list[Path]:
    task_root = root / "completate" / "Task"
    if not task_root.is_dir():
        return []
    result = []

    def visit(directory: Path) -> None:
        children = sorted(p for p in directory.iterdir() if p.is_dir() and not p.is_symlink())
        for child in children:
            if child.name.casefold() == cfg.UNASSIGNED_CALLS_DIR_NAME.casefold() or _CALL.match(child.name):
                continue
            if child.name.casefold() in _GROUPS:
                if child.name.casefold() != "archivio":
                    visit(child)
                continue
            nested = [p for p in child.iterdir() if p.is_dir() and not p.is_symlink()
                      and not _CALL.match(p.name) and p.name.casefold() not in _GROUPS
                      and p.name.casefold() != cfg.UNASSIGNED_CALLS_DIR_NAME.casefold()]
            if nested and any((p / "README.md").is_file() for p in nested):
                visit(child)
            else:
                result.append(child)

    visit(task_root)
    return sorted(result, key=lambda p: (p.name.casefold(), str(p).casefold()))


def task_labels(task_dirs: list[Path]) -> dict[Path, str]:
    counts = Counter(p.name.casefold() for p in task_dirs)
    labels = {}
    for path in task_dirs:
        if counts[path.name.casefold()] == 1:
            labels[path] = path.name
            continue
        anchor = next((p for p in path.parents if p.name == "Task"), None)
        labels[path] = "Task/" + path.relative_to(anchor).as_posix() if anchor else path.as_posix()
    return labels


def is_archived_task(path: Path) -> bool:
    anchor = next((p for p in path.parents if p.name == "Task"), None)
    parts = path.relative_to(anchor).parts if anchor else path.parts
    return any(part.casefold() in _GROUPS for part in parts)
