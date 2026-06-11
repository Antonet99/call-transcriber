"""Provider GitHub Copilot SDK."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import importlib.util
import os
from pathlib import Path

import scripts.settings as _cfg
from scripts.llm.providers.base import LlmProvider

_ROOT = Path(__file__).resolve().parents[3]


def _github_token() -> str | None:
    return (
        os.environ.get("COPILOT_GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or None
    )

_SYSTEM_MESSAGE = (
    "Sei il provider LLM della pipeline Call Transcriber. "
    "Rispondi sempre e solo con il contenuto richiesto dal prompt. "
    "Non creare, modificare o salvare file: il codice chiamante si occupa di tutto."
)

_SUMMARY_SUFFIX = """

---

Controlli interni obbligatori prima della risposta finale:
- verifica frontmatter YAML, persone, sistemi e tag rispetto alla trascrizione;
- verifica decisioni, action item, dipendenze, domande aperte, numeri, date e citazioni;
- verifica che il riassunto sia leggibile e contestualizzato, non una lista di frammenti scollegati;
- integra solo correzioni supportate dalla trascrizione;
- non riportare log, ragionamenti, audit o note operative.

La risposta finale deve contenere esclusivamente il Markdown del riassunto richiesto.
"""

_METADATA_AUDIT_PROMPT = """
Sei un revisore di metadati per riassunti di call in Obsidian.

Controlla il DRAFT rispetto alla TRASCRIZIONE nel prompt originale.
Verifica:
- frontmatter YAML valido e coerente;
- persone dedotte dalla call, senza nomi inventati;
- sistemi, tool, repository e piattaforme citati davvero;
- tag in kebab-case, con tag `call` presente.

Rispondi solo con:
- OK se non servono correzioni;
- oppure un elenco breve e puntuale di correzioni necessarie.
"""

_ACTION_AUDIT_PROMPT = """
Sei un revisore di contenuto per riassunti di call.

Controlla il DRAFT rispetto alla TRASCRIZIONE nel prompt originale.
Verifica:
- decisioni prese;
- action item, owner, scadenze e dipendenze;
- domande aperte, blocchi, rischi;
- numeri, date, ticket, repository e citazioni brevi.
- presenza di un contesto iniziale sufficiente a capire progetto, problema e obiettivo della call;
- coerenza narrativa tra contesto, dettagli tecnici, decisioni e prossimi passi.

Rispondi solo con:
- OK se non servono correzioni;
- oppure un elenco breve e puntuale di correzioni necessarie.
"""

_REVISION_PROMPT = """
Correggi il DRAFT usando solo le correzioni degli AUDIT e il prompt originale.

Regole:
- mantieni il formato richiesto dal prompt originale;
- non inventare dettagli;
- restituisci esclusivamente il Markdown finale del riassunto;
- non includere spiegazioni, audit, log o blocchi di codice.
"""


def _is_ok_audit(text: str) -> bool:
    return text.strip().upper() in {"OK", "OK."}


def _summary_models(model: str) -> list[str]:
    primary = model or _cfg.COPILOT_SUMMARY_MODEL
    fallback = _cfg.COPILOT_SUMMARY_FALLBACK_MODEL
    models = [primary]
    if fallback and fallback not in models:
        models.append(fallback)
    return models


class CopilotProvider(LlmProvider):
    def default_summary_model(self) -> str:
        return _cfg.COPILOT_SUMMARY_MODEL

    def default_task_model(self) -> str:
        return _cfg.COPILOT_TASK_MODEL

    def is_available(self) -> bool:
        return importlib.util.find_spec("copilot") is not None

    async def _call_async(
        self,
        prompt: str,
        model: str,
        timeout: float,
        reasoning_effort: str | None = None,
    ) -> str:
        from copilot import CopilotClient
        from copilot.generated.session_events import AssistantMessageData
        from copilot.session import PermissionHandler

        client = CopilotClient()
        try:
            session = await client.create_session(
                on_permission_request=PermissionHandler.approve_all,
                model=model,
                reasoning_effort=reasoning_effort or _cfg.COPILOT_REASONING_EFFORT,
                system_message={"mode": "replace", "content": _SYSTEM_MESSAGE},
                available_tools=[],
                enable_config_discovery=False,
                streaming=False,
                include_sub_agent_streaming_events=False,
                working_directory=str(_ROOT),
                github_token=_github_token(),
            )
            event = await session.send_and_wait(prompt, timeout=timeout)
            if not event:
                return ""
            data = event.data
            if isinstance(data, AssistantMessageData):
                return data.content.strip()
            return ""
        finally:
            with suppress(Exception):
                await client.stop()

    def _call(
        self,
        prompt: str,
        model: str,
        timeout: float,
        reasoning_effort: str | None = None,
    ) -> str:
        return asyncio.run(self._call_async(prompt, model, timeout, reasoning_effort))

    def invoke_summary(self, prompt: str, model: str) -> str:
        last_error: Exception | None = None
        summary_model = _summary_models(model)[0]
        draft = ""
        for candidate in _summary_models(model):
            try:
                draft = self._call(
                    prompt + _SUMMARY_SUFFIX,
                    candidate,
                    _cfg.COPILOT_SUMMARY_TIMEOUT_SECONDS,
                )
                summary_model = candidate
                break
            except Exception as exc:
                last_error = exc
        if not draft and last_error:
            raise last_error
        if not draft:
            return ""

        audit_input = f"PROMPT ORIGINALE:\n{prompt}\n\n---\n\nDRAFT:\n{draft}"
        metadata_audit = self._call(
            f"{_METADATA_AUDIT_PROMPT}\n\n{audit_input}",
            _cfg.COPILOT_AUDIT_MODEL,
            _cfg.COPILOT_LIGHT_TIMEOUT_SECONDS,
        )
        action_audit = self._call(
            f"{_ACTION_AUDIT_PROMPT}\n\n{audit_input}",
            _cfg.COPILOT_AUDIT_MODEL,
            _cfg.COPILOT_LIGHT_TIMEOUT_SECONDS,
        )

        if _is_ok_audit(metadata_audit) and _is_ok_audit(action_audit):
            return draft

        revision_prompt = (
            f"{_REVISION_PROMPT}\n\n"
            f"PROMPT ORIGINALE:\n{prompt}\n\n---\n\n"
            f"DRAFT:\n{draft}\n\n---\n\n"
            f"AUDIT METADATI:\n{metadata_audit or 'OK'}\n\n---\n\n"
            f"AUDIT CONTENUTO:\n{action_audit or 'OK'}"
        )
        revised = self._call(
            revision_prompt,
            summary_model,
            _cfg.COPILOT_SUMMARY_TIMEOUT_SECONDS,
        )
        return revised or draft

    def invoke_task_classification(self, prompt: str, model: str) -> str:
        return self._call(
            prompt,
            model or self.default_task_model(),
            _cfg.COPILOT_LIGHT_TIMEOUT_SECONDS,
            _cfg.COPILOT_LIGHT_REASONING_EFFORT,
        )

    def invoke_light(self, prompt: str, model: str) -> str:
        return self._call(
            prompt,
            model or _cfg.COPILOT_LIGHT_MODEL,
            _cfg.COPILOT_LIGHT_TIMEOUT_SECONDS,
            _cfg.COPILOT_LIGHT_REASONING_EFFORT,
        )
