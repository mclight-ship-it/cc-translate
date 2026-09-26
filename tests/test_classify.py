"""Tests for the local (model-free) code/text/mixed classification heuristics:
classify_selection, code_ratio, _looks_like_code_line.

These run on the translation hot path, so their behaviour must stay stable.
All expected values were captured from the real functions.
"""
import unittest

import cc_classify as tr


CLASSIFICATION_MATRIX = (
    ("python definition", "def greet(name):\n    return f'Hello {name}'", "code"),
    ("python class", "class Worker:\n    pass", "code"),
    ("python control flow", "if ready:\n    run()", "code"),
    ("python assignment", "items = [1, 2, 3]", "code"),
    ("consecutive assignments", "count = 0\ncount += 1", "code"),
    ("consecutive calls", "prepare()\nrun()\ncleanup()", "code"),
    ("python list construction", "items = [\n    first,\n    second,\n]", "code"),
    ("python dict construction", "options = {\n    'fast': True,\n}", "code"),
    ("python decorator import", "@dataclass\nclass User:\n    name: str", "code"),
    ("python imports", "from pathlib import Path\nimport json", "code"),
    (
        "python chinese docstring",
        "def greet(name):\n"
        "    \"\"\"返回中文问候语。\n"
        "    不调用外部服务。\n"
        "    \"\"\"\n"
        "    return f'你好，{name}'",
        "code",
    ),
    (
        "historical mixed python",
        "下面这段代码读取配置：\n"
        "def load_config(path):\n"
        "    return json.load(path)\n"
        "调用失败时请检查文件路径。",
        "mixed",
    ),
    (
        "json block",
        "{\n  \"name\": \"cc-translate\",\n  \"enabled\": true\n}",
        "code",
    ),
    ("inline json", "{\"name\": \"cc-translate\", \"enabled\": true}", "code"),
    (
        "yaml block",
        "service:\n  name: cc-translate\n  enabled: true",
        "code",
    ),
    (
        "ini config",
        "[server]\nhost=localhost\nport=8080",
        "code",
    ),
    ("standalone brackets", "[\n  \"alpha\",\n  \"beta\"\n]", "code"),
    ("short call prose", "call foo() today", "text"),
    ("longer call prose", "Please call foo() after lunch today.", "text"),
    ("form spouse", "Spouse's Full Name (include Maiden Name)", "text"),
    ("form birth date", "Date of Birth (MM/DD/YYYY)", "text"),
    ("ordinary date", "The meeting is on 2026-09-09 at 10:30.", "text"),
    ("windows path", r"Please open C:\Users\name\Documents today.", "text"),
    ("unix path", "The file is stored in /usr/local/share for reference.", "text"),
    ("parenthetical prose", "Please sign here (required).", "text"),
    ("natural equation", "Total = 42 dollars for the full order.", "text"),
    ("form fields", "Name: Taylor\nDate: 2026-09-09", "text"),
    ("multi-line form fields", "Name: Taylor\nDate: Today\nCity: Seattle", "text"),
)


class TestLooksLikeCodeLine(unittest.TestCase):
    def test_blank_line_is_neutral(self):
        # Blank/whitespace lines return None so they're excluded from the ratio.
        self.assertIsNone(tr._looks_like_code_line(""))
        self.assertIsNone(tr._looks_like_code_line("   "))

    def test_obvious_code_line(self):
        self.assertTrue(tr._looks_like_code_line("const x = getUserById(42);"))

    def test_plain_english_is_not_code(self):
        self.assertFalse(tr._looks_like_code_line("Hello, how are you today?"))

    def test_english_label_with_parenthetical_is_not_code(self):
        # Regression: a form label like "Word (note)" must not read as code.
        # A prose parenthetical has a space before "(", unlike a real call
        # "foo(", and common English words (include/if/from...) sit in the
        # code-keyword list — the two used to combine into a false positive.
        for label in (
                "Spouse's Full Name (include Maiden Name)",
                "Date of Birth (MM/DD/YYYY)",
                "Emergency Contact (Primary)",
                "Annual Income (before taxes)",
                "Country of Residence (if different)"):
            self.assertFalse(tr._looks_like_code_line(label), label)

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

    def test_block_context_can_promote_beyond_raw_line_ratio(self):
        python = (
            "def greet(name):\n"
            "    \"\"\"返回中文问候语。\"\"\"\n"
            "    return f'你好，{name}'"
        )
        yaml = "service:\n  name: cc-translate\n  enabled: true"
        for sample in (python, yaml):
            with self.subTest(sample=sample):
                self.assertLess(
                    tr.code_ratio(sample), tr.CODE_RATIO_PURE)
                self.assertEqual(tr.classify_selection(sample), "code")


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

    def test_english_form_label_is_text(self):
        # Regression for the reported bug: English form labels with a
        # parenthetical were misclassified as code and got a code explanation.
        self.assertEqual(
            tr.classify_selection("Spouse's Full Name (include Maiden Name)"),
            "text",
        )
        self.assertEqual(
            tr.classify_selection("Date of Birth (MM/DD/YYYY)"),
            "text",
        )

    def test_empty_and_whitespace_are_text(self):
        self.assertEqual(tr.classify_selection(""), "text")
        self.assertEqual(tr.classify_selection("   "), "text")
        self.assertEqual(tr.classify_selection(None), "text")

    def test_thresholds_are_ordered(self):
        # Guard the invariant the three-way split depends on.
        self.assertGreater(tr.CODE_RATIO_PURE, tr.CODE_RATIO_MIXED)

    def test_prose_with_short_function_call_is_text(self):
        self.assertEqual(tr.classify_selection("call foo() today"), "text")
        self.assertEqual(tr.classify_selection("just call foo() today"), "text")

    def test_function_call_alone_is_code(self):
        # A bare function call is correctly classified as code.
        self.assertEqual(tr.classify_selection("foo()"), "code")
        self.assertEqual(tr.classify_selection("name()"), "code")

    def test_multiline_with_code_lines_is_mixed(self):
        # Multi-line selections with both code and prose lines classify as mixed.
        multi_line = "This is prose\ncode();\nmore prose"
        self.assertEqual(tr.classify_selection(multi_line), "mixed")

    def test_operator_in_phrase(self):
        # A phrase with operators (=>, &&, ||) can classify as code due to
        # the operator signal plus symbol density. This is an edge case.
        # "a => b is a function" (20 chars, 1 operator "=>", symbol density 0.05)
        # has operator +1, but needs another signal to hit score >= 2
        self.assertEqual(
            tr.classify_selection("a => b is a function"), "text")

    def test_regex_pattern_is_code(self):
        # Regex patterns with brackets and symbols score >= 2:
        # - brackets/parens: symbol density
        # - [a-z] inside: camelCase-like or symbol-heavy
        self.assertEqual(
            tr.classify_selection("regex: [a-zA-Z0-9_]+"),
            "code",
        )

    def test_regression_matrix(self):
        for name, sample, expected in CLASSIFICATION_MATRIX:
            with self.subTest(name=name):
                self.assertEqual(
                    tr.classify_selection(sample), expected, sample)


if __name__ == "__main__":
    unittest.main()
