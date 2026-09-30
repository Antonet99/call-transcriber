"""Rigenera gli indici README.md Obsidian per tutte le task e il globale.

Replica fedele di rebuild_indexes.ps1.
"""
from __future__ import annotations

import re
from pathlib import Path

import scripts.settings as _cfg
from scripts.filesystem import atomic_write_text, safe_name
from scripts.obsidian import frontmatter as fm

try:
    from scripts.tasks import discover_task_dirs, is_archived_task, task_labels
except ImportError:  # Backward-compatible while the shared discovery helper is deployed.
    discover_task_dirs = is_archived_task = task_labels = None


def _people_for_title(fields: dict) -> list[str]:
    """Primi due partecipanti (primo nome, esclude MY_NAME) da usare nel titolo."""
    raw = fm.normalise_string_list(fields.get("persone", []))
    my_parts = {p.lower() for p in _cfg.MY_NAME.split()} if _cfg.MY_NAME else set()
    result: list[str] = []
    for p in raw:
        if {w.lower() for w in p.split()} & my_parts:
            continue
        result.append(p.split()[0])
        if len(result) >= 2:
            break
    return result


def _try_add_people_to_dir(call_dir: Path, kanban_path: Path, archived: bool = False) -> Path:
    """Rinomina la cartella aggiungendo i partecipanti al titolo, se assenti."""
    if (call_dir / ".call-job.json").exists():
        return call_dir
    parsed = _parse_dir_name(call_dir.name)
    if not parsed:
        return call_dir
    date_str, time_str, title = parsed

    candidates = [f for f in call_dir.glob("*.md") if f.name != "README.md"]
    if not candidates:
        return call_dir

    summary_source = candidates[0]
    old_summary_stem = summary_source.stem
    people = _people_for_title(fm.read_fields(summary_source))
    if not people:
        return call_dir

    missing = [
        p for p in people
        if not re.search(r'(?i)(^|[\s,;-])' + re.escape(p) + r'($|[\s,;-])', title)
    ]
    if not missing:
        return call_dir

    new_title = f"{', '.join(missing)}, {title}"
    time_compact = time_str.replace(":", ".")
    new_name = safe_name(f"{date_str} {time_compact} - {new_title}")
    new_dir = call_dir.parent / new_name

    if new_dir == call_dir or new_dir.exists():
        return call_dir

    call_dir.rename(new_dir)

    new_summary_stem = safe_name(new_title)
    new_summary = new_dir / f"{new_summary_stem}.md"
    moved_summary = new_dir / summary_source.name
    if moved_summary != new_summary and not new_summary.exists():
        moved_summary.rename(new_summary)
    new_summary_stem = new_summary.stem if new_summary.exists() else old_summary_stem

    if kanban_path.exists():
        content = kanban_path.read_text(encoding=_UTF8)
        archive_prefix = "archivio/" if archived else ""
        old_link = f"[[{archive_prefix}{call_dir.name}/{old_summary_stem}"
        new_link = f"[[{archive_prefix}{new_name}/{new_summary_stem}"
        updated = content.replace(old_link, new_link)
        if updated != content:
            atomic_write_text(kanban_path, updated, encoding=_UTF8)

    return new_dir

_UTF8 = "utf-8"
_DIR_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2})\s+(\d{2})\.(\d{2})\s+-\s+(.+)$')
_INVALID_FNAME = set(r'\/:*?"<>|')
_PERSON_SEGMENT = re.compile(r'^[\p{Lu}\p{Lt}][\w\'-]+$' if False else r"^[A-ZÁÀÈÉÌÍÎÓÒÙÚ][a-záàèéìíîóòùú'\-]+$")
_ARCHIVE_DIR_NAME = "archivio"
_ARCHIVED_TASKS_DIR_NAME = "progetti_archiviati"
_UNASSIGNED_DIR_NAME = _cfg.UNASSIGNED_CALLS_DIR_NAME
_TASK_CALLS_START = "<!-- TASK_CALLS:START -->"
_TASK_CALLS_END = "<!-- TASK_CALLS:END -->"
_TASK_CONTEXT_PLACEHOLDER = "<!-- Compila questa sezione con una breve descrizione del progetto, del suo obiettivo e dello stato attuale. -->"

