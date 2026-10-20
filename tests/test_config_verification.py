from contextlib import ExitStack
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from scripts import config_verification as cv, process_call as pc, settings, user_settings
from scripts.llm.providers.claude import ClaudeProvider


class ConfigurationVerificationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(cv, "context", return_value="synthetic-account"))
        self.target = self.root / "verification.json"
        self.stack.enter_context(patch.object(cv, "report_path", return_value=self.target))
        self.values = user_settings.snapshot()
        self.original = dict(self.values)
        self.calls = []

        def runner(cmd, **kwargs):
            self.calls.append((cmd, kwargs))
            model = cmd[cmd.index("--model") + 1]
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "OK", "subtype": "success", "modelUsage": {model: {"inputTokens": 1}}}), "")

        self.runner = Mock(side_effect=runner)
        self.provider = ClaudeProvider(executable="synthetic-claude", cwd=self.root, runner=self.runner)
        self.client = Mock()
        self.client.models.list.return_value = SimpleNamespace(data=[SimpleNamespace(id=self.values["GROQ_WHISPER_MODEL"])])
        self.client.audio.transcriptions.create.return_value = ""
        self.factory = Mock()
        self.factory.return_value.__enter__ = Mock(return_value=self.client)
        self.factory.return_value.__exit__ = Mock(return_value=False)

    def verify(self, **kwargs):
        return cv.verify(self.values, provider=self.provider, groq_factory=self.factory, **kwargs)

    def test_deduplicates_pairs_and_checks_summary_agents_with_real_adapter(self):
        for role in ("SUMMARY", "TASK", "LIGHT", "SUBAGENT"):
            self.values[f"CLAUDE_{role}_MODEL"] = "synthetic-model"
        report = self.verify()
        self.assertEqual(self.runner.call_count, 2)
        first, second = [call[0] for call in self.calls]
        self.assertNotIn("--agents", first)
        agents = json.loads(second[second.index("--agents") + 1])
        self.assertEqual(set(agents), {"call-metadata-auditor", "call-action-auditor"})
        self.assertEqual({agent["model"] for agent in agents.values()}, {"synthetic-model"})
        self.assertTrue(any(item["status"] == "parziale" for item in report["results"]))
        for command, args in self.calls:
            self.assertLessEqual(args["timeout"], 60)
            self.assertEqual(command[command.index("--max-budget-usd") + 1], "0.05")
            self.assertIn("--no-session-persistence", command)
            self.assertEqual(args["cwd"], str(self.root.resolve()))
        self.assertEqual(self.factory.call_args.kwargs["max_retries"], 0)
        self.client.audio.transcriptions.create.assert_not_called()
        self.assertEqual(user_settings.snapshot(), self.original)

    def test_distinct_pairs_are_all_probed(self):
        self.values["CLAUDE_TASK_EFFORT"] = "low"
        self.values["CLAUDE_LIGHT_EFFORT"] = "high"
        self.values["CLAUDE_SUBAGENT_EFFORT"] = "max"
        self.verify()
        self.assertEqual(self.runner.call_count, 5)

    def test_local_failure_prevents_all_calls(self):
        self.values["CLAUDE_SUMMARY_MODEL"] = "claude-haiku-4-5"
        report = self.verify()
        self.assertEqual(report["results"][-1]["status"], "fallito")
        self.runner.assert_not_called()
        self.factory.assert_not_called()

    def test_invalid_type_returns_local_failure(self):
        self.values["CLAUDE_TIMEOUT_SECONDS"] = -1
        self.assertEqual(self.verify()["results"][0]["status"], "fallito")
        self.runner.assert_not_called()

    def test_xhigh_on_known_unsupported_model_fails_locally(self):
        self.values.update(CLAUDE_SUMMARY_MODEL="claude-sonnet-4-6", CLAUDE_SUMMARY_EFFORT="xhigh")
        self.assertTrue(any(item["status"] == "fallito" for item in self.verify()["results"]))
        self.runner.assert_not_called()

    def test_unknown_model_is_not_replaced(self):
        self.values["CLAUDE_SUMMARY_MODEL"] = "claude-sonnet-5.5"
        self.verify()
        self.assertIn("claude-sonnet-5.5", self.calls[0][0])

    def test_fallback_fails_and_effort_is_never_assumed_attested(self):
        self.runner.side_effect = None
        self.runner.return_value = subprocess.CompletedProcess([], 0, json.dumps({"result": "OK", "modelUsage": {"unexpected-model": {}}}), "")
        report = self.verify()
        self.assertTrue(any(item["status"] == "fallito" and "fallback" in item["message"] for item in report["results"]))

    def test_missing_model_telemetry_is_partial(self):
        self.runner.side_effect = None
        self.runner.return_value = subprocess.CompletedProcess([], 0, '{"result":"OK"}', "")
        report = self.verify()
        self.assertEqual(report["results"][1]["status"], "parziale")

    def test_ignored_effort_warning_is_failure_even_with_successful_output(self):
        self.runner.side_effect = None
        self.runner.return_value = subprocess.CompletedProcess([], 0, '{"result":"OK"}', "Warning: effort unsupported; ignoring effort")
        report = self.verify()
        self.assertEqual(report["results"][1]["status"], "fallito")
        self.assertIn("effort ignorato", report["results"][1]["message"])

    def test_provider_errors_are_sanitized_without_retry(self):
        for error, expected in [("401 secret-token", "Autenticazione"), ("unsupported model secret-token", "Modello"),
                                ("effort unsupported secret-token", "Effort"), ("budget exceeded secret-token", "Budget"),
                                ("connection refused secret-token", "Connessione")]:
            with self.subTest(error=error):
                self.runner.reset_mock()
                self.runner.side_effect = RuntimeError(error)
                report = self.verify()
                self.assertEqual(self.runner.call_count, 1)
                self.assertIn(expected, report["results"][1]["message"])
                self.assertNotIn("secret-token", json.dumps(report))

    def test_timeout_has_no_retry(self):
        self.runner.side_effect = subprocess.TimeoutExpired("claude", 60)
        report = self.verify()
        self.assertIn("Timeout", report["results"][1]["message"])
        self.assertEqual(self.runner.call_count, 1)

    def test_groq_catalog_does_not_prove_audio(self):
        report = self.verify()
        self.assertEqual(report["results"][-1]["status"], "non verificato")
        self.client.audio.transcriptions.create.assert_not_called()

    def test_audio_is_synthetic_and_uses_pipeline_parameters(self):
        report = self.verify(audio=True)
        args = self.client.audio.transcriptions.create.call_args.kwargs
        with wave.open(io.BytesIO(args["file"][1]), "rb") as wav:
            self.assertEqual(wav.getnframes(), 16000)
            self.assertEqual(wav.readframes(16000), b"\0\0" * 16000)
        self.assertEqual(args["temperature"], 0.0)
        self.assertEqual(args["response_format"], "text")
        self.assertEqual(report["results"][-1]["status"], "verificato")

    def test_absent_groq_model_does_not_call_audio(self):
        self.client.models.list.return_value = SimpleNamespace(data=[])
        report = self.verify(audio=True)
        self.assertEqual(report["results"][-1]["status"], "fallito")
        self.client.audio.transcriptions.create.assert_not_called()

    def test_cache_expires_and_changes_invalidate_it(self):
        report = self.verify()
        self.target.write_text(json.dumps(report), encoding="utf-8")
        self.assertIsNotNone(cv.cached(self.values))
        changed = dict(self.values, CLAUDE_SUMMARY_EFFORT="high")
        self.assertIsNone(cv.cached(changed))
        with patch.object(cv, "context", return_value="other-account"):
            self.assertIsNone(cv.cached(self.values))
        with patch.object(cv.time, "time", return_value=report["checked_at"] + cv.TTL_SECONDS + 1):
            self.assertIsNone(cv.cached(self.values))

    def test_menu_verifies_unsaved_draft_and_does_not_save_preferences(self):
        with patch("scripts.terminal.select_item", side_effect=["CLAUDE_SUMMARY_MODEL", "verify", None, "discard"]), \
                patch("scripts.terminal.input_line", return_value="unsaved-model"), \
                patch.object(cv, "show_verification") as verify, patch.object(user_settings, "save") as save:
            user_settings.show_menu()
        self.assertEqual(verify.call_args.args[0]["CLAUDE_SUMMARY_MODEL"], "unsaved-model")
        self.assertTrue(verify.call_args.kwargs["draft"])
        save.assert_not_called()
        self.assertNotEqual(settings.CLAUDE_SUMMARY_MODEL, "unsaved-model")

    def test_opening_saving_and_cancelling_do_not_call_remote(self):
        with patch.object(cv, "verify") as remote, patch("scripts.terminal.select_item", return_value=None):
            user_settings.show_menu()
            cv.show_verification(self.values)
            remote.assert_not_called()
        with patch.object(cv, "verify") as remote, patch("scripts.terminal.select_item", return_value="save"), patch.object(user_settings, "save"):
            user_settings.show_menu()
            remote.assert_not_called()

    def test_manual_run_blocks_before_pipeline_or_whisper(self):
        state = {"config": {"runtime_settings": self.values, "provider": settings.LLM_PROVIDER,
                           "summary_model": self.values["CLAUDE_SUMMARY_MODEL"], "task_model": self.values["CLAUDE_TASK_MODEL"],
                           "transcription_model": self.values["GROQ_WHISPER_MODEL"]}}
        with patch("scripts.terminal.interactive", return_value=False), patch.object(pc, "_process_configured_job") as pipeline, \
                patch("scripts.transcribe_with_groq.transcribe") as whisper:
            with self.assertRaisesRegex(RuntimeError, "allow-unverified-config"):
                pc._process_job(self.root, state)
            pipeline.assert_not_called()
            whisper.assert_not_called()
            pc._process_job(self.root, state, allow_unverified_config=True)
            pipeline.assert_called_once()

    def test_resume_checks_checkpoint_and_explicit_models(self):
        checkpoint = dict(self.values, CLAUDE_SUMMARY_EFFORT="high")
        state = {"config": {"runtime_settings": checkpoint, "provider": settings.LLM_PROVIDER,
                           "summary_model": "checkpoint-model", "task_model": "checkpoint-task", "transcription_model": "checkpoint-whisper"}}
        with patch.object(cv, "require_ready") as gate, patch.object(pc, "_process_configured_job"):
            pc._process_job(self.root, state)
        checked = gate.call_args.args[0]
        self.assertEqual(checked["CLAUDE_SUMMARY_MODEL"], "checkpoint-model")
        self.assertEqual(checked["CLAUDE_SUMMARY_EFFORT"], "high")
        self.assertEqual(checked["GROQ_WHISPER_MODEL"], "checkpoint-whisper")
        self.assertEqual(user_settings.snapshot(), self.values)

    def test_verifying_from_gate_never_starts_pipeline_and_cancel_blocks(self):
        with patch("scripts.terminal.interactive", return_value=True), \
                patch("scripts.terminal.select_item", side_effect=["verify", None]), \
                patch.object(cv, "show_verification") as verify:
            with self.assertRaisesRegex(RuntimeError, "annullata"):
                cv.require_ready(self.values)
        verify.assert_called_once_with(self.values)

    def test_partial_report_still_requires_explicit_override(self):
        self.target.write_text(json.dumps(self.verify()), encoding="utf-8")
        with patch("scripts.terminal.interactive", return_value=False):
            with self.assertRaises(RuntimeError):
                cv.require_ready(self.values)
            cv.require_ready(self.values, allow_unverified=True)

    def test_first_error_stops_remaining_distinct_claude_probes(self):
        self.values["CLAUDE_TASK_EFFORT"] = "low"
        self.runner.side_effect = RuntimeError("401 invalid account")
        report = self.verify()
        self.assertEqual(self.runner.call_count, 1)
        self.assertTrue(any(item["status"] == "non verificato" and "TASK" in item["phase"] for item in report["results"]))

    def test_failed_preflight_can_explicitly_take_corrected_preferences(self):
        source = self.root / "synthetic.m4a"
        source.write_bytes(b"synthetic")
        vault = self.root / "synthetic-vault"
        with patch.object(pc, "wait_stable"), patch("scripts.terminal.interactive", return_value=False), \
                patch.object(pc, "_process_configured_job") as pipeline:
            with self.assertRaises(cv.ConfigurationNotReady):
                pc.process(source, root=vault)
            with patch.object(settings, "CLAUDE_SUMMARY_MODEL", "corrected-model"):
                with self.assertRaisesRegex(cv.ConfigurationNotReady, "use-current-config"):
                    pc.process(source, root=vault)
                pc.process(source, root=vault, use_current_config=True, allow_unverified_config=True)
            self.assertEqual(pipeline.call_args.args[1]["config"]["summary_model"], "corrected-model")
            self.assertTrue(source.exists())

    def test_pending_checkpoint_choice_is_explicit(self):
        saved, current = {"model": "old"}, {"model": "new"}
        with patch("scripts.terminal.interactive", return_value=True), patch("scripts.terminal.select_item", return_value="saved"):
            self.assertEqual(cv.pending_config(saved, current), saved)
        with patch("scripts.terminal.interactive", return_value=True), patch("scripts.terminal.select_item", return_value="current"):
            self.assertEqual(cv.pending_config(saved, current), current)

    def test_reports_are_browsable_by_phase(self):
        report = {"results": [cv._result("Claude", "parziale", "effort non attestato")]}
        with patch("scripts.terminal.select_item", side_effect=[0, None, None]) as select:
            cv.show_report(report, title="Esiti")
        self.assertIn("effort non attestato", select.call_args_list[1].kwargs["message"])
