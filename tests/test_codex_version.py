"""Shared minimum-version policy; protocol compatibility is checked separately."""

import unittest

from cc_providers.codex_appserver import appserver_version_supported
from cc_providers.codex_catalog import (
    MINIMUM_CODEX_VERSION, codex_version_supported, parse_codex_version,
)


class TestCodexVersion(unittest.TestCase):
    def test_minimum_and_newer_stable_versions_enter_protocol_validation(self):
        self.assertEqual(MINIMUM_CODEX_VERSION, (0, 146, 0))
        for text in ("0.146.0", "0.146.1", "0.147.0", "0.154.0", "0.200.0", "1.0.0"):
            with self.subTest(version=text):
                self.assertTrue(codex_version_supported("codex-cli " + text))
                self.assertTrue(appserver_version_supported("codex-cli " + text))

    def test_comparison_is_numeric_and_rejects_older_versions(self):
        for text in ("0.9.999", "0.145.999", "0.0.0"):
            with self.subTest(version=text):
                self.assertFalse(codex_version_supported("codex-cli " + text))
        self.assertTrue(codex_version_supported("codex-cli 0.1000.0"))

    def test_build_metadata_is_not_displayed_or_used_as_a_pin(self):
        version = parse_codex_version("codex-cli 0.147.0+SYNTHETIC-PRIVATE.build")
        self.assertEqual(version.text, "0.147.0")
        self.assertTrue(version.supported)
        self.assertNotIn("PRIVATE", repr(version))

    def test_prereleases_are_recognized_but_not_silently_supported(self):
        for text in ("0.146.0-alpha.1", "1.0.0-rc.2+build"):
            version = parse_codex_version("codex-cli " + text)
            self.assertTrue(version.prerelease)
            self.assertFalse(version.supported)

    def test_warning_versions_cannot_replace_the_identified_cli_version(self):
        output = "Warning: runtime 99.0.0 at SYNTHETIC_PRIVATE\ncodex-cli 0.147.0\n"
        self.assertEqual(parse_codex_version(output).text, "0.147.0")
        self.assertTrue(appserver_version_supported(output))
        self.assertFalse(appserver_version_supported("Warning: runtime 0.146.0"))

    def test_crlf_tabs_and_surrounding_whitespace_are_accepted(self):
        self.assertEqual(parse_codex_version(b"\r\n \tcodex-cli\t0.146.0 \r\n").text, "0.146.0")

    def test_duplicate_or_conflicting_version_lines_are_unreadable(self):
        for second in ("0.146.0", "0.147.0", "bad"):
            self.assertIsNone(parse_codex_version("codex-cli 0.146.0\ncodex-cli " + second))

    def test_malformed_components_and_suffixes_are_unreadable(self):
        for text in ("01.146.0", "0.0146.0", "0.146.00", "0.146", "0.146.0.1",
                     "0.146.0-", "0.146.0+", "0.146.0-a..b", "0.146.0+bad..build",
                     "0.146.0 suffix", "0.146.0/private", "9999999999.0.0",
                     "0.147.0-01", "0.147.0-alpha.01"):
            with self.subTest(version=text):
                self.assertIsNone(parse_codex_version("codex-cli " + text))

    def test_unidentified_and_non_ascii_versions_are_unreadable(self):
        for output in ("0.146.0", "other-cli 0.146.0", "codex-cli \u0660.146.0",
                       "\x1b[32mcodex-cli 0.146.0", "codex-cli 0.146.0\x00", ""):
            self.assertIsNone(parse_codex_version(output))

    def test_invalid_encoding_or_input_types_are_unreadable(self):
        for output in (b"\xff", b"codex-cli 0.146.0\n\xff", "\ud800", None, 146, []):
            self.assertIsNone(parse_codex_version(output))

    def test_stdout_budget_is_measured_in_utf8_bytes(self):
        line = "codex-cli 0.146.0\n"
        output = line + "x" * (8192 - len(line))
        self.assertTrue(codex_version_supported(output))
        self.assertIsNone(parse_codex_version(output + "x"))
        self.assertIsNone(parse_codex_version(line + "\u4e2d" * 2730))

    def test_result_is_frozen(self):
        version = parse_codex_version("codex-cli 0.146.0")
        with self.assertRaises(AttributeError):
            version.components = (1, 0, 0)
