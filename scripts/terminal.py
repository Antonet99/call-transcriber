"""Shared terminal presentation for the watcher and processing pipeline."""
from contextlib import contextmanager
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
        content.append(Text(self.message))
        content.extend(Text(step, style="green") for step in self.steps)
        if self.spinner is not None:
            content.append(self.spinner)
        return Group(
            Rule(Text("Call Transcriber", style="bold cyan"), style="cyan"),
            Text(""),
            Panel(Group(*content), title=Text(self.title, style="bold"),
                  border_style=self.color, padding=(1, 2), height=max(6, console.height - 6)),
            Text(self.notice, style="green", overflow="ellipsis", no_wrap=True),
            Text("Esc per tornare indietro · Ctrl+C per uscire", style="dim"),
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
                    msvcrt.getwch()
                    return None
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
            while select.select([sys.stdin], [], [], 0.02)[0]:
                if os.read(sys.stdin.fileno(), 1).isalpha():
                    break
            return None
    while True:
        try:
            return raw.decode(sys.stdin.encoding or "utf-8")
        except UnicodeDecodeError:
            if len(raw) >= 4:
                return None
            raw += os.read(sys.stdin.fileno(), 1)


def read_key(timeout: float = 0.2) -> str | None:
    value = _read_character(timeout)
    return value.lower() if value is not None else None


def input_line(label: str) -> str | None:
    value = ""
    with _keyboard_mode():
        try:
            if _screen is None:
                console.print(Text(label), end=" ")
            while True:
                if _screen is not None:
                    _screen.prompt = f"{label}\n> {value}▌"
                character = _read_character()
                if character is None:
                    continue
                if character == "\x1b":
                    return None
                if character in {"\r", "\n"}:
                    return value
                if character in {"\b", "\x7f"}:
                    if value:
                        value = value[:-1]
                        if _screen is None:
                            console.print("\b \b", end="")
                elif character.isprintable():
                    value += character
                    if _screen is None:
                        console.print(Text(character), end="", highlight=False)
        finally:
            if _screen is not None:
                _screen.prompt = ""
            else:
                console.print()


def panel(title: str, message: str, color: str = "cyan") -> None:
    if _screen is not None:
        _screen.title = title
        _screen.message = message
        _screen.color = color
        _screen.steps = []
        _screen.prompt = ""
        if color == "green":
            _screen.notice = f"{title} · {message.replace(chr(10), ' ')}"
        return
    console.print(Panel(Text(message), title=Text(title, style="bold"), border_style=color, padding=(1, 2)))


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
