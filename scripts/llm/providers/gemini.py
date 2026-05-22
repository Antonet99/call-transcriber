"""Provider Gemini via CLI (`gemini -p`)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import scripts.settings as _cfg
from scripts.llm.providers.base import LlmProvider

_NOISE_RE = re.compile(r"^Ripgrep is not available\. Falling back to GrepTool\.\s*$", re.I)
_CAPACITY_RE = re.compile(r"RESOURCE_EXHAUSTED|429", re.I)


class GeminiCapacityError(RuntimeError):
    pass


def _find_ripgrep_dir() -> str:
    rg = shutil.which("rg")
    if rg:
        return str(Path(rg).parent)
    local = os.environ.get("LOCALAPPDATA", "")
    for root in [
        Path(local) / "OpenAI" / "Codex" / "bin",
        Path(local) / "Microsoft" / "WinGet" / "Packages",
    ]:
        if root.exists():
            match = next(root.rglob("rg.exe"), None)
            if match:
                return str(match.parent)
    return ""


def _build_env() -> dict:
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    rg_dir = _find_ripgrep_dir()
    if rg_dir and rg_dir not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = rg_dir + os.pathsep + env.get("PATH", "")
    return env


_SUMMARY_SUFFIX = """

---

Istruzioni specifiche Gemini CLI:

- Se disponibili, usa i subagent `@call_metadata_auditor` e `@call_action_auditor` come controllo interno prima della risposta finale.
- `@call_metadata_auditor` deve verificare persone, sistemi, tag e frontmatter.
- `@call_action_auditor` deve verificare decisioni, action item, dipendenze, domande aperte e citazioni rilevanti.
- Non riportare log, ragionamenti, risultati dei subagent o note operative.
- La risposta finale deve restare solo il Markdown del `riassunto.md`, nel formato richiesto dal prompt principale.
"""


class GeminiProvider(LlmProvider):
    def is_available(self) -> bool:
        return shutil.which("gemini") is not None

    def default_summary_model(self) -> str:
        return _cfg.GEMINI_SUMMARY_MODEL

    def default_task_model(self) -> str:
        return _cfg.GEMINI_TASK_MODEL

    def _call(self, prompt: str, model: str) -> str:
        cmd = ["gemini", "-p", " ", "--model", model, "--output-format", "text", "--skip-trust"]
        result = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=_build_env(),
            encoding="utf-8",
            shell=(sys.platform == "win32"),
        )
        lines = [ln for ln in result.stdout.splitlines() if not _NOISE_RE.match(ln)]
        output = "\n".join(lines).strip()
        if result.returncode != 0:
            if _CAPACITY_RE.search(output):
                raise GeminiCapacityError(output)
            raise RuntimeError(f"Gemini CLI exit {result.returncode}: {output}")
        return output

    def invoke_summary(self, prompt: str, model: str) -> str:
        return self._call(prompt + _SUMMARY_SUFFIX, model)

    def invoke_task_classification(self, prompt: str, model: str) -> str:
        return self._call(prompt, model)

    def invoke_light(self, prompt: str, model: str) -> str:
        return self._call(prompt, model or _cfg.GEMINI_LIGHT_MODEL)
