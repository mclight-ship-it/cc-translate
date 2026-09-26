"""Portable facade contracts; no installed Claude, account, or model invocation."""

import base64
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from cc_providers import claude_darwin as native
from cc_providers.base import CLAUDE_PROVIDER, ProviderRequest
from cc_providers.codex_catalog import CatalogProbeError
from cc_providers.darwin_process import ProcessError
from cc_macos.image_fixture import PNG_BYTES


class TestDarwinClaudeProvider(unittest.TestCase):
    def setUp(self):
        self.root = str(Path.cwd() / ".synthetic-claude")
        self.command = os.path.join(self.root, "synthetic-cli")
        self.work = os.path.join(self.root, "work")
        self.environment = {"HOME": self.root, "PATH": "explicit", "AUTH_MARKER": "unchanged"}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(native.sys, "platform", "darwin"))
        self.makedirs = self.stack.enter_context(patch.object(native.os, "makedirs"))
        self.popen = self.stack.enter_context(patch("subprocess.Popen",
                                                  side_effect=AssertionError("Real CLI started")))
        self.transport = self.stack.enter_context(patch.object(native, "stream_output", side_effect=self.respond))
        self.request = ProviderRequest("text", "sonnet", "Only translate.", "PRIVATE input")
        self.provider = self.make_provider()

    def make_provider(self, **kwargs):
        values = dict(command=self.command, work_dir=self.work, environment=self.environment, log_error=Mock())
        values.update(kwargs)
        return native.DarwinClaudeProvider(**values)

    def respond(self, args, env, work, data, on_line, **kwargs):
        kwargs["on_write"]()
        on_line(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "delta": {"type": "text_delta", "text": "translated"}}}))
        on_line(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "translated"}))

    def assert_failure(self, result, code, submitted):
        self.assertFalse(result.ok)
        self.assertEqual(result.text, "")
        self.assertEqual(result.error_detail, "")
        self.assertEqual(result.error_code, code)
        self.assertIs(dict(result.metrics)["turn_submitted"], submitted)

    def test_constructor_is_inert_and_binds_only_the_explicit_environment(self):
        self.makedirs.assert_not_called()
        self.transport.assert_not_called()
        self.popen.assert_not_called()
        self.environment["AUTH_MARKER"] = "changed"
        self.assertEqual(self.provider.env["AUTH_MARKER"], "unchanged")
        with self.assertRaises(TypeError):
            self.provider.env["PATH"] = "mutation"
        self.assertEqual(self.provider.provider_id, CLAUDE_PROVIDER)
        self.assertTrue(self.provider.capabilities.images)
        self.assertTrue(self.provider.capabilities.warm_sessions)

    def test_one_text_request_preserves_auth_and_uses_no_version_probe_or_tool_bypass(self):
        deltas = []
        result = self.provider.stream(self.request, deltas.append)
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "translated")
        self.assertEqual(deltas, ["translated"])
        self.assertTrue(dict(result.metrics)["turn_submitted"])
        self.transport.assert_called_once()
        args, env, work, data, _line = self.transport.call_args.args
        options = self.transport.call_args.kwargs
        self.assertEqual(args[0], self.command)
        self.assertEqual(env, self.environment)
        self.assertEqual(work, self.work)
        self.assertNotIn(self.request.user_text, args)
        self.assertEqual(json.loads(data), {"type": "user", "message": {
            "role": "user", "content": [{"type": "text", "text": self.request.user_text}]}})
        self.assertTrue(data.endswith(b"\n"))
        self.assertIn("--model=sonnet", args)
        for flag, value in (
                ("--input-format", "stream-json"), ("--output-format", "stream-json"),
                ("--tools", ""), ("--disallowedTools", "mcp__*"), ("--permission-mode", "dontAsk"),
                ("--mcp-config", '{"mcpServers":{}}'), ("--settings", '{"disableAllHooks":true}')):
            self.assertEqual(args[args.index(flag) + 1], value)
        for flag in ("--print", "--verbose", "--include-partial-messages", "--strict-mcp-config",
                     "--setting-sources=", "--disable-slash-commands", "--no-session-persistence"):
            self.assertIn(flag, args)
        for flag in ("--version", "--bare", "--safe-mode", "--dangerously-skip-permissions",
                     "--fallback-model", "--resume", "--continue"):
            self.assertNotIn(flag, args)
        self.assertEqual(options["max_bytes"], native.MAX_OUTPUT_BYTES)
        self.assertEqual(options["max_input_bytes"], native.MAX_INPUT_BYTES)
        self.assertGreater(options["timeout"], 0)
        self.assertLessEqual(options["timeout"], self.request.timeout_seconds)

    def test_complete_summary_default_and_custom_model_use_the_same_single_path(self):
        for model in (None, "future-custom-model", "--not-a-cli-option"):
            result = self.provider.complete(replace(
                self.request, task="translation_summary", model=model, user_text="text\0inside JSON"))
            self.assertTrue(result.ok)
            args = self.transport.call_args.args[0]
            payload = json.loads(self.transport.call_args.args[3])
            self.assertEqual(payload["message"]["content"][0]["text"], "text\0inside JSON")
            if model is None:
                self.assertFalse(any(arg.startswith("--model") for arg in args))
            else:
                self.assertIn("--model=" + model, args)
        self.assertEqual(self.transport.call_count, 3)

    def idle_factory(self):
        def create(*args, **kwargs):
            warm = Mock(available=True, error="")
            warm.take.return_value = (Mock(), b'{"type":"system"}\n', 18)
            return warm
        return self.stack.enter_context(patch.object(native, "IdlePrintProcess", side_effect=create))

    def test_warmup_never_serializes_or_submits_user_text_and_reuses_one_slot(self):
        factory = self.idle_factory()
        with patch.object(native, "_input", side_effect=AssertionError("Warm input serialized")):
            first = self.provider.warm_up(self.request)
            warm = self.provider._warm
            second = self.provider.warm_up(self.request)
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertFalse(dict(first.metrics)["turn_submitted"])
        self.assertEqual(dict(first.metrics)["warm_process_start"], 1)
        self.assertEqual(dict(second.metrics)["warm_process_start"], 0)
        self.assertEqual(dict(first.metrics)["warm_process_hit"], 0)
        self.assertEqual(dict(second.metrics)["warm_process_hit"], 1)
        factory.assert_called_once()
        args, env, work = factory.call_args.args
        self.assertEqual(args, self.provider._command(self.request))
        self.assertNotIn(self.request.user_text, repr(factory.call_args))
        self.assertEqual(env, self.environment)
        self.assertEqual(work, self.work)
        self.assertEqual(factory.call_args.kwargs["idle_seconds"], 600)
        self.transport.assert_not_called()
        warm.take.assert_not_called()
        self.provider.shutdown(require_cleanup=True)
        warm.close.assert_called_once()

    def test_matching_warm_child_consumed_once_then_cold_or_explicit_refill(self):
        factory = self.idle_factory()
        self.assertTrue(self.provider.warm_up(self.request).ok)
        warm = self.provider._warm
        first = self.provider.complete(self.request)
        self.assertTrue(first.ok)
        self.assertEqual(dict(first.metrics)["warm_process_hit"], 1)
        self.assertEqual(dict(first.metrics)["cold_process_start"], 0)
        self.assertIs(self.transport.call_args.kwargs["owned_process"], warm.take.return_value[0])
        warm.take.assert_called_once()
        self.assertIsNone(self.provider._warm)
        second = self.provider.complete(self.request)
        self.assertEqual(dict(second.metrics)["warm_process_hit"], 0)
        self.assertEqual(dict(second.metrics)["cold_process_start"], 1)
        self.assertNotIn("owned_process", self.transport.call_args.kwargs)
        factory.assert_called_once()
        self.assertTrue(self.provider.warm_up(self.request).ok)
        self.assertEqual(factory.call_count, 2)
        self.provider.shutdown(require_cleanup=True)

    def test_model_prompt_and_task_mismatches_discard_before_cold_submission(self):
        self.idle_factory()
        for changed in (replace(self.request, model="opus"),
                        replace(self.request, system_prompt="Summarize instead."),
                        replace(self.request, task="translation_summary")):
            self.assertTrue(self.provider.warm_up(self.request).ok)
            warm = self.provider._warm
            def respond(*args, **kwargs):
                warm.close.assert_called_once()
                self.assertNotIn("owned_process", kwargs)
                self.respond(*args, **kwargs)
            self.transport.side_effect = respond
            result = self.provider.complete(changed)
            self.assertTrue(result.ok)
            self.assertEqual(dict(result.metrics)["warm_process_hit"], 0)
            warm.take.assert_not_called()

    def test_warm_replacement_closes_old_child_before_starting_new_one(self):
        factory = self.idle_factory()
        self.assertTrue(self.provider.warm_up(self.request).ok)
        old = self.provider._warm
        original = factory.side_effect
        def create(*args, **kwargs):
            old.close.assert_called_once()
            return original(*args, **kwargs)
        factory.side_effect = create
        self.assertTrue(self.provider.warm_up(replace(self.request, model="opus")).ok)
        self.assertEqual(factory.call_count, 2)
        self.provider.shutdown(require_cleanup=True)

    def test_expired_or_dead_warm_child_falls_back_without_using_its_old_deadline(self):
        self.idle_factory()
        clock = [0]
        with patch.object(native.time, "monotonic", side_effect=lambda: clock[0]):
            self.assertTrue(self.provider.warm_up(replace(self.request, timeout_seconds=1)).ok)
            warm = self.provider._warm
            clock[0] = 500
            self.assertTrue(self.provider.complete(self.request).ok)
            self.assertGreater(self.transport.call_args.kwargs["timeout"], 59)
            self.assertIs(self.transport.call_args.kwargs["owned_process"], warm.take.return_value[0])
            self.assertTrue(self.provider.warm_up(self.request).ok)
            self.provider._warm.take.return_value = None
            clock[0] = 1200
            result = self.provider.complete(self.request)
            self.assertTrue(result.ok)
            self.assertEqual(dict(result.metrics)["cold_process_start"], 1)
            self.assertNotIn("owned_process", self.transport.call_args.kwargs)

    def test_warm_failure_retries_only_before_a_positive_write(self):
        self.idle_factory()
        for submitted in (False, True):
            self.assertTrue(self.provider.warm_up(self.request).ok)
            def fail_warm(*args, **kwargs):
                if "owned_process" in kwargs:
                    if submitted:
                        kwargs["on_write"]()
                    raise ProcessError("probe_failed")
                self.respond(*args, **kwargs)
            self.transport.reset_mock()
            self.transport.side_effect = fail_warm
            result = self.provider.complete(self.request)
            self.assertEqual(result.ok, not submitted)
            self.assertEqual(self.transport.call_count, 1 if submitted else 2)
            self.assertEqual(dict(result.metrics)["cold_process_start"], 0 if submitted else 1)

    def test_cleanup_failure_on_discard_is_fatal_and_never_falls_back(self):
        factory = self.idle_factory()
        self.assertTrue(self.provider.warm_up(self.request).ok)
        self.provider._warm.close.side_effect = ProcessError("probe_cleanup_failed")
        self.assert_failure(self.provider.complete(replace(self.request, model="opus")),
                            "provider_cleanup_failed", False)
        self.assert_failure(self.provider.warm_up(self.request), "provider_cleanup_failed", False)
        self.transport.assert_not_called()
        factory.assert_called_once()
        with self.assertRaisesRegex(ProcessError, "provider_cleanup_failed"):
            self.provider.shutdown(require_cleanup=True)

    def test_asynchronous_idle_cleanup_failure_poisoning_is_fail_closed(self):
        factory = self.idle_factory()
        self.assertTrue(self.provider.warm_up(self.request).ok)
        factory.call_args.kwargs["on_error"]("probe_cleanup_failed")
        self.assert_failure(self.provider.complete(self.request), "provider_cleanup_failed", False)
        self.assert_failure(self.provider.warm_up(self.request), "provider_cleanup_failed", False)
        self.transport.assert_not_called()
        with self.assertRaisesRegex(ProcessError, "provider_cleanup_failed"):
            self.provider.shutdown(require_cleanup=True)

    def test_cancelled_warmup_and_images_do_not_start_or_submit(self):
        factory = self.idle_factory()
        cancel = threading.Event()
        cancel.set()
        self.assert_failure(self.provider.warm_up(self.request, cancel), "cancelled", False)
        self.assert_failure(self.provider.warm_up(replace(
            self.request, task="image", image_paths=(self.command,))), "unsupported_task", False)
        factory.assert_not_called()
        self.transport.assert_not_called()

    def test_cancel_during_warm_creation_drains_child(self):
        factory = self.idle_factory()
        cancel = threading.Event()
        original = factory.side_effect
        children = []
        def create(*args, **kwargs):
            children.append(original(*args, **kwargs))
            cancel.set()
            return children[-1]
        factory.side_effect = create
        self.assert_failure(self.provider.warm_up(self.request, cancel), "cancelled", False)
        children[0].close.assert_called_once()
        self.assertIsNone(self.provider._warm)
        self.transport.assert_not_called()

    def test_expired_warm_creation_drains_child_without_starting_a_turn(self):
        factory = self.idle_factory()
        clock = [0]
        children = []
        original = factory.side_effect
        def create(*args, **kwargs):
            children.append(original(*args, **kwargs))
            clock[0] = 2
            return children[-1]
        factory.side_effect = create
        with patch.object(native.time, "monotonic", side_effect=lambda: clock[0]):
            self.assert_failure(self.provider.warm_up(replace(self.request, timeout_seconds=1)),
                                "timeout", False)
        children[0].close.assert_called_once()
        self.transport.assert_not_called()

    def test_warm_late_result_failure_and_cleanup_failure_never_replay(self):
        self.idle_factory()
        for code in ("probe_failed", "probe_cleanup_failed"):
            self.assertTrue(self.provider.warm_up(self.request).ok)
            def fail(*args, **kwargs):
                self.respond(*args, **kwargs)
                raise ProcessError(code)
            self.transport.reset_mock()
            self.transport.side_effect = fail
            self.assert_failure(self.provider.complete(self.request),
                                "provider_cleanup_failed" if "cleanup" in code else code, True)
            self.transport.assert_called_once()

    def test_pre_submission_warm_cleanup_failure_never_falls_back(self):
        self.idle_factory()
        self.assertTrue(self.provider.warm_up(self.request).ok)
        self.transport.side_effect = ProcessError("probe_cleanup_failed")
        self.assert_failure(self.provider.complete(self.request), "provider_cleanup_failed", False)
        self.transport.assert_called_once()

    def test_unexpected_idle_handoff_failure_propagates_without_cold_fallback(self):
        self.idle_factory()
        self.assertTrue(self.provider.warm_up(self.request).ok)
        error = RuntimeError("synthetic idle bug")
        self.provider._warm.take.side_effect = error
        with self.assertRaises(RuntimeError) as raised:
            self.provider.complete(self.request)
        self.assertIs(raised.exception, error)
        self.assertIsNone(self.provider._active_thread)
        self.transport.assert_not_called()

    def test_unexpected_idle_failure_is_not_a_successful_warm_result(self):
        self.idle_factory()
        self.assertTrue(self.provider.warm_up(self.request).ok)
        error = RuntimeError("synthetic idle bug")
        warm = self.provider._warm
        warm.check_failure.side_effect = error
        with self.assertRaises(RuntimeError) as raised:
            self.provider.warm_up(self.request)
        self.assertIs(raised.exception, error)
        self.provider.shutdown(require_cleanup=True)
        warm.close.assert_called_once()
        self.transport.assert_not_called()

    def test_foreground_preempts_in_progress_warm_creation_and_gets_cold_fallback(self):
        factory = self.idle_factory()
        entered, release = threading.Event(), threading.Event()
        original = factory.side_effect
        children, warmed, translated = [], [], []
        def create(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            children.append(original(*args, **kwargs))
            return children[-1]
        def respond(*args, **kwargs):
            children[0].close.assert_called_once()
            self.assertNotIn("owned_process", kwargs)
            self.respond(*args, **kwargs)
        factory.side_effect = create
        self.transport.side_effect = respond
        warmer = threading.Thread(target=lambda: warmed.append(self.provider.warm_up(self.request)))
        foreground = threading.Thread(target=lambda: translated.append(self.provider.complete(self.request)))
        warmer.start()
        try:
            self.assertTrue(entered.wait(2))
            foreground.start()
            self.assertTrue(self.provider._preempt_warm.wait(2))
            self.assertFalse(self.provider.warm_up(self.request).ok)
        finally:
            release.set()
            warmer.join(3)
            if foreground.ident is not None:
                foreground.join(3)
        self.assertFalse(warmer.is_alive())
        self.assertFalse(foreground.is_alive())
        self.assert_failure(warmed[0], "cancelled", False)
        self.assertTrue(translated[0].ok)
        factory.assert_called_once()

    def test_shutdown_waits_for_warm_creation_to_finish_cleaning(self):
        factory = self.idle_factory()
        entered, release, stopped = (threading.Event() for _ in range(3))
        original = factory.side_effect
        children, results = [], []
        def create(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            children.append(original(*args, **kwargs))
            return children[-1]
        def stop():
            self.provider.shutdown(require_cleanup=True)
            stopped.set()
        factory.side_effect = create
        warmer = threading.Thread(target=lambda: results.append(self.provider.warm_up(self.request)))
        stopper = threading.Thread(target=stop)
        warmer.start()
        try:
            self.assertTrue(entered.wait(2))
            stopper.start()
            self.assertTrue(self.provider._closing.wait(2))
            self.assertFalse(stopped.is_set())
        finally:
            release.set()
            warmer.join(3)
            if stopper.ident is not None:
                stopper.join(3)
        self.assertTrue(stopped.is_set())
        self.assert_failure(results[0], "cancelled", False)
        children[0].close.assert_called_once()
        self.transport.assert_not_called()

    def test_warmup_does_not_wait_for_or_interrupt_an_active_foreground(self):
        factory = self.idle_factory()
        entered, release = threading.Event(), threading.Event()
        results = []
        def blocked(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            self.respond(*args, **kwargs)
        self.transport.side_effect = blocked
        worker = threading.Thread(target=lambda: results.append(self.provider.complete(self.request)))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            result = self.provider.warm_up(self.request)
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "warmup_busy")
            factory.assert_not_called()
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(results[0].ok)

    def test_cancelled_or_closed_before_start_does_not_spawn_or_create_workdir(self):
        cancel = threading.Event()
        cancel.set()
        self.assert_failure(self.provider.complete(self.request, cancel), "cancelled", False)
        self.provider.shutdown()
        self.assert_failure(self.provider.complete(self.request), "cancelled", False)
        self.transport.assert_not_called()
        self.makedirs.assert_not_called()

    def test_submission_metrics_distinguish_failed_spawn_from_partial_write(self):
        for wrote in (False, True):
            def fail(*args, **kwargs):
                if wrote:
                    kwargs["on_write"]()
                raise ProcessError("probe_failed")
            self.transport.side_effect = fail
            self.assert_failure(self.provider.complete(self.request), "probe_failed", wrote)
        self.assertEqual(self.transport.call_count, 2)

    def test_successful_json_is_not_success_after_nonzero_exit_or_cleanup_failure(self):
        for code in ("probe_failed", "probe_cleanup_failed"):
            def fail(*args, **kwargs):
                self.respond(*args, **kwargs)
                raise ProcessError(code)
            self.transport.side_effect = fail
            self.assert_failure(self.provider.complete(self.request),
                                "provider_cleanup_failed" if "cleanup" in code else code, True)
        calls = self.transport.call_count
        self.assert_failure(self.provider.complete(self.request), "provider_cleanup_failed", False)
        self.assertEqual(self.transport.call_count, calls)
        with self.assertRaisesRegex(ProcessError, "provider_cleanup_failed"):
            self.provider.shutdown(require_cleanup=True)

    def test_missing_terminal_and_malformed_output_cannot_succeed(self):
        for message in ({"type": "system", "subtype": "init"}, {"type": "result", "result": "PRIVATE"}):
            def output(*args, **kwargs):
                kwargs["on_write"]()
                args[4](json.dumps(message))
            self.transport.side_effect = output
            result = self.provider.complete(self.request)
            self.assertFalse(result.ok)
            self.assertEqual(result.text, "")
            self.assertTrue(dict(result.metrics)["turn_submitted"])
        self.assertEqual(self.transport.call_count, 2)

    def test_cancel_after_write_discards_even_a_success_result(self):
        cancel = threading.Event()
        def respond(*args, **kwargs):
            self.respond(*args, **kwargs)
            cancel.set()
        self.transport.side_effect = respond
        self.assert_failure(self.provider.complete(self.request, cancel), "cancelled", True)
        self.transport.assert_called_once()

    def test_deadline_includes_input_preparation_and_expired_preparation_never_spawns(self):
        clock = [0]
        original = native._input
        def prepare(*args):
            data = original(*args)
            clock[0] = 5
            return data
        with patch.object(native.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(native, "_input", side_effect=prepare):
            self.assert_failure(self.provider.complete(replace(self.request, timeout_seconds=1)), "timeout", False)
        self.transport.assert_not_called()
        self.makedirs.assert_not_called()

    def test_expired_during_transport_does_not_publish_success(self):
        clock = [0]
        def late(*args, **kwargs):
            self.respond(*args, **kwargs)
            clock[0] = 5
        self.transport.side_effect = late
        with patch.object(native.time, "monotonic", side_effect=lambda: clock[0]):
            self.assert_failure(self.provider.complete(replace(self.request, timeout_seconds=1)), "timeout", True)

    def test_protocol_or_output_failure_is_not_retried(self):
        for code in ("provider_protocol_error", "translation_output_limit", "unsafe_tool_event"):
            def fail(*args, **kwargs):
                kwargs["on_write"]()
                raise native.ClaudeOutputError(code)
            self.transport.side_effect = fail
            self.assert_failure(self.provider.complete(self.request), code, True)
        self.assertEqual(self.transport.call_count, 3)

    def test_callback_exception_propagates_and_operation_lock_is_released(self):
        class CallbackError(RuntimeError):
            pass
        def fail(_text):
            raise CallbackError()
        with self.assertRaises(CallbackError):
            self.provider.stream(self.request, fail)
        self.assertIsNone(self.provider._active_thread)
        self.assertTrue(self.provider.complete(self.request).ok)

    def test_reentrant_callback_does_not_spawn_a_second_request(self):
        def nested(_text):
            with self.assertRaisesRegex(RuntimeError, "provider_reentrant_request"):
                self.provider.complete(self.request)
        self.assertTrue(self.provider.stream(self.request, nested).ok)
        self.transport.assert_called_once()

    def test_catalog_and_diagnostics_do_not_invoke_codex_or_a_model(self):
        with self.assertRaisesRegex(CatalogProbeError, "model_catalog_unavailable"):
            self.provider.model_catalog()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(CatalogProbeError, "cancelled"):
            self.provider.model_catalog(cancel)
        with patch.object(native.os.path, "isfile", return_value=True):
            status = self.provider.diagnose()
        self.assertTrue(status.installed)
        self.assertIsNone(status.authenticated)
        self.assertEqual(status.version, "")
        self.assertEqual(status.backend, "native_print")
        self.transport.assert_not_called()

    def test_validation_and_unsupported_tasks_do_not_spawn(self):
        for request in (replace(self.request, task="unknown"), replace(self.request, task="image"),
                        replace(self.request, image_paths=(self.command,))):
            self.assert_failure(self.provider.complete(request), "unsupported_task", False)
        for request in (replace(self.request, timeout_seconds=float("nan")),
                        replace(self.request, timeout_seconds=True),
                        replace(self.request, model=3), replace(self.request, user_text=None),
                        replace(self.request, system_prompt="bad\0argument"),
                        replace(self.request, task="image", image_paths=("relative",))):
            with self.assertRaises(ValueError):
                self.provider.complete(request)
        with self.assertRaises(TypeError):
            self.provider.complete(None)
        with self.assertRaises(TypeError):
            self.provider.stream(self.request, None)
        self.transport.assert_not_called()

    def test_constructor_rejects_ambient_or_invalid_binding_without_discovery(self):
        for kwargs in ({"command": "claude"}, {"work_dir": "relative"},
                       {"environment": {}}, {"environment": {"HOME": self.root, "KEY": None}},
                       {"environment": {"HOME": self.root, "BAD=KEY": "value"}}, {"log_error": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, TypeError)):
                self.make_provider(**kwargs)
        with patch.object(native.sys, "platform", "win32"), self.assertRaises(ValueError):
            self.make_provider()
        self.transport.assert_not_called()

    def test_image_input_uses_exact_base64_png_bytes_not_path_or_tool_reference(self):
        data = PNG_BYTES
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.png"
            path.write_bytes(data)
            with patch.object(native.os, "O_NOFOLLOW", 0, create=True), \
                    patch.object(native.os, "O_NONBLOCK", 0, create=True):
                result = self.provider.complete(replace(
                    self.request, task="image", image_paths=(str(path),)))
            self.assertTrue(result.ok)
            args, _env, _work, payload, _line = self.transport.call_args.args
            self.assertNotIn(str(path), args)
            self.assertNotIn(str(path), payload.decode("utf-8"))
            image = json.loads(payload)["message"]["content"][1]
            self.assertEqual(image["source"]["type"], "base64")
            self.assertEqual(image["source"]["media_type"], "image/png")
            self.assertEqual(base64.b64decode(image["source"]["data"]), data)
            self.assertEqual(path.read_bytes(), data)

    def test_image_validation_cancel_and_size_error_do_not_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.png"
            path.write_bytes(b"not png")
            request = replace(self.request, task="image", image_paths=(str(path),))
            with patch.object(native.os, "O_NOFOLLOW", 0, create=True), \
                    patch.object(native.os, "O_NONBLOCK", 0, create=True):
                self.assert_failure(self.provider.complete(request), "image_unavailable", False)
                with patch.object(native, "MAX_IMAGE_BYTES", 1):
                    self.assert_failure(self.provider.complete(request), "image_too_large", False)
        self.transport.assert_not_called()

    def test_shutdown_waits_for_active_transport_cleanup_and_cancels_queued_requests(self):
        entered, release, closing, finished = (threading.Event() for _ in range(4))
        results = []
        def blocked(*args, **kwargs):
            kwargs["on_write"]()
            entered.set()
            self.assertTrue(release.wait(3))
            self.assertTrue(kwargs["cancel_event"].is_set())
            raise ProcessError("probe_cancelled")
        def shutdown():
            closing.set()
            self.provider.shutdown(require_cleanup=True)
            finished.set()
        self.transport.side_effect = blocked
        worker = threading.Thread(target=lambda: results.append(self.provider.complete(self.request)))
        stop = threading.Thread(target=shutdown)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            stop.start()
            self.assertTrue(closing.wait(2))
            self.assertTrue(self.provider._closing.wait(2))
            self.assertFalse(finished.wait(0.05))
            self.assert_failure(self.provider.complete(self.request), "cancelled", False)
        finally:
            release.set()
            worker.join(3)
            if stop.ident is not None:
                stop.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(stop.is_alive())
        self.assertTrue(finished.is_set())
        self.assertEqual(len(results), 1)
        self.assert_failure(results[0], "cancelled", True)
        self.transport.assert_called_once()
