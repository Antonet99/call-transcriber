"""Provider Claude via anthropic SDK.

Variabile ambiente richiesta: ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import os

from scripts.llm.providers.base import LlmProvider

_DEFAULT_SUMMARY_MODEL = "claude-sonnet-4-6"
_DEFAULT_TASK_MODEL = "claude-sonnet-4-6"
_AUDIT_MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 16000
_MAX_TOKENS_LIGHT = 2048


class ClaudeProvider(LlmProvider):
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError("Installa anthropic: pip install anthropic")
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise EnvironmentError("ANTHROPIC_API_KEY non impostata.")
            self._client = anthropic.Anthropic(api_key=api_key)
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

    def _call(self, prompt: str, model: str, max_tokens: int = _MAX_TOKENS) -> str:
        import anthropic
        client = self._get_client()
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()

    _METADATA_SYSTEM = (
        "Sei un revisore di metadati per riassunti di call in Obsidian. "
        "Verifica frontmatter YAML, persone, sistemi e tag rispetto alla trascrizione. "
        "Restituisci solo correzioni puntuali."
    )

    _ACTION_SYSTEM = (
        "Sei un revisore di contenuto per riassunti di call. "
        "Controlla che decisioni, action item, owner, scadenze, dipendenze, numeri e citazioni "
        "siano fedeli alla trascrizione. Segnala omissioni concrete."
    )

    def invoke_summary(self, prompt: str, model: str) -> str:
        meta_feedback = self._audit(prompt, self._METADATA_SYSTEM)
        action_feedback = self._audit(prompt, self._ACTION_SYSTEM)

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

    def _audit(self, prompt: str, system: str) -> str:
        audit_prompt = f"{system}\n\n---\n\n{prompt[:4000]}"
        try:
            return self._call(audit_prompt, _AUDIT_MODEL, max_tokens=1024)
        except Exception:
            return ""

    def invoke_task_classification(self, prompt: str, model: str) -> str:
        return self._call(prompt, model, max_tokens=256)

    def invoke_light(self, prompt: str, model: str) -> str:
        target = model or _AUDIT_MODEL
        return self._call(prompt, target, max_tokens=_MAX_TOKENS_LIGHT)
