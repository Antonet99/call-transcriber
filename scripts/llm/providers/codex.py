from scripts.llm.providers.base import LlmProvider


class CodexProvider(LlmProvider):
    def default_summary_model(self) -> str:
        return "codex"

    def default_task_model(self) -> str:
        return "codex"

    def is_available(self) -> bool:
        return False

    def invoke_summary(self, prompt: str, model: str) -> str:
        raise NotImplementedError(
            "Provider Codex non ancora implementato: definire prima CLI/API e modalita' headless."
        )

    def invoke_task_classification(self, prompt: str, model: str) -> str:
        raise NotImplementedError(
            "Provider Codex non ancora implementato: definire prima CLI/API e modalita' headless."
        )

    def invoke_light(self, prompt: str, model: str) -> str:
        raise NotImplementedError(
            "Provider Codex non ancora implementato: definire prima CLI/API e modalita' headless."
        )
