"""Session-owned memory reuse with real config/history and synthetic providers."""

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import threading
import unittest
from unittest.mock import patch

from cc_config import CFG, Config
from cc_macos import translation
from cc_macos.translation_cache import TranslationMemoryCache
from cc_providers.base import ProviderModelInfo, ProviderResult

if __package__:
    from .test_macos_translation import _TranslationDirectory, OUTPUT, request, action_request
else:
    from test_macos_translation import _TranslationDirectory, OUTPUT, request, action_request


class TranslationMemoryIntegrationTests(_TranslationDirectory):
    def setUp(self):
        super().setUp()
        self.config[CFG.HISTORY_ENABLED] = False
        self.session.perform({"operation": "config_save", "config": self.config})
        self.token = hashlib.sha256(b"synthetic resident one").digest()
        self.info = ProviderModelInfo("synthetic", "synthetic-resolved", "low")
        self.eligible = True
        self.scope_calls = 0
        actual_stream = self.provider.stream

        def stream(*args):
            return replace(actual_stream(*args), model_info=self.info)

        @contextmanager
        def scope(model):
            self.scope_calls += 1
            yield (self.token, self.info) if self.eligible and model == self.info.requested_model else None

        self.stream_patch = patch.object(self.provider, "stream", side_effect=stream)
        self.stream_patch.start()
        self.addCleanup(self.stream_patch.stop)
        scope_patch = patch.object(self.provider, "translation_cache_scope", side_effect=scope, create=True)
        scope_patch.start()
        self.addCleanup(scope_patch.stop)

    def run_translation(self, payload=None, *, finish=lambda: True, cancel=None):
        return self.session.translate(
            payload or request(), cancel or threading.Event(), lambda _: None, finish)

    def test_repeat_text_or_selection_reuses_without_provider_history_or_fresh_provenance(self):
        for origin in ("text", "selection"):
            with self.subTest(origin=origin):
                before = len(self.provider.requests)
                with patch.object(self.session._history, "find_cached", side_effect=AssertionError("disk read")), \
                        patch.object(self.session, "perform_history", side_effect=AssertionError("disk write")):
                    event, first = self.run_translation(request(origin=origin))
                    event, cached = self.run_translation(request(origin=origin))
                self.assertEqual((event, first["cached"], cached["cached"]), ("completed", False, True))
                self.assertEqual(len(self.provider.requests), before + 1)
                self.assertEqual(cached["text"], first["text"])
                self.assertEqual(cached["history"], "unchanged")
                self.assertFalse(cached["submitted"])
                self.assertEqual(cached["model_info"], {"requested_model": "synthetic"})
                self.assertEqual(cached["timings"]["memory_cache_hit"], 1)
                self.assertEqual(first["timings"]["memory_cache_hit"], 0)
        self.assertFalse((self.directory / "history.json").exists())
        self.assertNotIn(request()["text"], repr(self.session._memory_cache._entries))
        self.assertNotIn(OUTPUT, repr(self.session._memory_cache._entries))
        self.assertEqual(self.stderr.getvalue(), "")

    def test_repeat_workload_reports_provider_calls_and_cache_hits_without_history(self):
        evidence = {}
        for use_cache in (False, True):
            self.session.perform({"operation": "config_save", "config": self.config})
            self.provider.requests.clear()
            results = [self.run_translation(request(use_cache=use_cache))[1] for _ in range(10)]
            evidence["memory_enabled" if use_cache else "forced_fresh"] = {
                "requests": len(results),
                "provider_calls": len(self.provider.requests),
                "cache_hits": sum(int(result["cached"]) for result in results),
                "memory_cache_hits": sum(result["timings"]["memory_cache_hit"] for result in results),
            }
            self.assertFalse((self.directory / "history.json").exists())
        self.assertEqual(evidence, {
            "forced_fresh": {"requests": 10, "provider_calls": 10, "cache_hits": 0, "memory_cache_hits": 0},
            "memory_enabled": {"requests": 10, "provider_calls": 1, "cache_hits": 9, "memory_cache_hits": 9},
        })
        self.repeat_evidence = evidence

    def test_identity_change_during_key_generation_rechecks_before_hit_or_insert(self):
        self.run_translation()
        self.provider.text = "New runtime result."
        original_key = translation._memory_key
        changed = [False]

        def key(snapshot, identity):
            result = original_key(snapshot, identity)
            if not changed[0]:
                self.token = hashlib.sha256(b"runtime changed during key generation").digest()
                changed[0] = True
            return result

        with patch.object(translation, "_memory_key", side_effect=key):
            _, result = self.run_translation()
        self.assertFalse(result["cached"])
        self.assertEqual(result["text"], "New runtime result.")
        self.assertEqual(len(self.provider.requests), 2)
        self.session.perform({"operation": "config_save", "config": self.config})

        def changing_key(snapshot, identity):
            result = original_key(snapshot, identity)
            self.token = hashlib.sha256(self.token).digest()
            return result

        with patch.object(translation, "_memory_key", side_effect=changing_key):
            self.assertFalse(self.run_translation()[1]["cached"])
        self.assertEqual(self.session._memory_cache.count, 0)

    def test_forced_refresh_bypasses_and_invalidates_even_when_runtime_becomes_unavailable(self):
        self.run_translation()
        self.assertEqual(self.session._memory_cache.count, 1)
        self.eligible = False
        self.provider.text = "Fresh synthetic result."
        _, refreshed = self.run_translation(request(use_cache=False))
        self.assertEqual(self.session._memory_cache.count, 0)
        self.assertFalse(refreshed["cached"])
        self.eligible = True
        _, following = self.run_translation()
        self.assertFalse(following["cached"])
        self.assertEqual(following["text"], "Fresh synthetic result.")
        self.assertEqual(len(self.provider.requests), 3)

    def test_changed_runtime_model_effort_and_full_snapshot_contract_miss(self):
        self.run_translation()
        for token, info in (
                (hashlib.sha256(b"replacement resident").digest(), self.info),
                (self.token, ProviderModelInfo("synthetic", "synthetic-other", "low")),
                (self.token, ProviderModelInfo("synthetic", "synthetic-other", "high"))):
            self.token, self.info = token, info
            _, result = self.run_translation()
            self.assertFalse(result["cached"])
        snapshot = translation.snapshot_for_translation(Config(self.config), request())
        identity = (self.token, self.info)
        baseline = translation._memory_key(snapshot, identity)
        variations = (
            replace(snapshot, input=snapshot.input + " "),
            replace(snapshot, request=replace(snapshot.request, user_text=snapshot.request.user_text + "\n")),
            replace(snapshot, request=replace(snapshot.request, task="translation_summary")),
            replace(snapshot, request=replace(snapshot.request, system_prompt="Different fixed prompt")),
            replace(snapshot, sig=snapshot.sig + "-revision"),
            replace(snapshot, direction="to_ja"),
            replace(snapshot, app_language="zh_CN"),
            replace(snapshot, target_lang="ja"),
            replace(snapshot, summarize=True),
            replace(snapshot, stream_enabled=not snapshot.stream_enabled),
            replace(snapshot, config=dict(snapshot.config) | {"future_contract": True}),
            replace(snapshot, selection=replace(snapshot.selection, provider_id="claude_cli")),
        )
        for different in variations:
            self.assertNotEqual(translation._memory_key(different, identity), baseline)

    def test_successful_forced_refresh_replaces_stale_result_but_failure_cannot_restore_it(self):
        self.run_translation()
        self.provider.text = "Fresh synthetic result."
        self.assertFalse(self.run_translation(request(use_cache=False))[1]["cached"])
        _, cached = self.run_translation()
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["text"], "Fresh synthetic result.")
        self.assertEqual(len(self.provider.requests), 2)
        self.provider.result_code = "cancelled"
        self.assertEqual(self.run_translation(request(use_cache=False))[0], "cancelled")
        self.assertEqual(self.session._memory_cache.count, 0)
        self.provider.result_code = ""
        self.assertFalse(self.run_translation()[1]["cached"])

    def test_unknown_actual_or_effort_and_unverifiable_runtime_never_cache(self):
        for info in (ProviderModelInfo("synthetic"), ProviderModelInfo("synthetic", "synthetic-resolved"),
                     ProviderModelInfo("synthetic", reasoning_effort="low")):
            self.info = info
            for _ in range(2):
                _, result = self.run_translation()
                self.assertFalse(result["cached"])
            self.assertEqual(self.session._memory_cache.count, 0)
        self.info = ProviderModelInfo("synthetic", "synthetic-resolved", "low")
        self.eligible = False
        self.run_translation()
        self.assertEqual(self.session._memory_cache.count, 0)

    def test_settings_save_history_clear_and_close_remove_entries(self):
        for clear in (
                lambda: self.session.perform({"operation": "config_save", "config": self.config}),
                lambda: self.session.perform_history({"operation": "history_clear"}, "clear", 2)):
            self.run_translation()
            self.assertEqual(self.session._memory_cache.count, 1)
            clear()
            self.assertEqual(self.session._memory_cache.count, 0)
            _, result = self.run_translation()
            self.assertFalse(result["cached"])
        self.session.close()
        self.assertEqual(self.session._memory_cache.count, 0)
        self.assertTrue(self.session._memory_closing)

    def test_late_completion_after_clear_or_close_cannot_repopulate(self):
        for clear in (
                lambda: self.session.perform({"operation": "config_save", "config": self.config}),
                lambda: self.session.perform_history({"operation": "history_clear"}, "clear", 2),
                self.session.close):
            with patch.object(self.session, "_record", return_value=("disabled", None)):
                event, result = self.run_translation(finish=lambda: (clear(), True)[1])
            self.assertEqual(event, "completed")
            self.assertFalse(result["cached"])
            self.assertEqual(self.session._memory_cache.count, 0)

    def test_clear_during_blocked_provider_execution_keeps_late_result_out_of_memory(self):
        self.provider.release.clear()
        self.translate("blocked")
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.session.perform_history({"operation": "history_clear"}, "clear", 2)
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("blocked"))
        self.assertEqual(self.stdout.result("blocked")["type"], "completed")
        self.assertEqual(self.session._memory_cache.count, 0)
        self.assertFalse(self.run_translation()[1]["cached"])
        self.assertEqual(len(self.provider.requests), 2)

    def test_cancel_failure_cleanup_history_error_and_rejected_admission_do_not_store(self):
        for code in ("cancelled", "provider_failed", "provider_cleanup_failed"):
            self.provider.result_code = code
            if code == "cancelled":
                self.assertEqual(self.run_translation()[0], "cancelled")
            else:
                with self.assertRaises(translation.TranslationError):
                    self.run_translation()
            self.assertEqual(self.session._memory_cache.count, 0)
        self.provider.result_code = ""
        self.assertEqual(self.run_translation(finish=lambda: False)[0], "cancelled")
        with patch.object(self.session, "_record", return_value=("failed", "history_unavailable")):
            self.assertEqual(self.run_translation()[1]["history"], "failed")
        self.assertEqual(self.session._memory_cache.count, 0)
        with patch.object(self.provider, "stream", return_value=ProviderResult(True, " ", model_info=self.info)):
            with self.assertRaises(translation.TranslationError):
                self.run_translation()
        self.assertEqual(self.session._memory_cache.count, 0)

    def test_ttl_hits_do_not_extend_expiry_and_new_sessions_have_no_memory_entries(self):
        now = [10]
        self.session._memory_cache = TranslationMemoryCache(ttl_seconds=5, clock=lambda: now[0])
        self.run_translation()
        now[0] = 14
        self.assertTrue(self.run_translation()[1]["cached"])
        now[0] = 15
        self.assertFalse(self.run_translation()[1]["cached"])
        other = translation.TranslationSession(
            str(self.home), self.identity, str(self.home / "synthetic-cli"),
            {"HOME": str(self.home), "PATH": ""})
        self.assertEqual(other._memory_cache.count, 0)
        other.close()

    def test_ocr_and_actions_never_touch_memory_and_disk_history_behavior_is_preserved(self):
        self.run_translation()
        scope_calls = self.scope_calls
        with patch.object(self.session._memory_cache, "get", side_effect=AssertionError("memory read")), \
                patch.object(self.session._memory_cache, "put", side_effect=AssertionError("memory write")):
            for _ in range(2):
                self.assertFalse(self.run_translation(request(origin="ocr"))[1]["cached"])
            for action in translation.RESULT_ACTIONS:
                self.assertEqual(self.session.result_action(
                    action_request(action), threading.Event(), lambda _: None, lambda: True)[0], "completed")
        self.assertEqual(self.scope_calls, scope_calls)
        self.config[CFG.HISTORY_ENABLED] = True
        self.session.perform({"operation": "config_save", "config": self.config})
        self.run_translation()
        with patch.object(self.session._history, "find_cached", side_effect=AssertionError("memory hit read disk")):
            _, memory_hit = self.run_translation()
        self.assertEqual(memory_hit["timings"]["memory_cache_hit"], 1)
        self.eligible = False
        _, cached = self.run_translation()
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["timings"]["memory_cache_hit"], 0)
        self.assertEqual(cached["history"], "unchanged")
        self.assertEqual(len(self.history()), 1)

    def test_images_bypass_memory_even_when_an_eligible_text_result_exists(self):
        from cc_macos.image_fixture import PNG_BYTES
        if __package__:
            from .test_macos_image import request as image_request
        else:
            from test_macos_image import request as image_request

        self.run_translation()
        source = self.home / "cache-bypass.png"
        source.write_bytes(PNG_BYTES)
        scope_calls = self.scope_calls
        with patch.object(self.session._memory_cache, "get", side_effect=AssertionError("memory read")), \
                patch.object(self.session._memory_cache, "put", side_effect=AssertionError("memory write")):
            for _ in range(2):
                event, result = self.session.translate_image(
                    image_request(source, record_history=False), threading.Event(), lambda _: None, lambda: True)
                self.assertEqual((event, result["cached"]), ("completed", False))
        self.assertEqual(self.scope_calls, scope_calls)
        self.assertEqual(self.session._memory_cache.count, 1)
