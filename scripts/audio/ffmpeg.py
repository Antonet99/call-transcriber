from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

_BITRATES = [128, 64, 48, 32, 24]


def _run(*args: str) -> str:
    result = subprocess.run(list(args), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{args[0]} errore (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def _require(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"{name} non trovato nel PATH.")
    return path


def get_duration(path: Path) -> float:
    ffprobe = _require("ffprobe")
    raw = _run(
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    )
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"Impossibile leggere la durata di {path}: '{raw}'")


def extract_audio(src: Path, dst: Path) -> None:
    ffmpeg = _require("ffmpeg")
    _run(ffmpeg, "-hide_banner", "-y", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "128k",
         str(dst))


def convert_to_m4a(src: Path, dst: Path) -> None:
    ffmpeg = _require("ffmpeg")
    _run(ffmpeg, "-hide_banner", "-y", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "128k",
         str(dst))


def compress_audio(src: Path, dst: Path, max_mb: float, duration: float) -> None:
    max_bytes = int(max_mb * 1_000_000)

    if src.stat().st_size <= max_bytes:
        shutil.copy2(src, dst)
        return

    ffmpeg = _require("ffmpeg")
    target_kbps = math.floor((max_bytes * 8 / duration / 1000) * 0.92)
    bitrates = sorted(set([min(128, max(32, int(target_kbps)))] + _BITRATES), reverse=True)

    for bitrate in bitrates:
        tmp = dst.with_name(f"audio_compresso_tmp_{bitrate}k.m4a")
        try:
            _run(ffmpeg, "-hide_banner", "-y", "-i", str(src),
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", f"-b:a", f"{bitrate}k",
                 str(tmp))
        except RuntimeError:
            if tmp.exists():
                tmp.unlink()
            continue

        if tmp.stat().st_size <= max_bytes:
            tmp.replace(dst)
            return
        tmp.unlink()

    raise RuntimeError(f"Impossibile comprimere audio sotto {max_mb} MB.")


def segment_audio(src: Path, out_dir: Path, chunk_seconds: int) -> list[Path]:
    ffmpeg = _require("ffmpeg")
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "chunk_%03d.m4a")
    _run(ffmpeg, "-hide_banner", "-y", "-i", str(src),
         "-f", "segment", "-segment_time", str(chunk_seconds),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "128k",
         pattern)
    return sorted(out_dir.glob("chunk_*.m4a"))
