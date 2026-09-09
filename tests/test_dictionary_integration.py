import unittest
from unittest import mock

from tests._tr import tr
import cc_app_results
from cc_dictionary import DEVELOPMENT_DICTIONARY_PATH, LocalDictionary
from cc_dictionary_cache import DictionaryAiCacheError
from cc_dictionary_metrics import DictionaryMetrics


class TestLocalDictionaryRouting(unittest.TestCase):
    def _app(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.Config(dict(tr.DEFAULT_CONFIG))
        app.cfg[tr.CFG.LOCAL_DICTIONARY_ENABLED] = True
        app._job_id = 0
        app._ss = tr.StreamSession()
        app._provider_cancel_event = None
        app._local_dictionary = LocalDictionary(DEVELOPMENT_DICTIONARY_PATH)
        app._dictionary_ai_cache = mock.Mock()
        app._dictionary_ai_cache.get.return_value = None
        app._dictionary_metrics = DictionaryMetrics()
        self.addCleanup(app._local_dictionary.close_thread)
        app.root = mock.Mock()
        app.popup = None
        app._destroy_popup = mock.Mock()
        app._cancel_stream_flush = mock.Mock()
        app._show_result = mock.Mock()
        app._start_ai_dictionary_supplement = mock.Mock()
        app._is_centered_layout = mock.Mock(return_value=True)
        return app

    def test_high_confidence_hit_short_circuits_provider(self):
        app = self._app()
        with mock.patch.object(
                tr, "find_cached_translation", return_value=None) as cached, \
                mock.patch.object(tr.threading, "Thread") as thread:
            app._show_loading("hello")
        thread.assert_not_called()
        app.root.after.assert_not_called()
        app._show_result.assert_called_once()
        args, kwargs = app._show_result.call_args
        self.assertTrue(args[0])
        self.assertIn("hello", args[1])
        self.assertIn("[[cc-instant]]", args[1])
        self.assertTrue(kwargs["record"])
        self.assertTrue(app._last_dictionary_local)
        self.assertIn("local-dictionary", cached.call_args_list[0].args[2])
        app._start_ai_dictionary_supplement.assert_called_once_with(
            "hello", 1, None, args[1])
        self.assertEqual(
            app._dictionary_metrics.snapshot()["outcomes"]["hit"], 1)

    def test_local_cache_uses_versioned_local_signature(self):
        app = self._app()
        with mock.patch.object(
                tr, "find_cached_translation",
                return_value="LOCAL CACHED") as cached, \
                mock.patch.object(tr.threading, "Thread") as thread:
            app._dictionary_ai_cache.get.return_value = "AI CACHED"
            app._show_loading("hello")
        thread.assert_not_called()
        app._show_result.assert_called_once_with(
            True, "LOCAL CACHED", 1, record=False)
        signature = cached.call_args_list[0].args[2]
        self.assertIn("query-v1", signature)
        self.assertIn("format-v8", signature)
        self.assertIn(app._local_dictionary.status.data_version, signature)
        app._start_ai_dictionary_supplement.assert_called_once_with(
            "hello", 1, "AI CACHED", "LOCAL CACHED")

    def test_miss_falls_back_to_existing_provider_worker(self):
        app = self._app()
        with mock.patch.object(
                tr, "find_cached_translation", return_value=None), \
                mock.patch.object(tr.threading, "Thread") as thread:
            app._show_loading("notawordzzzz")
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()
        app._show_result.assert_not_called()
        self.assertFalse(app._last_dictionary_local)
        self.assertEqual(
            app._dictionary_metrics.snapshot()["outcomes"]["miss"], 1)

    def test_weak_match_falls_back_to_existing_provider_worker(self):
        app = self._app()
        weak = mock.Mock(is_high_confidence=False)
        app._local_dictionary.lookup = mock.Mock(return_value=weak)
        with mock.patch.object(
                tr, "find_cached_translation", return_value=None), \
                mock.patch.object(tr.threading, "Thread") as thread:
            app._show_loading("hello")
        thread.assert_called_once()
        app._show_result.assert_not_called()

    def test_database_error_logs_and_falls_back(self):
        app = self._app()
        app._local_dictionary.lookup = mock.Mock(
            side_effect=RuntimeError("broken database"))
        with mock.patch.object(
                tr, "find_cached_translation", return_value=None), \
                mock.patch.object(tr, "log_error") as log_error, \
                mock.patch.object(tr.threading, "Thread") as thread:
            app._show_loading("hello")
        log_error.assert_called_once()
        thread.assert_called_once()
        app._show_result.assert_not_called()

    def test_disabled_setting_skips_local_lookup(self):
        app = self._app()
        app.cfg[tr.CFG.LOCAL_DICTIONARY_ENABLED] = False
        app._local_dictionary.lookup = mock.Mock()
        with mock.patch.object(
                tr, "find_cached_translation", return_value=None), \
                mock.patch.object(tr.threading, "Thread") as thread:
            app._show_loading("hello")
        app._local_dictionary.lookup.assert_not_called()
        thread.assert_called_once()
        self.assertEqual(
            app._dictionary_metrics.snapshot()["outcomes"]["disabled"], 1)

    def test_forced_ai_bypasses_local_and_cache(self):
        app = self._app()
        app._local_dictionary.lookup = mock.Mock()
        with mock.patch.object(tr, "find_cached_translation") as cached, \
                mock.patch.object(tr.threading, "Thread") as thread:
            app._show_loading(
                "hello", use_cache=False, force_ai=True)
        app._local_dictionary.lookup.assert_not_called()
        cached.assert_not_called()
        thread.assert_called_once()
        self.assertFalse(app._last_dictionary_local)

    def test_ai_action_requests_uncached_forced_ai(self):
        app = self._app()
        app._last_input = "hello"
        app._last_origin = "text"
        app._show_loading = mock.Mock()
        app._query_dictionary_with_ai()
        app._show_loading.assert_called_once_with(
            "hello", origin="text", use_cache=False, force_ai=True)

    def test_cached_ai_supplement_replaces_pending_section_in_place(self):
        app = self._app()
        app._job_id = 3
        app.popup = mock.Mock()
        app.popup._text._rich = True
        app._current_popup_text = mock.Mock(return_value="LOCAL")
        app._set_popup_text = mock.Mock()
        app._remember_result = mock.Mock()
        app._result_title = mock.Mock(return_value="Dictionary")
        tr.TranslatorApp._start_ai_dictionary_supplement(
            app, "hello", 3, "AI DETAIL", "## LOCAL [[cc-instant]]")
        calls = app._set_popup_text.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertIn("LOCAL", calls[0].args[0])
        self.assertIn("[[cc-instant]]", calls[0].args[0])
        self.assertIn("AI DETAIL", calls[0].args[0])
        self.assertNotIn(tr.i18n.get("result.ai_supplement_loading"),
                         calls[0].args[0])

    def test_uncached_ai_supplement_starts_background_provider(self):
        app = self._app()
        app._job_id = 4
        app.popup = mock.Mock()
        app.popup._text._rich = False
        app._current_popup_text = mock.Mock(return_value="LOCAL")
        app._set_popup_text = mock.Mock()
        app._remember_result = mock.Mock()
        app._result_title = mock.Mock(return_value="Dictionary")
        app._provider_selection = mock.Mock(return_value="provider")
        app._ai_dictionary_supplement_signature = mock.Mock(
            return_value="supplement-sig")
        with mock.patch.object(tr.threading, "Thread") as thread:
            tr.TranslatorApp._start_ai_dictionary_supplement(
                app, "hello", 4, base_result="## LOCAL [[cc-instant]]")
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs["daemon"])
        thread.return_value.start.assert_called_once()

    def test_supplement_worker_uses_local_context_and_hidden_cache(self):
        app = self._app()
        app._job_id = 6
        app._call_model = mock.Mock(return_value=(True, "NEW DETAIL"))
        app._store_ai_dictionary_supplement = mock.Mock()
        app.root.after = mock.Mock()
        expected_win = mock.Mock()
        cancel_event = mock.Mock()
        cancel_event.is_set.return_value = False
        tr.TranslatorApp._do_ai_dictionary_supplement(
            app, "run", 6, expected_win, "## run\n- 跑",
            "provider", cancel_event, "supplement-sig")
        payload, prompt = app._call_model.call_args.args[:2]
        self.assertIn("<query>run</query>", payload)
        self.assertIn("## run", payload)
        self.assertEqual(prompt, tr.DICTIONARY_SUPPLEMENT_PROMPT)
        app._store_ai_dictionary_supplement.assert_called_once_with(
            "run", "supplement-sig", "NEW DETAIL")
        app.root.after.assert_called_once()

    def test_hidden_supplement_cache_errors_do_not_block_local_result(self):
        app = self._app()
        app._dictionary_ai_cache.get.side_effect = (
            DictionaryAiCacheError("broken"))
        with mock.patch.object(cc_app_results, "log_error") as log_error:
            self.assertIsNone(app._get_ai_dictionary_supplement("run"))
        log_error.assert_called_once()

    def test_supplement_cache_signature_tracks_provider_model_and_prompt(self):
        app = self._app()
        app._provider_selection = mock.Mock()
        app._provider_selection.return_value = mock.Mock(
            provider_id="claude", model="sonnet")
        baseline = app._ai_dictionary_supplement_signature()
        app._provider_selection.return_value = mock.Mock(
            provider_id="claude", model="opus")
        self.assertNotEqual(
            baseline, app._ai_dictionary_supplement_signature())
        with mock.patch.object(
                cc_app_results, "DICTIONARY_SUPPLEMENT_REVISION", "next"):
            self.assertNotEqual(
                baseline, app._ai_dictionary_supplement_signature())

    def test_stale_ai_supplement_cannot_modify_newer_result(self):
        app = self._app()
        app._job_id = 8
        app.popup = mock.Mock()
        app._set_popup_text = mock.Mock()
        app._remember_result = mock.Mock()
        tr.TranslatorApp._apply_ai_dictionary_supplement(
            app, True, "OLD AI", 7, app.popup, "OLD LOCAL")
        app._set_popup_text.assert_not_called()
        app._remember_result.assert_not_called()

    def test_ai_supplement_failure_keeps_local_result(self):
        app = self._app()
        app._job_id = 5
        app.popup = mock.Mock()
        app.popup._text._rich = False
        app._set_popup_text = mock.Mock()
        app._remember_result = mock.Mock()
        app._result_title = mock.Mock(return_value="Dictionary")
        tr.TranslatorApp._apply_ai_dictionary_supplement(
            app, False, "", 5, app.popup, "LOCAL")
        combined = app._set_popup_text.call_args.args[0]
        self.assertEqual(combined, "LOCAL")
        self.assertNotIn(
            tr.i18n.get("result.ai_supplement_failed"), combined)

    def test_expanding_senses_preserves_ai_supplement(self):
        app = self._app()
        app._last_local_dictionary_result = app._local_dictionary.lookup("hello")
        app.popup = mock.Mock()
        app.popup._text = mock.Mock()
        app.popup._dictionary_ai_supplement = "AI DETAIL"
        app.popup._dictionary_supplement_pending = False
        app._set_popup_text = mock.Mock()
        app._remember_result = mock.Mock()
        app._result_title = mock.Mock(return_value="Dictionary")

        tr.TranslatorApp._expand_local_dictionary_senses(app)

        combined = app._set_popup_text.call_args.args[0]
        self.assertIn("## hello", combined)
        self.assertIn("AI DETAIL", combined)
        self.assertEqual(
            app.popup._dictionary_base_result,
            cc_app_results.format_dictionary_result(
                app._last_local_dictionary_result, expanded=True))

    def test_local_and_ai_cache_signatures_are_distinct(self):
        app = self._app()
        app._last_dictionary_local = True
        local = app._cache_signature()
        app._last_dictionary_local = False
        ai = app._cache_signature()
        self.assertNotEqual(local, ai)
        self.assertTrue(local.startswith("local-dictionary|"))

    def test_local_result_records_dictionary_history_with_local_signature(self):
        app = self._app()
        app._job_id = 4
        app._last_input = "hello"
        app._last_origin = "text"
        app._last_class = "text"
        app._last_dictionary_local = True
        app._last_result_ok = None
        app._last_result_title = ""
        app._last_result_text = ""
        app._cycle_anchor = None
        app._stop_animation = mock.Mock()
        app._remember_result = mock.Mock()
        app._make_popup = mock.Mock(return_value=mock.Mock())
        app._maybe_add_explain_button = mock.Mock()
        app._maybe_add_as_text_button = mock.Mock()
        app._maybe_add_ai_dictionary_button = mock.Mock()
        app._maybe_add_result_actions_button = mock.Mock()
        with mock.patch.object(tr, "add_history") as add_history:
            tr.TranslatorApp._show_result(
                app, True, "LOCAL", job_id=4, record=True)
        add_history.assert_called_once()
        args = add_history.call_args.args
        kwargs = add_history.call_args.kwargs
        self.assertEqual(kwargs["kind"], "dict")
        self.assertTrue(args[2])
        self.assertTrue(kwargs["sig"].startswith("local-dictionary|"))


if __name__ == "__main__":
    unittest.main()
