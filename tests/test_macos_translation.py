"""Real temporary state and deterministic scheduling around the native provider boundary."""

import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from cc_config import CFG, Config, DEFAULT_CONFIG
from cc_direction import DIRECTION_MODES, direction_prompt, resolve_target_lang
from cc_prompts import CODE_EXPLAIN_PROMPT, DICTIONARY_PROMPT, SYSTEM_SUFFIX
from cc_summary import codex_summary_instruction
from cc_providers.base import ProviderResult
from cc_providers.darwin_process import ProcessError
from cc_macos import configuration, translation
from cc_macos.protocol import RESERVED_ID, ProtocolError, decode_frame
from cc_macos.server import Server

if __package__:
    from .test_macos_configuration import _ConfigurationDirectory, message
else:
    from test_macos_configuration import _ConfigurationDirectory, message


TEXT = "Synthetic input with a complete sentence."
OUTPUT = "Synthetic translated sentence."


def request(**changes):
    return dict(operation="translate", text=TEXT, app_language="en_US", origin="text",
                use_cache=True, record_history=True) | changes


class TranslationContracts(unittest.TestCase):
    def test_version_failure_categories_remain_distinct_and_allowlisted(self):
        for suffix in ("unsupported", "unreadable", "prerelease"):
            code = translation.provider_failure("appserver_version_" + suffix)
            self.assertEqual(code, "provider_version_" + suffix)
            self.assertIn(code, translation.TRANSLATION_FAILURE_CODES)
        self.assertEqual(translation.provider_failure("invalid_appserver_message"),
                         "provider_protocol_error")

    def test_exact_payload_types_and_unknown_fields(self):
        translation.validate_translation_request(request())
        for value in (request(extra=True), request(text=" "), request(text=True),
                      request(use_cache=1), request(record_history="true"), request(origin=[]),
                      request(app_language={}), request(operation="fixture")):
            with self.subTest(value=value), self.assertRaisesRegex(ProtocolError, "invalid_translation"):
                translation.validate_translation_request(value)

    def test_request_utf8_limit_and_invalid_unicode(self):
        translation.validate_translation_request(request(text="a" * 8192))
        for text in ("a" * 8193, "\u4e2d" * 2731, "\ud800"):
            with self.assertRaisesRegex(ProtocolError, "invalid_translation"):
                translation.validate_translation_request(request(text=text))

    def test_explicit_cli_environment_has_no_fallback_or_per_request_paths(self):
        home = str(Path.cwd())
        valid = json.dumps({"HOME": home, "PATH": "", "SECRET": "synthetic"})
        environment = {translation.CLI_ENVIRONMENT_KEY: valid}
        self.assertEqual(translation.parse_cli_environment(environment, home)["SECRET"], "synthetic")
        for raw in (None, "[]", '{"HOME":"x","HOME":"y"}', '{"HOME":null}',
                    json.dumps({"HOME": home, "PATH": "", "x": ["bad"]}),
                    json.dumps({"HOME": home, "PATH": "", "x": "\0"}),
                    json.dumps({"HOME": home, "PATH": "", "x": "a" * 32768})):
            with self.assertRaisesRegex(ProtocolError, "invalid_startup"):
                translation.parse_cli_environment({translation.CLI_ENVIRONMENT_KEY: raw}, home)

    def test_startup_is_explicit_and_does_not_construct_provider_or_create_paths(self):
        home, command = str(Path.cwd()), str(Path.cwd() / "synthetic-cli")
        env = {translation.CLI_ENVIRONMENT_KEY: json.dumps({"HOME": home, "PATH": ""})}
        with patch.object(translation, "DarwinCodexProvider") as provider, \
                patch.object(Path, "mkdir", side_effect=AssertionError("implicit mkdir")):
            self.assertIsNone(configuration.startup_configuration([], environment=env))
            session = configuration.startup_configuration(
                ["--config-home", home, "--application-id", "synthetic",
                 "--codex-command", command], environment=env)
            self.assertIsInstance(session, translation.TranslationSession)
            session.close()
            provider.assert_not_called()

    def test_only_explicit_translation_main_installs_and_restores_termination_handler(self):
        from cc_macos import server

        for enabled in (False, True):
            for failed in (False, True):
                with self.subTest(enabled=enabled, failed=failed), \
                        patch.object(server, "startup_configuration", return_value=None), \
                        patch.object(server, "Server") as factory, \
                        patch.object(server.signal, "signal", return_value=signal.SIG_DFL) as install:
                    instance = factory.return_value
                    instance._translation_enabled = enabled
                    if failed:
                        instance.run.side_effect = RuntimeError("synthetic")
                        with self.assertRaisesRegex(RuntimeError, "synthetic"):
                            server.main([])
                    else:
                        instance.run.return_value = 0
                        self.assertEqual(server.main([]), 0)
                    if enabled:
                        self.assertEqual(install.call_count, 2)
                        self.assertEqual(install.call_args_list[0].args,
                                         (signal.SIGTERM, instance.request_termination))
                        self.assertEqual(install.call_args_list[1].args, (signal.SIGTERM, signal.SIG_DFL))
                    else:
                        install.assert_not_called()

    def test_snapshot_freezes_settings_and_reuses_direction_prompt_and_cache_bytes(self):
        cfg = Config({CFG.CODEX_MODEL: "synthetic", CFG.SUMMARY_ENABLED: False,
                      CFG.LABS_DEFAULTS_MIGRATED: True, "future": {"x": [1]}})
        snap = translation.snapshot_for_translation(cfg, request())
        self.assertEqual(snap.request.system_prompt, direction_prompt("auto", "en_US") + SYSTEM_SUFFIX)
        self.assertEqual(snap.sig, "codex_cli|synthetic|auto|sum0|en_US|codex-format-v5")
        self.assertEqual((snap.kind, snap.target_lang, snap.request.timeout_seconds), ("text", "zh", 90))
        cfg[CFG.CODEX_MODEL] = "changed"
        cfg["future"]["x"].append(2)
        self.assertEqual(snap.selection.model, "synthetic")
        self.assertEqual(snap.config["future"]["x"], (1,))

    def test_unsupported_provider_and_invalid_settings_are_explicit(self):
        for values, code in (({CFG.MODEL_PROVIDER: "claude_cli"}, "unsupported_provider"),
                             ({CFG.DIRECTION: "unknown"}, "invalid_translation_settings"),
                             ({CFG.CODEX_MODEL: "x" * 257}, "invalid_translation_settings"),
                             ({CFG.MAX_CHARS: 1}, "invalid_translation_settings")):
            with self.assertRaisesRegex(translation.TranslationError, code):
                translation.snapshot_for_translation(Config(values), request())

    def test_snapshot_routes_code_dictionary_and_long_prose_with_shared_prompt_bytes(self):
        prose = "This is synthetic prose with complete sentences for the summary contract. " * 8
        for direction in DIRECTION_MODES:
            config = Config(dict(DEFAULT_CONFIG) | {CFG.DIRECTION: direction})
            for text, kind, prompt in (
                    ("hello", "dict", DICTIONARY_PROMPT),
                    ("def greeting():\n    return 42", "code", CODE_EXPLAIN_PROMPT)):
                with self.subTest(direction=direction, kind=kind):
                    snap = translation.snapshot_for_translation(config, request(text=text))
                    self.assertEqual((snap.kind, snap.target_lang, snap.summarize), (kind, None, False))
                    self.assertEqual(snap.request.system_prompt, prompt)
                    self.assertEqual(snap.request.task, "text")
            target = resolve_target_lang(direction, "en_US", prose)
            snap = translation.snapshot_for_translation(config, request(text=prose))
            self.assertEqual((snap.kind, snap.target_lang, snap.summarize), ("text", target, True))
            self.assertEqual(snap.request.system_prompt, codex_summary_instruction(target))
            self.assertEqual(snap.request.task, "translation_summary")
            config[CFG.SUMMARY_ENABLED] = False
            plain = translation.snapshot_for_translation(config, request(text=prose))
            self.assertFalse(plain.summarize)
            self.assertEqual(plain.request.system_prompt, direction_prompt(direction, "en_US") + SYSTEM_SUFFIX)

    def test_json_string_budget_counts_escapes_and_utf8(self):
        self.assertEqual(translation.text_bytes("\0" * 3999 + "aaaa"), 24000)
        self.assertEqual(translation.text_bytes("\u4e2d" * 7999 + "a"), 24000)
        self.assertEqual(translation.text_bytes("\n\\\""), 8)

    def test_default_server_import_has_no_home_environment_or_state_side_effects(self):
        core = str(Path(translation.__file__).resolve().parents[1])
        script = r"""
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
def forbidden(*args, **kwargs): raise AssertionError("implicit user state")
class ForbiddenEnvironment(dict):
    __getitem__ = get = __iter__ = items = keys = values = copy = forbidden
os.environ = ForbiddenEnvironment()
pathlib.Path.home = forbidden
pathlib.Path.mkdir = forbidden
os.makedirs = forbidden
import cc_macos.server
assert cc_macos.server.startup_configuration([]) is None
assert not any(name in sys.modules for name in ("cc_core", "cc_providers", "tkinter", "win32api", "win32gui"))
"""
        with tempfile.TemporaryDirectory(prefix="synthetic-translation-import-") as directory:
            completed = subprocess.run([sys.executable, "-I", "-B", "-c", script, core], cwd=directory,
                                       env=dict(os.environ, HOME=directory, USERPROFILE=directory),
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_all_synthetic_fixture_scenarios_prepare_without_cli_and_match_migration_policy(self):
        from cc_macos import translation_fixture

        with tempfile.TemporaryDirectory(prefix=".translation-fixture-", dir=Path.cwd()) as directory:
            for scenario in translation_fixture.SCENARIOS:
                with self.subTest(scenario=scenario):
                    fixture = translation_fixture.prepare(Path(directory) / scenario, "synthetic", scenario)
                    self.assertEqual(fixture["environment"]["HOME"], fixture["home"])
                    report = translation_fixture.verify(fixture["root"])
                    self.assertEqual((report["submitted_turns"], report["processes"]), (0, 0))
                    self.assertFalse(report["cleanup_verified"])
                    self.assertTrue(fixture["expected"]["stream"])
                    if scenario == "streaming-migration":
                        self.assertIs(fixture["config"]["codex_streaming_experimental"], False)


class ScriptedProvider:
    def __init__(self, *args, **kwargs):
        self.requests = []
        self.chunks, self.text = [OUTPUT], OUTPUT
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.result_code = ""
        self.closed = 0

    def stream(self, captured, on_delta, cancel):
        self.requests.append(captured)
        self.entered.set()
        if not self.release.wait(3):
            raise AssertionError("Synthetic provider gate was not released.")
        if cancel.is_set():
            return ProviderResult(False, error_code="cancelled", metrics=(("turn_submitted", True),))
        try:
            for text in self.chunks:
                on_delta(text)
        except ProcessError as error:
            return ProviderResult(False, error_code=str(error), metrics=(("turn_submitted", True),))
        return ProviderResult(not self.result_code, text=self.text, error_code=self.result_code,
                              metrics=(("turn_submitted", True),))

    def complete(self, captured, cancel):
        return self.stream(captured, lambda _text: None, cancel)

    def shutdown(self):
        self.closed += 1
        self.release.set()


class EventOutput(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.events = []
        self.condition = threading.Condition()
        self.break_on_delta = False

    def write(self, raw):
        event = decode_frame(raw)
        if self.break_on_delta and event["type"] == "delta":
            raise BrokenPipeError("SYNTHETIC_PRIVATE")
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()
        return super().write(raw)

    def terminal(self, id_):
        with self.condition:
            return self.condition.wait_for(lambda: any(
                e["id"] == id_ and e["type"] in ("completed", "cancelled", "failed") for e in self.events), 3)

    def result(self, id_):
        return next(e for e in reversed(self.events) if e["id"] == id_ and e["type"] in
                    ("completed", "cancelled", "failed"))


class TranslationServiceTests(_ConfigurationDirectory):
    def setUp(self):
        super().setUp()
        self.provider = ScriptedProvider()
        provider_patch = patch.object(translation, "DarwinCodexProvider", return_value=self.provider)
        provider_patch.start()
        self.addCleanup(provider_patch.stop)
        self.session = translation.TranslationSession(
            str(self.home), self.identity, str(self.home / "synthetic-cli"),
            {"HOME": str(self.home), "PATH": ""})
        self.addCleanup(self.session.close)
        self.stdout, self.stderr = EventOutput(), io.StringIO()
        self.server = Server(io.BytesIO(), self.stdout, self.stderr, configuration=self.session)
        self.server._handle(message("hello", "hello"))
        self.addCleanup(self.server._join_workers)
        self.addCleanup(self.provider.release.set)
        self.addCleanup(self.server._stop)
        self.config = dict(DEFAULT_CONFIG) | {CFG.CODEX_MODEL: "synthetic", CFG.SUMMARY_ENABLED: False}
        self.session.perform({"operation": "config_save", "config": self.config})

    def translate(self, id_="translate", **changes):
        self.server._handle(message(id_, "request", **request(**changes)))

    def history(self):
        return self.session.perform_history(
            {"operation": "history_load", "page_size": 100, "cursor": None}, "read", 2)["entries"]

    def test_termination_signal_only_marks_then_run_drains_execution_and_releases_owners(self):
        class Reader:
            def __init__(self, _stream, stopping):
                self.stopping = stopping

            def read(self):
                while not self.stopping.is_set():
                    self.stopping.event.wait(0.01)
                return None

        self.provider.release.clear()
        self.translate()
        self.assertTrue(self.provider.entered.wait(1))
        results = []
        with patch("cc_macos.server.PipeFrameReader", Reader):
            worker = threading.Thread(target=lambda: results.append(self.server.run()))
            worker.start()
            try:
                with self.server._lock:
                    self.server.request_termination(signal.SIGTERM, None)
                    self.assertFalse(self.server._stopping)
                    self.assertFalse(self.server._stop_event.is_set())
                    self.assertTrue(self.server._read_stop.is_set())
                self.assertTrue(self.server._stop_event.wait(1))
                self.assertTrue(worker.is_alive())
                self.assertEqual(self.provider.closed, 0)
                self.assertIsNotNone(self.session._owner)
                self.assertFalse(any(e["id"] == "translate" and e["type"] == "cancelled"
                                     for e in self.stdout.events))
            finally:
                self.provider.release.set()
                worker.join(4)
            self.assertFalse(worker.is_alive())
        self.assertEqual(results, [128 + signal.SIGTERM])
        self.assertEqual(self.stdout.result("translate")["payload"], {"submitted": True})
        self.assertEqual(self.provider.closed, 1)
        self.assertIsNone(self.session._owner)
        self.assertIsNone(self.session._history)
        self.assertEqual(self.stderr.getvalue(), "")

    def test_history_commit_cannot_be_cancelled_or_released_early(self):
        entered, release = threading.Event(), threading.Event()
        original = self.session._record
        def record(*args):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic history gate was not released.")
            return original(*args)
        with patch.object(self.session, "_record", side_effect=record):
            self.translate()
            try:
                self.assertTrue(entered.wait(1))
                self.server._handle(message("cancel", "cancel", request_id="translate"))
                self.assertIs(self.stdout.result("cancel")["payload"]["cancel_requested"], False)
                self.server._stop()
                self.assertIsNotNone(self.session._owner)
                self.assertFalse(any(e["id"] == "translate" and e["type"] in ("cancelled", "completed")
                                     for e in self.stdout.events))
            finally:
                release.set()
            self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["type"], "completed")
        self.assertEqual(len(self.history()), 1)

    def test_cleanup_failure_has_priority_over_started_cancellation(self):
        def fail(_request, _delta, cancel):
            cancel.set()
            return ProviderResult(False, error_code="group_cleanup_failed",
                                  metrics=(("turn_submitted", True),))
        with patch.object(self.provider, "stream", side_effect=fail):
            self.translate()
            self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"],
                         {"code": "provider_cleanup_failed", "submitted": True})
        self.assertEqual(self.history(), [])

    def test_shutdown_cleanup_failure_is_fixed_and_releases_both_state_owners(self):
        reader = iter([message("shutdown", "shutdown")])
        with patch("cc_macos.server.PipeFrameReader") as factory, \
                patch.object(self.provider, "shutdown", side_effect=ProcessError("PRIVATE_cleanup_failed")):
            factory.return_value.read.side_effect = lambda: next(reader)
            self.assertEqual(self.server.run(), 2)
        self.assertEqual(self.stdout.result("shutdown")["payload"], {"code": "provider_cleanup_failed"})
        self.assertIsNone(self.session._owner)
        self.assertIsNone(self.session._history)
        self.assertNotIn("PRIVATE", self.stderr.getvalue())

    def test_unexpected_transport_error_closes_without_fabricating_submission_state(self):
        with patch.object(self.provider, "stream", side_effect=ProcessError("PRIVATE_transport")):
            self.translate()
            self.server._join_workers()
        self.assertEqual([e["type"] for e in self.stdout.events if e["id"] == "translate"],
                         ["accepted", "started"])
        self.assertEqual(self.stdout.result(RESERVED_ID)["payload"], {"code": "internal_error"})
        self.assertTrue(self.server._stopping)
        self.assertNotIn("PRIVATE", self.stderr.getvalue())

    def test_invalid_unicode_delta_and_final_text_have_fixed_errors_without_history(self):
        for id_, chunks, text in (("delta", ["\ud800"], OUTPUT), ("final", [], "\ud800")):
            with self.subTest(id_=id_):
                self.provider.chunks, self.provider.text = chunks, text
                self.translate(id_)
                self.assertTrue(self.stdout.terminal(id_))
                self.assertEqual(self.stdout.result(id_)["payload"],
                                 {"code": "provider_protocol_error", "submitted": True})
        self.assertEqual(self.history(), [])

    def test_ready_is_native_and_first_request_streams_records_then_hits_cache(self):
        ready = self.stdout.events[0]["payload"]
        self.assertEqual((ready["backend"], ready["fixture"]), ("native_appserver", False))
        self.assertEqual(len(ready["capabilities"]), 6)
        self.assertEqual(self.provider.requests, [])
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        events = [e for e in self.stdout.events if e["id"] == "translate"]
        self.assertEqual([e["type"] for e in events], ["accepted", "started", "delta", "completed"])
        self.assertEqual([e["seq"] for e in events], [0, 1, 2, 3])
        self.assertEqual(events[-1]["payload"]["history"], "recorded")
        self.assertEqual(self.history()[0]["input"], TEXT)
        self.translate("cached")
        self.assertTrue(self.stdout.terminal("cached"))
        result = self.stdout.result("cached")["payload"]
        self.assertEqual((result["cached"], result["submitted"], result["history"]), (True, False, "unchanged"))
        self.assertEqual(len(self.provider.requests), 1)

    def test_current_history_optout_can_commit_while_native_request_is_running(self):
        self.provider.release.clear()
        self.translate()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.server._handle(message("optout", "request", operation="config_save",
                                        config=self.config | {CFG.HISTORY_ENABLED: False}))
            self.assertTrue(self.stdout.terminal("optout"))
            self.assertFalse(self.session.perform({"operation": "config_load"})["config"][CFG.HISTORY_ENABLED])
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"]["history"], "disabled")
        self.assertEqual(self.history(), [])

    def test_started_cancel_waits_for_execution_cleanup_before_terminal(self):
        self.provider.release.clear()
        self.translate()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.server._handle(message("cancel", "cancel", request_id="translate"))
            self.assertTrue(self.stdout.result("cancel")["payload"]["cancel_requested"])
            self.assertFalse(any(e["id"] == "translate" and e["type"] == "cancelled" for e in self.stdout.events))
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"], {"submitted": True})
        self.assertEqual(self.history(), [])

    def test_queued_cancel_is_determinate_and_does_not_execute(self):
        captured = []
        with patch.object(self.server, "_start_translation",
                          side_effect=lambda req, payload: captured.append((req, payload)) or True):
            self.translate()
        self.server._handle(message("cancel", "cancel", request_id="translate"))
        self.server._translate(*captured[0])
        events = [e for e in self.stdout.events if e["id"] == "translate"]
        self.assertEqual([(e["seq"], e["type"], e["payload"]) for e in events],
                         [(0, "accepted", {"operation": "translate"}), (1, "cancelled", {})])
        self.assertEqual(self.provider.requests, [])

    def test_thread_start_failure_keeps_original_exact_prestart_terminal(self):
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("synthetic")):
            self.translate()
        events = [e for e in self.stdout.events if e["id"] == "translate"]
        self.assertEqual([(e["seq"], e["type"]) for e in events], [(0, "accepted"), (1, "failed")])
        self.assertEqual(events[-1]["payload"], {"code": "worker_start_failed"})
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(self.server._workers, set())

    def test_malformed_and_oversize_requests_fail_before_acceptance(self):
        self.translate(text="a" * 8193)
        result = self.stdout.result("translate")
        self.assertEqual((result["seq"], result["type"], result["payload"]),
                         (0, "failed", {"code": "invalid_translation"}))
        self.assertEqual(self.provider.requests, [])

    def test_corrupt_configuration_is_not_overwritten_and_does_not_call_provider(self):
        self.path.write_bytes(b"{broken")
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"], {"code": "invalid_config", "submitted": False})
        self.assertEqual(self.path.read_bytes(), b"{broken")
        self.assertEqual(self.provider.requests, [])

    def test_corrupt_history_is_not_an_empty_cache(self):
        path = self.directory / "history.json"
        path.write_bytes(b"not-json")
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"], {"code": "invalid_history", "submitted": False})
        self.assertEqual(path.read_bytes(), b"not-json")
        self.assertEqual(self.provider.requests, [])

    def test_output_escape_budget_rejects_without_truncation_or_history(self):
        self.provider.chunks = ["\0" * 4000]
        self.provider.text = self.provider.chunks[0]
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"],
                         {"code": "translation_output_limit", "submitted": True})
        self.assertEqual(self.history(), [])

    def test_exact_output_budget_splits_utf8_and_preserves_result(self):
        text = "\u4e2d" * 7999 + "a"
        self.provider.chunks, self.provider.text = [text], text
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        deltas = [e["payload"]["text"] for e in self.stdout.events if e["type"] == "delta"]
        self.assertEqual("".join(deltas), text)
        self.assertTrue(all(translation.text_bytes(value) <= 4096 for value in deltas))
        self.assertEqual(self.stdout.result("translate")["payload"]["text"], text)

    def test_stream_budget_counts_actual_envelopes_and_retains_space_for_failure(self):
        self.provider.chunks = ["x"] * 23000
        self.translate(id_="r" * 64, use_cache=False)
        self.assertTrue(self.stdout.terminal("r" * 64))
        result = self.stdout.result("r" * 64)
        self.assertEqual(result["payload"], {"code": "translation_output_limit", "submitted": True})
        self.assertLessEqual(len(self.stdout.getvalue()), translation.MAX_STREAM_BYTES + 512)
        self.assertEqual(self.history(), [])

    def test_explicit_record_optout_and_disabled_cache_do_not_change_history(self):
        self.translate(record_history=False, use_cache=False)
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"]["history"], "disabled")
        self.assertEqual(self.history(), [])

    def test_record_failure_surfaces_separately_without_losing_successful_translation(self):
        with patch.object(self.session, "perform_history", side_effect=configuration.ConfigurationError("history_io_failed")):
            self.translate()
            self.assertTrue(self.stdout.terminal("translate"))
        result = self.stdout.result("translate")
        self.assertEqual(result["type"], "completed")
        self.assertEqual((result["payload"]["text"], result["payload"]["history_error"]),
                         (OUTPUT, "history_io_failed"))
        self.assertEqual(result["payload"]["history"], "failed")

    def test_shutdown_cancels_active_translation_then_releases_workers(self):
        self.provider.release.clear()
        self.translate()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.assertFalse(self.server._handle(message("shutdown", "shutdown")))
            self.assertTrue(self.server._tasks["translate"].cancel.is_set())
        finally:
            self.provider.release.set()
        self.server._join_workers()
        self.assertEqual(self.stdout.result("translate")["type"], "cancelled")
        self.assertEqual(self.provider.requests[0].user_text, TEXT)

    def test_lost_stdout_cancels_without_replaying_or_recording(self):
        self.stdout.break_on_delta = True
        self.translate()
        self.server._join_workers()
        self.assertTrue(self.server._pipe_closed)
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.history(), [])
        self.assertNotIn("SYNTHETIC_PRIVATE", self.stderr.getvalue())

    def test_failure_mapping_does_not_echo_provider_detail(self):
        self.provider.result_code = "SYNTHETIC_PRIVATE_PROVIDER"
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"], {"code": "provider_failed", "submitted": True})
        self.assertNotIn("SYNTHETIC_PRIVATE", self.stdout.getvalue().decode())


if __name__ == "__main__":
    unittest.main()