_GENERIC_HEADINGS = {
    'contesto', 'decisioni prese', 'punti discussi', 'task e action item',
    'blocchi, dubbi o rischi', 'prossimi passi', 'passaggi ambigui da verificare',
}


def _safe_name(name: str) -> str:
    return safe_name(name)


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


def _is_call_dir(directory: Path) -> bool:
    """Riconosce una cartella call senza confonderla con una cartella progetto.

    Le call correnti e archiviate hanno normalmente un nome con data/ora. La
    seconda condizione mantiene compatibili eventuali cartelle legacy senza
    data, ma esclude i progetti che contengono il loro README o la Kanban.
    """
    if not directory.is_dir() or directory.name == _ARCHIVE_DIR_NAME:
        return False
    if _parse_dir_name(directory.name):
        return True
    if (directory / "README.md").exists() or (directory / "Kanban.md").exists():
        return False
    return any(
        item.is_file() and (
            item.name == "trascrizione.txt"
            or (item.suffix.lower() == ".md" and item.name != "README.md")
        )
        for item in directory.iterdir()
    )


def _call_dirs(project_dir: Path) -> list[Path]:
    return sorted(
        [d for d in project_dir.iterdir() if _is_call_dir(d)],
        key=lambda d: d.name,
        reverse=True,
    )


def _active_task_dirs(task_root: Path) -> list[Path]:
    """Restituisce solo i progetti attivi direttamente sotto ``Task``."""
    reserved = {
        _ARCHIVE_DIR_NAME.casefold(),
        _ARCHIVED_TASKS_DIR_NAME.casefold(),
        _UNASSIGNED_DIR_NAME.casefold(),
    }
    return sorted(
        [d for d in task_root.iterdir() if d.is_dir() and d.name.casefold() not in reserved],
        key=lambda d: d.name,
    )


def _archived_task_dirs(task_root: Path) -> list[Path]:
    """Restituisce i progetti sotto il contenitore archivio reale del vault."""
    container = task_root / _ARCHIVED_TASKS_DIR_NAME
    if not container.exists():
        return []
    return sorted(
        [
            d for d in container.iterdir()
            if d.is_dir() and d.name != _ARCHIVE_DIR_NAME
        ],
        key=lambda d: d.name,
    )


def _task_records(root: Path, task_root: Path) -> list[dict]:
    """Return discovered tasks with stable relative paths and display labels."""
    if discover_task_dirs is not None:
        directories = list(discover_task_dirs(root))
        labels = task_labels(directories) if task_labels is not None else {}
        return [
            {
                "directory": directory,
                "archived": bool(is_archived_task(directory)) if is_archived_task else False,
                "label": labels.get(directory, directory.name),
            }
            for directory in sorted(directories, key=lambda path: str(path).casefold())
        ]

    active = _active_task_dirs(task_root)
    archived = _archived_task_dirs(task_root)
    return [
        {"directory": task, "archived": False, "label": task.name}
        for task in active
    ] + [
        {"directory": task, "archived": True, "label": task.name}
        for task in archived
    ]


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
    """Titles are labels; they are never a source of people metadata."""
    return []


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
        if (call_dir / ".call-job.json").exists():
            return src
        src.rename(target)
    return target


def sync_people_frontmatter(summary_path: Path, title: str) -> None:
    fields, body = fm.parse_frontmatter(summary_path.read_text(encoding=_UTF8))
    people = fm.normalise_string_list(fields.get("persone", []))
    tags = fm.normalise_string_list(fields.get("tags", []))
    if "call" not in tags:
        tags.append("call")
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


def clear_task_frontmatter(summary_path: Path) -> None:
    """Rimuove l'assegnazione task da una call senza progetto."""
    fields, body = fm.parse_frontmatter(summary_path.read_text(encoding=_UTF8))
    if "task" not in fields:
        return
    fields.pop("task", None)
    fm.write_with_frontmatter(summary_path, fields, body)


def _get_call_info(call_dir: Path, task_name: str, archived: bool = False) -> dict | None:
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
    if task_name:
        set_task_frontmatter(summary_path, task_name)
    else:
        clear_task_frontmatter(summary_path)

    return {
        "task": task_name,
        "directory": call_dir,
        "date": date_str,
        "time": time_str,
        "title": title,
        "summary_path": summary_path,
        "archived": archived,
    }


