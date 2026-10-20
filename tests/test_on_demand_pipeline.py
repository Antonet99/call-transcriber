from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import jobs, process_call as pc, recordings
from scripts.obsidian import frontmatter as fm


class OnDemandPipelineTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("scripts.config_verification.require_ready"))
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.temp) / "vault"
        self.task = self.root / "completate" / "Task" / "Alpha"
        self.incoming = self.root / "da_processare"
        self.task.mkdir(parents=True)
        self.incoming.mkdir(parents=True)
        (self.task / "README.md").write_text("# Alpha\nContesto sintetico.\n", encoding="utf-8")
        (self.task / "Kanban.md").write_text("# Alpha\n\n## Idee da call\n", encoding="utf-8")
        self.timestamp = datetime(2026, 9, 1, 10, 20).timestamp()
        self.source = self.incoming / "call originale.mp4"
        self.source.write_bytes(b"synthetic video bytes")
        os.utime(self.source, (self.timestamp, self.timestamp))
        self.job_id = "0123456789abcdef01234567"

    def _entry(self, title: str = "Review sync") -> dict:
        stat = self.source.stat()
        return {
            "id": self.job_id,
            "signature": [stat.st_size, stat.st_mtime_ns],
            "title": title,
            "task_dir": str(self.task),
            "timestamp": self.timestamp,
            "destination": None,
            "recording_target": None,
            "sha256": None,
        }

    def _save_context(self, archived: bool = False, title: str = "Review sync") -> tuple[Path, dict]:
        entry = self._entry(title)
        call_dir = recordings.save_recording(
            self.root, self.source, entry, lambda **values: entry.update(values)
        )
        self.source = Path(entry["recording_target"])
        if archived:
            archive_dir = self.task / "archivio"
            archive_dir.mkdir()
            call_dir = call_dir.rename(archive_dir / call_dir.name)
            self.source = call_dir / self.source.name
            note = call_dir / f"{title}.md"
            fm.update_field(note, "archived", True)
            fm.update_field(note, "archived_at", "2026-09-10")
        context = {
            "recording_id": entry["id"],
            "sha256": entry["sha256"],
            "category": "saved",
            "timestamp": self.timestamp,
            "existing_dir": str(call_dir),
            "title": title,
            "task_dir": str(self.task),
            "task_name": "Alpha",
        }
        return call_dir, context

    def _trash_context(self) -> tuple[Path, dict]:
        entry = self._entry("discarded call")
        recordings.discard_recording(
            self.root, self.source, entry, lambda **values: entry.update(values)
        )
        self.source = Path(entry["recording_target"])
        return self.source, {
            "recording_id": entry["id"],
            "sha256": entry["sha256"],
            "category": "trash",
            "timestamp": self.timestamp,
        }

    def _mock_pipeline(self):
        self.provider = Mock()
        self.provider.default_summary_model.return_value = "synthetic-summary"
        self.stack.enter_context(patch.object(pc, "wait_stable"))
        self.load_provider = self.stack.enter_context(
            patch.object(pc, "_load_claude", return_value=self.provider)
        )
        self.stack.enter_context(patch.object(pc._ffmpeg, "get_duration", return_value=20))
        self.stack.enter_context(
            patch.object(
                pc._ffmpeg,
                "extract_audio",
                side_effect=lambda _source, target: Path(target).write_bytes(b"synthetic audio"),
            )
        )
        self.stack.enter_context(
            patch.object(
                pc._ffmpeg,
                "compress_audio",
                side_effect=lambda _source, target, *_: Path(target).write_bytes(b"compressed audio"),
            )
        )
        self.transcribe = self.stack.enter_context(
            patch("scripts.transcribe_with_groq.transcribe", return_value="Discussione sintetica sul progetto Alpha.")
        )
        self.summary = self.stack.enter_context(
            patch.object(pc, "_invoke_summary", side_effect=self._write_summary)
        )
        self.recognize = self.stack.enter_context(
            patch.object(pc, "_recognize_task", return_value=self.task)
        )
        self.classify_final = self.stack.enter_context(
            patch.object(pc, "_get_task_dir", return_value=self.task)
        )
        self.rebuild = self.stack.enter_context(patch.object(pc.obs_indexes, "rebuild"))
        self.kanban = self.stack.enter_context(
            patch("scripts.update_project_kanban.update_from_summary", return_value=0)
        )
        self.stack.enter_context(
            patch("scripts.archive_old_calls.archive", return_value={"archived": 0, "skipped": 0, "videos_deleted": 0})
        )
        self.stack.enter_context(patch.object(pc, "_cleanup_source_archive", return_value=0))

    @staticmethod
    def _write_summary(
        _provider,
        _transcript_path,
        output_path,
        _prompt_path,
        _model,
        _project_instructions_path=None,
        _global_index_path=None,
        task_readme_path=None,
    ):
        fields = {"persone": ["Giulia Rossi"], "sistemi": ["CRM"], "tags": ["call"]}
        body = "# riassunto\n## Discussione tecnica\n\nSintesi sintetica della conversazione.\n"
        Path(output_path).write_text(fm.render_frontmatter(fields, body), encoding="utf-8")

    def _signature(self) -> list[int]:
        stat = self.source.stat()
        return [stat.st_size, stat.st_mtime_ns]

    def test_catalog_saved_processing_completes_in_place_and_disappears_from_listing(self):
        from scripts import recording_catalog

        self._mock_pipeline()
        call_dir, _ = self._save_context()
        items = recording_catalog.list_unprocessed(self.root)
        self.assertEqual(len(items), 1)

        result = recording_catalog.process_recording(self.root, items[0])

        self.assertEqual(result["call_directory"], str(call_dir))
        self.assertTrue(self.source.is_file())
        self.assertEqual(recording_catalog.list_unprocessed(self.root), [])
        self.transcribe.assert_called_once()

    def test_catalog_trash_processing_resumes_after_source_move_without_retranscription(self):
        from scripts import recording_catalog

        self._mock_pipeline()
        self._trash_context()
        self.rebuild.side_effect = [OSError("index unavailable"), None]
        item = recording_catalog.list_unprocessed(self.root)[0]
        with self.assertRaisesRegex(OSError, "index unavailable"):
            recording_catalog.process_recording(self.root, item)
        self.assertFalse(self.source.exists())

        interrupted = recording_catalog.list_unprocessed(self.root)
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0]["status"], "interrupted")
        result = recording_catalog.process_recording(self.root, interrupted[0])

        self.assertTrue(Path(result["source_archive"]).is_file())
        self.assertEqual(recording_catalog.list_unprocessed(self.root), [])
        self.transcribe.assert_called_once()

    def test_saved_recording_promotes_outputs_and_preserves_archive_metadata(self):
        self._mock_pipeline()
        call_dir, context = self._save_context(archived=True)
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = pc.process(
            self.source,
            root=self.root,
            expected_signature=self._signature(),
            recording_context=context,
        )

        self.assertEqual(str(call_dir), result["call_directory"])
        self.assertEqual(context["recording_id"], result["recording_id"])
        self.assertTrue(self.source.is_file())
        self.assertEqual(source_hash, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(1, len(list(call_dir.glob("*.mp4"))))
        self.assertTrue((call_dir / "audio_compresso.m4a").is_file())
        self.assertTrue((call_dir / "trascrizione.txt").is_file())
        self.assertTrue((call_dir / ".call-job.json").is_file())
        fields = fm.read_fields(call_dir / "Review sync.md")
        self.assertEqual("2026-09-01", fields["data"])
        self.assertEqual("10:20", fields["ora"])
        self.assertEqual("[[Alpha]]", fields["task"])
        self.assertIs(True, fields["archived"])
        self.assertEqual("2026-09-10", str(fields["archived_at"]))
        self.recognize.assert_not_called()
        self.classify_final.assert_not_called()
        self.assertEqual(self.task / "README.md", self.summary.call_args.args[7])
        self.rebuild.assert_called_once_with(self.root)
        self.kanban.assert_called_once()
        state = next(job for job in jobs.iter_jobs(self.root) if job.get("recording_id") == context["recording_id"])
        self.assertTrue(state["complete"])
        self.assertEqual(call_dir, Path(result["call_directory"]))

    def test_saved_audio_m4a_original_is_preserved(self):
        self.source = self.source.rename(self.incoming / "audio.m4a")
        self._mock_pipeline()
        call_dir, context = self._save_context(title="audio")
        original_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

        result = pc.process(
            self.source,
            root=self.root,
            expected_signature=self._signature(),
            recording_context=context,
        )

        self.assertTrue(self.source.is_file())
        self.assertEqual(original_hash, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(call_dir / "audio_compresso.m4a", Path(result["audio"]))
        self.assertEqual(original_hash, hashlib.sha256((call_dir / "audio.m4a").read_bytes()).hexdigest())

    def test_saved_audio_compresso_original_uses_alternate_archive_name(self):
        self.source = self.source.rename(self.incoming / "audio_compresso.m4a")
        self._mock_pipeline()
        call_dir, context = self._save_context(title="audio_compresso")
        original_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

        result = pc.process(
            self.source,
            root=self.root,
            expected_signature=self._signature(),
            recording_context=context,
        )

        self.assertTrue(self.source.is_file())
        self.assertEqual(original_hash, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(call_dir / "audio_elaborato.m4a", Path(result["audio"]))
        self.assertEqual(original_hash, hashlib.sha256((call_dir / "audio_compresso.m4a").read_bytes()).hexdigest())

    def test_saved_audio_compresso_refuses_foreign_alternate_before_provider(self):
        self.source = self.source.rename(self.incoming / "audio_compresso.m4a")
        call_dir, context = self._save_context(title="audio_compresso")
        self._mock_pipeline()
        alternate = call_dir / "audio_elaborato.m4a"
        alternate.write_bytes(b"foreign output")

        with self.assertRaisesRegex(FileExistsError, "Artefatto estraneo"):
            pc.process(
                self.source,
                root=self.root,
                expected_signature=self._signature(),
                recording_context=context,
            )

        self.load_provider.assert_not_called()
        self.transcribe.assert_not_called()
        self.summary.assert_not_called()
        self.assertEqual(b"foreign output", alternate.read_bytes())
        self.assertTrue(self.source.is_file())

    def test_saved_resume_finds_job_after_call_folder_moves(self):
        self._mock_pipeline()
        call_dir, context = self._save_context()
        self.rebuild.side_effect = [RuntimeError("synthetic index interruption"), None]
        with self.assertRaisesRegex(RuntimeError, "synthetic index interruption"):
            pc.process(
                self.source,
                root=self.root,
                expected_signature=self._signature(),
                recording_context=context,
            )

        archive_dir = self.task / "archivio"
        archive_dir.mkdir()
        moved_dir = call_dir.rename(archive_dir / call_dir.name)
        self.source = moved_dir / self.source.name
        os.utime(self.source, (self.timestamp + 60, self.timestamp + 60))
        context = dict(context, existing_dir=str(moved_dir))

        result = pc.process(
            self.source,
            root=self.root,
            expected_signature=self._signature(),
            recording_context=context,
        )

        matching_jobs = [
            job for job in jobs.iter_jobs(self.root)
            if job.get("recording_id") == context["recording_id"]
        ]
        self.assertEqual(1, len(matching_jobs))
        self.assertEqual(str(self.source), matching_jobs[0]["source"])
        self.assertEqual(str(moved_dir), result["call_directory"])
        self.assertEqual(1, self.transcribe.call_count)
        self.assertEqual(1, self.summary.call_count)
        self.assertEqual(str(self.source), result["source_archive"])

    def test_trash_recording_uses_original_timestamp_and_moves_video_after_outputs(self):
        self._mock_pipeline()
        source, context = self._trash_context()
        real_move = shutil.move

        def assert_ready_before_move(src, dst, *args, **kwargs):
            if Path(src) == source:
                call_dir = Path(dst).parent
                self.assertTrue((call_dir / "audio_compresso.m4a").is_file())
                self.assertTrue((call_dir / "trascrizione.txt").is_file())
                summaries = [path for path in call_dir.glob("*.md") if path.name != "README.md"]
                self.assertEqual(1, len(summaries))
            return real_move(src, dst, *args, **kwargs)

        with patch("scripts.process_call.shutil.move", side_effect=assert_ready_before_move):
            result = pc.process(
                source,
                root=self.root,
                expected_signature=self._signature(),
                recording_context=context,
            )

        self.assertFalse(source.exists())
        self.assertTrue(Path(result["source_archive"]).is_file())
        self.assertTrue(Path(result["call_directory"]).name.startswith("2026-09-01 10.20 - "))
        self.assertIn("Discussione tecnica", Path(result["call_directory"]).name)
        self.assertEqual("2026-09-01", fm.read_fields(Path(result["summary"]))["data"])
        self.assertEqual("10:20", fm.read_fields(Path(result["summary"]))["ora"])
        self.recognize.assert_called_once()
        self.classify_final.assert_called_once()
        self.rebuild.assert_called_once_with(self.root)
        self.kanban.assert_called_once()

        calls = (self.transcribe.call_count, self.summary.call_count)
        resumed = pc.process(source, root=self.root, recording_context=context)
        self.assertEqual(result, resumed)
        self.assertEqual(calls, (self.transcribe.call_count, self.summary.call_count))

    def test_saved_promotion_resumes_after_a_partial_file_replace(self):
        self._mock_pipeline()
        call_dir, context = self._save_context()
        real_replace = os.replace
        interrupted = False

        def replace_then_interrupt(src, dst):
            nonlocal interrupted
            result = real_replace(src, dst)
            if Path(dst) == call_dir / "trascrizione.txt" and not interrupted:
                interrupted = True
                raise OSError("interrupted after one promoted file")
            return result

        with patch("scripts.process_call.os.replace", side_effect=replace_then_interrupt):
            with self.assertRaisesRegex(OSError, "one promoted file"):
                pc.process(
                    self.source,
                    root=self.root,
                    expected_signature=self._signature(),
                    recording_context=context,
                )
        self.assertTrue(self.source.is_file())
        self.assertTrue((call_dir / "trascrizione.txt").is_file())
        self.assertEqual("solo_registrazione", fm.read_fields(call_dir / "Review sync.md")["modalita"])

        result = pc.process(
            self.source,
            root=self.root,
            expected_signature=self._signature(),
            recording_context=context,
        )
        self.assertEqual(str(call_dir), result["call_directory"])
        self.assertTrue(fm.read_fields(call_dir / "Review sync.md").get("modalita") is None)
        self.assertEqual(1, self.transcribe.call_count)
        self.assertEqual(1, self.summary.call_count)

    def test_saved_resume_refuses_to_replace_a_promoted_file_changed_by_another_writer(self):
        self._mock_pipeline()
        call_dir, context = self._save_context()
        real_replace = os.replace
        interrupted = False

        def replace_then_interrupt(src, dst):
            nonlocal interrupted
            result = real_replace(src, dst)
            if Path(dst) == call_dir / "trascrizione.txt" and not interrupted:
                interrupted = True
                raise OSError("interrupted after one promoted file")
            return result

        with patch("scripts.process_call.os.replace", side_effect=replace_then_interrupt):
            with self.assertRaisesRegex(OSError, "one promoted file"):
                pc.process(
                    self.source,
                    root=self.root,
                    expected_signature=self._signature(),
                    recording_context=context,
                )
        target = call_dir / "trascrizione.txt"
        target.write_text("changed by another writer", encoding="utf-8")

        with self.assertRaisesRegex(FileExistsError, "modificato o estraneo"):
            pc.process(
                self.source,
                root=self.root,
                expected_signature=self._signature(),
                recording_context=context,
            )
        self.assertEqual("changed by another writer", target.read_text(encoding="utf-8"))
        self.assertEqual(1, self.transcribe.call_count)
        self.assertEqual(1, self.summary.call_count)

    def test_context_hash_and_destination_marker_are_checked_before_processing(self):
        call_dir, context = self._save_context()
        self._mock_pipeline()
        bad_hash_context = dict(context, sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "marker"):
            pc.process(self.source, root=self.root, recording_context=bad_hash_context)
        self.load_provider.assert_not_called()
        self.transcribe.assert_not_called()
        self.summary.assert_not_called()
        self.assertTrue(self.source.is_file())

        context["existing_dir"] = str(Path(self.temp) / "outside")
        with self.assertRaisesRegex(ValueError, "fuori dalla root"):
            pc.process(self.source, root=self.root, recording_context=context)
        self.assertTrue(self.source.is_file())


if __name__ == "__main__":
    unittest.main()
