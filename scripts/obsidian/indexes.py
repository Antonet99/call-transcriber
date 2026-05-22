"""Rigenera gli indici README.md Obsidian per tutte le task e il globale.

Replica fedele di rebuild_indexes.ps1.
"""
from __future__ import annotations

import re
from pathlib import Path

import scripts.settings as _cfg
from scripts.obsidian import frontmatter as fm

_UTF8 = "utf-8"
_DIR_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2})\s+(\d{2})\.(\d{2})\s+-\s+(.+)$')
_INVALID_FNAME = set(r'\/:*?"<>|')
_PERSON_SEGMENT = re.compile(r'^[\p{Lu}\p{Lt}][\w\'-]+$' if False else r"^[A-ZÁÀÈÉÌÍÎÓÒÙÚ][a-záàèéìíîóòùú'\-]+$")

_GENERIC_HEADINGS = {
    'contesto', 'decisioni prese', 'punti discussi', 'task e action item',
    'blocchi, dubbi o rischi', 'prossimi passi', 'passaggi ambigui da verificare',
}


def _safe_name(name: str) -> str:
    safe = "".join('-' if c in _INVALID_FNAME else c for c in name)
    return re.sub(r'\s+', ' ', safe).strip()


def _to_wiki_path(path: str) -> str:
    return path.replace("\\", "/")


def _to_kebab(value: str) -> str:
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', value.lower())).strip('-')


def _parse_dir_name(name: str) -> tuple[str, str, str] | None:
    """Restituisce (date_str, time_str HH:MM, title) o None."""
    m = _DIR_PATTERN.match(name)
    if not m:
        return None
    return m.group(1), f"{m.group(2)}:{m.group(3)}", m.group(4).strip()


def _short_title(title: str) -> str:
    clean = re.sub(r'[#*_`]', '', title).strip()
    words = clean.split()
    if len(words) > _cfg.INDEX_TITLE_MAX_WORDS:
        clean = " ".join(words[:_cfg.INDEX_TITLE_MAX_WORDS])
    return _safe_name(clean)


def _is_person_segment(value: str) -> bool:
    parts = re.split(r'\s+(?:e|and)\s+|[&/]', value)
    for part in parts:
        part = part.strip()
        if not part:
            return False
        words = part.split()
        if len(words) > 2:
            return False
        for w in words:
            if re.match(r'^[A-Z0-9_]{2,}$', w):
                return False
            if not re.match(r'^[A-ZÁÀÈÉÌÍÎÓÒÙÚ][a-zA-ZáàèéìíîóòùúÁÀÈÉÌÍÎÓÒÙÚ\'\-]+$', w):
                return False
    return True


def _people_from_title(title: str) -> list[str]:
    m = re.match(r'^([^,]+),\s*.+$', title)
    if not m:
        return []
    prefix = m.group(1).strip()
    if not _is_person_segment(prefix):
        return []
    return [p.strip() for p in re.split(r'\s+(?:e|and)\s+|[&/]', prefix) if p.strip()]


def _summary_title_from_body(body: str) -> str:
    for line in body.splitlines():
        m = re.match(r'^##\s+(.+?)\s*$', line)
        if m:
            t = _short_title(m.group(1))
            if t and t.lower() not in _GENERIC_HEADINGS:
                return t
    return ''


def _find_summary_file(call_dir: Path, title: str) -> Path | None:
    expected = call_dir / (_safe_name(title) + ".md")
    if expected.exists():
        return expected
    legacy = call_dir / "riassunto.md"
    if legacy.exists():
        return legacy
    candidates = [f for f in call_dir.glob("*.md") if f.name != "README.md"]
    return candidates[0] if candidates else None


def sync_summary_file(call_dir: Path, title: str) -> Path | None:
    src = _find_summary_file(call_dir, title)
    if not src:
        return None
    target = call_dir / (_safe_name(title) + ".md")
    if src != target:
        src.rename(target)
    return target


def sync_people_frontmatter(summary_path: Path, title: str) -> None:
    people_from_title = _people_from_title(title)
    if not people_from_title:
        return

    fields, body = fm.parse_frontmatter(summary_path.read_text(encoding=_UTF8))
    existing_people = fields.get("persone", [])
    if isinstance(existing_people, str):
        existing_people = [existing_people] if existing_people else []
    existing_tags = fields.get("tags", [])
    if isinstance(existing_tags, str):
        existing_tags = [existing_tags] if existing_tags else []

    people = list(dict.fromkeys(existing_people + people_from_title))
    tags = list(dict.fromkeys(existing_tags + ["call"] + [_to_kebab(p) for p in people_from_title]))

    fields["persone"] = people
    fields["tags"] = tags
    fm.write_with_frontmatter(summary_path, fields, body)


