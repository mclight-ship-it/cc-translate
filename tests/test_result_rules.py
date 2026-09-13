"""Portable byte contracts and frozen translator rules from 855b73f."""

from itertools import product
from types import FunctionType, SimpleNamespace
import unittest
from unittest.mock import Mock

import cc_classify
import cc_result_rules as rules


def legacy_history_kind(self):
    if self._last_origin == "ocr":
        return "ocr"
    if self._last_class == "code":
        return "code"
    if self._last_input and is_single_word(self._last_input):
        return "dict"
    return "text"


def legacy_cache_signature(self, route=None) -> str:
    if route == "local" or (
            route is None and getattr(self, "_last_dictionary_local", False)):
        dictionary = getattr(self, "_local_dictionary", None)
        dictionary_version = (
            dictionary.cache_version if dictionary is not None
            else "unavailable")
        return "|".join((
            "local-dictionary", dictionary_version, FORMATTER_VERSION))
    selection = self._provider_selection()
    fields = [
        selection.provider_id,
        str(selection.model or "auto"),
        str(self.cfg.get(CFG.DIRECTION, "auto")),
        "sum1" if self.cfg.get(
            CFG.SUMMARY_ENABLED,
            DEFAULT_CONFIG[CFG.SUMMARY_ENABLED]) else "sum0",
        str(self.cfg.get(CFG.LANGUAGE) or i18n.get_language()),
    ]
    prompt_revision = PROVIDER_PROMPT_REVISIONS.get(
        selection.provider_id, "")
    if prompt_revision:
        fields.append(prompt_revision)
    return "|".join(fields)


def bind_legacy(namespace):
    return tuple(FunctionType(function.__code__, namespace, function.__name__, function.__defaults__)
                 for function in (legacy_history_kind, legacy_cache_signature))


def resolved_provider_fields(provider, model, direction, summary, language, revision):
    return provider, str(model or "auto"), str(direction), bool(summary), str(language), revision


PROVIDER_CASES = (
    (("claude_cli", "haiku", "auto", False, "zh", ""), b"claude_cli|haiku|auto|sum0|zh"),
    (("codex_cli", "auto", "auto", True, "en_US", "codex-format-v5"),
     b"codex_cli|auto|auto|sum1|en_US|codex-format-v5"),
    (("codex_cli", "auto-fast", "to_zh", False, "zh_CN", "codex-format-v5"),
     b"codex_cli|auto-fast|to_zh|sum0|zh_CN|codex-format-v5"),
    (("claude_cli", None, "auto", None, "zh", None), b"claude_cli|auto|auto|sum0|zh"),
    (("claude_cli", "", "", 0, "", ""), b"claude_cli|auto||sum0|"),
    (("codex_cli", False, None, "false", None, ""), b"codex_cli|auto|None|sum1|None"),
    (("codex_cli", 0, 7, [], "ja", False), b"codex_cli|auto|7|sum0|ja"),
    (("custom|provider", 42, "x\ny", True, "en|US", "r|1"),
     b"custom|provider|42|x\ny|sum1|en|US|r|1"),
    (("", "model", "auto", 1, " ", ""), b"|model|auto|sum1| "),
    (("claude_cli", "haiku", "to_ja", False, "\u4e2d\u6587", "r\u4e8c"),
     b"claude_cli|haiku|to_ja|sum0|\xe4\xb8\xad\xe6\x96\x87|r\xe4\xba\x8c"),
)

HISTORY_CASES = (
    (("ocr", "code", "hello"), "ocr"),
    (("ocr", "text", "A complete sentence."), "ocr"),
    (("text", "code", "hello"), "code"),
    (("text", "code", None), "code"),
    (("text", "text", "hello"), "dict"),
    (("text", "mixed", "machine learning"), "dict"),
    (("text", "text", "\u4e2d\u6587"), "dict"),
    (("text", "text", "A complete sentence."), "text"),
    (("text", "text", ""), "text"),
    (("text", "text", "  "), "text"),
    ((None, None, None), "text"),
)


