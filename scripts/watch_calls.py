"""Rileva registrazioni e conserva una coda persistente di scelte esplicite."""
from __future__ import annotations

import argparse
import copy
import json
import logging
import logging.handlers
import math
import os
import uuid
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer
from rich.logging import RichHandler

import scripts.settings as _cfg
from scripts.filesystem import atomic_write_text
from scripts.terminal import console, panel, interactive, read_key, clear_input, input_line, screen_session, Choice, Selection, select_item

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
    ]
    terminal_handler = RichHandler(console=console, show_time=False, show_path=False, show_level=True)
    terminal_handler.setLevel(logging.WARNING)
    terminal_handler.setFormatter(logging.Formatter("%(message)s"))
    terminal_handler.addFilter(lambda record: not record.name.startswith("scripts.process_call"))
    terminal_handler.addFilter(lambda record: not record.exc_info)
    handlers.append(terminal_handler)
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)


@contextmanager
def _instance_lock(root: Path) -> Iterator[None]:
    """Acquisisce un lock di processo per evitare watcher concorrenti."""
    lock_path = root / ".pipeline" / "watcher.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
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
        acquired = True
        yield
    finally:
        try:
            if acquired and os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif acquired:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class _StateStore:
    def __init__(self, root: Path) -> None:
        self._path = root / ".pipeline" / "watcher-state.json"
        self._backup = self._path.with_suffix(".json.bak")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    @staticmethod
    def key(path: Path | str) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    @staticmethod
    def _valid_entry(entry: Any) -> bool:
        if not isinstance(entry, dict):
            return False
        for name in ("first_seen", "ready_at", "timestamp"):
            value = entry.get(name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value)):
                return False
        signature = entry.get("signature")
        if signature is not None and (not isinstance(signature, list) or len(signature) != 2
                                      or any(type(v) is not int or v < 0 for v in signature)):
            return False
        for name in ("action", "status", "destination", "recording_target", "title", "task_dir", "id"):
            if entry.get(name) is not None and not isinstance(entry[name], str):
                return False
        return entry.get("pipeline_kwargs") is None or isinstance(entry["pipeline_kwargs"], dict)

    def _load(self) -> None:
        errors = []
        for path in (self._path, self._backup):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if (not isinstance(payload, dict) or payload.get("version") != _STATE_VERSION
                        or not isinstance(payload.get("entries"), dict)
                        or not all(self._valid_entry(v) for v in payload["entries"].values())):
                    raise ValueError("Formato del journal non valido")
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                errors.append(exc)
                continue
            self._entries = payload["entries"]
            if path == self._backup:
                _LOGGER.warning("Coda recuperata dalla copia di sicurezza del journal.")
            return
        if errors:
            raise RuntimeError("Journal della coda illeggibile: file preservati, nessuna azione avviata.") from errors[0]

    def get(self, path: Path | str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._entries.get(self.key(path), {}))

    def entries(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._entries)

    def update(self, path: Path | str, **values: Any) -> dict[str, Any]:
        key = self.key(path)
        with self._lock:
            old = copy.deepcopy(self._entries)
            entry = self._entries.setdefault(key, {})
            entry.update(values)
            entry["updated_at"] = time.time()
            try:
                self._save()
            except OSError:
                self._entries = old
                raise
            return copy.deepcopy(entry)

    def remove(self, path: Path | str) -> None:
        with self._lock:
            old = copy.deepcopy(self._entries)
            self._entries.pop(self.key(path), None)
            try:
                self._save()
            except OSError:
                self._entries = old
                raise

    def _save(self) -> None:
        payload = json.dumps({"version": _STATE_VERSION, "entries": self._entries},
                             ensure_ascii=False, indent=2)
        atomic_write_text(self._path, payload)
        try:
            atomic_write_text(self._backup, payload)
        except OSError:
            _LOGGER.warning("Copia di sicurezza del journal non aggiornata", exc_info=True)


