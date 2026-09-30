"""Aggiorna la Kanban del progetto con card estratte dai riassunti call."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import scripts.settings as _cfg
from scripts.llm import common as llm_common
from scripts.llm.providers import get_provider
from scripts.obsidian import kanban

_CARD_RE = re.compile(r'^\-\s+\[\s*\]')
_DIR_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\s+\d{2}\.\d{2}\s+-\s+')
_UTF8 = "utf-8"


def update_from_summary(
    summary_path: Path,
    task_dir: Path,
    model: str = "",
) -> int:
    provider = get_provider()
    if not provider.is_available():
        raise RuntimeError("Claude CLI non disponibile: Kanban lasciata invariata.")

    kanban_path = task_dir / "Kanban.md"
    if not kanban_path.exists():
        kanban.create(kanban_path, task_dir.name)
        print(f"[kanban] Creata: {kanban_path}")
    if not kanban.has_ideas_section(kanban_path):
        raise ValueError(f"La Kanban non contiene la sezione 'Idee da call': {kanban_path}")

    summary = summary_path.read_text(encoding=_UTF8)
    kanban_content = kanban_path.read_text(encoding=_UTF8)
    existing_cards = kanban.get_all_cards(kanban_path)

    summary_base = summary_path.stem
    try:
        relative_summary = summary_path.relative_to(task_dir).with_suffix("")
    except ValueError as exc:
        raise ValueError(f"Il riassunto non appartiene alla task: {summary_path}") from exc
    call_wiki = relative_summary.as_posix()
    call_label = re.sub(r'^\d{4}-\d{2}-\d{2}\s+\d{2}\.\d{2}\s+-\s+', '', summary_base)
    if not call_label:
        call_label = summary_base

    prompt = llm_common.build_kanban_prompt(summary, kanban_content, call_wiki, call_label)
    answer = provider.invoke_light(prompt, model).strip()

    if not answer:
        raise ValueError(f"{summary_base}: risposta LLM vuota.")
    if answer.upper() == "NONE":
        print(f"[kanban] {summary_base}: nessuna card nuova.")
        return 0

    new_cards = [
        ln.strip() for ln in answer.splitlines()
        if _CARD_RE.match(ln.strip())
    ][:_cfg.KANBAN_MAX_CARDS_PER_CALL]

    if not new_cards:
        raise ValueError(f"{summary_base}: risposta LLM non nel formato atteso.")

    existing_ids = {_card_identity(card) for card in existing_cards}
    filtered = []
    for card in new_cards:
        identity = _card_identity(card)
        if not identity or identity in existing_ids:
            continue
        filtered.append(card)
        existing_ids.add(identity)

    if not filtered:
        print(f"[kanban] {summary_base}: card già presenti, skip.")
        return 0

    added = kanban.update(kanban_path, filtered)
    print(f"[kanban] {summary_base}: {added} card aggiunte.")
    return added


def _card_identity(card: str) -> str:
    """Compare the activity text independently of status, tags and sources."""
    value = re.sub(r"^\s*-\s*\[[ xX]\]\s*", "", card).strip()
    value = re.sub(r"\[\[[^\]]+\]\]", " ", value)
    value = re.sub(r"(?<!\w)#[\w-]+", " ", value)
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.casefold().split())


def update_all(
    task_dir: Path,
    model: str = "",
    include_archive: bool = False,
) -> int:
    """Scansiona tutte le call della task e aggiorna la Kanban per ognuna."""
    call_dirs: list[Path] = sorted(
        [d for d in task_dir.iterdir() if d.is_dir() and _DIR_DATE_RE.match(d.name)],
        key=lambda d: d.name,
    )
    if include_archive:
        archive_dir = task_dir / "archivio"
        if archive_dir.exists():
            call_dirs += sorted(
                [d for d in archive_dir.iterdir() if d.is_dir() and _DIR_DATE_RE.match(d.name)],
                key=lambda d: d.name,
            )

    if not call_dirs:
        print(f"[kanban] Nessuna call trovata in {task_dir.name}.")
        return 0

    print(f"[kanban] {len(call_dirs)} call trovate in {task_dir.name}.")
    total = 0
    for call_dir in call_dirs:
        candidates = [f for f in call_dir.glob("*.md") if f.name != "README.md"]
        if not candidates:
            continue
        summary_path = candidates[0]
        total += update_from_summary(summary_path, task_dir, model)

    print(f"[kanban] Totale card aggiunte: {total}")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggiorna Kanban da riassunto call.")
    parser.add_argument("--task-directory", required=True, type=Path)

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--summary-path", type=Path, help="Aggiorna da un singolo riassunto.")
    mode.add_argument("--all", action="store_true", help="Scansiona tutte le call della task.")

    parser.add_argument("--include-archive", action="store_true",
                        help="Con --all, include anche le call archiviate.")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    if args.all:
        update_all(args.task_directory, args.model, args.include_archive)
    else:
        update_from_summary(args.summary_path, args.task_directory, args.model)


if __name__ == "__main__":
    main()
