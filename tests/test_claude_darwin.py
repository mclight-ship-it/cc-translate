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
        self.assertFalse(self.provider.capabilities.warm_sessions)

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
