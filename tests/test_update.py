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


class TestUninstaller(unittest.TestCase):
    """The uninstaller writes a detached cleanup script; verify its contents
    without ever spawning a process or deleting anything real."""

    def _run(self, tmp, remove_data, notify=True):
        import os
        import unittest.mock as mock
        cc = tr._cc_update
        app_dir = os.path.join(tmp, "cc-translate")
        data_dir = os.path.join(tmp, "CC Translate")
        os.makedirs(app_dir, exist_ok=True)
        os.makedirs(data_dir, exist_ok=True)
        with mock.patch.dict(os.environ, {"TEMP": tmp, "TMP": tmp}), \
                mock.patch.object(cc.subprocess, "Popen") as popen:
            ok = cc.spawn_uninstaller(
                app_dir=app_dir, data_dir=data_dir,
                remove_data=remove_data, pid=999999, notify=notify)
        script_path = os.path.join(tmp, "cc_uninstall.ps1")
        with open(script_path, encoding="utf-8") as f:
            script = f.read()
        return ok, script, app_dir, data_dir, popen

    def test_spawns_and_targets_app_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ok, script, app_dir, data_dir, popen = self._run(tmp, remove_data=False)
            self.assertTrue(ok)
            self.assertTrue(popen.called)
            # Always removes the program folder.
            self.assertIn(app_dir, script)
            # Waits on the given pid before deleting.
            self.assertIn("999999", script)

    def test_keep_data_leaves_data_dir_untouched(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _, script, app_dir, data_dir, _ = self._run(tmp, remove_data=False)
            self.assertNotIn(data_dir, script)

    def test_remove_data_includes_data_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _, script, app_dir, data_dir, _ = self._run(tmp, remove_data=True)
            self.assertIn(data_dir, script)

    def test_notify_toggles_messagebox(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _, with_msg, _, _, _ = self._run(tmp, remove_data=False, notify=True)
            self.assertIn("MessageBox", with_msg)
        with tempfile.TemporaryDirectory() as tmp:
            _, no_msg, _, _, _ = self._run(tmp, remove_data=False, notify=False)
            self.assertNotIn("MessageBox", no_msg)


if __name__ == "__main__":
    unittest.main()
