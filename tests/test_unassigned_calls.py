from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta
import tempfile
import unittest

from scripts.archive_old_calls import archive
from scripts.llm import common as llm_common
from scripts.obsidian import indexes
from scripts.process_call import _discover_task_dirs, _get_task_dir, _recognize_task


class _Provider:
    def __init__(self, answer: str = "", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error

    def default_task_model(self) -> str:
        return "test-model"

    def invoke_task_classification(self, prompt: str, model: str) -> str:
        if self.error:
            raise self.error
        return self.answer


def _make_task(root: Path, name: str = "Alpha") -> Path:
    task_dir = root / "completate" / "Task" / name
    task_dir.mkdir(parents=True)
    (task_dir / "README.md").write_text(
        f"# {name}\n\n## Contesto del progetto\n\nProgetto di test.\n\n"
        "## Tag\n\n- alpha\n\n"
        "## Keyword di classificazione\n\n- alpha pipeline\n\n"
        "<!-- TASK_CALLS:START -->\n## Call recenti (0)\n\nNessuna call recente.\n"
        "\n## Call archiviate (0)\n\nNessuna call archiviata.\n<!-- TASK_CALLS:END -->\n",
        encoding="utf-8",
    )
    return task_dir


class UnassignedCallTests(unittest.TestCase):
    def test_no_task_token_is_parsed_and_prompt_exposes_it(self) -> None:
        task_dirs = [Path("Alpha")]

        self.assertTrue(llm_common.is_no_task_answer("NESSUNA_TASK"))
        self.assertTrue(llm_common.is_no_task_answer("```text\nNESSUNA_TASK\n```"))
        self.assertIsNone(llm_common.select_task(task_dirs, "NESSUNA_TASK"))

        prompt = llm_common.build_task_prompt(["Alpha"])
        self.assertIn(llm_common.NO_TASK_TOKEN, prompt)
        self.assertIn("senza un progetto riconoscibile", prompt)
        self.assertIn("\n- NESSUNA_TASK", prompt)
        self.assertNotIn("NESSUNA_TASK (", prompt)

    def test_task_selection_requires_one_exact_value(self) -> None:
        task_dirs = [Path("Alpha"), Path("Beta")]

        self.assertEqual(llm_common.select_task(task_dirs, "Alpha"), task_dirs[0])
        for answer in ("Alpha oppure Beta", "Motivo: Alpha", "NESSUNA_TASK\nAlpha"):
            self.assertIsNone(llm_common.select_task(task_dirs, answer))

    def test_preliminary_no_task_token_does_not_discover_unassigned_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _make_task(root)
            unassigned = root / "completate" / "Senza progetto"
            unassigned.mkdir()

            result = _recognize_task(root, "discussione tecnica generica", _Provider("NESSUNA_TASK"), "")

            self.assertIsNone(result)
            self.assertEqual(_discover_task_dirs(root), [root / "completate" / "Task" / "Alpha"])

    def test_preliminary_logs_continue_to_final_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _make_task(root)

            with self.assertLogs("scripts.process_call", level="WARNING") as captured:
                _recognize_task(root, "discussione tecnica generica", _Provider("Motivo: Alpha"), "")
                _recognize_task(
                    root,
                    "discussione tecnica generica",
                    _Provider(error=RuntimeError("provider indisponibile")),
                    "",
                )

            messages = "\n".join(captured.output)
            self.assertIn("output task non riconoscibile", messages)
            self.assertIn("task fallito", messages)
            self.assertIn("nessuna assegnazione preliminare", messages)
            self.assertIn("si procede alla classificazione finale", messages)
            self.assertNotIn("fallback sicuro in Senza progetto", messages)

    def test_classification_events_are_persisted_to_standard_logger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _make_task(root)
            summary = root / "summary.md"
            summary.write_text(
                "# riassunto\n## Discussione tecnica\n### Note\nGenerica.",
                encoding="utf-8",
            )

            with self.assertLogs("scripts.process_call", level="INFO") as captured:
                _recognize_task(root, "discussione tecnica generica", _Provider("NESSUNA_TASK"), "")
                _get_task_dir(root, summary, "Discussione tecnica", _Provider("Motivo: Alpha"), "")
                _get_task_dir(
                    root,
                    summary,
                    "Discussione tecnica",
                    _Provider(error=RuntimeError("provider indisponibile")),
                    "",
                )

            messages = "\n".join(captured.output)
            self.assertIn("NESSUNA_TASK", messages)
            self.assertIn("Classificazione finale: output non riconoscibile", messages)
            self.assertIn("Classificazione finale task fallita", messages)
            self.assertIn("fallback sicuro in Senza progetto", messages)

    def test_discovery_excludes_the_complete_reserved_subtree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_dir = _make_task(root)
            for reserved in (
                root / "completate" / "Task" / "Senza progetto" / "Nested",
                task_dir / "Senza progetto" / "Nested",
            ):
                reserved.mkdir(parents=True)
                (reserved / "README.md").write_text("# Non task\n", encoding="utf-8")

            self.assertEqual(_discover_task_dirs(root), [task_dir])

    def test_final_semantic_and_technical_failures_use_same_safe_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _make_task(root)
            summary = root / "summary.md"
            summary.write_text("# riassunto\n## Discussione tecnica\n### Note\nGenerica.", encoding="utf-8")

            semantic = _get_task_dir(
                root, summary, "Discussione tecnica", _Provider("NESSUNA_TASK"), ""
            )
            technical = _get_task_dir(
                root,
                summary,
                "Discussione tecnica",
                _Provider(error=RuntimeError("provider indisponibile")),
                "",
            )
            invalid = _get_task_dir(
                root, summary, "Discussione tecnica", _Provider("risposta non valida"), ""
            )

            expected = root / "completate" / "Senza progetto"
            self.assertEqual(semantic, expected)
            self.assertEqual(technical, expected)
            self.assertEqual(invalid, expected)
            self.assertTrue(expected.is_dir())
            self.assertFalse((expected / "README.md").exists())

    def test_rebuild_indexes_unassigned_calls_and_removes_stale_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_dir = _make_task(root)
            task_readme_before = (task_dir / "README.md").read_text(encoding="utf-8")

            unassigned = root / "completate" / "Senza progetto"
            call_dir = unassigned / "2026-08-26 10.00 - Discussione tecnica"
            call_dir.mkdir(parents=True)
            summary = call_dir / "riassunto.md"
            summary.write_text(
                "---\n"
                "task: \"[[Alpha]]\"\n"
                "persone: [Antonio Baio, Gerardo]\n"
                "tags: [call, tecnica]\n"
                "---\n\n"
                "# riassunto\n## Discussione tecnica\n\n### Note\nConfronto tra colleghi.",
                encoding="utf-8",
            )

            result = indexes.rebuild(root)
            global_index = (root / "completate" / "README.md").read_text(encoding="utf-8")
            normalized_call_dir = next(unassigned.iterdir())
            summary_path = next(
                path for path in normalized_call_dir.glob("*.md") if path.name != "README.md"
            )
            fields = indexes.fm.read_fields(summary_path)

            self.assertEqual(result["unassigned_calls"], 1)
            self.assertIn("## Call senza progetto (1)", global_index)
            self.assertIn(
                "[[Senza progetto/2026-08-26 10.00 - Gerardo, Discussione tecnica/"
                "Gerardo, Discussione tecnica|2026-08-26 - Gerardo, Discussione tecnica]]",
                global_index,
            )
            self.assertNotIn("task", fields)
            self.assertFalse((unassigned / "README.md").exists())
            self.assertIn("# Alpha", (task_dir / "README.md").read_text(encoding="utf-8"))
            self.assertIn("## Contesto del progetto", (task_dir / "README.md").read_text(encoding="utf-8"))
            self.assertEqual(
                task_readme_before.split("<!-- TASK_CALLS:START -->")[0],
                (task_dir / "README.md").read_text(encoding="utf-8").split("<!-- TASK_CALLS:START -->")[0],
            )

    def test_unassigned_archive_roundtrip_keeps_recent_and_archived_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _make_task(root)
            unassigned = root / "completate" / "Senza progetto"
            old_name = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
            recent_name = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
            for name, title in (
                (f"{old_name} 10.00 - Vecchia discussione", "Vecchia discussione"),
                (f"{recent_name} 11.00 - Discussione corrente", "Discussione corrente"),
            ):
                call_dir = unassigned / name
                call_dir.mkdir(parents=True)
                (call_dir / f"{title}.md").write_text(
                    f"---\ntask: \"[[Alpha]]\"\ntags: [call]\n---\n\n"
                    f"# riassunto\n## {title}\n\n### Note\nCall.",
                    encoding="utf-8",
                )

            archived_result = archive(root, days=10)
            result = indexes.rebuild(root)
            global_index = (root / "completate" / "README.md").read_text(encoding="utf-8")

            self.assertEqual(archived_result["archived"], 1)
            self.assertEqual(result["calls"], 2)
            self.assertEqual(result["recent_calls"], 1)
            self.assertEqual(result["archived_calls"], 1)
            self.assertEqual(result["unassigned_calls"], 2)
            self.assertTrue((unassigned / "archivio" / f"{old_name} 10.00 - Vecchia discussione").exists())
            self.assertIn(
                f"[[Senza progetto/archivio/{old_name} 10.00 - Vecchia discussione/"
                "Vecchia discussione|",
                global_index,
            )
            self.assertIn(
                f"[[Senza progetto/{recent_name} 11.00 - Discussione corrente/"
                "Discussione corrente|",
                global_index,
            )


if __name__ == "__main__":
    unittest.main()
