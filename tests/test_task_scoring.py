from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.task_scoring import (
    TaskProfile,
    automatic_winner,
    classification_text,
    format_scores,
    load_profiles,
    normalize,
    parse_task_profile,
    phrase_matches,
    score_tasks,
)
from scripts.llm.common import build_task_prompt
from scripts.obsidian.frontmatter import strip_frontmatter


class TaskScoringTests(unittest.TestCase):
    def test_normalization_handles_accents_and_separators(self) -> None:
        self.assertEqual(normalize("Azure-AI_Search"), "azure ai search")
        self.assertTrue(phrase_matches("Ingestion dell'architettura RAG", "architettura-rag"))
        self.assertFalse(phrase_matches("RAG pipeline", "rag pipelined"))

    def test_frontmatter_is_removed_from_classification_and_prompt(self) -> None:
        summary = (
            "---\n"
            "task: \"[[Synergie - Chatbot AI]]\"\n"
            "data: 2026-08-26\n"
            "persone: [Antonio Baio, Gerardo]\n"
            "sistemi:\n"
            "  - Azure AI Search\n"
            "tags: [call, ingestion]\n"
            "---\n\n"
            "# riassunto\n## Ingestion RAG\n\n### Decisioni\nTesto della call."
        )
        body = strip_frontmatter(summary)
        self.assertTrue(body.startswith("# riassunto"))
        self.assertNotIn("task:", body)
        self.assertNotIn("persone:", body)
        self.assertNotIn("Azure AI Search", body)

        classification = classification_text("Titolo", summary, "Trascrizione")
        self.assertEqual(classification, "Titolo\n\n# riassunto\n## Ingestion RAG\n\n### Decisioni\nTesto della call.\n\nTrascrizione")

        prompt = build_task_prompt(["AI Club - ML Assistant"], summary=summary)
        self.assertIn("# riassunto", prompt)
        self.assertNotIn("task: \"[[Synergie - Chatbot AI]]\"", prompt)
        self.assertNotIn("persone: [Antonio Baio, Gerardo]", prompt)

    def test_phrase_matches_words_not_substrings(self) -> None:
        self.assertTrue(phrase_matches("Uso Azure AI Search oggi", "azure-ai-search"))
        self.assertFalse(phrase_matches("Uso azure ai searchable", "azure ai search"))

    def test_keyword_has_priority_and_margin_selects_winner(self) -> None:
        profiles = [
            TaskProfile("Alpha", Path("Alpha"), tags=("shared",), keywords=("alpha pipeline",)),
            TaskProfile("Beta", Path("Beta"), tags=("shared",), keywords=("beta pipeline",)),
        ]
        scores = score_tasks("Alpha pipeline shared", profiles)
        self.assertEqual(scores[0].score, 4)
        self.assertEqual(scores[1].score, 1)
        self.assertEqual(automatic_winner(scores), scores[0])

    def test_ambiguous_scores_fall_back_to_claude(self) -> None:
        profiles = [
            TaskProfile("Alpha", Path("Alpha"), keywords=("alpha pipeline",)),
            TaskProfile("Beta", Path("Beta"), keywords=("beta pipeline",)),
        ]
        scores = score_tasks("Alpha pipeline and beta pipeline", profiles)
        self.assertIsNone(automatic_winner(scores))
        self.assertIn("verifica Claude", format_scores(scores))

    def test_archived_task_needs_a_strong_keyword(self) -> None:
        archived = TaskProfile(
            "Legacy",
            Path("Task") / "progetti_archiviati" / "Legacy",
            tags=("azure",),
            keywords=(),
        )
        scores = score_tasks("Discussione Azure", [archived])
        self.assertIsNone(automatic_winner(scores))

        archived_with_keyword = TaskProfile(
            archived.name,
            archived.path,
            tags=archived.tags,
            keywords=("legacy migration",),
        )
        scores = score_tasks("Discussione legacy migration", [archived_with_keyword])
        self.assertEqual(automatic_winner(scores), scores[0])

    def test_missing_readme_or_keywords_cannot_auto_assign(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing = root / "Missing README"
            tag_only = root / "Tag only"
            tag_only.mkdir()
            (tag_only / "README.md").write_text(
                "# Tag only\n\n## Tag\n\n- azure\n", encoding="utf-8"
            )
            profiles = load_profiles([missing, tag_only])
            self.assertFalse(profiles[0].readme_exists)
            self.assertEqual(profiles[1].keywords, ())
            scores = score_tasks("Azure", profiles)
            self.assertIsNone(automatic_winner(scores))

    def test_profile_parses_tags_keywords_and_ignores_people(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir) / "AI Club"
            task_dir.mkdir()
            (task_dir / "README.md").write_text(
                "# AI Club\n\n"
                "## Persone coinvolte\n\n- Antonio Baio\n\n"
                "## Tag\n\n- machine-learning\n- mcp\n\n"
                "## Keyword di classificazione\n\n"
                "- ML Assistant\n- literature review\n",
                encoding="utf-8",
            )
            profile = parse_task_profile(task_dir)
            self.assertEqual(profile.tags, ("machine learning", "mcp"))
            self.assertEqual(profile.keywords, ("ml assistant", "literature review"))
            scores = score_tasks("Antonio Baio e letteratura generica", [profile])
            self.assertEqual(scores[0].score, 0)


if __name__ == "__main__":
    unittest.main()
