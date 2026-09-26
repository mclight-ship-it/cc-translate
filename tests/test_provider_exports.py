"""Compatibility for explicitly requested backend exports on the existing path."""

import unittest
from unittest.mock import patch

import cc_providers
from cc_providers.claude_cli import ClaudeCliProvider
from cc_providers.codex_cli import CodexCliProvider, build_codex_prompt, find_codex_cmd


class TestProviderExports(unittest.TestCase):
    def test_explicit_backend_exports_resolve_and_cache_original_objects(self):
        for name, expected in (
                ("ClaudeCliProvider", ClaudeCliProvider),
                ("CodexCliProvider", CodexCliProvider),
                ("build_codex_prompt", build_codex_prompt),
                ("find_codex_cmd", find_codex_cmd)):
            with self.subTest(name=name):
                self.assertIn(name, cc_providers.__all__)
                self.assertIn(name, dir(cc_providers))
                self.assertIs(getattr(cc_providers, name), expected)
                self.assertIs(vars(cc_providers)[name], expected)

    def test_unknown_attribute_fails_instead_of_loading_a_default_backend(self):
        with patch.object(cc_providers, "import_module") as load:
            with self.assertRaises(AttributeError):
                getattr(cc_providers, "unknown_provider_export")
            load.assert_not_called()

    def test_backend_import_failure_propagates_without_a_fallback(self):
        with patch.object(cc_providers, "import_module", side_effect=ImportError("synthetic import failure")):
            with self.assertRaisesRegex(ImportError, "synthetic import failure"):
                cc_providers.__getattr__("CodexCliProvider")
