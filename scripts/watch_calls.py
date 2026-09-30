"""Watcher non bloccante della cartella ``da_processare/``.

La verifica di stabilità gira in un thread dedicato: un file ancora in
scrittura resta in attesa mentre le altre call pronte possono proseguire. Gli
errori della pipeline vengono conservati in un piccolo journal JSON per
limitare i retry anche dopo il riavvio del watcher.
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer

import scripts.settings as _cfg
from scripts.filesystem import atomic_write_text

_LOGGER = logging.getLogger(__name__)
_SUPPORTED_EXT = {
    ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".webm", ".wma",
    ".mp4", ".mkv", ".mov", ".avi",
}
_DEFAULT_RETRY_LIMIT = 3
_DEFAULT_RETRY_BACKOFF_SECONDS = 30.0
_DEFAULT_READINESS_INTERVAL_SECONDS = 1.0
_DEFAULT_STABLE_CHECKS = 3
_DEFAULT_STOP_TIMEOUT_SECONDS = 10.0
_STATE_VERSION = 1


def _setup_logging(root: Path) -> None:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "watcher.log"
    fmt = "%(asctime)s %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)


@contextmanager
def _instance_lock(root: Path) -> Iterator[None]:
    """Acquisisce un lock di processo per evitare watcher concorrenti."""
    lock_path = root / ".pipeline" / "watcher.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Un altro watcher è già attivo per questo vault.") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Un altro watcher è già attivo per questo vault.") from exc
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class _StateStore:
    def __init__(self, root: Path) -> None:
        self._path = root / ".pipeline" / "watcher-state.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    @staticmethod
    def key(path: Path | str) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    def _load(self) -> None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if payload.get("version") != _STATE_VERSION or not isinstance(payload.get("entries"), dict):
            return
        self._entries = {
            str(key): value
            for key, value in payload["entries"].items()
            if isinstance(value, dict)
        }

    def get(self, path: Path | str) -> dict[str, Any]:
        with self._lock:
            return dict(self._entries.get(self.key(path), {}))

    def update(self, path: Path | str, **values: Any) -> dict[str, Any]:
        key = self.key(path)
        with self._lock:
            entry = self._entries.setdefault(key, {})
            entry.update(values)
            entry["updated_at"] = time.time()
            self._save()
            return dict(entry)

    def remove(self, path: Path | str) -> None:
        with self._lock:
            self._entries.pop(self.key(path), None)
            self._save()

    def _save(self) -> None:
        try:
            atomic_write_text(
                self._path,
                json.dumps(
                    {"version": _STATE_VERSION, "entries": self._entries},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            _LOGGER.warning(
                "Impossibile salvare lo stato del watcher in %s; "
                "verrà ritentato alla prossima modifica",
                self._path,
                exc_info=True,
            )


class _CallHandler(FileSystemEventHandler):
    def __init__(
        self,
        root: Path,
        extra_kwargs: dict[str, Any],
        *,
        retry_limit: int = _DEFAULT_RETRY_LIMIT,
        retry_backoff_seconds: float = _DEFAULT_RETRY_BACKOFF_SECONDS,
        readiness_interval_seconds: float = _DEFAULT_READINESS_INTERVAL_SECONDS,
        stable_checks: int = _DEFAULT_STABLE_CHECKS,
        stop_timeout_seconds: float = _DEFAULT_STOP_TIMEOUT_SECONDS,
    ) -> None:
        self._root = root
        self._extra = extra_kwargs
        self._retry_limit = max(1, retry_limit)
        self._retry_backoff = max(0.0, retry_backoff_seconds)
        self._readiness_interval = max(0.05, readiness_interval_seconds)
        self._stable_checks = max(1, stable_checks)
        self._stop_timeout = max(0.1, stop_timeout_seconds)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._pending: dict[str, dict[str, Any]] = {}
        self._known: set[str] = set()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._state = _StateStore(root)
        self._worker = threading.Thread(target=self._run_worker, name="call-worker", daemon=True)
        self._readiness = threading.Thread(target=self._run_readiness, name="call-readiness", daemon=True)
        self._worker.start()
        self._readiness.start()

    def _should_process(self, path: str) -> bool:
        return Path(path).suffix.lower() in _SUPPORTED_EXT

    @staticmethod
    def _signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_size, stat.st_mtime_ns

    def _handle(self, path: str, *, bypass_stability: bool = False) -> None:
        if not self._should_process(path):
            return
        key = _StateStore.key(path)
        signature = self._signature(Path(path))
        with self._lock:
            if key in self._known:
                return
            self._known.add(key)
            current = self._state.get(path)
            previous_signature = current.get("signature")
            if previous_signature != list(signature or ()):
                self._state.update(path, attempts=0, status="waiting", signature=list(signature or ()))
            self._pending[key] = {
                "path": path,
                "signature": signature,
                "same": self._stable_checks if bypass_stability else 0,
                "queued": bypass_stability,
            }
            if bypass_stability:
                self._queue.put(path)

    def _due(self, path: str) -> bool:
        entry = self._state.get(path)
        if entry.get("status") == "failed":
            return False
        return float(entry.get("next_attempt_at", 0.0) or 0.0) <= time.time()

    def _run_readiness(self) -> None:
        while not self._stop_event.wait(self._readiness_interval):
            with self._lock:
                pending = list(self._pending.values())
            for item in pending:
                path = Path(item["path"])
                if item.get("queued") or not self._due(str(path)):
                    continue
                signature = self._signature(path)
                if signature is None:
                    if self._state.get(path).get("from_job"):
                        self._enqueue(path, bypass_stability=True)
                    continue
                with self._lock:
                    if signature == item.get("signature"):
                        item["same"] = int(item.get("same", 0)) + 1
                    else:
                        item["signature"] = signature
                        item["same"] = 1
                    stable = item["same"] >= self._stable_checks
                if stable:
                    self._enqueue(path)

    def _enqueue(self, path: Path, *, bypass_stability: bool = False) -> None:
        key = _StateStore.key(path)
        with self._lock:
            item = self._pending.get(key)
            if item is None:
                self._pending[key] = {
                    "path": str(path),
                    "signature": self._signature(path),
                    "same": self._stable_checks,
                    "queued": True,
                }
            else:
                if item.get("queued"):
                    return
                item["queued"] = True
            self._state.update(path, status="queued", next_attempt_at=0.0)
            self._queue.put(str(path))

    def _schedule_retry(self, path: str, exc: Exception) -> None:
        current = self._state.get(path)
        attempts = int(current.get("attempts", 0) or 0) + 1
        failed = attempts >= self._retry_limit
        delay = self._retry_backoff * (2 ** max(0, attempts - 1))
        status = "failed" if failed else "waiting"
        self._state.update(
            path,
            attempts=attempts,
            status=status,
            next_attempt_at=time.time() + delay,
            last_error=str(exc)[:1000],
        )
        if failed:
            _LOGGER.error(
                "Pipeline definitivamente fallita per %s dopo %d tentativi; "
                "stato salvato in .pipeline/watcher-state.json",
                path,
                attempts,
            )
        else:
            _LOGGER.warning("Retry %d/%d per %s tra %.0fs", attempts, self._retry_limit, path, delay)
        with self._lock:
            item = self._pending.get(_StateStore.key(path))
            if item is not None:
                item["queued"] = False

    def _run_worker(self) -> None:
        from scripts.process_call import process

        while True:
            path = self._queue.get()
            if path is None:
                self._queue.task_done()
                break
            if self._stop_event.is_set():
                with self._lock:
                    item = self._pending.get(_StateStore.key(path))
                    if item is not None:
                        item["queued"] = False
                self._queue.task_done()
                continue
            self._state.update(path, status="processing")
            try:
                process(input_path=Path(path), root=self._root, **self._extra)
            except Exception as exc:
                _LOGGER.exception("Pipeline fallita per %s", path)
                self._schedule_retry(path, exc)
            else:
                self._state.remove(path)
                with self._lock:
                    self._pending.pop(_StateStore.key(path), None)
            finally:
                with self._lock:
                    self._known.discard(_StateStore.key(path))
                self._queue.task_done()

    def refresh_pending_inputs(self) -> None:
        try:
            from scripts.jobs import pending_inputs

            paths = pending_inputs(self._root)
        except (ImportError, AttributeError, OSError) as exc:
            _LOGGER.debug("Impossibile leggere il journal delle lavorazioni: %s", exc)
            return
        for raw_path in paths:
            path = Path(raw_path)
            self._state.update(path, from_job=True)
            self._handle(str(path), bypass_stability=not path.exists())

    def run_maintenance(self) -> None:
        try:
            from scripts.archive_old_calls import archive
            from scripts.jobs import pending_call_dirs, vault_lock
            from scripts.obsidian import indexes as obs_indexes
            from scripts.process_call import _cleanup_source_archive
        except (ImportError, AttributeError):
            return
        try:
            with vault_lock(self._root):
                excluded = pending_call_dirs(self._root)
                result = archive(self._root, excluded_call_dirs=excluded)
                deleted_sources = _cleanup_source_archive(self._root)
                if result.get("archived"):
                    obs_indexes.rebuild(self._root)
            if result.get("archived") or result.get("videos_deleted") or deleted_sources:
                _LOGGER.info(
                    "Manutenzione vault: %s, sorgenti rimosse: %d",
                    result,
                    deleted_sources,
                )
        except Exception:
            _LOGGER.exception("Manutenzione periodica fallita")

    def stop(self) -> None:
        self._stop_event.set()
        self._queue.put(None)
        self._readiness.join(timeout=self._stop_timeout)
        self._worker.join(timeout=self._stop_timeout)
        if self._readiness.is_alive() or self._worker.is_alive():
            _LOGGER.warning("Arresto watcher scaduto dopo %.1fs", self._stop_timeout)

    def on_created(self, event: FileCreatedEvent) -> None:
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event: FileMovedEvent) -> None:
        if not event.is_directory:
            self._handle(event.dest_path)

    def on_modified(self, event: FileModifiedEvent) -> None:
        if not event.is_directory:
            self._handle(event.src_path)


def watch(root: Path | None = None, **kwargs: Any) -> None:
    if root is None:
        root = _cfg.VAULT_ROOT
    root = root.resolve()
    _setup_logging(root)

    watch_dir = root / "da_processare"
    watch_dir.mkdir(parents=True, exist_ok=True)
    retry_limit = int(kwargs.pop("retry_limit", _DEFAULT_RETRY_LIMIT))
    retry_backoff = float(kwargs.pop("retry_backoff_seconds", _DEFAULT_RETRY_BACKOFF_SECONDS))
    readiness_interval = float(kwargs.pop("readiness_interval_seconds", _DEFAULT_READINESS_INTERVAL_SECONDS))
    stable_checks = int(kwargs.pop("stable_checks", _DEFAULT_STABLE_CHECKS))
    stop_timeout = float(kwargs.pop("stop_timeout_seconds", _DEFAULT_STOP_TIMEOUT_SECONDS))
    maintenance_interval = max(1.0, float(kwargs.pop("maintenance_interval_seconds", 900.0)))

    with _instance_lock(root):
        handler = _CallHandler(
            root,
            kwargs,
            retry_limit=retry_limit,
            retry_backoff_seconds=retry_backoff,
            readiness_interval_seconds=readiness_interval,
            stable_checks=stable_checks,
            stop_timeout_seconds=stop_timeout,
        )
        observer = Observer()
        observer.schedule(handler, str(watch_dir), recursive=False)
        observer.start()
        _LOGGER.info("Watcher avviato su: %s", watch_dir)
        _LOGGER.info("Premi Ctrl+C per fermare.")

        for existing in watch_dir.iterdir():
            if existing.is_file() and existing.suffix.lower() in _SUPPORTED_EXT:
                _LOGGER.info("File preesistente rilevato: %s", existing.name)
                handler._handle(str(existing))
        handler.refresh_pending_inputs()

        last_maintenance = time.monotonic()
        try:
            while observer.is_alive():
                observer.join(timeout=1)
                if time.monotonic() - last_maintenance >= maintenance_interval:
                    handler.refresh_pending_inputs()
                    handler.run_maintenance()
                    last_maintenance = time.monotonic()
        except KeyboardInterrupt:
            _LOGGER.info("Arresto richiesto dall'utente")
            observer.stop()
        finally:
            observer.stop()
            observer.join(timeout=stop_timeout)
            handler.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Watcher cartella da_processare/.")
    parser.add_argument("--root-path", type=Path, default=None)
    parser.add_argument("--archive-max-mb", type=float, default=None)
    parser.add_argument("--keep-video", action="store_true")
    parser.add_argument("--retry-limit", type=int, default=_DEFAULT_RETRY_LIMIT)
    parser.add_argument("--retry-backoff-seconds", type=float, default=_DEFAULT_RETRY_BACKOFF_SECONDS)
    parser.add_argument("--maintenance-interval-seconds", type=float, default=900.0)
    args = parser.parse_args()

    kwargs: dict[str, Any] = {
        "keep_video": args.keep_video,
        "retry_limit": args.retry_limit,
        "retry_backoff_seconds": args.retry_backoff_seconds,
        "maintenance_interval_seconds": args.maintenance_interval_seconds,
    }
    if args.archive_max_mb is not None:
        kwargs["archive_max_mb"] = args.archive_max_mb
    watch(root=args.root_path, **kwargs)


if __name__ == "__main__":
    main()
