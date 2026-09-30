"""Provider Anthropic Messages API, opzionale rispetto alla CLI."""
from __future__ import annotations

import json
import os
from typing import Any

import scripts.settings as _cfg
from scripts.llm.providers.base import LlmProvider


def _setting(name: str, default: Any) -> Any:
    return getattr(_cfg, name, default)


def _load_sdk() -> Any:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "Il provider Anthropic API richiede la dipendenza opzionale 'anthropic'. "
            "Installare l'extra del progetto prima di selezionarlo."
        ) from exc
    return anthropic


def _value(response: Any, key: str, default: Any = None) -> Any:
    if isinstance(response, dict):
        return response.get(key, default)
    return getattr(response, key, default)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    for method_name in ("model_dump", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            dumped = method()
            if isinstance(dumped, dict):
                return dict(dumped)
    return {
        key: getattr(value, key)
        for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        if hasattr(value, key)
    }


def _text_content(response: Any) -> str:
    content = _value(response, "content", [])
    chunks: list[str] = []
    for block in content or []:
        block_type = _value(block, "type")
        if block_type == "text":
            text = _value(block, "text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


class AnthropicProvider(LlmProvider):
    """Adapter stateless per l'API Messages ufficiale Anthropic."""

    def __init__(self, *, client: Any | None = None) -> None:
        self._client_instance = client
        self.last_usage: dict[str, Any] = {}
        self.last_response: Any = None

    def is_available(self) -> bool:
        try:
            _load_sdk()
        except RuntimeError:
            return False
        return True

    def default_summary_model(self) -> str:
        return _setting("ANTHROPIC_SUMMARY_MODEL", _setting("CLAUDE_SUMMARY_MODEL", "claude-sonnet-5"))

    def default_task_model(self) -> str:
        return _setting("ANTHROPIC_TASK_MODEL", _setting("CLAUDE_TASK_MODEL", "claude-sonnet-5"))

    def default_light_model(self) -> str:
        return _setting("ANTHROPIC_LIGHT_MODEL", _setting("CLAUDE_LIGHT_MODEL", "claude-sonnet-5"))

    def _client(self) -> Any:
        if self._client_instance is not None:
            return self._client_instance

        sdk = _load_sdk()
        kwargs: dict[str, Any] = {}
        api_key = _setting("ANTHROPIC_API_KEY", None) or os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            kwargs["api_key"] = api_key
        base_url = _setting("ANTHROPIC_BASE_URL", None) or os.environ.get("ANTHROPIC_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        timeout = _setting("ANTHROPIC_TIMEOUT_SECONDS", None)
        if timeout is not None:
            kwargs["timeout"] = float(timeout)
        try:
            return sdk.Anthropic(**kwargs)
        except Exception as exc:
            raise RuntimeError(f"Inizializzazione Anthropic API fallita: {exc}") from exc

    def _output_config(self, role: str) -> dict[str, Any] | None:
        effort = _setting(f"ANTHROPIC_{role.upper()}_EFFORT", None)
        if effort is None:
            effort = _setting(f"CLAUDE_{role.upper()}_EFFORT", None)
        return {"effort": str(effort)} if effort else None

    def _request(self, prompt: str, model: str, role: str, *, output_config: dict[str, Any] | None = None) -> str:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": int(_setting(f"ANTHROPIC_{role.upper()}_MAX_TOKENS", 16384 if role == "summary" else 2048)),
            "messages": [{"role": "user", "content": prompt}],
        }
        configured_output = self._output_config(role)
        if configured_output:
            kwargs["output_config"] = configured_output
        if output_config:
            kwargs["output_config"] = {
                **(kwargs.get("output_config") or {}),
                **output_config,
            }

        try:
            response = self._client().messages.create(**kwargs)
        except Exception as exc:
            raise RuntimeError(f"Anthropic API request fallita: {exc}") from exc

        self.last_response = response
        self.last_usage = _as_dict(_value(response, "usage", {}))
        stop_reason = _value(response, "stop_reason")
        if stop_reason in {"max_tokens", "model_context_window_exceeded"}:
            raise RuntimeError(f"Anthropic API ha troncato la risposta ({stop_reason}).")
        if stop_reason == "refusal":
            details = _value(response, "stop_details", "rifiuto del provider")
            raise RuntimeError(f"Anthropic API ha rifiutato la richiesta: {details}")

        text = _text_content(response)
        if not text:
            raise RuntimeError("Anthropic API non ha restituito contenuto testuale.")
        return text

    def invoke_summary(self, prompt: str, model: str) -> str:
        return self._request(prompt, model or self.default_summary_model(), "summary")

    def invoke_task_classification(self, prompt: str, model: str) -> str:
        if not _setting("ANTHROPIC_STRUCTURED_CLASSIFICATION", True):
            return self._request(prompt, model or self.default_task_model(), "task")

        schema = {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Nome esatto della task o NESSUNA_TASK.",
                }
            },
            "required": ["task"],
            "additionalProperties": False,
        }
        text = self._request(
            prompt,
            model or self.default_task_model(),
            "task",
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Anthropic API ha restituito classificazione JSON non valida.") from exc
        task = payload.get("task") if isinstance(payload, dict) else None
        if not isinstance(task, str) or not task.strip():
            raise RuntimeError("Anthropic API ha restituito una classificazione senza task.")
        return task.strip()

    def invoke_light(self, prompt: str, model: str) -> str:
        return self._request(prompt, model or self.default_light_model(), "light")
