"""Migra i video sorgente dall'archivio generale nelle cartelle delle call."""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

import scripts.settings as _cfg

_CALL_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}\.\d{2})\s+-\s+")
_VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm"}


def _safe_name(name: str) -> str:
    invalid = set(r'\/:*?"<>|')
    return re.sub(r"\s+", " ", "".join("-" if c in invalid else c for c in name)).strip()


def _call_dirs(root: Path) -> list[Path]:
    completed = root / "completate"
    containers = [completed / "Task", completed / _cfg.UNASSIGNED_CALLS_DIR_NAME]
    return sorted(
        {
            directory
            for container in containers
            if container.exists()
            for directory in container.rglob("*")
            if directory.is_dir() and _CALL_PREFIX.match(directory.name)
        },
        key=lambda path: str(path).casefold(),
    )


def _summary_stem(call_dir: Path) -> str:
    summaries = [path for path in call_dir.glob("*.md") if path.name != "README.md"]
    if len(summaries) == 1:
        return summaries[0].stem
    match = _CALL_PREFIX.match(call_dir.name)
    return call_dir.name[match.end():] if match else call_dir.name


def migrate(root: Path) -> dict[str, object]:
    archive_dir = root / "completate" / "archivio"
    if not archive_dir.exists():
        return {"moved": 0, "unmatched": [], "conflicts": []}

    calls = _call_dirs(root)
    moved = 0
    unmatched: list[str] = []
    conflicts: list[str] = []
    for source in sorted(archive_dir.iterdir(), key=lambda path: path.name.casefold()):
        if not source.is_file() or source.suffix.casefold() not in _VIDEO_EXT:
            continue
        prefix = _CALL_PREFIX.match(source.name)
        matches = [
            call for call in calls
            if prefix and call.name.startswith(prefix.group(1) + " - ")
        ]
        if len(matches) != 1:
            unmatched.append(str(source))
            continue
        target = matches[0] / f"{_safe_name(_summary_stem(matches[0]))}{source.suffix.lower()}"
        if target.exists():
            conflicts.append(str(source))
            continue
        shutil.move(str(source), str(target))
        moved += 1
        print(f"Migrato: {source.name} -> {target}")
    return {"moved": moved, "unmatched": unmatched, "conflicts": conflicts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Migra i video dall'archivio generale alle call.")
    parser.add_argument("--root-path", type=Path, default=None)
    args = parser.parse_args()
    result = migrate(args.root_path or _cfg.VAULT_ROOT)
    print(
        f"Migrati: {result['moved']}  "
        f"Non associati: {len(result['unmatched'])}  "
        f"Conflitti: {len(result['conflicts'])}"
    )
    for item in [*result["unmatched"], *result["conflicts"]]:
        print(f"Da verificare: {item}")


if __name__ == "__main__":
    main()
