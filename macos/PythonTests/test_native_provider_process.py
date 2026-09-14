"""Mandatory bundled-Python/C-bridge native provider tests, not host substitutes."""

from contextlib import redirect_stderr, redirect_stdout
import ctypes
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest


if sys.platform != "darwin":
    raise RuntimeError("This suite requires bundled macOS Python; do not skip or run on a host.")

import test_codex_catalog_process as owned_support
import cc_request
from cc_macos import native_provider_fixture
from cc_macos.catalog_fixture import PAYLOAD
from cc_providers import base, codex_appserver, codex_cli, codex_config_darwin, codex_darwin, darwin_rpc
from cc_providers.base import ProviderRequest, ProviderSelection
from cc_providers.codex_cli import build_codex_prompt
from cc_providers.codex_config import CODEX_CONFIG_OVERRIDES, integration_overrides
from cc_providers.darwin_process import load_supervision
from cc_request import RequestSnapshot


class TestNativeProviderProcess(unittest.TestCase):
    # Reuse ownership checks and the independently owned sibling harness, without
    # inheriting (and rerunning) the catalog test cases.
    setUp = owned_support.TestOwnedCatalogProcess.setUp
    tearDown = owned_support.TestOwnedCatalogProcess.tearDown
    _finish_sibling = owned_support.TestOwnedCatalogProcess._finish_sibling
    _process_rows = staticmethod(owned_support.TestOwnedCatalogProcess._process_rows)
    assert_gone = owned_support.TestOwnedCatalogProcess.assert_gone
    _assert_group_gone = owned_support.TestOwnedCatalogProcess._assert_group_gone
    _assert_processes = owned_support.TestOwnedCatalogProcess._assert_processes

    @classmethod
    def setUpClass(cls):
        owned_support.TestOwnedCatalogProcess.setUpClass.__func__(cls)
        for module in (native_provider_fixture, base, codex_appserver, codex_cli,
                       codex_config_darwin, codex_darwin, darwin_rpc, cc_request):
            expected = cls.core.joinpath(*module.__name__.split(".")).with_suffix(".py")
            if Path(module.__file__).resolve() != expected.resolve():
                raise RuntimeError("Native provider suite imported code outside the app's Core.")

    def test_native_provider_and_bridge_are_really_bundled(self):
        bridge = load_supervision()
        self.assertIsInstance(bridge, ctypes.CDLL)
        self.assertEqual(bridge.cc_process_support_abi(), 1)
        self.assertEqual(Path(bridge._name).resolve(),
                         self.contents / "Helpers" / "python" / "lib" / "libCCProcessSupport.dylib")
        self.assertIs(codex_darwin.RpcProcess, darwin_rpc.RpcProcess)

    def create(self, mode="normal"):
        command, environment, work, cache, warnings = native_provider_fixture.create_cli(self.root, mode)
        self.assertEqual(warnings, [])
        for key in ("HOME", "CODEX_HOME", "TMPDIR"):
            self.assertTrue(Path(environment[key]).resolve().is_relative_to(self.root))
        self.assertTrue(Path(work).resolve().is_relative_to(Path(environment["HOME"])))
        self.assertEqual(set(environment), {
            "PATH", "HOME", "CODEX_HOME", "TMPDIR", "CC_SYNTHETIC_ROOT", "CC_SYNTHETIC_MODE"})
        self.warnings = []
        self.provider = codex_darwin.DarwinCodexProvider(
            command, work, environment=environment, catalog_cache_dir=str(cache),
            log_error=lambda where, error: self.warnings.append((where, str(error))))
        self.addCleanup(self.provider.shutdown)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.rpc(), [])
        self.config = Path(environment["CODEX_HOME"]) / "config.toml"
        self.config_before = self.config.read_bytes()
        return self.provider

    @staticmethod
    def request(timeout=8):
        return ProviderRequest("text", "synthetic", native_provider_fixture.SYSTEM_PROMPT,
                               native_provider_fixture.USER_TEXT, timeout_seconds=timeout)

    @staticmethod
    def read_lines(path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def calls(self):
        return [record for name in ("calls.jsonl", "version.jsonl", "native-processes.jsonl")
                for record in self.read_lines(self.root / name)]

    def rpc(self, kind="provider"):
        return [record for record in self.read_lines(self.root / "native-rpc.jsonl")
                if record["kind"] == kind]

    def methods(self):
        return [record["request"]["method"] for record in self.rpc()]

    def _remember(self, root):
        calls = self.calls()
        for call in calls:
            self.leaders[call["pid"]] = call
        children = []
        for path in (root / "children").glob("*.json"):
            child = json.loads(path.read_bytes())
            self.children[child["pid"]] = child
            self.assertEqual(child["group"], int(path.stem))
            children.append(child)
        return calls, children

    def _cleanup_owned(self):
        self._remember(self.root)
        for pid in self.children:
            self.assert_gone(pid, orphan=True)
        for pid in self.leaders:
            self.assert_gone(pid)

    def assert_finished(self, *, descendant=False):
        self.provider.shutdown()
        calls = self.calls()
        self.assertGreaterEqual(len(calls), 1)
        children = tuple(index for index, call in enumerate(calls)
                         if descendant and call.get("kind") == "provider")
        self._assert_processes(self.root, calls, children)
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertEqual(self.warnings, [])
        self.assertFalse(list(self.root.rglob(".catalog-*")))
        for call in calls:
            self.assertNotIn("exec", call["args"])
            self.assertEqual(Path(call["cwd"]), Path(self.provider.work_dir))
        # Reading receipts never treats a reaped PID as permission to signal it.
        before = self.calls()
        time.sleep(0.05)
        self.assertEqual(self.calls(), before, "An operation was retried after returning.")

    def assert_failure(self, result, code, *, submitted):
        self.assertFalse(result.ok)
        self.assertEqual((result.error_code, result.error_detail, result.text), (code, "", ""))
        self.assertIs(dict(result.metrics)["turn_submitted"], submitted)
        self.assertNotIn("SYNTHETIC_PRIVATE", repr(result))

    def test_prewarm_complete_stream_and_catalog_reuse_without_exec(self):
        provider = self.create()
        warm = provider.warm_up("synthetic")
        self.assertTrue(warm.ok, warm.error_code)
        self.assertIs(dict(warm.metrics)["turn_submitted"], False)
        self.assertEqual(self.methods(), ["initialize", "initialized", "hooks/list"])
        self.assertTrue(provider.warm_up("synthetic").ok)
        complete = provider.complete(self.request())
        deltas = []
        streamed = provider.stream(self.request(), deltas.append)
        self.assertTrue(complete.ok, complete.error_code)
        self.assertTrue(streamed.ok, streamed.error_code)
        self.assertEqual((complete.text, streamed.text, deltas),
                         (native_provider_fixture.TEXT, native_provider_fixture.TEXT,
                          [native_provider_fixture.TEXT]))
        self.assertEqual(self.methods(), [
            "initialize", "initialized", "hooks/list",
            "hooks/list", "thread/start", "turn/start",
            "hooks/list", "thread/start", "turn/start"])
        self.assertEqual(len({record["pid"] for record in self.rpc()}), 1)
        self.assertEqual([record["request"]["method"] for record in self.rpc("config")],
                         ["initialize", "config/read"])
        catalog_calls = self.read_lines(self.root / "calls.jsonl")
        self.assertEqual(len(catalog_calls), 3)
        self.assertEqual(catalog_calls[0]["args"][:1], ["--version"])
        self.assertEqual(catalog_calls[1]["args"][:2], ["debug", "models"])
        self.assertEqual(catalog_calls[2]["args"][:3], ["debug", "models", "-c"])
        self.assertEqual(len(self.read_lines(self.root / "version.jsonl")), 4)
        self.assert_finished()

    def test_real_request_snapshot_prompt_safety_and_catalog_bytes(self):
        provider = self.create()
        request = self.request()
        snapshot = RequestSnapshot(
            request=request, selection=ProviderSelection("codex_cli", "synthetic"),
            config={"synthetic": ["frozen"]}, input=request.user_text, origin="text",
            content_class="text", kind="text", sig="synthetic", direction="auto",
            app_language="en_US", target_lang="zh", summarize=False, dictionary=False,
            stream_enabled=True)
        self.assertIsInstance(snapshot.request, ProviderRequest)
        result = provider.complete(snapshot.request)
        self.assertTrue(result.ok, result.error_code)
        requests = [record["request"] for record in self.rpc()]
        thread = next(record["params"] for record in requests if record["method"] == "thread/start")
        turn = next(record["params"] for record in requests if record["method"] == "turn/start")
        self.assertEqual(turn["input"][0]["text"].encode("utf-8"),
                         build_codex_prompt(snapshot.request).encode("utf-8"))
        self.assertEqual(turn["input"][0]["type"], "text")
        self.assertEqual((thread["ephemeral"], thread["sandbox"], thread["approvalPolicy"]),
                         (True, "read-only", "never"))
        self.assertEqual(thread["model"], "synthetic")
        self.assertEqual(thread["config"], {"mcp_servers": {}, "web_search": "disabled"})
        self.assertEqual(turn["sandboxPolicy"], {"type": "readOnly", "networkAccess": False})
        self.assertEqual(turn["approvalPolicy"], "never")
        process = next(call for call in self.calls() if call.get("kind") == "provider")
        overrides = process["args"][5:][1::2]
        expected = list(CODEX_CONFIG_OVERRIDES) + list(
            integration_overrides(native_provider_fixture.NATIVE_CONFIG["config"]))
        self.assertEqual(overrides[:-1], expected)
        path = Path(json.loads(overrides[-1].split("=", 1)[1]))
        self.assertTrue(path.resolve().is_relative_to(self.root / "cache"))
        self.assertEqual(json.loads(path.read_bytes()), PAYLOAD)
        self.assertEqual(len(list((self.root / "cache").rglob("state.json"))), 1)
        self.assert_finished()

    def test_blank_lines_and_crlf_json_frames_preserve_real_session_reuse(self):
        provider = self.create("blank_crlf")
        warm = provider.warm_up("synthetic")
        self.assertTrue(warm.ok, warm.error_code)
        self.assertIs(dict(warm.metrics)["turn_submitted"], False)
        self.assertEqual(self.methods(), ["initialize", "initialized", "hooks/list"])
        deltas = []
        result = provider.stream(self.request(), deltas.append)
        self.assertTrue(result.ok, result.error_code)
        complete = provider.complete(self.request())
        self.assertTrue(complete.ok, complete.error_code)
        self.assertEqual((result.text, complete.text, deltas),
                         (native_provider_fixture.TEXT, native_provider_fixture.TEXT,
                          [native_provider_fixture.TEXT]))
        self.assertEqual(len({record["pid"] for record in self.rpc()}), 1)
        self.assertEqual(self.methods().count("initialize"), 1)
        self.assertEqual(self.methods().count("turn/start"), 2)
        self.assert_finished()

    def test_blank_lines_cannot_reset_the_real_cumulative_output_budget(self):
        self.exercise_failure("blank_flood", "rpc_output_limit", descendant=True)

    def test_successful_session_shutdown_cleans_term_resistant_descendant(self):
        provider = self.create("descendant")
        result = provider.complete(self.request())
        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(len(list((self.root / "children").glob("*.json"))), 1)
        self.assert_finished(descendant=True)

    def test_shutdown_waits_for_idle_timer_cleanup_of_the_real_owned_group(self):
        provider = self.create("descendant")
        result = provider.complete(self.request())
        self.assertTrue(result.ok, result.error_code)
        transport, proc = provider._transport, provider._transport._proc
        transport._cancel_idle_timer()
        generation = transport._idle_generation
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        errors = []
        real_close = proc.close

        def close_after_gate():
            entered.set()
            if not release.wait(5):
                raise AssertionError("Synthetic idle-cleanup synchronization was not released.")
            real_close()

        def expire():
            try:
                transport._expire_idle_process(generation)
            except BaseException as error:
                errors.append(error)

        def shutdown():
            try:
                provider.shutdown()
            except BaseException as error:
                errors.append(error)
            finally:
                returned.set()

        # Gate only entry to close; ownership, signals, wait, pipes, and the C
        # bridge remain the real bundled implementations.
        proc.close = close_after_gate
        timer = threading.Timer(0, expire)
        timer.daemon = True
        closer = threading.Thread(target=shutdown, daemon=True)
        timer.start()
        closer_started = False
        try:
            self.assertTrue(entered.wait(3), "The actual idle callback never reached process cleanup.")
            self.assertIsNone(transport._proc)
            self.assertIs(transport._owner_operation_lock, provider._operation_lock)
            closer.start()
            closer_started = True
            self.assertTrue(provider._closing.wait(1))
            self.assertFalse(returned.wait(0.1),
                             "Shutdown returned while the idle callback still owned live processes.")
            calls, children = self._remember(self.root)
            leader = next(call for call in calls if call.get("kind") == "provider")
            self.assertEqual(len(children), 1)
            os.kill(leader["pid"], 0)
            os.kill(children[0]["pid"], 0)
        finally:
            release.set()
            timer.join(4)
            if closer_started:
                closer.join(4)
            proc.close = real_close
        self.assertFalse(timer.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertTrue(returned.is_set())
        self.assertEqual(errors, [])
        self.assert_finished(descendant=True)

    def test_catalog_stops_at_explicit_home_despite_poisoned_ancestors(self):
        provider = self.create()
        for directory in (self.root, Path(provider.env["HOME"])):
            marker = directory / ".codex"
            marker.mkdir()
            (marker / "config.toml").write_text(
                'model_provider="SYNTHETIC_OUTSIDE_BOUNDARY"\n', encoding="utf-8")
        result = provider.warm_up("synthetic")
        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(provider._catalog.status, "ready")
        self.assertEqual(self.methods(), ["initialize", "initialized", "hooks/list"])
        self.assertEqual(len(self.read_lines(self.root / "calls.jsonl")), 3)
        self.assert_finished()

    def exercise_failure(self, mode, code, *, submitted=False, descendant=False, timeout=8):
        provider = self.create(mode)
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = provider.complete(self.request(timeout))
        self.assert_failure(result, code, submitted=submitted)
        self.assertEqual((output.getvalue(), errors.getvalue()), ("", ""))
        self.assertEqual(self.methods().count("turn/start"), int(submitted))
        self.assertEqual(len({record["pid"] for record in self.rpc()}), 1)
        self.assert_finished(descendant=descendant)

    def test_missing_rpc_id_is_rejected(self):
        self.exercise_failure("missing_rpc", "invalid_appserver_message")

    def test_wrong_rpc_id_is_rejected(self):
        self.exercise_failure("wrong_rpc", "invalid_appserver_message")

    def test_duplicate_rpc_response_is_rejected(self):
        self.exercise_failure("duplicate_rpc", "invalid_appserver_message")

    def test_late_rpc_response_is_rejected(self):
        self.exercise_failure("late_rpc", "invalid_appserver_message")

    def test_duplicate_json_keys_are_rejected(self):
        self.exercise_failure("duplicate_keys", "invalid_appserver_json")

    def test_nonfinite_json_is_rejected(self):
        self.exercise_failure("nonfinite", "invalid_appserver_json")

    def test_unknown_envelope_is_rejected(self):
        self.exercise_failure("unknown_envelope", "invalid_appserver_message")

    def test_response_notification_fields_are_mutually_exclusive(self):
        self.exercise_failure("response_params", "invalid_appserver_message")

    def test_notification_cannot_carry_response_result(self):
        self.exercise_failure("notification_result", "invalid_appserver_message", submitted=True)

    def test_enabled_hook_is_rejected_before_submission(self):
        self.exercise_failure("hooks", "unsafe_tool_event")

    def test_enabled_defender_shaped_hook_is_rejected_on_darwin(self):
        self.exercise_failure("defender_hook", "unsafe_tool_event")

    def test_system_lifecycle_hook_notification_is_rejected(self):
        self.exercise_failure("hook_notification", "unsafe_tool_event", submitted=True)

    def test_server_error_has_no_private_detail(self):
        self.exercise_failure("error", "appserver_request_failed")

    def test_wrong_thread_identity_is_rejected_after_submission(self):
        self.exercise_failure("wrong_identity", "invalid_appserver_message", submitted=True)

    def test_missing_turn_identity_is_rejected_after_submission(self):
        self.exercise_failure("missing_identity", "invalid_appserver_message", submitted=True)

    def test_missing_item_identity_is_rejected_after_submission(self):
        self.exercise_failure("missing_item", "invalid_appserver_message", submitted=True)

    def test_completed_item_requires_its_own_item_id(self):
        self.exercise_failure("missing_completed_item", "invalid_appserver_message", submitted=True)

    def test_started_item_requires_its_own_item_id(self):
        self.exercise_failure("missing_started_item", "invalid_appserver_message", submitted=True)

    def test_wrong_nested_sdk_completion_turn_id_is_rejected(self):
        self.exercise_failure("wrong_completion", "invalid_appserver_message", submitted=True)

    def test_tool_event_is_rejected_without_retry(self):
        self.exercise_failure("tool", "unsafe_tool_event", submitted=True)

    def test_server_request_is_rejected_without_retry(self):
        self.exercise_failure("server_request", "unsafe_tool_event", submitted=True)

    def test_eof_cleans_early_leader_and_inherited_pipe_holders(self):
        self.exercise_failure("eof", "appserver_exited", submitted=True, descendant=True)

    def test_timeout_cleans_silent_session_and_descendant(self):
        started = time.monotonic()
        self.exercise_failure("timeout", "timeout", submitted=True, descendant=True, timeout=3)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 2.9)
        self.assertLess(elapsed, 7)

    def wait_for_turn(self):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            try:
                calls, children = self._remember(self.root)
                if "turn/start" in self.methods() and children:
                    provider = next(call for call in calls if call.get("kind") == "provider")
                    os.kill(provider["pid"], 0)
                    os.kill(children[0]["pid"], 0)
                    return
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(0.01)
        self.fail("No actual submitted synthetic turn and descendant start receipt.")

    def exercise_cancel(self, shutdown=False):
        provider = self.create("shutdown" if shutdown else "cancel")
        cancel = threading.Event()
        results, failures = [], []
        def run():
            try:
                results.append(provider.complete(self.request(), cancel))
            except BaseException as error:
                failures.append(error)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            self.wait_for_turn()
            if shutdown:
                provider.shutdown()
            else:
                cancel.set()
            worker.join(4)
            self.assertFalse(worker.is_alive(), "Cancellation returned without process cleanup.")
            self.assertEqual(failures, [])
            self.assertEqual(len(results), 1)
            self.assert_failure(results[0], "appserver_shutdown" if shutdown else "cancelled",
                                submitted=True)
            self.assertEqual(self.methods().count("turn/start"), 1)
            self.assert_finished(descendant=True)
        finally:
            cancel.set()
            provider.shutdown()
            worker.join(4)
            self.assertFalse(worker.is_alive())

    def test_cancel_after_real_turn_start_cleans_group_and_preserves_sibling(self):
        self.exercise_cancel()

    def test_shutdown_waits_for_active_session_and_descendant_cleanup(self):
        self.exercise_cancel(shutdown=True)

    def test_precancel_and_unsupported_tasks_never_spawn_any_process(self):
        provider = self.create()
        cancel = threading.Event()
        cancel.set()
        self.assert_failure(provider.complete(self.request(), cancel), "cancelled", submitted=False)
        self.assert_failure(provider.complete(ProviderRequest(
            "image", "synthetic", "", "synthetic", image_paths=("synthetic.png",))),
            "unsupported_task", submitted=False)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.rpc(), [])
        self.assertFalse((self.root / "cache").exists())
        provider.shutdown()
        self.assertEqual(self.config.read_bytes(), self.config_before)

    def test_unsupported_version_never_probes_config_catalog_or_appserver(self):
        provider = self.create("unsupported_version")
        self.assert_failure(provider.complete(self.request()), "appserver_version_unsupported",
                            submitted=False)
        self.assertEqual(self.rpc(), [])
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.calls()[0]["args"], ["--version"])
        self.assertFalse((self.root / "cache").exists())
        self.assert_finished()
