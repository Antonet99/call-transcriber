"""Small, shared filesystem helpers used by artifact writers."""
from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path


_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$",
    re.IGNORECASE,
)
_ATOMIC_WRITE_RETRIES = 6
_ATOMIC_WRITE_DELAY_SECONDS = 0.25


def safe_name(name: str) -> str:
    """Return a usable Windows path component while preserving its label.

    Invalid/control characters are replaced with ``-`` and trailing spaces or
    dots are removed.  Reserved device names receive a leading underscore.
    The component is bounded so generated paths remain manageable.
    """
    value = _INVALID_NAME.sub("-", str(name))
    value = re.sub(r"\s+", " ", value).strip().rstrip(" .")
    if not value or value in {".", ".."}:
        value = "_"
    if _RESERVED_NAME.fullmatch(value):
        value = f"_{value}"
    return value[:120].rstrip(" .") or "_"


def unique_path(path: Path) -> Path:
    """Return ``path`` or the next collision-free ``(n)`` sibling.

    The suffix is kept on the filename, so ``video.mp4`` becomes
    ``video (2).mp4``.  The helper works for both files and directories.
    """
    path = Path(path)
    if not path.exists():
        return path
    suffix = "".join(path.suffixes)
    stem = path.name[:-len(suffix)] if suffix else path.name
    index = 2
    while True:
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def atomic_write_text(
    path: Path,
    text: str,
    encoding: str = "utf-8",
    *,
    retries: int = _ATOMIC_WRITE_RETRIES,
    retry_delay_seconds: float = _ATOMIC_WRITE_DELAY_SECONDS,
) -> None:
    """Write text through a sibling temporary file and atomically replace it."""
    path = Path(path)
    try:
        if path.read_text(encoding=encoding) == text:
            return
    except (OSError, UnicodeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        attempts = max(1, retries)
        for attempt in range(attempts):
            try:
                os.replace(temp_name, path)
                temp_name = None
                break
            except PermissionError:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(max(0.0, retry_delay_seconds) * (attempt + 1))
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink()
            except OSError:
                pass