class TestResultRules(unittest.TestCase):
    def reference(self, language="fallback", revisions=None, formatter="format-v8"):
        return bind_legacy({
            "__builtins__": __builtins__,
            "CFG": SimpleNamespace(DIRECTION="direction", SUMMARY_ENABLED="summary", LANGUAGE="language"),
            "DEFAULT_CONFIG": {"summary": False},
            "i18n": SimpleNamespace(get_language=lambda: language),
            "PROVIDER_PROMPT_REVISIONS": revisions or {},
            "FORMATTER_VERSION": formatter,
            "is_single_word": cc_classify.is_single_word,
        })

    def test_fixed_provider_bytes_and_frozen_implementation(self):
        for args, expected in PROVIDER_CASES:
            with self.subTest(args=args):
                provider, model, direction, summary, language, revision = args
                _, legacy = self.reference(language, {provider: revision})
                app = SimpleNamespace(
                    cfg={"direction": direction, "summary": summary, "language": language},
                    _provider_selection=lambda: SimpleNamespace(provider_id=provider, model=model))
                self.assertEqual(rules.provider_cache_signature(
                    *resolved_provider_fields(*args)).encode("utf-8"), expected)
                self.assertEqual(legacy(app).encode("utf-8"), expected)

    def test_local_bytes_and_frozen_route_priority(self):
        for version, formatter, expected in (
                ("query:data", "format-v8", b"local-dictionary|query:data|format-v8"),
                ("unavailable", "format-v8", b"local-dictionary|unavailable|format-v8"),
                ("", "", b"local-dictionary||"),
                ("q|d", "f\nv", b"local-dictionary|q|d|f\nv")):
            with self.subTest(version=version, formatter=formatter):
                _, legacy = self.reference(formatter=formatter)
                app = SimpleNamespace(
                    _local_dictionary=SimpleNamespace(cache_version=version),
                    _last_dictionary_local=True,
                    _provider_selection=Mock(side_effect=AssertionError("local queried provider")))
                self.assertEqual(rules.local_cache_signature(version, formatter).encode(), expected)
                self.assertEqual(legacy(app).encode(), expected)
                self.assertEqual(legacy(app, route="local").encode(), expected)

    def test_provider_field_cross_product_matches_frozen_bytes(self):
        for args in product(
                ("claude_cli", "codex_cli", "custom"), (None, "", "auto", "auto-fast", "haiku"),
                (None, "", "auto", "to_en"), (False, True, None, "false"),
                ("zh", "en_US", ""), (None, "", "r|1")):
            with self.subTest(args=args):
                provider, model, direction, summary, language, revision = args
                _, legacy = self.reference(language, {provider: revision})
                app = SimpleNamespace(
                    cfg={"direction": direction, "summary": summary, "language": language},
                    _provider_selection=lambda: SimpleNamespace(provider_id=provider, model=model))
                self.assertEqual(rules.provider_cache_signature(
                    *resolved_provider_fields(*args)).encode("utf-8"),
                                 legacy(app).encode("utf-8"))

    def test_resolved_strings_are_not_coerced_or_defaulted_again(self):
        class Rendered(str):
            def __str__(self):
                raise RuntimeError("resolved field stringified twice")

            def __bool__(self):
                raise RuntimeError("resolved field defaulted twice")

        for fields, expected in (
                (("", "", ""), b"claude_cli|||sum0|"),
                ((Rendered("model"), Rendered("direction"), Rendered("language")),
                 b"claude_cli|model|direction|sum0|language")):
            with self.subTest(expected=expected):
                model, direction, language = fields
                self.assertEqual(rules.provider_cache_signature(
                    "claude_cli", model, direction, False, language).encode(), expected)

    def test_summary_truth_failure_propagates(self):
        class Broken:
            def __bool__(self):
                raise RuntimeError("synthetic truth failure")

        summary = Broken()
        _, legacy = self.reference("zh")
        app = SimpleNamespace(
            cfg={"summary": summary, "language": "zh"},
            _provider_selection=lambda: SimpleNamespace(provider_id="claude_cli", model="auto"))
        with self.assertRaisesRegex(RuntimeError, "^synthetic truth failure$"):
            legacy(app)
        with self.assertRaisesRegex(RuntimeError, "^synthetic truth failure$"):
            rules.provider_cache_signature("claude_cli", "auto", "auto", summary, "zh")

    def test_default_values_are_resolved_by_the_caller(self):
        _, legacy = self.reference(language="fallback")
        app = SimpleNamespace(cfg={}, _provider_selection=lambda: SimpleNamespace(provider_id="claude_cli", model=None))
        self.assertEqual(legacy(app), rules.provider_cache_signature(
            "claude_cli", "auto", "auto", False, "fallback"))
        app._last_dictionary_local = True
        self.assertEqual(legacy(app), rules.local_cache_signature("unavailable", "format-v8"))

    def test_history_priority_matches_fixed_and_frozen_outputs(self):
        legacy, _ = self.reference()
        for args, expected in HISTORY_CASES:
            with self.subTest(args=args):
                app = SimpleNamespace(_last_origin=args[0], _last_class=args[1], _last_input=args[2])
                self.assertEqual(rules.history_kind(*args), expected)
                self.assertEqual(legacy(app), expected)

    def test_history_uses_existing_word_rule_and_preserves_lazy_predicate(self):
        self.assertIs(rules.history_kind.__kwdefaults__["word_test"], cc_classify.is_single_word)
        rejected = Mock(side_effect=AssertionError("predicate must not run"))
        for args in (("ocr", "code", "hello"), ("text", "code", "hello"), ("text", "text", None)):
            rules.history_kind(*args, word_test=rejected)
        rejected.assert_not_called()
        override = Mock(return_value=True)
        self.assertEqual(rules.history_kind("text", "mixed", "synthetic sentence", word_test=override), "dict")
        override.assert_called_once_with("synthetic sentence")
        with self.assertRaisesRegex(RuntimeError, "synthetic word error"):
            rules.history_kind("text", "text", "word", word_test=Mock(side_effect=RuntimeError("synthetic word error")))
        with self.assertRaises(TypeError):
            rules.history_kind("text", "text", "word", word_test=None)

    def test_invalid_join_fields_are_not_normalized_or_silently_defaulted(self):
        for version, formatter in ((None, "f"), (3, "f"), ("v", None), ("v", 3)):
            with self.subTest(version=version, formatter=formatter):
                _, legacy = self.reference(formatter=formatter)
                app = SimpleNamespace(_local_dictionary=SimpleNamespace(cache_version=version))
                with self.assertRaises(TypeError) as old:
                    legacy(app, "local")
                with self.assertRaises(TypeError) as new:
                    rules.local_cache_signature(version, formatter)
                self.assertEqual(str(old.exception), str(new.exception))
        for provider, revision in ((None, ""), (3, ""), ("codex_cli", 3), ("codex_cli", True)):
            with self.subTest(provider=provider, revision=revision):
                _, legacy = self.reference("zh", {provider: revision})
                app = SimpleNamespace(
                    cfg={"language": "zh"},
                    _provider_selection=lambda: SimpleNamespace(provider_id=provider, model=None))
                with self.assertRaises(TypeError) as old:
                    legacy(app)
                with self.assertRaises(TypeError) as new:
                    rules.provider_cache_signature(provider, "auto", "auto", False, "zh", revision)
                self.assertEqual(str(old.exception), str(new.exception))
