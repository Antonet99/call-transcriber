"""Watcher della cartella da_processare/ via watchdog.

Avvia la pipeline process_call.process() per ogni file audio/video rilevato.
"""
from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer


def _setup_logging(root: Path) -> None:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "watcher.log"
    fmt = "%(asctime)s %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)

_SUPPORTED_EXT = {
    ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".webm", ".wma",
    ".mp4", ".mkv", ".mov", ".avi",
}


class _CallHandler(FileSystemEventHandler):
    def __init__(self, root: Path, provider: str, extra_kwargs: dict) -> None:
        self._root = root
        self._provider = provider
        self._extra = extra_kwargs
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._known: set[str] = set()
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run_worker)
        self._worker.start()

    def _should_process(self, path: str) -> bool:
        return Path(path).suffix.lower() in _SUPPORTED_EXT

    def _handle(self, path: str) -> None:
        with self._lock:
            if path in self._known:
                return
            self._known.add(path)
        self._queue.put(path)

    def _run_worker(self) -> None:
        from scripts.process_call import process

        while True:
            path = self._queue.get()
            if path is None:
                self._queue.task_done()
                break
            try:
                process(
                    input_path=Path(path),
                    root=self._root,
                    provider_name=self._provider,
                    **self._extra,
                )
            except Exception as exc:
                logging.error("Pipeline fallita per %s: %s", path, exc)
            finally:
                with self._lock:
                    self._known.discard(path)
                self._queue.task_done()

    def stop(self) -> None:
        self._queue.put(None)
        self._worker.join()

    def on_created(self, event: FileCreatedEvent) -> None:
        if not event.is_directory and self._should_process(event.src_path):
            self._handle(event.src_path)

    def on_moved(self, event: FileMovedEvent) -> None:
        if not event.is_directory and self._should_process(event.dest_path):
            self._handle(event.dest_path)


def watch(
    root: Path | None = None,
    provider: str = "gemini",
    **kwargs,
) -> None:
    if root is None:
        root = Path(__file__).parent.parent

    _setup_logging(root)

    watch_dir = root / "da_processare"
    watch_dir.mkdir(parents=True, exist_ok=True)

    handler = _CallHandler(root, provider, kwargs)
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=False)
    observer.start()
    logging.info("Watcher avviato su: %s", watch_dir)
    logging.info("Premi Ctrl+C per fermare.")

    # Elabora file già presenti
    for existing in watch_dir.iterdir():
        if existing.is_file() and existing.suffix.lower() in _SUPPORTED_EXT:
            logging.info("File preesistente rilevato: %s", existing.name)
            handler._handle(str(existing))

    try:
        while observer.is_alive():
            observer.join(timeout=1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    handler.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Watcher cartella da_processare/.")
    parser.add_argument("--root-path", type=Path, default=None)
    parser.add_argument("--provider", default="gemini", choices=["gemini", "claude", "codex"])
    parser.add_argument("--archive-max-mb", type=float, default=19.0)
    parser.add_argument("--keep-video", action="store_true")
    args = parser.parse_args()

    watch(
        root=args.root_path,
        provider=args.provider,
        archive_max_mb=args.archive_max_mb,
        keep_video=args.keep_video,
    )


if __name__ == "__main__":
    main()
