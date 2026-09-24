"""Synthetic-only prewarm admission, cancellation, snapshots and private timings."""

import hashlib
import threading
from unittest.mock import Mock, patch

from cc_config import CFG
from cc_direction import direction_prompt
from cc_macos import translation
from cc_macos.image_fixture import PNG_BYTES
from cc_macos.configuration import ConfigurationError
from cc_macos.protocol import ProtocolError
from cc_macos.server import _Request
from cc_prompts import CODE_EXPLAIN_PROMPT, DICTIONARY_PROMPT, SYSTEM_SUFFIX
from cc_providers.base import ProviderRequest, ProviderResult
from cc_providers.darwin_process import ProcessError

if __package__:
    from .test_macos_translation import _TranslationDirectory, OUTPUT, action_request, message, request
else:
    from test_macos_translation import _TranslationDirectory, OUTPUT, action_request, message, request


def warm_request(**changes):
    return {"operation": "prewarm", "app_language": "en_US"} | changes


class PrewarmServiceTests(_TranslationDirectory):
    def setUp(self):
        super().setUp()
        self.provider.warm_up = Mock(return_value=ProviderResult(True))

    def warm(self, id_="warm", **changes):
        self.server._handle(message(id_, "request", **warm_request(**changes)))

    def test_prewarm_is_opt_in_and_exact_payload_excludes_all_user_content(self):
        self.assertIn("prewarm", self.stdout.events[0]["payload"]["capabilities"])
        self.provider.warm_up.assert_not_called()
        self.assertEqual(self.provider.requests, [])
        for language in ("zh_CN", "en_US"):
            translation.validate_prewarm_request(warm_request(app_language=language))
        invalid = [warm_request(text=""), warm_request(model="private"), warm_request(image_path="private"),
                   warm_request(direction="to_en"), warm_request(operation="translate"),
                   warm_request(app_language=True), warm_request(app_language=[]),
                   warm_request(app_language={}), warm_request(app_language="en")]
        invalid += [{key: value for key, value in warm_request().items() if key != removed}
                    for removed in warm_request()]
        for index, payload in enumerate(invalid):
            with self.subTest(index=index), self.assertRaisesRegex(ProtocolError, "^invalid_prewarm$"):
                translation.validate_prewarm_request(payload)
        self.warm(text="never a warm payload")
        self.assertEqual(self.stdout.result("warm")["payload"], {"code": "invalid_prewarm"})
        self.provider.warm_up.assert_not_called()

    def test_snapshot_uses_saved_selection_direction_language_with_empty_normal_request(self):
        config = self.config | {CFG.DIRECTION: "to_fr", CFG.LANGUAGE: "zh_CN", CFG.SUMMARY_ENABLED: True}
        self.session.perform({"operation": "config_save", "config": config})
        before = self.path.read_bytes()
        with patch.object(translation, "ProviderRequest", wraps=ProviderRequest) as factory, \
                patch.object(translation, "classify_selection", side_effect=AssertionError("classification")), \
                patch.object(translation, "codex_summary_instruction", side_effect=AssertionError("summary")), \
                patch.object(self.session._history, "find_cached", side_effect=AssertionError("cache")), \
                patch.object(self.session, "_record", side_effect=AssertionError("history")), \
                patch.object(self.session, "translate", side_effect=AssertionError("translation")):
            self.warm()
            self.assertTrue(self.stdout.terminal("warm"))
        built = factory.call_args
        self.assertEqual(built.args, ("text", "synthetic", direction_prompt("to_fr", "zh_CN") + SYSTEM_SUFFIX, ""))
        self.assertEqual(len(self.provider.warm_up.call_args_list), 1)
        arg = self.provider.warm_up.call_args.args[0]
        if self.bound_provider == "claude_cli":
            self.assertEqual((arg.task, arg.user_text, arg.image_paths), ("text", "", ()))
            self.assertEqual(arg.system_prompt, direction_prompt("to_fr", "zh_CN") + SYSTEM_SUFFIX)
        else:
            self.assertEqual(arg, "synthetic")
        self.assertIsInstance(self.provider.warm_up.call_args.kwargs["cancel_event"], threading.Event)
        events = [e for e in self.stdout.events if e["id"] == "warm"]
        self.assertEqual([e["type"] for e in events], ["accepted", "started", "completed"])
        self.assertEqual(events[-1]["payload"], {"warmed": True})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(self.history(), [])

    def test_request_language_fallback_and_post_snapshot_changes_are_isolated(self):
        self.session.perform({"operation": "config_save", "config": self.config | {CFG.LANGUAGE: ""}})
        captured = []
        def warm(arg, *, cancel_event):
            captured.append(arg)
            self.session.perform({"operation": "config_save", "config": self.config | {
                CFG.LANGUAGE: "zh_CN", CFG.DIRECTION: "to_ja"}})
            return ProviderResult(True)
        self.provider.warm_up.side_effect = warm
        with patch.object(translation, "ProviderRequest", wraps=ProviderRequest) as factory:
            self.warm()
            self.assertTrue(self.stdout.terminal("warm"))
        self.assertEqual(factory.call_args.args[2], direction_prompt("auto", "en_US") + SYSTEM_SUFFIX)
        self.assertEqual(len(captured), 1)

    def test_prewarm_does_not_block_configuration_or_history_workers(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def warm(arg, *, cancel_event):
            entered.set()
            if not release.wait(3):
                raise AssertionError("warm gate not released")
            return ProviderResult(True)
        self.provider.warm_up.side_effect = warm
        try:
            self.warm()
            self.assertTrue(entered.wait(1))
            self.server._handle(message("config", "request", operation="config_load"))
            self.server._handle(message("history", "request", operation="history_load", page_size=10, cursor=None))
            self.assertTrue(self.stdout.terminal("config"))
            self.assertTrue(self.stdout.terminal("history"))
            self.assertFalse(any(e["id"] == "warm" and e["type"] == "completed" for e in self.stdout.events))
        finally:
            release.set()
        self.assertTrue(self.stdout.terminal("warm"))

    def test_duplicate_warm_is_rejected_and_foreground_cancels_inflight_warm_first(self):
        entered = threading.Event()
        cancel_seen = []
        def warm(arg, *, cancel_event):
            cancel_seen.append(cancel_event)
            entered.set()
            if not cancel_event.wait(3):
                raise AssertionError("foreground did not cancel warm")
            return ProviderResult(False, error_code="cancelled")
        self.provider.warm_up.side_effect = warm
        self.warm()
        self.assertTrue(entered.wait(1))
        self.warm("duplicate")
        self.assertEqual(self.stdout.result("duplicate")["payload"], {"code": "busy"})
        actual = self.provider.stream
        def stream(*args):
            self.assertTrue(cancel_seen[0].is_set())
            return actual(*args)
        with patch.object(self.provider, "stream", side_effect=stream):
            self.translate("foreground")
            self.assertTrue(self.stdout.terminal("foreground"))
        self.assertTrue(self.stdout.terminal("warm"))
        self.assertEqual(self.stdout.result("warm")["payload"], {})
        self.assertEqual(self.stdout.result("warm")["type"], "cancelled")
        self.assertEqual(len(self.provider.requests), 1)
        self.provider.warm_up.assert_called_once()

    def test_foreground_preempts_pending_warm_and_warm_never_enters_provider(self):
        with self.server._lock:
            self.warm()
            self.translate("foreground")
        self.assertTrue(self.stdout.terminal("foreground"))
        self.assertTrue(self.stdout.terminal("warm"))
        self.assertEqual(self.stdout.result("warm")["type"], "cancelled")
        self.assertEqual(self.stdout.result("warm")["seq"], 1)
        self.provider.warm_up.assert_not_called()

    def test_foreground_has_capacity_even_while_one_warm_is_draining(self):
        with self.server._lock:
            self.warm()
            for index in range(3):
                self.server._tasks[f"occupied{index}"] = _Request(f"occupied{index}", started=True)
            try:
                self.translate("foreground")
            finally:
                for index in range(3):
                    self.server._tasks.pop(f"occupied{index}")
        self.assertTrue(self.stdout.terminal("foreground"))
        self.assertEqual(self.stdout.result("foreground")["type"], "completed")

    def test_warm_is_rejected_while_foreground_is_queued_or_running(self):
        self.provider.release.clear()
        self.translate()
        self.assertTrue(self.provider.entered.wait(1))
        self.warm()
        self.assertEqual(self.stdout.result("warm")["payload"], {"code": "busy"})
        self.provider.warm_up.assert_not_called()
        self.provider.release.set()
        self.assertTrue(self.stdout.terminal("translate"))

    def test_cancel_and_shutdown_drain_inflight_prewarm_without_submission(self):
        for operation in ("cancel", "shutdown"):
            entered = threading.Event()
            def warm(arg, *, cancel_event):
                entered.set()
                if not cancel_event.wait(3):
                    raise AssertionError("control did not cancel")
                return ProviderResult(False, error_code="cancelled")
            self.provider.warm_up.side_effect = warm
            self.warm(operation)
            self.assertTrue(entered.wait(1))
            if operation == "shutdown":
                self.server._stop()
            else:
                self.server._handle(message("cancel-control", "cancel", request_id=operation))
                self.assertEqual(self.stdout.result("cancel-control")["payload"], {"cancel_requested": True})
            self.server._join_workers()
            self.assertEqual(self.stdout.result(operation)["type"], "cancelled")
            self.assertEqual(self.stdout.result(operation)["payload"], {})
            self.assertEqual(self.provider.requests, [])
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(self.session.prewarm(warm_request(), cancel, lambda: True), ("cancelled", {}))

    def test_failures_are_fixed_safe_codes_cleanup_failure_beats_cancellation_and_no_retry(self):
        for index, (raw, expected) in enumerate((
                ("private model account path", "prewarm_failed"),
                ("probe_timeout", "prewarm_failed"),
                ("provider_cleanup_failed", "provider_cleanup_failed"))):
            self.provider.warm_up.side_effect = None
            self.provider.warm_up.return_value = ProviderResult(False, error_code=raw)
            self.warm(f"failed{index}")
            self.assertTrue(self.stdout.terminal(f"failed{index}"))
            self.assertEqual(self.stdout.result(f"failed{index}")["payload"],
                             {"code": expected, "submitted": False})
        self.assertEqual(self.provider.warm_up.call_count, 3)
        for raises in (False, True):
            cancel = threading.Event()
            def cleanup(arg, *, cancel_event):
                cancel_event.set()
                if raises:
                    raise ProcessError("probe_cleanup_failed")
                return ProviderResult(False, error_code="provider_cleanup_failed")
            self.provider.warm_up.side_effect = cleanup
            with self.assertRaisesRegex(translation.TranslationError, "^provider_cleanup_failed$"):
                self.session.prewarm(warm_request(), cancel, lambda: True)
        for error in (ProcessError("private"), OSError("private")):
            self.provider.warm_up.side_effect = error
            with self.assertRaisesRegex(translation.TranslationError, "^prewarm_failed$"):
                self.session.prewarm(warm_request(), threading.Event(), lambda: True)
        self.provider.warm_up.side_effect = AssertionError("programming fault")
        with self.assertRaisesRegex(AssertionError, "programming fault"):
            self.session.prewarm(warm_request(), threading.Event(), lambda: True)

    def test_snapshot_failure_and_cancel_at_finish_do_not_claim_warmed(self):
        with patch.object(self.session, "_translation_config", side_effect=ConfigurationError("config_io_failed")):
            self.warm()
            self.assertTrue(self.stdout.terminal("warm"))
        self.assertEqual(self.stdout.result("warm")["payload"], {"code": "config_io_failed", "submitted": False})
        self.provider.warm_up.assert_not_called()
        self.assertEqual(self.session.prewarm(warm_request(), threading.Event(), lambda: False), ("cancelled", {}))


class ClaudePrewarmServiceTests(PrewarmServiceTests):
    bound_provider = "claude_cli"


class ClaudeWarmProfileTests(_TranslationDirectory):
    bound_provider = "claude_cli"
    prose = "This synthetic private PDF sentence contains prose to summarize accurately. " * 9

    def setUp(self):
        super().setUp()
        self.provider.warm_up = Mock(return_value=ProviderResult(True))
        self.config.update({CFG.SUMMARY_ENABLED: True, CFG.LANGUAGE: "en_US"})
        self.save()

    def save(self, **changes):
        self.session.perform({"operation": "config_save", "config": self.config | changes})

    def translate_direct(self, text, *, use_cache=False, record_history=False, begin_finish=lambda: True):
        return self.session.translate(request(text=text, use_cache=use_cache, record_history=record_history),
                                      threading.Event(), lambda text: None, begin_finish)

    def warm_direct(self, language="en_US"):
        self.assertEqual(self.session.prewarm(
            warm_request(app_language=language), threading.Event(), lambda: True), ("completed", {"warmed": True}))
        return self.provider.warm_up.call_args.args[0]

    def assert_profile_matches(self, source_request):
        warmed = self.warm_direct()
        self.assertEqual((warmed.task, warmed.model, warmed.system_prompt),
                         (source_request.task, source_request.model, source_request.system_prompt))
        self.assertEqual((warmed.user_text, warmed.image_paths), ("", ()))
        self.assertEqual(warmed.timeout_seconds, 90)
        return warmed

    def test_first_warm_is_normal_then_dictionary_code_and_summary_refill_exact_last_profile(self):
        self.assertEqual(self.warm_direct().system_prompt, direction_prompt("auto", "en_US") + SYSTEM_SUFFIX)
        self.assertIsNone(self.session._warm_profile)
        for text, task, prompt in (
                ("serendipity", "text", DICTIONARY_PROMPT),
                ("def private_function():\n    return 42", "text", CODE_EXPLAIN_PROMPT),
                (self.prose, "translation_summary", None)):
            for repeat in range(2):
                with self.subTest(task=task, repeat=repeat):
                    self.assertEqual(self.translate_direct(text)[0], "completed")
                    source = self.provider.requests[-1]
                    self.assertEqual(source.task, task)
                    if prompt is not None:
                        self.assertEqual(source.system_prompt, prompt)
                    calls = len(self.provider.requests)
                    warmed = self.assert_profile_matches(source)
                    self.assertEqual(self.warm_direct(), warmed)
                    self.assertEqual(len(self.provider.requests), calls)
        self.assertEqual(self.history(), [])

    def test_profile_contains_only_small_settings_key_and_content_free_task_model_prompt(self):
        original = self.prose
        translated = "Synthetic secret translated output that must never enter warm metadata."
        self.provider.text, self.provider.chunks = translated, [translated]
        self.translate_direct(original)
        source = self.provider.requests[-1]
        profile = self.session._warm_profile
        self.assertEqual(profile, (
            ("claude_cli", "synthetic", "auto", "en_US", True),
            source.task, source.model, source.system_prompt))
        self.assertNotIn(original, repr(profile))
        self.assertNotIn(translated, repr(profile))
        warmed = self.assert_profile_matches(source)
        self.assertEqual(warmed.user_text, "")
        self.assertNotIn(original, repr(warmed))
        self.assertNotIn(translated, repr(warmed))
        self.assertEqual(self.history(), [])

    def test_model_direction_language_summary_changes_invalidate_and_clear_profile(self):
        for changes, model, direction, language in (
                ({CFG.CLAUDE_MODEL: "another-model"}, "another-model", "auto", "en_US"),
                ({CFG.DIRECTION: "to_fr"}, "synthetic", "to_fr", "en_US"),
                ({CFG.LANGUAGE: "zh_CN"}, "synthetic", "auto", "zh_CN"),
                ({CFG.SUMMARY_ENABLED: False}, "synthetic", "auto", "en_US")):
            with self.subTest(changes=changes):
                self.save()
                self.translate_direct("serendipity")
                self.assertIsNotNone(self.session._warm_profile)
                self.save(**changes)
                warmed = self.warm_direct()
                self.assertEqual((warmed.task, warmed.model, warmed.system_prompt),
                                 ("text", model, direction_prompt(direction, language) + SYSTEM_SUFFIX))
                self.assertIsNone(self.session._warm_profile)
                self.save()
                self.assertEqual(self.warm_direct().system_prompt,
                                 direction_prompt("auto", "en_US") + SYSTEM_SUFFIX)

    def test_provider_change_cannot_reuse_profile_and_unrelated_preferences_preserve_it(self):
        self.translate_direct("serendipity")
        source = self.provider.requests[-1]
        self.save(**{CFG.HISTORY_ENABLED: False})
        self.assert_profile_matches(source)
        self.save(**{CFG.MODEL_PROVIDER: "codex_cli"})
        before = self.provider.warm_up.call_count
        with self.assertRaisesRegex(translation.TranslationError, "^unsupported_provider$"):
            self.warm_direct()
        self.assertEqual(self.provider.warm_up.call_count, before)

    def test_effective_fallback_language_invalidates_while_explicit_saved_language_does_not(self):
        self.save(**{CFG.LANGUAGE: ""})
        self.translate_direct("serendipity")
        self.assertEqual(self.warm_direct("en_US").system_prompt, DICTIONARY_PROMPT)
        self.assertEqual(self.warm_direct("zh_CN").system_prompt, direction_prompt("auto", "zh_CN") + SYSTEM_SUFFIX)
        self.assertIsNone(self.session._warm_profile)
        self.save()
        self.translate_direct("serendipity")
        self.assertEqual(self.warm_direct("zh_CN").system_prompt, DICTIONARY_PROMPT)

    def test_settings_changed_during_successful_translation_do_not_mislabel_old_profile(self):
        stream = self.provider.stream
        def changed(*args):
            self.save(**{CFG.DIRECTION: "to_ja"})
            return stream(*args)
        with patch.object(self.provider, "stream", side_effect=changed):
            self.translate_direct(self.prose)
        self.assertEqual(self.session._warm_profile[0][2], "auto")
        self.assertEqual(self.warm_direct().system_prompt, direction_prompt("to_ja", "en_US") + SYSTEM_SUFFIX)
        self.assertIsNone(self.session._warm_profile)

    def test_actions_and_images_never_replace_main_profile_or_retain_image_paths(self):
        self.translate_direct("serendipity")
        source, profile = self.provider.requests[-1], self.session._warm_profile
        for action in translation.RESULT_ACTIONS:
            event, _ = self.session.result_action(
                action_request(action), threading.Event(), lambda text: None, lambda: True)
            self.assertEqual(event, "completed")
            self.assertEqual(self.session._warm_profile, profile)
        image = self.home / "private-source-image.png"
        image.write_bytes(PNG_BYTES)
        event, _ = self.session.translate_image({
            "operation": "translate_image", "image_path": str(image),
            "image_bytes": len(PNG_BYTES), "image_sha256": hashlib.sha256(PNG_BYTES).hexdigest(),
            "app_language": "en_US", "record_history": False,
        }, threading.Event(), lambda text: None, lambda: True)
        self.assertEqual(event, "completed")
        self.assertEqual(self.session._warm_profile, profile)
        self.assertNotIn(str(image), repr(profile))
        self.assertNotIn(self.provider.requests[-1].image_paths[0], repr(profile))
        self.assert_profile_matches(source)
        self.assertEqual(self.history(), [])

    def test_failed_and_cancelled_text_cannot_replace_last_successful_profile(self):
        self.translate_direct("serendipity")
        source, profile = self.provider.requests[-1], self.session._warm_profile
        self.provider.result_code = "provider_protocol_error"
        with self.assertRaisesRegex(translation.TranslationError, "^provider_protocol_error$"):
            self.translate_direct(self.prose)
        self.assertEqual(self.session._warm_profile, profile)
        self.provider.result_code = ""
        self.assertEqual(self.translate_direct(self.prose, begin_finish=lambda: False)[0], "cancelled")
        self.assertEqual(self.session._warm_profile, profile)
        self.assert_profile_matches(source)

    def test_successful_cached_translation_refreshes_last_profile_without_model_call(self):
        self.translate_direct("serendipity", use_cache=True, record_history=True)
        dictionary = self.provider.requests[-1]
        self.translate_direct(self.prose)
        self.assertEqual(self.session._warm_profile[1], "translation_summary")
        before = len(self.provider.requests)
        event, result = self.translate_direct("serendipity", use_cache=True, record_history=True)
        self.assertEqual((event, result["cached"]), ("completed", True))
        self.assertEqual(len(self.provider.requests), before)
        self.assert_profile_matches(dictionary)

    def test_closing_releases_profile(self):
        self.translate_direct("serendipity")
        self.assertIsNotNone(self.session._warm_profile)
        self.session.close()
        self.assertIsNone(self.session._warm_profile)


class TranslationTimingTests(_TranslationDirectory):
    def test_allowlist_discards_private_keys_strings_booleans_nonfinite_negative_and_unbounded_values(self):
        valid = {key: 12.5 for key in translation.PROVIDER_TIMING_FIELDS}
        valid.update(version_cache_hit=True, warm_process_hit=False)
        metrics = valid | {"model": "synthetic-private", "account": 42, "path": 0,
                           "helper_elapsed_ms": "synthetic-private", "cache_hit": 1, "turn_submitted": True}
        expected = valid | {"version_cache_hit": 1, "warm_process_hit": 0}
        self.assertEqual(translation.provider_timings(metrics.items()), expected)
        for invalid in ("synthetic-private", None, True, False, [], {}, -1, float("nan"),
                        float("inf"), float("-inf"), translation.MAX_TIMING_MS + 1, 10 ** 500):
            self.assertEqual(translation.provider_timings((("total_ms", invalid),)), {})
        for invalid in ("1", 2, -1, float("nan"), float("inf"), None, [], {}):
            self.assertEqual(translation.provider_timings((("warm_process_hit", invalid),)), {})

    def test_live_cache_and_actions_receive_only_current_request_metrics(self):
        metrics = (("total_ms", 12.5), ("warm_process_hit", True), ("version_cache_hit", 1),
                   ("account", "synthetic-private"), ("turn_submitted", True))
        with patch.object(self.provider, "stream", return_value=ProviderResult(True, text=OUTPUT, metrics=metrics)) as model:
            self.translate("first")
            self.assertTrue(self.stdout.terminal("first"))
            self.translate("cached")
            self.assertTrue(self.stdout.terminal("cached"))
            self.server._handle(message("action", "request", **action_request()))
            self.assertTrue(self.stdout.terminal("action"))
        self.assertEqual(model.call_count, 2)
        for id_, cache in (("first", 0), ("cached", 1), ("action", 0)):
            payload = self.stdout.result(id_)["payload"]
            timings = payload["timings"]
            self.assertEqual(timings["cache_hit"], cache)
            self.assertIs(type(timings["helper_elapsed_ms"]), int)
            self.assertGreaterEqual(timings["helper_elapsed_ms"], 0)
            self.assertLessEqual(timings["helper_elapsed_ms"], translation.MAX_TIMING_MS)
            self.assertEqual(set(timings), {"helper_elapsed_ms", "cache_hit"} if cache else {
                "helper_elapsed_ms", "cache_hit", "total_ms", "warm_process_hit", "version_cache_hit"})
        self.assertNotIn(b"synthetic-private", self.stdout.getvalue())

    def test_helper_elapsed_spans_snapshot_provider_and_history_and_is_bounded(self):
        clock = [10]
        capture, record, stream = self.session._capture, self.session._record, self.provider.stream
        def take_snapshot(payload):
            clock[0] += 1
            return capture(payload)
        def model(*args):
            clock[0] += 2
            return stream(*args)
        def history(*args):
            clock[0] += 4
            return record(*args)
        with patch.object(translation.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(self.session, "_capture", side_effect=take_snapshot), \
                patch.object(self.provider, "stream", side_effect=model), \
                patch.object(self.session, "_record", side_effect=history):
            event, result = self.session.translate(request(), threading.Event(), lambda text: None, lambda: True)
        self.assertEqual(event, "completed")
        self.assertEqual(result["timings"], {"cache_hit": 0, "helper_elapsed_ms": 7000})
        with patch.object(translation.time, "monotonic", side_effect=(0, 100_000)):
            _, result = self.session.translate(request(), threading.Event(), lambda text: None, lambda: True)
        self.assertEqual(result["timings"], {"cache_hit": 1, "helper_elapsed_ms": translation.MAX_TIMING_MS})
