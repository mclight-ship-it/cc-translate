"""Windows entry-point compatibility for the shared classification module."""

import unittest

import cc_classify
import cc_core
import cc_app_results
from tests._tr import tr
from tests.test_classify import CLASSIFICATION_MATRIX


class TestClassificationExports(unittest.TestCase):
    def test_windows_uses_shared_functions_and_thresholds(self):
        for name in (
                "classify_selection", "code_ratio", "_looks_like_code_line",
                "_block_code_line_indexes", "_assignment_looks_like_code",
                "CODE_RATIO_PURE", "CODE_RATIO_MIXED"):
            with self.subTest(name=name):
                self.assertIs(getattr(tr, name), getattr(cc_classify, name))

    def test_windows_classification_matrix(self):
        for name, sample, expected in CLASSIFICATION_MATRIX:
            with self.subTest(name=name):
                self.assertEqual(tr.classify_selection(sample), expected)

    def test_dictionary_and_result_actions_use_the_shared_rule(self):
        for entry in (cc_core, tr, cc_app_results):
            with self.subTest(entry=entry.__name__):
                self.assertIs(entry.is_single_word, cc_classify.is_single_word)
