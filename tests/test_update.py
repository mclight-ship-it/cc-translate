"""Tests for the self-update decision helpers:
update_available, classify_update_state and _format_version.

The git/network calls (remote_head, _git, ...) are intentionally not tested
here against a live repo. The decision helpers and user-visible version label
must stay stable, so we cover them with mocked git responses.
"""
import unittest
import unittest.mock
import os
import subprocess
import struct
import sys
import tempfile

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


class TestClassifyUpdateState(unittest.TestCase):
    def test_behind_when_remote_descends_from_local(self):
        cc = tr._cc_update
        with unittest.mock.patch.object(
                cc, "_git",
                side_effect=[(0, "abc123", ""), (0, "def456", ""),
                             (0, "", "")]):
            state, local, remote = cc.classify_update_state()
        self.assertEqual((state, local, remote), ("behind", "abc123", "def456"))

    def test_ahead_when_local_already_contains_remote(self):
        cc = tr._cc_update
        with unittest.mock.patch.object(
                cc, "_git",
                side_effect=[(0, "abc123", ""), (0, "def456", ""),
                             (1, "", ""), (0, "", "")]):
            state, local, remote = cc.classify_update_state()
        self.assertEqual((state, local, remote), ("ahead", "abc123", "def456"))

    def test_diverged_when_neither_side_contains_the_other(self):
        cc = tr._cc_update
        with unittest.mock.patch.object(
                cc, "_git",
                side_effect=[(0, "abc123", ""), (0, "def456", ""),
                             (1, "", ""), (1, "", "")]):
            state, local, remote = cc.classify_update_state()
        self.assertEqual((state, local, remote), ("diverged", "abc123", "def456"))

    def test_unknown_when_merge_base_errors(self):
        cc = tr._cc_update
        with unittest.mock.patch.object(
                cc, "_git",
                side_effect=[(0, "abc123", ""), (0, "def456", ""),
                             (128, "", "bad object")]):
            state, local, remote = cc.classify_update_state()
        self.assertEqual((state, local, remote), ("unknown", "abc123", "def456"))


class TestFormatVersion(unittest.TestCase):
    def test_numeric_version_uses_release_minor_and_build(self):
        self.assertEqual(tr._cc_update._format_numeric_version(241), "4.8.241")

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


class TestBrandedLauncher(unittest.TestCase):
    def test_version_resource_contains_product_identity(self):
        import cc_launcher
        payload = cc_launcher.build_version_resource("4.5.243")
        self.assertEqual(len(payload) % 4, 0)
        self.assertEqual(int.from_bytes(payload[:2], "little"), len(payload))
        self.assertIn("CC Translate".encode("utf-16le"), payload)
        self.assertIn("CCTranslate.exe".encode("utf-16le"), payload)

    def test_icon_resource_contains_every_ico_image(self):
        import cc_launcher
        group, images = cc_launcher.build_icon_resources(
            tr._cc_update.ICON_PATH)
        self.assertEqual(struct.unpack_from("<HHH", group), (0, 1, len(images)))
        self.assertEqual(len(group), 6 + 14 * len(images))
        self.assertGreaterEqual(len(images), 1)
        for index, (resource_id, payload) in enumerate(images, 1):
            self.assertEqual(resource_id, index)
            self.assertTrue(payload)

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher only")
    def test_generated_launcher_is_branded_and_runs_python(self):
        import cc_launcher
        with tempfile.TemporaryDirectory() as tmp:
            result = cc_launcher.ensure_branded_launcher(
                tr._cc_update.PYTHONW, tmp, "4.5.243",
                tr._cc_update.ICON_PATH)
            self.assertTrue(result.startswith(tmp))
            self.assertEqual(
                cc_launcher.read_file_description(result), "CC Translate")
            self.assertEqual(
                cc_launcher.read_version_string(result, "ProductVersion"),
                "4.5.243")
            self.assertTrue(cc_launcher.launcher_has_icon(
                result, tr._cc_update.ICON_PATH))

            marker = os.path.join(tmp, "ran.txt")
            code = (
                "from pathlib import Path;"
                f"Path({marker!r}).write_text('ok', encoding='utf-8')")
            completed = subprocess.run(
                [result, "-c", code], timeout=15, check=False)
            self.assertEqual(completed.returncode, 0)
            with open(marker, encoding="utf-8") as f:
                self.assertEqual(f.read(), "ok")

            updated = cc_launcher.ensure_branded_launcher(
                tr._cc_update.PYTHONW, tmp, "4.6.244",
                tr._cc_update.ICON_PATH)
            self.assertNotEqual(updated, result)
            self.assertEqual(
                cc_launcher.read_version_string(updated, "ProductVersion"),
                "4.6.244")
            self.assertTrue(os.path.exists(result))
            cc_launcher.cleanup_old_launchers(tmp, updated)
            self.assertFalse(os.path.exists(result))


class TestAutostartMigration(unittest.TestCase):
    def test_failed_replacement_keeps_legacy_launcher(self):
        cc = tr._cc_update
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "QuickTranslate.vbs")
            startup = os.path.join(tmp, "CC Translate.lnk")
            with open(legacy, "w", encoding="utf-8") as f:
                f.write("legacy")
            with unittest.mock.patch.object(
                    cc, "LEGACY_STARTUP_VBS", legacy), \
                    unittest.mock.patch.object(cc, "STARTUP_LNK", startup), \
                    unittest.mock.patch.object(
                        cc, "_create_shortcut",
                        side_effect=OSError("shortcut failed")):
                self.assertFalse(cc.set_autostart(True))
            self.assertTrue(os.path.exists(legacy))
            self.assertFalse(os.path.exists(startup))

    def test_successful_replacement_removes_legacy_launcher(self):
        cc = tr._cc_update
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "QuickTranslate.vbs")
            startup = os.path.join(tmp, "CC Translate.lnk")
            with open(legacy, "w", encoding="utf-8") as f:
                f.write("legacy")

            def create(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("shortcut")

            with unittest.mock.patch.object(
                    cc, "LEGACY_STARTUP_VBS", legacy), \
                    unittest.mock.patch.object(cc, "STARTUP_LNK", startup), \
                    unittest.mock.patch.object(
                        cc, "_create_shortcut", side_effect=create):
                self.assertTrue(cc.set_autostart(True))
            self.assertFalse(os.path.exists(legacy))
            self.assertTrue(os.path.exists(startup))


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
