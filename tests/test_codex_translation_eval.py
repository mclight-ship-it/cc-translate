"""Offline-only tests. Never launch Codex, read an account, or contact a model."""

import contextlib
import copy
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import unittest
import uuid
from unittest.mock import Mock, patch

if importlib.util.find_spec("tomllib") is None:
    raise unittest.SkipTest("Existing Codex provider imports stdlib tomllib; run these tests with Python 3.11+.")

from cc_direction import direction_prompt
from cc_prompts import CODE_EXPLAIN_PROMPT, DICTIONARY_PROMPT, SYSTEM_SUFFIX
from cc_providers.base import ProviderResult
from cc_providers.codex_appserver import CodexAppServerTransport
from cc_providers.codex_config import CODEX_CONFIG_OVERRIDES
from cc_summary import codex_summary_instruction
from tools import codex_translation_eval as evaluator


MODEL = "gpt-catalog-test-model"
OTHER = "gpt-catalog-second-model"
SAFE_CONFIG = {
    "config": {"model_provider": "openai"},
    "origins": {},
    "layers": [{"name": {"type": "user", "file": str(Path.cwd() / "config.toml")},
                "version": "fixture-version", "config": {"model_provider": "openai"},
                "disabledReason": None}],
}
SAFE_ACCOUNT = {"account": {"type": "chatgpt", "email": "synthetic-account-do-not-report", "planType": "plus"},
                "requiresOpenaiAuth": True}
CATALOG = {"data": [{"id": MODEL, "model": MODEL, "isHidden": False}], "nextCursor": None}


def case(name):
    return next(value for value in evaluator.CORPUS if value.id == name)


