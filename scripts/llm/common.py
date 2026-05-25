"""Funzioni condivise tra provider LLM: prompt, pulizia Markdown, validazione."""
from __future__ import annotations

import re
from pathlib import Path

import scripts.settings as _cfg


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def build_summary_prompt(prompt_path: Path, transcript: str) -> str:
    prompt = prompt_path.read_text(encoding="utf-8")
    return f"{prompt}\n\n---\n\nTrascrizione da riassumere:\n\n{transcript}"


def build_task_prompt(task_names: list[str], title: str, summary: str) -> str:
    task_list = "\n".join(f"- {t}" for t in task_names)
    trimmed = summary[:_cfg.TASK_PROMPT_SUMMARY_TRUNCATE]
    return (
        "Devi classificare una call già riassunta dentro una delle cartelle task esistenti.\n\n"
        "Rispondi solo con il nome esatto di una cartella tra quelle elencate. "
        "Non aggiungere spiegazioni, virgolette, markdown o testo extra.\n\n"
        f"Cartelle task disponibili:\n{task_list}\n\n"
        f"Titolo call:\n{title}\n\n"
        f"Riassunto call:\n{trimmed}"
    )


def build_kanban_prompt(summary: str, kanban_content: str, call_wikilink: str, call_label: str) -> str:
    trimmed = summary[:_cfg.KANBAN_PROMPT_SUMMARY_TRUNCATE]
    return (
        "Leggi il riassunto di questa call e la Kanban di progetto.\n\n"
        "Estrai le MACRO-ATTIVITA' da fare che emergono dalla call. "
        "Una macro-attivita' e' un obiettivo autonomo e consegnabile (es. 'Integrare tabella Z_AUTH in PowerBI'), "
        "NON un sotto-passo tecnico (es. 'Verificare la colonna X', 'Aprire la connessione Y').\n\n"
        "Regole TASSATIVE:\n"
        "- Se piu' sotto-passi portano allo stesso obiettivo, scrivi UNA sola card per quell'obiettivo.\n"
        "- Non duplicare card gia' presenti nella Kanban.\n"
        "- Usa solo contenuti presenti nel riassunto. Non inventare nulla.\n"
        "- Se la call non aggiunge macro-attivita' nuove rispetto alla Kanban, rispondi esattamente: NONE\n\n"
        "Formato risposta (una riga per card, niente altro):\n"
        f"- [ ] Macro-attivita' #tag [[{call_wikilink}|{call_label}]]\n\n"
        f"Massimo {_cfg.KANBAN_MAX_CARDS_PER_CALL} card. Preferisci 1-2 card precise a 4-5 generiche.\n\n"
        "---\n\n"
        f"Riassunto call:\n{trimmed}\n\n"
        "---\n\n"
        f"Kanban attuale:\n{kanban_content}"
    )


# ---------------------------------------------------------------------------
# Post-processing output LLM
# ---------------------------------------------------------------------------

def clean_markdown(text: str) -> str:
    clean = text.strip()

    m = re.search(r'(?ms)```(?:md|markdown)?\s*(.*?)```', clean)
    if m:
        clean = m.group(1).strip()

    lines = [
        ln for ln in clean.splitlines()
        if not ln.strip().startswith("```")
        and ln.strip() != "Leggo la trascrizione e produco il riassunto."
    ]
    clean = "\n".join(lines).strip()

    fm_match = re.search(r'(?ms)^---\s*$.*?^---\s*$\s*^#\s+riassunto\s*$', clean)
    if fm_match and fm_match.start() > 0:
        clean = clean[fm_match.start():].strip()
    else:
        h_match = re.search(r'(?m)^#\s+riassunto\s*$', clean)
        if h_match and h_match.start() > 0:
            clean = clean[h_match.start():].strip()

    return clean


def validate_summary(text: str) -> None:
    if re.search(r'(?i)in attesa di approvazione|approvazione per scrivere|scrivere il file', text):
        raise ValueError("Il provider LLM ha restituito una richiesta operativa invece del riassunto.")

    lines = text.splitlines()
    first_nonempty = next((i for i, l in enumerate(lines) if l.strip()), None)
    if first_nonempty is not None and lines[first_nonempty].strip() == "---":
        closed = any(lines[j].strip() == "---" for j in range(first_nonempty + 1, len(lines)))
        if not closed:
            raise ValueError("Il frontmatter YAML non e' chiuso correttamente.")

    if not re.search(r'(?m)^#\s+riassunto\s*$', text):
        raise ValueError("Il riassunto non contiene il titolo principale richiesto.")
    if not re.search(r'(?m)^##\s+\S', text):
        raise ValueError("Il riassunto non contiene il sottotitolo contestuale richiesto.")
    if not re.search(r'(?m)^###\s+\S', text):
        raise ValueError("Il riassunto non contiene sezioni di dettaglio.")


# ---------------------------------------------------------------------------
# Task selection
# ---------------------------------------------------------------------------

def select_task(task_dirs: list[Path], answer: str) -> Path | None:
    clean = answer.strip().strip('"`\'')
    clean = re.sub(r'(?i)^```(?:text|markdown)?\s*', '', clean)
    clean = re.sub(r'(?i)\s*```$', '', clean).strip()

    for d in task_dirs:
        if d.name.lower() == clean.lower():
            return d
    for d in task_dirs:
        if clean.lower() in d.name.lower() or d.name.lower() in clean.lower():
            return d
    return None