def _file_available(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        return False
    close(handle)
    return True


class _CallHandler(FileSystemEventHandler):
    def __init__(self, root: Path, extra_kwargs: dict[str, Any], *,
                 readiness_interval_seconds: float = _DEFAULT_READINESS_INTERVAL_SECONDS,
                 stable_checks: int = _DEFAULT_STABLE_CHECKS,
                 stop_timeout_seconds: float = _DEFAULT_STOP_TIMEOUT_SECONDS,
                 **legacy_kwargs: Any) -> None:
        self._root = root.resolve()
        self._extra = extra_kwargs
        self._readiness_interval = max(0.05, readiness_interval_seconds)
        self._stable_checks = max(1, stable_checks)
        self._stop_timeout = max(0.1, stop_timeout_seconds)
        self._pending: dict[str, dict[str, Any]] = {}
        self._active: str | None = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._state = _StateStore(root)
        self._readiness = threading.Thread(target=self._run_readiness, name="call-readiness", daemon=True)
        self._readiness.start()

    @staticmethod
    def _signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
            return stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def _handle(self, path: str, *, bypass_stability: bool = False) -> None:
        source = Path(os.path.abspath(path))
        if (source.suffix.lower() not in _SUPPORTED_EXT or source.is_symlink()
                or source.parent.resolve() != self._root / "da_processare"):
            return
        key = _StateStore.key(source)
        with self._lock:
            if key in self._pending:
                return
            current = self._state.get(source)
            signature = self._signature(source)
            reappeared = current.get("status") == "done" and current.get("source_removed") and signature is not None
            if (current.get("status") == "done" and current.get("signature") == list(signature or ())
                    and not reappeared):
                return
            if reappeared or (current.get("signature") != list(signature or ()) and signature is not None):
                current = self._state.update(source, id=uuid.uuid4().hex[:24], action="",
                                             destination=None, recording_target=None, sha256=None,
                                             note_ready=False, discarded_at=None, original_path=None,
                                             source_removed=False, pipeline_kwargs=self._extra,
                                             first_seen=time.time(), ready_at=None, last_error="",
                                             signature=list(signature), timestamp=signature[1] / 1e9,
                                             status="waiting", from_job=False)
            elif not current.get("id"):
                current = self._state.update(source, id=uuid.uuid4().hex[:24],
                                             first_seen=time.time(), status="waiting")
            if current.get("status") == "processing":
                current = self._state.update(source, status="interrupted")
            resume_status = current.get("status", "waiting")
            if signature is not None and current.get("status") != "waiting":
                self._state.update(source, status="waiting")
            self._pending[key] = {"path": str(source), "signature": signature, "same": 0,
                                  "resume_status": resume_status}
            if bypass_stability:
                self._state.update(source, status="interrupted", ready_at=current.get("ready_at") or time.time())

    def scan(self) -> None:
        watch_dir = self._root / "da_processare"
        paths = []
        for path in watch_dir.iterdir():
            signature = self._signature(path)
            if signature is not None:
                paths.append((signature[1], path.name, path))
        for _, _, path in sorted(paths):
            if path.is_file():
                self._handle(str(path))
        for path, entry in self._state.entries().items():
            if entry.get("status") != "done":
                self._handle(path, bypass_stability=bool(entry.get("destination") and not Path(path).exists()))

    def _check_readiness(self) -> None:
        with self._lock:
            for item in list(self._pending.values()):
                path = Path(item["path"])
                current = self._state.get(path)
                if current.get("status") == "done" or self._active == _StateStore.key(path):
                    continue
                if current.get("status") == "processing":
                    current = self._state.update(path, status="interrupted")
                signature = self._signature(path)
                if signature is None:
                    status = "interrupted" if current.get("destination") or current.get("from_job") else "missing"
                    if current.get("status") != status:
                        self._state.update(path, status=status, ready_at=current.get("ready_at") or time.time())
                    continue
                if list(signature) != current.get("signature"):
                    self._state.update(path, signature=list(signature), action="", status="waiting",
                                       id=uuid.uuid4().hex[:24], timestamp=signature[1] / 1e9,
                                       ready_at=None, destination=None, recording_target=None, sha256=None,
                                       note_ready=False, discarded_at=None, original_path=None,
                                       source_removed=False, pipeline_kwargs=self._extra,
                                       last_error="", from_job=False)
                    item.update(signature=signature, same=0, resume_status="waiting")
                if signature == item["signature"]:
                    item["same"] += 1
                else:
                    item.update(signature=signature, same=1)
                if item["same"] < self._stable_checks or signature[0] <= 0 or not _file_available(path):
                    if current.get("status") == "ready":
                        self._state.update(path, status="waiting")
                    continue
                current = self._state.get(path)
                if current.get("status") in {"waiting", "missing"}:
                    resume = item.get("resume_status")
                    status = resume if resume in {"error", "interrupted"} else "ready"
                    self._state.update(path, status=status, ready_at=current.get("ready_at") or time.time())

    def _run_readiness(self) -> None:
        while not self._stop_event.wait(self._readiness_interval):
            try:
                self._check_readiness()
            except Exception:
                _LOGGER.exception("Controllo coda differito; registrazioni preservate")

    def snapshot(self, *, ready_only: bool = False) -> list[tuple[Path, dict]]:
        with self._lock:
            values = [(Path(key), self._state.get(key)) for key in self._pending]
        values = [(path, entry) for path, entry in values if entry.get("status") != "done"
                  and (not ready_only or entry.get("status") in {"ready", "error", "interrupted"})]
        return sorted(values, key=lambda item: (item[1].get("ready_at") or float("inf"),
                                               item[1].get("first_seen", 0), str(item[0])))

    def execute(self, path: Path, action: str, *, title: str = "", task_dir: Path | None = None) -> bool:
        from scripts import jobs, recordings
        from scripts.process_call import process
        from scripts.obsidian import indexes

        if action not in {"process", "save", "discard"}:
            raise ValueError("Azione non valida")
        with self._lock:
            entry = self._state.get(path)
            if entry.get("status") not in {"ready", "error", "interrupted"}:
                return False
            old_action = entry.get("action")
            if old_action and old_action != action:
                raise ValueError("Riprendere prima l'operazione già scelta per questa registrazione.")
            signature = self._signature(path)
            if signature is not None:
                if list(signature) != entry.get("signature") or not _file_available(path):
                    self._state.update(path, status="waiting", action="", ready_at=None)
                    self._pending[_StateStore.key(path)].update(same=0, signature=signature, resume_status="waiting")
                    return False
            elif not (entry.get("destination") or entry.get("from_job")):
                self._state.update(path, status="missing")
                return False
            entry = self._state.update(path, action=action, status="processing",
                                       from_job=action == "process",
                                       title=entry.get("title") if old_action else title,
                                       task_dir=entry.get("task_dir") if old_action else str(task_dir or ""),
                                       pipeline_kwargs=entry.get("pipeline_kwargs", self._extra))
            self._active = _StateStore.key(path)

        def checkpoint(**values: Any) -> None:
            updated = self._state.update(path, **values)
            entry.clear()
            entry.update(updated)

        try:
            if action == "process":
                process(input_path=path, root=self._root, expected_signature=entry.get("signature"),
                        **entry["pipeline_kwargs"])
            else:
                with jobs.vault_lock(self._root):
                    if action == "save":
                        destination = recordings.save_recording(self._root, path, entry, checkpoint)
                        indexes.rebuild(self._root)
                        panel("Registrazione salvata", f"{destination}\n\nNessuna trascrizione o chiamata ai provider.", "green")
                    elif action == "discard":
                        destination = recordings.discard_recording(self._root, path, entry, checkpoint)
                        panel("Registrazione scartata", f"{destination}\n\nRecuperabile per 30 giorni dalla data di scarto.", "green")
                    else:
                        raise ValueError("Azione non valida")
            checkpoint(status="done", last_error="", source_removed=not path.exists())
            with self._lock:
                self._pending.pop(_StateStore.key(path), None)
            return True
        except Exception as exc:
            _LOGGER.exception("Operazione interrotta per %s", path)
            try:
                checkpoint(status="error", last_error=str(exc)[:1000])
            except OSError:
                _LOGGER.exception("Salvataggio errore differito; decisione già conservata")
            console.print("  [red]Operazione interrotta.[/red] Stato conservato; usa Riprendi. Dettagli in logs/watcher.log.")
            return False
        finally:
            with self._lock:
                self._active = None

    def refresh_pending_inputs(self) -> None:
        from scripts.jobs import iter_jobs

        try:
            for job in iter_jobs(self._root):
                path = Path(job["source"])
                if path.parent.resolve() != self._root / "da_processare":
                    continue
                current = self._state.get(path)
                if job.get("complete"):
                    if (not path.exists() and current.get("action") == "process"
                            and current.get("signature") == [job.get("size"), job.get("mtime_ns")]):
                        self._state.update(path, status="done", source_removed=True)
                        with self._lock:
                            self._pending.pop(_StateStore.key(path), None)
                    continue
                job_signature = [job.get("size"), job.get("mtime_ns")]
                signature = self._signature(path)
                if ((signature is not None and list(signature) != job_signature)
                        or (current.get("signature") and current["signature"] != job_signature)):
                    _LOGGER.warning("Checkpoint precedente ignorato: il percorso %s è stato riutilizzato", path)
                    continue
                self._handle(str(path), bypass_stability=not path.exists())
                current = self._state.get(path)
                if current.get("status") != "done":
                    config = job["config"]
                    self._state.update(path, from_job=True, action="process",
                                       signature=job_signature,
                                       status="waiting" if path.exists() else "interrupted",
                                       ready_at=current.get("ready_at") or time.time(),
                                       pipeline_kwargs={"archive_max_mb": config["archive_max_mb"],
                                                        "summary_model": config["summary_model"],
                                                        "task_model": config["task_model"]})
                    with self._lock:
                        item = self._pending.get(_StateStore.key(path))
                        if item is not None:
                            item["resume_status"] = "interrupted"
        except (OSError, ValueError, RuntimeError, KeyError):
            _LOGGER.exception("Checkpoint non leggibile; nessuna ripresa automatica")

    def run_maintenance(self) -> None:
        from scripts.archive_old_calls import archive
        from scripts.jobs import pending_call_dirs, vault_lock
        from scripts.obsidian import indexes
        from scripts.process_call import _cleanup_source_archive
        from scripts.recordings import cleanup_trash

        try:
            with vault_lock(self._root):
                excluded = pending_call_dirs(self._root)
                excluded.update(Path(entry["destination"]) for entry in self._state.entries().values()
                                if entry.get("destination") and entry.get("status") != "done")
                result = archive(self._root, excluded_call_dirs=excluded)
                _cleanup_source_archive(self._root)
                from scripts.recording_catalog import protected_trash_dirs
                cleanup_trash(self._root, excluded_dirs=protected_trash_dirs(self._root))
                if result.get("archived"):
                    indexes.rebuild(self._root)
        except Exception:
            _LOGGER.exception("Manutenzione differita; verrà riprovata")

    def stop(self) -> None:
        self._stop_event.set()
        self._readiness.join(timeout=self._stop_timeout)

    def on_created(self, event: FileCreatedEvent) -> None:
        self._event(event)

    def on_moved(self, event: FileMovedEvent) -> None:
        self._event(event)

    def on_modified(self, event: FileModifiedEvent) -> None:
        self._event(event)

    def _event(self, event: Any) -> None:
        if not event.is_directory:
            try:
                self._handle(getattr(event, "dest_path", None) or event.src_path)
            except Exception:
                _LOGGER.exception("Rilevamento differito alla prossima scansione")


def _save_details(root: Path, path: Path) -> tuple[str, Path] | None:
    from scripts import tasks
    from scripts.filesystem import safe_name

    title = path.stem
    while True:
        panel("Titolo registrazione", path.name)
        title = input_line("Modifica il titolo · Invio conferma · Esc indietro", initial=title)
        if title is None:
            return None
        title = title.strip()
        if not title:
            continue
        directories = [directory for directory in tasks.discover_task_dirs(root) if not tasks.is_archived_task(directory)]
        labels = tasks.task_labels(directories)
        choices = [Choice(root / "completate" / _cfg.UNASSIGNED_CALLS_DIR_NAME, "Senza progetto")]
        choices.extend(Choice(directory, labels[directory]) for directory in directories)
        directory = select_item("Progetto", choices, message=title)
        if directory is not None:
            return safe_name(title), directory


def _show_queue(handler: _CallHandler) -> Path | None:
    names = {"waiting": "in registrazione", "ready": "da scegliere", "error": "da riprovare",
             "interrupted": "da riprendere", "missing": "file non disponibile"}
    def choices():
        return [Choice(path, f"{path.name} · {names.get(entry.get('status'), entry.get('status'))}",
                       enabled=entry.get("status") in {"ready", "error", "interrupted"})
                for path, entry in handler.snapshot()]
    return select_item("Coda registrazioni", choices)


def _show_unprocessed(handler: _CallHandler) -> None:
    from scripts.recording_catalog import list_unprocessed, process_recording

    groups = ("Salvate nelle cartelle", "Nel cestino")
    message = "Seleziona una registrazione per vedere i dettagli."
    while True:
        try:
            items = list_unprocessed(handler._root)
        except (OSError, ValueError, RuntimeError):
            _LOGGER.exception("Catalogo non disponibile")
            select_item("Registrazioni non elaborate", [], message="Catalogo temporaneamente non disponibile.", groups=groups)
            return
        choices = []
        for category, group in (("saved", groups[0]), ("trash", groups[1])):
            for item in items:
                if item["category"] != category:
                    continue
                label = f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(item['timestamp']))} · {item['title']}"
                if category == "saved":
                    label += f" · {item.get('task_name') or 'Senza progetto'}"
                if item.get("status") == "interrupted":
                    label += " · Da riprendere"
                choices.append(Choice(item["id"], label, group))
        selected = select_item("Registrazioni non elaborate", choices, message=message, groups=groups)
        if selected is None:
            return
        item = next(item for item in items if item["id"] == selected)
        detail = f"{item['title']}\n{item['source']}"
        if item["category"] == "saved":
            detail += f"\nProgetto: {item.get('task_name') or 'Senza progetto'} · titolo e cartella vengono mantenuti."
        else:
            expires = item.get("discarded_at", time.time()) + 30 * 86400
            detail += f"\nNel cestino fino al {time.strftime('%d/%m/%Y', time.localtime(expires))}."
        label = "Riprendi elaborazione" if item.get("status") == "interrupted" else "Trascrivi e riassumi"
        if select_item("Elabora registrazione", [Choice("process", label)], message=detail) is None:
            continue
        try:
            process_recording(handler._root, item, **handler._extra)
            message = "Elaborazione completata. La registrazione è stata rimossa dall'elenco."
        except Exception as exc:
            _LOGGER.exception("Elaborazione su richiesta interrotta")
            message = f"Elaborazione non completata: {exc}. Stato conservato; seleziona nuovamente la registrazione per riprendere."
        finally:
            clear_input()