class FakeWire(CodexAppServerTransport):
    """Exercise the real streaming parser and safety policy, replacing only I/O."""

    def __init__(self):
        super().__init__("unused-native-binary", str(Path.cwd()), env={})
        self.sent = []
        self.messages = []
        self.stopped = 0
        self.config = copy.deepcopy(SAFE_CONFIG)
        self.account = copy.deepcopy(SAFE_ACCOUNT)
        self.catalog = copy.deepcopy(CATALOG)
        self.thread_result = {"thread": {"id": "thread-1"}, "model": MODEL, "modelProvider": "openai"}
        self.extra_events = []
        self.content = "图书馆今天提前关门。"
        self.cli_sha256 = "0" * 64

    def _version_supported(self, cancel_event=None):
        return True

    def _start_process(self, request, *, cancel_event=None):
        self._proc = object()
        self._profile = request.model
        return self._proc

    def _stop_process(self, proc):
        self.stopped += 1
        self._proc = None

    @staticmethod
    def _process_running(proc):
        return proc is not None

    def _send(self, proc, method, params=None, request_id=None):
        self.sent.append((method, copy.deepcopy(params)))
        results = {
            "initialize": {},
            "config/read": self.config,
            "account/read": self.account,
            "model/list": self.catalog,
            "hooks/list": {"data": [{"cwd": str(Path.cwd()), "hooks": []}]},
            "thread/start": self.thread_result,
            "turn/start": {"turn": {"id": "turn-1"}},
        }
        if request_id is not None:
            self.messages.append({"id": request_id, "result": results[method]})
        if method == "turn/start":
            identity = {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1"}
            self.messages.extend(self.extra_events)
            self.messages.extend([
                {"method": "item/agentMessage/delta", "params": dict(identity, delta=self.content)},
                {"method": "item/completed", "params": dict(identity, item={
                    "type": "agentMessage", "id": "item-1", "text": self.content, "phase": "final_answer"})},
                {"method": "turn/completed", "params": dict(identity, turn={
                    "id": "turn-1", "status": "completed"})},
            ])

    def _next_message(self, proc, output_queue, deadline, cancel_event):
        if not self.messages:
            return "eof", None
        return "line", json.dumps(self.messages.pop(0), ensure_ascii=False)


class WireTransport(evaluator.GuardedTransportMixin, FakeWire):
    def __init__(self):
        super().__init__()
        self.setup_evaluation(30)


class OfflineAndSchedulingTests(unittest.TestCase):
    def invoke(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = evaluator.main(args)
        return code, json.loads(output.getvalue())

    def test_default_offline_makes_no_cli_discovery_or_probe(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess")), \
                patch.object(subprocess, "run", side_effect=AssertionError("subprocess")), \
                patch.object(evaluator, "inspect_catalog", side_effect=AssertionError("probe")), \
                patch.object(evaluator, "guard_environment", side_effect=AssertionError("environment")):
            code, report = self.invoke([])
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "offline_plan")
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["results"], [])
        self.assertEqual(report["models_selected"], [])
        self.assertEqual(report["request_count"], 17)
        self.assertTrue(all("input" not in row for row in report["schedule"]))
        self.assertEqual(report["cases"][0]["text"], case("short").text)
        self.assertIn("<data>", report["cases"][0]["prompts"]["production"])

    def test_python39_syntax_and_missing_tomllib_skip_before_provider_import(self):
        for path in (Path(__file__), Path(evaluator.__file__)):
            ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 9))
        original = importlib.util.find_spec
        with patch.object(importlib.util, "find_spec",
                          side_effect=lambda name: None if name == "tomllib" else original(name)):
            with self.assertRaises(unittest.SkipTest):
                runpy.run_path(str(Path(__file__)), run_name="older_python_probe")

    def test_consent_alone_still_offline(self):
        with patch.object(evaluator, "inspect_catalog") as probe:
            code, report = self.invoke(["--consent-chatgpt", "--model", MODEL])
        probe.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "offline_plan")

    def test_redirected_legacy_windows_stdout_preserves_unicode_as_json(self):
        output = io.BytesIO()
        stream = io.TextIOWrapper(output, encoding="ascii")
        with contextlib.redirect_stdout(stream):
            code = evaluator.main(["--case", "numbers"])
        stream.flush()
        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["cases"][0]["text"], case("numbers").text)
        stream.detach()

    def test_live_requires_consent_absolute_cli_and_explicit_models(self):
        for args, expected in [
            (["--live"], "explicit_consent_required"),
            (["--inspect"], "explicit_consent_required"),
            (["--live", "--consent-chatgpt"], "absolute_official_cli_required"),
            (["--live", "--consent-chatgpt", "--codex", "relative.exe"], "absolute_official_cli_required"),
            (["--live", "--consent-chatgpt", "--codex", str(Path.cwd() / "codex.exe")],
             "explicit_catalog_models_required"),
        ]:
            with self.subTest(args=args), patch.object(evaluator, "inspect_catalog") as probe:
                code, report = self.invoke(args)
            self.assertEqual(code, 2)
            self.assertEqual(report["error"], expected)
            probe.assert_not_called()

    def test_bounds_and_invalid_models_fail_before_probe(self):
        for options in [
            ["--repeats", "0"], ["--repeats", "4"], ["--max-requests", "65"],
            ["--max-requests", "1"], ["--model", "auto"], ["--model", "auto-fast"],
            ["--model", MODEL, "--model", MODEL], ["--case", "short", "--case", "short"],
            ["--timeout", "nan"], ["--timeout", "181"], ["--timeout", "0"],
            ["--model", "name with spaces"],
            ["--model", "o3"], ["--model", "claude-sonnet"], ["--model", "GPT-5"],
            ["--model", "gpt-"],
        ]:
            with self.subTest(options=options), patch.object(evaluator, "inspect_catalog") as probe:
                code, report = self.invoke(options)
            self.assertEqual(code, 2)
            self.assertEqual(report["status"], "blocked")
            probe.assert_not_called()

    def test_unexpected_main_bug_propagates_and_closes_report(self):
        path = Path.cwd() / "tests" / (".codex-eval-test-" + uuid.uuid4().hex + ".json")
        try:
            with patch.object(evaluator, "make_plan", side_effect=TypeError("programming bug")), \
                    self.assertRaises(TypeError):
                evaluator.main(["--report", str(path)])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["results"], [])
            path.unlink()
        finally:
            path.unlink(missing_ok=True)

    def test_schedule_is_bounded_adjacent_and_alternating(self):
        rows = evaluator.schedule([case("short"), case("numbers")], [MODEL, OTHER], 2, 16)
        self.assertEqual(len(rows), 16)
        self.assertEqual([row["variant"] for row in rows[:2]], ["production", "compact"])
        self.assertEqual([row["variant"] for row in rows[4:6]], ["compact", "production"])
        self.assertEqual([rows[i]["model"] for i in (0, 2, 4, 6)], [MODEL, OTHER, OTHER, MODEL])
        self.assertEqual([row["position"] for row in rows], list(range(1, 17)))
        for a, b in zip(rows[::2], rows[1::2]):
            self.assertEqual((a["case"], a["model"], a["repeat"]), (b["case"], b["model"], b["repeat"]))
        self.assertEqual(rows[0]["variant"], rows[9]["variant"])

    def test_controls_are_not_identical_duplicate_prompt_arms(self):
        controls = [case(name) for name in ("dictionary", "code", "mixed-code", "long-en", "long-zh")]
        rows = evaluator.schedule(controls, [MODEL], 1, 5)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row["variant"] == "production" for row in rows))

    def test_unknown_catalog_model_prevents_all_turns(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(evaluator, "inspect_catalog", return_value={"models": [OTHER]}) as probe, \
                patch.object(evaluator, "run_one") as run:
            code, report = self.invoke([
                "--live", "--consent-chatgpt", "--codex", str(Path.cwd() / "codex.exe"),
                "--model", MODEL, "--case", "short"])
        probe.assert_called_once()
        run.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(report["error"], "model_not_in_current_catalog")

    def test_inspection_does_not_submit_turns(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(evaluator, "inspect_catalog", return_value={"models": [MODEL]}), \
                patch.object(evaluator, "run_one") as run:
            code, report = self.invoke([
                "--inspect", "--consent-chatgpt", "--codex", str(Path.cwd() / "codex.exe")])
        run.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "inspection_only")
        self.assertEqual(report["model_calls"], 0)

    def test_live_stops_on_first_failure_and_never_retries(self):
        failure = {"status": "failed", "turn_submitted": True, "error": "timeout"}
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(evaluator, "inspect_catalog", return_value={"models": [MODEL]}), \
                patch.object(evaluator, "run_one", return_value=failure) as run:
            code, report = self.invoke([
                "--live", "--consent-chatgpt", "--codex", str(Path.cwd() / "codex.exe"),
                "--model", MODEL, "--case", "short"])
        self.assertEqual(code, 1)
        self.assertEqual(report["model_calls"], 1)
        self.assertEqual(report["results"], [failure])
        run.assert_called_once()

    def test_existing_or_outside_report_is_rejected_before_any_cli(self):
        for path in (Path(__file__), Path.cwd().parent / "outside-eval.json"):
            with self.subTest(path=path), patch.object(evaluator, "inspect_catalog") as probe:
                code, report = self.invoke(["--inspect", "--consent-chatgpt",
                                            "--report", str(path), "--codex", str(Path.cwd() / "codex.exe")])
            self.assertEqual(code, 2)
            self.assertEqual(report["status"], "blocked")
            probe.assert_not_called()

    def test_json_report_persists_original_data_and_refuses_overwrite(self):
        path = Path.cwd() / "tests" / (".codex-eval-test-" + uuid.uuid4().hex + ".json")
        try:
            with patch.object(evaluator, "inspect_catalog", side_effect=AssertionError("probe")):
                code = evaluator.main(["--case", "numbers", "--report", str(path)])
            self.assertEqual(code, 0)
            original = path.read_bytes()
            report = json.loads(original)
            self.assertEqual(report["cases"][0]["text"], case("numbers").text)
            self.assertEqual(report["results"], [])
            code, blocked = self.invoke(["--report", str(path)])
            self.assertEqual(code, 2)
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(path.read_bytes(), original)
        finally:
            path.unlink(missing_ok=True)

    def test_live_report_is_persisted_before_and_between_requests(self):
        path = Path.cwd() / "tests" / (".codex-eval-test-" + uuid.uuid4().hex + ".json")
        observed = []

        def result(*args):
            persisted = json.loads(path.read_text(encoding="utf-8"))
            observed.append((persisted["status"], len(persisted["results"])))
            return {"status": "completed", "turn_submitted": True}

        try:
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(evaluator, "inspect_catalog", return_value={"models": [MODEL]}), \
                    patch.object(evaluator, "run_one", side_effect=result):
                code = evaluator.main([
                    "--live", "--consent-chatgpt", "--codex", str(Path.cwd() / "codex.exe"),
                    "--model", MODEL, "--case", "short", "--report", str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(observed, [("running", 0), ("running", 1)])
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "completed")
            self.assertEqual(report["model_calls"], 2)
        finally:
            path.unlink(missing_ok=True)


class GuardTests(unittest.TestCase):
    def assert_blocked(self, function, value):
        with self.assertRaises(evaluator.EvaluationError):
            function(value)

    def test_default_openai_and_empty_overrides_allowed(self):
        evaluator.guard_config(copy.deepcopy(SAFE_CONFIG))
        evaluator.guard_config({"config": {"model_providers": {}, "profile": None},
                                "layers": [{"config": {"model": MODEL}, "name": {"type": "user"}}]})
        evaluator.guard_environment({"PATH": "unchanged", "HOME": "unchanged"})
        evaluator.guard_account(SAFE_ACCOUNT)

    def test_paid_custom_and_ambiguous_config_rejected(self):
        overrides = [
            {"model_provider": "custom"},
            {"model_provider": "openai", "model_providers": {"openai": {"base_url": "https://example.invalid"}}},
            {"model_providers": {"unused": {}}},
            {"model_providers": {"openai": {"auth": {"command": "do-not-execute"}}}},
            {"model_providers": {"openai": {"env_key": "SECRET"}}},
            {"model_providers": {"openai": {"requires_openai_auth": False}}},
            {"openai_base_url": "https://api.openai.com"},
            {"chatgpt_base_url": "https://chatgpt.com"},
            {"base_url": "https://api.openai.com"},
            {"api_key": "never-report-this"}, {"auth": {"token": "never-report-this"}},
            {"experimental_bearer_token": "never-report-this"},
            {"model_catalog_json": "catalog.json"}, {"profile": "fast"},
            {"profiles": {"fast": {}}}, {"forced_login_method": "api"},
            {"http_headers": {"Authorization": "never-report-this"}}, {"service_tier": "priority"},
            {"requires_openai_auth": False}, {"proxy_url": "https://example.invalid"},
        ]
        for override in overrides:
            with self.subTest(override=override):
                self.assert_blocked(evaluator.guard_config, {"config": override, "layers": []})
                self.assert_blocked(evaluator.guard_config, {"config": {},
                                                             "layers": [{"config": override}]})

    def test_missing_or_malformed_layers_are_not_assumed_safe(self):
        for native in ({}, None, {"config": {}}, {"config": {}, "layers": None},
                       {"config": {}, "layers": [{}]}):
            with self.subTest(native=native):
                self.assert_blocked(evaluator.guard_config, native)

    def test_environment_override_blocked_not_cleared(self):
        for key in (
            "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY", "CODEX_HOME",
            "CC_TRANSLATE_CODEX_HOME", "CC_TRANSLATE_CODEX_ENV", "CHATGPT_BASE_URL",
            "AZURE_OPENAI_ENDPOINT", "HTTPS_PROXY", "http_proxy", "ALL_PROXY",
            "SSL_CERT_FILE", "DYLD_INSERT_LIBRARIES", "NODE_OPTIONS",
        ):
            with self.subTest(key=key):
                environment = {key: "private-value"}
                self.assert_blocked(evaluator.guard_environment, environment)
                self.assertEqual(environment, {key: "private-value"})

    def test_login_must_explicitly_be_chatgpt_not_api_or_external(self):
        for value in (
            {}, None, {"requiresOpenaiAuth": True, "account": None},
            {"requiresOpenaiAuth": True, "account": {"type": "apiKey"}},
            {"requiresOpenaiAuth": True, "account": {"type": "chatgptAuthTokens"}},
            {"requiresOpenaiAuth": False, "account": {"type": "chatgpt"}},
            {"account": {"type": "chatgpt"}},
            {"requiresOpenaiAuth": True, "account": {"type": "chatgpt", "authMode": "apiKey"}},
            {"requiresOpenaiAuth": True, "account": {"type": "chatgpt", "email": "x", "planType": "plus",
                                                   "apiKey": "must-not-use"}},
        ):
            with self.subTest(value=value):
                self.assert_blocked(evaluator.guard_account, value)

    def test_official_response_fixture_shapes_and_null_layer_rejection(self):
        evaluator.guard_account(copy.deepcopy(SAFE_ACCOUNT))
        evaluator.guard_config(copy.deepcopy(SAFE_CONFIG))
        invalid = copy.deepcopy(SAFE_CONFIG)
        invalid["layers"][0]["config"] = None
        self.assert_blocked(evaluator.guard_config, invalid)

    def test_errors_do_not_include_exception_messages(self):
        self.assertEqual(evaluator.safe_error(RuntimeError("secret-token")), "evaluation_failed")
        self.assertEqual(evaluator.safe_error(evaluator.EvaluationError("bad_code", "secret-token")), "bad_code")
        self.assertEqual(evaluator.safe_error(evaluator.EvaluationError("secret token")), "evaluation_failed")

    def test_wrapper_executable_rejected_before_any_cli_invocation(self):
        with patch.object(Path, "resolve", return_value=Path.cwd() / "wrapper.cmd"), \
                patch("builtins.open", return_value=io.BytesIO(b"@echo off")), \
                patch.object(evaluator.sys, "platform", "win32"), \
                patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess")):
            with self.assertRaises(evaluator.EvaluationError) as error:
                evaluator.native_transport("wrapper.cmd", {}, Path.cwd(), 30)
        self.assertEqual(error.exception.code, "native_official_cli_required")


class CorpusAndPromptTests(unittest.TestCase):
    def test_original_bilingual_data_has_required_coverage(self):
        self.assertEqual(len(evaluator.CORPUS), 11)
        self.assertEqual({value.target for value in evaluator.CORPUS}, {"en", "zh"})
        self.assertIn("1,250", case("numbers").text)
        self.assertIn("does not", case("terms").text)
        self.assertIn("```python", case("mixed-code").text)
        for value in evaluator.CORPUS:
            self.assertTrue(value.review)
        for name in ("long-en", "long-zh"):
            self.assertTrue(evaluator.case_contract(case(name))[2])

    def test_production_baselines_reuse_existing_prompts_exactly(self):
        for value in evaluator.CORPUS:
            classification, dictionary, summary, _ = evaluator.case_contract(value)
            if classification == "code":
                expected = CODE_EXPLAIN_PROMPT
            elif dictionary:
                expected = DICTIONARY_PROMPT
            elif summary:
                expected = codex_summary_instruction(value.target)
            else:
                expected = direction_prompt("to_" + value.target, "zh_CN") + SYSTEM_SUFFIX
            request = evaluator.make_request(value, MODEL, "production", 90)
            self.assertEqual(request.system_prompt, expected)
            self.assertEqual(request.user_text, value.text)
            self.assertEqual(request.model, MODEL)
            self.assertEqual(request.image_paths, ())

    def test_summary_first_cannot_be_compacted_or_reordered(self):
        for name in ("long-en", "long-zh"):
            value = case(name)
            request = evaluator.make_request(value, MODEL, "production", 90)
            summary, translation = evaluator.summary_headings(value.target)
            prompt = evaluator.build_codex_prompt(request)
            self.assertLess(prompt.index("## " + summary), prompt.index("## " + translation))
            self.assertEqual(request.task, "translation_summary")
            with self.assertRaises(evaluator.EvaluationError):
                evaluator.make_request(value, MODEL, "compact", 90)

    def test_compact_only_ordinary_text_with_same_safe_envelope(self):
        for value in evaluator.CORPUS:
            if evaluator.case_contract(value)[3]:
                baseline = evaluator.make_request(value, MODEL, "production", 90)
                compact = evaluator.make_request(value, MODEL, "compact", 90)
                self.assertLess(len(compact.system_prompt), len(baseline.system_prompt))
                prompt = evaluator.build_codex_prompt(compact)
                self.assertIn("Never use tools", prompt)
                encoded = prompt.split("<data>\n", 1)[1].removesuffix("\n</data>")
                self.assertEqual(json.loads(encoded), value.text)
            else:
                with self.assertRaises(evaluator.EvaluationError):
                    evaluator.make_request(value, MODEL, "compact", 90)


class MetricTests(unittest.TestCase):
    def metric(self, summary=True):
        self.now = 10.0
        return evaluator.OutputMetrics("zh", summary, clock=lambda: self.now)

    def test_fragmented_summary_heading_does_not_count_as_meaningful(self):
        metric = self.metric()
        self.now = 10.01
        metric.feed(" ")
        self.now = 10.02
        metric.feed("\n## 摘")
        self.assertIsNone(metric.first_meaningful_ms)
        self.now = 10.03
        metric.feed("要\n- ")
        self.assertIsNone(metric.first_meaningful_ms)
        self.now = 10.04
        metric.feed("保留条件。")
        self.now = 10.05
        metric.feed("\n## 译")
        self.assertIsNone(metric.summary_completion_ms)
        self.now = 10.06
        metric.feed("文\n完整译文。")
        self.now = 10.10
        result = metric.finish()
        self.assertAlmostEqual(result["raw_first_delta_ms"], 10)
        self.assertAlmostEqual(result["meaningful_first_ms"], 40)
        self.assertAlmostEqual(result["summary_completion_ms"], 60)
        self.assertAlmostEqual(result["total_ms"], 100)
        self.assertTrue(result["summary_first_format"])

    def test_wrong_order_missing_sections_and_preamble_fail_format(self):
        for output in (
            "## 译文\n翻译\n## 摘要\n摘要",
            "这是说明。\n## 摘要\n- 条件\n## 译文\n翻译",
            "## 摘要\n## 译文\n翻译",
            "## 摘要\n- 条件",
            "## 摘要\n- 条件\n## 译文",
            "## Summary\n- condition\n## Translation\ntranslation",
        ):
            with self.subTest(output=output):
                metric = self.metric()
                metric.feed(output)
                self.assertFalse(metric.finish()["summary_first_format"])

    def test_code_fence_heading_is_not_a_section_boundary(self):
        metric = self.metric()
        metric.feed("## 摘要\n- 保留代码。\n```text\n## 译文\n```\n")
        self.assertIsNone(metric.summary_completion_ms)
        metric.feed("## 译文\n内容")
        self.assertTrue(metric.finish()["summary_first_format"])

    def test_markdown_and_numeric_markers_are_not_content_but_symbols_are(self):
        for text in (" ", "- ", "1.", "42)", "[]", "*_`", "!", "#\u3000#"):
            with self.subTest(text=text):
                metric = self.metric(False)
                metric.feed(text)
                self.assertIsNone(metric.first_meaningful_ms)
        for text in (".", "?", "=", "中", "🙂", "0", "[] = {}"):
            with self.subTest(text=text):
                metric = self.metric(False)
                metric.feed(text)
                self.assertIsNotNone(metric.first_meaningful_ms)

    def test_fence_with_suffix_does_not_close_or_create_summary_boundary(self):
        metric = self.metric()
        metric.feed("## 摘要\n- 内容\n```text\n```not-a-close\n## 译文\n")
        self.assertIsNone(metric.summary_completion_ms)
        metric.feed("```\n## 译文\n完整译文")
        self.assertTrue(metric.finish()["summary_first_format"])

    def test_cr_and_crlf_delimiters_preserve_original_output(self):
        output = "## 摘要\r- 要点\r\n## 译文\r正文"
        metric = self.metric()
        for chunk in output:
            metric.feed(chunk)
        self.assertEqual(metric.output, output)
        self.assertTrue(metric.finish()["summary_first_format"])

    def test_empty_stream_has_no_invented_timings(self):
        metric = self.metric(False)
        result = metric.finish()
        for key in ("raw_first_delta_ms", "meaningful_first_ms", "summary_completion_ms"):
            self.assertIsNone(result[key])

    def test_output_limit_is_utf8_bytes_and_keeps_prior_data(self):
        metric = self.metric(False)
        metric.feed("original")
        with self.assertRaises(evaluator.EvaluationError):
            metric.feed("中" * evaluator.MAX_OUTPUT_BYTES)
        self.assertEqual(metric.output, "original")


class TransportTests(unittest.TestCase):
    def request(self):
        return evaluator.make_request(case("short"), MODEL, "production", 30)

    def run_wire(self, wire):
        return evaluator.run_one(case("short"),
                                 {"case": "short", "model": MODEL, "variant": "production", "position": 1},
                                 "unused", {}, Path.cwd(), 30, factory=lambda *args: wire)

    def test_complete_live_protocol_is_serial_safe_ephemeral_and_closed(self):
        wire = WireTransport()
        record = self.run_wire(wire)
        self.assertEqual(record["status"], "completed", record)
        self.assertEqual(record["quality"], "not_evaluated")
        self.assertEqual(record["input"], case("short").text)
        self.assertEqual(record["output"], wire.content)
        self.assertEqual(record["streamed_output"], wire.content)
        self.assertTrue(record["turn_submitted"])
        self.assertEqual(record["model_provenance"]["thread_model"], MODEL)
        self.assertEqual(record["model_provenance"]["served_model"], "unknown")
        self.assertNotIn("do-not-report", json.dumps(record))
        self.assertEqual(wire.stopped, 1)
        methods = [method for method, _ in wire.sent]
        self.assertEqual(methods, ["initialize", "initialized", "hooks/list", "config/read",
                                   "account/read", "model/list", "thread/start", "config/read",
                                   "account/read", "turn/start"])
        thread = next(params for method, params in wire.sent if method == "thread/start")
        turn = next(params for method, params in wire.sent if method == "turn/start")
        self.assertTrue(thread["ephemeral"])
        self.assertEqual(thread["sandbox"], "read-only")
        self.assertEqual(turn["sandboxPolicy"], {"type": "readOnly", "networkAccess": False})
        self.assertIn("Never use tools", turn["input"][0]["text"])
        account = next(params for method, params in wire.sent if method == "account/read")
        self.assertEqual(account, {"refreshToken": False})

    def test_custom_config_blocks_before_account_catalog_thread_and_turn(self):
        wire = WireTransport()
        wire.config["config"]["model_provider"] = "custom"
        record = self.run_wire(wire)
        self.assertEqual(record["error"], "custom_provider_blocked")
        self.assertFalse(record["turn_submitted"])
        self.assertFalse(any(method in ("account/read", "model/list", "thread/start", "turn/start")
                             for method, _ in wire.sent))
        self.assertEqual(wire.stopped, 1)

    def test_paid_login_and_missing_catalog_model_block_thread(self):
        for change in ("paid", "catalog"):
            with self.subTest(change=change):
                wire = WireTransport()
                if change == "paid":
                    wire.account["account"]["type"] = "apiKey"
                else:
                    wire.catalog["data"][0]["model"] = OTHER
                record = self.run_wire(wire)
                self.assertEqual(record["status"], "failed")
                self.assertFalse(any(method in ("thread/start", "turn/start") for method, _ in wire.sent))
                self.assertEqual(wire.stopped, 1)

    def test_wrong_and_null_confirmable_model_block_turn(self):
        for model in (OTHER, None, ""):
            with self.subTest(model=model):
                wire = WireTransport()
                wire.thread_result["model"] = model
                record = self.run_wire(wire)
                self.assertEqual(record["error"], "unexpected_or_missing_model")
                self.assertFalse(record["turn_submitted"])
                self.assertEqual(wire.stopped, 1)

    def test_wrong_thread_provider_blocks_turn(self):
        wire = WireTransport()
        wire.thread_result["modelProvider"] = "custom"
        record = self.run_wire(wire)
        self.assertEqual(record["error"], "unexpected_model_provider")
        self.assertFalse(record["turn_submitted"])

    def test_absent_model_provenance_explicitly_unknown(self):
        wire = WireTransport()
        del wire.thread_result["model"]
        record = self.run_wire(wire)
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["model_provenance"], {"thread_model": None, "served_model": "unknown", "events": []})

    def test_reroute_unknown_protocol_tool_and_wrong_verification_abort(self):
        for event, error in (
            ({"method": "model/rerouted", "params": {}}, "model_rerouted"),
            ({"method": "unexpected/event", "params": {}}, "unknown_appserver_event"),
            ({"method": "item/started", "params": {"item": {"type": "commandExecution"}}}, "unsafe_tool_event"),
            ({"method": "model/verification", "params": {"model": OTHER}}, "unexpected_or_missing_model"),
            ({"method": "account/updated", "params": {}}, "account_changed_during_turn"),
            ({"method": "turn/completed", "params": {"turn": {"model": OTHER}}}, "unexpected_or_missing_model"),
        ):
            with self.subTest(event=event):
                wire = WireTransport()
                wire.extra_events = [event]
                record = self.run_wire(wire)
                self.assertEqual(record["error"], error)
                self.assertEqual(wire.stopped, 1)
                self.assertEqual(sum(method == "turn/start" for method, _ in wire.sent), 1)

    def test_no_second_turn_even_when_transport_is_reused_accidentally(self):
        wire = WireTransport()
        record = self.run_wire(wire)
        self.assertEqual(record["status"], "completed")
        params = {"model": MODEL, "approvalPolicy": "never",
                  "sandboxPolicy": {"type": "readOnly", "networkAccess": False}}
        with self.assertRaises(evaluator.EvaluationError) as error:
            wire._send(object(), "turn/start", params, 99)
        self.assertEqual(error.exception.code, "second_turn_blocked")

    def test_read_only_inspection_never_uses_hooks_thread_turn_or_warmup(self):
        wire = WireTransport()
        result = evaluator.inspect_catalog("unused", {}, Path.cwd(), factory=lambda *args: wire)
        self.assertEqual(result["models"], [MODEL])
        self.assertEqual([method for method, _ in wire.sent],
                         ["initialize", "initialized", "config/read", "account/read", "model/list"])
        self.assertEqual(wire.stopped, 1)

    def test_catalog_pagination_is_bounded_and_cursor_repeats_fail(self):
        wire = WireTransport()
        wire.catalog["nextCursor"] = "same-cursor"
        with self.assertRaises(evaluator.EvaluationError) as error:
            evaluator.inspect_catalog("unused", {}, Path.cwd(), factory=lambda *args: wire)
        self.assertEqual(error.exception.code, "catalog_cursor_invalid")
        self.assertEqual(wire.stopped, 1)
        self.assertEqual(sum(method == "model/list" for method, _ in wire.sent), 2)

    def test_catalog_exposes_only_gpt_ids_even_if_other_families_are_available(self):
        wire = WireTransport()
        wire.catalog["data"].extend({"model": name, "id": name} for name in ("o3", "claude-sonnet", "GPT-5"))
        result = evaluator.inspect_catalog("unused", {}, Path.cwd(), factory=lambda *args: wire)
        self.assertEqual(result["models"], [MODEL])

    def test_build_command_preserves_all_provider_safety_overrides(self):
        wire = WireTransport()
        with patch.object(evaluator, "read_native_config", return_value=SAFE_CONFIG):
            command = wire.build_command(self.request())
        overrides = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "-c"]
        self.assertTrue(set(CODEX_CONFIG_OVERRIDES) <= set(overrides))
        self.assertIn("mcp_servers={}", overrides)
        self.assertFalse(any(value.startswith(("model_provider=", "model_catalog_json=")) for value in overrides))

    def test_unsafe_prelaunch_config_rejected_before_startup(self):
        wire = WireTransport()
        native = {"config": {"model_providers": {"openai": {"base_url": "private"}}}, "layers": []}
        with patch.object(evaluator, "read_native_config", return_value=native), \
                self.assertRaises(evaluator.EvaluationError):
            wire.build_command(self.request())
        self.assertEqual(wire.sent, [])

    def test_rpc_error_and_eof_inspection_close_without_leaking_detail(self):
        for message in ({"id": 1, "error": {"message": "secret-token"}}, None):
            wire = WireTransport()
            if message is None:
                wire._next_message = Mock(return_value=("eof", None))
            else:
                wire.messages = [message]
            with self.subTest(message=message), self.assertRaises(evaluator.EvaluationError if message is None
                                                                else evaluator.CodexAppServerProtocolError):
                evaluator.inspect_catalog("unused", {}, Path.cwd(), factory=lambda *args: wire)
            self.assertEqual(wire.stopped, 1)


