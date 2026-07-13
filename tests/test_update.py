"""Tests for the pure (network-free, git-free) self-update helpers:
update_available and _format_version.

The git/network calls (remote_head, _git, ...) are intentionally not tested
here — they require a live repo. These two functions hold the decision logic
and the user-visible version label, so their behaviour must stay stable.
"""
import unittest

from tests._tr import tr


class TestUpdateAvailable(unittest.TestCase):
    def test_same_sha_is_no_update(self):
        self.assertFalse(tr.update_available("abc123", "abc123"))

    def test_different_sha_is_update(self):
        self.assertTrue(tr.update_available("abc123", "def456"))

    def test_whitespace_is_ignored(self):
        self.assertFalse(tr.update_available("abc123\n", "  abc123 "))

    def test_missing_side_is_no_update(self):
        # Never claim an update when either side is unknown (git/network failed).
        self.assertFalse(tr.update_available(None, "def456"))
        self.assertFalse(tr.update_available("abc123", None))
        self.assertFalse(tr.update_available("", "def456"))
        self.assertFalse(tr.update_available(None, None))


class TestFormatVersion(unittest.TestCase):
    def test_sha_and_date(self):
        self.assertEqual(
            tr._format_version("9ef3615", "2026-07-13"),
            "9ef3615 · 2026-07-13",
        )

    def test_sha_without_date(self):
        self.assertEqual(tr._format_version("9ef3615", None), "9ef3615")
        self.assertEqual(tr._format_version("9ef3615", ""), "9ef3615")

    def test_missing_sha_is_unknown(self):
        self.assertEqual(tr._format_version(None, "2026-07-13"), "未知版本")
        self.assertEqual(tr._format_version("", None), "未知版本")


if __name__ == "__main__":
    unittest.main()
