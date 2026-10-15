from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import recordings
from scripts.archive_old_calls import archive
from scripts.obsidian.frontmatter import read_fields
from scripts.recordings import cleanup_trash, discard_recording, save_recording


class RecordingActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "vault"
        self.incoming = self.root / "da_processare"
        self.task = self.root / "completate" / "Task" / "Alpha"
        self.incoming.mkdir(parents=True)
        self.task.mkdir(parents=True)
        (self.task / "README.md").write_text("# Alpha\n", encoding="utf-8")
        self.timestamp = datetime(2026, 10, 15, 9, 30).timestamp()

    def _source(self, name: str = "original.m4a", data: bytes = b"synthetic recording") -> Path:
        path = self.incoming / name
        path.write_bytes(data)
        os.utime(path, (self.timestamp, self.timestamp))
        return path

    def _entry(self, source: Path, *, job_id: str = "0123456789abcdef01234567", title: str = "Call tecnica") -> dict:
        stat = source.stat()
        return {
            "id": job_id,
            "signature": [stat.st_size, stat.st_mtime_ns],
            "title": title,
            "task_dir": str(self.task),
            "timestamp": self.timestamp,
            "destination": None,
            "recording_target": None,
            "sha256": None,
        }

    def test_save_keeps_only_original_and_note_without_processing_audio(self):
        source = self._source()
        entry = self._entry(source)
        checkpoint_calls = []

        def checkpoint(**values):
            if "destination" in values:
                self.assertTrue(source.is_file())
            checkpoint_calls.append(values)
            entry.update(values)

        destination = save_recording(self.root, source, entry, checkpoint)
        recording = destination / "Call tecnica.m4a"
        note = destination / "Call tecnica.md"

        self.assertFalse(source.exists())
        self.assertEqual(b"synthetic recording", recording.read_bytes())
        self.assertEqual(
            {"Call tecnica.m4a", "Call tecnica.md", ".recording-job.json"},
            {path.name for path in destination.iterdir()},
        )
        self.assertEqual(2, len(checkpoint_calls))
        self.assertEqual(str(destination), entry["destination"])
        self.assertEqual(str(recording), entry["recording_target"])
        self.assertEqual(hashlib.sha256(recording.read_bytes()).hexdigest(), entry["sha256"])
        fields = read_fields(note)
        self.assertEqual("2026-10-15", fields["data"])
        self.assertEqual("09:30", fields["ora"])
        self.assertEqual("[[Alpha]]", fields["task"])
        self.assertEqual([], fields["persone"])
        self.assertEqual([], fields["sistemi"])
        self.assertEqual(["call"], fields["tags"])
        self.assertEqual("solo_registrazione", fields["modalita"])
        self.assertIn("[[Call tecnica.m4a]]", note.read_text(encoding="utf-8"))
        self.assertIn("non trascritta", note.read_text(encoding="utf-8"))
        self.assertTrue(entry["note_ready"])

    def test_save_resumes_after_recording_was_moved_but_note_was_not_written(self):
        source = self._source()
        entry = self._entry(source)
        move = recordings._move_no_overwrite
        failed = False

        def move_then_fail(source_path, target_path):
            nonlocal failed
            move(source_path, target_path)
            if not failed:
                failed = True
                raise OSError("simulated interruption after move")

        with patch("scripts.recordings._move_no_overwrite", side_effect=move_then_fail):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                save_recording(self.root, source, entry, lambda **values: entry.update(values))

        self.assertFalse(source.exists())
        target = Path(entry["recording_target"])
        self.assertTrue(target.is_file())
        destination = save_recording(self.root, source, entry, lambda **values: entry.update(values))
        self.assertTrue((destination / "Call tecnica.md").is_file())
        self.assertEqual(b"synthetic recording", target.read_bytes())

    def test_same_title_and_timestamp_gets_a_collision_suffix(self):
        first_source = self._source("first.mp4", b"first")
        second_source = self._source("second.mp4", b"second")
        first_entry = self._entry(first_source, job_id="111111111111111111111111")
        second_entry = self._entry(second_source, job_id="222222222222222222222222")
        first = save_recording(
            self.root, first_source, first_entry, lambda **values: first_entry.update(values)
        )
        second = save_recording(
            self.root, second_source, second_entry, lambda **values: second_entry.update(values)
        )
        self.assertEqual("2026-10-15 09.30 - Call tecnica", first.name)
        self.assertEqual("2026-10-15 09.30 - Call tecnica (2)", second.name)
        self.assertEqual(b"first", (first / "Call tecnica.mp4").read_bytes())
        self.assertEqual(b"second", (second / "Call tecnica (2).mp4").read_bytes())
        self.assertTrue((second / "Call tecnica (2).mp4").is_file())
        self.assertTrue((second / "Call tecnica (2).md").is_file())

    def test_discard_is_retained_for_29_days_and_removed_at_30(self):
        source = self._source(data=b"discard this")
        entry = self._entry(source)
        destination = discard_recording(self.root, source, entry, lambda **values: entry.update(values))
        metadata_path = destination / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual("original.m4a", metadata["name"])
        self.assertEqual(str(source), metadata["original_path"])
        self.assertEqual(entry["id"], metadata["id"])
        self.assertEqual(hashlib.sha256(b"discard this").hexdigest(), metadata["sha256"])
        self.assertFalse(source.exists())

        with patch("scripts.recordings._file_hash", side_effect=AssertionError("fresh recording must not be hashed")):
            self.assertEqual(0, cleanup_trash(self.root, now=metadata["discarded_at"] + 29 * 86400))
        self.assertTrue((destination / "original.m4a").is_file())
        self.assertEqual(1, cleanup_trash(self.root, now=metadata["discarded_at"] + 30 * 86400))
        self.assertFalse(destination.exists())

    def test_cleanup_ignores_corrupt_metadata_and_unrelated_files(self):
        source = self._source(data=b"keep until verified")
        entry = self._entry(source)
        destination = discard_recording(self.root, source, entry, lambda **values: entry.update(values))
        metadata_path = destination / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata_path.write_text("{broken", encoding="utf-8")
        unrelated = destination / "manual.txt"
        unrelated.write_text("keep", encoding="utf-8")
        second_source = self._source("other.m4a", b"another expired recording")
        second_entry = self._entry(second_source, job_id="abcdefabcdefabcdefabcdef")
        second_destination = discard_recording(
            self.root, second_source, second_entry, lambda **values: second_entry.update(values)
        )
        second_metadata = json.loads((second_destination / "metadata.json").read_text(encoding="utf-8"))
        second_unrelated = second_destination / "manual.txt"
        second_unrelated.write_text("also keep", encoding="utf-8")

        self.assertEqual(
            1,
            cleanup_trash(
                self.root,
                now=max(metadata["discarded_at"], second_metadata["discarded_at"]) + 31 * 86400,
            ),
        )
        self.assertTrue((destination / "original.m4a").is_file())
        self.assertEqual("keep", unrelated.read_text(encoding="utf-8"))
        self.assertFalse((second_destination / "other.m4a").exists())
        self.assertEqual("also keep", second_unrelated.read_text(encoding="utf-8"))
        self.assertFalse((second_destination / "metadata.json").exists())
        self.assertFalse((second_destination / ".recording-job.json").exists())

    def test_rejects_path_escape_and_source_symlink(self):
        source = self._source()
        entry = self._entry(source)
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        entry["destination"] = str(outside / "2026-10-15 09.30 - Call tecnica")
        entry["recording_target"] = str(outside / "2026-10-15 09.30 - Call tecnica" / "Call tecnica.m4a")
        with self.assertRaises(ValueError):
            save_recording(self.root, source, entry, lambda **values: entry.update(values))
        self.assertTrue(source.is_file())

        symlink_entry = self._entry(source, job_id="abcdefabcdefabcdefabcdef")
        original_is_symlink = Path.is_symlink

        def simulated_symlink(path):
            return path == source or original_is_symlink(path)

        with patch.object(Path, "is_symlink", new=simulated_symlink):
            with self.assertRaisesRegex(ValueError, "collegamento simbolico"):
                save_recording(self.root, source, symlink_entry, lambda **values: symlink_entry.update(values))

    def test_detects_source_change_during_hash_before_checkpoint(self):
        source = self._source()
        entry = self._entry(source)
        original_hash = recordings._file_hash
        changed = False

        def resize_during_hash(path):
            nonlocal changed
            digest = original_hash(path)
            if path == source and not changed:
                changed = True
                path.write_bytes(path.read_bytes() + b" changed")
            return digest

        checkpoint_calls = []
        with patch("scripts.recordings._file_hash", side_effect=resize_during_hash):
            with self.assertRaisesRegex(ValueError, "cambiata durante il controllo"):
                save_recording(self.root, source, entry, lambda **values: checkpoint_calls.append(values))
        self.assertTrue(source.is_file())
        self.assertEqual([], checkpoint_calls)

    def test_discard_resumes_after_recording_move(self):
        source = self._source(data=b"resume discard")
        entry = self._entry(source)
        move = recordings._move_no_overwrite
        interrupted = False

        def move_then_fail(source_path, target_path):
            nonlocal interrupted
            move(source_path, target_path)
            if not interrupted:
                interrupted = True
                raise OSError("simulated interruption after discard move")

        with patch("scripts.recordings._move_no_overwrite", side_effect=move_then_fail):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                discard_recording(self.root, source, entry, lambda **values: entry.update(values))
        self.assertFalse(source.exists())
        destination = discard_recording(self.root, source, entry, lambda **values: entry.update(values))
        self.assertTrue((destination / "original.m4a").is_file())
        self.assertTrue((destination / "metadata.json").is_file())

    def test_discard_retry_after_move_failure_starts_retention_at_success(self):
        source = self._source(data=b"retry discard")
        entry = self._entry(source)
        first_attempt = self.timestamp + 10
        successful_move = first_attempt + 31 * 86400

        with patch("scripts.recordings.time.time", side_effect=[first_attempt, successful_move]):
            with patch("scripts.recordings._move_no_overwrite", side_effect=OSError("move failed")):
                with self.assertRaisesRegex(OSError, "move failed"):
                    discard_recording(self.root, source, entry, lambda **values: entry.update(values))
            destination = discard_recording(self.root, source, entry, lambda **values: entry.update(values))

        metadata = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(successful_move, metadata["discarded_at"])
        self.assertEqual(0, cleanup_trash(self.root, now=successful_move + 29 * 86400))
        self.assertEqual(1, cleanup_trash(self.root, now=successful_move + 30 * 86400))

    def test_save_accepts_its_note_after_index_normalization(self):
        source = self._source()
        entry = self._entry(source)
        destination = save_recording(self.root, source, entry, lambda **values: entry.update(values))
        note = destination / "Call tecnica.md"
        text = note.read_text(encoding="utf-8").replace("tags: [call]", "tags: [call, solo_registrazione]")
        note.write_text(text, encoding="utf-8")

        resumed = save_recording(self.root, source, entry, lambda **values: entry.update(values))
        self.assertEqual(destination, resumed)
        self.assertEqual("[call, solo_registrazione]", next(line for line in note.read_text(encoding="utf-8").splitlines() if line.startswith("tags:"))[6:])

    def test_cleanup_skips_symlinked_trash_entries(self):
        trash = self.root / "cestino_registrazioni"
        trash.mkdir()
        directory = trash / "333333333333333333333333"
        directory.mkdir()
        recording = directory / "original.m4a"
        recording.write_bytes(b"leave untouched")
        original_is_symlink = Path.is_symlink

        def simulated_symlink(path):
            return path == directory or original_is_symlink(path)

        with patch.object(Path, "is_symlink", new=simulated_symlink):
            self.assertEqual(0, cleanup_trash(self.root, now=datetime.now().timestamp() + 365 * 86400))
        self.assertEqual(b"leave untouched", recording.read_bytes())

    def test_archived_video_retention_removes_only_original_video_after_15_days(self):
        old_timestamp = datetime(2026, 9, 25, 9, 30).timestamp()
        self.timestamp = old_timestamp
        source = self._source("original.mp4", b"original video")
        entry = self._entry(source)
        destination = save_recording(self.root, source, entry, lambda **values: entry.update(values))

        result = archive(self.root, days=15)
        archived = self.task / "archivio" / destination.name
        self.assertEqual(1, result["archived"])
        self.assertFalse((archived / "Call tecnica.mp4").exists())
        self.assertTrue((archived / "Call tecnica.md").is_file())
        self.assertTrue((archived / ".recording-job.json").is_file())


if __name__ == "__main__":
    unittest.main()
