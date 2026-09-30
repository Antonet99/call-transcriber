"""Funzioni condivise tra provider LLM: prompt, pulizia Markdown, validazione."""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

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
    # Le action item possono essere in coda al riassunto: non tagliare il
    # contesto prima di estrarle.
    trimmed = summary
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

_OPERATIONAL_OUTPUT_RE = re.compile(
    r"(?i)\bin attesa di approvazione\b|\bapprovazione per scrivere\b|\bscrivere il file\b"
)
_SUMMARY_TITLE_RE = re.compile(r"(?m)^#\s+riassunto\s*$")
_SUMMARY_CONTEXT_RE = re.compile(r"(?m)^##\s+\S")
_SUMMARY_DETAIL_RE = re.compile(r"(?m)^###\s+\S")
_TAG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _unwrap_markdown_wrapper(text: str) -> str:
    """Rimuove un solo fence esterno, lasciando intatti i fence interni."""
    lines = text.strip().splitlines()
    if len(lines) < 3:
        return text.strip()
    if not re.fullmatch(r"```(?:md|markdown)?\s*", lines[0].strip(), re.IGNORECASE):
        return text.strip()
    if lines[-1].strip() != "```":
        return text.strip()
    return "\n".join(lines[1:-1]).strip()


def clean_markdown(text: str) -> str:
    clean = _unwrap_markdown_wrapper(text)
    lines: list[str] = []
    in_fence = False
    for line in clean.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            lines.append(line)
            continue
        if not in_fence and line.strip() == "Leggo la trascrizione e produco il riassunto.":
            continue
        lines.append(line)
    clean = "\n".join(lines).strip()

    fm_match = re.search(r'(?ms)^---\s*$.*?^---\s*$\s*^#\s+riassunto\s*$', clean)
    if fm_match:
        clean = clean[fm_match.start():].strip()
    else:
        h_match = re.search(r'(?m)^#\s+riassunto\s*$', clean)
        if h_match and h_match.start() > 0:
            clean = clean[h_match.start():].strip()

    return clean


def _text_outside_clean_summary(text: str, clean: str) -> str:
    if not clean:
        return text

    start = text.find(clean)
    if start < 0:
        unwrapped = _unwrap_markdown_wrapper(text)
        start = unwrapped.find(clean)
        if start >= 0:
            text = unwrapped
    if start < 0:
        # If the wrapper was cleaned together with a preamble, the heading is
        # still a reliable boundary for detecting operational text before it.
        heading = text.find("# riassunto")
        return text[:heading] if heading >= 0 else text

    end = start + len(clean)
    return f"{text[:start]}\n{text[end:]}"


def _frontmatter_and_body(clean: str) -> tuple[dict[str, Any], str]:
    lines = clean.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("Il riassunto deve iniziare con un frontmatter YAML.")

    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration as exc:
        raise ValueError("Il frontmatter YAML non e' chiuso correttamente.") from exc

    yaml_text = "\n".join(lines[1:end]).strip()
    try:
        fields = yaml.safe_load(yaml_text) if yaml_text else {}
    except yaml.YAMLError as exc:
        raise ValueError("Il frontmatter YAML non e' valido.") from exc
    if not isinstance(fields, dict):
        raise ValueError("Il frontmatter YAML deve contenere una mappa di metadati.")
    return dict(fields), "\n".join(lines[end + 1 :]).strip()


def _validate_metadata(fields: dict[str, Any]) -> None:
    for key in ("persone", "sistemi", "tags"):
        if key not in fields:
            continue
        value = fields[key]
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"Il campo frontmatter '{key}' deve essere una lista di stringhe non vuote.")

    tags = fields.get("tags")
    if not isinstance(tags, list) or "call" not in {item.casefold() for item in tags if isinstance(item, str)}:
        raise ValueError("Il frontmatter deve contenere il tag 'call'.")
    if any(not _TAG_RE.fullmatch(item) for item in tags):
        raise ValueError("I tag del frontmatter devono essere in kebab-case minuscolo.")

    for key in ("data", "ora", "task", "archived_at"):
        if key in fields and fields[key] is not None and not isinstance(fields[key], (str, date, datetime)):
            raise ValueError(f"Il campo frontmatter '{key}' deve essere scalare.")
    if "archived" in fields and not isinstance(fields["archived"], bool):
        raise ValueError("Il campo frontmatter 'archived' deve essere booleano.")


def validate_summary(text: str, *, raw_text: str | None = None) -> None:
    clean = clean_markdown(text)
    source = raw_text if raw_text is not None else text
    external_text = _text_outside_clean_summary(source, clean)
    if _OPERATIONAL_OUTPUT_RE.search(external_text):
        raise ValueError("Il provider LLM ha restituito una richiesta operativa invece del riassunto.")

    fields, body = _frontmatter_and_body(clean)
    _validate_metadata(fields)

    title_match = _SUMMARY_TITLE_RE.search(body)
    if not title_match:
        raise ValueError("Il riassunto non contiene il titolo principale richiesto.")
    if len(_SUMMARY_TITLE_RE.findall(body)) != 1 or not body.lstrip().startswith("# riassunto"):
        raise ValueError("La struttura del riassunto deve iniziare con un unico titolo principale.")
    context_match = _SUMMARY_CONTEXT_RE.search(body, title_match.end())
    if not context_match:
        raise ValueError("Il riassunto non contiene il sottotitolo contestuale richiesto.")
    if not _SUMMARY_DETAIL_RE.search(body, context_match.end()):
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
