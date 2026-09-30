"""Persistent processing checkpoints and a vault-wide interprocess lock."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from scripts.filesystem import atomic_write_text

_locks: dict[str, threading.RLock] = {}
_guard = threading.Lock()
_held = threading.local()


@contextmanager
def vault_lock(root: Path, timeout: float = 30.0):
    key = os.path.normcase(str(root.resolve()))
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=timeout):
        raise TimeoutError("Vault occupato da un'altra lavorazione.")
    held = getattr(_held, "roots", set())
    stream = None
    acquired = False
    nested = key in held
    try:
        if not nested:
            directory = root / ".pipeline"
            directory.mkdir(parents=True, exist_ok=True)
            stream = (directory / "vault.lock").open("a+b")
            if stream.seek(0, 2) == 0:
                stream.write(b"0")
                stream.flush()
            deadline = time.monotonic() + timeout
            while True:
                stream.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Vault occupato da un altro processo.")
                    time.sleep(0.1)
            _held.roots = held | {key}
        yield
    finally:
        if stream is not None:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_UN)
                _held.roots = held
            stream.close()
        lock.release()


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_job(root: Path, state: dict) -> None:
    path = root / ".pipeline" / "jobs" / f"{state['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2))


def iter_jobs(root: Path):
    for path in sorted((root / ".pipeline" / "jobs").glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Checkpoint illeggibile: {path.name}") from exc
        if not isinstance(value, dict) or value.get("id") != path.stem:
            raise ValueError(f"Checkpoint non valido: {path.name}")
        yield value


def pending_inputs(root: Path) -> list[Path]:
    return list(dict.fromkeys(Path(s["source"]) for s in iter_jobs(root) if not s.get("complete")))


def pending_call_dirs(root: Path) -> set[Path]:
    return {Path(s["final_dir"]) for s in iter_jobs(root)
            if not s.get("complete") and s.get("final_dir")}


def load_job(root: Path, source: Path, config: dict) -> dict:
    source_key = os.path.normcase(str(source.resolve()))
    if not source.is_file():
        candidates = [s for s in iter_jobs(root)
                      if os.path.normcase(s["source"]) == source_key]
        if not candidates:
            raise FileNotFoundError(f"File non trovato: {source}")
        return max(candidates, key=lambda s: s["timestamp"])
    before = source.stat()
    digest = fingerprint(source)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("La sorgente è cambiata durante il controllo di integrità.")
    identity = f"{source_key}\0{before.st_mtime_ns}\0{digest}"
    job_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
    path = root / ".pipeline" / "jobs" / f"{job_id}.json"
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("config") != config and not state.get("complete"):
            raise ValueError("Configurazione diversa dal checkpoint: ripristinare i parametri originali per riprendere.")
        return state
    state = {"id": job_id, "source": str(source.resolve()), "sha256": digest,
             "timestamp": before.st_mtime, "mtime_ns": before.st_mtime_ns,
             "size": before.st_size, "config": config, "complete": False}
    save_job(root, state)
    return state
