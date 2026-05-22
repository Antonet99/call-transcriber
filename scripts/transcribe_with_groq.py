"""Trascrizione audio via Groq Whisper con chunking automatico per file > 19 MB."""
from __future__ import annotations

import math
import os
import shutil
import tempfile
import argparse
from pathlib import Path

from groq import Groq

from scripts.audio.ffmpeg import compress_audio, get_duration, segment_audio

_MAX_MB: float = 19.0
_CHUNK_TARGET_MB: float = 18.0
_DEFAULT_MODEL = "whisper-large-v3-turbo"
_UTF8 = "utf-8"


def _transcribe_single(client: Groq, path: Path, model: str) -> str:
    with open(path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(path.name, f.read()),
            model=model,
            response_format="text",
            temperature=0.0,
        )
    return str(result).strip()


def _compress_if_needed(src: Path, tmp_dir: Path, max_mb: float) -> Path:
    if src.stat().st_size <= int(max_mb * 1_000_000):
        return src
    duration = get_duration(src)
    dst = tmp_dir / ("compressed_" + src.name)
    compress_audio(src, dst, max_mb, duration)
    return dst


def transcribe(
    audio_path: Path,
    output_path: Path,
    model: str = _DEFAULT_MODEL,
    max_mb: float = _MAX_MB,
    chunk_target_mb: float = _CHUNK_TARGET_MB,
) -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY non impostata.")

    client = Groq(api_key=api_key)
    max_bytes = int(max_mb * 1_000_000)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if audio_path.stat().st_size <= max_bytes:
        text = _transcribe_single(client, audio_path, model)
        output_path.write_text(text, encoding=_UTF8)
        return text

    duration = get_duration(audio_path)
    target_bytes = int(chunk_target_mb * 1_000_000)
    chunk_count = max(2, math.ceil(audio_path.stat().st_size / target_bytes))
    chunk_seconds = max(60, math.ceil(duration / chunk_count))

    tmp_dir = Path(tempfile.mkdtemp(prefix="groq_chunks_"))
    try:
        chunks = segment_audio(audio_path, tmp_dir / "_chunks", chunk_seconds)
        parts: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            compressed = _compress_if_needed(chunk, tmp_dir, max_mb)
            part_text = _transcribe_single(client, compressed, model)
            parts.append(f"[PARTE {i}]\n\n{part_text}")
        text = "\n\n".join(parts)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    output_path.write_text(text, encoding=_UTF8)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Trascrivi audio con Groq Whisper.")
    parser.add_argument("--audio-path", required=True, type=Path)
    parser.add_argument("--output-path", type=Path, default=None)
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    args = parser.parse_args()

    out = args.output_path or args.audio_path.parent / "trascrizione.txt"
    result = transcribe(args.audio_path, out, model=args.model)
    print(f"Trascrizione salvata in: {out} ({len(result)} caratteri)")


if __name__ == "__main__":
    main()
