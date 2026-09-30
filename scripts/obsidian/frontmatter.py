"""Parsing e scrittura del frontmatter YAML nei file Markdown Obsidian.

Preserva il formato esatto: UTF-8 senza BOM, array inline `[a, b]`,
stringhe con wikilink tra doppi apici.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from scripts.filesystem import atomic_write_text

_UTF8 = "utf-8"
_SEP = "---"


class _FrontmatterDumper(yaml.SafeDumper):
    """Safe PyYAML dumper with compact, valid inline lists."""

    def represent_list(self, data: list[Any]) -> yaml.nodes.SequenceNode:
        node = super().represent_list(data)
        node.flow_style = True
        return node


_FrontmatterDumper.add_representer(list, _FrontmatterDumper.represent_list)


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

    yaml_text = "\n".join(fm_lines[1:-1]).strip()
    if yaml_text:
        try:
            parsed = yaml.safe_load(yaml_text) or {}
            if isinstance(parsed, dict):
                return dict(parsed), body
        except yaml.YAMLError:
            pass

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


def strip_frontmatter(text: str) -> str:
    """Restituisce il body Markdown senza il frontmatter iniziale, se presente."""
    _, body_lines = _split_raw(text)
    return "\n".join(body_lines).strip()


def read_fields(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding=_UTF8)
    fields, _ = parse_frontmatter(text)
    return fields


# ---------------------------------------------------------------------------
# Scrittura
# ---------------------------------------------------------------------------


def _write_if_changed(path: Path, content: str) -> None:
    try:
        if path.read_text(encoding=_UTF8) == content:
            return
    except FileNotFoundError:
        pass
    atomic_write_text(path, content, encoding=_UTF8)


def _fmt_value(val: Any) -> str:
    return yaml.dump(
        val,
        Dumper=_FrontmatterDumper,
        allow_unicode=True,
        default_flow_style=isinstance(val, list),
        sort_keys=False,
    ).strip()


def render_frontmatter(fields: dict[str, Any], body: str) -> str:
    lines = [_SEP]
    if fields:
        rendered = yaml.dump(
            dict(fields),
            Dumper=_FrontmatterDumper,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=120,
        ).rstrip()
        lines.extend(rendered.splitlines())
    lines.append(_SEP)
    body_stripped = body.lstrip("\n")
    if body_stripped:
        return "\n".join(lines) + "\n\n" + body_stripped
    return "\n".join(lines) + "\n"


def write_with_frontmatter(path: Path, fields: dict[str, Any], body: str) -> None:
    content = render_frontmatter(fields, body)
    _write_if_changed(path, content)


# ---------------------------------------------------------------------------
# Operazioni in-place
# ---------------------------------------------------------------------------

def update_field(path: Path, key: str, value: Any) -> None:
    """Aggiorna o aggiunge un singolo campo senza riscrivere il body."""
    text = path.read_text(encoding=_UTF8)
    fields, body = parse_frontmatter(text)
    fields[key] = value
    write_with_frontmatter(path, fields, body)


def add_archived_fields(path: Path, archived_at: date | None = None) -> None:
    """Aggiunge archived: true e archived_at al frontmatter se assenti."""
    text = path.read_text(encoding=_UTF8)
    fields, body = parse_frontmatter(text)
    if fields.get("archived") is True and fields.get("archived_at"):
        return
    fields["archived"] = True
    fields.setdefault("archived_at", archived_at or date.today())
    write_with_frontmatter(path, fields, body)


def normalise_string_list(value: Any) -> list[str]:
    """Normalise malformed list metadata without raising during a rebuild."""
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    result: list[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item and item not in result:
            result.append(item)
    return result


def get_people(path: Path) -> list[str]:
    fields = read_fields(path)
    return normalise_string_list(fields.get("persone", []))


def ensure_kebab_tag(tags: list[str], new_tag: str) -> list[str]:
    if new_tag not in tags:
        tags = list(tags) + [new_tag]
    return tags


def _to_kebab(value: str) -> str:
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', value.lower())).strip('-')
