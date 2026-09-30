
"""Factory degli adapter LLM."""
from __future__ import annotations

import scripts.settings as _cfg
from scripts.llm.providers.base import LlmProvider


def get_provider(name: str | None = None) -> LlmProvider:
    """Crea il provider configurato, mantenendo Claude CLI come default."""
    configured = name if name is not None else getattr(_cfg, "LLM_PROVIDER", "claude")
    provider_name = str(configured).strip().casefold()

    if provider_name in {"claude", "claude-cli", "cli"}:
        from scripts.llm.providers.claude import ClaudeProvider

        return ClaudeProvider()
    if provider_name in {"anthropic", "anthropic-api", "api"}:
        from scripts.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider()
    raise ValueError(
        f"LLM_PROVIDER non supportato: {configured!r}. Valori ammessi: claude, anthropic."
    )


__all__ = ["get_provider"]
