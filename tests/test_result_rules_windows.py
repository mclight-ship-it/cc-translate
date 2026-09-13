"""Windows consumers and differential contracts against the frozen UI rules."""

from types import SimpleNamespace
import unittest
from unittest import mock

import cc_app_results
import cc_result_rules as rules
from tests._tr import tr
from tests import test_full as fixtures
from tests.test_result_rules import HISTORY_CASES, bind_legacy


legacy_history_kind, legacy_cache_signature = bind_legacy(tr.__dict__)
_MISSING = object()


def bare_app():
    app = object.__new__(tr.TranslatorApp)
    app.cfg = {
        tr.CFG.MODEL_PROVIDER: "claude_cli",
        tr.CFG.MODEL: "haiku",
        tr.CFG.DIRECTION: "auto",
        tr.CFG.SUMMARY_ENABLED: False,
        tr.CFG.LANGUAGE: "zh",
    }
    app._last_origin = "text"
    app._last_class = "text"
    app._last_input = "hello world"
    return app


class ResultRulesTestCase(unittest.TestCase):
    def assert_signature(self, app, expected, route=None):
        self.assertEqual(legacy_cache_signature(app, route).encode("utf-8"), expected)
        self.assertEqual(app._cache_signature(route).encode("utf-8"), expected)

    def assert_same_error(self, old, new, exception_type):
        with self.assertRaises(exception_type) as before:
            old()
        with self.assertRaises(exception_type) as after:
            new()
        self.assertEqual(str(after.exception), str(before.exception))


class TestWindowsResultRuleExports(ResultRulesTestCase):
    def test_exports_are_core_function_objects(self):
        for name in ("history_kind", "local_cache_signature", "provider_cache_signature"):
            with self.subTest(name=name):
                self.assertIs(getattr(tr, name), getattr(rules, name))

    def test_cache_wrappers_invoke_exported_core_with_resolved_fields(self):
        app = bare_app()
        app._provider_selection = mock.Mock(
            return_value=SimpleNamespace(provider_id="codex_cli", model=None))
        app.cfg = {tr.CFG.DIRECTION: None, tr.CFG.SUMMARY_ENABLED: "false"}
        with mock.patch.object(tr, "provider_cache_signature", wraps=rules.provider_cache_signature) as provider, \
                mock.patch.object(tr, "local_cache_signature", wraps=rules.local_cache_signature) as local, \
                mock.patch.object(tr.i18n, "get_language", return_value="en_US"):
            self.assertEqual(
                app._cache_signature().encode(),
                b"codex_cli|auto|None|sum1|en_US|codex-format-v5")
            provider.assert_called_once_with(
                "codex_cli", "auto", "None", True, "en_US", "codex-format-v5")
            local.assert_not_called()
            app._local_dictionary = SimpleNamespace(cache_version="query:test")
            with mock.patch.object(tr, "FORMATTER_VERSION", "format:test"):
                self.assertEqual(
                    app._cache_signature("local").encode(),
                    b"local-dictionary|query:test|format:test")
            local.assert_called_once_with("query:test", "format:test")
            self.assertEqual(provider.call_count, 1)
            app._provider_selection.assert_called_once_with()

    def test_history_wrapper_invokes_exported_core_in_every_branch(self):
        app = bare_app()
        for origin, content_class, text, expected_args, expected in (
                ("ocr", "code", "hello", ("ocr", None, None), "ocr"),
                ("text", "code", "hello", ("text", "code", None), "code"),
                ("text", "text", "hello", ("text", "text", "hello"), "dict"),
                ("text", "mixed", "", ("text", "mixed", ""), "text")):
            with self.subTest(origin=origin, content_class=content_class, text=text):
                app._last_origin, app._last_class, app._last_input = origin, content_class, text
                with mock.patch.object(tr, "history_kind", wraps=rules.history_kind) as core:
                    self.assertEqual(app._history_kind(), expected)
                kwargs = {} if expected in ("ocr", "code") else {"word_test": tr.is_single_word}
                core.assert_called_once_with(*expected_args, **kwargs)


