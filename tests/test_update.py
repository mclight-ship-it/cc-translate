"""Tests for the self-update decision helpers:
update_available, classify_update_state and _format_version.

The git/network calls (remote_head, _git, ...) are intentionally not tested
here against a live repo. The decision helpers and user-visible version label
must stay stable, so we cover them with mocked git responses.
"""
import unittest
import unittest.mock
import contextlib
import ctypes
import json
import os
import shutil
import subprocess
import struct
import sys
import tempfile
import time

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
        self.assertEqual(tr._cc_update._format_numeric_version(241), "5.7.241")

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

    def test_remote_version_uses_release_constants_from_remote_ref(self):
        cc = tr._cc_update
        source = "VERSION_MAJOR = 6\nVERSION_MINOR = 4\n"
        with unittest.mock.patch.object(
                cc, "_commit_count", return_value=321), \
                unittest.mock.patch.object(
                    cc, "_git", return_value=(0, source, "")):
            self.assertEqual(
                cc.remote_version_string("origin/master"), "6.4.321")

    def test_remote_version_falls_back_when_remote_source_is_unreadable(self):
        cc = tr._cc_update
        with unittest.mock.patch.object(
                cc, "_commit_count", return_value=321), \
                unittest.mock.patch.object(
                    cc, "_git", return_value=(1, "", "missing")):
            self.assertEqual(
                cc.remote_version_string("origin/master"), "5.7.321")


