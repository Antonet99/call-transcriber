"""Provider Gemini via google-genai SDK.

Variabile ambiente richiesta: GOOGLE_API_KEY (o GEMINI_API_KEY come alias).

I nomi dei modelli qui sotto sono i nomi API ufficiali. Se il progetto usava
alias della CLI (gemini-3.1-pro-preview, gemini-3-flash-preview), aggiornarli
con i nomi SDK correnti al momento del deploy.
"""
from __future__ import annotations

import os

from scripts.llm.providers.base import LlmProvider

_DEFAULT_SUMMARY_MODEL = "gemini-2.5-pro-preview-05-06"
_DEFAULT_TASK_MODEL = "gemini-2.0-flash"
_DEFAULT_LIGHT_MODEL = "gemini-2.0-flash"

_CAPACITY_ERRORS = {
    "RESOURCE_EXHAUSTED",
    "MODEL_CAPACITY_EXHAUSTED",
    "No capacity available for model",
    "RetryableQuotaError",
}


class GeminiCapacityError(RuntimeError):
    pass


class GeminiProvider(LlmProvider):
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError:
                raise ImportError("Installa google-genai: pip install google-genai")

            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                raise EnvironmentError("GEMINI_API_KEY o GOOGLE_API_KEY non impostata.")
            self._client = genai.Client(api_key=api_key)
        return self._client

    def default_summary_model(self) -> str:
        return _DEFAULT_SUMMARY_MODEL

    def default_task_model(self) -> str:
        return _DEFAULT_TASK_MODEL

    def is_available(self) -> bool:
        try:
            self._get_client()
            return True
        except Exception:
            return False

    def _call(self, prompt: str, model: str) -> str:
        try:
            from google.genai import errors as genai_errors
        except ImportError:
            genai_errors = None  # type: ignore

        client = self._get_client()
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            return response.text.strip()
        except Exception as exc:
            msg = str(exc)
            if any(e in msg for e in _CAPACITY_ERRORS):
                raise GeminiCapacityError(msg) from exc
            raise

    _METADATA_AUDITOR_PROMPT = (
        "Sei un revisore di metadati per riassunti di call in Obsidian. "
        "Verifica che frontmatter YAML, persone, sistemi e tag derivino dalla trascrizione, "
        "che il tag 'call' sia presente e che i nomi non vengano inventati. "
        "Restituisci solo correzioni puntuali, nessuna riscrittura."
    )

    _ACTION_AUDITOR_PROMPT = (
        "Sei un revisore di contenuto per riassunti di call. "
        "Controlla che decisioni, action item, owner, scadenze, dipendenze, numeri, date "
        "e citazioni brevi siano fedeli alla trascrizione. "
        "Segnala omissioni concrete senza riscrivere tutto il riassunto."
    )

    def invoke_summary(self, prompt: str, model: str) -> str:
        meta_feedback = self._audit(prompt, self._METADATA_AUDITOR_PROMPT, model)
        action_feedback = self._audit(prompt, self._ACTION_AUDITOR_PROMPT, model)

        augmented = prompt
        if meta_feedback or action_feedback:
            augmented += (
                "\n\n---\n\nRevisioni pre-elaborazione da applicare dove pertinenti:\n"
            )
            if meta_feedback:
                augmented += f"\nMetadati: {meta_feedback}"
            if action_feedback:
                augmented += f"\nContenuto: {action_feedback}"

        return self._call(augmented, model)

    def _audit(self, main_prompt: str, system: str, model: str) -> str:
        audit_prompt = f"{system}\n\n---\n\n{main_prompt[:4000]}"
        try:
            return self._call(audit_prompt, _DEFAULT_LIGHT_MODEL)
        except Exception:
            return ""

    def invoke_task_classification(self, prompt: str, model: str) -> str:
        return self._call(prompt, model)

    def invoke_light(self, prompt: str, model: str) -> str:
        return self._call(prompt, model or _DEFAULT_LIGHT_MODEL)
