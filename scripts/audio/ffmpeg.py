from __future__ import annotations

import math
import shutil
from pathlib import Path

from scripts.subprocesses import run_command

_BITRATES = (128, 96, 64, 48, 32, 24, 16, 12, 8)
_DEFAULT_TIMEOUT_SECONDS = 600.0
_DEFAULT_SEGMENT_BITRATE_KBPS = 128


def _timeout_seconds() -> float:
    # settings.py può fornire un valore specifico senza rendere questo
    # modulo dipendente dalla configurazione durante i test isolati.
    try:
        import scripts.settings as _cfg

        value = getattr(_cfg, "FFMPEG_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)
    except (ImportError, AttributeError):
        value = _DEFAULT_TIMEOUT_SECONDS
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return _DEFAULT_TIMEOUT_SECONDS
    return float(value)


def _run(*args: str, timeout: float | None = None) -> str:
    result = run_command(args, timeout=timeout or _timeout_seconds())
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
        duration = float(raw)
    except (TypeError, ValueError):
        raise RuntimeError(f"Impossibile leggere la durata di {path}: '{raw}'")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Durata non valida per {path}: {duration!r}")
    return duration


def _encode_to_m4a(src: Path, dst: Path) -> None:
    ffmpeg = _require("ffmpeg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(dst),
    )


def extract_audio(src: Path, dst: Path) -> None:
    _encode_to_m4a(src, dst)


def convert_to_m4a(src: Path, dst: Path) -> None:
    if src.suffix.casefold() == ".m4a":
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    _encode_to_m4a(src, dst)


def _validate_compression(max_mb: float, duration: float) -> tuple[int, float]:
    if not isinstance(max_mb, (int, float)) or not math.isfinite(max_mb) or max_mb <= 0:
        raise ValueError(f"max_mb deve essere finito e maggiore di zero: {max_mb!r}")
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"duration deve essere finita e maggiore di zero: {duration!r}")
    max_bytes = int(max_mb * 1_000_000)
    if max_bytes <= 0:
        raise ValueError(f"max_mb troppo piccolo: {max_mb!r}")
    return max_bytes, float(duration)


def _candidate_bitrates(max_bytes: int, duration: float) -> list[int]:
    target = math.floor((max_bytes * 8 / duration / 1000) * 0.92)
    target = min(max(_BITRATES[-1], target), _BITRATES[0])
    # Il target calcolato viene provato per primo; le prove successive
    # possono soltanto ridurre il bitrate e sono limitate alla tabella nota.
    lower = sorted({rate for rate in _BITRATES if rate < target}, reverse=True)
    return [target, *lower]


def compress_audio(src: Path, dst: Path, max_mb: float, duration: float) -> None:
    max_bytes, duration = _validate_compression(max_mb, duration)
    if not src.is_file():
        raise FileNotFoundError(src)

    if src.stat().st_size <= max_bytes:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return

    ffmpeg = _require("ffmpeg")
    bitrates = _candidate_bitrates(max_bytes, duration)
    dst.parent.mkdir(parents=True, exist_ok=True)
    attempted: list[int] = []

    for bitrate in bitrates:
        attempted.append(bitrate)
        tmp = dst.with_name(f"audio_compresso_tmp_{bitrate}k.m4a")
        try:
            _run(ffmpeg, "-hide_banner", "-y", "-i", str(src),
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", f"-b:a", f"{bitrate}k",
                 str(tmp))
        except RuntimeError:
            if tmp.exists():
                tmp.unlink()
            continue

        if tmp.is_file() and tmp.stat().st_size <= max_bytes:
            tmp.replace(dst)
            return
        tmp.unlink(missing_ok=True)

    raise RuntimeError(
        f"Impossibile comprimere audio sotto {max_mb} MB "
        f"(bitrate provati: {', '.join(f'{rate}k' for rate in attempted)})."
    )


def estimate_chunk_seconds(target_bytes: int, bitrate_kbps: int, safety: float = 0.88) -> int:
    """Calcola chunk brevi abbastanza per il bitrate prodotto dal codec."""
    if target_bytes <= 0 or bitrate_kbps <= 0:
        raise ValueError("target_bytes e bitrate_kbps devono essere maggiori di zero")
    if not math.isfinite(safety) or safety <= 0 or safety > 1:
        raise ValueError("safety deve essere finito e nell'intervallo (0, 1]")
    seconds = target_bytes * 8 * safety / (bitrate_kbps * 1000)
    return max(60, math.floor(seconds))


def segment_audio(
    src: Path,
    out_dir: Path,
    chunk_seconds: int,
    bitrate_kbps: int = _DEFAULT_SEGMENT_BITRATE_KBPS,
) -> list[Path]:
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds deve essere maggiore di zero")
    if bitrate_kbps <= 0:
        raise ValueError("bitrate_kbps deve essere maggiore di zero")
    ffmpeg = _require("ffmpeg")
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "chunk_%03d.m4a")
    _run(ffmpeg, "-hide_banner", "-y", "-i", str(src),
         "-f", "segment", "-segment_time", str(chunk_seconds),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", f"{bitrate_kbps}k",
         pattern)
    return sorted(out_dir.glob("chunk_*.m4a"))
