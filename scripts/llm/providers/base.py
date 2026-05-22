from __future__ import annotations

from abc import ABC, abstractmethod


class LlmProvider(ABC):
    @abstractmethod
    def default_summary_model(self) -> str: ...

    @abstractmethod
    def default_task_model(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def invoke_summary(self, prompt: str, model: str) -> str:
        """Genera un riassunto Markdown da prompt+trascrizione."""
        ...

    @abstractmethod
    def invoke_task_classification(self, prompt: str, model: str) -> str:
        """Classifica la call in una task esistente."""
        ...

    @abstractmethod
    def invoke_light(self, prompt: str, model: str) -> str:
        """Chiamata leggera per Kanban update (low effort/cost)."""
        ...
