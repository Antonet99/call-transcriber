"""Lettura e aggiornamento Kanban.md Obsidian.

Preserva il blocco %% kanban:settings %% in fondo al file.
"""
from __future__ import annotations

import re
from pathlib import Path

import scripts.settings as _cfg

_UTF8 = "utf-8"
_CARD_RE = re.compile(r'^\-\s+\[\s*\]')
_SETTINGS_RE = re.compile(r'^%%\s*kanban:settings')
_SECTION_RE = re.compile(r'^##\s+')
_IDEE_RE = re.compile(r'^##\s+Idee da call\s*$')


def _default_settings(tag_slug: str) -> str:
    return (
        '{"kanban-plugin":"board","list-collapse":[false,false,false],'
        f'"tag-colors":[{{"tagKey":"#{tag_slug}","color":"","backgroundColor":"rgba(89, 238, 216, 0.1)"}}]}}'
    )


def create(path: Path, task_name: str) -> None:
    tag_slug = re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', task_name.lower())).strip('-')
    lines = [
        "---",
        "kanban-plugin: board",
        "tipo: kanban",
        "scope: progetto",
        f'progetto: "[[{task_name}]]"',
        f'tag_progetto: "#{tag_slug}"',
        "tags:",
        "  - kanban",
        f"  - {tag_slug}",
        "---",
        "",
        "## Idee da call",
        "",
        "## Da fare / In corso",
        "",
        "## Fatto",
        "",
        "%% kanban:settings",
        "```",
        _default_settings(tag_slug),
        "```",
        "%%",
    ]
    path.write_text("\n".join(lines) + "\n", encoding=_UTF8)


def get_all_cards(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding=_UTF8).splitlines()
    return [ln.strip() for ln in lines if _CARD_RE.match(ln.strip())]


def update(path: Path, new_cards: list[str]) -> int:
    """Inserisce new_cards sotto ## Idee da call. Restituisce il numero di card aggiunte."""
    if not new_cards:
        return 0

    content = path.read_text(encoding=_UTF8)
    lines = content.splitlines()

    settings_start = next(
        (i for i, ln in enumerate(lines) if _SETTINGS_RE.match(ln)), len(lines)
    )

    idee_start = next(
        (i for i, ln in enumerate(lines) if _IDEE_RE.match(ln)), -1
    )
    if idee_start < 0:
        return 0

    section_end = settings_start
    for i in range(idee_start + 1, settings_start):
        if _SECTION_RE.match(lines[i]):
            section_end = i
            break

    # Trova ultimo non-vuoto nella sezione
    insert_after = idee_start
    for i in range(section_end - 1, idee_start, -1):
        if lines[i].strip():
            insert_after = i
            break

    before = lines[: insert_after + 1]
    after = lines[insert_after + 1 :]
    new_lines = before + new_cards + after
    path.write_text("\n".join(new_lines) + "\n", encoding=_UTF8)
    return len(new_cards)
