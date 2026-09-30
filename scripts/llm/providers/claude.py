"""Provider Claude via CLI (`claude -p`)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

import scripts.settings as _cfg
from scripts.llm.providers.base import LlmProvider
from scripts.subprocesses import run_command

_REPO_ROOT = Path(__file__).resolve().parents[3]

_SUMMARY_SUFFIX = """

---

Istruzioni Claude Code:
- Durante la preparazione del riassunto, usa se utile i subagent call-metadata-auditor e call-action-auditor come controllo interno.
- Integra solo le correzioni utili nel Markdown finale.
- Non riportare log, ragionamenti, output dei subagent o note operative.
- La risposta finale deve contenere esclusivamente il Markdown del riassunto richiesto.
"""


def _setting(name: str, default: Any) -> Any:
    return getattr(_cfg, name, default)


def _summary_agents_json() -> str:
    agents = {
        "call-metadata-auditor": {
            "description": "Controlla metadati, persone, sistemi e tag del riassunto call prima della risposta finale.",
            "prompt": (
                "Sei un revisore di metadati per riassunti di call in Obsidian. "
                "Verifica che frontmatter YAML, persone, sistemi e tags derivino dalla trascrizione, "
                "che il tag call sia presente e che i nomi non vengano inventati. "
                "Restituisci al main agent solo correzioni puntuali."
            ),
            "model": _setting("CLAUDE_SUBAGENT_MODEL", "claude-sonnet-5"),
            "effort": _setting("CLAUDE_SUBAGENT_EFFORT", "medium"),
        },
        "call-action-auditor": {
            "description": "Controlla decisioni, action item, dipendenze, numeri e citazioni rilevanti del riassunto call.",
            "prompt": (
                "Sei un revisore di contenuto per riassunti di call. "
                "Controlla che decisioni, action item, owner, scadenze, dipendenze, numeri, date e citazioni brevi "
                "siano fedeli alla trascrizione. Segnala omissioni concrete al main agent senza riscrivere tutto il riassunto."
            ),
            "model": _setting("CLAUDE_SUBAGENT_MODEL", "claude-sonnet-5"),
            "effort": _setting("CLAUDE_SUBAGENT_EFFORT", "medium"),
        },
    }
    return json.dumps(agents, separators=(",", ":"))


class ClaudeProvider(LlmProvider):
    """Controlla una singola invocazione CLI senza sessione persistente."""

    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        cwd: str | Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._configured_executable = executable
        self._cwd = Path(cwd or _setting("CLAUDE_CWD", _REPO_ROOT)).expanduser().resolve()
        self._runner = runner
        self.last_usage: dict[str, Any] = {}
        self.last_response: dict[str, Any] = {}

    def _resolve_executable(self) -> str | None:
        configured = self._configured_executable or _setting("CLAUDE_EXECUTABLE", None)
        if configured:
            path = Path(configured).expanduser()
            return str(path.resolve()) if path.exists() else str(path)
        return shutil.which("claude.exe") or shutil.which("claude")

    def is_available(self) -> bool:
        return self._resolve_executable() is not None and self._cwd.is_dir()

    def default_summary_model(self) -> str:
        return _setting("CLAUDE_SUMMARY_MODEL", "claude-sonnet-5")

    def default_task_model(self) -> str:
        return _setting("CLAUDE_TASK_MODEL", "claude-sonnet-5")

    def default_light_model(self) -> str:
        return _setting("CLAUDE_LIGHT_MODEL", "claude-sonnet-5")

    def _tools(self, agents_json: str) -> str:
        configured = _setting("CLAUDE_TOOLS", None)
        if configured is not None:
            return str(configured)
        return "Task" if agents_json else ""

    def _call(self, prompt: str, model: str, effort: str, agents_json: str = "") -> str:
        executable = self._resolve_executable()
        if not executable:
            raise RuntimeError("Claude CLI non disponibile: configurare CLAUDE_EXECUTABLE o installare claude.")
        if not self._cwd.is_dir():
            raise RuntimeError(f"Directory di lavoro Claude non trovata: {self._cwd}")

        cmd = [
            executable,
            "-p",
            "--model",
            model,
            "--effort",
            effort,
            "--output-format",
            "json",
            "--max-turns",
            str(_setting("CLAUDE_MAX_TURNS", 3)),
            "--no-session-persistence",
            "--tools",
            self._tools(agents_json),
        ]
        max_budget = _setting("CLAUDE_MAX_BUDGET_USD", 1.0)
        if max_budget is not None:
            cmd += ["--max-budget-usd", str(max_budget)]
        if agents_json:
            cmd += ["--agents", agents_json]

        runner = self._runner or run_command
        timeout = float(_setting("CLAUDE_TIMEOUT_SECONDS", 900))
        try:
            result = runner(
                cmd,
                input=prompt,
                timeout=timeout,
                cwd=str(self._cwd),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Claude CLI timeout dopo {timeout:g} secondi.") from exc
        except OSError as exc:
            raise RuntimeError(f"Avvio Claude CLI fallito: {exc}") from exc

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            detail = stderr or stdout or "nessun dettaglio"
            raise RuntimeError(f"Claude CLI exit {result.returncode}: {detail}")
        if not stdout:
            raise RuntimeError("Claude CLI ha restituito un output vuoto.")

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Claude CLI ha restituito JSON non valido.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Claude CLI ha restituito un JSON inatteso.")

        self.last_response = payload
        usage = payload.get("usage")
        self.last_usage = dict(usage) if isinstance(usage, dict) else {}
        if payload.get("is_error") or payload.get("subtype") not in (None, "success"):
            detail = payload.get("result") or payload.get("error") or payload.get("subtype")
            raise RuntimeError(f"Claude CLI ha segnalato un errore: {detail}")
        output = payload.get("result")
        if not isinstance(output, str) or not output.strip():
            raise RuntimeError("Claude CLI non ha restituito il campo testuale 'result'.")
        return output.strip()

    def invoke_summary(self, prompt: str, model: str) -> str:
        return self._call(
            prompt + _SUMMARY_SUFFIX,
            model or self.default_summary_model(),
            _setting("CLAUDE_SUMMARY_EFFORT", "medium"),
            _summary_agents_json(),
        )

    def invoke_task_classification(self, prompt: str, model: str) -> str:
        return self._call(
            prompt,
            model or self.default_task_model(),
            _setting("CLAUDE_TASK_EFFORT", "medium"),
        )

    def invoke_light(self, prompt: str, model: str) -> str:
        return self._call(
            prompt,
            model or self.default_light_model(),
            _setting("CLAUDE_LIGHT_EFFORT", "medium"),
        )
