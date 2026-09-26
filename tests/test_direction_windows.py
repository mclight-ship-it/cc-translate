"""Windows compatibility exports and localization around shared direction logic."""

import unittest
from unittest.mock import patch

import cc_core
import cc_direction
from tests._tr import tr
from tests.test_direction import DIRECTION_MATRIX


class TestDirectionExports(unittest.TestCase):
    def test_existing_entries_export_the_same_functions_and_constants(self):
        names = (
            "LANGUAGES", "DIRECTION_MODES", "CJK_SOURCE_RATIO",
            "auto_direction_prompt", "direction_prompt", "resolve_target_lang",
            "source_is_cjk", "source_has_english",
        )
        for entry in (cc_core, tr):
            for name in names:
                with self.subTest(entry=entry.__name__, name=name):
                    self.assertIs(getattr(entry, name), getattr(cc_direction, name))
        self.assertIs(cc_core._cjk_latin_counts, cc_direction._cjk_latin_counts)

    def test_windows_target_matrix(self):
        for ui, text, expected in DIRECTION_MATRIX:
            with self.subTest(ui=ui, text=text):
                self.assertEqual(tr.resolve_target_lang("auto", ui, text), expected)

    def test_ui_localization_stays_in_the_existing_wrapper(self):
        for ui, labels in (
                ("zh_CN", cc_core.DIRECTION_LABELS_ZH),
                ("en_US", cc_core.DIRECTION_LABELS_EN)):
            with patch.object(cc_core.i18n, "get_language", return_value=ui):
                self.assertIs(cc_core.get_direction_labels(), labels)
                self.assertIs(tr.get_direction_labels(), labels)
                self.assertEqual(set(labels), set(cc_direction.DIRECTION_MODES))