def set_task_frontmatter(summary_path: Path, task_name: str) -> None:
    if not task_name:
        return
    fields, body = fm.parse_frontmatter(summary_path.read_text(encoding=_UTF8))

    prefix_keys = [k for k in ("data", "ora") if k in fields]
    task_line_val = f"[[{task_name}]]"
    fields.pop("task", None)

    ordered: dict = {}
    for k in prefix_keys:
        ordered[k] = fields[k]
    ordered["task"] = task_line_val
    for k, v in fields.items():
        if k not in ordered:
            ordered[k] = v

    fm.write_with_frontmatter(summary_path, ordered, body)


def _get_call_info(call_dir: Path, task_name: str) -> dict | None:
    parsed = _parse_dir_name(call_dir.name)
    if parsed is None:
        title = call_dir.name
        date_str = ""
        time_str = ""
    else:
        date_str, time_str, title = parsed

    summary_path = sync_summary_file(call_dir, title)
    if not summary_path:
        return None

    sync_people_frontmatter(summary_path, title)
    set_task_frontmatter(summary_path, task_name)

    return {
        "task": task_name,
        "directory": call_dir,
        "date": date_str,
        "time": time_str,
        "title": title,
        "summary_path": summary_path,
    }


def rebuild(root: Path) -> dict:
    from scripts.archive_old_calls import archive as _archive
    _archive(root)

    completed_root = root / "completate"
    task_root = completed_root / "Task"
    completed_root.mkdir(parents=True, exist_ok=True)
    task_root.mkdir(parents=True, exist_ok=True)

    tasks = sorted([d for d in task_root.iterdir() if d.is_dir()], key=lambda d: d.name)
    all_calls: list[dict] = []

    for task in tasks:
        call_dirs = sorted(
            [d for d in task.iterdir() if d.is_dir() and d.name != "archivio"],
            key=lambda d: d.name,
            reverse=True,
        )
        calls = [c for c in (_get_call_info(d, task.name) for d in call_dirs) if c]

        archive_dir = task / "archivio"
        archived_calls: list[dict] = []
        if archive_dir.exists():
            archived_dirs = sorted(
                [d for d in archive_dir.iterdir() if d.is_dir()],
                key=lambda d: d.name,
                reverse=True,
            )
            archived_calls = [c for c in (_get_call_info(d, task.name) for d in archived_dirs) if c]

        all_calls.extend(calls)

        # Aggregate people and tags from both active and archived
        people: list[str] = []
        tags: list[str] = []
        for call in calls + archived_calls:
            fields = fm.read_fields(call["summary_path"])
            p = fields.get("persone", [])
            t = fields.get("tags", [])
            people += p if isinstance(p, list) else ([p] if p else [])
            tags += t if isinstance(t, list) else ([t] if t else [])

        people = sorted(set(filter(None, people)))
        tags = sorted(set(filter(None, tags)))

        lines: list[str] = [f"# {task.name}", ""]
        if people or tags:
            lines += ["## Riepilogo"]
            if people:
                lines.append(f"- Persone: {', '.join(people)}")
            if tags:
                lines.append(f"- Tag: {', '.join(tags)}")
            lines.append("")

        lines.append(f"## Call ({len(calls)})")
        for call in calls:
            sname = call["summary_path"].stem
            target = _to_wiki_path(str(Path(call["directory"].name) / sname))
            alias = f"{call['date']} - {call['title']}" if call['date'] else call['title']
            lines.append(f"- [[{target}|{alias}]]")

        if archived_calls:
            lines += ["", "## Archivio"]
            for call in archived_calls:
                sname = call["summary_path"].stem
                target = _to_wiki_path(str(Path("archivio") / call["directory"].name / sname))
                alias = f"{call['date']} - {call['title']}" if call['date'] else call['title']
                lines.append(f"- [[{target}|{alias}]]")

        readme = task / "README.md"
        readme.write_text("\n".join(lines) + "\n", encoding=_UTF8)

    # Global README
    global_lines: list[str] = ["# Knowledge base call", "", "## Task attive"]
    if tasks:
        for task in tasks:
            count = sum(1 for c in all_calls if c["task"] == task.name)
            target = _to_wiki_path(str(Path("Task") / task.name / "README"))
            global_lines.append(f"- [[{target}|{task.name}]] - {count} call")
    else:
        global_lines.append("- Nessuna task presente.")

    global_lines += ["", f"## Ultime {_cfg.INDEX_LATEST_CALLS_COUNT} call"]
    latest = sorted(all_calls, key=lambda c: c["directory"].name, reverse=True)[:_cfg.INDEX_LATEST_CALLS_COUNT]
    if latest:
        for call in latest:
            sname = call["summary_path"].stem
            target = _to_wiki_path(
                str(Path("Task") / call["task"] / call["directory"].name / sname)
            )
            dt = f"{call['date']} {call['time']}" if call['date'] else call["directory"].name
            global_lines.append(f"- {dt} - [[{target}|{call['title']}]] (task: {call['task']})")
    else:
        global_lines.append("- Nessuna call archiviata.")

    (completed_root / "README.md").write_text("\n".join(global_lines) + "\n", encoding=_UTF8)

    return {
        "global_index": str(completed_root / "README.md"),
        "task_indexes": len(tasks),
        "calls": len(all_calls),
    }
