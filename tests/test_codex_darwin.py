"""Portable native-facade contracts; only the OS/process boundaries are synthetic."""

from collections import deque
from contextlib import ExitStack
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from cc_macos import native_provider_fixture
from cc_macos.catalog_fixture import PAYLOAD, SyntheticCatalog
from cc_macos.native_provider_fixture import NATIVE_CONFIG, TEXT, reply_messages
from cc_providers import codex_appserver, codex_darwin as native
from cc_providers.base import ProviderRequest, ProviderSelection
from cc_providers.codex_cli import build_codex_prompt
from cc_providers.codex_config import CODEX_CONFIG_OVERRIDES, CodexConfigError
from cc_providers.codex_catalog import CatalogProbeError
from cc_providers.darwin_process import ProcessError
from cc_providers.darwin_rpc import RpcError
from cc_request import RequestSnapshot


class ScriptedRpc:
    def __init__(self, harness, args, env, cwd):
        self.harness, self.args, self.env, self.cwd = harness, args, env, cwd
        self.messages, self.sent = deque(), []
        self.closed, self.close_count, self.resets = False, 0, 0
        self.waiting = threading.Event()
        self.close_entered, self.close_release = threading.Event(), threading.Event()
        self.close_release.set()
        self.close_error = None
        self.block_method = harness.block_method

    def is_running(self):
        return not self.closed

    def reset_budget(self):
        self.resets += 1

    def send(self, data, *, deadline, cancel_event=None, on_write=None):
        request = json.loads(data)
        self.sent.append(request)
        if self.harness.send_error and request["method"] == self.harness.send_error[0]:
            if self.harness.send_error[1] and on_write is not None:
                on_write()
            raise RpcError("rpc_io_failed")
        if on_write is not None:
            on_write()
        replies = self.harness.responses(self, request)
        if request["method"] == self.block_method:
            self.waiting.set()
        else:
            self.messages.extend(replies)

    def receive(self, *, deadline, cancel_event=None):
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RpcError("rpc_cancelled")
            if time.monotonic() >= deadline:
                raise RpcError("rpc_timeout")
            if self.messages:
                message = self.messages.popleft()
                if isinstance(message, Exception):
                    raise message
                if message is None or isinstance(message, str):
                    return message
                return json.dumps(message, ensure_ascii=False)
            self.waiting.set()
            time.sleep(0.002)

    def close(self):
        self.close_count += 1
        self.close_entered.set()
        if not self.close_release.wait(3):
            raise AssertionError("synthetic close was not released")
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class TestDarwinCodexProvider(unittest.TestCase):
    def setUp(self):
        self.root = str(Path.cwd() / ".synthetic-native-contract")
        self.home = os.path.join(self.root, "home")
        self.work = os.path.join(self.home, "work")
        self.command = os.path.join(self.root, "synthetic-cli")
        self.cache = os.path.join(self.root, "cache")
        self.environment = {"HOME": self.home, "PATH": "synthetic-path",
                            "SYNTHETIC_SETTING": "original"}
        self.processes, self.providers = [], []
        self.block_method, self.send_error = None, None
        self.responses = lambda proc, request: reply_messages(request, proc.cwd)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(native.sys, "platform", "darwin"))
        self.capture = self.stack.enter_context(patch.object(
            native, "capture_output", return_value=b"codex-cli 0.146.0\n"))
        self.export = self.stack.enter_context(patch(
            "cc_providers.darwin_process.capture_output", return_value=json.dumps(PAYLOAD).encode()))
        self.rpc = self.stack.enter_context(patch.object(native, "RpcProcess", side_effect=self.spawn))
        self.config = self.stack.enter_context(patch(
            "cc_providers.codex_appserver.read_native_config", return_value=NATIVE_CONFIG))
        self.real_catalog_overrides = native.CodexModelCatalog.overrides
        self.catalog = self.stack.enter_context(patch(
            "cc_providers.codex_catalog.CodexModelCatalog.overrides",
            return_value=('model_catalog_json="synthetic-catalog"',)))
        self.makedirs = self.stack.enter_context(patch.object(native.os, "makedirs"))
        self.real_schedule = codex_appserver.CodexAppServerTransport._schedule_idle_shutdown
        self.schedule = self.stack.enter_context(patch(
            "cc_providers.codex_appserver.CodexAppServerTransport._schedule_idle_shutdown"))
        self.popen = self.stack.enter_context(patch(
            "subprocess.Popen", side_effect=AssertionError("portable test spawned a process")))
        self.addCleanup(self.close_providers)

    def close_providers(self):
        for proc in self.processes:
            proc.close_release.set()
            proc.close_error = None
        for provider in self.providers:
            provider.shutdown()

    def spawn(self, args, env, cwd):
        proc = ScriptedRpc(self, args, env, cwd)
        self.processes.append(proc)
        return proc

    def provider(self, **kwargs):
        values = dict(command=self.command, work_dir=self.work, environment=self.environment,
                      catalog_cache_dir=self.cache, log_error=Mock())
        values.update(kwargs)
        provider = native.DarwinCodexProvider(**values)
        self.providers.append(provider)
        return provider

    @staticmethod
    def request(**kwargs):
        return replace(ProviderRequest("text", "synthetic", " Translate only 中🙂\n",
                                       "  synthetic input 中🙂\r\n", timeout_seconds=1), **kwargs)

    @staticmethod
    def methods(proc):
        return [request["method"] for request in proc.sent]

    def assert_failure(self, result, code, submitted=False):
        self.assertFalse(result.ok)
        self.assertEqual((result.error_code, result.error_detail, result.text), (code, "", ""))
        self.assertIs(dict(result.metrics)["turn_submitted"], submitted)
        self.assertNotIn("SYNTHETIC_PRIVATE", repr(result))

    def assert_no_activity(self):
        self.rpc.assert_not_called()
        self.capture.assert_not_called()
        self.config.assert_not_called()
        self.catalog.assert_not_called()
        self.makedirs.assert_not_called()
        self.popen.assert_not_called()
        self.export.assert_not_called()

    def start(self, function):
        result, errors = [], []
        def run():
            try:
                result.append(function())
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, result, errors

    def finish(self, running):
        thread, result, errors = running
        thread.join(3)
        self.assertFalse(thread.is_alive(), "native operation or lock leaked")
        self.assertEqual(errors, [])
        self.assertEqual(len(result), 1)
        return result[0]

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("synthetic operation did not reach its synchronization point")
            time.sleep(0.002)

    def test_catalog_is_explicit_read_only_no_version_config_rpc_or_model_submission(self):
        provider = self.provider()
        self.assert_no_activity()
        self.export.return_value = native_provider_fixture.catalog_export("metadata")
        self.assertEqual(provider.model_catalog(), native_provider_fixture.CATALOG_EXPECTED)
        self.capture.assert_not_called()
        self.config.assert_not_called()
        self.catalog.assert_not_called()
        self.rpc.assert_not_called()
        self.assertEqual(self.export.call_args.args[:3],
                         ([self.command, "debug", "models",
                           *[part for value in CODEX_CONFIG_OVERRIDES for part in ("-c", value)]],
                          provider.env, self.work))
        self.assertIsNone(provider._transport.operation)
        self.assertIsNone(provider._active_thread)
        self.assertEqual(provider._foreground_waiters, 0)
        self.assertEqual(provider._catalog.status, "not_checked")
        self.assertEqual(provider.model_catalog(), native_provider_fixture.CATALOG_EXPECTED)
        self.assertEqual(self.export.call_count, 2)

    def test_catalog_failure_is_sanitized_and_does_not_block_later_explicit_translation(self):
        provider = self.provider()
        for output in (b"SYNTHETIC_PRIVATE_CATALOG", b'{"models":null}', b'{"models":[{"slug":null}]}'):
            with self.subTest(output=output):
                self.export.return_value = output
                with self.assertRaisesRegex(CatalogProbeError, "^model_catalog_failed$"):
                    provider.model_catalog()
                self.assertIsNone(provider._fatal)
        self.rpc.assert_not_called()
        self.catalog.assert_not_called()
        self.assertEqual(self.export.call_count, 3)
        result = provider.complete(self.request())
        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(result.text, TEXT)
        self.assertEqual(self.methods(self.processes[-1]).count("turn/start"), 1)

    def test_catalog_export_limit_and_timeout_are_distinct_from_fatal_cleanup(self):
        provider = self.provider()
        for code, expected in (("probe_output_limit", "model_catalog_too_large"),
                               ("probe_timeout", "model_catalog_failed"),
                               ("probe_failed", "model_catalog_failed")):
            with self.subTest(code=code):
                self.export.side_effect = ProcessError(code)
                with self.assertRaisesRegex(CatalogProbeError, "^" + expected + "$"):
                    provider.model_catalog()
                self.assertIsNone(provider._fatal)
                self.assertIsNone(provider._active_thread)
        self.assertEqual(self.export.call_count, 3)
        self.rpc.assert_not_called()

    def test_catalog_precancel_and_cancelled_provider_lock_wait_never_launch(self):
        provider = self.provider()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(CatalogProbeError, "^cancelled$"):
            provider.model_catalog(cancel)
        cancel.clear()
        with provider._operation_lock:
            running = self.start(lambda: provider.model_catalog(cancel))
            self.wait_for(lambda: provider._foreground_waiters == 1)
            cancel.set()
            running[0].join(2)
            self.assertFalse(running[0].is_alive())
        self.assertEqual([str(error) for error in running[2]], ["cancelled"])
        self.assert_no_activity()
        self.assertEqual(provider._foreground_waiters, 0)

    def test_catalog_cancellation_drains_before_releasing_lock_and_later_translation(self):
        provider = self.provider()
        cancel, entered, release = threading.Event(), threading.Event(), threading.Event()
        def capture(*_args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            self.assertTrue(kwargs["cancel_event"].is_set())
            raise ProcessError("probe_cancelled")
        self.export.side_effect = capture
        running = self.start(lambda: provider.model_catalog(cancel))
        try:
            self.assertTrue(entered.wait(1))
            cancel.set()
            later = self.start(lambda: provider.complete(self.request()))
            self.assertTrue(running[0].is_alive())
            self.assertTrue(later[0].is_alive())
            self.rpc.assert_not_called()
        finally:
            release.set()
            running[0].join(3)
        self.assertEqual([str(error) for error in running[2]], ["cancelled"])
        self.assertTrue(self.finish(later).ok)
        self.assertIsNone(provider._active_thread)

    def test_catalog_shutdown_signals_active_capture_and_waits_for_drain(self):
        provider = self.provider()
        entered, release = threading.Event(), threading.Event()
        def capture(*_args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            self.assertTrue(kwargs["cancel_event"].is_set())
            raise ProcessError("probe_cancelled")
        self.export.side_effect = capture
        running = self.start(provider.model_catalog)
        try:
            self.assertTrue(entered.wait(1))
            shutdown = self.start(provider.shutdown)
            self.assertTrue(provider._closing.wait(1))
            self.assertTrue(shutdown[0].is_alive())
        finally:
            release.set()
            running[0].join(3)
        self.assertEqual([str(error) for error in running[2]], ["appserver_shutdown"])
        self.assertIsNone(self.finish(shutdown))
        with self.assertRaisesRegex(CatalogProbeError, "^appserver_shutdown$"):
            provider.model_catalog()
        self.assertEqual(self.export.call_count, 1)

    def test_catalog_fatal_cleanup_wins_over_cancel_and_poisons_later_requests(self):
        provider = self.provider()
        self.assertTrue(provider.complete(self.request()).ok)
        cancel = threading.Event()
        def fail(*_args, **_kwargs):
            cancel.set()
            raise ProcessError("probe_cleanup_failed")
        self.export.side_effect = fail
        with self.assertRaisesRegex(CatalogProbeError, "^provider_cleanup_failed$"):
            provider.model_catalog(cancel)
        self.assertTrue(self.processes[0].closed)
        with self.assertRaisesRegex(CatalogProbeError, "^provider_cleanup_failed$"):
            provider.model_catalog()
        self.assert_failure(provider.complete(self.request()), "provider_cleanup_failed")
        self.assert_failure(provider.warm_up("synthetic"), "provider_cleanup_failed")
        self.assertEqual(self.export.call_count, 1)

    def test_catalog_reentrant_call_is_rejected_without_mutating_active_translation(self):
        provider = self.provider()
        errors = []
        def delta(_text):
            with self.assertRaisesRegex(RuntimeError, "^provider_reentrant_request$") as error:
                provider.model_catalog()
            errors.append(str(error.exception))
            self.assertIsNotNone(provider._transport.operation)
        self.assertTrue(provider.stream(self.request(), delta).ok)
        self.assertEqual(errors, ["provider_reentrant_request"])
        self.export.assert_not_called()
        self.assertIsNone(provider._active_thread)
        self.assertEqual(provider._foreground_waiters, 0)

    def test_catalog_deadline_and_prior_cleanup_failure_do_not_admit_new_capture(self):
        provider = self.provider()
        def expired(*_args, **kwargs):
            operation = kwargs["cancel_event"]
            operation.deadline = time.monotonic() - 1
            return json.dumps(PAYLOAD).encode()
        self.export.side_effect = expired
        with self.assertRaisesRegex(CatalogProbeError, "^timeout$"):
            provider.model_catalog()
        provider._transport.cleanup_failed.set()
        with self.assertRaisesRegex(CatalogProbeError, "^provider_cleanup_failed$"):
            provider.model_catalog()
        self.assertEqual(self.export.call_count, 1)
        self.rpc.assert_not_called()

    def test_catalog_fixture_data_and_debug_dispatch_are_independent_of_translation_scenario(self):
        from cc_macos import catalog_process_fixture, translation_fixture
        from cc_providers.codex_catalog import catalog_models

        with tempfile.TemporaryDirectory(prefix=".catalog-fixture-", dir=Path.cwd()) as directory:
            fixture = translation_fixture.prepare(Path(directory) / "prepared", "synthetic")
            root = Path(fixture["root"])
            before = (root / "expected-request.json").read_bytes()
            with patch.object(sys, "path", list(sys.path)), \
                    patch.object(sys, "argv", [fixture["command"], "debug", "models"]), \
                    patch.dict(os.environ, fixture["environment"]), \
                    patch.object(Path, "cwd", return_value=Path(fixture["home"]) / "work"), \
                    patch.object(catalog_process_fixture, "_serve") as serve:
                (root / "catalog-mode.txt").write_text("metadata", encoding="utf-8")
                native_provider_fixture._serve()
                self.assertEqual(catalog_models(json.loads(serve.call_args.kwargs["export_output"])),
                                 native_provider_fixture.CATALOG_EXPECTED)
                (root / "catalog-mode.txt").write_text("normal", encoding="utf-8")
                native_provider_fixture._serve()
                self.assertIsNone(serve.call_args.kwargs["export_output"])
                self.assertEqual(serve.call_count, 2)
            self.assertEqual((root / "expected-request.json").read_bytes(), before)
            for name in ("calls.jsonl", "version.jsonl", "native-rpc.jsonl", "native-processes.jsonl"):
                self.assertFalse((root / name).exists())
        for mode in ("metadata", "empty", "wrong_shape", "bad_id", "oversized"):
            self.assertIsInstance(json.loads(native_provider_fixture.catalog_export(mode)), dict)
        with self.assertRaisesRegex(ValueError, "^synthetic_catalog_mode_required$"):
            native_provider_fixture.catalog_export("unknown")

    def test_constructor_has_no_process_filesystem_or_host_home_side_effects(self):
        with patch("builtins.open", side_effect=AssertionError("constructor I/O")), \
                patch.object(Path, "home", side_effect=AssertionError("host home lookup")), \
                patch.object(Path, "stat", side_effect=AssertionError("constructor stat")):
            provider = self.provider()
        self.assertEqual(provider._catalog._user_home, Path(self.home))
        self.assert_no_activity()

    def test_facade_passes_its_exact_operation_lock_and_logger_to_native_transport(self):
        logger = Mock()
        provider = self.provider(log_error=logger)
        self.assertIs(provider._transport._owner_operation_lock, provider._operation_lock)
        self.assertIs(provider._transport._log_error, logger)
        self.assert_no_activity()

    def test_environment_is_copied_frozen_and_codex_home_uses_explicit_home(self):
        with patch.dict(os.environ, {"HOST_SECRET": "SYNTHETIC_PRIVATE_HOST",
                                     "CODEX_HOME": "SYNTHETIC_PRIVATE_HOME"}):
            provider = self.provider()
            self.environment["SYNTHETIC_SETTING"] = "changed"
            self.environment["NEW"] = "not inherited"
            self.assertEqual(dict(provider.env), {
                "HOME": self.home, "PATH": "synthetic-path", "SYNTHETIC_SETTING": "original",
                "CODEX_HOME": os.path.join(self.home, ".codex")})
            with self.assertRaises(TypeError):
                provider.env["HOME"] = "changed"
            self.assertTrue(provider.complete(self.request()).ok)
        self.assertEqual(dict(self.processes[0].env), dict(provider.env))
        self.assertEqual(self.capture.call_args.args[1], provider.env)

    def test_explicit_codex_home_override_never_expands_host_syntax(self):
        selected = os.path.join(self.home, "selected")
        environment = {**self.environment, "CODEX_HOME": os.path.join(self.home, "old"),
                       "CC_TRANSLATE_CODEX_HOME": selected}
        self.assertEqual(self.provider(environment=environment).env["CODEX_HOME"], selected)
        for value in ("relative", "~", "$HOME"):
            with self.subTest(value=value), self.assertRaises(CodexConfigError):
                self.provider(environment={**self.environment, "CODEX_HOME": value})
        self.assert_no_activity()

    def test_constructor_rejects_non_darwin(self):
        with patch.object(native.sys, "platform", "win32"), self.assertRaisesRegex(ValueError, "darwin"):
            self.provider()
        self.assert_no_activity()

    def test_constructor_rejects_nonabsolute_paths_and_work_outside_home(self):
        for kwargs in (
                {"command": "codex"}, {"work_dir": "work"}, {"catalog_cache_dir": "cache"},
                {"work_dir": self.root}, {"environment": {"HOME": "relative"}},
                {"environment": {}}, {"command": self.command + "\0"},
                {"work_dir": os.path.join(self.home, "child", "..", "work")},
                {"environment": {"HOME": os.path.join(self.home, "..", "home")}},
                {"catalog_cache_dir": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, TypeError)):
                self.provider(**kwargs)
        self.assert_no_activity()

    def test_constructor_rejects_invalid_environment_and_logger(self):
        for value in (None, [], {"HOME": self.home, "bad=key": "x"},
                      {"HOME": self.home, "bad": 1}, {"HOME": self.home, "": "x"},
                      {"HOME": self.home, "bad": "x\0"}):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                self.provider(environment=value)
        with self.assertRaises(TypeError):
            self.provider(log_error=None)
        self.assert_no_activity()

    def test_unsupported_image_and_task_are_rejected_without_any_probe(self):
        provider = self.provider()
        for request in (self.request(task="image"), self.request(task="translate"),
                        self.request(image_paths=["synthetic.png"])):
            self.assert_failure(provider.complete(request), "unsupported_task")
        self.assert_no_activity()

    def test_invalid_request_and_callback_are_rejected_without_any_probe(self):
        provider = self.provider()
        for request in (None, {}, self.request(timeout_seconds=True),
                        self.request(timeout_seconds=0), self.request(timeout_seconds=-1),
                        self.request(timeout_seconds=float("nan")),
                        self.request(timeout_seconds=float("inf")),
                        self.request(user_text=1), self.request(system_prompt=None),
                        self.request(model=1), self.request(image_paths=None),
                        self.request(user_text="\ud800")):
            with self.subTest(request=request), self.assertRaises((TypeError, ValueError)):
                provider.complete(request)
        with self.assertRaises(TypeError):
            provider.stream(self.request(), None)
        self.assert_no_activity()

    def test_huge_integer_timeouts_are_rejected_without_overflow_or_activity(self):
        provider = self.provider()
        for exponent in (1024, 4096, 16384):
            with self.subTest(exponent=exponent):
                for sign in (1, -1):
                    with self.assertRaisesRegex(ValueError, "^invalid_provider_request$"):
                        provider.complete(self.request(timeout_seconds=sign * (1 << exponent)))
        self.assert_no_activity()

    def test_largest_finite_float_and_integer_timeouts_remain_valid(self):
        provider = self.provider()
        for timeout in (sys.float_info.max, int(sys.float_info.max)):
            with self.subTest(type=type(timeout).__name__):
                self.assertTrue(provider.complete(self.request(timeout_seconds=timeout)).ok)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.methods(self.processes[0]).count("turn/start"), 2)

    def test_complete_and_stream_both_use_appserver_never_exec(self):
        provider, deltas = self.provider(), []
        self.assertEqual(provider.complete(self.request()).text, TEXT)
        self.assertEqual(provider.stream(self.request(), deltas.append).text, TEXT)
        self.assertEqual(deltas, [TEXT])
        self.assertEqual(len(self.processes), 1)
        proc = self.processes[0]
        self.assertEqual(self.methods(proc), [
            "initialize", "initialized", "hooks/list", "thread/start", "turn/start",
            "hooks/list", "thread/start", "turn/start"])
        self.assertEqual(proc.args[:5], [self.command, "app-server", "--listen", "stdio://",
                                        "--strict-config"])
        self.assertNotIn("exec", proc.args)
        self.assertEqual(proc.resets, 2)
        self.config.assert_called_once()
        self.catalog.assert_called_once()
        self.popen.assert_not_called()

    def test_blank_and_crlf_frames_are_ignored_without_resetting_operation_budget(self):
        def responses(proc, request):
            return [line for reply in reply_messages(request, proc.cwd)
                    for line in ("", "\r", " \t\r", json.dumps(reply, ensure_ascii=False) + "\r")]
        self.responses = responses
        provider, deltas = self.provider(), []
        result = provider.stream(self.request(), deltas.append)
        self.assertTrue(result.ok)
        self.assertEqual((result.text, deltas), (TEXT, [TEXT]))
        self.assertEqual(self.processes[0].resets, 1)
        self.assertEqual(provider.complete(self.request()).text, TEXT)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.processes[0].resets, 2)
        self.assertEqual(self.methods(self.processes[0]).count("turn/start"), 2)

    def test_eof_after_only_blank_frames_is_not_ignored_or_retried(self):
        self.responses = lambda proc, request: ["", "\r", " \t\r", None]
        self.assert_failure(self.provider().complete(self.request()), "appserver_exited")
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.processes[0].resets, 1)
        self.assertEqual(self.methods(self.processes[0]), ["initialize"])
        self.assertTrue(self.processes[0].closed)

    def test_prewarm_initializes_and_validates_hooks_without_thread_or_turn(self):
        provider = self.provider()
        result = provider.warm_up("synthetic")
        self.assertTrue(result.ok)
        self.assertIs(dict(result.metrics)["turn_submitted"], False)
        self.assertEqual(self.methods(self.processes[0]), ["initialize", "initialized", "hooks/list"])
        self.assertTrue(provider.warm_up("synthetic").ok)
        self.assertTrue(provider.complete(self.request()).ok)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.methods(self.processes[0]).count("initialize"), 1)
        self.assertEqual(self.methods(self.processes[0]).count("turn/start"), 1)

    def test_diagnose_is_native_prewarm_not_a_model_request(self):
        provider = self.provider()
        with patch.object(os.path, "isfile", return_value=True):
            status = provider.diagnose()
        self.assertEqual((status.installed, status.authenticated, status.command, status.backend),
                         (True, None, self.command, "native_appserver"))
        self.assertEqual((status.error_code, status.error_detail), ("", ""))
        self.assertEqual(self.methods(self.processes[0]), ["initialize", "initialized", "hooks/list"])
        self.assertEqual(provider.capabilities.images, False)
        self.assertEqual(provider.capabilities.warm_sessions, True)

    def test_translation_summary_uses_unchanged_shared_prompt(self):
        request = self.request(task="translation_summary")
        self.assertTrue(self.provider().complete(request).ok)
        turn = next(r["params"] for r in self.processes[0].sent if r["method"] == "turn/start")
        self.assertEqual(turn["input"][0]["text"].encode("utf-8"),
                         build_codex_prompt(request).encode("utf-8"))

    def test_prompt_bytes_and_safety_overrides_are_not_rewritten(self):
        provider, request = self.provider(), self.request()
        self.assertTrue(provider.complete(request).ok)
        proc = self.processes[0]
        overrides = proc.args[6::2]
        self.assertEqual(overrides[:len(CODEX_CONFIG_OVERRIDES)], list(CODEX_CONFIG_OVERRIDES))
        self.assertIn('mcp_servers={"fixture.node"={enabled=false}}', overrides)
        self.assertEqual(overrides[-1], 'model_catalog_json="synthetic-catalog"')
        self.assertEqual(self.config.call_args.args, (self.command, provider.env, self.work))
        self.assertIs(self.catalog.call_args.kwargs["native_config"], NATIVE_CONFIG)
        thread = next(r["params"] for r in proc.sent if r["method"] == "thread/start")
        turn = next(r["params"] for r in proc.sent if r["method"] == "turn/start")
        self.assertEqual(thread["model"], "synthetic")
        self.assertEqual((thread["ephemeral"], thread["approvalPolicy"], thread["sandbox"]),
                         (True, "never", "read-only"))
        self.assertEqual(thread["config"], {"mcp_servers": {}, "web_search": "disabled"})
        self.assertEqual(turn["input"], [{"type": "text", "text": build_codex_prompt(request)}])
        self.assertEqual(turn["input"][0]["text"].encode(), build_codex_prompt(request).encode())
        self.assertEqual(turn["sandboxPolicy"], {"type": "readOnly", "networkAccess": False})
        self.assertEqual(turn["approvalPolicy"], "never")

    def test_synthetic_launcher_reuses_catalog_setup_and_explicit_home(self):
        with tempfile.TemporaryDirectory(prefix=".cc-native-fixture-", dir=Path.cwd()) as directory:
            root = Path(directory) / "synthetic work 中 # %"
            command, environment, work, cache, warnings = native_provider_fixture.create_cli(root)
            self.assertEqual(warnings, [])
            self.assertTrue(Path(command).is_relative_to(root))
            self.assertTrue(Path(work).is_relative_to(Path(environment["HOME"])))
            self.assertEqual(Path(environment["HOME"]), root / "home")
            self.assertEqual(Path(environment["CODEX_HOME"]), root / "home")
            self.assertEqual(cache, root / "cache")
            launcher = Path(command).read_text(encoding="utf-8")
            self.assertTrue(launcher.startswith("#!/bin/sh\nexec "))
            self.assertIn(" -I -B ", launcher)
            self.assertIn("native_provider_fixture.py", launcher)
            self.assertNotIn("codex exec", launcher)
            self.assertEqual(json.loads((root / "overrides.json").read_bytes()),
                             list(CODEX_CONFIG_OVERRIDES))
            self.assertFalse((root / "calls.jsonl").exists())
            self.assertFalse(cache.exists())
        self.rpc.assert_not_called()
        self.popen.assert_not_called()

    def test_synthetic_server_accepts_actual_facade_command_and_prompt_transcript(self):
        with tempfile.TemporaryDirectory(prefix=".cc-native-fixture-", dir=Path.cwd()) as directory:
            root = Path(directory) / "synthetic"
            command, environment, work, cache, _warnings = native_provider_fixture.create_cli(root)
            cache.mkdir()
            catalog = cache / "models.json"
            catalog.write_bytes((root / "payload.json").read_bytes())
            self.catalog.return_value = ("model_catalog_json=" + json.dumps(str(catalog)),)
            provider = self.provider(command=command, environment=environment, work_dir=work,
                                     catalog_cache_dir=str(cache))
            request = self.request(system_prompt=native_provider_fixture.SYSTEM_PROMPT,
                                   user_text=native_provider_fixture.USER_TEXT)
            self.assertTrue(provider.complete(request).ok)
            proc = self.processes[0]
            transcript = "\n".join(json.dumps(item) for item in proc.sent) + "\n"
            output = io.StringIO()
            with patch.dict(os.environ, environment, clear=True), \
                    patch.object(sys, "path", list(sys.path)), \
                    patch.object(sys, "argv", [str(Path(native_provider_fixture.__file__)), *proc.args[1:]]), \
                    patch.object(sys, "stdin", io.StringIO(transcript)), \
                    patch.object(sys, "stdout", output), \
                    patch.object(Path, "cwd", return_value=Path(work)), \
                    patch.object(os, "getpgrp", return_value=111, create=True), \
                    patch.object(os, "getsid", return_value=111, create=True), \
                    patch.object(native_provider_fixture, "_receipt") as receipt:
                native_provider_fixture._serve()
            expected = [reply for item in proc.sent for reply in reply_messages(item, work)]
            self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()], expected)
            rpc_receipts = [call.args[2]["request"] for call in receipt.call_args_list
                            if call.args[1] == "native-rpc.jsonl"]
            self.assertEqual(rpc_receipts, proc.sent)
        self.popen.assert_not_called()

    def test_explicit_catalog_home_never_looks_up_host_home_or_scans_ancestors(self):
        with tempfile.TemporaryDirectory(prefix=".cc-native-fixture-", dir=Path.cwd()) as directory:
            root = Path(directory) / "synthetic"
            command, environment, work, cache, _warnings = native_provider_fixture.create_cli(root)
            home = Path(environment["HOME"])
            for parent in (root, home):
                marker = parent / ".codex"
                marker.mkdir()
                (marker / "config.toml").write_text(
                    'model_provider="SYNTHETIC_OUTSIDE_BOUNDARY"\n', encoding="utf-8")
            warnings, scanned = [], []
            original_is_file = Path.is_file
            def is_file(path):
                if path.name == "config.toml" and path.parent.name == ".codex":
                    scanned.append(path)
                return original_is_file(path)
            # Override the existing harness patch so this exercises actual
            # catalog resolution/storage with only SyntheticCatalog._run fake.
            with patch.object(native.CodexModelCatalog, "overrides", self.real_catalog_overrides), \
                    patch.object(Path, "home", side_effect=AssertionError("ambient home read")), \
                    patch.object(Path, "is_file", is_file):
                manager = SyntheticCatalog(command, environment, cache, work,
                    user_home=str(home), log_error=lambda where, error: warnings.append((where, str(error))))
                result = manager.overrides(native_config=NATIVE_CONFIG)
                self.assertEqual(manager.status, "ready")
                self.assertEqual(len(manager.calls), 3)
                self.assertEqual(scanned, [Path(work) / ".codex" / "config.toml"])
                catalog = Path(json.loads(result[0].split("=", 1)[1]))
                self.assertEqual(json.loads(catalog.read_bytes()), PAYLOAD)
                reopened = SyntheticCatalog(command, environment, cache, work,
                    user_home=str(home), log_error=lambda where, error: warnings.append((where, str(error))))
                self.assertEqual(reopened.overrides(native_config=NATIVE_CONFIG), result)
                self.assertEqual(len(reopened.calls), 1)
            self.assertEqual(warnings, [])
        self.rpc.assert_not_called()
        self.popen.assert_not_called()

    def test_changed_model_closes_old_process_and_starts_exactly_one_new_process(self):
        provider = self.provider()
        self.assertTrue(provider.complete(self.request()).ok)
        self.assertTrue(provider.complete(self.request(model="synthetic-small")).ok)
        self.assertEqual(len(self.processes), 2)
        self.assertEqual(self.processes[0].close_count, 1)
        self.assertEqual(self.methods(self.processes[1]).count("turn/start"), 1)

    def test_old_unreadable_and_prerelease_versions_never_start_a_turn(self):
        for version, code in (
                (b"codex-cli 0.145.0", "appserver_version_unsupported"),
                (b"\xff", "appserver_version_unreadable"),
                (b"warning 0.146.0", "appserver_version_unreadable"),
                (b"codex-cli 0.147.0-rc.1", "appserver_version_prerelease")):
            with self.subTest(version=version):
                self.capture.return_value = version
                self.assert_failure(self.provider().complete(self.request()), code)
        self.rpc.assert_not_called()
        self.config.assert_not_called()

    def test_newer_versions_still_require_real_protocol_checks(self):
        normal_responses = self.responses
        for version in (b"codex-cli 0.147.0", b"codex-cli 1.0.0+build"):
            with self.subTest(version=version):
                self.responses = normal_responses
                self.capture.return_value = version
                provider = self.provider()
                self.assertTrue(provider.complete(self.request()).ok)
                provider.shutdown()
                self.malformed_response({"id": True, "result": {}}, "invalid_appserver_message")

    def test_version_failure_does_not_replay_or_make_a_later_explicit_request_sticky(self):
        provider = self.provider()
        self.capture.return_value = b"unknown SYNTHETIC_PRIVATE 0.146.0"
        self.assert_failure(provider.complete(self.request()), "appserver_version_unreadable")
        self.rpc.assert_not_called()
        self.capture.return_value = b"codex-cli 0.154.0"
        self.assertTrue(provider.complete(self.request()).ok)
        self.assertEqual(self.capture.call_count, 2)
        self.assertEqual(sum(self.methods(process).count("turn/start") for process in self.processes), 1)

    def malformed_response(self, message, code="invalid_appserver_message"):
        self.responses = lambda proc, request: [message] if request["method"] == "initialize" else []
        start = len(self.processes)
        self.assert_failure(self.provider().complete(self.request()), code)
        self.assertEqual(len(self.processes), start + 1)
        self.assertEqual(self.methods(self.processes[-1]), ["initialize"])
        self.assertEqual(self.processes[-1].close_count, 1)

    def test_missing_wrong_boolean_and_unexpected_rpc_ids_are_rejected(self):
        for message in ({"result": {}}, {"id": 99, "result": {}}, {"id": True, "result": {}},
                        {"id": "1", "result": {}}, {"id": None, "result": {}}):
            with self.subTest(message=message):
                self.malformed_response(message)

    def test_envelope_requires_exactly_result_or_error_and_no_unknown_fields(self):
        for message in ({"id": 1}, {"id": 1, "result": {}, "error": {}},
                        {"id": 1, "result": {}, "extra": "SYNTHETIC_PRIVATE"},
                        {"id": 1, "result": {}, "jsonrpc": "1.0"},
                        {"id": 1, "result": []}):
            with self.subTest(message=message):
                self.malformed_response(message)

    def test_official_timestamped_startup_notification_allows_prewarm_and_reuse(self):
        def responses(proc, request):
            replies = reply_messages(request, proc.cwd)
            if request["method"] == "hooks/list":
                replies.insert(0, {
                    "method": "remoteControl/status/changed",
                    "params": {"status": "disabled", "serverName": "synthetic",
                               "installationId": "synthetic", "environmentId": None},
                    "emittedAtMs": 1789560000000,
                })
            return replies
        self.responses = responses
        provider = self.provider()
        warm = provider.warm_up("synthetic")
        self.assertTrue(warm.ok, warm.error_code)
        self.assertIs(dict(warm.metrics)["turn_submitted"], False)
        self.assertEqual(self.methods(self.processes[0]), ["initialize", "initialized", "hooks/list"])
        for _ in range(2):
            result = provider.complete(self.request())
            self.assertTrue(result.ok, result.error_code)
            self.assertEqual(result.text, TEXT)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.methods(self.processes[0]).count("turn/start"), 2)

    def test_timestamp_does_not_allow_unknown_or_unsafe_startup_notifications(self):
        for method, code in (("synthetic/unknown", "unknown_appserver_event"),
                             ("hook/started", "unsafe_tool_event"),
                             ("command/exec/outputDelta", "unsafe_tool_event")):
            with self.subTest(method=method):
                self.malformed_response(
                    {"method": method, "params": {}, "emittedAtMs": 1789560000000}, code)

    def test_notification_timestamp_accepts_nullable_signed_i64_without_affecting_stream(self):
        for timestamp in (None, -(2 ** 63), -1, 0, 1789560000000, 2 ** 63 - 1):
            def responses(proc, request):
                replies = reply_messages(request, proc.cwd)
                if request["method"] == "initialize":
                    replies.insert(0, {"method": "warning", "params": {}})
                for message in replies:
                    if "method" in message:
                        message["emittedAtMs"] = timestamp
                return replies
            self.responses = responses
            with self.subTest(timestamp=timestamp):
                deltas = []
                result = self.provider().stream(self.request(), deltas.append)
                self.assertTrue(result.ok, result.error_code)
                self.assertEqual((result.text, deltas), (TEXT, [TEXT]))
                self.assertIs(dict(result.metrics)["turn_submitted"], True)

    def test_invalid_notification_timestamp_is_rejected_before_submission(self):
        for timestamp in (True, False, 1.0, 0.5, "1789560000000", [], {}, 2 ** 63, -(2 ** 63) - 1):
            with self.subTest(timestamp=timestamp):
                self.malformed_response({"method": "warning", "params": {}, "emittedAtMs": timestamp})

    def test_timestamp_is_notification_only_and_cannot_hide_duplicate_or_response_fields(self):
        for message in ({"id": 1, "result": {}, "emittedAtMs": 1},
                        {"id": 1, "error": {}, "emittedAtMs": None},
                        {"method": "warning", "params": {}, "result": {}, "emittedAtMs": 1},
                        {"method": "warning", "params": {}, "extra": None, "emittedAtMs": 1}):
            with self.subTest(message=message):
                self.malformed_response(message)
        self.malformed_response(
            '{"method":"warning","params":{},"emittedAtMs":1,"emittedAtMs":2}',
            "invalid_appserver_json")

    def test_response_cannot_smuggle_notification_params(self):
        for extra in ({}, None, {"SYNTHETIC_PRIVATE": "notification"}):
            with self.subTest(extra=extra):
                self.malformed_response({"id": 1, "result": {}, "params": extra})

    def test_notification_cannot_smuggle_response_fields(self):
        for key in ("result", "error"):
            def responses(proc, request):
                replies = reply_messages(request, proc.cwd)
                if request["method"] == "turn/start":
                    replies[1][key] = {"SYNTHETIC_PRIVATE": "response"}
                return replies
            self.responses = responses
            with self.subTest(key=key):
                deltas = []
                self.assert_failure(self.provider().stream(self.request(), deltas.append),
                                    "invalid_appserver_message", True)
                self.assertEqual(deltas, [])
                self.assertEqual(self.methods(self.processes[-1]).count("turn/start"), 1)

    def test_json_duplicate_keys_nonfinite_depth_and_surrogates_are_rejected(self):
        for line in ('{"id":1,"id":1,"result":{}}', '{"id":1,"result":{"n":NaN}}',
                     '{"id":1,"result":{"n":Infinity}}', '{"id":1,"result":{"n":1e999}}',
                     '{"id":1,"result":{"x":"\\ud800"}}',
                     '{"id":1,"result":' + "[" * 66 + "0" + "]" * 66 + "}"):
            with self.subTest(line=line[:70]):
                self.malformed_response(line, "invalid_appserver_json")

    def test_duplicate_and_late_rpc_responses_cannot_be_reused(self):
        for late in (False, True):
            first = []
            def responses(proc, request):
                replies = reply_messages(request, proc.cwd)
                if request["method"] == "initialize":
                    first[:] = replies
                    return replies if late else replies * 2
                if request["method"] == "hooks/list" and late:
                    return first + replies
                return replies
            self.responses = responses
            with self.subTest(late=late):
                self.assert_failure(self.provider().complete(self.request()), "invalid_appserver_message")
                self.assertNotIn("turn/start", self.methods(self.processes[-1]))

    def test_native_rpc_error_redacts_server_message(self):
        self.malformed_response({"id": 1, "error": {"message": "SYNTHETIC_PRIVATE_PROVIDER"}},
                                "appserver_request_failed")

    def test_server_requests_are_rejected_before_submission(self):
        self.malformed_response({"id": 1, "method": "command/exec", "params": {}},
                                "unsafe_tool_event")

    def test_invalid_thread_and_turn_start_shapes_are_fatal(self):
        for method, value in (("thread/start", None), ("thread/start", {"thread": {"id": ""}}),
                              ("turn/start", {"turn": {"id": 1}})):
            def responses(proc, request):
                return ([{"id": request["id"], "result": value}]
                        if request["method"] == method else reply_messages(request, proc.cwd))
            self.responses = responses
            with self.subTest(method=method, value=value):
                self.assert_failure(self.provider().complete(self.request()),
                                    "invalid_appserver_message", method == "turn/start")
                self.assertEqual(self.methods(self.processes[-1]).count("turn/start"),
                                 int(method == "turn/start"))

    def test_missing_wrong_and_empty_delta_identity_are_fatal_after_submit(self):
        for key, value in (("threadId", None), ("threadId", "other"), ("threadId", ""),
                           ("turnId", None), ("turnId", "other"), ("itemId", None),
                           ("itemId", 1), ("itemId", "")):
            def responses(proc, request):
                replies = reply_messages(request, proc.cwd)
                if request["method"] == "turn/start":
                    if value is None:
                        replies[1]["params"].pop(key)
                    else:
                        replies[1]["params"][key] = value
                return replies
            self.responses = responses
            deltas = []
            with self.subTest(key=key, value=value):
                self.assert_failure(self.provider().stream(self.request(), deltas.append),
                                    "invalid_appserver_message", True)
                self.assertEqual(deltas, [])

    def test_started_and_completed_items_require_nonempty_item_id_and_matching_turn(self):
        for method in ("item/started", "item/completed"):
            for key, value in (("id", None), ("id", ""), ("id", 1),
                               ("turnId", None), ("turnId", "other"), ("threadId", "other")):
                def responses(proc, request):
                    replies = reply_messages(request, proc.cwd)
                    if request["method"] == "turn/start":
                        replies[2]["method"] = method
                        params = replies[2]["params"]
                        target = params["item"] if key == "id" else params
                        if value is None:
                            target.pop(key)
                        else:
                            target[key] = value
                    return replies
                self.responses = responses
                with self.subTest(method=method, key=key, value=value):
                    self.assert_failure(self.provider().complete(self.request()),
                                        "invalid_appserver_message", True)

    def test_sdk_completion_uses_nested_turn_id_and_rejects_mismatches(self):
        self.assertTrue(self.provider().complete(self.request()).ok)
        def responses(proc, request):
            replies = reply_messages(request, proc.cwd)
            if request["method"] == "turn/start":
                replies[-1]["params"]["turn"]["id"] = "other"
            return replies
        self.responses = responses
        self.assert_failure(self.provider().complete(self.request()), "invalid_appserver_message", True)

    def test_completed_item_rejects_duplicate_completion_late_delta_and_restart(self):
        for method in ("item/completed", "item/agentMessage/delta", "item/started"):
            for streaming in (False, True):
                with self.subTest(method=method, streaming=streaming):
                    def responses(proc, request):
                        replies = reply_messages(request, proc.cwd)
                        if request["method"] == "turn/start":
                            late = json.loads(json.dumps(
                                replies[1] if method.endswith("/delta") else replies[2]))
                            late["method"] = method
                            if method == "item/completed":
                                late["params"]["item"]["text"] = "SYNTHETIC_PRIVATE_REPLACEMENT"
                            replies = [replies[0], replies[2], late, replies[-1]]
                        return replies
                    self.responses = responses
                    provider, deltas = self.provider(), []
                    result = (provider.stream(self.request(), deltas.append) if streaming
                              else provider.complete(self.request()))
                    self.assert_failure(result, "invalid_appserver_message", True)
                    self.assertEqual(deltas, [])
                    self.assertEqual(self.processes[-1].close_count, 1)
                    self.assertEqual(self.methods(self.processes[-1]).count("turn/start"), 1)

    def test_invalid_item_type_returns_fixed_failure_in_complete_and_stream(self):
        for value in ([], {}, None, True, 1):
            for streaming in (False, True):
                with self.subTest(value=value, streaming=streaming):
                    def responses(proc, request):
                        replies = reply_messages(request, proc.cwd)
                        if request["method"] == "turn/start":
                            replies[2]["params"]["item"]["type"] = value
                            replies = [replies[0], replies[2], replies[-1]]
                        return replies
                    self.responses = responses
                    provider, deltas = self.provider(), []
                    result = (provider.stream(self.request(), deltas.append) if streaming
                              else provider.complete(self.request()))
                    self.assert_failure(result, "invalid_appserver_message", True)
                    self.assertEqual(deltas, [])
                    self.assertEqual(self.processes[-1].close_count, 1)

    def test_agent_item_fields_are_validated_before_the_shared_parser(self):
        fields = (("type", []), ("type", {}), ("type", False), ("text", []),
                  ("text", None), ("text", False), ("phase", []), ("phase", {}),
                  ("phase", False), ("phase", 1), ("phase", "SYNTHETIC_PRIVATE_PHASE"))
        for method in ("item/started", "item/completed"):
            for key, value in fields:
                with self.subTest(method=method, key=key, value=value):
                    def responses(proc, request):
                        replies = reply_messages(request, proc.cwd)
                        if request["method"] == "turn/start":
                            replies[2]["method"] = method
                            replies[2]["params"]["item"][key] = value
                            replies = [replies[0], replies[2], replies[-1]]
                        return replies
                    self.responses = responses
                    deltas = []
                    result = self.provider().stream(self.request(), deltas.append)
                    self.assert_failure(result, "invalid_appserver_message", True)
                    self.assertEqual(deltas, [])
                    self.assertEqual(self.processes[-1].close_count, 1)

    def test_distinct_items_remain_valid_and_item_state_resets_on_reuse(self):
        def responses(proc, request):
            replies = reply_messages(request, proc.cwd)
            if request["method"] == "turn/start":
                started = json.loads(json.dumps(replies[2]))
                started["method"] = "item/started"
                started["params"]["item"].update(text="", phase=None)
                delta = json.loads(json.dumps(replies[1]))
                delta["params"].update(itemId="synthetic-second", delta="Synthetic second")
                final = json.loads(json.dumps(replies[2]))
                final["params"]["item"].update(id="synthetic-second", text="Synthetic second")
                replies = [replies[0], started, *replies[1:3], delta, final, replies[-1]]
            return replies
        self.responses = responses
        provider = self.provider()
        for _ in range(2):
            deltas = []
            result = provider.stream(self.request(), deltas.append)
            self.assertTrue(result.ok, result.error_code)
            self.assertEqual(result.text, "Synthetic second")
            self.assertEqual(deltas, [TEXT, "Synthetic second"])
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.methods(self.processes[0]).count("turn/start"), 2)

    def test_late_previous_turn_notification_cannot_satisfy_next_operation(self):
        provider = self.provider()
        self.assertTrue(provider.complete(self.request()).ok)
        self.processes[0].messages.append({
            "method": "turn/completed", "params": {
                "threadId": "synthetic-thread",
                "turn": {"id": "synthetic-turn", "status": "completed"}}})
        self.assert_failure(provider.complete(self.request()), "invalid_appserver_message")
        self.assertEqual(self.methods(self.processes[0]).count("turn/start"), 1)
        self.assertEqual(len(self.processes), 1)
        self.assertTrue(self.processes[0].closed)

    def test_unknown_notification_is_fatal_and_does_not_leak_its_payload(self):
        def responses(proc, request):
            replies = reply_messages(request, proc.cwd)
            if request["method"] == "turn/start":
                return [replies[0], {"method": "SYNTHETIC_PRIVATE_UNKNOWN", "params": {}}]
            return replies
        self.responses = responses
        self.assert_failure(self.provider().complete(self.request()), "unknown_appserver_event", True)
        self.assertEqual(len(self.processes), 1)

    def test_tool_items_and_hook_notifications_are_never_allowed(self):
        for event in (
                {"method": "item/started", "params": {
                    "threadId": "synthetic-thread", "turnId": "synthetic-turn",
                    "item": {"id": "synthetic-item", "type": "commandExecution"}}},
                {"method": "hook/started", "params": {"run": {}}},
                {"method": "command/exec/outputDelta", "params": {}},
                {"id": 900, "method": "item/commandExecution/requestApproval", "params": {}}):
            def responses(proc, request):
                replies = reply_messages(request, proc.cwd)
                return [replies[0], event] if request["method"] == "turn/start" else replies
            self.responses = responses
            with self.subTest(event=event):
                self.assert_failure(self.provider().complete(self.request()), "unsafe_tool_event", True)

    def test_enabled_or_malformed_hooks_are_rejected_before_thread_start(self):
        for hooks, code in (([{"enabled": True}], "unsafe_tool_event"),
                            ([{"enabled": "false"}], "invalid_appserver_message"),
                            ([{}], "invalid_appserver_message")):
            def responses(proc, request):
                replies = reply_messages(request, proc.cwd)
                if request["method"] == "hooks/list":
                    replies[0]["result"]["data"][0]["hooks"] = hooks
                return replies
            self.responses = responses
            with self.subTest(hooks=hooks):
                self.assert_failure(self.provider().complete(self.request()), code)
                self.assertNotIn("thread/start", self.methods(self.processes[-1]))

    def test_native_enabled_defender_hook_never_uses_windows_whitelist(self):
        hook = {
            "enabled": True, "source": "system", "handlerType": "command",
            "isManaged": True, "trustStatus": "managed",
            "sourcePath": os.path.join(self.root, "ProgramData", "Microsoft",
                                       "Windows Defender", "Platform", "synthetic.json"),
            "command": codex_appserver._DEFENDER_HOOK_COMMAND,
        }
        def responses(proc, request):
            replies = reply_messages(request, proc.cwd)
            if request["method"] == "hooks/list":
                replies[0]["result"]["data"][0]["hooks"] = [hook]
            return replies
        self.responses = responses
        with patch.dict(os.environ, {"ProgramData": "SYNTHETIC_PRIVATE_AMBIENT"}), \
                patch.object(codex_appserver, "_is_trusted_defender_path",
                             side_effect=AssertionError("ambient ProgramData access")) as whitelist:
            self.assert_failure(self.provider().complete(self.request()), "unsafe_tool_event")
            whitelist.assert_not_called()
        self.assertNotIn("thread/start", self.methods(self.processes[0]))

    def test_disabled_hook_metadata_does_not_invoke_windows_whitelist(self):
        def responses(proc, request):
            replies = reply_messages(request, proc.cwd)
            if request["method"] == "hooks/list":
                replies[0]["result"]["data"][0]["hooks"] = [{
                    "enabled": False, "source": "synthetic",
                    "handlerType": "command", "command": "SYNTHETIC_NEVER_EXECUTED"}]
            return replies
        self.responses = responses
        provider = self.provider()
        with patch.object(codex_appserver, "_is_trusted_defender_path",
                          side_effect=AssertionError("ambient ProgramData access")) as whitelist:
            self.assertTrue(provider.warm_up("synthetic").ok)
            self.assertTrue(provider.complete(self.request()).ok)
            whitelist.assert_not_called()
        self.assertEqual(self.methods(self.processes[0]).count("turn/start"), 1)

    def test_native_system_lifecycle_hooks_never_reach_shared_allowlist(self):
        for method in ("hook/started", "hook/completed"):
            def responses(proc, request):
                replies = reply_messages(request, proc.cwd)
                if request["method"] == "turn/start":
                    return [replies[0], {"method": method, "params": {"run": {
                        "source": "system", "handlerType": "command",
                        "eventName": "SessionStart", "sourcePath": None}}}]
                return replies
            self.responses = responses
            with self.subTest(method=method), \
                    patch.object(codex_appserver.CodexAppServerParser, "_handle_hook",
                                 side_effect=AssertionError("shared hook allowlist used")) as allowlist:
                self.assert_failure(self.provider().complete(self.request()), "unsafe_tool_event", True)
                allowlist.assert_not_called()

    def test_pre_submit_probe_failures_do_not_retry_or_claim_submission(self):
        for error in (CodexConfigError("config_invalid"), CatalogProbeError("catalog_probe_failed"),
                      ProcessError("probe_unavailable")):
            self.config.side_effect = error
            with self.subTest(error=error):
                self.assert_failure(self.provider().complete(self.request()), str(error))
        self.assertEqual(self.config.call_count, 3)
        self.rpc.assert_not_called()

    def test_fixed_probe_cleanup_failures_are_fatal_before_any_native_turn(self):
        for boundary, error, expected_calls in (
                (self.capture, ProcessError("probe_cleanup_failed"), (1, 0, 0)),
                (self.config, CodexConfigError("config_probe_cleanup_failed"), (1, 1, 0)),
                (self.catalog, CatalogProbeError("catalog_probe_cleanup_failed"), (1, 1, 1))):
            with self.subTest(code=str(error)):
                self.capture.side_effect = self.config.side_effect = self.catalog.side_effect = None
                boundary.side_effect = error
                before = (self.capture.call_count, self.config.call_count, self.catalog.call_count)
                provider = self.provider()
                self.assert_failure(provider.complete(self.request()), "provider_cleanup_failed")
                after = (self.capture.call_count, self.config.call_count, self.catalog.call_count)
                self.assertEqual(tuple(new - old for old, new in zip(before, after)), expected_calls)
                self.assert_failure(provider.complete(self.request()), "provider_cleanup_failed")
                self.assert_failure(provider.warm_up("synthetic"), "provider_cleanup_failed")
                self.assertEqual((self.capture.call_count, self.config.call_count, self.catalog.call_count),
                                 after)
                self.assertTrue(provider._transport._stream_lock.acquire(blocking=False))
                provider._transport._stream_lock.release()
        self.rpc.assert_not_called()

    def test_rpc_constructor_cleanup_failure_is_sticky_before_process_assignment(self):
        for initial in ("complete", "warm_up"):
            with self.subTest(initial=initial):
                error = RpcError("rpc_cleanup_failed")
                error.__cause__ = RuntimeError("SYNTHETIC_PRIVATE_CONSTRUCTOR_CLEANUP")
                self.rpc.side_effect = error
                before = self.rpc.call_count
                provider = self.provider()
                result = (provider.complete(self.request()) if initial == "complete" else
                          provider.warm_up("synthetic"))
                self.assert_failure(result, "provider_cleanup_failed")
                self.assertEqual(self.rpc.call_count, before + 1)
                self.assertEqual(self.processes, [])
                self.assertIsNone(provider._transport._proc)
                self.assertEqual(provider._fatal, "provider_cleanup_failed")
                counts = (self.capture.call_count, self.config.call_count,
                          self.catalog.call_count, self.rpc.call_count)
                self.assert_failure(provider.complete(self.request()), "provider_cleanup_failed")
                self.assert_failure(provider.warm_up("synthetic"), "provider_cleanup_failed")
                self.assertEqual((self.capture.call_count, self.config.call_count,
                                  self.catalog.call_count, self.rpc.call_count), counts)

                def locks_available():
                    for lock in (provider._operation_lock, provider._transport._stream_lock):
                        if not lock.acquire(timeout=0.25):
                            return False
                        lock.release()
                    return True

                self.assertTrue(self.finish(self.start(locks_available)))

    def test_zero_and_partial_turn_writes_have_conservative_submission_metrics(self):
        for partial in (False, True):
            self.send_error = ("turn/start", partial)
            with self.subTest(partial=partial):
                self.assert_failure(self.provider().complete(self.request()), "rpc_io_failed", partial)
                self.assertEqual(self.methods(self.processes[-1]).count("turn/start"), 1)
                self.assertEqual(self.processes[-1].close_count, 1)
        self.assertEqual(len(self.processes), 2)

    def test_eof_after_submission_is_fatal_without_retry(self):
        self.responses = lambda proc, request: (
            [None] if request["method"] == "turn/start" else reply_messages(request, proc.cwd))
        self.assert_failure(self.provider().complete(self.request()), "appserver_exited", True)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.methods(self.processes[0]).count("turn/start"), 1)
        self.assertTrue(self.processes[0].closed)

    def test_failed_completed_turn_redacts_native_error_without_retry(self):
        def responses(proc, request):
            replies = reply_messages(request, proc.cwd)
            if request["method"] == "turn/start":
                replies[-1]["params"]["turn"].update(
                    status="failed", error={"message": "SYNTHETIC_PRIVATE_PROVIDER"})
            return replies
        self.responses = responses
        self.assert_failure(self.provider().complete(self.request()), "request_failed", True)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.methods(self.processes[0]).count("turn/start"), 1)

    def test_precancel_has_zero_activity_and_does_not_poison_next_request(self):
        provider, cancel = self.provider(), threading.Event()
        cancel.set()
        self.assert_failure(provider.complete(self.request(), cancel), "cancelled")
        self.assert_no_activity()
        self.assertTrue(provider.complete(self.request()).ok)

    def test_timeout_cleans_an_already_submitted_turn(self):
        self.block_method = "turn/start"
        started = time.monotonic()
        self.assert_failure(self.provider().complete(self.request(timeout_seconds=0.05)), "timeout", True)
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertEqual(self.processes[0].close_count, 1)

    def test_cancel_after_turn_ack_sends_one_interrupt_and_cleans(self):
        provider, cancel = self.provider(), threading.Event()
        def on_delta(_delta):
            cancel.set()
        result = provider.stream(self.request(), on_delta, cancel)
        self.assert_failure(result, "cancelled", True)
        self.assertEqual(self.methods(self.processes[0]).count("turn/interrupt"), 1)
        self.assertEqual(self.processes[0].close_count, 1)

    def test_queued_cancel_never_enters_the_transport(self):
        provider, cancel = self.provider(), threading.Event()
        provider._operation_lock.acquire()
        try:
            running = self.start(lambda: provider.complete(self.request(), cancel))
            self.wait_for(lambda: provider._foreground_waiters == 1)
            cancel.set()
            self.assert_failure(self.finish(running), "cancelled")
            self.assert_no_activity()
        finally:
            provider._operation_lock.release()

    def test_queued_timeout_does_not_spawn_or_submit(self):
        provider = self.provider()
        provider._operation_lock.acquire()
        try:
            running = self.start(lambda: provider.complete(self.request(timeout_seconds=0.05)))
            self.assert_failure(self.finish(running), "timeout")
            self.assert_no_activity()
        finally:
            provider._operation_lock.release()

    def test_request_copy_is_taken_before_waiting_for_the_operation_lock(self):
        provider = self.provider()
        images = []
        request = self.request(image_paths=images)
        expected = build_codex_prompt(request)
        provider._operation_lock.acquire()
        try:
            running = self.start(lambda: provider.complete(request))
            self.wait_for(lambda: provider._foreground_waiters == 1)
            object.__setattr__(request, "user_text", "mutated after submission")
            object.__setattr__(request, "model", "mutated")
            images.append("mutated.png")
        finally:
            provider._operation_lock.release()
        self.assertTrue(self.finish(running).ok)
        turn = next(r["params"] for r in self.processes[0].sent if r["method"] == "turn/start")
        self.assertEqual(turn["input"][0]["text"], expected)
        self.assertEqual(turn["model"], "synthetic")

    def test_real_request_snapshot_flows_into_provider_without_fixture_conversion(self):
        request = self.request()
        snapshot = RequestSnapshot(
            request=request, selection=ProviderSelection("codex_cli", "synthetic"),
            config={"nested": ["synthetic"]}, input=request.user_text, origin="text",
            content_class="text", kind="text", sig="synthetic", direction="auto",
            app_language="en_US", target_lang="zh", summarize=False, dictionary=False,
            stream_enabled=True)
        self.assertIsInstance(snapshot.request, ProviderRequest)
        self.assertTrue(self.provider().complete(snapshot.request).ok)
        turn = next(r["params"] for r in self.processes[0].sent if r["method"] == "turn/start")
        self.assertEqual(turn["input"][0]["text"], build_codex_prompt(snapshot.request))

    def test_foreground_preempts_active_prewarm_before_any_warm_turn(self):
        provider = self.provider()
        self.block_method = "initialize"
        warm = self.start(lambda: provider.warm_up("synthetic"))
        self.wait_for(lambda: self.processes and self.processes[0].waiting.is_set())
        self.block_method = None
        foreground = self.start(lambda: provider.complete(self.request()))
        self.assert_failure(self.finish(warm), "cancelled")
        self.assertTrue(self.finish(foreground).ok)
        self.assertEqual(len(self.processes), 2)
        self.assertNotIn("thread/start", self.methods(self.processes[0]))
        self.assertEqual(self.methods(self.processes[1]).count("turn/start"), 1)

    def test_queued_prewarm_cannot_jump_a_foreground_waiter(self):
        provider = self.provider()
        provider._operation_lock.acquire()
        try:
            foreground = self.start(lambda: provider.complete(self.request()))
            self.wait_for(lambda: provider._foreground_waiters == 1)
            warm = provider.warm_up("synthetic")
            self.assert_failure(warm, "cancelled")
            self.assert_no_activity()
        finally:
            provider._operation_lock.release()
        self.assertTrue(self.finish(foreground).ok)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.methods(self.processes[0])[:5],
                         ["initialize", "initialized", "hooks/list", "thread/start", "turn/start"])

    def test_shutdown_cancels_active_and_queued_requests_and_rejects_future_work(self):
        provider = self.provider()
        self.block_method = "turn/start"
        active = self.start(lambda: provider.complete(self.request()))
        self.wait_for(lambda: self.processes and self.processes[0].waiting.is_set())
        queued = self.start(lambda: provider.complete(self.request()))
        self.wait_for(lambda: provider._foreground_waiters == 2)
        provider.shutdown()
        self.assert_failure(self.finish(active), "appserver_shutdown", True)
        self.assert_failure(self.finish(queued), "appserver_shutdown")
        self.assert_failure(provider.complete(self.request()), "appserver_shutdown")
        self.assert_failure(provider.warm_up("synthetic"), "appserver_shutdown")
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.processes[0].close_count, 1)

    def test_shutdown_waits_for_cleanup_and_does_not_return_early(self):
        provider = self.provider()
        self.assertTrue(provider.complete(self.request()).ok)
        proc = self.processes[0]
        proc.close_release.clear()
        closing = self.start(provider.shutdown)
        self.assertTrue(proc.close_entered.wait(1))
        self.assertTrue(closing[0].is_alive())
        self.assertEqual(closing[1], [])
        proc.close_release.set()
        self.assertIsNone(self.finish(closing))
        self.assertTrue(proc.closed)

    def test_cleanup_error_is_sticky_and_both_locks_are_released(self):
        provider = self.provider()
        def responses(proc, request):
            proc.close_error = ProcessError("probe_cleanup_failed")
            return [None] if request["method"] == "turn/start" else reply_messages(request, proc.cwd)
        self.responses = responses
        self.assert_failure(provider.complete(self.request()), "provider_cleanup_failed", True)
        self.assertTrue(provider._transport._stream_lock.acquire(blocking=False))
        provider._transport._stream_lock.release()
        later = self.start(lambda: provider.complete(self.request()))
        self.assert_failure(self.finish(later), "provider_cleanup_failed")
        self.assertEqual(len(self.processes), 1)

    def test_prewarm_cleanup_error_releases_shared_stream_lock(self):
        provider = self.provider()
        def responses(proc, request):
            proc.close_error = ProcessError("probe_cleanup_failed")
            return [None]
        self.responses = responses
        self.assert_failure(provider.warm_up("synthetic"), "provider_cleanup_failed")
        self.assertTrue(provider._transport._stream_lock.acquire(blocking=False))
        provider._transport._stream_lock.release()
        self.assert_failure(self.finish(self.start(lambda: provider.warm_up("synthetic"))),
                            "provider_cleanup_failed")

    def test_idle_cleanup_failure_is_sticky_and_reports_only_fixed_diagnostic(self):
        provider = self.provider()
        self.assertTrue(provider.complete(self.request()).ok)
        self.processes[0].close_error = ProcessError("probe_cleanup_failed")
        transport = provider._transport
        transport._expire_idle_process(transport._idle_generation)
        self.assertTrue(transport.cleanup_failed.is_set())
        transport._log_error.assert_called_once()
        where, error = transport._log_error.call_args.args
        self.assertEqual((where, str(error)), ("codex_provider", "provider_cleanup_failed"))
        self.assert_failure(self.finish(self.start(lambda: provider.complete(self.request()))),
                            "provider_cleanup_failed")
        self.assertEqual(len(self.processes), 1)

    def test_idle_expiry_is_nonblocking_when_the_facade_operation_lock_is_owned(self):
        provider = self.provider()
        self.assertTrue(provider.complete(self.request()).ok)
        transport, proc = provider._transport, self.processes[0]
        provider._operation_lock.acquire()
        try:
            expiry = self.start(lambda: transport._expire_idle_process(transport._idle_generation))
            self.assertIsNone(self.finish(expiry))
            self.assertEqual(proc.close_count, 0)
            self.assertIs(transport._proc, proc)
        finally:
            provider._operation_lock.release()
        self.assertTrue(provider.complete(self.request()).ok)
        self.assertEqual(len(self.processes), 1)

    def test_idle_expiry_during_same_model_warm_fast_path_retains_cleanup(self):
        timers = []
        class ControlledTimer:
            def __init__(self, interval, function, args):
                self.interval, self.function, self.args = interval, function, args
                self.cancelled = False
                timers.append(self)
            def start(self):
                pass
            def cancel(self):
                self.cancelled = True
            def fire(self):
                self.function(*self.args)

        provider = self.provider()
        self.assertTrue(provider.warm_up("synthetic").ok)
        transport, proc = provider._transport, self.processes[0]
        entered, release = threading.Event(), threading.Event()
        def version_probe(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic version gate was not released.")
            return b"codex-cli 0.146.0\n"
        self.capture.side_effect = version_probe
        with patch.object(transport, "_schedule_idle_shutdown", self.real_schedule.__get__(transport)), \
                patch.object(codex_appserver.threading, "Timer", ControlledTimer):
            transport._schedule_idle_shutdown()
            expired = timers[0]
            warm = self.start(lambda: provider.warm_up("synthetic"))
            try:
                self.assertTrue(entered.wait(1))
                self.assertIsNone(self.finish(self.start(expired.fire)))
                self.assertEqual(proc.close_count, 0)
            finally:
                release.set()
                result = self.finish(warm)
            self.assertTrue(result.ok, result.error_code)
            self.assertEqual(len(timers), 2, "Idle expiry lost its cleanup responsibility.")
            self.assertGreater(transport._idle_generation, expired.args[0])
            self.assertTrue(expired.cancelled)
            timers[-1].fire()
            self.assertEqual(proc.close_count, 1)
            self.assertIsNone(transport._proc)
            self.assertEqual(self.methods(proc), ["initialize", "initialized", "hooks/list"])
            self.assertEqual(len(self.processes), 1)

    def test_stale_idle_generation_cannot_rearm_or_close_after_replacement_or_shutdown(self):
        provider = self.provider()
        self.assertTrue(provider.warm_up("synthetic").ok)
        transport, proc = provider._transport, self.processes[0]
        generation = transport._idle_generation
        transport._cancel_idle_timer()
        with patch.object(transport, "_schedule_idle_shutdown", self.real_schedule.__get__(transport)), \
                patch.object(codex_appserver.threading, "Timer") as timer:
            provider._operation_lock.acquire()
            try:
                self.assertIsNone(self.finish(self.start(lambda: transport._expire_idle_process(generation))))
            finally:
                provider._operation_lock.release()
            transport._expire_idle_process(generation)
            self.assertEqual(proc.close_count, 0)
            timer.assert_not_called()
            provider.shutdown()
            generation = transport._idle_generation
            provider._operation_lock.acquire()
            try:
                self.assertIsNone(self.finish(self.start(lambda: transport._expire_idle_process(generation))))
            finally:
                provider._operation_lock.release()
            timer.assert_not_called()
            self.assertEqual(proc.close_count, 1)

    def test_shutdown_waits_for_idle_cleanup_even_after_transport_detaches_process(self):
        provider = self.provider()
        self.assertTrue(provider.complete(self.request()).ok)
        transport, proc = provider._transport, self.processes[0]
        proc.close_release.clear()
        expiry = self.start(lambda: transport._expire_idle_process(transport._idle_generation))
        self.assertTrue(proc.close_entered.wait(1))
        self.assertIsNone(transport._proc)
        returned = threading.Event()
        def shutdown():
            provider.shutdown()
            returned.set()
        closing = self.start(shutdown)
        try:
            self.assertTrue(provider._closing.wait(1))
            self.assertFalse(returned.wait(0.05), "Shutdown returned while idle cleanup still owned the group.")
            self.assertTrue(closing[0].is_alive())
            self.assertEqual(closing[1], [])
            self.assertFalse(proc.closed)
        finally:
            proc.close_release.set()
        self.assertIsNone(self.finish(expiry))
        self.assertIsNone(self.finish(closing))
        self.assertEqual(proc.close_count, 1)
        self.assertTrue(proc.closed)
        self.assert_failure(provider.complete(self.request()), "appserver_shutdown")

    def test_queued_foreground_cannot_pass_failing_idle_cleanup_or_leak_background_detail(self):
        logger = Mock()
        provider = self.provider(log_error=logger)
        self.assertTrue(provider.complete(self.request()).ok)
        transport, proc = provider._transport, self.processes[0]
        proc.close_error = ProcessError("SYNTHETIC_PRIVATE_IDLE_CLEANUP")
        proc.close_release.clear()
        expiry = self.start(lambda: transport._expire_idle_process(transport._idle_generation))
        self.assertTrue(proc.close_entered.wait(1))
        foreground = self.start(lambda: provider.complete(self.request()))
        try:
            self.wait_for(lambda: provider._foreground_waiters == 1)
            self.assertEqual(len(self.processes), 1)
            self.assertEqual(self.methods(proc).count("turn/start"), 1)
            self.assertEqual(foreground[1], [])
        finally:
            proc.close_release.set()
        self.assertIsNone(self.finish(expiry))
        self.assert_failure(self.finish(foreground), "provider_cleanup_failed")
        self.assertTrue(transport.cleanup_failed.is_set())
        logger.assert_called_once()
        where, error = logger.call_args.args
        self.assertEqual((where, str(error)), ("codex_provider", "provider_cleanup_failed"))
        self.assertNotIn("SYNTHETIC_PRIVATE", repr(logger.call_args))
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.methods(proc).count("turn/start"), 1)
        self.assertTrue(transport._stream_lock.acquire(blocking=False))
        transport._stream_lock.release()
        self.assert_failure(self.finish(self.start(lambda: provider.warm_up("synthetic"))),
                            "provider_cleanup_failed")

    def test_sticky_cleanup_failure_blocks_native_spawn_and_send_boundaries(self):
        provider = self.provider()
        self.assertTrue(provider.complete(self.request()).ok)
        transport, proc = provider._transport, self.processes[0]
        sent, pending = list(proc.sent), dict(transport._pending_rpc)
        counts = (self.rpc.call_count, self.config.call_count, self.catalog.call_count)
        transport.cleanup_failed.set()
        with self.assertRaisesRegex(ProcessError, "^probe_cleanup_failed$"):
            transport._start_process(self.request())
        with self.assertRaisesRegex(ProcessError, "^probe_cleanup_failed$"):
            transport._send(proc, "turn/start", {}, 999)
        self.assertEqual(proc.sent, sent)
        self.assertEqual(transport._pending_rpc, pending)
        self.assertEqual((self.rpc.call_count, self.config.call_count, self.catalog.call_count), counts)

    def test_cleanup_failure_takes_priority_over_shutdown_and_precancel(self):
        provider, cancel = self.provider(), threading.Event()
        cancel.set()
        provider._closing.set()
        provider._transport.cleanup_failed.set()
        self.assert_failure(provider.complete(self.request(), cancel), "provider_cleanup_failed")
        self.assert_failure(provider.warm_up("synthetic"), "provider_cleanup_failed")
        self.assert_no_activity()

    def test_reentrant_callback_request_is_rejected_without_deadlock(self):
        provider, errors = self.provider(), []
        def callback(_delta):
            try:
                provider.complete(self.request())
            except RuntimeError as error:
                errors.append(str(error))
        self.assertTrue(provider.stream(self.request(), callback).ok)
        self.assertEqual(errors, ["provider_reentrant_request"])
        self.assertEqual(self.methods(self.processes[0]).count("turn/start"), 1)
        self.assertTrue(provider.complete(self.request()).ok)
