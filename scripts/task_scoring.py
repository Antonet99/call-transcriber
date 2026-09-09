"""Scoring deterministico, spiegabile e conservativo per l'assegnazione delle call."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

import scripts.settings as _cfg
from scripts.obsidian import frontmatter as _fm


_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")
_LEGACY_TAGS_RE = re.compile(r"^\s*-\s*Tag\s*:\s*(.+?)\s*$", re.IGNORECASE)
_ARCHIVED_PARTS = {
    "archivio",
    "archiviati",
    "archiviate",
    "progetti_archiviati",
}


def normalize(value: str) -> str:
    """Normalizza testo e separatori per confronti indipendenti dalla grafia."""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    ascii_value = ascii_value.casefold().replace("_", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_value)).strip()


def phrase_matches(text: str, phrase: str) -> bool:
    """Restituisce True solo per una corrispondenza di parole intere consecutive."""
    normalized_text = normalize(text)
    normalized_phrase = normalize(phrase)
    if not normalized_text or not normalized_phrase:
        return False
    padded_text = f" {normalized_text} "
    return f" {normalized_phrase} " in padded_text


def is_archived_task(task_dir: Path) -> bool:
    """Riconosce task sotto una cartella archivio senza usare nomi di persone."""
    return any(part.casefold() in _ARCHIVED_PARTS for part in task_dir.parts)


def _unique_terms(values: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize(value.lstrip("- ").strip())
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return tuple(result)


def _section_values(lines: list[str], section_names: set[str]) -> list[str]:
    values: list[str] = []
    active = False
    for line in lines:
        heading = _SECTION_RE.match(line)
        if heading:
            active = normalize(heading.group(1)) in section_names
            continue
        if not active:
            continue
        match = _BULLET_RE.match(line)
        if match:
            values.append(match.group(1).strip())
    return values


@dataclass(frozen=True)
class TaskProfile:
    """Profilo classificabile di una task ricavato dal suo README."""

    name: str
    path: Path
    tags: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    readme_exists: bool = False

    @property
    def archived(self) -> bool:
        return is_archived_task(self.path)


def parse_task_profile(task_dir: Path) -> TaskProfile:
    readme_path = task_dir / "README.md"
    if not readme_path.is_file():
        return TaskProfile(task_dir.name, task_dir, readme_exists=False)

    content = readme_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    tags = _section_values(lines, {"tag", "tags"})
    if not tags:
        for line in lines:
            match = _LEGACY_TAGS_RE.match(line)
            if match:
                tags = [item.strip() for item in match.group(1).split(",")]
                break
    keywords = _section_values(
        lines,
        {"keyword di classificazione", "keywords di classificazione"},
    )
    return TaskProfile(
        name=task_dir.name,
        path=task_dir,
        tags=_unique_terms(tags),
        keywords=_unique_terms(keywords),
        readme_exists=True,
    )


def load_profiles(task_dirs: list[Path]) -> list[TaskProfile]:
    """Carica tutti i profili forniti, incluse task attive e archiviate."""
    return [parse_task_profile(task_dir) for task_dir in task_dirs]


@dataclass(frozen=True)
class TaskScore:
    profile: TaskProfile
    score: int
    keyword_matches: tuple[str, ...] = ()
    tag_matches: tuple[str, ...] = ()

    @property
    def strong_keyword_matched(self) -> bool:
        return bool(self.keyword_matches)

    @property
    def evidence(self) -> str:
        parts: list[str] = []
        if self.keyword_matches:
            parts.append("keyword=" + ", ".join(self.keyword_matches))
        if self.tag_matches:
            parts.append("tag=" + ", ".join(self.tag_matches))
        return "; ".join(parts) if parts else "nessuna evidenza"


def score_tasks(text: str, profiles: list[TaskProfile]) -> list[TaskScore]:
    """Calcola score per ogni task, ordinando per score e poi per nome."""
    scores: list[TaskScore] = []
    for profile in profiles:
        keyword_matches = tuple(
            keyword for keyword in profile.keywords if phrase_matches(text, keyword)
        )
        tag_matches = tuple(tag for tag in profile.tags if phrase_matches(text, tag))
        score = (
            len(keyword_matches) * _cfg.TASK_KEYWORD_SCORE
            + len(tag_matches) * _cfg.TASK_TAG_SCORE
        )
        scores.append(
            TaskScore(
                profile=profile,
                score=score,
                keyword_matches=keyword_matches,
                tag_matches=tag_matches,
            )
        )
    return sorted(scores, key=lambda item: (-item.score, item.profile.name.casefold()))


def automatic_winner(scores: list[TaskScore]) -> TaskScore | None:
    """Seleziona solo un vincitore con keyword forte e margine sufficiente."""
    if not scores:
        return None
    top = scores[0]
    second_score = scores[1].score if len(scores) > 1 else 0
    margin = top.score - second_score
    if (
        top.score < _cfg.TASK_SCORE_MIN_TOTAL
        or not top.strong_keyword_matched
        or margin < _cfg.TASK_SCORE_MIN_MARGIN
    ):
        return None
    return top


def format_scores(scores: list[TaskScore]) -> str:
    """Formatta score ed evidenze per log e prompt di Claude."""
    if not scores:
        return "Scoring deterministico: nessuna task disponibile."
    lines = ["Scoring deterministico task (keyword forte > tag):"]
    for item in scores:
        state = "archiviata" if item.profile.archived else "attiva"
        lines.append(
            f"- {item.profile.name} [{state}]: {item.score} punti ({item.evidence})"
        )
    top = scores[0]
    second_score = scores[1].score if len(scores) > 1 else 0
    margin = top.score - second_score
    winner = automatic_winner(scores)
    if winner:
        lines.append(
            f"Decisione deterministica: {winner.profile.name} "
            f"(margine {margin}, keyword forte presente)."
        )
    else:
        lines.append(
            f"Decisione deterministica: ambigua/non sufficiente "
            f"(margine {margin}, richiesta verifica Claude)."
        )
    return "\n".join(lines)


def classification_text(title: str = "", summary: str = "", transcript: str = "") -> str:
    """Compone il testo da classificare, escludendo un eventuale frontmatter stale."""
    summary_body = _fm.strip_frontmatter(summary)
    return "\n\n".join(part for part in (title, summary_body, transcript) if part).strip()
