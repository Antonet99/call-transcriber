"""Esecuzione controllata di processi esterni.

Il wrapper mantiene l'output testuale omogeneo e, su Windows, termina anche
i processi figli quando scade il timeout. Non stampa mai gli argomenti o gli
input: i chiamanti possono quindi evitare di registrare segreti nei log.
"""
from __future__ import annotations

import math
import os
import signal
import subprocess
from collections.abc import Sequence


def _command_args(args: Sequence[str | os.PathLike[str]]) -> list[str]:
    return [os.fspath(arg) for arg in args]


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    """Termina process e discendenti, senza nascondere l'errore originale."""
    if process.poll() is not None:
        return

    if os.name == "nt":
        # taskkill /T è il percorso più affidabile per processi creati da
        # CLI native che non ereditano il gruppo del processo Python.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    try:
        process.kill()
    except (OSError, ProcessLookupError):
        pass


def run_command(
    args: Sequence[str | os.PathLike[str]],
    *,
    timeout: float | None,
    input: str | bytes | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Esegue ``args`` con output UTF-8 e timeout applicativo.

    Il risultato segue il contratto di :func:`subprocess.run`, ma ``check``
    resta responsabilità del chiamante. ``input`` in bytes viene decodificato
    in UTF-8 per mantenere il risultato e l'I/O sempre testuali.
    """
    command = _command_args(args)
    input_text = input.decode("utf-8", errors="replace") if isinstance(input, bytes) else input
    if timeout is not None and (
        not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0
    ):
        raise ValueError(f"timeout deve essere finito e maggiore di zero: {timeout!r}")

    creationflags = 0
    start_new_session = os.name != "nt"
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=start_new_session,
        creationflags=creationflags,
        cwd=os.fspath(cwd) if cwd is not None else None,
    )
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired as cleanup_exc:
            # Un discendente può aver ereditato stdout/stderr anche dopo la
            # chiusura del processo principale; non attendere indefinitamente.
            stdout = cleanup_exc.output or exc.output or ""
            stderr = cleanup_exc.stderr or exc.stderr or ""
            try:
                process.kill()
                process.wait(timeout=1)
            except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                pass
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout or exc.output,
            stderr=stderr or exc.stderr,
        ) from exc

    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