def _menu_loop(handler: _CallHandler, observer: Observer, maintenance_interval: float) -> None:
    selected = None
    visible_selection = None
    rendered = None
    selection = Selection([])
    last_scan = last_maintenance = time.monotonic()
    while observer.is_alive():
        now = time.monotonic()
        if now - last_scan >= 5:
            try:
                handler.scan()
            except OSError:
                _LOGGER.exception("Scansione differita")
            last_scan = now
        if now - last_maintenance >= maintenance_interval:
            handler.run_maintenance()
            last_maintenance = now
        ready = handler.snapshot(ready_only=True)
        if selected not in [path for path, _ in ready]:
            selected = ready[0][0] if ready else None
        entry = next((entry for path, entry in ready if path == selected), {})
        focus = None
        if selected != visible_selection:
            previous = selection.choices[selection.index].value if selection.index >= 0 else None
            focus = previous if previous in {"queue", "catalog"} else ("queue" if visible_selection is not None else None)
            clear_input()
            selection = Selection([])
            visible_selection = selected
        choices = []
        if selected:
            if entry.get("action"):
                choices.append(Choice(entry["action"], "Riprendi l'operazione già scelta"))
            else:
                choices.extend([Choice("process", "Trascrivi e riassumi"),
                                Choice("save", "Salva solo registrazione"),
                                Choice("discard", "Scarta · cestino per 30 giorni")])
        choices.extend([Choice("queue", "Nuove registrazioni in attesa · Q"),
                        Choice("catalog", "Registrazioni non elaborate · N")])
        selection.update(choices)
        if focus is not None:
            selection.index = next(i for i, choice in enumerate(choices) if choice.value == focus)
        display = (str(selected), entry.get("status"), entry.get("action"), len(handler.snapshot()), len(ready), selection.index, console.size)
        if display != rendered:
            message = f"{selected.name}\nIn attesa: {display[3]} · Pronte: {len(ready)}" if selected else f"Registrazioni in attesa: {display[3]}"
            panel("Nuova call" if selected else "In ascolto", selection.render(message))
            rendered = display
        key = read_key()
        action = {"q": "queue", "n": "catalog"}.get(key) or selection.handle(key)
        if action == "queue":
            selected = _show_queue(handler) or selected
            rendered = None
        elif action == "catalog":
            _show_unprocessed(handler)
            rendered = None
        elif selected and action in {"process", "save", "discard"}:
            title, directory = "", None
            if action == "save" and not entry.get("action"):
                details = _save_details(handler._root, selected)
                if details is None:
                    rendered = None
                    continue
                title, directory = details
            try:
                handler.execute(selected, action, title=title, task_dir=directory)
            except (OSError, ValueError) as exc:
                _LOGGER.exception("Scelta non eseguita")
                panel("Scelta non eseguita", str(exc), "yellow")
            clear_input()
            rendered = None


