"""Aggiorna la Kanban del progetto con card estratte dall'ultimo riassunto call."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import scripts.settings as _cfg
from scripts.llm import common as llm_common
from scripts.obsidian import kanban

_CARD_RE = re.compile(r'^\-\s+\[\s*\]')
_UTF8 = "utf-8"


def _load_provider(provider_name: str):
    from scripts.settings import ENABLED_PROVIDERS
    if provider_name not in ENABLED_PROVIDERS:
        raise RuntimeError(
            f"Provider '{provider_name}' disabilitato. Abilitarlo in scripts/settings.py."
        )
    if provider_name == "gemini":
        from scripts.llm.providers.gemini import GeminiProvider
        return GeminiProvider()
    if provider_name == "claude":
        from scripts.llm.providers.claude import ClaudeProvider
        return ClaudeProvider()
    raise ValueError(f"Provider non supportato: {provider_name}")


def update_from_summary(
    summary_path: Path,
    task_dir: Path,
    provider_name: str | None = None,
    model: str = "",
) -> int:
    if provider_name is None:
        from scripts.settings import ENABLED_PROVIDERS
        provider_name = ENABLED_PROVIDERS[0] if ENABLED_PROVIDERS else "gemini"
    provider = _load_provider(provider_name)
    if not provider.is_available():
        print(f"[kanban] Provider {provider_name} non disponibile, skip.")
        return 0

    kanban_path = task_dir / "Kanban.md"
    if not kanban_path.exists():
        kanban.create(kanban_path, task_dir.name)
        print(f"[kanban] Creata: {kanban_path}")

    summary = summary_path.read_text(encoding=_UTF8)
    kanban_content = kanban_path.read_text(encoding=_UTF8)
    existing_cards = kanban.get_all_cards(kanban_path)

    # Wikilink relativo alla task dir
    summary_dir_name = summary_path.parent.name
    summary_base = summary_path.stem
    call_wiki = f"{summary_dir_name}/{summary_base}"
    call_label = re.sub(r'^\d{4}-\d{2}-\d{2}\s+\d{2}\.\d{2}\s+-\s+', '', summary_base)
    if not call_label:
        call_label = summary_base

    prompt = llm_common.build_kanban_prompt(summary, kanban_content, call_wiki, call_label)
    answer = provider.invoke_light(prompt, model).strip()

    if not answer or answer.upper() == "NONE":
        print("[kanban] Nessuna card nuova da questa call.")
        return 0

    new_cards = [
        ln.strip() for ln in answer.splitlines()
        if _CARD_RE.match(ln.strip())
    ][:_cfg.KANBAN_MAX_CARDS_PER_CALL]

    if not new_cards:
        print("[kanban] Risposta LLM non contiene card nel formato atteso.")
        return 0

    # Deduplica rozza per parole chiave
    filtered = []
    for card in new_cards:
        card_lower = card.lower()
        if not any(card_lower[:_cfg.KANBAN_DEDUP_LENGTH] in e.lower() for e in existing_cards):
            filtered.append(card)

    if not filtered:
        print("[kanban] Tutte le card erano già presenti.")
        return 0

    added = kanban.update(kanban_path, filtered)
    print(f"[kanban] {added} card aggiunte in {task_dir.name}.")
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggiorna Kanban da riassunto call.")
    parser.add_argument("--summary-path", required=True, type=Path)
    parser.add_argument("--task-directory", required=True, type=Path)
    parser.add_argument("--provider", default="gemini", choices=["gemini", "claude"])
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    update_from_summary(args.summary_path, args.task_directory, args.provider, args.model)


if __name__ == "__main__":
    main()
