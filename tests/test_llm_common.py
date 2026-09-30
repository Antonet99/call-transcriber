from __future__ import annotations

import unittest

from scripts.llm.common import build_kanban_prompt, clean_markdown, validate_summary


def _summary(tags: str = "[call]") -> str:
    return (
        "---\n"
        "persone: [Marco]\n"
        "sistemi: [Databricks]\n"
        f"tags: {tags}\n"
        "---\n\n"
        "# riassunto\n"
        "## Rilascio\n\n"
        "### Stato\n"
        "Il rilascio resta in attesa di approvazione prima della pubblicazione.\n"
    )


class SummaryValidationTests(unittest.TestCase):
    def test_narrative_approval_phrase_is_allowed_inside_summary(self) -> None:
        validate_summary(_summary())

    def test_operational_request_outside_summary_is_rejected(self) -> None:
        raw = "Sono in attesa di approvazione per scrivere il file.\n\n" + _summary()
        with self.assertRaisesRegex(ValueError, "richiesta operativa"):
            validate_summary(clean_markdown(raw), raw_text=raw)

    def test_outer_markdown_fence_is_removed_but_inner_fence_is_preserved(self) -> None:
        source = "```markdown\n" + _summary() + "\n```python\nprint('ok')\n```\n```"
        clean = clean_markdown(source)
        self.assertIn("```python", clean)
        self.assertNotIn("```markdown", clean)

    def test_metadata_types_and_required_tag_are_checked(self) -> None:
        with self.assertRaisesRegex(ValueError, "persone"):
            validate_summary(_summary(tags="[call]").replace("persone: [Marco]", "persone: Marco"))
        with self.assertRaisesRegex(ValueError, "tag 'call'"):
            validate_summary(_summary(tags="[riassunto]"))

    def test_kanban_prompt_keeps_action_items_at_end_of_summary(self) -> None:
        summary = "x" * 7000 + "\n### Action item\nIntegrare il connettore SAP."
        prompt = build_kanban_prompt(summary, "## Idee da call", "Task/Call", "Call")
        self.assertIn("Integrare il connettore SAP.", prompt)


if __name__ == "__main__":
    unittest.main()
