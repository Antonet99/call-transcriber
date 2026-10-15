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

    def test_title_escape_returns_to_menu(self):
        with patch.object(watch_calls, "panel"), \
                patch.object(watch_calls, "input_line", return_value=None):
            self.assertIsNone(watch_calls._save_details(Path("synthetic-vault"), Path("call.mp4")))

    def test_project_escape_returns_to_title(self):
        with patch.object(watch_calls, "panel"), \
                patch("scripts.tasks.discover_task_dirs", return_value=[]), \
                patch.object(watch_calls, "input_line", side_effect=["Call", None]) as title, \
                patch.object(watch_calls, "select_item", return_value=None):
            self.assertIsNone(watch_calls._save_details(Path("synthetic-vault"), Path("call.mp4")))
        self.assertEqual(title.call_count, 2)
        self.assertEqual(title.call_args.kwargs["initial"], "Call")

    def test_escape_returns_from_an_empty_queue(self):
        handler = Mock()
        handler.snapshot.return_value = []
        with patch.object(terminal, "panel"), \
                patch.object(terminal, "read_key", return_value="escape"), \
                patch.object(terminal, "clear_input"):
            self.assertIsNone(watch_calls._show_queue(handler))
        handler.execute.assert_not_called()

    def test_selection_skips_disabled_rows_and_only_enter_confirms(self):
        selection = terminal.Selection([terminal.Choice("a", "First"),
                                        terminal.Choice("b", "Busy", enabled=False),
                                        terminal.Choice("c", "Last")])
        self.assertIsNone(selection.handle("3"))
        self.assertIsNone(selection.handle("down"))
        self.assertEqual(selection.handle("enter"), "c")
        self.assertIsNone(selection.handle("escape"))
        selection.handle("up")
        self.assertEqual(selection.handle("enter"), "a")

    def test_enter_on_empty_list_does_not_go_back(self):
        with patch.object(terminal, "panel"), \
                patch.object(terminal, "read_key", side_effect=["enter", "escape"]) as read, \
                patch.object(terminal, "clear_input"):
            self.assertIsNone(terminal.select_item("Empty", []))
        self.assertEqual(read.call_count, 2)

    def test_selection_groups_and_scrolling_keep_selected_row_visible(self):
        choices = [terminal.Choice(str(i), f"Call {i}", "Salvate" if i < 3 else "Cestino") for i in range(40)]
        selection = terminal.Selection(choices, ("Salvate", "Cestino"))
        with patch.object(terminal, "console", Console(file=StringIO(), height=24)):
            self.assertIn("Salvate", selection.render().plain)
            self.assertIn("Cestino", selection.render().plain)
            for _ in range(39):
                selection.handle("down")
            rendered = selection.render().plain
        self.assertIn("› Call 39", rendered)
        self.assertIn("↑ Altri elementi", rendered)
        self.assertNotIn("Call 0\n", rendered)

    def test_title_is_prefilled_and_enter_confirms_visible_value(self):
        with patch.object(terminal, "console", Console(file=StringIO())), \
                patch.object(terminal, "_keyboard_mode", return_value=nullcontext()), \
                patch.object(terminal, "_read_character", return_value="\r"):
            self.assertEqual(terminal.input_line("Titolo", initial="Originale"), "Originale")

    def test_read_key_normalizes_enter_and_escape_without_extra_bindings(self):
        for raw, expected in (("\r", "enter"), ("\x1b", "escape"), ("N", "n"), ("up", "up")):
            with self.subTest(raw=raw), patch.object(terminal, "_read_character", return_value=raw):
                self.assertEqual(terminal.read_key(), expected)

    @unittest.skipUnless(os.name == "nt", "Sequenze dei tasti specifiche di Windows")
    def test_windows_arrow_keys_are_decoded_for_selection(self):
        for raw, expected in (("H", "up"), ("P", "down"), ("K", "left"), ("M", "right")):
            with self.subTest(raw=raw), patch("msvcrt.kbhit", return_value=True), \
                    patch("msvcrt.getwch", side_effect=["\xe0", raw]):
                self.assertEqual(terminal.read_key(), expected)

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
