from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from scripts import watch_calls as watcher


class OnDemandUITests(unittest.TestCase):
    def setUp(self):
        self.handler = Mock()
        self.handler._root = Path("synthetic-vault")
        self.handler._extra = {}
        self.items = [
            {"id": "saved-id", "category": "saved", "source": "synthetic-vault/call.mp4",
             "title": "Call salvata", "timestamp": 1, "task_name": "Alpha", "status": "ready"},
            {"id": "trash-id", "category": "trash", "source": "synthetic-vault/trash/call.mp4",
             "title": "Call scartata", "timestamp": 2, "discarded_at": 3, "status": "ready"},
        ]

    def test_catalog_opens_both_categories_without_a_category_choice(self):
        with patch("scripts.recording_catalog.list_unprocessed", return_value=self.items), \
                patch("scripts.recording_catalog.process_recording") as process, \
                patch.object(watcher, "select_item", return_value=None) as selection:
            watcher._show_unprocessed(self.handler)

        selection.assert_called_once()
        args, kwargs = selection.call_args
        self.assertEqual(args[0], "Registrazioni non elaborate")
        self.assertEqual([choice.value for choice in args[1]], ["saved-id", "trash-id"])
        self.assertEqual(kwargs["groups"], ("Salvate nelle cartelle", "Nel cestino"))
        process.assert_not_called()

    def test_selecting_a_recording_then_escape_does_not_process_it(self):
        with patch("scripts.recording_catalog.list_unprocessed", return_value=self.items), \
                patch("scripts.recording_catalog.process_recording") as process, \
                patch.object(watcher, "select_item", side_effect=["saved-id", None, None]):
            watcher._show_unprocessed(self.handler)
        process.assert_not_called()

    def test_processing_starts_only_after_confirming_the_action(self):
        with patch("scripts.recording_catalog.list_unprocessed", side_effect=[self.items, []]), \
                patch("scripts.recording_catalog.process_recording", return_value={}) as process, \
                patch.object(watcher, "select_item", side_effect=["trash-id", "process", None]), \
                patch.object(watcher, "clear_input"):
            watcher._show_unprocessed(self.handler)
        process.assert_called_once_with(self.handler._root, self.items[1])

    def test_main_n_opens_catalog_without_starting_a_new_call(self):
        self.handler.snapshot.return_value = []
        observer = Mock()
        observer.is_alive.side_effect = [True, False]
        with patch.object(watcher, "read_key", return_value="n"), \
                patch.object(watcher, "panel"), \
                patch.object(watcher, "_show_unprocessed") as catalog:
            watcher._menu_loop(self.handler, observer, 3600)
        catalog.assert_called_once_with(self.handler)
        self.handler.execute.assert_not_called()

    def test_process_failure_stays_in_catalog_for_explicit_retry(self):
        with patch("scripts.recording_catalog.list_unprocessed", return_value=self.items), \
                patch("scripts.recording_catalog.process_recording", side_effect=OSError("temporarily unavailable")) as process, \
                patch.object(watcher, "select_item", side_effect=["saved-id", "process", None]) as selection, \
                patch.object(watcher, "clear_input"), \
                patch.object(watcher._LOGGER, "exception"):
            watcher._show_unprocessed(self.handler)
        self.assertEqual(process.call_count, 1)
        self.assertIn("Stato conservato", selection.call_args.kwargs["message"])

    def test_new_recording_does_not_steal_focus_from_catalog_before_enter(self):
        phase = 0
        path = Path("new-recording.mp4")
        def snapshot(**_kwargs):
            return [] if phase == 0 else [(path, {"status": "ready", "action": ""})]
        def read_key():
            nonlocal phase
            if phase == 0:
                phase = 1
                return "down"
            return "enter"
        self.handler.snapshot.side_effect = snapshot
        observer = Mock()
        observer.is_alive.side_effect = [True, True, False]
        with patch.object(watcher, "read_key", side_effect=read_key), \
                patch.object(watcher, "panel"), \
                patch.object(watcher, "clear_input"), \
                patch.object(watcher, "_show_unprocessed") as catalog:
            watcher._menu_loop(self.handler, observer, 3600)

        catalog.assert_called_once_with(self.handler)
        self.handler.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
