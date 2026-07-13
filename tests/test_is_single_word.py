"""Tests for is_single_word, which decides whether a selection gets the
dictionary treatment (single word / short term) rather than a sentence
translation. Expected values captured from the real function.
"""
import unittest

from tests._tr import tr


class TestIsSingleWord(unittest.TestCase):
    def test_single_latin_word(self):
        self.assertTrue(tr.is_single_word("apple"))
        self.assertTrue(tr.is_single_word("serendipity"))

    def test_two_word_term_allowed(self):
        self.assertTrue(tr.is_single_word("machine learning"))
        self.assertTrue(tr.is_single_word("New York"))

    def test_hyphenated_term_allowed(self):
        self.assertTrue(tr.is_single_word("co-operate"))

    def test_leading_trailing_space_stripped(self):
        self.assertTrue(tr.is_single_word("  spaced  "))

    def test_three_tokens_is_sentence(self):
        self.assertFalse(tr.is_single_word("hello world foo"))

    def test_trailing_punctuation_is_sentence(self):
        self.assertFalse(tr.is_single_word("runtime."))

    def test_overly_long_token_rejected(self):
        self.assertFalse(
            tr.is_single_word("supercalifragilisticexpialidociousandthensome"))

    def test_newline_is_sentence(self):
        self.assertFalse(tr.is_single_word("line1\nline2"))

    def test_short_cjk_term_allowed(self):
        self.assertTrue(tr.is_single_word("青提"))
        self.assertTrue(tr.is_single_word("一丝不苟"))

    def test_cjk_sentence_rejected(self):
        self.assertFalse(tr.is_single_word("这是一整句话。"))


if __name__ == "__main__":
    unittest.main()