class ResultAndCleanupTests(unittest.TestCase):
    def provider(self, *, result=None, deltas=(), error=None, cleanup_error=None):
        provider = Mock()
        provider.turn_sent = True
        provider.provenance = {"thread_model": None, "served_model": "unknown", "events": []}

        def stream(request, callback):
            for delta in deltas:
                callback(delta)
            if error:
                raise error
            return result or ProviderResult(True, "".join(deltas))

        provider.stream.side_effect = stream
        provider.shutdown.side_effect = cleanup_error
        return provider

    def run_provider(self, provider, selected=None):
        selected = selected or case("short")
        return evaluator.run_one(selected, {"case": selected.id, "model": MODEL,
                                           "variant": "production", "position": 1},
                                 "unused", {}, Path.cwd(), 30, factory=lambda *args: provider)

    def test_final_only_text_preserved_without_fabricated_first_timings(self):
        provider = self.provider(result=ProviderResult(True, "最终输出"))
        record = self.run_provider(provider)
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["output"], "最终输出")
        self.assertIsNone(record["timings"]["raw_first_delta_ms"])
        self.assertIsNone(record["timings"]["meaningful_first_ms"])
        provider.stream.assert_called_once()
        provider.shutdown.assert_called_once()

    def test_final_only_summary_format_checked_but_timing_unknown(self):
        text = "## 摘要\n- 要点\n## 译文\n完整译文"
        provider = self.provider(result=ProviderResult(True, text))
        record = self.run_provider(provider, case("long-en"))
        self.assertEqual(record["status"], "completed")
        self.assertIsNone(record["timings"]["summary_completion_ms"])
        self.assertIsNone(record["timings"]["summary_first_format"])

    def test_final_summary_wrong_order_fails_even_without_deltas(self):
        provider = self.provider(result=ProviderResult(True, "## 译文\n正文\n## 摘要\n要点"))
        record = self.run_provider(provider, case("long-en"))
        self.assertEqual(record["error"], "summary_first_format_missing")

    def test_stream_final_mismatch_records_both_outputs_and_fails(self):
        provider = self.provider(result=ProviderResult(True, "different"), deltas=["original"])
        record = self.run_provider(provider)
        self.assertEqual(record["error"], "stream_final_mismatch")
        self.assertEqual(record["output"], "different")
        self.assertEqual(record["streamed_output"], "original")
        provider.shutdown.assert_called_once()

    def test_exception_interrupt_and_protocol_failure_all_cleanup(self):
        for error, expected in (
            (OSError("secret-token"), "evaluation_failed"),
            (KeyboardInterrupt(), "interrupted"),
            (evaluator.EvaluationError("unsafe_tool_event", "secret-token"), "unsafe_tool_event"),
        ):
            with self.subTest(error=type(error)):
                provider = self.provider(deltas=["partial"], error=error)
                record = self.run_provider(provider)
                self.assertEqual(record["error"], expected)
                self.assertEqual(record["streamed_output"], "partial")
                self.assertNotIn("secret-token", json.dumps(record))
                provider.shutdown.assert_called_once()

    def test_programming_bugs_are_not_relabelled_provider_failures(self):
        for error in (RuntimeError("logic defect"), TypeError("wrong code"), AssertionError("invariant")):
            with self.subTest(error=type(error)):
                provider = self.provider(deltas=["partial"], error=error)
                with self.assertRaises(type(error)):
                    self.run_provider(provider)
                provider.shutdown.assert_called_once()

    def test_unexpected_shutdown_bug_propagates(self):
        provider = self.provider(deltas=["output"], cleanup_error=TypeError("programming bug"))
        with self.assertRaises(TypeError):
            self.run_provider(provider)

    def test_cleanup_failure_never_reported_as_success(self):
        provider = self.provider(deltas=["output"], cleanup_error=evaluator.ProcessError("probe_cleanup_failed"))
        record = self.run_provider(provider)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "cleanup_failed")

    def test_output_limit_always_cleanup_and_does_not_retain_overflow(self):
        provider = self.provider(deltas=["small", "x" * evaluator.MAX_OUTPUT_BYTES])
        record = self.run_provider(provider)
        self.assertEqual(record["error"], "output_limit")
        self.assertEqual(record["streamed_output"], "small")
        provider.shutdown.assert_called_once()

    def test_provider_failure_stays_failed_and_drops_sensitive_error_detail(self):
        provider = self.provider(result=ProviderResult(False, error_code="timeout", error_detail="secret-token"))
        record = self.run_provider(provider)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "timeout")
        self.assertNotIn("secret-token", json.dumps(record))


    def test_bounded_provider_timings_are_retained_without_unrecognized_fields(self):
        provider = self.provider(result=ProviderResult(True, "output", metrics=(
            ("turn_total_ms", 123), ("first_result_ms", 65), ("secret-token", 1),
            ("spawn_ms", -1), ("initialize_ms", float("nan")))))
        record = self.run_provider(provider)
        self.assertEqual(record["provider_timings"], {"turn_total_ms": 123, "first_result_ms": 65})
        self.assertNotIn("secret-token", json.dumps(record))


