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


class _Screen:
    def __init__(self):
        self.title = "In ascolto"
        self.message = ""
        self.color = "cyan"
        self.steps = []
        self.spinner = None
        self.prompt = ""
        self.notice = ""

    def render(self):
        content = []
        if self.prompt:
            content.extend([Text(self.prompt, style="cyan"), Text("")])
        content.append(self.message if isinstance(self.message, Text) else Text(self.message))
        content.extend(Text(step, style="green") for step in self.steps)
        if self.spinner is not None:
            content.append(self.spinner)
        return Group(
            Rule(Text("Call Transcriber", style="bold cyan"), style="cyan"),
            Text(""),
            Panel(Group(*content), title=Text(self.title, style="bold"),
                  border_style=self.color, padding=(1, 2), height=max(6, console.height - 6)),
            Text(self.notice, style="green", overflow="ellipsis", no_wrap=True),
            Text("↑/↓ Seleziona · Invio Conferma · Esc Indietro · Ctrl+C Esci", style="dim"),
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
                console.print(Text(label), end=" ")
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
        _screen.steps = []
        _screen.prompt = ""
        if color == "green":
            _screen.notice = f"{title} · {str(message).replace(chr(10), ' ')}"
        return
    console.print(Panel(message if isinstance(message, Text) else Text(message), title=Text(title, style="bold"), border_style=color, padding=(1, 2)))


@contextmanager
def status(label: str):
    if _screen is None:
        with console.status(Text(label, style="cyan"), spinner="dots"):
            yield
        return
    _screen.spinner = Spinner("dots", text=Text(label, style="cyan"))
    try:
        yield
    finally:
        _screen.spinner = None


@contextmanager
def phase(label: str):
    started = time.monotonic()
    with status(label):
        yield
    completed = f"  ✓ {label}  ·  {time.monotonic() - started:.0f}s"
    if _screen is not None:
        _screen.steps.append(completed)
    else:
        console.print(Text(completed, style="green"))


@dataclass(frozen=True)
class Choice:
    value: Any
    label: str
    group: str = ""
    enabled: bool = True


class Selection:
    def __init__(self, choices: list[Choice], groups: tuple[str, ...] = ()):
        self.choices = choices
        self.groups = groups
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

    def render(self, message: str = "") -> Text:
        rows = []
        selected_row = 0
        groups = self.groups or tuple(dict.fromkeys(choice.group for choice in self.choices)) or ("",)
        for group in groups:
            if group:
                rows.append((group, "bold cyan"))
            entries = [(i, choice) for i, choice in enumerate(self.choices) if choice.group == group]
            if not entries:
                rows.append(("  Nessuna registrazione disponibile.", "dim"))
            for i, choice in entries:
                if i == self.index:
                    selected_row = len(rows)
                style = "bold black on cyan" if i == self.index else ("" if choice.enabled else "dim")
                rows.append((f"{'›' if i == self.index else ' '} {choice.label}", style))
            if group:
                rows.append(("", ""))
        height = max(3, console.height - 13 - message.count("\n"))
        start = max(0, min(selected_row - height // 2, len(rows) - height))
        text = Text(no_wrap=True, overflow="ellipsis")
        if message:
            text.append(message + "\n\n")
        if start:
            text.append("  ↑ Altri elementi\n", style="dim")
        for label, style in rows[start:start + height]:
            text.append(label + "\n", style=style)
        if start + height < len(rows):
            text.append("  ↓ Altri elementi", style="dim")
        return text


def select_item(title: str, choices, *, message: str = "",
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
