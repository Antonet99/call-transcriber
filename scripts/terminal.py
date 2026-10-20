"""Shared terminal presentation for the watcher and processing pipeline."""
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
import os
import sys
import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.text import Text

console = Console()
_screen = None
_TEXT = "#e2e8f0"
_COLORS = {"cyan": "#67e8f9", "green": "#86efac", "yellow": "#fbbf24", "red": "#fb7185"}


def _footer(confirming: bool = False) -> Text:
    if confirming:
        return Text.assemble(
            (" Invio ", "bold #86efac on #14532d"), (" Torna alla home  ", _TEXT),
            (" Ctrl+C ", "bold #fda4af on #881337"), (" Esci", _TEXT),
        )
    return Text.assemble(
        (" ↑/↓ ", "bold #67e8f9 on #164e63"), (" Seleziona  ", _TEXT),
        (" Invio ", "bold #86efac on #14532d"), (" Conferma  ", _TEXT),
        (" Esc ", "bold #fcd34d on #713f12"), (" Indietro  ", _TEXT),
        (" Ctrl+C ", "bold #fda4af on #881337"), (" Esci", _TEXT),
    )


def _panel_height(confirming: bool = False, notice: str = "") -> int:
    width = max(1, console.width)
    reserved = len(_footer(confirming).wrap(console, width))
    reserved += len(Text(notice, no_wrap=False, overflow="fold").wrap(console, width))
    return max(6, console.height - 4 - reserved)


class _Screen:
    def __init__(self):
        self.title = "In ascolto"
        self.message = ""
        self.color = "cyan"
        self.steps = []
        self.spinner = None
        self.prompt = ""
        self.notice = ""
        self.notice_expires_at = None
        self.confirming = False

    def render(self):
        if self.notice_expires_at is not None and time.monotonic() >= self.notice_expires_at:
            self.notice = ""
            self.notice_expires_at = None
        content = []
        if self.prompt:
            content.extend([Text(self.prompt, style="bold #c4b5fd"), Text("")])
        content.append(self.message if isinstance(self.message, Text) else Text(self.message, style=_TEXT))
        if self.steps:
            content.append(Text(""))
        content.extend(step if isinstance(step, Text) else Text(step, style=_TEXT) for step in self.steps)
        if self.spinner is not None:
            content.append(self.spinner)
        if self.confirming:
            content.extend([Text(""), Text.assemble(
                ("Riassunto completato.", "bold #86efac"), (" Premi ", _TEXT),
                ("Invio", "bold #67e8f9"), (" per tornare alla home.", _TEXT),
            )])
        footer = _footer(self.confirming)
        notice = Text("" if self.confirming else self.notice, style="bold #86efac", overflow="fold", no_wrap=False)
        return Group(
            Rule(Text("Call Transcriber", style="bold #67e8f9"), style="#38bdf8"),
            Text(""),
            Panel(Group(*content), title=Text(self.title, style=f"bold {_COLORS.get(self.color, self.color)}"),
                  border_style=_COLORS.get(self.color, self.color), padding=(1, 2),
                  height=_panel_height(self.confirming, notice.plain)),
            notice,
            footer,
        )


@contextmanager
def _keyboard_mode():
    if os.name == "nt" or not sys.stdin.isatty():
        yield
        return
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        yield
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)


@contextmanager
def screen_session():
    global _screen
    state = _Screen()
    _screen = state
    try:
        with _keyboard_mode(), Live(console=console, screen=True, refresh_per_second=8,
                                    get_renderable=state.render):
            yield
    finally:
        _screen = None


def interactive() -> bool:
    return bool(sys.stdin and sys.stdin.isatty() and console.is_terminal)


def clear_input() -> None:
    if os.name == "nt":
        import msvcrt

        while msvcrt.kbhit():
            msvcrt.getwch()
    elif sys.stdin.isatty():
        import termios

        termios.tcflush(sys.stdin, termios.TCIFLUSH)


