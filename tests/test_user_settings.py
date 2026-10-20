from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import jobs, settings, user_settings


class UserSettingsTests(unittest.TestCase):
    def test_roundtrip_and_failed_save_preserves_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "settings.json"
            values = user_settings.snapshot()
            values["CLAUDE_SUMMARY_MODEL"] = "custom-model"
            user_settings.save(values, target)
            self.assertEqual(user_settings.load(target), values)
            with self.assertRaises(ValueError):
                user_settings.save({"CLAUDE_TIMEOUT_SECONDS": -1}, target)
            self.assertEqual(user_settings.load(target), values)

    def test_rejects_unknown_keys_invalid_models_and_nonfinite_numbers(self):
        for values in ({"GROQ_API_KEY": "secret"}, {"CLAUDE_SUMMARY_MODEL": "--help"},
                       {"CLAUDE_SUMMARY_EFFORT": "unknown"}, {"CLAUDE_SUMMARY_RETRIES": True},
                       {"CLAUDE_TIMEOUT_SECONDS": float("nan")}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                user_settings.validate(values)

    def test_corrupt_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "settings.json"
            target.write_text("broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                user_settings.load(target)
            self.assertEqual(target.read_text(encoding="utf-8"), "broken")

    def test_checkpoint_preferences_are_scoped_and_restored_on_failure(self):
        original = settings.CLAUDE_SUMMARY_EFFORT
        with self.assertRaises(RuntimeError):
            with user_settings.use({"CLAUDE_SUMMARY_EFFORT": "high"}):
                self.assertEqual(settings.CLAUDE_SUMMARY_EFFORT, "high")
                raise RuntimeError("failed")
        self.assertEqual(settings.CLAUDE_SUMMARY_EFFORT, original)

    def test_preferences_do_not_invalidate_checkpoint_but_archive_changes_do(self):
        old = {"archive_max_mb": 19, "summary_model": "old", "runtime_settings": {}}
        new = {"archive_max_mb": 19, "summary_model": "new", "runtime_settings": {"x": 1}}
        self.assertTrue(jobs.compatible_config(old, new))
        self.assertFalse(jobs.compatible_config(old, {**new, "archive_max_mb": 5}))

    def test_menu_save_applies_and_escape_discards(self):
        with patch("scripts.terminal.select_item", side_effect=["CLAUDE_SUMMARY_MODEL", "save"]), \
                patch("scripts.terminal.input_line", return_value="custom-model"), \
                patch.object(user_settings, "save") as save, \
                patch.object(settings, "CLAUDE_SUMMARY_MODEL", "original"):
            user_settings.show_menu()
            self.assertEqual(settings.CLAUDE_SUMMARY_MODEL, "custom-model")
            self.assertEqual(save.call_args.args[0]["CLAUDE_SUMMARY_MODEL"], "custom-model")
        with patch("scripts.terminal.select_item", side_effect=["CLAUDE_SUMMARY_MODEL", None, "discard"]), \
                patch("scripts.terminal.input_line", return_value="other"), \
                patch.object(user_settings, "save") as save:
            before = user_settings.snapshot()
            user_settings.show_menu()
            self.assertEqual(user_settings.snapshot(), before)
            save.assert_not_called()
