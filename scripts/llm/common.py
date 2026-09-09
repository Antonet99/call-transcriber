"""Funzioni condivise tra provider LLM: prompt, pulizia Markdown, validazione."""
from __future__ import annotations

import re
from pathlib import Path

import scripts.settings as _cfg
from scripts.obsidian import frontmatter as _fm

NO_TASK_TOKEN = "NESSUNA_TASK"


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def _read_context(path: Path | None, limit: int = 12000) -> str:
    if path is None or not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()[:limit]


def build_summary_prompt(
    prompt_path: Path,
    transcript: str,
    project_instructions_path: Path | None = None,
    global_index_path: Path | None = None,
    task_readme_path: Path | None = None,
) -> str:
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    sections = [prompt]

    if project_instructions_path is not None and not project_instructions_path.is_file():
        raise FileNotFoundError(
            f"Istruzioni operative del progetto non trovate: {project_instructions_path}"
        )
    project_instructions = _read_context(project_instructions_path)
    if project_instructions:
        sections += [
            "Istruzioni di progetto da leggere e applicare:",
            project_instructions,
        ]

    global_index = _read_context(global_index_path)
    if global_index:
        sections += [
            "Indice globale delle task e delle call:",
            global_index,
        ]

    task_readme = _read_context(task_readme_path)
    if task_readme:
        sections += [
            "README del progetto assegnato:",
            task_readme,
        ]

    sections += ["Trascrizione da riassumere:", transcript]
    return "\n\n---\n\n".join(sections)


def build_task_prompt(
    task_names: list[str],
    title: str = "",
    summary: str = "",
    *,
    transcript: str = "",
    global_index: str = "",
    task_contexts: dict[str, str] | None = None,
    preliminary_task: str = "",
    scoring_evidence: str = "",
) -> str:
    task_list = "\n".join([*(f"- {t}" for t in task_names), f"- {NO_TASK_TOKEN}"])
    trimmed_summary = _fm.strip_frontmatter(summary)[:_cfg.TASK_PROMPT_SUMMARY_TRUNCATE]
    trimmed_transcript = transcript[:_cfg.TASK_PROMPT_TRANSCRIPT_TRUNCATE]
    sections = [
        "Devi assegnare una call a una delle task elencate, includendo anche le task archiviate, "
        f"oppure usare esattamente {NO_TASK_TOKEN} se la call non è collegata a nessun progetto.",
        "Usa il contenuto della trascrizione o del riassunto e l'indice globale.",
        f"Rispondi solo con il nome esatto di una task tra quelle elencate oppure {NO_TASK_TOKEN}. "
        "Usa quest'ultimo per discussioni tecniche generiche, confronti tra colleghi "
        "e call senza un progetto riconoscibile. Non aggiungere spiegazioni, virgolette, "
        "markdown o testo extra.",
        f"Task disponibili:\n{task_list}",
    ]
    if preliminary_task:
        sections.append(
            f"Assegnazione preliminare da verificare, non vincolante:\n{preliminary_task}"
        )
    if scoring_evidence:
        sections.append(
            "Evidenze dello scoring deterministico (supporto, non verità assoluta):\n"
            f"{scoring_evidence}"
        )
    if global_index:
        sections.append(f"Indice globale delle task:\n{global_index}")
    if task_contexts:
        sections.insert(2, "Usa anche il README di ciascuna task quando disponibile.")
        contexts = []
        for name in task_names:
            context = task_contexts.get(name, "").strip()
            if context:
                contexts.append(f"README task {name}:\n{context[:6000]}")
        if contexts:
            sections.append("\n\n".join(contexts))
    if title:
        sections.append(f"Titolo call:\n{title}")
    if trimmed_summary:
        sections.append(f"Riassunto call:\n{trimmed_summary}")
    if trimmed_transcript:
        sections.append(f"Trascrizione call:\n{trimmed_transcript}")
    return "\n\n---\n\n".join(sections)


def build_kanban_prompt(summary: str, kanban_content: str, call_wikilink: str, call_label: str) -> str:
    trimmed = summary[:_cfg.KANBAN_PROMPT_SUMMARY_TRUNCATE]
    return (
        "Leggi il riassunto di questa call e la Kanban di progetto.\n\n"
        "Estrai le MACRO-ATTIVITA' da fare che emergono dalla call. "
        "Una macro-attivita' e' un obiettivo autonomo e consegnabile (es. 'Integrare tabella in PowerBI'), "
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

def _clean_task_answer(answer: str) -> str:
    clean = (answer or "").strip()
    clean = re.sub(r"(?is)^```(?:text|markdown)?\s*", "", clean)
    clean = re.sub(r"(?is)\s*```$", "", clean).strip()
    return clean.strip('"`\'').strip()


def is_no_task_answer(answer: str) -> bool:
    """Riconosce la scelta semantica esplicita del contenitore senza progetto."""
    return _clean_task_answer(answer).casefold() == NO_TASK_TOKEN.casefold()


def select_task(task_dirs: list[Path], answer: str) -> Path | None:
    clean = _clean_task_answer(answer)

    if not clean or is_no_task_answer(clean):
        return None

    for d in task_dirs:
        if d.name.lower() == clean.lower():
            return d
    return None
