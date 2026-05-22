"""Parsing e scrittura del frontmatter YAML nei file Markdown Obsidian.

Preserva il formato esatto: UTF-8 senza BOM, array inline `[a, b]`,
stringhe con wikilink tra doppi apici.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

_UTF8 = "utf-8"
_SEP = "---"


# ---------------------------------------------------------------------------
# Lettura
# ---------------------------------------------------------------------------

def _split_raw(text: str) -> tuple[list[str], list[str]]:
    """Restituisce (righe frontmatter inclusi i ---) e (righe body)."""
    lines = text.splitlines()
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1

    if start >= len(lines) or lines[start].strip() != _SEP:
        return [], lines

    for i in range(start + 1, len(lines)):
        if lines[i].strip() == _SEP:
            return lines[start : i + 1], lines[i + 1 :]

    return [], lines


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Restituisce (dizionario campi, body come stringa)."""
    fm_lines, body_lines = _split_raw(text)
    body = "\n".join(body_lines)

    if not fm_lines:
        return {}, body

    fields: dict[str, Any] = {}
    i = 1
    end = len(fm_lines) - 1
    while i < end:
        line = fm_lines[i]
        m = re.match(r'^(\w[\w_-]*)\s*:\s*(.*)', line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()

        if val == "" or val is None:
            # multi-line list
            items: list[str] = []
            i += 1
            while i < end and fm_lines[i].startswith("  - "):
                items.append(fm_lines[i][4:].strip())
                i += 1
            fields[key] = items
            continue

        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            fields[key] = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
        else:
            fields[key] = val.strip('"').strip("'")

        i += 1

    return fields, body


def read_fields(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding=_UTF8)
    fields, _ = parse_frontmatter(text)
    return fields


# ---------------------------------------------------------------------------
# Scrittura
# ---------------------------------------------------------------------------

def _fmt_value(val: Any) -> str:
    if isinstance(val, list):
        return "[" + ", ".join(str(v) for v in val) + "]"
    s = str(val)
    if "[[" in s or ":" in s or s.startswith('"'):
        if not (s.startswith('"') and s.endswith('"')):
            s = f'"{s}"'
    return s


def render_frontmatter(fields: dict[str, Any], body: str) -> str:
    lines = [_SEP]
    for k, v in fields.items():
        lines.append(f"{k}: {_fmt_value(v)}")
    lines.append(_SEP)
    body_stripped = body.lstrip("\n")
    if body_stripped:
        return "\n".join(lines) + "\n\n" + body_stripped
    return "\n".join(lines) + "\n"


def write_with_frontmatter(path: Path, fields: dict[str, Any], body: str) -> None:
    content = render_frontmatter(fields, body)
    path.write_text(content, encoding=_UTF8)


# ---------------------------------------------------------------------------
# Operazioni in-place
# ---------------------------------------------------------------------------

def update_field(path: Path, key: str, value: Any) -> None:
    """Aggiorna o aggiunge un singolo campo senza riscrivere il body."""
    text = path.read_text(encoding=_UTF8)
    fm_lines, body_lines = _split_raw(text)

    if not fm_lines:
        new_content = f"{_SEP}\n{key}: {_fmt_value(value)}\n{_SEP}\n\n" + "\n".join(body_lines)
        path.write_text(new_content, encoding=_UTF8)
        return

    new_line = f"{key}: {_fmt_value(value)}"
    pattern = re.compile(r'^' + re.escape(key) + r'\s*:')
    replaced = False
    new_fm: list[str] = []
    for line in fm_lines:
        if pattern.match(line):
            new_fm.append(new_line)
            replaced = True
        else:
            new_fm.append(line)

    if not replaced:
        new_fm.insert(-1, new_line)

    body = "\n".join(body_lines)
    path.write_text("\n".join(new_fm) + "\n" + ("\n" + body.lstrip("\n") if body.strip() else ""), encoding=_UTF8)


def add_archived_fields(path: Path, archived_at: date | None = None) -> None:
    """Aggiunge archived: true e archived_at al frontmatter se assenti."""
    text = path.read_text(encoding=_UTF8)
    fm_lines, body_lines = _split_raw(text)
    at_str = (archived_at or date.today()).isoformat()

    if not fm_lines:
        new_content = (
            f"{_SEP}\narchived: true\narchived_at: {at_str}\n{_SEP}\n\n"
            + "\n".join(body_lines)
        )
        path.write_text(new_content, encoding=_UTF8)
        return

    for line in fm_lines:
        if re.match(r'^\s*archived\s*:', line):
            return  # già archiviato

    new_fm = fm_lines[:-1] + [f"archived: true", f"archived_at: {at_str}", fm_lines[-1]]
    body = "\n".join(body_lines)
    path.write_text(
        "\n".join(new_fm) + "\n" + ("\n" + body.lstrip("\n") if body.strip() else ""),
        encoding=_UTF8,
    )


def get_people(path: Path) -> list[str]:
    fields = read_fields(path)
    val = fields.get("persone", [])
    if isinstance(val, list):
        return [v for v in val if v]
    if val:
        return [val]
    return []


def ensure_kebab_tag(tags: list[str], new_tag: str) -> list[str]:
    if new_tag not in tags:
        tags = list(tags) + [new_tag]
    return tags


def _to_kebab(value: str) -> str:
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', value.lower())).strip('-')