def _read_character(timeout: float = 0.2) -> str | None:
    if os.name == "nt":
        import msvcrt

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                value = msvcrt.getwch()
                if value == "\x03":
                    raise KeyboardInterrupt
                if value in {"\x00", "\xe0"}:
                    return {"H": "up", "P": "down", "K": "left", "M": "right", "S": "delete", "G": "home", "O": "end"}.get(msvcrt.getwch())
                return value
            time.sleep(0.02)
        return None
    import select

    if not select.select([sys.stdin], [], [], timeout)[0]:
        return None
    raw = os.read(sys.stdin.fileno(), 1)
    if not raw:
        raise EOFError
    if raw == b"\x1b" and select.select([sys.stdin], [], [], 0.02)[0]:
        following = os.read(sys.stdin.fileno(), 1)
        if following in {b"[", b"O"}:
            sequence = b""
            while select.select([sys.stdin], [], [], 0.02)[0]:
                part = os.read(sys.stdin.fileno(), 1)
                sequence += part
                if part.isalpha() or part == b"~":
                    break
            return {b"A": "up", b"B": "down", b"C": "right", b"D": "left",
                    b"H": "home", b"F": "end", b"3~": "delete"}.get(sequence)
    while True:
        try:
            return raw.decode(sys.stdin.encoding or "utf-8")
        except UnicodeDecodeError:
            if len(raw) >= 4:
                return None
            raw += os.read(sys.stdin.fileno(), 1)


def read_key(timeout: float = 0.2) -> str | None:
    value = _read_character(timeout)
    if value in {"\r", "\n"}:
        return "enter"
    if value == "\x1b":
        return "escape"
    return value.lower() if value is not None else None


def input_line(label: str, initial: str = "") -> str | None:
    value = initial
    cursor = len(value)
    with _keyboard_mode():
        try:
            if _screen is None:
                console.print(Text(label, style="bold #c4b5fd"), end=" ")
            while True:
                if _screen is not None:
                    _screen.prompt = f"{label}\n> {value[:cursor]}▌{value[cursor:]}"
                character = _read_character()
                if character is None:
                    continue
                if character == "\x1b":
                    return None
                if character in {"\r", "\n"}:
                    return value
                if character in {"\b", "\x7f"}:
                    if cursor:
                        value = value[:cursor - 1] + value[cursor:]
                        cursor -= 1
                        if _screen is None:
                            console.print("\b \b", end="")
                elif character == "left":
                    cursor = max(0, cursor - 1)
                elif character == "right":
                    cursor = min(len(value), cursor + 1)
                elif character == "home":
                    cursor = 0
                elif character == "end":
                    cursor = len(value)
                elif character == "delete":
                    value = value[:cursor] + value[cursor + 1:]
                elif character not in {"up", "down"} and character.isprintable():
                    value = value[:cursor] + character + value[cursor:]
                    cursor += len(character)
                    if _screen is None:
                        console.print(Text(character), end="", highlight=False)
        finally:
            if _screen is not None:
                _screen.prompt = ""
            else:
                console.print()


def panel(title: str, message: str | Text, color: str = "cyan") -> None:
    if _screen is not None:
        _screen.title = title
        _screen.message = message
        _screen.color = color
        if color != "green":
            _screen.steps = []
        _screen.prompt = ""
        if color == "green":
            _screen.notice = f"{title} · {str(message).replace(chr(10), ' ')}"
            _screen.notice_expires_at = time.monotonic() + 10
        return
    accent = _COLORS.get(color, color)
    console.print(Panel(message if isinstance(message, Text) else Text(message, style=_TEXT),
                        title=Text(title, style=f"bold {accent}"), border_style=accent, padding=(1, 2)))


def completion_details(title: str, project: str, destination: str) -> Text:
    return Text.assemble(
        (title, "bold #f8fafc"), ("\n\nProgetto: ", _TEXT),
        (project, "bold #c4b5fd"), ("\nSalvata in: ", _TEXT),
        (destination, "#93c5fd"),
    )


def confirm_completion() -> None:
    if _screen is None:
        return
    clear_input()
    _screen.confirming = True
    _screen.notice_expires_at = None
    try:
        while read_key() != "enter":
            pass
    finally:
        _screen.confirming = False
        _screen.notice_expires_at = time.monotonic() + 10


@contextmanager
def status(label: str):
    if _screen is None:
        with console.status(Text(label, style="bold #fbbf24"), spinner="dots", spinner_style="#c4b5fd"):
            yield
        return
    _screen.spinner = Spinner("dots", text=Text(label, style="bold #fbbf24"), style="#c4b5fd")
    try:
        yield
    finally:
        _screen.spinner = None