def watch(root: Path | None = None, **kwargs: Any) -> None:
    root = (root or _cfg.VAULT_ROOT).resolve()
    _setup_logging(root)
    watch_dir = root / "da_processare"
    watch_dir.mkdir(parents=True, exist_ok=True)
    kwargs.pop("retry_limit", None)
    kwargs.pop("retry_backoff_seconds", None)
    readiness_interval = float(kwargs.pop("readiness_interval_seconds", _DEFAULT_READINESS_INTERVAL_SECONDS))
    stable_checks = int(kwargs.pop("stable_checks", _DEFAULT_STABLE_CHECKS))
    stop_timeout = float(kwargs.pop("stop_timeout_seconds", _DEFAULT_STOP_TIMEOUT_SECONDS))
    maintenance_interval = max(1.0, float(kwargs.pop("maintenance_interval_seconds", 900.0)))
    try:
        with _instance_lock(root):
            handler = _CallHandler(root, kwargs, readiness_interval_seconds=readiness_interval,
                                   stable_checks=stable_checks, stop_timeout_seconds=stop_timeout)
            observer = None
            started = False
            try:
                handler.scan()
                handler.refresh_pending_inputs()
                if not interactive():
                    panel("Call Transcriber", "Terminale non interattivo: coda conservata, nessuna operazione avviata.\nAvvia scripts\\start_watcher.cmd in una console per scegliere.")
                    return
                observer = Observer()
                observer.schedule(handler, str(watch_dir), recursive=False)
                observer.start()
                started = True
                with screen_session():
                    handler.run_maintenance()
                    _menu_loop(handler, observer, maintenance_interval)
            except (KeyboardInterrupt, EOFError):
                _LOGGER.info("Arresto richiesto; coda e decisioni conservate")
            finally:
                if started:
                    observer.stop()
                    observer.join(timeout=stop_timeout)
                handler.stop()
    except (OSError, RuntimeError) as exc:
        _LOGGER.exception("Watcher non avviato; registrazioni preservate")
        panel("Avvio sospeso", str(exc), "yellow")


def main() -> None:
    parser = argparse.ArgumentParser(description="Watcher cartella da_processare/.")
    parser.add_argument("--root-path", type=Path, default=None)
    parser.add_argument("--archive-max-mb", type=float, default=None)
    parser.add_argument("--keep-video", action="store_true")
    parser.add_argument("--retry-limit", type=int, default=_DEFAULT_RETRY_LIMIT,
                        help="Compatibilità: i retry richiedono sempre una scelta dal menu.")
    parser.add_argument("--retry-backoff-seconds", type=float, default=_DEFAULT_RETRY_BACKOFF_SECONDS,
                        help="Compatibilità: non vengono eseguiti retry automatici.")
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
