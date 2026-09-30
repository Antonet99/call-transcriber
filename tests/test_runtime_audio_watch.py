from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.audio import ffmpeg
from scripts import filesystem
from scripts.subprocesses import run_command
from scripts.watch_calls import _StateStore


class AudioRuntimeTests(unittest.TestCase):
    def test_compression_starts_at_calculated_target_and_only_decreases(self) -> None:
        values = ffmpeg._candidate_bitrates(1_000_000, 100)

        self.assertEqual(values[0], 73)
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertTrue(all(value <= values[0] for value in values))

    def test_audio_parameters_reject_non_finite_values(self) -> None:
        with self.assertRaises(ValueError):
            ffmpeg._validate_compression(math.nan, 10)
        with self.assertRaises(ValueError):
            ffmpeg._validate_compression(1, math.inf)
        with self.assertRaises(ValueError):
            ffmpeg.estimate_chunk_seconds(0, 128)

    def test_m4a_conversion_reuses_source_without_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.m4a"
            target = root / "nested" / "audio.m4a"
            source.write_bytes(b"audio")
            ffmpeg.convert_to_m4a(source, target)
            self.assertEqual(target.read_bytes(), b"audio")


class SubprocessRuntimeTests(unittest.TestCase):
    def test_run_command_returns_utf8_text(self) -> None:
        result = run_command(
            [sys.executable, "-c", "print('ciao')"],
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "ciao")


class WatchStateTests(unittest.TestCase):
    def test_failure_state_survives_new_watcher_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "da_processare" / "call.m4a"
            source.parent.mkdir()
            first = _StateStore(root)
            first.update(source, attempts=2, status="waiting", last_error="timeout")

            second = _StateStore(root)
            state = second.get(source)
            self.assertEqual(state["attempts"], 2)
            self.assertEqual(state["status"], "waiting")
            self.assertEqual(state["last_error"], "timeout")

    def test_atomic_state_write_retries_transient_windows_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            original_replace = filesystem.os.replace
            attempts = 0

            def flaky_replace(source: str, target: Path) -> None:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError(5, "Accesso negato")
                original_replace(source, target)

            with patch.object(filesystem.os, "replace", side_effect=flaky_replace):
                filesystem.atomic_write_text(
                    path,
                    '{"status": "ok"}',
                    retries=3,
                    retry_delay_seconds=0,
                )

            self.assertEqual(path.read_text(encoding="utf-8"), '{"status": "ok"}')
            self.assertEqual(attempts, 3)


if __name__ == "__main__":
    unittest.main()
