"""Tests for highlight_code (Pygments-backed code-block colouring) and the
token->tag mapping. Written to pass whether or not Pygments is installed, so
the graceful-degradation contract is itself under test.
"""
import unittest

from tests._tr import tr


class TestHighlightCode(unittest.TestCase):
    def test_empty_code_returns_none(self):
        self.assertIsNone(tr.highlight_code("", "python"))
        self.assertIsNone(tr.highlight_code(None, "python"))

    def test_known_language_produces_token_tags(self):
        result = tr.highlight_code("def f(x):\n    return x + 1", "python")
        if tr._PYGMENTS_OK:
            self.assertIsNotNone(result)
            found = {t for _, t in result}
            self.assertIn("rich_tok_keyword", found)
            # Every segment carries a rich_ tag (tok or the codeblock fallback).
            self.assertTrue(all(t and t.startswith("rich_") for _, t in result))
            # Text is preserved (Pygments may append a single trailing newline).
            joined = "".join(c for c, _ in result)
            self.assertEqual(joined.rstrip("\n"), "def f(x):\n    return x + 1")
        else:
            self.assertIsNone(result)

    def test_unknown_language_does_not_crash(self):
        # A bogus lexer name must not raise; returns a list (guessed) or None.
        result = tr.highlight_code("SELECT 1", "no-such-lang-xyz")
        self.assertTrue(result is None or isinstance(result, list))


class TestTokenTagMapping(unittest.TestCase):
    def test_maps_common_token_types(self):
        if not tr._PYGMENTS_OK:
            self.skipTest("Pygments not installed")
        T = tr._PygToken
        self.assertEqual(tr._pyg_token_tag(T.Keyword), "rich_tok_keyword")
        self.assertEqual(tr._pyg_token_tag(T.String), "rich_tok_string")
        self.assertEqual(tr._pyg_token_tag(T.Comment), "rich_tok_comment")
        self.assertEqual(tr._pyg_token_tag(T.Number), "rich_tok_number")
        self.assertEqual(tr._pyg_token_tag(T.Operator), "rich_tok_operator")

    def test_unknown_token_falls_back_to_codeblock(self):
        if not tr._PYGMENTS_OK:
            self.skipTest("Pygments not installed")
        self.assertEqual(
            tr._pyg_token_tag(tr._PygToken.Generic), "rich_codeblock")


if __name__ == "__main__":
    unittest.main()
