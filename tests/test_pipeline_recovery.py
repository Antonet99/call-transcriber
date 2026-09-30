from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import jobs, process_call as pc, tasks, task_scoring

SUMMARY = "---\npersone: []\nsistemi: []\ntags: [call]\n---\n# riassunto\n## Discussione tecnica\n### Contesto\nConfronto tecnico."


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.vault = self.root / "vault"
        self.provider = Mock()
        self.provider.default_summary_model.return_value = "test"
        self.provider.invoke_summary.return_value = SUMMARY
        self.provider.invoke_task_classification.return_value = "NESSUNA_TASK"
        self.stack.enter_context(patch.object(pc, "wait_stable"))
        self.stack.enter_context(patch.object(pc, "_load_claude", return_value=self.provider))
        self.stack.enter_context(patch.object(pc._ffmpeg, "get_duration", return_value=30))
        self.stack.enter_context(patch.object(pc._ffmpeg, "extract_audio", side_effect=shutil.copy2))
        self.transcribe = self.stack.enter_context(patch("scripts.transcribe_with_groq.transcribe", side_effect=self._transcribe))
        self.compress = self.stack.enter_context(patch.object(pc._ffmpeg, "compress_audio", side_effect=lambda src, dst, *_: shutil.copy2(src, dst)))

    def _transcribe(self, audio, output, **kwargs):
        text = "Discussione tecnica " + audio.read_bytes().decode()
        output.write_text(text, encoding="utf-8")
        return text

    def source(self, name="source.m4a", data=b"synthetic"):
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_compression_retry_reuses_transcript_and_summary(self):
        source = self.source()
        self.compress.side_effect = RuntimeError("compression failed")
        with self.assertRaises(RuntimeError):
            pc.process(source, root=self.vault)
        self.compress.side_effect = lambda src, dst, *_: shutil.copy2(src, dst)
        result = pc.process(source, root=self.vault)
        self.assertEqual(self.transcribe.call_count, 1)
        self.assertEqual(self.provider.invoke_summary.call_count, 1)
        self.assertTrue(Path(result["summary"]).is_file())
        self.assertEqual(len(list((self.vault / "completate" / "Senza progetto").glob("*/.call-job.json"))), 1)

    def test_inputs_same_stem_and_minute_have_independent_cache(self):
        first = self.source("same.m4a", b"first")
        second = self.source("same.mp4", b"second")
        stamp = datetime.now().replace(second=0, microsecond=0).timestamp()
        for source in (first, second):
            os.utime(source, (stamp, stamp))
        self.provider.invoke_summary.side_effect = RuntimeError("provider offline")
        with patch.object(pc._cfg, "LLM_RETRY_DELAY_SECONDS", 0):
            for source in (first, second):
                with self.assertRaises(RuntimeError):
                    pc.process(source, root=self.vault)
        self.assertEqual(self.transcribe.call_count, 2)
        self.assertEqual(len(list(jobs.iter_jobs(self.vault))), 2)

    def test_resume_after_source_move_recovers_pending_index(self):
        source = self.source()
        with patch.object(pc.obs_indexes, "rebuild", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                pc.process(source, root=self.vault)
        self.assertFalse(source.exists())
        self.assertEqual(jobs.pending_inputs(self.vault), [source])
        result = pc.process(source, root=self.vault)
        self.assertTrue(Path(result["summary"]).is_file())
        self.assertEqual(self.provider.invoke_summary.call_count, 1)
        self.assertEqual(jobs.pending_inputs(self.vault), [])

    def test_old_source_remains_at_returned_paths(self):
        source = self.source("old.mp4")
        stamp = (datetime.now() - timedelta(days=30)).timestamp()
        os.utime(source, (stamp, stamp))
        result = pc.process(source, root=self.vault)
        for key in ("summary", "audio", "transcript", "source_archive"):
            self.assertTrue(Path(result[key]).is_file(), key)

    def test_failure_after_directory_rename_recovers_reserved_destination(self):
        source = self.source()
        original = jobs.save_job
        failed = False

        def save(root, state):
            nonlocal failed
            if state.get("committed") and not failed:
                failed = True
                raise OSError("checkpoint failure")
            original(root, state)

        with patch.object(jobs, "save_job", side_effect=save):
            with self.assertRaises(OSError):
                pc.process(source, root=self.vault)
        result = pc.process(source, root=self.vault)
        self.assertTrue(Path(result["summary"]).is_file())
        self.assertEqual(self.transcribe.call_count, 1)

    def test_persistent_config_change_is_not_silently_reused(self):
        source = self.source()
        self.compress.side_effect = RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            pc.process(source, root=self.vault)
        with self.assertRaisesRegex(ValueError, "Configurazione diversa"):
            pc.process(source, root=self.vault, archive_max_mb=5)

    def test_vault_lock_is_reentrant(self):
        with jobs.vault_lock(self.vault):
            with jobs.vault_lock(self.vault):
                self.assertTrue((self.vault / ".pipeline" / "vault.lock").is_file())

    def test_explicit_negation_prevents_automatic_assignment(self):
        profile = task_scoring.TaskProfile("Alpha", self.root / "Alpha", keywords=("alpha pipeline",))
        scores = task_scoring.score_tasks("Questa call non riguarda alpha pipeline.", [profile])
        self.assertIsNone(task_scoring.automatic_winner(scores))

    def test_duplicate_task_names_have_unique_labels(self):
        paths = [self.vault / "completate" / "Task" / "Alpha",
                 self.vault / "completate" / "Task" / "progetti_archiviati" / "Alpha"]
        labels = tasks.task_labels(paths)
        self.assertEqual(labels[paths[0]], "Task/Alpha")
        self.assertEqual(labels[paths[1]], "Task/progetti_archiviati/Alpha")