def _task_call_lines(calls: list[dict], archived_calls: list[dict]) -> list[str]:
    """Costruisce il blocco rigenerabile dell'indice call del progetto."""
    lines = [
        _TASK_CALLS_START,
        f"## Call recenti ({len(calls)})",
        "",
    ]
    if calls:
        for call in calls:
            sname = call["summary_path"].stem
            target = _to_wiki_path(str(Path(call["directory"].name) / sname))
            alias = f"{call['date']} - {call['title']}" if call['date'] else call['title']
            lines.append(f"- [[{target}|{alias}]]")
    else:
        lines.append("Nessuna call recente.")

    lines += ["", f"## Call archiviate ({len(archived_calls)})", ""]
    if archived_calls:
        for call in archived_calls:
            sname = call["summary_path"].stem
            target = _to_wiki_path(str(Path(_ARCHIVE_DIR_NAME) / call["directory"].name / sname))
            alias = f"{call['date']} - {call['title']}" if call['date'] else call['title']
            lines.append(f"- [[{target}|{alias}]]")
    else:
        lines.append("Nessuna call archiviata.")
    lines.append(_TASK_CALLS_END)
    return lines


def _split_task_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Divide un README in titolo/preambolo e sezioni di secondo livello."""
    heading_pattern = re.compile(r"(?m)^##\s+.+$")
    matches = list(heading_pattern.finditer(text))
    if not matches:
        return text.rstrip(), []

    preamble = text[:matches[0].start()].rstrip()
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(0).strip(), text[match.end():end].strip()))
    return preamble, sections


def _section_name(heading: str) -> str:
    return re.sub(r"\s+", " ", heading[3:].strip()).lower()


def _is_legacy_generated_section(name: str) -> bool:
    """Recognise only the historical generated call headings.

    A heading such as ``Call con fornitori`` is user content and must survive
    migration.
    """
    return bool(re.fullmatch(r"call (?:recenti|archiviate)(?: \(\d+\))?", name))


def _render_task_readme(
    readme: Path,
    task_name: str,
    people: list[str],
    tags: list[str],
    calls: list[dict],
    archived_calls: list[dict],
) -> None:
    """Aggiorna solo il blocco call e preserva il contesto scritto a mano."""
    existing = readme.read_text(encoding=_UTF8) if readme.exists() else ""
    call_block = "\n".join(_task_call_lines(calls, archived_calls))

    marker_pattern = re.compile(
        rf"(?ms)^(?P<prefix>.*?)^{re.escape(_TASK_CALLS_START)}\s*$.*?^{re.escape(_TASK_CALLS_END)}(?P<suffix>.*)$"
    )
    marker_match = marker_pattern.match(existing)
    if marker_match:
        prefix = marker_match.group("prefix")
        suffix = marker_match.group("suffix")
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        if suffix and not suffix.startswith("\n"):
            suffix = "\n" + suffix
        atomic_write_text(readme, prefix + call_block + suffix, encoding=_UTF8)
        return

    # Migrazione dei README legacy: le vecchie liste Call/Archivio sono
    # generate e vengono sostituite; le altre sezioni manuali restano intatte.
    preamble, sections = _split_task_sections(existing)
    title = next(
        (line.strip() for line in preamble.splitlines() if line.strip().startswith("# ")),
        f"# {task_name}",
    )
    intro = "\n".join(
        line for line in preamble.splitlines()
        if line.strip() and line.strip() != title
    ).strip()

    context_section: tuple[str, str] | None = None
    people_section: tuple[str, str] | None = None
    tags_section: tuple[str, str] | None = None
    preserved: list[tuple[str, str]] = []
    legacy_notes: list[str] = []
    generated_names = {"riepilogo", "archivio"}

    for heading, body in sections:
        name = _section_name(heading)
        if name == "contesto del progetto":
            context_section = (heading, body)
        elif name == "persone coinvolte":
            people_section = (heading, body)
        elif name == "tag":
            tags_section = (heading, body)
        elif _is_legacy_generated_section(name) or name in generated_names:
            if name == "riepilogo":
                for line in body.splitlines():
                    if line.strip() and not re.match(r"^\s*-\s*(Persone|Tag):", line, re.IGNORECASE):
                        legacy_notes.append(line)
        else:
            preserved.append((heading, body))

    lines = [title, ""]
    if intro and context_section:
        lines += [intro, ""]
    if context_section:
        lines += [context_section[0], "", context_section[1], ""]
    else:
        context_body = intro or _TASK_CONTEXT_PLACEHOLDER
        lines += ["## Contesto del progetto", "", context_body, ""]

    if people_section:
        lines += [people_section[0], "", people_section[1], ""]
    else:
        lines += ["## Persone coinvolte", ""]
        lines += [f"- {person}" for person in people]
        lines.append("")

    if tags_section:
        lines += [tags_section[0], "", tags_section[1], ""]
    else:
        lines += ["## Tag", ""]
        lines += [f"- {tag}" for tag in tags]
        lines.append("")

    if legacy_notes:
        lines += ["## Note mantenute", "", *legacy_notes, ""]
    for heading, body in preserved:
        lines += [heading, "", body, ""]

    lines += [call_block]
    atomic_write_text(readme, "\n".join(lines).rstrip() + "\n", encoding=_UTF8)


def rebuild(root: Path, archive_old: bool = False) -> dict:
    if archive_old:
        from scripts.archive_old_calls import archive as _archive
        _archive(root)

    completed_root = root / "completate"
    task_root = completed_root / "Task"
    completed_root.mkdir(parents=True, exist_ok=True)
    task_root.mkdir(parents=True, exist_ok=True)

    task_records = _task_records(root, task_root)
    active_tasks = [record["directory"] for record in task_records if not record["archived"]]
    archived_tasks = [record["directory"] for record in task_records if record["archived"]]
    all_calls: list[dict] = []
    unassigned_calls: list[dict] = []

    for task_record in task_records:
        task = task_record["directory"]
        task_is_archived = task_record["archived"]
        task_label = task_record["label"]
        task_link_path = task.relative_to(completed_root)
        kanban_path = task / "Kanban.md"
        call_dirs = _call_dirs(task)
        call_dirs = [_try_add_people_to_dir(d, kanban_path) for d in call_dirs]
        calls = [
            c for c in (_get_call_info(d, task_label, archived=False) for d in call_dirs)
            if c
        ]

        archive_dir = task / "archivio"
        archived_calls: list[dict] = []
        if archive_dir.exists():
            archived_dirs = _call_dirs(archive_dir)
            archived_dirs = [_try_add_people_to_dir(d, kanban_path, archived=True) for d in archived_dirs]
            archived_calls = [
                c for c in (_get_call_info(d, task_label, archived=True) for d in archived_dirs)
                if c
            ]

        all_calls.extend(calls)
        all_calls.extend(archived_calls)

        for call in calls + archived_calls:
            call["task_link_path"] = task_link_path

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

        # La nuova struttura con sezioni manuali vale solo per i progetti
        # attivi. I progetti già archiviati mantengono il README storico:
        # serve come contesto per la classificazione, ma non deve essere
        # riscritto o migrato da un rebuild.
        if not task_is_archived:
            readme = task / "README.md"
            _render_task_readme(readme, task.name, people, tags, calls, archived_calls)

    # Le call senza progetto restano fuori da ``Task`` e non hanno un README
    # di progetto: vengono solo normalizzate e indicizzate globalmente.
    unassigned_dir = completed_root / _UNASSIGNED_DIR_NAME
    unassigned_recent_calls: list[dict] = []
    unassigned_archived_calls: list[dict] = []
    if unassigned_dir.exists():
        unassigned_call_dirs = _call_dirs(unassigned_dir)
        unassigned_call_dirs = [
            _try_add_people_to_dir(d, unassigned_dir / "Kanban.md")
            for d in unassigned_call_dirs
        ]
        unassigned_recent_calls = [
            c for c in (
                _get_call_info(d, "", archived=False)
                for d in unassigned_call_dirs
            ) if c
        ]
        archive_dir = unassigned_dir / _ARCHIVE_DIR_NAME
        if archive_dir.exists():
            unassigned_archived_dirs = _call_dirs(archive_dir)
            unassigned_archived_dirs = [
                _try_add_people_to_dir(d, unassigned_dir / "Kanban.md", archived=True)
                for d in unassigned_archived_dirs
            ]
            unassigned_archived_calls = [
                c for c in (
                    _get_call_info(d, "", archived=True)
                    for d in unassigned_archived_dirs
                ) if c
            ]
        unassigned_calls = unassigned_recent_calls + unassigned_archived_calls
        for call in unassigned_calls:
            call["task_link_path"] = Path(_UNASSIGNED_DIR_NAME)
        all_calls.extend(unassigned_calls)
    else:
        unassigned_calls = []

    # Global README. Il contenitore Task/progetti_archiviati non è un task:
    # i suoi figli sono i progetti archiviati da proporre nella classificazione.
    global_lines: list[str] = ["# Knowledge base call", "", "## Task attive"]
    if active_tasks:
        for task_record in (record for record in task_records if not record["archived"]):
            task = task_record["directory"]
            task_label = task_record["label"]
            task_link_path = task.relative_to(completed_root)
            recent_count = sum(
                1 for c in all_calls
                if c["task"] == task_label and c["task_link_path"] == task_link_path
                and not c["archived"]
            )
            archived_count = sum(
                1 for c in all_calls
                if c["task"] == task_label and c["task_link_path"] == task_link_path
                and c["archived"]
            )
            target = _to_wiki_path(str(task_link_path / "README"))
            global_lines.append(
                f"- [[{target}|{task.name}]] - ({recent_count} call recenti, {archived_count} archiviate)"
            )
    else:
        global_lines.append("- Nessuna task presente.")

    global_lines += ["", "## Task archiviate"]
    if archived_tasks:
        for task_record in (record for record in task_records if record["archived"]):
            task = task_record["directory"]
            task_label = task_record["label"]
            task_path = task.relative_to(completed_root)
            recent_count = sum(
                1 for c in all_calls
                if c["task"] == task_label and c["task_link_path"] == task_path
                and not c["archived"]
            )
            archived_count = sum(
                1 for c in all_calls
                if c["task"] == task_label and c["task_link_path"] == task_path
                and c["archived"]
            )
            target = _to_wiki_path(str(task_path / "README"))
            global_lines.append(
                f"- [[{target}|{task.name}]] - ({recent_count} call recenti, {archived_count} archiviate)"
            )
    else:
        global_lines.append("- Nessuna task archiviata.")

    global_lines += ["", f"## Call senza progetto ({len(unassigned_calls)})", ""]
    if unassigned_calls:
        for call in unassigned_calls:
            sname = call["summary_path"].stem
            call_base = Path(_UNASSIGNED_DIR_NAME)
            if call["archived"]:
                call_base /= _ARCHIVE_DIR_NAME
            target = _to_wiki_path(str(call_base / call["directory"].name / sname))
            alias = f"{call['date']} - {call['title']}" if call["date"] else call["title"]
            global_lines.append(f"- [[{target}|{alias}]]")
    else:
        global_lines.append("- Nessuna call senza progetto.")

    global_lines += ["", f"## Ultime {_cfg.INDEX_LATEST_CALLS_COUNT} call"]
    latest = sorted(all_calls, key=lambda c: c["directory"].name, reverse=True)[:_cfg.INDEX_LATEST_CALLS_COUNT]
    if latest:
        for call in latest:
            sname = call["summary_path"].stem
            call_base = call["task_link_path"]
            if call["archived"]:
                call_base /= _ARCHIVE_DIR_NAME
            target = _to_wiki_path(str(call_base / call["directory"].name / sname))
            dt = f"{call['date']} {call['time']}" if call['date'] else call["directory"].name
            task_label = call["task"] or _UNASSIGNED_DIR_NAME
            global_lines.append(f"- {dt} - [[{target}|{call['title']}]] (task: {task_label})")
    else:
        global_lines.append("- Nessuna call presente.")

    atomic_write_text(completed_root / "README.md", "\n".join(global_lines) + "\n", encoding=_UTF8)

    return {
        "global_index": str(completed_root / "README.md"),
        "task_indexes": len(task_records),
        "calls": len(all_calls),
        "recent_calls": sum(1 for c in all_calls if not c["archived"]),
        "archived_calls": sum(1 for c in all_calls if c["archived"]),
        "unassigned_calls": len(unassigned_calls),
    }
