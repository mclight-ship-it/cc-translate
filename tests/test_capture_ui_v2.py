"""Pure tests for the development-only UI v2 capture tool."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock


_TOOL_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "capture_ui_v2.py")
_SPEC = importlib.util.spec_from_file_location("capture_ui_v2", _TOOL_PATH)
capture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(capture)


class TestCaptureUiV2(unittest.TestCase):
    def test_pillow_is_required_only_when_capturing(self):
        with mock.patch.object(capture, "Image", None):
            with self.assertRaisesRegex(RuntimeError, "Pillow is required"):
                capture._require_pillow()

    def test_manifest_artifact_paths_are_portable(self):
        output_dir = Path("C:/temp/baseline")
        path = output_dir / "nested" / "settings-dark.png"
        self.assertEqual(
            capture._manifest_artifact_path(output_dir, path),
            "nested/settings-dark.png",
        )

    def test_loading_surface_is_registered_for_cleanup(self):
        win = object()
        app = SimpleNamespace(
            popup=None,
            _make_loading_popup=lambda: win,
        )
        self.assertIs(capture._build_surface(None, app, "loading"), win)
        self.assertIs(app.popup, win)

    def test_result_and_error_capture_titles_follow_language(self):
        calls = []
        i18n = SimpleNamespace(
            get=lambda key: {
                "result.title": "Translation",
                "error.title": "Error",
            }[key],
            get_language=lambda: "en_US",
        )
        tr = SimpleNamespace(i18n=i18n)
        app = SimpleNamespace(
            popup=None,
            _make_popup=lambda message, **kwargs: (
                calls.append((message, kwargs)) or object()),
        )
        capture._build_surface(tr, app, "result")
        capture._build_surface(tr, app, "error")
        self.assertEqual(calls[0][1]["title"], "Translation")
        self.assertEqual(calls[1][1]["title"], "Error")
        self.assertTrue(calls[1][0].startswith("Unable to connect"))

    def test_capture_app_initializes_resize_state(self):
        app = SimpleNamespace()
        capture._initialize_window_state(app)
        self.assertIsNone(app._resize_mode)
        self.assertIsNone(app._resize_start)

    def test_load_path_includes_repository_when_launched_from_tools(self):
        app_path = os.fspath(capture.APP_DIR)
        original_path = sys.path[:]
        try:
            sys.path = [item for item in sys.path if item != app_path]
            capture._ensure_app_import_path()
            self.assertEqual(sys.path[0], app_path)
        finally:
            sys.path = original_path

    def test_parse_all_surfaces(self):
        self.assertEqual(capture.parse_surfaces(["all"]), list(capture.SURFACES))

    def test_parse_deduplicates_comma_separated_surfaces(self):
        self.assertEqual(
            capture.parse_surfaces(["settings,result", "settings"]),
            ["settings", "result"],
        )

    def test_parse_rejects_unknown_surface(self):
        with self.assertRaises(ValueError):
            capture.parse_surfaces(["clipboard"])

    def test_both_themes_are_deterministic(self):
        self.assertEqual(capture.parse_themes("both"), ["dark", "light"])

    def test_synthetic_history_contains_no_external_data(self):
        entries = capture.synthetic_history()
        self.assertEqual({entry["sig"] for entry in entries},
                         {"capture-fixture"})
        self.assertEqual({entry["kind"] for entry in entries},
                         {"text", "dict", "code"})
        self.assertTrue(all(
            entry["ts"].startswith("2000-") for entry in entries))

    def test_runtime_overrides_force_v2_and_hide_host_state(self):
        env_name = "CC_UI_V2_CAPTURE_TEST"
        original_history = lambda: ["host history"]
        original_autostart = lambda: True
        tr = SimpleNamespace(
            UI_V2_ENV=env_name,
            load_history=original_history,
        )
        settings_module = SimpleNamespace(
            is_autostart_enabled=original_autostart,
        )
        with mock.patch.dict(os.environ, {env_name: "0"}, clear=False):
            with capture._capture_runtime_overrides(tr, settings_module):
                self.assertEqual(os.environ[env_name], "1")
                self.assertIs(tr.load_history, capture.synthetic_history)
                self.assertFalse(settings_module.is_autostart_enabled())
            self.assertEqual(os.environ[env_name], "0")
        self.assertIs(tr.load_history, original_history)
        self.assertIs(
            settings_module.is_autostart_enabled, original_autostart)

    def test_runtime_overrides_remove_temporary_v2_environment(self):
        env_name = "CC_UI_V2_CAPTURE_TEST_MISSING"
        tr = SimpleNamespace(UI_V2_ENV=env_name, load_history=lambda: [])
        settings_module = SimpleNamespace(is_autostart_enabled=lambda: True)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(env_name, None)
            with capture._capture_runtime_overrides(tr, settings_module):
                self.assertEqual(os.environ[env_name], "1")
            self.assertNotIn(env_name, os.environ)

    def test_diagnostics_is_part_of_full_capture(self):
        self.assertIn("diagnostics", capture.parse_surfaces(["all"]))

    def test_ocr_overlay_is_part_of_full_capture(self):
        self.assertIn("ocr-overlay", capture.parse_surfaces(["all"]))

    def test_about_secondary_windows_are_part_of_full_capture(self):
        surfaces = capture.parse_surfaces(["all"])
        self.assertIn("support-author", surfaces)
        self.assertIn("uninstall", surfaces)

    def test_synthetic_ocr_screen_is_private_and_deterministic(self):
        image = capture.synthetic_ocr_screen()
        self.assertEqual(image.size, (960, 600))
        self.assertNotEqual(image.getpixel((0, 0)), image.getpixel((959, 599)))

    def test_expand_box_clamps_to_image(self):
        self.assertEqual(
            capture.expand_box((10, 20, 90, 80), 30, (100, 100)),
            (0, 0, 100, 100),
        )

    def test_settings_crop_includes_left_column_controls(self):
        self.assertEqual(
            capture.settings_left_column_crop_box(
                (53, 102, 267, 394), 28, (1327, 920)),
            (25, 74, 663, 422),
        )

    def test_default_output_is_outside_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_tempdir = tempfile.tempdir
            try:
                tempfile.tempdir = temp_dir
                output = capture.default_output_dir()
            finally:
                tempfile.tempdir = old_tempdir
        self.assertTrue(str(output).startswith(temp_dir))
        self.assertNotIn(str(capture.APP_DIR), str(output))


if __name__ == "__main__":
    unittest.main()