class MockedWindowsInvocationTests(unittest.TestCase):
    """Use the actual factory, build_command, Popen setup and RPC implementation."""

    def invoke(self, *, startup_config=None, submission_config=None, submission_account=None):
        path = Path.cwd() / "tests" / (".codex-native-test-" + uuid.uuid4().hex)
        path.write_bytes(b"MZ-not-an-executable-test-fixture")
        content = "图书馆今天提前关门。"
        identity = {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1"}
        replies = [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"data": [{"cwd": str(Path.cwd()), "hooks": []}]}},
            {"id": 4, "result": SAFE_CONFIG},
            {"id": 5, "result": SAFE_ACCOUNT},
            {"id": 6, "result": CATALOG},
            {"id": 3, "result": {"thread": {"id": "thread-1"}, "model": MODEL, "modelProvider": "openai"}},
            {"id": 8, "result": submission_config if submission_config is not None else SAFE_CONFIG},
            {"id": 9, "result": submission_account if submission_account is not None else SAFE_ACCOUNT},
            {"id": 7, "result": {"turn": {"id": "turn-1"}}},
            {"method": "item/agentMessage/delta", "params": dict(identity, delta=content)},
            {"method": "item/completed", "params": dict(identity, item={
                "type": "agentMessage", "id": "item-1", "text": content, "phase": "final_answer"})},
            {"method": "turn/completed", "params": dict(identity, turn={"id": "turn-1", "status": "completed"})},
        ]
        process = Mock()
        process.pid = None
        process.poll.return_value = None
        process.wait.return_value = 0
        process.stdin = io.StringIO()
        process.stdout = io.StringIO("\n".join(json.dumps(reply) for reply in replies) + "\n")
        process.stderr = io.StringIO()
        version = subprocess.CompletedProcess([str(path), "--version"], 0, "codex-cli 0.154.0", "")
        try:
            with patch.object(evaluator.sys, "platform", "win32"), \
                    patch.object(subprocess, "run", return_value=version) as version_probe, \
                    patch.object(subprocess, "Popen", return_value=process) as popen, \
                    patch.object(evaluator, "read_native_config",
                                 return_value=startup_config if startup_config is not None else SAFE_CONFIG):
                result = evaluator.run_one(case("short"), {
                    "case": "short", "model": MODEL, "variant": "production", "position": 1,
                }, str(path), {}, Path.cwd(), 30)
            sent = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
            return result, sent, popen, version_probe, process
        finally:
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()
            path.unlink(missing_ok=True)

    def test_actual_windows_factory_and_streaming_invocation_is_safe_and_closed(self):
        result, sent, popen, version, process = self.invoke()
        self.assertEqual(result["status"], "completed", result)
        self.assertIsNotNone(result["timings"]["turn_raw_first_delta_ms"])
        popen.assert_called_once()
        version.assert_called_once()
        argv = popen.call_args.args[0]
        self.assertEqual(argv[1:5], ["app-server", "--listen", "stdio://", "--strict-config"])
        self.assertEqual(popen.call_args.kwargs["env"], {})
        self.assertEqual(popen.call_args.kwargs["cwd"], str(Path.cwd()))
        self.assertIn('approval_policy="never"', argv)
        self.assertIn("project_doc_max_bytes=0", argv)
        self.assertIn("mcp_servers={}", argv)
        methods = [message["method"] for message in sent]
        self.assertEqual(methods.count("thread/start"), 1)
        self.assertEqual(methods.count("turn/start"), 1)
        self.assertEqual(methods[-3:], ["config/read", "account/read", "turn/start"])
        self.assertTrue(all(message["params"] == {"refreshToken": False}
                            for message in sent if message["method"] == "account/read"))
        process.kill.assert_called_once()
        process.wait.assert_called_once()

    def test_paid_or_external_account_at_submission_cannot_fall_back(self):
        for account in (
            {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True},
            {"account": {"type": "chatgptAuthTokens"}, "requiresOpenaiAuth": True},
            {"account": dict(SAFE_ACCOUNT["account"], apiKey="not-allowed"), "requiresOpenaiAuth": True},
            {"account": SAFE_ACCOUNT["account"], "requiresOpenaiAuth": False},
        ):
            with self.subTest(account=account):
                result, sent, popen, _, process = self.invoke(submission_account=account)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["turn_submitted"])
                self.assertNotIn("turn/start", [message["method"] for message in sent])
                popen.assert_called_once()
                process.kill.assert_called_once()
                self.assertNotIn("not-allowed", json.dumps(result))

    def test_routing_change_at_submission_is_blocked(self):
        unsafe = copy.deepcopy(SAFE_CONFIG)
        unsafe["config"] = {"model_provider": "openai", "model_providers": {"openai": {"env_key": "PAID_KEY"}}}
        result, sent, _, _, process = self.invoke(submission_config=unsafe)
        self.assertEqual(result["error"], "routing_or_auth_override_blocked")
        self.assertNotIn("turn/start", [message["method"] for message in sent])
        process.kill.assert_called_once()

    def test_custom_startup_config_prevents_real_transport_spawn(self):
        unsafe = copy.deepcopy(SAFE_CONFIG)
        unsafe["config"]["model_provider"] = "custom"
        result, sent, popen, _, _ = self.invoke(startup_config=unsafe)
        self.assertEqual(result["error"], "custom_provider_blocked")
        self.assertFalse(result["turn_submitted"])
        self.assertEqual(sent, [])
        popen.assert_not_called()

if __name__ == "__main__":
    unittest.main()