class TestWindowsCacheRuleDifferential(ResultRulesTestCase):
    def test_real_provider_selection_and_missing_config_fields(self):
        for cfg, expected in (
                ({}, b"codex_cli|auto-fast|auto|sum1|fallback|codex-format-v5"),
                ({tr.CFG.MODEL_PROVIDER: "claude_cli"},
                 b"claude_cli|haiku|auto|sum1|fallback"),
                ({tr.CFG.MODEL_PROVIDER: "claude_cli", tr.CFG.MODEL: "opus"},
                 b"claude_cli|opus|auto|sum1|fallback"),
                ({tr.CFG.MODEL_PROVIDER: "claude_cli", tr.CFG.MODEL: "opus",
                  tr.CFG.CLAUDE_MODEL: "sonnet"},
                 b"claude_cli|sonnet|auto|sum1|fallback"),
                ({tr.CFG.MODEL_PROVIDER: "codex_cli", tr.CFG.CODEX_MODEL: "gpt-5.4"},
                 b"codex_cli|gpt-5.4|auto|sum1|fallback|codex-format-v5"),
                ({tr.CFG.MODEL_PROVIDER: "unknown", tr.CFG.MODEL: "custom"},
                 b"unknown|custom|auto|sum1|fallback")):
            with self.subTest(cfg=cfg), \
                    mock.patch.object(tr.i18n, "get_language", return_value="fallback"):
                app = bare_app()
                app.cfg = cfg
                self.assert_signature(app, expected)

    def test_route_and_last_local_truth_table(self):
        for route in (_MISSING, None, "local", "ai", "", "unknown"):
            for last_local in (_MISSING, False, True, None, "", "yes"):
                with self.subTest(route=route, last_local=last_local):
                    app = bare_app()
                    if last_local is not _MISSING:
                        app._last_dictionary_local = last_local
                    app._local_dictionary = SimpleNamespace(cache_version="q:data")
                    app._provider_selection = mock.Mock(
                        return_value=SimpleNamespace(provider_id="claude_cli", model="haiku"))
                    local = route == "local" or (
                        route in (_MISSING, None) and last_local is not _MISSING and bool(last_local))
                    expected = (b"local-dictionary|q:data|f:test" if local
                                else b"claude_cli|haiku|auto|sum0|zh")
                    args = () if route is _MISSING else (route,)
                    with mock.patch.object(tr, "FORMATTER_VERSION", "f:test"):
                        self.assertEqual(legacy_cache_signature(app, *args).encode(), expected)
                        self.assertEqual(app._cache_signature(*args).encode(), expected)
                    self.assertEqual(app._provider_selection.call_count, 0 if local else 2)

    def test_local_does_not_read_config_provider_i18n_or_revisions(self):
        class LocalOnly:
            _last_dictionary_local = True
            _local_dictionary = SimpleNamespace(cache_version="q:local")

            def __getattr__(self, name):
                raise AssertionError("unexpected state: " + name)

        for route in (None, "local"):
            with self.subTest(route=route), \
                    mock.patch.object(tr.i18n, "get_language", side_effect=AssertionError("i18n")) as language, \
                    mock.patch.object(tr, "PROVIDER_PROMPT_REVISIONS") as revisions, \
                    mock.patch.object(tr, "FORMATTER_VERSION", "injected"):
                for method in (legacy_cache_signature, tr.TranslatorApp._cache_signature):
                    self.assertEqual(
                        method(LocalOnly(), route).encode(),
                        b"local-dictionary|q:local|injected")
                language.assert_not_called()
                revisions.get.assert_not_called()

    def test_explicit_route_does_not_read_last_local_flag(self):
        class ExplicitRoute:
            cfg = bare_app().cfg
            _local_dictionary = None

            @property
            def _last_dictionary_local(self):
                raise AssertionError("explicit route read last-local flag")

            def _provider_selection(self):
                return SimpleNamespace(provider_id="claude_cli", model="haiku")

        for route, expected in (
                ("local", b"local-dictionary|unavailable|f"),
                ("ai", b"claude_cli|haiku|auto|sum0|zh")):
            with self.subTest(route=route), mock.patch.object(tr, "FORMATTER_VERSION", "f"):
                for method in (legacy_cache_signature, tr.TranslatorApp._cache_signature):
                    self.assertEqual(method(ExplicitRoute(), route).encode(), expected)

    def test_missing_none_and_unavailable_dictionary_keep_exact_bytes(self):
        for dictionary in (_MISSING, None, SimpleNamespace(cache_version="unavailable")):
            with self.subTest(dictionary=dictionary), mock.patch.object(tr, "FORMATTER_VERSION", "f"):
                app = bare_app()
                if dictionary is not _MISSING:
                    app._local_dictionary = dictionary
                self.assert_signature(app, b"local-dictionary|unavailable|f", "local")

    def test_dictionary_attribute_and_version_errors_are_not_swallowed(self):
        class BrokenDictionary:
            @property
            def cache_version(self):
                raise RuntimeError("dictionary version failure")

        for dictionary, error in (
                (SimpleNamespace(), AttributeError),
                (BrokenDictionary(), RuntimeError),
                (SimpleNamespace(cache_version=None), TypeError),
                (SimpleNamespace(cache_version=3), TypeError)):
            with self.subTest(dictionary=dictionary):
                app = bare_app()
                app._local_dictionary = dictionary
                self.assert_same_error(
                    lambda: legacy_cache_signature(app, "local"),
                    lambda: app._cache_signature("local"), error)

    def test_formatter_injection_keeps_join_contract(self):
        app = bare_app()
        app._local_dictionary = SimpleNamespace(cache_version="q|data")
        for formatter, expected in (
                ("", b"local-dictionary|q|data|"),
                ("f\nnext", b"local-dictionary|q|data|f\nnext")):
            with self.subTest(formatter=formatter), \
                    mock.patch.object(tr, "FORMATTER_VERSION", formatter):
                self.assert_signature(app, expected, "local")
        for formatter in (None, 4):
            with self.subTest(formatter=formatter), \
                    mock.patch.object(tr, "FORMATTER_VERSION", formatter):
                self.assert_same_error(
                    lambda: legacy_cache_signature(app, "local"),
                    lambda: app._cache_signature("local"), TypeError)

    def test_auto_none_empty_and_summary_truthiness_keep_field_bytes(self):
        for model, direction, summary, language, expected in (
                (None, _MISSING, _MISSING, _MISSING, b"claude_cli|auto|auto|sum1|fallback"),
                ("", "", False, "", b"claude_cli|auto||sum0|fallback"),
                (False, None, None, None, b"claude_cli|auto|None|sum0|fallback"),
                (0, 7, [], 0, b"claude_cli|auto|7|sum0|fallback"),
                ("auto", "auto", "false", "en", b"claude_cli|auto|auto|sum1|en"),
                ("auto-fast", "to_ja", [0], " ", b"claude_cli|auto-fast|to_ja|sum1| "),
                (42, "x|y", {}, "zh", b"claude_cli|42|x|y|sum0|zh"),
                ("m|n", "to_zh", {"enabled": False}, "en|US",
                 b"claude_cli|m|n|to_zh|sum1|en|US")):
            with self.subTest(model=model, direction=direction, summary=summary, language=language):
                app = bare_app()
                app._provider_selection = mock.Mock(
                    return_value=SimpleNamespace(provider_id="claude_cli", model=model))
                app.cfg = {key: value for key, value in (
                    (tr.CFG.DIRECTION, direction),
                    (tr.CFG.SUMMARY_ENABLED, summary),
                    (tr.CFG.LANGUAGE, language)) if value is not _MISSING}
                with mock.patch.object(tr.i18n, "get_language", return_value="fallback") as fallback:
                    self.assert_signature(app, expected)
                self.assertEqual(fallback.call_count, 2 if language is _MISSING or not language else 0)

    def test_i18n_fallback_result_is_stringified_without_another_default(self):
        app = bare_app()
        app.cfg[tr.CFG.LANGUAGE] = None
        for fallback, expected in (
                (None, b"claude_cli|haiku|auto|sum0|None"),
                ("", b"claude_cli|haiku|auto|sum0|"),
                (7, b"claude_cli|haiku|auto|sum0|7")):
            with self.subTest(fallback=fallback), \
                    mock.patch.object(tr.i18n, "get_language", return_value=fallback):
                self.assert_signature(app, expected)

    def test_model_stringification_is_not_repeated_or_defaulted_again(self):
        class Rendered(str):
            def __str__(self):
                raise RuntimeError("model stringified twice")

        class Model:
            def __init__(self, rendered):
                self.rendered = rendered

            def __str__(self):
                return self.rendered

        for rendered, expected in (
                ("", b"claude_cli||auto|sum0|zh"),
                (Rendered("rendered"), b"claude_cli|rendered|auto|sum0|zh")):
            with self.subTest(rendered=repr(rendered)):
                app = bare_app()
                app._provider_selection = mock.Mock(
                    return_value=SimpleNamespace(provider_id="claude_cli", model=Model(rendered)))
                self.assert_signature(app, expected)

    def test_direction_and_language_stringification_is_not_repeated(self):
        class Rendered(str):
            def __str__(self):
                raise RuntimeError("field stringified twice")

        class Field:
            def __str__(self):
                return Rendered("rendered")

        for field, expected in (
                ("direction", b"claude_cli|haiku|rendered|sum0|zh"),
                ("language", b"claude_cli|haiku|auto|sum0|rendered"),
                ("fallback", b"claude_cli|haiku|auto|sum0|rendered")):
            with self.subTest(field=field):
                app = bare_app()
                if field == "direction":
                    app.cfg[tr.CFG.DIRECTION] = Field()
                else:
                    app.cfg[tr.CFG.LANGUAGE] = None if field == "fallback" else Field()
                with mock.patch.object(tr.i18n, "get_language", return_value=Field()) as fallback:
                    self.assert_signature(app, expected)
                self.assertEqual(fallback.call_count, 2 if field == "fallback" else 0)

    def test_prompt_revision_presence_and_invalid_types(self):
        app = bare_app()
        for revisions, suffix in (
                ({}, b""), ({"claude_cli": ""}, b""), ({"claude_cli": None}, b""),
                ({"claude_cli": False}, b""), ({"claude_cli": []}, b""),
                ({"claude_cli": "r|next"}, b"|r|next")):
            with self.subTest(revisions=revisions), \
                    mock.patch.object(tr, "PROVIDER_PROMPT_REVISIONS", revisions):
                self.assert_signature(app, b"claude_cli|haiku|auto|sum0|zh" + suffix)
        for revision in (True, 3, ["revision"]):
            with self.subTest(revision=revision), \
                    mock.patch.object(tr, "PROVIDER_PROMPT_REVISIONS", {"claude_cli": revision}):
                self.assert_same_error(
                    lambda: legacy_cache_signature(app),
                    app._cache_signature, TypeError)

    def test_invalid_provider_id_is_not_coerced(self):
        for provider in (None, 7, []):
            with self.subTest(provider=provider):
                app = bare_app()
                app._provider_selection = mock.Mock(
                    return_value=SimpleNamespace(provider_id=provider, model="m"))
                self.assert_same_error(
                    lambda: legacy_cache_signature(app), app._cache_signature, TypeError)

    def trace_signature(self, method, *, fallback=False, fail_at=None):
        events = []

        def step(name):
            events.append(name)
            if name == fail_at:
                raise RuntimeError("stopped at " + name)

        class Value:
            def __init__(self, name, truth=True):
                self.name, self.truth = name, truth

            def __bool__(self):
                step(self.name + ".bool")
                return self.truth

            def __str__(self):
                step(self.name + ".str")
                return self.name

        class Selection:
            @property
            def provider_id(self):
                step("selection.provider")
                return "claude_cli"

            @property
            def model(self):
                step("selection.model")
                return Value("model")

        class Config(dict):
            def get(self, key, default=None):
                step("cfg.get." + key)
                return super().get(key, default)

        class Defaults(dict):
            def __getitem__(self, key):
                step("defaults." + key)
                return super().__getitem__(key)

        class Revision(str):
            def __bool__(self):
                step("revision.bool")
                return True

        app = bare_app()
        app.cfg = Config({
            tr.CFG.DIRECTION: Value("direction"),
            tr.CFG.SUMMARY_ENABLED: Value("summary"),
            tr.CFG.LANGUAGE: Value("language", not fallback),
        })

        def selection():
            step("selection")
            return Selection()

        def language():
            step("i18n.get_language")
            return Value("fallback")

        def revision(provider, default):
            self.assertEqual((provider, default), ("claude_cli", ""))
            step("revisions.get")
            return Revision("revision")

        app._provider_selection = selection
        with mock.patch.object(tr, "DEFAULT_CONFIG", Defaults(tr.DEFAULT_CONFIG)), \
                mock.patch.object(tr.i18n, "get_language", side_effect=language), \
                mock.patch.object(tr, "PROVIDER_PROMPT_REVISIONS", SimpleNamespace(get=revision)):
            try:
                result = method(app)
            except RuntimeError as exc:
                result = (type(exc), str(exc))
        return result, events

    def test_config_coercion_and_revision_evaluation_order(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                old = self.trace_signature(legacy_cache_signature, fallback=fallback)
                new = self.trace_signature(tr.TranslatorApp._cache_signature, fallback=fallback)
                expected_events = [
                    "selection", "selection.provider", "selection.model", "model.bool", "model.str",
                    "cfg.get." + tr.CFG.DIRECTION, "direction.str",
                    "defaults." + tr.CFG.SUMMARY_ENABLED,
                    "cfg.get." + tr.CFG.SUMMARY_ENABLED, "summary.bool",
                    "cfg.get." + tr.CFG.LANGUAGE, "language.bool",
                    *(["i18n.get_language", "fallback.str"] if fallback else ["language.str"]),
                    "selection.provider", "revisions.get", "revision.bool",
                ]
                self.assertEqual(old[1], expected_events)
                self.assertEqual(new, old)
                self.assertEqual(new[0].encode(), (
                    b"claude_cli|model|direction|sum1|" +
                    (b"fallback" if fallback else b"language") + b"|revision"))

    def test_errors_stop_before_lower_priority_config_and_revision_reads(self):
        for fallback in (False, True):
            _, events = self.trace_signature(legacy_cache_signature, fallback=fallback)
            for index, event in enumerate(events):
                if event in events[:index]:
                    continue
                with self.subTest(fallback=fallback, event=event):
                    old = self.trace_signature(
                        legacy_cache_signature, fallback=fallback, fail_at=event)
                    new = self.trace_signature(
                        tr.TranslatorApp._cache_signature, fallback=fallback, fail_at=event)
                    self.assertEqual(old[0], (RuntimeError, "stopped at " + event))
                    self.assertEqual(old[1], events[:index + 1])
                    self.assertEqual(new, old)


class TestWindowsHistoryRuleDifferential(ResultRulesTestCase):
    def test_history_priority_and_word_matrix(self):
        app = bare_app()
        for (origin, content_class, text), expected in HISTORY_CASES:
            with self.subTest(origin=origin, content_class=content_class, text=text):
                app._last_origin, app._last_class, app._last_input = origin, content_class, text
                self.assertEqual(legacy_history_kind(app), expected)
                self.assertEqual(app._history_kind(), expected)

    def test_windows_predicate_replacement_reaches_core(self):
        app = bare_app()
        for answer, expected in ((True, "dict"), (False, "text")):
            with self.subTest(answer=answer), \
                    mock.patch.object(tr, "is_single_word", return_value=answer) as word_test, \
                    mock.patch.object(tr, "history_kind", wraps=rules.history_kind) as core:
                self.assertEqual(legacy_history_kind(app), expected)
                self.assertEqual(app._history_kind(), expected)
                core.assert_called_once_with(
                    "text", "text", "hello world", word_test=word_test)
                self.assertEqual(word_test.call_args_list, [mock.call("hello world")] * 2)

    def test_predicate_failure_propagates_but_empty_text_is_lazy(self):
        app = bare_app()
        with mock.patch.object(tr, "is_single_word", side_effect=RuntimeError("predicate failure")) as word_test:
            self.assert_same_error(
                lambda: legacy_history_kind(app), app._history_kind, RuntimeError)
            word_test.reset_mock()
            for text in ("", None):
                app._last_input = text
                self.assertEqual(legacy_history_kind(app), "text")
                self.assertEqual(app._history_kind(), "text")
            word_test.assert_not_called()

    def test_ocr_and_code_never_access_lower_priority_state_or_predicate(self):
        class State:
            def __init__(self, values, events):
                self.values, self.events = values, events

            def __getattr__(self, name):
                self.events.append(name)
                if name not in self.values:
                    raise AssertionError("forbidden state: " + name)
                return self.values[name]

        for values, expected in (
                ({"_last_origin": "ocr"}, "ocr"),
                ({"_last_origin": "text", "_last_class": "code"}, "code")):
            with self.subTest(values=values), \
                    mock.patch.object(tr, "is_single_word", side_effect=AssertionError("predicate")) as word_test:
                for method in (legacy_history_kind, tr.TranslatorApp._history_kind):
                    events = []
                    self.assertEqual(method(State(values, events)), expected)
                    self.assertEqual(events, list(values))
                word_test.assert_not_called()


class TestWindowsResultRuleConsumers(ResultRulesTestCase):
    def result_app(self):
        return fixtures.TestShowResultRecordFlag()._app()

    def test_real_history_meta_is_an_independent_job_snapshot(self):
        prose = "The quick brown fox jumps over the lazy dog. " * 20
        for provider in ("claude_cli", "codex_cli"):
            for origin, content_class, text, local, kind, summarize in (
                    ("ocr", "code", "hello", False, "ocr", False),
                    ("text", "code", "print(x)", False, "code", False),
                    ("text", "text", "hello", True, "dict", False),
                    ("text", "text", "hello", False, "dict", False),
                    ("text", "text", "A short sentence.", False, "text", False),
                    ("text", "text", prose, False, "text", True),
                    ("text", "mixed", prose, False, "text", True)):
                with self.subTest(provider=provider, kind=kind, local=local, summarize=summarize):
                    self.assertIs(tr.threading.current_thread(), tr.threading.main_thread())
                    app = bare_app()
                    app.cfg.update({
                        tr.CFG.MODEL_PROVIDER: provider, tr.CFG.CODEX_MODEL: "auto-fast",
                        tr.CFG.SUMMARY_ENABLED: True,
                    })
                    app._last_origin, app._last_class, app._last_input = origin, content_class, text
                    app._last_dictionary_local = local
                    app._local_dictionary = SimpleNamespace(cache_version="query:original")
                    app._provider_cancel_event = tr.threading.Event()
                    with mock.patch.object(tr, "FORMATTER_VERSION", "format:original"), \
                            mock.patch.object(tr, "history_kind", wraps=rules.history_kind) as history, \
                            mock.patch.object(tr, "local_cache_signature", wraps=rules.local_cache_signature) as local_core, \
                            mock.patch.object(tr, "provider_cache_signature", wraps=rules.provider_cache_signature) as provider_core:
                        meta = app._history_meta()
                        expected = {
                            "input": text, "origin": origin, "is_code": content_class == "code",
                            "kind": kind, "sig": legacy_cache_signature(app),
                            "provider": provider, "model": "haiku" if provider == "claude_cli" else "auto-fast",
                            "direction": "auto", "summarize": summarize,
                            "task": "translation_summary" if summarize else "text",
                            "system_prompt": (
                                tr.codex_summary_instruction(tr.resolve_target_lang("auto", "zh", text))
                                if provider == "codex_cli" and summarize else app._system_prompt_for(text)),
                            "cancel_event": app._provider_cancel_event,
                        }
                        self.assertEqual(meta, expected)
                        self.assertEqual(meta["kind"], legacy_history_kind(app))
                        self.assertEqual(history.call_count, 1)
                        self.assertEqual(local_core.call_count, int(local))
                        self.assertEqual(provider_core.call_count, int(not local))
                    expected_sig = (
                        b"local-dictionary|query:original|format:original" if local else
                        b"claude_cli|haiku|auto|sum1|zh" if provider == "claude_cli" else
                        b"codex_cli|auto-fast|auto|sum1|zh|codex-format-v5")
                    self.assertEqual(meta["sig"].encode(), expected_sig)
                    app.cfg.update({
                        tr.CFG.MODEL_PROVIDER: "unknown", tr.CFG.MODEL: "changed",
                        tr.CFG.CODEX_MODEL: "changed", tr.CFG.DIRECTION: "to_en",
                        tr.CFG.SUMMARY_ENABLED: False, tr.CFG.LANGUAGE: "en",
                    })
                    app._last_input, app._last_origin, app._last_class = "changed input.", "ocr", "code"
                    app._last_dictionary_local = not local
                    app._local_dictionary.cache_version = "query:changed"
                    app._provider_cancel_event = tr.threading.Event()
                    self.assertEqual(meta, expected)
                    self.assertNotEqual(app._cache_signature(), meta["sig"])
                    self.assertNotEqual(app._provider_selection().provider_id, meta["provider"])
                    self.assertIsNot(meta["cancel_event"], app._provider_cancel_event)

    def test_loading_passes_real_main_thread_snapshot_to_worker(self):
        app = fixtures.TestCacheShortCircuit()._app()
        app.cfg.update({
            tr.CFG.MODEL_PROVIDER: "claude_cli", tr.CFG.MODEL: "opus",
        })
        with mock.patch.object(tr, "find_cached_translation", return_value=None), \
                mock.patch.object(tr.threading, "Thread") as thread, \
                mock.patch.object(tr, "history_kind", wraps=rules.history_kind) as history, \
                mock.patch.object(tr, "provider_cache_signature", wraps=rules.provider_cache_signature) as signature:
            app._show_loading("A complete sentence.")
        worker_text, job_id, meta = thread.call_args.kwargs["args"]
        self.assertEqual(worker_text, "A complete sentence.")
        self.assertEqual(job_id, app._job_id)
        self.assertEqual(meta["kind"], "text")
        self.assertEqual(meta["sig"].encode(), b"claude_cli|opus|auto|sum0|zh")
        self.assertEqual(meta["provider"], "claude_cli")
        self.assertEqual(history.call_count, 2)
        self.assertEqual(signature.call_count, 2)
        captured = dict(meta)
        app.cfg[tr.CFG.MODEL_PROVIDER] = "codex_cli"
        app._last_input, app._last_origin, app._last_class = "new", "ocr", "code"
        app._last_dictionary_local = True
        app._begin_job()
        self.assertEqual(meta, captured)
        self.assertTrue(meta["cancel_event"].is_set())
        thread.return_value.start.assert_called_once_with()

    def test_cache_hit_uses_real_wrappers_and_result_without_history_write(self):
        for provider, source, kind, expected in (
                ("claude_cli", "A complete sentence.", "text", b"claude_cli|haiku|auto|sum0|zh"),
                ("codex_cli", "hello", "dict", b"codex_cli|auto-fast|auto|sum0|zh|codex-format-v5")):
            with self.subTest(provider=provider):
                app = self.result_app()
                app.cfg[tr.CFG.MODEL_PROVIDER] = provider
                app._ss = tr.StreamSession()
                app.root = mock.Mock()
                with mock.patch.object(tr, "find_cached_translation", return_value="CACHED") as cached, \
                        mock.patch.object(tr, "add_history") as history_save, \
                        mock.patch.object(tr, "log_perf"), \
                        mock.patch.object(tr.threading, "Thread") as thread, \
                        mock.patch.object(tr, "history_kind", wraps=rules.history_kind) as history, \
                        mock.patch.object(tr, "provider_cache_signature", wraps=rules.provider_cache_signature) as signature:
                    app._show_loading(source)
                cached.assert_called_once_with(source, kind, expected.decode())
                self.assertEqual(cached.call_args.args[2].encode(), expected)
                self.assertEqual(signature.call_count, 1)
                self.assertEqual(history.call_count, 2)
                app._make_popup.assert_called_once()
                self.assertEqual(app._make_popup.call_args.args[0], "CACHED")
                self.assertEqual(app._last_result_text, "CACHED")
                history_save.assert_not_called()
                thread.assert_not_called()
                app.root.after.assert_not_called()

    def test_show_result_saves_exact_kind_and_signature_through_real_wrappers(self):
        for origin, content_class, source, local, kind, is_dict, expected in (
                ("ocr", "code", "", False, "ocr", False, b"claude_cli|haiku|auto|sum0|zh"),
                ("text", "code", "hello", False, "code", True, b"claude_cli|haiku|auto|sum0|zh"),
                ("text", "text", "hello", True, "dict", True, b"local-dictionary|q:test|f:test"),
                ("text", "text", "hello", False, "dict", True, b"claude_cli|haiku|auto|sum0|zh"),
                ("text", "text", "A complete sentence.", False, "text", False, b"claude_cli|haiku|auto|sum0|zh")):
            with self.subTest(kind=kind, local=local):
                app = self.result_app()
                app.cfg[tr.CFG.MODEL_PROVIDER] = "claude_cli"
                app._last_origin, app._last_class, app._last_input = origin, content_class, source
                app._last_dictionary_local = local
                app._local_dictionary = SimpleNamespace(cache_version="q:test")
                with mock.patch.object(tr, "add_history") as save, \
                        mock.patch.object(tr, "FORMATTER_VERSION", "f:test"), \
                        mock.patch.object(tr, "history_kind", wraps=rules.history_kind) as history, \
                        mock.patch.object(tr, "local_cache_signature", wraps=rules.local_cache_signature) as local_core, \
                        mock.patch.object(tr, "provider_cache_signature", wraps=rules.provider_cache_signature) as provider_core:
                    app._show_result(True, "FRESH", job_id=4)
                save.assert_called_once_with(
                    source, "FRESH", is_dict, 100, is_code=content_class == "code",
                    kind=kind, sig=expected.decode())
                self.assertEqual(save.call_args.kwargs["sig"].encode(), expected)
                self.assertEqual(history.call_count, 1)
                self.assertEqual(local_core.call_count, int(local))
                self.assertEqual(provider_core.call_count, int(not local))

    def test_ai_supplement_cache_uses_provider_wrapper_even_after_local_result(self):
        for provider, expected in (
                ("claude_cli", b"claude_cli|haiku|auto|sum0|zh|supplement:test"),
                ("codex_cli", b"codex_cli|auto-fast|auto|sum0|zh|codex-format-v5|supplement:test")):
            with self.subTest(provider=provider):
                app = bare_app()
                app.cfg[tr.CFG.MODEL_PROVIDER] = provider
                app._last_dictionary_local = True
                app._local_dictionary = SimpleNamespace(cache_version="q:local")
                app._dictionary_ai_cache = mock.Mock()
                app._dictionary_ai_cache.get.return_value = "AI DETAIL"
                with mock.patch.object(cc_app_results, "DICTIONARY_SUPPLEMENT_REVISION", "supplement:test"), \
                        mock.patch.object(tr, "local_cache_signature", wraps=rules.local_cache_signature) as local, \
                        mock.patch.object(tr, "provider_cache_signature", wraps=rules.provider_cache_signature) as signature:
                    self.assertEqual(app._get_ai_dictionary_supplement("hello"), "AI DETAIL")
                app._dictionary_ai_cache.get.assert_called_once_with("hello", expected.decode())
                self.assertEqual(app._dictionary_ai_cache.get.call_args.args[1].encode(), expected)
                self.assertEqual(signature.call_count, 1)
                local.assert_not_called()
                self.assertTrue(app._last_dictionary_local)


if __name__ == "__main__":
    unittest.main()
