"""Tests for the local (model-free) code/text/mixed classification heuristics:
classify_selection, code_ratio, _looks_like_code_line.

These run on the translation hot path, so their behaviour must stay stable.
All expected values were captured from the real functions.
"""
import unittest

from tests._tr import tr


class TestLooksLikeCodeLine(unittest.TestCase):
    def test_blank_line_is_neutral(self):
        # Blank/whitespace lines return None so they're excluded from the ratio.
        self.assertIsNone(tr._looks_like_code_line(""))
        self.assertIsNone(tr._looks_like_code_line("   "))

    def test_obvious_code_line(self):
        self.assertTrue(tr._looks_like_code_line("const x = getUserById(42);"))

    def test_plain_english_is_not_code(self):
        self.assertFalse(tr._looks_like_code_line("Hello, how are you today?"))

    def test_chinese_prose_is_not_code(self):
        # CJK-heavy lines are prose even with stray punctuation.
        self.assertFalse(tr._looks_like_code_line("这是一句中文。"))


class TestCodeRatio(unittest.TestCase):
    def test_pure_code_ratio_is_one(self):
        self.assertEqual(tr.code_ratio("def foo(x):\n    return x + 1"), 1.0)

    def test_plain_prose_ratio_is_zero(self):
        self.assertEqual(tr.code_ratio("just some plain english prose here"), 0.0)

    def test_all_blank_lines_ratio_is_zero(self):
        # No non-blank lines to consider → 0.0, never a divide-by-zero.
        self.assertEqual(tr.code_ratio("\n\n   \n"), 0.0)


class TestClassifySelection(unittest.TestCase):
    def test_pure_code(self):
        code = "def foo(x):\n    return x + 1\n    y = getUserById(x)"
        self.assertEqual(tr.classify_selection(code), "code")

    def test_plain_sentence_is_text(self):
        self.assertEqual(
            tr.classify_selection("今天天气很好，我们出去走走吧，顺便买点东西。"),
            "text",
        )

    def test_prose_with_one_inline_call_is_text(self):
        # A single foo() inside a Chinese sentence should not tip it to code.
        self.assertEqual(
            tr.classify_selection(
                "这个函数 foo() 的作用是把 x 加一然后返回给调用方使用。"),
            "text",
        )

    def test_empty_and_whitespace_are_text(self):
        self.assertEqual(tr.classify_selection(""), "text")
        self.assertEqual(tr.classify_selection("   "), "text")
        self.assertEqual(tr.classify_selection(None), "text")

    def test_thresholds_are_ordered(self):
        # Guard the invariant the three-way split depends on.
        self.assertGreater(tr.CODE_RATIO_PURE, tr.CODE_RATIO_MIXED)


if __name__ == "__main__":
    unittest.main()
