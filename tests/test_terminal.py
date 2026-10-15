from contextlib import nullcontext
from io import StringIO
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from rich.console import Console

from scripts import terminal, watch_calls


class TerminalTests(unittest.TestCase):
    def test_escape_cancels_input_immediately(self):
        with patch.object(terminal, "console", Console(file=StringIO())), \
                patch.object(terminal, "_keyboard_mode", return_value=nullcontext()), \
                patch.object(terminal, "_read_character", side_effect=["T", "i", "\x1b"]) as read:
            self.assertIsNone(terminal.input_line("Titolo (Esc = torna):"))
        self.assertEqual(read.call_count, 3)

    def test_input_preserves_case_accents_and_backspace(self):
        with patch.object(terminal, "console", Console(file=StringIO())), \
                patch.object(terminal, "_keyboard_mode", return_value=nullcontext()), \
                patch.object(terminal, "_read_character", side_effect=["C", "a", "F", "X", "\b", "è", "\r"]):
            self.assertEqual(terminal.input_line("Titolo:"), "CaFè")

    def test_enter_accepts_empty_input(self):
        with patch.object(terminal, "console", Console(file=StringIO())), \
                patch.object(terminal, "_keyboard_mode", return_value=nullcontext()), \
                patch.object(terminal, "_read_character", return_value="\r"):
            self.assertEqual(terminal.input_line("Titolo:"), "")

    def test_title_and_project_escape_return_to_menu(self):
        for values in ([None], ["Call", None]):
            with self.subTest(values=values), \
                    patch.object(watch_calls, "panel"), \
                    patch("scripts.tasks.discover_task_dirs", return_value=[]), \
                    patch.object(watch_calls, "input_line", side_effect=values):
                self.assertIsNone(watch_calls._save_details(Path("synthetic-vault"), Path("call.mp4")))

    def test_escape_returns_from_an_empty_queue(self):
        handler = Mock()
        handler.snapshot.return_value = []
        with patch.object(watch_calls, "panel"), \
                patch.object(watch_calls, "input_line", return_value=None) as prompt:
            self.assertIsNone(watch_calls._show_queue(handler))
        prompt.assert_called_once()
        handler.execute.assert_not_called()

    def test_repaint_replaces_panels_and_keeps_last_result(self):
        output = StringIO()
        screen = terminal._Screen()
        with patch.object(terminal, "_screen", screen):
            terminal.panel("Vecchio pannello", "vecchio contenuto")
            terminal.panel("Registrazione salvata", "Call sintetica", "green")
            terminal.panel("In ascolto", "Registrazioni in attesa: 0")
        Console(file=output, width=100).print(screen.render())
        rendered = output.getvalue()
        self.assertNotIn("Vecchio pannello", rendered)
        self.assertNotIn("vecchio contenuto", rendered)
        self.assertEqual(rendered.count("Call Transcriber"), 1)
        self.assertIn("In ascolto", rendered)
        self.assertIn("Registrazione salvata · Call sintetica", rendered)

    def test_live_phases_use_the_same_screen_and_reset_for_next_call(self):
        screen = terminal._Screen()
        with patch.object(terminal, "_screen", screen), \
                patch.object(terminal.console, "status") as nested_status:
            terminal.panel("Nuova call", "call.mp4")
            with terminal.phase("Trascrizione"):
                self.assertIsNotNone(screen.spinner)
            self.assertIsNone(screen.spinner)
            self.assertEqual(len(screen.steps), 1)
            terminal.panel("In ascolto", "0 in attesa")
            self.assertEqual(screen.steps, [])
        nested_status.assert_not_called()

    def test_interrupt_restores_alternate_screen_and_session_state(self):
        output = StringIO()
        with patch.dict(os.environ, {"TERM": "xterm-256color"}):
            console = Console(file=output, force_terminal=True, width=100, height=30,
                              legacy_windows=False)
        with patch.dict(os.environ, {"TERM": "xterm-256color"}), \
                patch.object(terminal, "console", console), \
                patch.object(terminal, "_keyboard_mode", return_value=nullcontext()):
            with self.assertRaises(KeyboardInterrupt):
                with terminal.screen_session():
                    terminal.panel("Nuova call", "synthetic.mp4")
                    raise KeyboardInterrupt
        self.assertIsNone(terminal._screen)
        self.assertIn("\x1b[?1049h", output.getvalue())
        self.assertIn("\x1b[?1049l", output.getvalue())


if __name__ == "__main__":
    unittest.main()
