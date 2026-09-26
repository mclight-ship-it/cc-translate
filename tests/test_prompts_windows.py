"""Existing Windows consumers must use the same shared prompt objects."""

import unittest

from tests._tr import tr
from tests.test_prompts import PROMPT_NAMES
import cc_app_results
import cc_app_warm
import cc_core
import cc_prompts


class TestPromptExports(unittest.TestCase):
    def test_core_and_application_export_shared_catalog(self):
        for entry in (cc_core, tr):
            for name in PROMPT_NAMES:
                with self.subTest(entry=entry.__name__, name=name):
                    self.assertIs(getattr(entry, name), getattr(cc_prompts, name))

    def test_warm_and_result_actions_use_shared_prompt_objects(self):
        for entry, names in (
                (cc_app_warm, ("SYSTEM_SUFFIX", "DICTIONARY_PROMPT")),
                (cc_app_results, (
                    "SYSTEM_SUFFIX", "DICTIONARY_SUPPLEMENT_PROMPT",
                    "DICTIONARY_SUPPLEMENT_REVISION", "CODE_EXPLAIN_APPEND_PROMPT",
                    "RESULT_ACTION_PROMPTS"))):
            for name in names:
                with self.subTest(entry=entry.__name__, name=name):
                    self.assertIs(getattr(entry, name), getattr(cc_prompts, name))
