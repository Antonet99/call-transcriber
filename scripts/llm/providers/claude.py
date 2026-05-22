"""Provider Claude via CLI (`claude -p`)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

import scripts.settings as _cfg
from scripts.llm.providers.base import LlmProvider

_SUMMARY_SUFFIX = """

---

Istruzioni Claude Code:
- Durante la preparazione del riassunto, usa se utile i subagent call-metadata-auditor e call-action-auditor come controllo interno.
- Integra solo le correzioni utili nel Markdown finale.
- Non riportare log, ragionamenti, output dei subagent o note operative.
- La risposta finale deve contenere esclusivamente il Markdown del riassunto richiesto.
"""


def _summary_agents_json() -> str:
    agents = {
        "call-metadata-auditor": {
            "description": "Controlla metadati, persone, sistemi e tag del riassunto call prima della risposta finale.",
            "prompt": (
                "Sei un revisore di metadati per riassunti di call in Obsidian. "
                "Verifica che frontmatter YAML, persone, sistemi e tag derivino dalla trascrizione, "
                "che il tag call sia presente e che i nomi non vengano inventati. "
                "Restituisci al main agent solo correzioni puntuali."
            ),
            "model": _cfg.CLAUDE_SUBAGENT_MODEL,
            "effort": _cfg.CLAUDE_SUBAGENT_EFFORT,
        },
        "call-action-auditor": {
            "description": "Controlla decisioni, action item, dipendenze, numeri e citazioni rilevanti del riassunto call.",
            "prompt": (
                "Sei un revisore di contenuto per riassunti di call. "
                "Controlla che decisioni, action item, owner, scadenze, dipendenze, numeri, date e citazioni brevi "
                "siano fedeli alla trascrizione. Segnala omissioni concrete al main agent senza riscrivere tutto il riassunto."
            ),
            "model": _cfg.CLAUDE_SUBAGENT_MODEL,
            "effort": _cfg.CLAUDE_SUBAGENT_EFFORT,
        },
    }
    return json.dumps(agents, separators=(",", ":"))


class ClaudeProvider(LlmProvider):
    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def default_summary_model(self) -> str:
        return _cfg.CLAUDE_SUMMARY_MODEL

    def default_task_model(self) -> str:
        return _cfg.CLAUDE_TASK_MODEL

    def _call(self, prompt: str, model: str, effort: str, agents_json: str = "") -> str:
        cmd = ["claude", "-p", "--model", model, "--effort", effort, "--output-format", "text"]
        if agents_json:
            cmd += ["--agents", agents_json]
        result = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            encoding="utf-8",
            shell=(sys.platform == "win32"),
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            detail = (result.stderr or output).strip()
            raise RuntimeError(f"Claude CLI exit {result.returncode}: {detail}")
        return output

    def invoke_summary(self, prompt: str, model: str) -> str:
        return self._call(
            prompt + _SUMMARY_SUFFIX,
            model,
            _cfg.CLAUDE_SUMMARY_EFFORT,
            _summary_agents_json(),
        )

    def invoke_task_classification(self, prompt: str, model: str) -> str:
        return self._call(prompt, model, _cfg.CLAUDE_TASK_EFFORT)

    def invoke_light(self, prompt: str, model: str) -> str:
        return self._call(prompt, model or _cfg.CLAUDE_LIGHT_MODEL, _cfg.CLAUDE_LIGHT_EFFORT)
