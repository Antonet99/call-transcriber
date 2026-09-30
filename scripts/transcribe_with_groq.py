"""Trascrizione audio via Groq Whisper con chunking automatico per file > soglia MB."""
from __future__ import annotations

import math
import os
import hashlib
import json
import shutil
import tempfile
import argparse
from pathlib import Path
from typing import Any

from groq import Groq

import scripts.settings as _cfg
from scripts.audio.ffmpeg import (
    compress_audio,
    estimate_chunk_seconds,
    get_duration,
    segment_audio,
)
from scripts.filesystem import atomic_write_text

_UTF8 = "utf-8"
_CHECKPOINT_VERSION = 1
_CHUNK_RETRIES = 2


def _checkpoint_path(output_path: Path) -> Path:
    return output_path.with_name(output_path.name + ".checkpoint.json")


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _config_fingerprint(audio_path: Path, model: str, max_mb: float, chunk_target_mb: float) -> str:
    payload = {
        "version": _CHECKPOINT_VERSION,
        "audio_sha256": _file_fingerprint(audio_path),
        "model": model,
        "max_mb": max_mb,
        "chunk_target_mb": chunk_target_mb,
        "segment_codec": "aac-mono-16khz",
        "segment_bitrate_kbps": 128,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode(_UTF8)).hexdigest()


def _load_checkpoint(path: Path, fingerprint: str) -> dict[str, Any]:
    if not path.is_file():
        return {"version": _CHECKPOINT_VERSION, "fingerprint": fingerprint, "chunks": {}}
    try:
        checkpoint = json.loads(path.read_text(encoding=_UTF8))
    except (OSError, json.JSONDecodeError):
        return {"version": _CHECKPOINT_VERSION, "fingerprint": fingerprint, "chunks": {}}
    if (
        checkpoint.get("version") != _CHECKPOINT_VERSION
        or checkpoint.get("fingerprint") != fingerprint
        or not isinstance(checkpoint.get("chunks"), dict)
    ):
        return {"version": _CHECKPOINT_VERSION, "fingerprint": fingerprint, "chunks": {}}
    return checkpoint


def _save_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding=_UTF8)


def _write_text_atomic(path: Path, text: str) -> None:
    atomic_write_text(path, text, encoding=_UTF8)


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


def _transcribe_chunk_with_retry(client: Groq, path: Path, model: str) -> str:
    last_error: Exception | None = None
    for _attempt in range(_CHUNK_RETRIES + 1):
        try:
            return _transcribe_single(client, path, model)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Trascrizione del chunk fallita dopo {_CHUNK_RETRIES + 1} tentativi: {last_error}") from last_error


def transcribe(
    audio_path: Path,
    output_path: Path,
    model: str = "",
    max_mb: float = 0.0,
    chunk_target_mb: float = 0.0,
) -> str:
    model = model or _cfg.GROQ_WHISPER_MODEL
    max_mb = max_mb or _cfg.TRANSCRIPTION_MAX_MB
    chunk_target_mb = chunk_target_mb or _cfg.TRANSCRIPTION_CHUNK_TARGET_MB

    if not isinstance(max_mb, (int, float)) or not math.isfinite(max_mb) or max_mb <= 0:
        raise ValueError(f"max_mb deve essere finito e maggiore di zero: {max_mb!r}")
    if (
        not isinstance(chunk_target_mb, (int, float))
        or not math.isfinite(chunk_target_mb)
        or chunk_target_mb <= 0
    ):
        raise ValueError(
            f"chunk_target_mb deve essere finito e maggiore di zero: {chunk_target_mb!r}"
        )
    if chunk_target_mb > max_mb:
        raise ValueError("chunk_target_mb non può superare max_mb")

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY non impostata.")

    client = Groq(api_key=api_key)
    max_bytes = int(max_mb * 1_000_000)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if audio_path.stat().st_size <= max_bytes:
        text = _transcribe_single(client, audio_path, model)
        _write_text_atomic(output_path, text)
        return text

    duration = get_duration(audio_path)
    target_bytes = int(chunk_target_mb * 1_000_000)
    chunk_bitrate_kbps = 128
    chunk_seconds = estimate_chunk_seconds(target_bytes, chunk_bitrate_kbps)
    chunk_count = max(2, math.ceil(duration / chunk_seconds))
    chunk_seconds = max(60, math.ceil(duration / chunk_count))

    checkpoint_file = _checkpoint_path(output_path)
    fingerprint = _config_fingerprint(audio_path, model, max_mb, chunk_target_mb)
    checkpoint = _load_checkpoint(checkpoint_file, fingerprint)
    checkpoint.update(
        {
            "version": _CHECKPOINT_VERSION,
            "fingerprint": fingerprint,
            "chunk_seconds": chunk_seconds,
            "chunk_bitrate_kbps": chunk_bitrate_kbps,
        }
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="groq_chunks_"))
    try:
        chunks = segment_audio(
            audio_path,
            tmp_dir / "_chunks",
            chunk_seconds,
            bitrate_kbps=chunk_bitrate_kbps,
        )
        if not chunks:
            raise RuntimeError("FFmpeg non ha prodotto alcun chunk audio.")
        parts: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            key = str(i)
            cached = checkpoint.get("chunks", {}).get(key)
            if isinstance(cached, dict) and cached.get("status") == "done":
                part_text = str(cached.get("text", ""))
            else:
                print(f"  [trascrizione] Chunk {i}/{len(chunks)}...")
                compressed = _compress_if_needed(chunk, tmp_dir, max_mb)
                try:
                    part_text = _transcribe_chunk_with_retry(client, compressed, model)
                except Exception as exc:
                    checkpoint["chunks"][key] = {
                        "status": "failed",
                        "error": str(exc)[:1000],
                    }
                    _save_checkpoint(checkpoint_file, checkpoint)
                    raise
                checkpoint["chunks"][key] = {"status": "done", "text": part_text}
                _save_checkpoint(checkpoint_file, checkpoint)
            parts.append(f"[PARTE {i}]\n\n{part_text}")
        text = "\n\n".join(parts)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    _write_text_atomic(output_path, text)
    checkpoint_file.unlink(missing_ok=True)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Trascrivi audio con Groq Whisper.")
    parser.add_argument("--audio-path", required=True, type=Path)
    parser.add_argument("--output-path", type=Path, default=None)
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    out = args.output_path or args.audio_path.parent / "trascrizione.txt"
    result = transcribe(args.audio_path, out, model=args.model)
    print(f"Trascrizione salvata in: {out} ({len(result)} caratteri)")


if __name__ == "__main__":
    main()
