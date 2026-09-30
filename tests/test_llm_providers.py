from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.llm.providers import get_provider
from scripts.llm.providers.anthropic import AnthropicProvider
from scripts.llm.providers.claude import ClaudeProvider


class _Runner:
    def __init__(self, payload: dict, returncode: int = 0) -> None:
        self.payload = payload
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command: list[str], **kwargs):
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            self.returncode,
            stdout=json.dumps(self.payload),
            stderr="",
        )


class ClaudeProviderTests(unittest.TestCase):
    def test_cli_is_bounded_json_and_shell_free(self) -> None:
        runner = _Runner(
            {
                "subtype": "success",
                "result": "answer",
                "usage": {"input_tokens": 12, "output_tokens": 4},
            }
        )
        provider = ClaudeProvider(executable="claude.exe", cwd=Path.cwd(), runner=runner)

        self.assertEqual(provider.invoke_light("prompt", "model"), "answer")
        command, kwargs = runner.calls[0]
        self.assertEqual(command[0], "claude.exe")
        self.assertIn("--output-format", command)
        self.assertIn("json", command)
        self.assertIn("--max-turns", command)
        self.assertIn("--max-budget-usd", command)
        self.assertIn("--no-session-persistence", command)
        self.assertEqual(kwargs["cwd"], str(Path.cwd().resolve()))
        self.assertEqual(provider.last_usage["output_tokens"], 4)

    def test_cli_invalid_json_is_a_clear_error(self) -> None:
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="not-json", stderr="")

        provider = ClaudeProvider(executable="claude.exe", cwd=Path.cwd(), runner=runner)
        with self.assertRaisesRegex(RuntimeError, "JSON non valido"):
            provider.invoke_light("prompt", "model")

    def test_cli_timeout_is_a_clear_error(self) -> None:
        def runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        provider = ClaudeProvider(executable="claude.exe", cwd=Path.cwd(), runner=runner)
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            provider.invoke_light("prompt", "model")


class _FakeMessages:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeClient:
    def __init__(self, response) -> None:
        self.messages = _FakeMessages(response)


class AnthropicProviderTests(unittest.TestCase):
    def test_api_is_lazy_and_records_usage(self) -> None:
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="answer")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=3),
        )
        client = _FakeClient(response)
        provider = AnthropicProvider(client=client)

        self.assertEqual(provider.invoke_summary("prompt", "model"), "answer")
        self.assertEqual(provider.last_usage["input_tokens"], 10)
        self.assertEqual(client.messages.calls[0]["messages"][0]["content"], "prompt")

    def test_classification_structured_output_keeps_string_interface(self) -> None:
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"task":"Alpha"}')],
            stop_reason="end_turn",
            usage={},
        )
        client = _FakeClient(response)
        provider = AnthropicProvider(client=client)

        self.assertEqual(provider.invoke_task_classification("prompt", "model"), "Alpha")
        output_config = client.messages.calls[0]["output_config"]
        self.assertEqual(output_config["format"]["type"], "json_schema")

    def test_api_truncation_is_not_silently_returned(self) -> None:
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="partial")],
            stop_reason="max_tokens",
            usage={},
        )
        with self.assertRaisesRegex(RuntimeError, "troncato"):
            AnthropicProvider(client=_FakeClient(response)).invoke_summary("prompt", "model")


class ProviderFactoryTests(unittest.TestCase):
    def test_cli_remains_default(self) -> None:
        with patch("scripts.llm.providers._cfg.LLM_PROVIDER", "claude", create=True):
            self.assertIsInstance(get_provider(), ClaudeProvider)

    def test_api_can_be_selected_explicitly(self) -> None:
        self.assertIsInstance(get_provider("anthropic"), AnthropicProvider)


if __name__ == "__main__":
    unittest.main()
