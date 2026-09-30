from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False

from scripts.archive_old_calls import archive
from scripts.filesystem import atomic_write_text, safe_name, unique_path
from scripts.obsidian import frontmatter as fm
from scripts.obsidian import indexes
from scripts.update_project_kanban import update_from_summary


def _call_summary(path: Path, *, people: list[str] | None = None) -> None:
    fm.write_with_frontmatter(
        path,
        {"persone": people or [], "sistemi": ["Azure: AI"], "tags": ["call"]},
        "# riassunto\n\n## Decisioni\n\nTesto della call.\n",
    )


class ArtifactIntegrityTests(unittest.TestCase):
    def test_frontmatter_roundtrip_quotes_scalars_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "summary.md"
            fields = {
                "sistemi": ["Azure: AI", "Foo, Bar"],
                "persone": ["on", "123"],
                "note": "foo # bar",
                "archived": False,
            }
            fm.write_with_frontmatter(path, fields, "# riassunto\n")
            first = path.read_text(encoding="utf-8")
            fm.write_with_frontmatter(path, fields, "# riassunto\n")
            self.assertEqual(first, path.read_text(encoding="utf-8"))
            parsed = fm.read_fields(path)
            self.assertEqual(fields, parsed)
            fm.add_archived_fields(path)
            self.assertIs(True, fm.read_fields(path)["archived"])

    def test_filesystem_helpers_keep_suffix_and_reject_windows_names(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            source = root / "video.mp4"
            source.write_bytes(b"x")
            (root / "video (2).mp4").write_bytes(b"x")
            self.assertEqual("video (3).mp4", unique_path(source).name)
            self.assertEqual("_CON", safe_name("CON"))
            self.assertEqual("call-name", safe_name(" call:name. "))
            atomic_write_text(root / "nested" / "value.txt", "ok")
            self.assertEqual("ok", (root / "nested" / "value.txt").read_text())

    def test_archive_moves_before_video_cleanup_and_preserves_on_move_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            call_dir = root / "completate" / "Senza progetto" / "2026-08-01 10.00 - Call"
            call_dir.mkdir(parents=True)
            (call_dir / "Call.md").write_text("# call\n", encoding="utf-8")
            video = call_dir / "Call.mp4"
            video.write_bytes(b"video")
            with patch.object(Path, "rename", side_effect=OSError("rename failed")):
                result = archive(root, days=1)
            self.assertEqual(0, result["archived"])
            self.assertEqual(1, result["skipped"])
            self.assertTrue(video.exists())

    def test_rebuild_preserves_manual_sections_and_updates_full_renamed_link(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            task = root / "completate" / "Task" / "Alpha"
            call = task / "2026-08-01 10.00 - Discussione tecnica"
            call.mkdir(parents=True)
            summary = call / "Discussione tecnica.md"
            _call_summary(summary, people=["Mario Rossi"])
            kanban = task / "Kanban.md"
            kanban.write_text(
                "## Idee da call\n\n"
                "- [[2026-08-01 10.00 - Discussione tecnica/Discussione tecnica|Call]]\n",
                encoding="utf-8",
            )
            (task / "README.md").write_text(
                "# Alpha\n\nIntroduzione mantenuta.\n\n"
                "## Contesto del progetto\n\nContesto scritto a mano.\n\n"
                "## Call con fornitori\n\nSezione manuale.\n",
                encoding="utf-8",
            )

            indexes.rebuild(root)

            renamed = task / "2026-08-01 10.00 - Mario, Discussione tecnica"
            renamed_summary = renamed / "Mario, Discussione tecnica.md"
            self.assertTrue(renamed_summary.exists())
            self.assertIn(
                "[[2026-08-01 10.00 - Mario, Discussione tecnica/Mario, Discussione tecnica|Call]]",
                kanban.read_text(encoding="utf-8"),
            )
            readme = (task / "README.md").read_text(encoding="utf-8")
            self.assertIn("Introduzione mantenuta.", readme)
            self.assertIn("## Call con fornitori", readme)
            self.assertEqual(["Mario Rossi"], fm.read_fields(renamed_summary)["persone"])

            title_only = task / "2026-08-02 10.00 - Azure, Discussione"
            title_only.mkdir()
            title_only_summary = title_only / "Azure, Discussione.md"
            _call_summary(title_only_summary)
            indexes.rebuild(root)
            self.assertEqual([], fm.get_people(title_only_summary))

            before = {
                path: path.read_text(encoding="utf-8")
                for path in (renamed_summary, task / "README.md", kanban)
            }
            indexes.rebuild(root)
            self.assertEqual(before, {path: path.read_text(encoding="utf-8") for path in before})

    def test_marker_call_keeps_stable_directory_and_summary_names(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            task = root / "completate" / "Task" / "Alpha"
            call = task / "2026-08-01 10.00 - Discussione tecnica"
            call.mkdir(parents=True)
            (call / ".call-job.json").write_text("{}", encoding="utf-8")
            summary = call / "legacy-name.md"
            _call_summary(summary, people=["Mario Rossi"])
            indexes.rebuild(root)
            self.assertTrue(call.exists())
            self.assertTrue(summary.exists())
            self.assertFalse((call / "Mario, Discussione tecnica.md").exists())

    def test_archived_kanban_link_and_completed_card_are_deduplicated(self) -> None:
        class Provider:
            def is_available(self) -> bool:
                return True

            def invoke_light(self, prompt: str, model: str = "") -> str:
                self.prompt = prompt
                return "- [ ] Preparare report #alpha [[archivio/2026-08-01 10.00 - Call/Call|Call]]"

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            task = root / "completate" / "Task" / "Alpha"
            summary = task / "archivio" / "2026-08-01 10.00 - Call" / "Call.md"
            summary.parent.mkdir(parents=True)
            _call_summary(summary)
            kanban = task / "Kanban.md"
            kanban.write_text(
                "## Idee da call\n\n"
                "- [x] Preparare report #alpha [[Call precedente|Call]]\n\n"
                "## Fatto\n",
                encoding="utf-8",
            )
            provider = Provider()
            with patch("scripts.update_project_kanban.get_provider", return_value=provider):
                added = update_from_summary(summary, task)
            self.assertEqual(0, added)
            self.assertIn("[[archivio/2026-08-01 10.00 - Call/Call|Call]]", provider.prompt)
            self.assertEqual(1, kanban.read_text(encoding="utf-8").count("Preparare report"))


if __name__ == "__main__":
    unittest.main()
