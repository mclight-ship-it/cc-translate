"""Tests for is_single_word, which decides whether a selection gets the
dictionary treatment (single word / short term) rather than a sentence
translation. Expected values captured from the real function.
"""
import unittest

import cc_classify as tr


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

    def test_empty_none_and_whitespace_rejected(self):
        for text in (None, "", " ", "\t", "\n", "\r\n"):
            with self.subTest(text=text):
                self.assertFalse(tr.is_single_word(text))

    def test_latin_character_limit_includes_separators(self):
        self.assertTrue(tr.is_single_word("a" * 30))
        self.assertFalse(tr.is_single_word("a" * 31))
        self.assertTrue(tr.is_single_word("a" * 14 + " " + "b" * 15))
        self.assertFalse(tr.is_single_word("a" * 15 + " " + "b" * 15))

    def test_cjk_character_limit_and_mixed_terms(self):
        self.assertTrue(tr.is_single_word("\u6f22" * 4))
        self.assertFalse(tr.is_single_word("\u6f22" * 5))
        self.assertTrue(tr.is_single_word("AI\u52a9\u624b"))
        self.assertFalse(tr.is_single_word("AI \u52a9\u624b"))

    def test_all_existing_sentence_terminators_rejected(self):
        for terminator in ".!?\u2026\u3002\uff01\uff1f\uff0c,;\uff1b:\uff1a":
            for term in ("word", "\u4e2d\u6587"):
                with self.subTest(terminator=terminator, term=term):
                    self.assertFalse(tr.is_single_word(term + terminator))

    def test_outer_line_breaks_are_stripped_but_internal_lf_rejected(self):
        self.assertTrue(tr.is_single_word("\nword\r\n"))
        self.assertTrue(tr.is_single_word("word\tanother"))
        self.assertTrue(tr.is_single_word("word\ranother"))
        self.assertFalse(tr.is_single_word("word\nanother"))

    def test_alphabetic_latin_and_existing_apostrophe_rule(self):
        self.assertTrue(tr.is_single_word("caf\u00e9"))
        self.assertTrue(tr.is_single_word("can't"))
        self.assertFalse(tr.is_single_word("can\u2019t"))
        self.assertFalse(tr.is_single_word("word2"))
        self.assertFalse(tr.is_single_word("two_word"))

    def test_existing_permissive_symbol_terms_are_not_changed_by_extraction(self):
        for text in ("---", "''", "\U0001f600", "\u4e2d\t\u6587"):
            with self.subTest(text=text):
                self.assertTrue(tr.is_single_word(text))

    def test_three_tokens_rejected_even_when_short(self):
        self.assertFalse(tr.is_single_word("a b c"))
        self.assertFalse(tr.is_single_word("a\tb\tc"))


if __name__ == "__main__":
    unittest.main()