class TestBrandedLauncher(unittest.TestCase):
    @contextlib.contextmanager
    def _without_error_dialogs(self):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        set_mode = kernel32.SetErrorMode
        set_mode.argtypes = (ctypes.c_uint,)
        set_mode.restype = ctypes.c_uint
        previous = set_mode(0x0001 | 0x0002 | 0x8000)
        try:
            yield
        finally:
            set_mode(previous)

    def _isolated_environment(self):
        env = dict(os.environ)
        for name in list(env):
            if name.upper() in {"PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE",
                                "__PYVENV_LAUNCHER__"}:
                del env[name]
        env["PATH"] = os.path.join(os.environ["SystemRoot"], "System32")
        return env

    def _run_launcher(self, launcher, code):
        with self._without_error_dialogs():
            return subprocess.run(
                [launcher, "-B", "-c", code],
                env=self._isolated_environment(),
                cwd=os.environ["SystemRoot"],
                timeout=15, check=False, capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW)

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
            self.assertEqual(os.path.basename(result), "CCTranslate.exe")
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
            completed = self._run_launcher(result, code)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with open(marker, encoding="utf-8") as f:
                self.assertEqual(f.read(), "ok")

            updated = cc_launcher.ensure_branded_launcher(
                tr._cc_update.PYTHONW, tmp, "4.6.244",
                tr._cc_update.ICON_PATH)
            self.assertEqual(updated, result)
            self.assertEqual(
                cc_launcher.read_version_string(updated, "ProductVersion"),
                "4.5.243")
            legacy = os.path.join(tmp, "CCTranslate-4.4.242-deadbeef.exe")
            with open(legacy, "wb") as f:
                f.write(b"legacy")
            cc_launcher.cleanup_old_launchers(tmp, updated)
            self.assertTrue(os.path.exists(result))
            self.assertFalse(os.path.exists(legacy))

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher only")
    def test_runtime_binding_preserves_native_imports_and_repair_source(self):
        import cc_launcher
        with tempfile.TemporaryDirectory(prefix="cc launcher ") as tmp:
            launcher = cc_launcher.ensure_branded_launcher(
                tr._cc_update.PYTHONW, tmp, "5.7.1", tr._cc_update.ICON_PATH)
            marker = os.path.join(tmp, "runtime.json")
            code = (
                "import ctypes,json,sqlite3,ssl,sys,tkinter;"
                "from pathlib import Path;"
                "import PIL,pythoncom,win32api;"
                f"sys.path.insert(0, {tr.APP_DIR!r});"
                "import cc_update;"
                "result={'base_prefix':sys.base_prefix,"
                "'executable':sys.executable,'pythonw':cc_update.PYTHONW,"
                "'tcl':tkinter.Tcl().eval('info patchlevel')};"
                "sqlite3.connect(':memory:').close();"
                "ssl.create_default_context();"
                f"Path({marker!r}).write_text(json.dumps(result), encoding='utf-8')")
            completed = self._run_launcher(launcher, code)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with open(marker, encoding="utf-8") as source:
                result = json.load(source)
            self.assertEqual(
                os.path.normcase(result["base_prefix"]),
                os.path.normcase(sys.base_prefix))
            self.assertEqual(
                os.path.normcase(result["executable"]), os.path.normcase(launcher))
            self.assertEqual(result["pythonw"], tr._cc_update.PYTHONW)
            self.assertTrue(result["tcl"])

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher only")
    def test_repairs_legacy_launcher_without_replacing_its_identity(self):
        import cc_launcher
        with tempfile.TemporaryDirectory() as tmp:
            launcher = os.path.join(tmp, "CCTranslate.exe")
            shutil.copy2(tr._cc_update.PYTHONW, launcher)
            cc_launcher.set_version_resource(launcher, "4.11.253")
            cc_launcher.set_icon_resources(launcher, tr._cc_update.ICON_PATH)
            with open(launcher, "rb") as source:
                original = source.read()
            failed = self._run_launcher(launcher, "pass")
            self.assertEqual(failed.returncode & 0xFFFFFFFF, 0xC0000135)
            self.assertEqual(
                cc_launcher.ensure_branded_launcher(
                    tr._cc_update.PYTHONW, tmp, "5.7.1", tr._cc_update.ICON_PATH),
                launcher)
            with open(launcher, "rb") as source:
                self.assertEqual(source.read(), original)
            self.assertEqual(self._run_launcher(launcher, "pass").returncode, 0)
            os.remove(os.path.join(tmp, "python3.dll"))
            python_dll = next(
                name for name in cc_launcher._pe_identity(launcher)[1]
                if name.startswith("python") and name.endswith(".dll"))
            with open(os.path.join(tmp, python_dll), "wb") as target:
                target.write(b"damaged runtime")
            with open(os.path.join(tmp, "pyvenv.cfg"), "w", encoding="utf-8") as target:
                target.write("home = missing\n")
            cc_launcher.ensure_branded_launcher(
                tr._cc_update.PYTHONW, tmp, "5.7.1", tr._cc_update.ICON_PATH)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "python3.dll")))
            self.assertEqual(self._run_launcher(launcher, "pass").returncode, 0)

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher only")
    def test_existing_runtime_can_be_verified_while_launcher_is_running(self):
        import cc_launcher
        with tempfile.TemporaryDirectory() as tmp:
            launcher = cc_launcher.ensure_branded_launcher(
                tr._cc_update.PYTHONW, tmp, "5.7.1", tr._cc_update.ICON_PATH)
            marker = os.path.join(tmp, "ready")
            code = (
                "import sys;from pathlib import Path;"
                f"Path({marker!r}).touch();"
                "sys.stdin.read(1)")
            with self._without_error_dialogs(), subprocess.Popen(
                    [launcher, "-B", "-c", code],
                    env=self._isolated_environment(), cwd=os.environ["SystemRoot"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW) as process:
                try:
                    deadline = time.monotonic() + 10
                    while not os.path.exists(marker) and process.poll() is None:
                        if time.monotonic() >= deadline:
                            self.fail("launcher did not become ready")
                        time.sleep(0.02)
                    self.assertIsNone(process.poll())
                    self.assertTrue(os.path.exists(marker))
                    self.assertEqual(
                        cc_launcher.ensure_branded_launcher(
                            tr._cc_update.PYTHONW, tmp, "5.7.2",
                            tr._cc_update.ICON_PATH), launcher)
                finally:
                    process.communicate(input=b"x", timeout=15)
                self.assertEqual(process.returncode, 0)

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher only")
    def test_shortcut_starts_without_python_path(self):
        cc = tr._cc_update
        with tempfile.TemporaryDirectory(prefix="cc startup ' ") as tmp:
            host_dir = os.path.join(tmp, "host")
            script = os.path.join(tmp, "probe.pyw")
            marker = os.path.join(tmp, "started")
            link = os.path.join(tmp, "CC Translate.lnk")
            with open(script, "w", encoding="utf-8") as target:
                target.write(
                    f"from pathlib import Path\nPath({marker!r}).touch()\n")
            with unittest.mock.patch.object(cc, "LAUNCHER_DIR", host_dir), \
                    unittest.mock.patch.object(cc, "SCRIPT_PATH", script), \
                    unittest.mock.patch.object(cc, "APP_DIR", tmp), \
                    unittest.mock.patch.object(cc, "version_string", return_value="5.7.1"):
                cc._create_shortcut(link)
            powershell = os.path.join(
                os.environ["SystemRoot"], "System32", "WindowsPowerShell",
                "v1.0", "powershell.exe")
            with self._without_error_dialogs():
                completed = subprocess.run(
                    [powershell, "-NoProfile", "-NonInteractive", "-Command",
                     "$ErrorActionPreference='Stop';"
                     f"Start-Process -FilePath {cc._ps_squote(link)} -Wait"],
                    env=self._isolated_environment(), cwd=os.environ["SystemRoot"],
                    timeout=20, check=False, capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(os.path.isfile(marker))

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher only")
    def test_relauncher_prepares_runtime_before_spawning_waiter(self):
        cc = tr._cc_update
        with tempfile.TemporaryDirectory() as tmp:
            host_dir = os.path.join(tmp, "host")
            with unittest.mock.patch.object(cc, "LAUNCHER_DIR", host_dir), \
                    unittest.mock.patch.object(cc, "version_string", return_value="5.7.1"), \
                    unittest.mock.patch.object(cc.subprocess, "Popen") as popen:
                cc._spawn_relauncher(pid=999999, data_dir=tmp)
            popen.assert_called_once()
            with open(os.path.join(tmp, "_relaunch.ps1"), encoding="utf-8") as source:
                script = source.read()
            launcher = os.path.join(host_dir, "CCTranslate.exe")
            self.assertIn(f"Start-Process -FilePath {cc._ps_squote(launcher)}", script)
            self.assertEqual(self._run_launcher(launcher, "pass").returncode, 0)

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher only")
    def test_missing_source_runtime_fails_before_installing_launcher(self):
        import cc_launcher
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = os.path.join(tmp, "source")
            os.mkdir(source_dir)
            source = os.path.join(source_dir, "pythonw.exe")
            shutil.copy2(tr._cc_update.PYTHONW, source)
            target = os.path.join(tmp, "target")
            with self.assertRaisesRegex(FileNotFoundError, "runtime dependency"):
                cc_launcher.ensure_branded_launcher(source, target, "5.7.1")
            self.assertFalse(os.path.exists(target))

    def test_failed_companion_replacement_preserves_file_and_cleans_staging(self):
        import cc_launcher
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pyvenv.cfg")
            with open(path, "wb") as target:
                target.write(b"original")
            with unittest.mock.patch.object(
                    cc_launcher.os, "replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    cc_launcher._write_if_changed(path, b"replacement")
            with open(path, "rb") as source:
                self.assertEqual(source.read(), b"original")
            self.assertEqual(os.listdir(tmp), ["pyvenv.cfg"])

    def test_rejects_invalid_pe_header(self):
        import cc_launcher
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "invalid.exe")
            for payload in (b"", b"MZ", b"MZ" + b"\0" * 100):
                with self.subTest(payload=payload):
                    with open(path, "wb") as target:
                        target.write(payload)
                    with self.assertRaises(ValueError):
                        cc_launcher._pe_identity(path)


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
