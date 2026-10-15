import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from scripts import jobs, recordings
from scripts import watch_calls as watcher


class RecordingQueueTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.watch_dir = self.root / "da_processare"
        self.watch_dir.mkdir()
        self.handler = self.new_handler()

    def new_handler(self):
        handler = watcher._CallHandler(self.root, {}, readiness_interval_seconds=3600)
        self.addCleanup(handler.stop)
        return handler

    def add_file(self, name="call.m4a", data=b"synthetic recording"):
        path = self.watch_dir / name
        path.write_bytes(data)
        return path

    def make_ready(self, path, handler=None):
        handler = handler or self.handler
        handler._handle(str(path))
        for _ in range(3):
            handler._check_readiness()
        return handler._state.get(path)

    def test_ready_recording_does_not_start_pipeline_without_a_choice(self):
        path = self.add_file()
        with patch("scripts.process_call.process") as process:
            entry = self.make_ready(path)

        self.assertEqual(entry["status"], "ready")
        process.assert_not_called()

    def test_new_arrivals_and_duplicate_events_keep_fifo_queue(self):
        first = self.add_file("z-first.m4a")
        self.handler.on_created(FileCreatedEvent(str(first)))
        original_id = self.handler._state.get(first)["id"]
        self.handler._check_readiness()
        second = self.add_file("a-second.m4a")
        self.handler.on_created(FileCreatedEvent(str(first)))
        self.handler.on_modified(FileModifiedEvent(str(first)))
        self.handler.on_moved(FileMovedEvent(str(first), str(first)))
        moved_source = self.root / "outside.m4a"
        moved_source.write_bytes(second.read_bytes())
        second.unlink()
        moved_source.rename(second)
        self.handler.on_moved(FileMovedEvent(str(moved_source), str(second)))
        for _ in range(3):
            self.handler._check_readiness()

        self.handler._state.update(first, ready_at=100, first_seen=10)
        self.handler._state.update(second, ready_at=100, first_seen=20)
        queue = self.handler.snapshot()

        self.assertEqual([path.name for path, _ in queue], ["z-first.m4a", "a-second.m4a"])
        self.assertEqual(len(queue), 2)
        self.assertEqual(self.handler._state.get(first)["id"], original_id)

    def test_queue_id_and_ready_at_survive_handler_recreation(self):
        path = self.add_file()
        before = self.make_ready(path)
        self.handler.stop()
        restarted = self.new_handler()

        restarted.scan()
        for _ in range(3):
            restarted._check_readiness()
        after = restarted._state.get(path)

        self.assertEqual(after["status"], "ready")
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["ready_at"], before["ready_at"])

    def test_changed_file_invalidates_saved_action_and_waits_for_stability(self):
        path = self.add_file()
        self.make_ready(path)
        with patch("scripts.process_call.process", side_effect=RuntimeError("synthetic failure")):
            with patch.object(watcher.console, "print"), patch.object(watcher._LOGGER, "exception"):
                self.assertFalse(self.handler.execute(path, "process"))
        before = self.handler._state.get(path)
        self.assertEqual(before["action"], "process")

        with path.open("ab") as recording:
            recording.write(b" changed")
        self.handler._check_readiness()
        changed = self.handler._state.get(path)

        self.assertEqual(changed["status"], "waiting")
        self.assertEqual(changed["action"], "")
        self.assertNotEqual(changed["id"], before["id"])
        self.assertIsNone(changed["ready_at"])
        self.handler._check_readiness()
        self.assertEqual(self.handler._state.get(path)["status"], "waiting")
        self.handler._check_readiness()
        self.assertEqual(self.handler._state.get(path)["status"], "ready")

    def test_process_failure_keeps_choice_and_does_not_retry_automatically(self):
        path = self.add_file()
        self.make_ready(path)
        with patch("scripts.process_call.process", side_effect=RuntimeError("synthetic failure")) as process:
            with patch.object(watcher.console, "print"), patch.object(watcher._LOGGER, "exception"):
                self.assertFalse(self.handler.execute(path, "process"))
            for _ in range(3):
                self.handler._check_readiness()

        state = self.handler._state.get(path)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["action"], "process")
        self.assertEqual(state["last_error"], "synthetic failure")

    def test_mutation_before_execute_blocks_action(self):
        path = self.add_file()
        self.make_ready(path)
        with path.open("ab") as recording:
            recording.write(b" changed")

        with patch("scripts.process_call.process") as process:
            self.assertFalse(self.handler.execute(path, "process"))

        state = self.handler._state.get(path)
        self.assertEqual(state["status"], "waiting")
        self.assertEqual(state["action"], "")
        process.assert_not_called()

    def test_new_arrival_during_process_does_not_block_queue(self):
        first = self.add_file("first.m4a")
        second = self.watch_dir / "second.m4a"
        self.make_ready(first)
        handler_entered = threading.Event()
        completed_during_process = []

        def process_call(**_kwargs):
            second.write_bytes(b"another synthetic recording")
            worker = threading.Thread(
                target=lambda: (self.handler._handle(str(second)), handler_entered.set()),
                daemon=True,
            )
            worker.start()
            worker.join(timeout=1)
            completed_during_process.append(not worker.is_alive())

        with patch("scripts.process_call.process", side_effect=process_call):
            self.assertTrue(self.handler.execute(first, "process"))

        self.assertTrue(handler_entered.is_set())
        self.assertEqual(completed_during_process, [True])
        self.assertEqual(self.handler._state.get(first)["status"], "done")
        self.assertEqual(self.handler._state.get(second)["status"], "waiting")

    def test_primary_journal_failure_rolls_back_action_and_skips_process(self):
        path = self.add_file()
        self.make_ready(path)
        before = self.handler._state.get(path)
        write_text = watcher.atomic_write_text

        def fail_primary(target, text, *args, **kwargs):
            if Path(target) == self.handler._state._path:
                raise OSError("primary journal unavailable")
            return write_text(target, text, *args, **kwargs)

        with patch("scripts.process_call.process") as process:
            with patch.object(watcher, "atomic_write_text", side_effect=fail_primary):
                with self.assertRaisesRegex(OSError, "primary journal unavailable"):
                    self.handler.execute(path, "process")

        self.assertEqual(self.handler._state.get(path), before)
        process.assert_not_called()

    def test_corrupt_primary_journal_recovers_from_backup(self):
        path = self.add_file()
        before = self.make_ready(path)
        self.handler.stop()
        state_path = self.root / ".pipeline" / "watcher-state.json"
        backup_path = state_path.with_suffix(".json.bak")
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
        state_path.write_text("{broken", encoding="utf-8")

        restarted = self.new_handler()
        restarted.scan()
        for _ in range(3):
            restarted._check_readiness()

        recovered = restarted._state.get(path)
        self.assertEqual(recovered["status"], "ready")
        self.assertEqual(recovered["id"], before["id"])
        self.assertEqual(recovered["ready_at"], before["ready_at"])
        self.assertTrue(backup["entries"])

    def test_two_corrupt_journals_stop_handler_creation_safely(self):
        path = self.add_file()
        self.make_ready(path)
        self.handler.stop()
        state_path = self.root / ".pipeline" / "watcher-state.json"
        backup_path = state_path.with_suffix(".json.bak")
        state_path.write_text("{broken primary", encoding="utf-8")
        backup_path.write_text("{broken backup", encoding="utf-8")

        with patch("scripts.process_call.process") as process:
            with self.assertRaisesRegex(RuntimeError, "Journal della coda illeggibile"):
                self.new_handler()

        self.assertEqual(state_path.read_text(encoding="utf-8"), "{broken primary")
        self.assertEqual(backup_path.read_text(encoding="utf-8"), "{broken backup")
        process.assert_not_called()

    def test_noninteractive_watch_persists_queue_without_observer_or_pipeline(self):
        path = self.add_file()
        with patch.object(watcher, "_setup_logging"), \
                patch.object(watcher, "interactive", return_value=False), \
                patch.object(watcher, "Observer") as observer, \
                patch.object(watcher, "panel"), \
                patch("scripts.process_call.process") as process, \
                patch("scripts.process_call._load_claude") as claude, \
                patch("scripts.transcribe_with_groq.transcribe") as transcribe:
            watcher.watch(self.root)

        observer.assert_not_called()
        process.assert_not_called()
        claude.assert_not_called()
        transcribe.assert_not_called()
        payload = json.loads((self.root / ".pipeline" / "watcher-state.json").read_text(encoding="utf-8"))
        entry = payload["entries"][watcher._StateStore.key(path)]
        self.assertEqual(entry["status"], "waiting")
        self.assertEqual(entry["action"], "")

    def test_pending_legacy_checkpoint_is_queued_for_explicit_resume(self):
        path = self.watch_dir / "checkpoint.m4a"
        job = {
            "id": "legacy-job",
            "source": str(path.resolve()),
            "complete": False,
            "size": 12,
            "mtime_ns": 34,
            "timestamp": 1,
            "config": {"archive_max_mb": None, "summary_model": "test", "task_model": "test"},
        }
        jobs.save_job(self.root, job)

        with patch("scripts.process_call.process") as process:
            self.handler.refresh_pending_inputs()
            state = self.handler._state.get(path)

        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["action"], "process")
        self.assertTrue(state["from_job"])
        self.assertEqual(self.handler.snapshot(ready_only=True)[0][0], path)
        process.assert_not_called()

    def test_save_choice_creates_note_without_processing_and_recovers_after_index_failure(self):
        path = self.add_file()
        self.make_ready(path)
        task_dir = self.root / "completate" / "Senza progetto"
        with patch("scripts.process_call.process") as process, \
                patch("scripts.process_call._load_claude") as claude, \
                patch("scripts.transcribe_with_groq.transcribe") as transcribe, \
                patch("scripts.update_project_kanban.get_provider") as provider, \
                patch("scripts.obsidian.indexes.rebuild", side_effect=OSError("index unavailable")), \
                patch.object(watcher._LOGGER, "exception"), \
                patch.object(watcher.console, "print"):
            self.assertFalse(self.handler.execute(path, "save", title="Call salvata", task_dir=task_dir))

        state = self.handler._state.get(path)
        destination = Path(state["destination"])
        target = Path(state["recording_target"])
        self.assertFalse(path.exists())
        self.assertTrue(target.is_file())
        self.assertTrue((destination / "Call salvata.md").is_file())
        self.assertEqual(state["action"], "save")
        self.assertEqual(state["status"], "error")
        self.assertTrue(state["note_ready"])
        process.assert_not_called()
        claude.assert_not_called()
        transcribe.assert_not_called()
        provider.assert_not_called()

        self.handler.stop()
        restarted = self.new_handler()
        restarted.scan()
        self.assertEqual(restarted._state.get(path)["status"], "interrupted")
        with patch("scripts.process_call.process") as process, \
                patch("scripts.process_call._load_claude") as claude, \
                patch("scripts.transcribe_with_groq.transcribe") as transcribe, \
                patch("scripts.update_project_kanban.get_provider") as provider:
            self.assertTrue(restarted.execute(path, "save"))

        self.assertEqual(restarted._state.get(path)["status"], "done")
        self.assertTrue(target.is_file())
        self.assertTrue((destination / "Call salvata.md").is_file())
        process.assert_not_called()
        claude.assert_not_called()
        transcribe.assert_not_called()
        provider.assert_not_called()

    def test_discard_choice_resumes_after_recording_move_without_processing(self):
        path = self.add_file()
        self.make_ready(path)
        write_text = recordings.atomic_write_text

        def fail_metadata(target, text, *args, **kwargs):
            if Path(target).name == "metadata.json":
                raise OSError("metadata unavailable")
            return write_text(target, text, *args, **kwargs)

        with patch("scripts.process_call.process") as process, \
                patch("scripts.process_call._load_claude") as claude, \
                patch("scripts.transcribe_with_groq.transcribe") as transcribe, \
                patch("scripts.update_project_kanban.get_provider") as provider, \
                patch("scripts.recordings.atomic_write_text", side_effect=fail_metadata), \
                patch.object(watcher._LOGGER, "exception"), \
                patch.object(watcher.console, "print"):
            self.assertFalse(self.handler.execute(path, "discard"))

        state = self.handler._state.get(path)
        destination = Path(state["destination"])
        target = Path(state["recording_target"])
        self.assertFalse(path.exists())
        self.assertTrue(target.is_file())
        self.assertFalse((destination / "metadata.json").exists())
        self.assertEqual(state["action"], "discard")
        self.assertEqual(state["status"], "error")
        process.assert_not_called()
        claude.assert_not_called()
        transcribe.assert_not_called()
        provider.assert_not_called()

        self.handler.stop()
        restarted = self.new_handler()
        restarted.scan()
        with patch("scripts.process_call.process") as process, \
                patch("scripts.process_call._load_claude") as claude, \
                patch("scripts.transcribe_with_groq.transcribe") as transcribe, \
                patch("scripts.update_project_kanban.get_provider") as provider:
            self.assertTrue(restarted.execute(path, "discard"))

        metadata = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(target.is_file())
        self.assertEqual(metadata["id"], state["id"])
        self.assertEqual(restarted._state.get(path)["status"], "done")
        process.assert_not_called()
        claude.assert_not_called()
        transcribe.assert_not_called()
        provider.assert_not_called()

    def test_menu_key_three_discards_without_followup_confirmation(self):
        path = self.add_file()
        handler = Mock()
        handler.snapshot.return_value = [(path, {"status": "ready", "action": ""})]
        observer = Mock()
        observer.is_alive.side_effect = [True, False]

        with patch.object(watcher, "read_key", return_value="3") as read_key, \
                patch.object(watcher, "panel"), \
                patch.object(watcher, "clear_input"):
            watcher._menu_loop(handler, observer, maintenance_interval=3600)

        handler.execute.assert_called_once_with(path, "discard", title="", task_dir=None)
        read_key.assert_called_once_with()

    def test_stale_pending_checkpoint_does_not_replace_reused_source_path(self):
        path = self.add_file(data=b"new recording")
        current = self.make_ready(path)
        jobs.save_job(self.root, {
            "id": "stale-job",
            "source": str(path.resolve()),
            "complete": False,
            "size": current["signature"][0] + 1,
            "mtime_ns": current["signature"][1] + 1,
            "timestamp": 1,
            "config": {"archive_max_mb": None, "summary_model": "old", "task_model": "old"},
        })

        self.handler.refresh_pending_inputs()
        after = self.handler._state.get(path)

        self.assertEqual(after["id"], current["id"])
        self.assertEqual(after["status"], "ready")
        self.assertEqual(after["action"], "")
        self.assertFalse(after.get("from_job", False))

    def test_completed_process_checkpoint_reconciles_missing_source_as_done(self):
        path = self.add_file()
        ready = self.make_ready(path)
        self.handler._state.update(path, action="process", status="interrupted")
        path.unlink()
        jobs.save_job(self.root, {
            "id": "completed-job",
            "source": str(path.resolve()),
            "complete": True,
            "size": ready["signature"][0],
            "mtime_ns": ready["signature"][1],
            "timestamp": 1,
            "config": {"archive_max_mb": None, "summary_model": "test", "task_model": "test"},
        })

        self.handler.refresh_pending_inputs()

        self.assertEqual(self.handler._state.get(path)["status"], "done")
        self.assertNotIn(path, [queued for queued, _ in self.handler.snapshot()])

    def test_kanban_skips_provider_for_save_only_note(self):
        from scripts import update_project_kanban
        from scripts.obsidian import frontmatter

        task_dir = self.root / "Task" / "Synthetic"
        task_dir.mkdir(parents=True)
        summary = task_dir / "Call.md"
        summary.write_text(
            frontmatter.render_frontmatter(
                {
                    "data": "2026-01-01",
                    "ora": "10:00",
                    "task": "Senza progetto",
                    "persone": [],
                    "sistemi": [],
                    "tags": ["call"],
                    "modalita": "solo_registrazione",
                },
                "# Call\n\nRegistrazione presente, ma non trascritta.\n",
            ),
            encoding="utf-8",
        )

        with patch.object(update_project_kanban, "get_provider", side_effect=AssertionError("provider called")):
            self.assertEqual(update_project_kanban.update_from_summary(summary, task_dir), 0)

    def test_restored_discarded_file_with_original_name_needs_a_new_choice(self):
        path = self.add_file()
        original = self.make_ready(path)
        self.assertTrue(self.handler.execute(path, "discard"))
        discarded = self.handler._state.get(path)
        Path(discarded["recording_target"]).rename(path)

        restored = self.make_ready(path)

        self.assertEqual(restored["signature"], original["signature"])
        self.assertNotEqual(restored["id"], original["id"])
        self.assertEqual(restored["action"], "")
        self.assertEqual(restored["status"], "ready")
        self.assertIsNone(restored["discarded_at"])

    def test_resume_missing_source_selects_checkpoint_for_the_chosen_signature(self):
        source = self.watch_dir / "reused.m4a"
        config = {"archive_max_mb": 19, "summary_model": "test", "task_model": "test"}
        for name, size, timestamp in (("older", 1, 10), ("newer", 2, 20)):
            jobs.save_job(self.root, {"id": name, "source": str(source), "timestamp": timestamp,
                                      "size": size, "mtime_ns": timestamp, "complete": False,
                                      "config": config})

        selected = jobs.load_job(self.root, source, config, expected_signature=[1, 10])

        self.assertEqual(selected["id"], "older")

    def test_load_job_rejects_source_changed_while_waiting_for_vault_lock(self):
        path = self.add_file()
        signature = self.make_ready(path)["signature"]
        path.write_bytes(b"replacement with a different size")

        with patch.object(jobs, "fingerprint") as fingerprint:
            with self.assertRaisesRegex(RuntimeError, "cambiata dopo la scelta"):
                jobs.load_job(self.root, path, {}, expected_signature=signature)

        fingerprint.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Controllo di condivisione file specifico di Windows")
    def test_stable_file_still_open_for_writing_is_not_ready(self):
        import ctypes
        from ctypes import wintypes

        path = self.add_file()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        handle = create(str(path), 0x40000000, 1, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            self.assertEqual(self.make_ready(path)["status"], "waiting")
        finally:
            close(handle)

        self.handler._check_readiness()
        self.assertEqual(self.handler._state.get(path)["status"], "ready")

    def test_only_one_instance_can_hold_vault_lock(self):
        with watcher._instance_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "Un altro watcher"):
                with watcher._instance_lock(self.root):
                    self.fail("second watcher acquired the same lock")


if __name__ == "__main__":
    unittest.main()
