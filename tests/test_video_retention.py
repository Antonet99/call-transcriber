from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest

from scripts.archive_old_calls import archive
from scripts.migrate_video_sources import migrate
from scripts.process_call import _archive_processed_source


class VideoRetentionTests(unittest.TestCase):
    def test_processed_video_moves_into_call_with_summary_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.mp4"
            source.write_bytes(b"video")
            call_dir = root / "completate" / "Task" / "Alpha" / "2026-09-09 10.00 - Call"
            call_dir.mkdir(parents=True)

            target = _archive_processed_source(
                source,
                root,
                timestamp=date.today(),
                keep_source=False,
                video_call_dir=call_dir,
                video_name="Titolo della call",
            )

            self.assertEqual(target, call_dir / "Titolo della call.mp4")
            self.assertTrue(target.is_file())
            self.assertFalse(source.exists())
            self.assertFalse((root / "completate" / "archivio").exists())

    def test_archiving_call_deletes_only_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = (date.today() - timedelta(days=16)).isoformat()
            call_dir = root / "completate" / "Task" / "Alpha" / f"{old} 10.00 - Vecchia call"
            call_dir.mkdir(parents=True)
            (call_dir / "Vecchia call.md").write_text("# Riassunto", encoding="utf-8")
            (call_dir / "audio_compresso.m4a").write_bytes(b"audio")
            (call_dir / "Vecchia call.mp4").write_bytes(b"video")

            result = archive(root, days=15)
            archived_dir = call_dir.parent / "archivio" / call_dir.name

            self.assertEqual(result["archived"], 1)
            self.assertEqual(result["videos_deleted"], 1)
            self.assertTrue((archived_dir / "Vecchia call.md").exists())
            self.assertTrue((archived_dir / "audio_compresso.m4a").exists())
            self.assertFalse((archived_dir / "Vecchia call.mp4").exists())

    def test_existing_general_archive_video_is_migrated_by_unique_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_dir = root / "completate" / "archivio"
            call_dir = root / "completate" / "Task" / "Alpha" / "2026-09-08 17.22 - Estrazione documenti SGQ per POSI"
            archive_dir.mkdir(parents=True)
            call_dir.mkdir(parents=True)
            (call_dir / "Estrazione documenti SGQ per POSI.md").write_text(
                "# Riassunto", encoding="utf-8"
            )
            source = archive_dir / "2026-09-08 17.22 - 2026-09-08 17-12-06.mp4"
            source.write_bytes(b"video")

            result = migrate(root)

            target = call_dir / "Estrazione documenti SGQ per POSI.mp4"
            self.assertEqual(result["moved"], 1)
            self.assertTrue(target.exists())
            self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main()