@contextmanager
def phase(label: str):
    started = time.monotonic()
    with status(label):
        yield
    completed = Text.assemble(
        ("  [OK] ", "bold #86efac"), (label, _TEXT),
        ("  |  ", "#64748b"), (f"{time.monotonic() - started:.0f}s", "#93c5fd"),
    )
    if _screen is not None:
        _screen.steps.append(completed)
    else:
        console.print(completed)


@dataclass(frozen=True)
class Choice:
    value: Any
    label: str | Text
    group: str = ""
    enabled: bool = True
    style: str = _TEXT


class Selection:
    def __init__(self, choices: list[Choice], groups: tuple[str, ...] = (), *, separate_groups: bool = False):
        self.choices = choices
        self.groups = groups
        self.separate_groups = separate_groups
        self.index = next((i for i, choice in enumerate(choices) if choice.enabled), -1)

    def update(self, choices: list[Choice]) -> None:
        current = self.choices[self.index].value if self.index >= 0 else None
        self.choices = choices
        self.index = next((i for i, choice in enumerate(choices) if choice.enabled and choice.value == current),
                          next((i for i, choice in enumerate(choices) if choice.enabled), -1))

    def handle(self, key: str | None):
        enabled = [i for i, choice in enumerate(self.choices) if choice.enabled]
        if key in {"up", "down"} and enabled:
            position = enabled.index(self.index) if self.index in enabled else 0
            self.index = enabled[max(0, min(len(enabled) - 1, position + (1 if key == "down" else -1)))]
        if key == "enter" and self.index >= 0:
            return self.choices[self.index].value
        return None

    def render(self, message: str | Text = "") -> Text:
        rows = []
        selected_row = 0
        width = max(1, console.width - 6)
        groups = self.groups or tuple(dict.fromkeys(choice.group for choice in self.choices)) or ("",)
        for group in groups:
            if rows and self.separate_groups:
                rows.append(Text("─" * width, style="#64748b"))
                rows.append(Text())
            if group:
                accent = next((choice.style for choice in self.choices if choice.group == group), "#67e8f9")
                rows.extend(Text(group, style=f"bold {accent}").wrap(console, width, overflow="fold", no_wrap=False))
            entries = [(i, choice) for i, choice in enumerate(self.choices) if choice.group == group]
            if not entries:
                rows.extend(Text("  Nessuna registrazione disponibile.", style="#94a3b8").wrap(console, width, overflow="fold", no_wrap=False))
            for i, choice in entries:
                if i == self.index:
                    selected_row = len(rows)
                style = "bold #0f172a on #67e8f9" if i == self.index else (choice.style if choice.enabled else "#94a3b8")
                row = Text(f"{'›' if i == self.index else ' '} ", style=style)
                label = choice.label
                if i == self.index:
                    label = label.plain if isinstance(label, Text) else label
                row.append(label)
                rows.extend(row.wrap(console, width, overflow="fold", no_wrap=False))
            if group and not self.separate_groups:
                rows.append(Text())
        message = message if isinstance(message, Text) else Text(message, style=_TEXT)
        message_height = len(message.wrap(console, width, overflow="fold", no_wrap=False)) if message else 0
        panel_height = _panel_height(_screen.confirming, _screen.notice) if _screen else _panel_height()
        height = max(1, panel_height - 7 - message_height - (2 if message else 0))
        start = max(0, min(selected_row - height // 2, len(rows) - height))
        text = Text(no_wrap=False, overflow="fold")
        if message:
            text.append(message)
            text.append("\n\n")
        if start:
            text.append("  ↑ Altri elementi\n", style="#67e8f9")
        for row in rows[start:start + height]:
            text.append(row)
            text.append("\n")
        if start + height < len(rows):
            text.append("  ↓ Altri elementi", style="#67e8f9")
        return text


def select_item(title: str, choices, *, message: str | Text = "",
                groups: tuple[str, ...] = ()):
    clear_input()
    selection = Selection(choices() if callable(choices) else choices, groups)
    rendered = None
    while True:
        if callable(choices):
            selection.update(choices())
        display = (selection.index, console.size, selection.choices)
        if rendered != display:
            panel(title, selection.render(message))
            rendered = display
        key = read_key()
        if key == "escape":
            return None
        value = selection.handle(key)
        if key == "enter" and value is not None:
            return value
