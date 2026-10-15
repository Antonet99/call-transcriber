"""Shared terminal presentation for the watcher and processing pipeline."""
from contextlib import contextmanager
import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()


def panel(title: str, message: str, color: str = "cyan") -> None:
    console.print(Panel(Text(message), title=Text(title, style="bold"), border_style=color, padding=(1, 2)))


@contextmanager
def phase(label: str):
    started = time.monotonic()
    with console.status(Text(label, style="cyan"), spinner="dots"):
        yield
    console.print(Text(f"  ✓ {label}  ·  {time.monotonic() - started:.0f}s", style="green"))
