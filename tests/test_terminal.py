from contextlib import nullcontext
from io import StringIO
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from rich.console import Console
from rich.text import Text

from scripts import terminal, watch_calls


class TerminalTests(unittest.TestCase):
    def test_menu_message_and_long_choices_wrap_without_losing_text(self):
        message = ("Elaborazione sospesa prima di Whisper/Claude sulla registrazione. "
                   "Procedere comunque accetta il rischio di errore e consumo; vale solo per questa esecuzione.")
        label = "Procedi comunque per questa registrazione"
        for width in (40, 60, 100):
            with self.subTest(width=width):
                output = StringIO()
                console = Console(file=output, width=width, height=40)
                screen = terminal._Screen()
                with patch.object(terminal, "console", console), patch.object(terminal, "_screen", screen):
                    selection = terminal.Selection([terminal.Choice("proceed", label)])
                    terminal.panel("Configurazione", selection.render(message))
                    console.print(screen.render())
                rendered = output.getvalue()
                normalized = " ".join(rendered.replace("│", " ").split())
                self.assertIn(message, normalized)
                self.assertIn(label, normalized)
                self.assertNotIn("…", rendered)
                self.assertTrue(all(len(line) <= width for line in rendered.splitlines()))

    def test_resize_reflows_menu_and_preserves_selection(self):
        console = Console(file=StringIO(), width=100, height=35)
        selection = terminal.Selection([terminal.Choice("first", "Primo elemento"),
                                        terminal.Choice("last", "Una voce abbastanza lunga da richiedere piu righe nel terminale stretto")])
        selection.handle("down")
        with patch.object(terminal, "console", console):
            wide = selection.render()
            console.width = 35
            narrow = selection.render()
            console.width = 100
            restored = selection.render()
        self.assertGreater(narrow.plain.count("\n"), wide.plain.count("\n"))
        self.assertEqual(restored.plain, wide.plain)
        self.assertEqual(selection.handle("enter"), "last")

    def test_wrapped_rows_scroll_with_last_selected_choice_visible(self):
        choices = [terminal.Choice(i, f"Registrazione {i} con un titolo lungo da visualizzare su piu righe") for i in range(20)]
        selection = terminal.Selection(choices)
        for _ in range(19):
            selection.handle("down")
        output = StringIO()
        console = Console(file=output, width=40, height=24)
        screen = terminal._Screen()
        with patch.object(terminal, "console", console), patch.object(terminal, "_screen", screen):
            terminal.panel("Registrazioni", selection.render("Seleziona la registrazione."))
            console.print(screen.render())
        rendered = output.getvalue()
        self.assertIn("› Registrazione 19", rendered)
        self.assertIn("↑ Altri elementi", rendered)
        self.assertIn("Invio", rendered)
        self.assertLessEqual(len(rendered.splitlines()), 24)

    def test_long_notice_wraps_and_leaves_space_for_footer(self):
        output = StringIO()
        console = Console(file=output, width=40, height=24)
        screen = terminal._Screen()
        screen.notice = "Registrazione salvata nella cartella del progetto selezionato correttamente."
        with patch.object(terminal, "console", console):
            console.print(screen.render())
        normalized = " ".join(output.getvalue().split())
        self.assertIn(screen.notice, normalized)
        self.assertIn("Ctrl+C", normalized)
        self.assertLessEqual(len(output.getvalue().splitlines()), 24)

    def test_completed_phase_uses_portable_badge_and_distinct_colors(self):
        screen = terminal._Screen()
        with patch.object(terminal, "_screen", screen), patch.object(terminal, "status", return_value=nullcontext()), \
                patch.object(terminal.time, "monotonic", side_effect=[100, 102]):
            with terminal.phase("Trascrizione"):
                pass
        step = screen.steps[0]
        self.assertEqual(step.plain, "  [OK] Trascrizione  |  2s")
        console = Console(file=StringIO(), color_system="truecolor")
        colors = {segment.text: segment.style.color.get_truecolor().hex for segment in console.render(step)
                  if segment.style and segment.style.color}
        self.assertNotEqual(colors["  [OK] "], colors["Trascrizione"])
        self.assertNotEqual(colors["Trascrizione"], colors["2s"])

    def test_result_notice_expires_after_ten_seconds_without_sleeping(self):
        screen = terminal._Screen()
        output = StringIO()
        with patch.object(terminal, "_screen", screen), patch.object(terminal.time, "monotonic", return_value=100) as clock:
            terminal.panel("Call completata", "Call sintetica", "green")
            terminal.panel("Home", "Menu principale")
            clock.return_value = 109.9
            Console(file=output, width=100).print(screen.render())
            self.assertIn("Call completata · Call sintetica", output.getvalue())
            clock.return_value = 110
            output = StringIO()
            Console(file=output, width=100).print(screen.render())
            self.assertNotIn("Call completata", output.getvalue())
            self.assertEqual(screen.notice, "")

    def test_completion_keeps_steps_until_enter_and_starts_notice_timer_afterwards(self):
        screen = terminal._Screen()
        screen.steps = ["✓ Trascrizione", "✓ Riassunto"]
        keys = iter([None, "escape", "q", "enter"])

        def read_key():
            self.assertTrue(screen.confirming)
            self.assertIsNone(screen.notice_expires_at)
            output = StringIO()
            Console(file=output, width=120).print(screen.render())
            self.assertIn("Premi Invio per tornare alla home", output.getvalue())
            self.assertIn("✓ Trascrizione", output.getvalue())
            return next(keys)

        with patch.object(terminal, "_screen", screen), patch.object(terminal.time, "monotonic", return_value=100) as clock, \
                patch.object(terminal, "clear_input") as clear, patch.object(terminal, "read_key", side_effect=read_key) as read:
            terminal.panel("Call completata", "Call sintetica", "green")
            clock.return_value = 200
            terminal.confirm_completion()
        clear.assert_called_once()
        self.assertEqual(read.call_count, 4)
        self.assertFalse(screen.confirming)
        self.assertEqual(screen.notice_expires_at, 210)
        self.assertEqual(screen.steps, ["✓ Trascrizione", "✓ Riassunto"])
        self.assertIn("Call completata", screen.notice)

    def test_completion_interrupt_restores_confirmation_state(self):
        screen = terminal._Screen()
        with patch.object(terminal, "_screen", screen), patch.object(terminal, "clear_input"), \
                patch.object(terminal, "read_key", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                terminal.confirm_completion()
        self.assertFalse(screen.confirming)

    def test_styled_choices_preserve_literal_labels_and_readable_selection(self):
        label = Text("[Review] · Progetto Alpha", style="bold #f8fafc")
        selection = terminal.Selection([terminal.Choice("call", label, "Salvate", style="#86efac")])
        console = Console(file=StringIO(), force_terminal=True, color_system="truecolor", width=100)
        rendered = selection.render(Text("Seleziona una call", style="#c4b5fd"))
        self.assertIn("[Review] · Progetto Alpha", rendered.plain)
        self.assertEqual(selection.handle("enter"), "call")
        styles = [segment.style for segment in console.render(rendered) if "[Review]" in segment.text]
        self.assertTrue(styles)
        self.assertTrue(all(style.bgcolor is not None and style.color.get_truecolor().hex == "#0f172a" for style in styles))

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
