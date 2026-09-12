"""Portable regression for the existing direction and prompt contract."""

import unittest

import cc_direction as direction


DIRECTION_MATRIX = (
    ("zh_CN", "This is English prose.", "zh"),
    ("zh_CN", "\u8fd9\u662f\u4e00\u6bb5\u4e2d\u6587\u5185\u5bb9", "en"),
    ("zh_CN", "\u3053\u3093\u306b\u3061\u306f\u4e16\u754c", "zh"),
    ("zh_CN", "\uff76\uff80\uff76\uff85\u4e16\u754c", "zh"),
    ("zh_CN", "\uc548\ub155\ud558\uc138\uc694 \uc138\uacc4", "zh"),
    ("zh_CN", "\u4e2d\u6587" * 10 + " with Python code foo()", "en"),
    ("zh_CN", "This is a mostly English paragraph with a stray token \u4e2d\u6587.", "zh"),
    ("en_US", "This is English prose.", "zh"),
    ("en_US", "\u8fd9\u662f\u4e00\u6bb5\u4e2d\u6587\u5185\u5bb9", "en"),
    ("en_US", "\u3053\u3093\u306b\u3061\u306f\u4e16\u754c", "en"),
    ("en_US", "\uc548\ub155\ud558\uc138\uc694 \uc138\uacc4", "en"),
    ("zh_CN", "", "zh"),
    ("en_US", "", "en"),
    ("zh_CN", "123 !?", "zh"),
    ("en_US", "123 !?", "en"),
)


class TestDirection(unittest.TestCase):
    def test_language_catalog_and_mode_order(self):
        self.assertEqual(list(direction.LANGUAGES), ["zh", "en", "ja", "ko", "fr", "de", "es"])
        self.assertEqual(
            list(direction.DIRECTION_MODES),
            ["auto"] + [f"to_{code}" for code in direction.LANGUAGES])
        self.assertEqual(
            [names[1] for names in direction.LANGUAGES.values()],
            ["Simplified Chinese", "English", "Japanese", "Korean", "French", "German", "Spanish"])

    def test_auto_direction_matrix(self):
        for ui, text, expected in DIRECTION_MATRIX:
            with self.subTest(ui=ui, text=text):
                self.assertEqual(direction.resolve_target_lang("auto", ui, text), expected)

    def test_every_explicit_target_ignores_ui_and_source(self):
        for code in direction.LANGUAGES:
            for ui, text, _ in DIRECTION_MATRIX:
                with self.subTest(code=code, ui=ui, text=text):
                    self.assertEqual(direction.resolve_target_lang(f"to_{code}", ui, text), code)

    def test_unknown_mode_preserves_auto_target_routing(self):
        for mode in (None, "", "unknown", "to_unknown"):
            for ui, text, expected in DIRECTION_MATRIX:
                with self.subTest(mode=mode, ui=ui, text=text):
                    self.assertEqual(direction.resolve_target_lang(mode, ui, text), expected)

    def test_unknown_ui_preserves_chinese_ui_pivot(self):
        for ui in (None, "", "ja_JP", "unknown"):
            self.assertEqual(direction.resolve_target_lang("auto", ui, "English prose"), "zh")
            self.assertEqual(direction.auto_direction_prompt(ui), direction.auto_direction_prompt("zh_CN"))

    def test_character_counts_remain_ascii_latin_only(self):
        self.assertEqual(direction._cjk_latin_counts("Az \u00e9 \u6f22 123!"), (1, 2))
        self.assertEqual(direction._cjk_latin_counts(None), (0, 0))

    def test_source_thresholds_are_unchanged_and_inclusive(self):
        self.assertEqual(direction.CJK_SOURCE_RATIO, 0.34)
        self.assertTrue(direction.source_is_cjk("\u6f22" * 34 + "a" * 100))
        self.assertFalse(direction.source_is_cjk("\u6f22" * 33 + "a" * 100))
        self.assertTrue(direction.source_has_english("a" * 34 + "\u6f22" * 100))
        self.assertFalse(direction.source_has_english("a" * 33 + "\u6f22" * 100))

    def test_source_requires_two_meaningful_characters(self):
        for text in (None, "", " ", "a", "\u6f22", "12!?"):
            with self.subTest(text=text):
                self.assertFalse(direction.source_is_cjk(text))
                self.assertFalse(direction.source_has_english(text))

    def test_auto_prompt_english_ui_is_exact(self):
        self.assertEqual(direction.direction_prompt("auto", "en_US"), (
            "Translate the user's text. If it contains any meaningful "
            "English prose, translate the WHOLE text into natural Simplified "
            "Chinese. Only if it has essentially no English (e.g. it is "
            "Chinese or another language) translate it into natural English."))

    def test_auto_prompt_chinese_ui_is_exact(self):
        self.assertEqual(direction.direction_prompt("auto", "zh_CN"), (
            "Translate the user's text. If it contains any meaningful Chinese "
            "(even when mixed with English words, code or punctuation), translate "
            "the WHOLE text into natural English. Only if it has essentially no "
            "Chinese translate it into natural Simplified Chinese."))

    def test_explicit_prompt_is_exact_for_all_languages(self):
        for code, (_, name) in direction.LANGUAGES.items():
            for ui in ("zh_CN", "en_US"):
                with self.subTest(code=code, ui=ui):
                    self.assertEqual(
                        direction.direction_prompt(f"to_{code}", ui),
                        f"Translate the user's text into natural {name}.")

    def test_unknown_prompt_mode_keeps_legacy_fallback(self):
        expected = ("Translate the user's text. If it is Chinese, translate to natural "
                    "English; otherwise translate to natural Simplified Chinese.")
        for mode in (None, "", "unknown", "to_unknown"):
            for ui in ("zh_CN", "en_US"):
                self.assertEqual(direction.direction_prompt(mode, ui), expected)
