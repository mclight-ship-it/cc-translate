"""Comprehensive functional test suite for CC Translate.

Covers the areas NOT exercised by the existing unit tests:
  - Module structure, imports, and cross-module wiring (log_error injection)
  - CFG constant completeness and correctness
  - StreamSession dataclass
  - Config persistence: load/save round-trip, defaults, missing-file grace
  - History I/O: add / load / clear / limit enforcement / is_dict flag
  - Theme resolution: dark / light / system mode, required colour keys
  - Direction & language modes completeness
  - Popup layout labels consistency with CFG values
  - Rich-text edge cases: empty, all-whitespace, single char, deeply nested
  - cc_warm: CLAUDE_CMD shape, constants sanity, WarmClaude instantiation
  - cc_update: SCRIPT_PATH sanity, PYTHONW shape, version_string non-empty
  - log_error wiring: both sub-modules use the app-level function
  - code_ratio boundary values around the mixed/pure thresholds
  - resolve_theme returns a dict with all required colour keys
  - DEFAULT_CONFIG completeness (every CFG.* attribute present)
"""
import gc
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock
from datetime import datetime

from tests._tr import tr


# ============================================================
# Helpers
# ============================================================

# All colour keys that every theme dict must provide.
REQUIRED_THEME_KEYS = {
    "bg", "fg", "bar_bg", "btn_bg", "btn_active", "btn_close_active",
    "border", "sel_bg", "popup_bg", "popup_border", "popup_hint", "accent",
    "scroll_thumb", "scroll_thumb_active", "trough", "hint_fg",
    "settings_bg", "settings_fg", "list_bg", "list_sel",
    "status_ok", "status_err",
    "rich_code_fg", "rich_code_bg",
    "rich_heading_fg", "rich_bold_fg",
    "rich_url_fg", "rich_bullet_fg",
    "rich_ident_fg", "rich_string_fg", "rich_number_fg",
}


# ============================================================
# Module structure & cross-module wiring
# ============================================================

class TestModuleImports(unittest.TestCase):
    def test_cc_rich_importable(self):
        import cc_rich
        self.assertTrue(hasattr(cc_rich, "iter_rich_segments"))
        self.assertTrue(hasattr(cc_rich, "highlight_code"))
        self.assertTrue(hasattr(cc_rich, "_PYGMENTS_OK"))

    def test_cc_warm_importable(self):
        import cc_warm
        self.assertTrue(hasattr(cc_warm, "WarmClaude"))
        self.assertTrue(hasattr(cc_warm, "CLAUDE_CMD"))
        self.assertTrue(hasattr(cc_warm, "WARM_POOL_ENABLED"))

    def test_cc_update_importable(self):
        import cc_update
        self.assertTrue(hasattr(cc_update, "update_available"))
        self.assertTrue(hasattr(cc_update, "version_string"))
        self.assertTrue(hasattr(cc_update, "SCRIPT_PATH"))

    def test_log_error_wired_into_cc_warm(self):
        """cc_warm._log_error must be the app-level log_error, not the no-op."""
        import cc_warm
        self.assertIs(cc_warm._log_error, tr.log_error,
                      "cc_warm._log_error should be wired to translator.log_error")

    def test_log_error_wired_into_cc_update(self):
        import cc_update
        self.assertIs(cc_update._log_error, tr.log_error,
                      "cc_update._log_error should be wired to translator.log_error")

    def test_symbols_re_exported_in_translator(self):
        """Key symbols from sub-modules must be accessible via tr.* (tests rely on this)."""
        for attr in ("iter_rich_segments", "highlight_code", "_PYGMENTS_OK",
                     "WarmClaude", "CLAUDE_CMD",
                     "update_available", "version_string", "_format_version",
                     "is_git_deploy"):
            self.assertTrue(hasattr(tr, attr), f"tr.{attr} missing")


# ============================================================
# CFG constants
# ============================================================

class TestCFGConstants(unittest.TestCase):
    _EXPECTED_KEYS = {
        "MODEL", "MODEL_PROVIDER", "CLAUDE_MODEL", "CODEX_MODEL",
        "CODEX_STREAMING_EXPERIMENTAL",
        "DOUBLE_PRESS_WINDOW", "FONT_SIZE", "DIRECTION",
        "MAX_CHARS", "THEME", "POPUP_LAYOUT",
        "HISTORY_ENABLED", "HISTORY_LIMIT",
        "AUTO_UPDATE_ENABLED", "AUTO_UPDATE_HOUR",
        "OCR_ENGINE", "OCR_HOTKEY_ENABLED",
        "CLIPBOARD_PROTECTION_ENABLED",
        "AUTOSTART_INITIALIZED",
        "SUMMARY_ENABLED",
        "TRAY_CLICK_ACTION",
        "UI_V2",
    }

    def test_cfg_has_all_attributes(self):
        for k in self._EXPECTED_KEYS:
            self.assertTrue(hasattr(tr.CFG, k), f"CFG.{k} missing")

    def test_cfg_values_are_strings(self):
        for k in self._EXPECTED_KEYS:
            v = getattr(tr.CFG, k)
            self.assertIsInstance(v, str, f"CFG.{k} should be a string, got {type(v)}")

    def test_cfg_values_are_unique(self):
        values = [getattr(tr.CFG, k) for k in self._EXPECTED_KEYS]
        self.assertEqual(len(values), len(set(values)),
                         "CFG values must all be distinct")

    def test_default_config_uses_all_cfg_keys(self):
        cfg_values = {getattr(tr.CFG, k) for k in self._EXPECTED_KEYS}
        for v in cfg_values:
            self.assertIn(v, tr.DEFAULT_CONFIG,
                          f"DEFAULT_CONFIG missing key '{v}' (CFG.{v})")

    def test_default_config_has_no_extra_keys(self):
        cfg_values = {getattr(tr.CFG, k) for k in self._EXPECTED_KEYS}
        for k in tr.DEFAULT_CONFIG:
            self.assertIn(k, cfg_values,
                          f"DEFAULT_CONFIG has key '{k}' not in CFG class")

    def test_default_values_reasonable(self):
        dc = tr.DEFAULT_CONFIG
        self.assertIsInstance(dc[tr.CFG.FONT_SIZE], int)
        self.assertGreater(dc[tr.CFG.FONT_SIZE], 0)
        self.assertIsInstance(dc[tr.CFG.DOUBLE_PRESS_WINDOW], float)
        self.assertGreater(dc[tr.CFG.DOUBLE_PRESS_WINDOW], 0)
        self.assertIsInstance(dc[tr.CFG.MAX_CHARS], int)
        self.assertGreater(dc[tr.CFG.MAX_CHARS], 0)
        self.assertIsInstance(dc[tr.CFG.HISTORY_ENABLED], bool)
        self.assertIsInstance(dc[tr.CFG.AUTO_UPDATE_ENABLED], bool)

    def test_release_defaults_use_smart_codex_streaming_and_font_10(self):
        self.assertIs(
            tr.DEFAULT_CONFIG[tr.CFG.CODEX_STREAMING_EXPERIMENTAL], True)
        self.assertEqual(
            tr.DEFAULT_CONFIG[tr.CFG.CODEX_MODEL], "auto-fast")
        self.assertEqual(tr.DEFAULT_CONFIG[tr.CFG.FONT_SIZE], 10)


# ============================================================
# v2 UI dark-launch flag
# ============================================================

class TestUiV2Flag(unittest.TestCase):
    def setUp(self):
        # Never let an ambient CC_UI_V2 in the dev shell leak into assertions.
        self._saved_env = os.environ.pop(tr.UI_V2_ENV, None)

    def tearDown(self):
        os.environ.pop(tr.UI_V2_ENV, None)
        if self._saved_env is not None:
            os.environ[tr.UI_V2_ENV] = self._saved_env

    def test_defaults_off_in_production(self):
        # Ships to everyone via auto-update but must stay dark by default.
        self.assertFalse(tr.DEFAULT_CONFIG[tr.CFG.UI_V2])
        self.assertFalse(tr.ui_v2_enabled({}))
        self.assertFalse(tr.ui_v2_enabled(None))

    def test_config_opt_in_enables(self):
        self.assertTrue(tr.ui_v2_enabled({tr.CFG.UI_V2: True}))
        self.assertFalse(tr.ui_v2_enabled({tr.CFG.UI_V2: False}))

    def test_env_forces_on_regardless_of_config(self):
        for val in ("1", "true", "yes", "on", "TRUE", "On"):
            os.environ[tr.UI_V2_ENV] = val
            self.assertTrue(
                tr.ui_v2_enabled({tr.CFG.UI_V2: False}),
                f"CC_UI_V2={val!r} should force the v2 UI on")

    def test_env_forces_off_over_config(self):
        # An explicit off in the env beats an opt-in config, so a developer can
        # pin the legacy UI for one run even if the setting is saved on.
        for val in ("0", "false", "no", "off", ""):
            os.environ[tr.UI_V2_ENV] = val
            self.assertFalse(
                tr.ui_v2_enabled({tr.CFG.UI_V2: True}),
                f"CC_UI_V2={val!r} should force the v2 UI off")

    def test_unrecognized_env_falls_through_to_config(self):
        os.environ[tr.UI_V2_ENV] = "maybe"
        self.assertTrue(tr.ui_v2_enabled({tr.CFG.UI_V2: True}))
        self.assertFalse(tr.ui_v2_enabled({tr.CFG.UI_V2: False}))


# ============================================================
# StreamSession dataclass
# ============================================================

class TestStreamSession(unittest.TestCase):
    def test_default_construction(self):
        ss = tr.StreamSession()
        self.assertFalse(ss.popup_ready)
        self.assertEqual(ss.accum, "")
        self.assertIsNone(ss.flush_job)
        self.assertEqual(ss.cols, 0)
        self.assertEqual(ss.fixed_w, 0)
        self.assertEqual(ss.max_h, 0)
        self.assertIsNone(ss.origin_x)
        self.assertIsNone(ss.origin_y)
        self.assertIsNone(ss.monitor_rect)

    def test_queue_is_fresh_per_instance(self):
        import queue
        ss1 = tr.StreamSession()
        ss2 = tr.StreamSession()
        self.assertIsNot(ss1.queue, ss2.queue,
                         "Each StreamSession must get an independent queue")
        ss1.queue.put("x")
        self.assertTrue(ss2.queue.empty(), "Queues must not be shared")

    def test_field_mutation(self):
        ss = tr.StreamSession()
        ss.accum = "hello"
        ss.cols = 42
        ss.popup_ready = True
        self.assertEqual(ss.accum, "hello")
        self.assertEqual(ss.cols, 42)
        self.assertTrue(ss.popup_ready)


# ============================================================
# Config persistence
# ============================================================

class TestConfigPersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8")
        self._path = self._tmp.name
        self._tmp.close()
        self._orig_path = tr.CONFIG_PATH

    def tearDown(self):
        tr.CONFIG_PATH = self._orig_path
        try:
            os.unlink(self._path)
        except Exception:
            pass

    def _patch_config_path(self, path):
        tr.CONFIG_PATH = path

    def test_load_missing_file_returns_defaults(self):
        missing = self._path + "_does_not_exist.json"
        self._patch_config_path(missing)
        cfg = tr.load_config()
        for k, v in tr.DEFAULT_CONFIG.items():
            self.assertIn(k, cfg)
            self.assertEqual(cfg[k], v,
                             f"Default for '{k}' should be {v!r}, got {cfg[k]!r}")

    def test_save_and_reload_round_trip(self):
        self._patch_config_path(self._path)
        original = dict(tr.DEFAULT_CONFIG)
        original[tr.CFG.FONT_SIZE] = 16
        original[tr.CFG.DIRECTION] = "to_en"
        original[tr.CFG.THEME] = "dark"
        tr.save_config(original)
        loaded = tr.load_config()
        self.assertEqual(loaded[tr.CFG.FONT_SIZE], 16)
        self.assertEqual(loaded[tr.CFG.DIRECTION], "to_en")
        self.assertEqual(loaded[tr.CFG.THEME], "dark")

    def test_corrupt_json_falls_back_to_defaults(self):
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("{broken json !!!}")
        self._patch_config_path(self._path)
        cfg = tr.load_config()
        self.assertIsInstance(cfg, dict)
        self.assertIn(tr.CFG.MODEL, cfg)

    def test_partial_config_merges_with_defaults(self):
        """A config file with only some keys set leaves the rest at defaults."""
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({tr.CFG.THEME: "dark"}, f)
        self._patch_config_path(self._path)
        cfg = tr.load_config()
        self.assertEqual(cfg[tr.CFG.THEME], "dark")
        self.assertEqual(cfg[tr.CFG.MODEL], tr.DEFAULT_CONFIG[tr.CFG.MODEL])
        self.assertIs(cfg[tr.CFG.CODEX_STREAMING_EXPERIMENTAL], True)
        self.assertEqual(cfg[tr.CFG.FONT_SIZE], 10)

    def test_saved_release_settings_override_new_defaults(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({
                tr.CFG.CODEX_STREAMING_EXPERIMENTAL: False,
                tr.CFG.FONT_SIZE: 16,
            }, f)
        self._patch_config_path(self._path)

        cfg = tr.load_config()

        self.assertIs(cfg[tr.CFG.CODEX_STREAMING_EXPERIMENTAL], False)
        self.assertEqual(cfg[tr.CFG.FONT_SIZE], 16)

    def test_legacy_model_migrates_to_claude_model(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({tr.CFG.MODEL: "opus"}, f)
        self._patch_config_path(self._path)
        cfg = tr.load_config()
        self.assertEqual(cfg[tr.CFG.MODEL_PROVIDER], "claude_cli")
        self.assertEqual(cfg[tr.CFG.CLAUDE_MODEL], "opus")
        self.assertEqual(cfg[tr.CFG.MODEL], "opus")


class TestConfigWrapper(unittest.TestCase):
    """The Config wrapper must stay a drop-in dict while adding coercion and
    typed accessors."""

    def test_config_is_a_dict_subclass(self):
        cfg = tr.Config()
        self.assertIsInstance(cfg, dict)

    def test_defaults_present_when_empty(self):
        cfg = tr.Config()
        for k, v in tr.DEFAULT_CONFIG.items():
            self.assertEqual(cfg[k], v)

    def test_unknown_keys_preserved(self):
        cfg = tr.Config({"future_flag": "keep me"})
        self.assertEqual(cfg["future_flag"], "keep me")

    def test_json_serializable(self):
        # save_config json.dumps the config; a dict subclass must serialize.
        cfg = tr.Config({tr.CFG.THEME: "dark"})
        restored = json.loads(json.dumps(cfg))
        self.assertEqual(restored[tr.CFG.THEME], "dark")

    def test_coerces_bool_from_int(self):
        cfg = tr.Config({tr.CFG.SUMMARY_ENABLED: 1})
        self.assertIs(cfg[tr.CFG.SUMMARY_ENABLED], True)

    def test_coerces_bool_from_string(self):
        cfg = tr.Config({tr.CFG.HISTORY_ENABLED: "false"})
        self.assertIs(cfg[tr.CFG.HISTORY_ENABLED], False)

    def test_coerces_int_from_numeric_string(self):
        cfg = tr.Config({tr.CFG.FONT_SIZE: "16"})
        self.assertEqual(cfg[tr.CFG.FONT_SIZE], 16)
        self.assertIsInstance(cfg[tr.CFG.FONT_SIZE], int)

    def test_bad_value_falls_back_to_default(self):
        cfg = tr.Config({tr.CFG.MAX_CHARS: "not-a-number"})
        self.assertEqual(cfg[tr.CFG.MAX_CHARS],
                         tr.DEFAULT_CONFIG[tr.CFG.MAX_CHARS])

    def test_typed_accessors_match_dict(self):
        cfg = tr.Config({tr.CFG.MODEL: "sonnet", tr.CFG.THEME: "light"})
        self.assertEqual(cfg.model, "sonnet")
        self.assertEqual(cfg.theme, "light")
        self.assertEqual(cfg.max_chars, cfg[tr.CFG.MAX_CHARS])

    def test_language_absent_by_default(self):
        # LANGUAGE is intentionally not in DEFAULT_CONFIG (set on first launch).
        cfg = tr.Config()
        self.assertIsNone(cfg.language)

    def test_unknown_provider_is_preserved_for_explicit_error(self):
        cfg = tr.Config({tr.CFG.MODEL_PROVIDER: "not-a-provider"})
        self.assertEqual(cfg[tr.CFG.MODEL_PROVIDER], "not-a-provider")

    def test_explicit_claude_model_keeps_legacy_model_synchronized(self):
        cfg = tr.Config({
            tr.CFG.MODEL: "haiku",
            tr.CFG.CLAUDE_MODEL: "sonnet",
        })
        self.assertEqual(cfg[tr.CFG.CLAUDE_MODEL], "sonnet")
        self.assertEqual(cfg[tr.CFG.MODEL], "sonnet")

    def test_legacy_explicit_mini_migrates_to_smart_routing(self):
        cfg = tr.Config({
            tr.CFG.CODEX_MODEL: "gpt-5.4-mini",
        })

        self.assertEqual(cfg[tr.CFG.CODEX_MODEL], "auto-fast")


# ============================================================
# History I/O
# ============================================================
class TestHistoryIO(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8")
        self._path = self._tmp.name
        self._tmp.close()
        os.unlink(self._path)   # start with no file (missing = empty history)
        self._orig_path = tr.HISTORY_PATH

    def tearDown(self):
        tr.HISTORY_PATH = self._orig_path
        try:
            os.unlink(self._path)
        except Exception:
            pass

    def _use_tmp(self):
        tr.HISTORY_PATH = self._path

    def test_load_missing_returns_empty_list(self):
        tr.HISTORY_PATH = self._path + "_missing"
        entries = tr.load_history()
        self.assertEqual(entries, [])

    def test_add_and_load(self):
        self._use_tmp()
        tr.add_history("hello", "你好", False, 100)
        entries = tr.load_history()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["input"], "hello")
        self.assertEqual(entries[0]["output"], "你好")
        self.assertFalse(entries[0]["is_dict"])
        self.assertIn("ts", entries[0])

    def test_add_dict_entry_flag(self):
        self._use_tmp()
        tr.add_history("serendipity", "意外之喜", True, 100, is_code=False)
        e = tr.load_history()[0]
        self.assertTrue(e["is_dict"])
        self.assertFalse(e["is_code"])

    def test_add_code_entry_flag(self):
        self._use_tmp()
        tr.add_history("def f(): pass", "# 函数定义", False, 100, is_code=True)
        e = tr.load_history()[0]
        self.assertTrue(e["is_code"])

    def test_add_custom_kind_entry(self):
        self._use_tmp()
        tr.add_history("", "截图结果", False, 100, kind="ocr")
        e = tr.load_history()[0]
        self.assertEqual(e["kind"], "ocr")

    def test_newest_entry_is_first(self):
        self._use_tmp()
        tr.add_history("first", "第一", False, 100)
        tr.add_history("second", "第二", False, 100)
        entries = tr.load_history()
        self.assertEqual(entries[0]["input"], "second")
        self.assertEqual(entries[1]["input"], "first")

    def test_limit_is_enforced(self):
        self._use_tmp()
        for i in range(10):
            tr.add_history(f"input{i}", f"output{i}", False, 5)
        entries = tr.load_history()
        self.assertLessEqual(len(entries), 5)

    def test_clear_history_removes_file(self):
        self._use_tmp()
        tr.add_history("x", "y", False, 100)
        tr.clear_history()
        self.assertFalse(os.path.exists(self._path),
                         "clear_history should remove the history file")
        self.assertEqual(tr.load_history(), [])

    def test_clear_history_logs_on_failure(self):
        # A failed removal must leave a trace (log_error) rather than vanish.
        self._use_tmp()
        tr.add_history("x", "y", False, 100)
        with unittest.mock.patch.object(tr.os, "remove",
                                        side_effect=OSError("locked")), \
                unittest.mock.patch.object(tr, "log_error") as log_error:
            tr.clear_history()
        log_error.assert_called_once()
        self.assertEqual(log_error.call_args.args[0], "clear_history")

    def test_load_corrupt_returns_empty(self):
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("not valid json at all")
        tr.HISTORY_PATH = self._path
        entries = tr.load_history()
        self.assertEqual(entries, [])

    def test_load_non_list_returns_empty(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"not": "a list"}, f)
        tr.HISTORY_PATH = self._path
        entries = tr.load_history()
        self.assertEqual(entries, [])


# ============================================================
# Theme resolution
# ============================================================

class TestThemeResolution(unittest.TestCase):
    def _make_cfg(self, theme_val):
        cfg = dict(tr.DEFAULT_CONFIG)
        cfg[tr.CFG.THEME] = theme_val
        return cfg

    def test_dark_theme_has_required_keys(self):
        theme = tr.resolve_theme(self._make_cfg("dark"))
        for k in REQUIRED_THEME_KEYS:
            self.assertIn(k, theme, f"dark theme missing key '{k}'")

    def test_light_theme_has_required_keys(self):
        theme = tr.resolve_theme(self._make_cfg("light"))
        for k in REQUIRED_THEME_KEYS:
            self.assertIn(k, theme, f"light theme missing key '{k}'")

    def test_system_theme_resolves_to_dict(self):
        theme = tr.resolve_theme(self._make_cfg("system"))
        self.assertIsInstance(theme, dict)
        for k in REQUIRED_THEME_KEYS:
            self.assertIn(k, theme, f"system theme missing key '{k}'")

    def test_unknown_theme_does_not_crash(self):
        # Falling back to any valid theme is acceptable; must not raise.
        theme = tr.resolve_theme(self._make_cfg("neon_pink_does_not_exist"))
        self.assertIsInstance(theme, dict)

    def test_dark_bg_is_darker_than_light(self):
        dark = tr.resolve_theme(self._make_cfg("dark"))
        light = tr.resolve_theme(self._make_cfg("light"))
        # A crude but solid invariant: dark bg starts with # and has a lower
        # average channel value than light bg.
        def avg_rgb(hex_str):
            h = hex_str.lstrip("#")
            return sum(int(h[i:i+2], 16) for i in (0, 2, 4)) / 3
        self.assertLess(avg_rgb(dark["bg"]), avg_rgb(light["bg"]))


class TestAdaptiveTrayIcon(unittest.TestCase):
    """The tray icon adapts to the taskbar (light/dark) theme."""

    def test_detect_taskbar_theme_returns_valid(self):
        self.assertIn(tr.detect_taskbar_theme(), ("light", "dark"))

    def test_icon_files_exist(self):
        for path in (tr.ICON_PATH_DARK, tr.ICON_PATH_LIGHT):
            self.assertTrue(os.path.exists(path),
                            f"missing tray icon file: {path}")

    def test_tray_icon_path_picks_matching_theme(self):
        # Each taskbar theme shows the *opposite* tile for contrast: a light
        # taskbar gets the dark tile and a dark taskbar gets the light tile.
        self.assertEqual(tr.tray_icon_path("light"), tr.ICON_PATH_DARK)
        self.assertEqual(tr.tray_icon_path("dark"), tr.ICON_PATH_LIGHT)

    def test_tray_icon_path_falls_back_to_tile(self):
        # If the theme-specific file is missing, fall back to cc.ico, else None.
        real_exists = os.path.exists

        def fake_exists(p):
            if p in (tr.ICON_PATH_DARK, tr.ICON_PATH_LIGHT):
                return False
            return real_exists(p)

        with unittest.mock.patch("os.path.exists", side_effect=fake_exists):
            expected = tr.ICON_PATH if real_exists(tr.ICON_PATH) else None
            self.assertEqual(tr.tray_icon_path("light"), expected)

    def test_icon_files_are_valid_multisize_icos(self):
        from PIL import Image
        for path in (tr.ICON_PATH_DARK, tr.ICON_PATH_LIGHT):
            with Image.open(path) as im:
                self.assertEqual(im.format, "ICO")
                sizes = im.info.get("sizes", set())
                self.assertIn((16, 16), sizes)
                self.assertIn((32, 32), sizes)


# ============================================================
# Direction / language modes
# ============================================================

class TestDirectionModes(unittest.TestCase):
    def test_auto_mode_present(self):
        self.assertIn("auto", tr.DIRECTION_MODES)
        self.assertIn("auto", tr.DIRECTION_LABELS)

    def test_all_languages_have_mode_and_label(self):
        for code in tr.LANGUAGES:
            key = f"to_{code}"
            self.assertIn(key, tr.DIRECTION_MODES,
                          f"DIRECTION_MODES missing key '{key}'")
            self.assertIn(key, tr.DIRECTION_LABELS,
                          f"DIRECTION_LABELS missing key '{key}'")

    def test_mode_and_label_keys_are_identical(self):
        self.assertEqual(set(tr.DIRECTION_MODES.keys()),
                         set(tr.DIRECTION_LABELS.keys()))

    def test_all_mode_values_are_non_empty_strings(self):
        for k, v in tr.DIRECTION_MODES.items():
            self.assertIsInstance(v, str, f"DIRECTION_MODES[{k!r}] is not a str")
            self.assertGreater(len(v.strip()), 0,
                               f"DIRECTION_MODES[{k!r}] is empty")

    def test_popup_layout_labels_match_cfg_values(self):
        valid_layouts = set(tr.POPUP_LAYOUT_LABELS.keys())
        default_layout = tr.DEFAULT_CONFIG[tr.CFG.POPUP_LAYOUT]
        self.assertIn(default_layout, valid_layouts,
                      "DEFAULT_CONFIG popup_layout not in POPUP_LAYOUT_LABELS")

    def test_popup_layout_default_is_dynamic(self):
        # The default popup layout is follow-cursor ("dynamic"); the classic
        # centered mode is opt-in.
        self.assertEqual(tr.DEFAULT_CONFIG[tr.CFG.POPUP_LAYOUT], "dynamic")

    def test_popup_layout_dynamic_listed_before_centered(self):
        # Settings renders the layout Combobox from the label dict's order, so
        # the default (dynamic / near-cursor) must appear before centered.
        for labels in (tr.POPUP_LAYOUT_LABELS_ZH, tr.POPUP_LAYOUT_LABELS_EN):
            keys = list(labels.keys())
            self.assertLess(keys.index("dynamic"), keys.index("centered"),
                            "dynamic must be listed before centered")

    def test_tray_click_action_labels_and_default(self):
        # The default tray-click action must be a valid, labelled key, and the
        # zh/en label maps must expose the same set of action keys so no option
        # is missing in either language.
        default_action = tr.DEFAULT_CONFIG[tr.CFG.TRAY_CLICK_ACTION]
        self.assertEqual(default_action, "settings",
                         "default tray-click action should be Settings")
        zh = set(tr.TRAY_CLICK_ACTION_LABELS_ZH.keys())
        en = set(tr.TRAY_CLICK_ACTION_LABELS_EN.keys())
        self.assertEqual(zh, en,
                         "tray-click action labels must match across languages")
        self.assertEqual(
            zh, {"settings", "history", "screenshot", "quick_input"},
            "tray-click action keys should be the four window actions")
        self.assertIn(default_action, zh)


# ============================================================
# Model labels (settings dropdown display ↔ stored value)
# ============================================================

class TestModelLabels(unittest.TestCase):
    def _labels(self):
        prev = tr.i18n.get_language()
        self.addCleanup(lambda: tr.i18n.set_language(prev))
        return prev

    def test_labels_cover_all_models(self):
        for lang in ("zh_CN", "en_US"):
            tr.i18n.set_language(lang)
            labels = tr.get_model_labels()
            self.assertEqual(set(labels), {"haiku", "sonnet", "opus"})

    def test_label_keeps_bare_model_name_prefix(self):
        # The stored/routed value is the bare model name, so each display label
        # must start with it — the parenthesised hint is display-only.
        self._labels()
        for lang in ("zh_CN", "en_US"):
            tr.i18n.set_language(lang)
            for model, label in tr.get_model_labels().items():
                self.assertTrue(label.startswith(model),
                                f"{label!r} should start with {model!r}")

    def test_label_to_value_roundtrip(self):
        self._labels()
        for lang in ("zh_CN", "en_US"):
            tr.i18n.set_language(lang)
            labels = tr.get_model_labels()
            label_to_model = {v: k for k, v in labels.items()}
            for model in ("haiku", "sonnet", "opus"):
                self.assertEqual(label_to_model[labels[model]], model)

    def test_english_and_chinese_labels_differ(self):
        self._labels()
        tr.i18n.set_language("zh_CN")
        zh = tr.get_model_labels()
        tr.i18n.set_language("en_US")
        en = tr.get_model_labels()
        self.assertNotEqual(zh["haiku"], en["haiku"])

    def test_provider_labels_and_models_are_separate(self):
        providers = tr.get_provider_labels()
        self.assertEqual(set(providers), {"claude_cli", "codex_cli"})
        self.assertEqual(
            set(tr.get_provider_model_labels("claude_cli")),
            {"haiku", "sonnet", "opus"})
        self.assertEqual(
            set(tr.get_provider_model_labels("codex_cli")),
            {"auto-fast", "auto"})

    def test_codex_labels_are_concise_and_smart_routing_is_first(self):
        self._labels()
        tr.i18n.set_language("zh_CN")
        zh = tr.get_provider_model_labels("codex_cli")
        self.assertEqual(list(zh), ["auto-fast", "auto"])
        self.assertEqual(zh["auto-fast"], "智能路由（极速）")
        self.assertEqual(zh["auto"], "自动选择（优质）")


# ============================================================
# Rich-text rendering: edge cases
# ============================================================

class TestRichTextEdgeCases(unittest.TestCase):
    def test_empty_string(self):
        segs = tr.iter_rich_segments("")
        self.assertIsInstance(segs, list)

    def test_whitespace_only(self):
        segs = tr.iter_rich_segments("   \n  \n")
        self.assertIsInstance(segs, list)

    def test_single_character(self):
        segs = tr.iter_rich_segments("X")
        self.assertTrue(any(c == "X" for c, _ in segs))

    def test_heading_levels_h1_h2_h3(self):
        for level, prefix in ((1, "# "), (2, "## "), (3, "### ")):
            segs = tr.iter_rich_segments(f"{prefix}Title")
            tags = [t for _, t in segs if t]
            self.assertIn(f"rich_h{level}", tags,
                          f"h{level} heading tag missing")

    def test_heading_capped_at_h3(self):
        segs = tr.iter_rich_segments("#### DeepHeading")
        tags = [t for _, t in segs if t]
        self.assertNotIn("rich_h4", tags)
        self.assertIn("rich_h3", tags)

    def test_numbered_list(self):
        segs = tr.iter_rich_segments("1. first item")
        tags = [t for _, t in segs if t]
        self.assertIn("rich_bullet", tags)

    def test_url_in_code_block_not_hyperlinked(self):
        segs = tr.iter_rich_segments("```\nhttps://example.com\n```")
        tags = [t for _, t in segs if t]
        # Inside a code fence the URL should be rich_codeblock, not rich_url.
        self.assertNotIn("rich_url", tags)

    def test_nested_inline_inside_bullet(self):
        segs = tr.iter_rich_segments("- bullet with **bold** text")
        tags = [t for _, t in segs if t]
        self.assertIn("rich_bullet", tags)
        self.assertIn("rich_bold", tags)

    def test_multiple_paragraphs(self):
        text = "Paragraph one.\n\nParagraph two."
        segs = tr.iter_rich_segments(text)
        full = "".join(c for c, _ in segs)
        self.assertIn("Paragraph one.", full)
        self.assertIn("Paragraph two.", full)

    def test_markers_stripped_from_reconstruction(self):
        text = "Start **bold** `code` *italic* end"
        segs = tr.iter_rich_segments(text)
        recon = "".join(c for c, _ in segs)
        self.assertNotIn("**", recon)
        self.assertNotIn("*italic*", recon)
        self.assertNotIn("`", recon)
        self.assertIn("bold", recon)
        self.assertIn("code", recon)
        self.assertIn("italic", recon)

    def test_very_long_plain_text(self):
        text = "普通中文 " * 500
        segs = tr.iter_rich_segments(text)
        self.assertTrue(len(segs) > 0)

    def test_no_trailing_newline_segment(self):
        segs = tr.iter_rich_segments("单行文字")
        self.assertNotEqual(segs[-1], ("\n", None),
                            "iter_rich_segments should strip the final newline")


# ============================================================
# Code ratio boundary values
# ============================================================

class TestCodeRatioBoundaries(unittest.TestCase):
    def test_pure_threshold_greater_than_mixed(self):
        self.assertGreater(tr.CODE_RATIO_PURE, tr.CODE_RATIO_MIXED)
        self.assertGreater(tr.CODE_RATIO_MIXED, 0.0)
        self.assertLessEqual(tr.CODE_RATIO_PURE, 1.0)

    def test_classify_boundary_pure(self):
        # Force a purely-code ratio and check the "code" label.
        code = "\n".join([
            "def f(x): return x",
            "for i in range(10):",
            "    foo(bar(baz(i)))",
        ])
        result = tr.classify_selection(code)
        self.assertEqual(result, "code")

    def test_classify_boundary_mixed(self):
        # English prose with NO function-call syntax stays "text".
        prose = "This is a regular description paragraph that talks about users."
        result = tr.classify_selection(prose)
        self.assertIn(result, ("text", "mixed"))

    def test_classify_returns_only_valid_labels(self):
        for text in ("hello", "def f(): pass", "これはテストです", ""):
            r = tr.classify_selection(text)
            self.assertIn(r, ("text", "code", "mixed"),
                          f"unexpected label {r!r} for {text!r}")


# ============================================================
# Long-text summary feature
# ============================================================

class TestSummaryHelpers(unittest.TestCase):
    def test_stream_and_summary_thresholds_unified(self):
        self.assertEqual(tr.STREAM_MIN_CHARS, tr.SUMMARY_MIN_CHARS)
        self.assertEqual(tr.STREAM_MIN_CHARS, 400)
        self.assertEqual(tr.CODEX_STREAM_MIN_CHARS, 400)

    def test_prose_paragraph_is_summarizable(self):
        prose = ("The quick brown fox jumps over the lazy dog. " * 20).strip()
        self.assertTrue(tr.is_summarizable_prose(prose))

    def test_empty_is_not_summarizable(self):
        self.assertFalse(tr.is_summarizable_prose(""))
        self.assertFalse(tr.is_summarizable_prose("   \n  "))

    def test_bullet_list_is_not_summarizable(self):
        lst = "\n".join(f"- item number {i} in the list here" for i in range(8))
        self.assertFalse(tr.is_summarizable_prose(lst))

    def test_numbered_list_is_not_summarizable(self):
        lst = "\n".join(f"{i}. step number {i} to follow here" for i in range(1, 9))
        self.assertFalse(tr.is_summarizable_prose(lst))

    def test_url_dump_is_not_summarizable(self):
        urls = "\n".join(
            "https://example.com/some/long/path/segment/page%d" % i
            for i in range(10))
        self.assertFalse(tr.is_summarizable_prose(urls))

    def test_json_blob_is_not_summarizable(self):
        blob = ('{"name": "test", "value": 123, "items": [1, 2, 3], '
                '"nested": {"a": true, "b": false}, "more": "data here"}' * 3)
        self.assertFalse(tr.is_summarizable_prose(blob))

    def test_yaml_like_config_block_is_not_summarizable(self):
        cfg = "\n".join([
            "service:",
            "  name: gateway-edge",
            "  region: ap-east-1",
            "  replicas: 6",
            "routing:",
            "  - path: /api/v1/checkout",
            "    timeout_ms: 1800",
            "    retries: 2",
            "logging:",
            "  level: info",
            "  endpoint: https://log-collector.example.net/ingest",
        ])
        self.assertFalse(tr.is_summarizable_prose(cfg))

    def test_summary_headings_localized(self):
        # Headings are keyed to the TARGET language (what the text is translated
        # INTO), not the app UI language, so a zh->en summary reads in English.
        self.assertEqual(tr.summary_headings("en"), ("Summary", "Translation"))
        self.assertEqual(tr.summary_headings("zh"), ("摘要", "译文"))
        self.assertEqual(tr.summary_headings("ja"), ("要約", "翻訳"))
        # Unknown target falls back to English headings, never crashes.
        self.assertEqual(tr.summary_headings("xx"), ("Summary", "Translation"))

    def test_summary_instruction_contains_headings(self):
        # zh target: Chinese headings + names Simplified Chinese as the language.
        instr = tr.summary_instruction("zh")
        self.assertIn("## 摘要", instr)
        self.assertIn("## 译文", instr)
        self.assertIn("Simplified Chinese", instr)
        # en target: English headings + names English as the one output language.
        instr_en = tr.summary_instruction("en")
        self.assertIn("## Summary", instr_en)
        self.assertIn("## Translation", instr_en)
        self.assertIn("English", instr_en)

    def test_resolve_target_lang_auto_zh_ui(self):
        # zh UI, auto: Chinese source -> English target; English source -> zh.
        self.assertEqual(
            tr.resolve_target_lang("auto", "zh_CN", "这是一段中文内容需要翻译"),
            "en")
        self.assertEqual(
            tr.resolve_target_lang("auto", "zh_CN", "This is English prose."),
            "zh")

    def test_resolve_target_lang_auto_en_ui(self):
        # en UI, auto: English source -> Chinese target; Chinese source -> en.
        self.assertEqual(
            tr.resolve_target_lang("auto", "en_US", "This is English prose."),
            "zh")
        self.assertEqual(
            tr.resolve_target_lang("auto", "en_US", "这是一段中文内容需要翻译"),
            "en")

    def test_resolve_target_lang_explicit_mode(self):
        # Explicit to_xx modes translate into a fixed language regardless of src.
        self.assertEqual(
            tr.resolve_target_lang("to_ja", "zh_CN", "任意内容"), "ja")
        self.assertEqual(
            tr.resolve_target_lang("to_en", "zh_CN", "任意内容"), "en")
    def test_resolve_target_lang_mixed_chinese_with_english_terms(self):
        # THE reported bug: a zh-UI user selects a Chinese paragraph that embeds
        # English technical terms / code. str.isalpha() counts CJK as alphabetic,
        # so the old ``cjk >= letters`` test flipped to non-CJK the moment ANY
        # English letter appeared -> the text was "translated" back into Chinese.
        # A Chinese-dominant mixed selection must route to English.
        mixed = ("你的判断完全正确，这是个真 bug。根因：摘要的标题和正文语言原来是按"
                 "应用界面语言定的，而不是这次翻译的目标语言。在 auto 模式下目标语言是"
                 "动态的（中文界面选中文，目标是英文），新增 resolve_target_lang() 来"
                 "确定这次翻译真正的目标语言。")
        self.assertEqual(tr.resolve_target_lang("auto", "zh_CN", mixed), "en")

    def test_resolve_target_lang_stray_cjk_stays_english(self):
        # Guard the other side: a predominantly-English paragraph with a single
        # stray CJK token must NOT flip to a Chinese source; it still -> zh.
        english = ("This is a mostly English paragraph about software design "
                   "with a single stray token 中文 in the middle for testing.")
        self.assertEqual(tr.resolve_target_lang("auto", "zh_CN", english), "zh")

    def test_summary_language_matches_translation_zh_to_en(self):
        # Regression (the reported bug): a zh-UI user selecting CHINESE text
        # gets an English translation, so the summary must ALSO be English —
        # not Chinese. The prompt must therefore carry English headings and
        # name English as the sole output language.
        target = tr.resolve_target_lang("auto", "zh_CN", "这是一段较长的中文内容。")
        self.assertEqual(target, "en")
        instr = tr.summary_instruction(target)
        self.assertIn("## Summary", instr)
        self.assertIn("## Translation", instr)
        self.assertNotIn("## 摘要", instr)
        self.assertIn("English", instr)

    def test_summary_default_off(self):
        self.assertFalse(tr.DEFAULT_CONFIG[tr.CFG.SUMMARY_ENABLED])


class TestShouldSummarize(unittest.TestCase):
    """Exercise TranslatorApp._should_summarize without constructing the full
    app: call the unbound method against a lightweight stub self."""

    def _stub(self, *, enabled=True, last_class="text", last_origin="text"):
        ns = types.SimpleNamespace()
        ns.cfg = {tr.CFG.SUMMARY_ENABLED: enabled}
        ns._last_class = last_class
        ns._last_origin = last_origin
        return ns

    def _call(self, stub, text):
        return tr.TranslatorApp._should_summarize(stub, text)

    def _long_prose(self):
        return ("The quick brown fox jumps over the lazy dog. " * 20).strip()

    def test_long_prose_enabled(self):
        self.assertTrue(self._call(self._stub(), self._long_prose()))

    def test_disabled_setting(self):
        self.assertFalse(self._call(self._stub(enabled=False), self._long_prose()))

    def test_short_text_not_summarized(self):
        self.assertFalse(self._call(self._stub(), "Short sentence here."))

    def test_code_class_not_summarized(self):
        self.assertFalse(
            self._call(self._stub(last_class="code"), self._long_prose()))

    def test_mixed_class_is_summarized(self):
        # Mixed prose+code long text now qualifies (summary prompt keeps code
        # verbatim); only pure code and screenshots are excluded.
        self.assertTrue(
            self._call(self._stub(last_class="mixed"), self._long_prose()))

    def test_local_ocr_is_not_summarized(self):
        self.assertFalse(self._call(
            self._stub(last_origin="ocr"), self._long_prose()))

    def test_ocr_class_not_summarized(self):
        # Screenshots take a separate one-shot vision path, not this pipeline.
        self.assertFalse(
            self._call(self._stub(last_class="ocr"), self._long_prose()))

    def test_mixed_config_block_not_summarized(self):
        cfg = "\n".join([
            "service:",
            "  name: gateway-edge",
            "  region: ap-east-1",
            "  runtime: python3.12",
            "routing:",
            "  - path: /api/v1/checkout",
            "    timeout_ms: 1800",
            "    retries: 2",
            "metadata: {\"owner\":\"platform-core\",\"rollback\":\"enabled\"}",
        ])
        self.assertFalse(self._call(self._stub(last_class="mixed"), cfg))

    def test_single_word_not_summarized(self):
        long_word = "a" * 500
        self.assertFalse(self._call(self._stub(), long_word))


# ============================================================
# cc_warm constants and WarmClaude class
# ============================================================

class TestCCWarm(unittest.TestCase):
    def test_claude_cmd_is_string(self):
        self.assertIsInstance(tr.CLAUDE_CMD, str)
        self.assertGreater(len(tr.CLAUDE_CMD), 0)

    def test_warm_constants_reasonable(self):
        import cc_warm
        self.assertGreater(cc_warm.WARM_UP_MS, 0)
        self.assertGreater(cc_warm.WARM_MAX_AGE_S, cc_warm.WARM_UP_MS / 1000)
        self.assertGreater(cc_warm.WARM_SEND_TIMEOUT_S, 0)
        self.assertIsInstance(cc_warm.WARM_POOL_ENABLED, bool)

    def test_warm_claude_instantiation(self):
        w = tr.WarmClaude("haiku", "You are a translator.", ("haiku", "auto"))
        self.assertEqual(w.model, "haiku")
        self.assertEqual(w.key, ("haiku", "auto"))
        self.assertFalse(w.ready)
        self.assertFalse(w.spent)
        self.assertIsNone(w.proc)

    def test_warm_claude_usable_false_before_start(self):
        w = tr.WarmClaude("haiku", "sys", ("haiku", "auto"))
        self.assertFalse(w.usable(("haiku", "auto")))

    def test_warm_claude_close_no_proc_is_noop(self):
        w = tr.WarmClaude("haiku", "sys", ("haiku", "auto"))
        w.close()   # should not raise

    def test_warm_claude_send_without_proc_returns_none(self):
        w = tr.WarmClaude("haiku", "sys", ("haiku", "auto"))
        result = w.send_and_stream("hello", lambda x: None)
        self.assertIsNone(result)

    def test_warm_start_preserves_claude_cli_contract(self):
        import cc_warm

        proc = unittest.mock.Mock()
        with unittest.mock.patch.object(
                cc_warm.subprocess, "Popen", return_value=proc) as popen, \
                unittest.mock.patch.object(cc_warm.threading, "Thread") as thread:
            w = cc_warm.WarmClaude(
                "sonnet", "SYSTEM", ("translate", "sonnet", "auto"))
            self.assertTrue(w.start())

        self.assertEqual(
            popen.call_args.args[0],
            [
                cc_warm.CLAUDE_CMD, "-p", "--safe-mode", "--model", "sonnet",
                "--system-prompt", "SYSTEM",
                "--input-format", "stream-json",
                "--output-format", "stream-json",
                "--include-partial-messages", "--verbose",
                "--tools", "",
                "--exclude-dynamic-system-prompt-sections",
                "--no-session-persistence",
            ],
        )
        self.assertEqual(
            popen.call_args.kwargs,
            {
                "stdin": cc_warm.subprocess.PIPE,
                "stdout": cc_warm.subprocess.PIPE,
                "stderr": cc_warm.subprocess.DEVNULL,
                "text": True,
                "encoding": "utf-8",
                "creationflags": cc_warm.subprocess.CREATE_NO_WINDOW,
            },
        )
        thread.assert_called_once()

    def test_warm_send_preserves_stream_json_input_contract(self):
        import cc_warm

        stdin = unittest.mock.Mock()
        stdout = iter([
            json.dumps({
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"text": "你"},
                },
            }),
            json.dumps({
                "type": "result",
                "is_error": False,
                "result": "你好",
            }),
        ])
        proc = unittest.mock.Mock(
            stdin=stdin, stdout=stdout, poll=unittest.mock.Mock(return_value=None))
        timer = unittest.mock.Mock()
        deltas = []
        w = cc_warm.WarmClaude(
            "sonnet", "SYSTEM", ("translate", "sonnet", "auto"))
        w.proc = proc

        with unittest.mock.patch.object(
                cc_warm.threading, "Timer", return_value=timer) as timer_cls:
            result = w.send_and_stream("hello", deltas.append)

        expected = {
            "type": "user",
            "message": {
                "role": "user",
                "content": "<text>\nhello\n</text>",
            },
        }
        stdin.write.assert_called_once_with(json.dumps(expected) + "\n")
        stdin.flush.assert_called_once_with()
        timer_cls.assert_called_once_with(
            cc_warm.WARM_SEND_TIMEOUT_S, unittest.mock.ANY)
        timer.start.assert_called_once_with()
        timer.cancel.assert_called_once_with()
        self.assertEqual(deltas, ["你"])
        self.assertEqual(result, "你好")


class TestWarmPoolProfiles(unittest.TestCase):
    """The warm pool keeps up to WARM_POOL_DEPTH pre-warmed processes per
    profile so the common cold paths (normal translation AND single-word
    dictionary lookups) skip the ~2s CLI cold-start, and back-to-back requests
    don't fall back to cold while a replacement warms. Dictionary lookups used
    to always run cold."""

    def _app(self, model="haiku", direction="auto"):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.load_config()
        app.cfg[tr.CFG.MODEL] = model
        app.cfg[tr.CFG.DIRECTION] = direction
        app._warm_lock = __import__("threading").Lock()
        app._warm_pool = {}
        app._warm_pending = {}
        app._warm_generation = 0
        app._warm_enabled = True
        return app

    def test_profile_spec_translate_and_dictionary(self):
        app = self._app(model="haiku", direction="auto")
        tkey, tprompt = app._warm_profile_spec("translate")
        self.assertEqual(tkey[0], "translate")
        self.assertIn("haiku", tkey)
        self.assertTrue(tprompt)
        dkey, dprompt = app._warm_profile_spec("dictionary")
        self.assertEqual(dkey[0], "dictionary")
        # Dictionary prompt is direction-independent and is exactly the
        # DICTIONARY_PROMPT used by the cold path (no direction in the key).
        self.assertEqual(dprompt, tr.DICTIONARY_PROMPT)
        self.assertNotIn("auto", dkey)

    def test_profile_spec_unknown_is_none(self):
        app = self._app()
        self.assertIsNone(app._warm_profile_spec("nope"))

    def test_pool_depth_is_two(self):
        # The user asked for depth 2; guard against an accidental regression.
        self.assertEqual(tr.WARM_POOL_DEPTH, 2)

    def test_take_warm_returns_usable_process_for_profile(self):
        app = self._app()
        app._spawn_warm_async = unittest.mock.Mock()
        key = app._warm_profile_spec("dictionary")[0]
        fake = unittest.mock.Mock()
        fake.usable.return_value = True
        app._warm_pool["dictionary"] = [fake]
        got = app._take_warm("dictionary")
        self.assertIs(got, fake)
        fake.usable.assert_called_once_with(key)
        # Taken out of the pool so it isn't handed out twice.
        self.assertEqual(app._warm_pool["dictionary"], [])
        # Removing one triggers a refill toward depth.
        app._spawn_warm_async.assert_called_once_with("dictionary")

    def test_take_warm_leaves_second_ready_process_for_back_to_back(self):
        # Depth 2: taking one usable process leaves the other in the pool so a
        # rapid second request also hits warm.
        app = self._app()
        app._spawn_warm_async = unittest.mock.Mock()
        key = app._warm_profile_spec("translate")[0]
        a, b = unittest.mock.Mock(), unittest.mock.Mock()
        a.usable.return_value = True
        a.ready = True
        a.key = key
        b.usable.return_value = True
        b.ready = True
        b.key = key
        app._warm_pool["translate"] = [a, b]
        got = app._take_warm("translate")
        self.assertIs(got, a)                      # takes exactly one
        self.assertEqual(app._warm_pool["translate"], [b])   # keeps the other

    def test_take_warm_none_when_profile_empty(self):
        app = self._app()
        self.assertIsNone(app._take_warm("translate"))

    def test_take_warm_disabled_returns_none(self):
        app = self._app()
        app._warm_enabled = False
        app._warm_pool["translate"] = [unittest.mock.Mock()]
        self.assertIsNone(app._take_warm("translate"))

    def test_take_warm_keeps_still_warming_process(self):
        # A not-yet-ready process (usable False, ready False) must stay in the
        # pool, not be evicted as stale.
        app = self._app()
        app._spawn_warm_async = unittest.mock.Mock()
        warming = unittest.mock.Mock()
        warming.usable.return_value = False
        warming.ready = False
        app._warm_pool["translate"] = [warming]
        got = app._take_warm("translate")
        self.assertIsNone(got)
        self.assertEqual(app._warm_pool["translate"], [warming])
        warming.close.assert_not_called()

    def test_take_warm_discards_wrong_key_and_refills(self):
        app = self._app()
        stale = unittest.mock.Mock()
        stale.usable.return_value = False
        stale.ready = True
        stale.key = ("translate", "haiku", "SOMETHING-ELSE")
        app._warm_pool["translate"] = [stale]
        app._spawn_warm_async = unittest.mock.Mock()
        got = app._take_warm("translate")
        self.assertIsNone(got)
        stale.close.assert_called_once_with()
        app._spawn_warm_async.assert_called_once_with("translate")

    def test_spawn_respects_depth_and_pending(self):
        # Plan must fill to WARM_POOL_DEPTH accounting for what's already held
        # and what's already in flight, never over-shooting.
        app = self._app()
        started = []

        class _FakeWarm:
            def __init__(self, model, prompt, key):
                self.key = key
            def start(self):
                started.append(self.key)
                return True

        import cc_app_warm
        with unittest.mock.patch.object(cc_app_warm, "WarmClaude", _FakeWarm), \
                unittest.mock.patch.object(tr.threading, "Thread") as Thread:
            # Run the worker synchronously so we can assert on results.
            Thread.side_effect = lambda target, daemon=None: type(
                "T", (), {"start": staticmethod(target)})()
            app._spawn_warm_async("translate")
        # Empty pool, depth 2 → spawns exactly 2.
        self.assertEqual(len(started), 2)
        self.assertEqual(len(app._warm_pool["translate"]), 2)
        self.assertEqual(app._warm_pending["translate"], 0)

    def test_close_warm_pool_closes_every_process(self):
        app = self._app()
        a, b, c = (unittest.mock.Mock() for _ in range(3))
        app._warm_pool = {"translate": [a, b], "dictionary": [c]}
        app.close_warm_pool()
        for m in (a, b, c):
            m.close.assert_called_once_with()
        self.assertFalse(app._warm_enabled)
        self.assertEqual(app._warm_pool, {})

    def test_completed_spawn_is_discarded_after_provider_switch(self):
        app = self._app()
        started = {}
        candidates = [unittest.mock.Mock(), unittest.mock.Mock()]
        for candidate in candidates:
            candidate.start.return_value = True

        class _FakeThread:
            def __init__(self, target, daemon=None):
                started["target"] = target

            def start(self):
                pass

        import cc_app_warm
        with unittest.mock.patch.object(
                cc_app_warm, "WarmClaude", side_effect=candidates), \
                unittest.mock.patch.object(
                    cc_app_warm.threading, "Thread", _FakeThread):
            app._spawn_warm_async("translate")
            app._set_warm_provider("codex_cli")
            started["target"]()

        self.assertFalse(app._warm_enabled)
        self.assertEqual(app._warm_pool, {})
        for candidate in candidates:
            candidate.close.assert_called_once_with()

    def test_reset_warm_pool_closes_all_and_respawns(self):
        app = self._app()
        a, b = unittest.mock.Mock(), unittest.mock.Mock()
        app._warm_pool = {"translate": [a], "dictionary": [b]}
        app._spawn_warm_async = unittest.mock.Mock()
        app._reset_warm_pool()
        a.close.assert_called_once_with()
        b.close.assert_called_once_with()
        self.assertEqual(app._warm_pool, {})
        app._spawn_warm_async.assert_called_once_with()


class TestDoTranslateWarmRouting(unittest.TestCase):
    """_do_translate must send single words to the dictionary warm profile,
    normal text to the translate profile, and leave code/summary cold."""

    def _app(self):
        app = object.__new__(tr.TranslatorApp)
        app._ss = tr.StreamSession()
        app._last_class = "text"
        app._last_origin = "text"
        app._should_summarize = lambda text: False
        app._warm_enabled = True
        app.root = unittest.mock.Mock()
        app._warm_translate = unittest.mock.Mock(return_value=True)
        app._record_history = unittest.mock.Mock()
        return app

    def _profile_of_call(self, app):
        # _warm_translate(text, job_id, ss, meta, profile) — profile is last arg.
        return app._warm_translate.call_args.args[-1]

    def test_single_word_routes_to_dictionary_profile(self):
        app = self._app()
        app._do_translate("hello", job_id=1, meta={"input": "hello"})
        app._warm_translate.assert_called_once()
        self.assertEqual(self._profile_of_call(app), "dictionary")

    def test_sentence_routes_to_translate_profile(self):
        app = self._app()
        app._do_translate("hello world, how are you today",
                          job_id=1, meta={"input": "x"})
        app._warm_translate.assert_called_once()
        self.assertEqual(self._profile_of_call(app), "translate")

    def test_code_stays_cold(self):
        app = self._app()
        app._last_class = "code"
        app._call_claude = unittest.mock.Mock(return_value=(True, "explained"))
        app._stream_claude = unittest.mock.Mock(return_value=False)
        app._do_translate("print(x)", job_id=1, meta={"input": "print(x)"})
        app._warm_translate.assert_not_called()

    def test_summary_stays_cold(self):
        app = self._app()
        app._should_summarize = lambda text: True
        app._call_claude = unittest.mock.Mock(return_value=(True, "summary"))
        app._stream_claude = unittest.mock.Mock(return_value=False)
        long_text = "This is a long sentence. " * 30
        app._do_translate(long_text, job_id=1, meta={"input": long_text})
        app._warm_translate.assert_not_called()

    def test_long_text_fallback_order_remains_warm_stream_oneshot(self):
        app = self._app()
        app._warm_translate.return_value = False
        app._stream_claude = unittest.mock.Mock(return_value=False)
        app._call_claude = unittest.mock.Mock(return_value=(True, "fallback"))
        calls = unittest.mock.Mock()
        calls.attach_mock(app._warm_translate, "warm")
        calls.attach_mock(app._stream_claude, "stream")
        calls.attach_mock(app._call_claude, "oneshot")
        long_text = "This is a long sentence. " * 30

        app._do_translate(long_text, job_id=1, meta={"input": long_text})

        self.assertEqual(
            [call[0] for call in calls.mock_calls],
            ["warm", "stream", "oneshot"],
        )

class TestProviderLifecycle(unittest.TestCase):
    def test_shutdown_cancels_work_and_closes_all_model_processes(self):
        app = object.__new__(tr.TranslatorApp)
        app._provider_cancel_event = threading.Event()
        app.close_warm_pool = unittest.mock.Mock()
        app._provider_registry = unittest.mock.Mock()

        app._shutdown_model_processes()

        self.assertTrue(app._provider_cancel_event.is_set())
        app.close_warm_pool.assert_called_once_with()
        app._provider_registry.shutdown.assert_called_once_with()

    def test_run_always_shuts_down_model_processes(self):
        app = object.__new__(tr.TranslatorApp)
        app.root = unittest.mock.Mock()
        app.root.mainloop.side_effect = RuntimeError("Tk failed")
        app._shutdown_model_processes = unittest.mock.Mock()

        with self.assertRaisesRegex(RuntimeError, "Tk failed"):
            app.run()

        app._shutdown_model_processes.assert_called_once_with()


class TestProviderRouting(unittest.TestCase):
    def test_claude_text_facade_forwards_snapshot_model(self):
        app = object.__new__(tr.TranslatorApp)
        app._call_claude = unittest.mock.Mock(return_value=(True, "ok"))
        selection = tr.ProviderSelection("claude_cli", "opus")

        result = app._call_model(
            "hello", "PROMPT", selection=selection)

        self.assertEqual(result, (True, "ok"))
        app._call_claude.assert_called_once_with(
            "hello", "PROMPT", model="opus")

    def test_claude_image_facade_forwards_snapshot_model(self):
        app = object.__new__(tr.TranslatorApp)
        app._call_claude_vision = unittest.mock.Mock(
            return_value=(True, "ok"))
        selection = tr.ProviderSelection("claude_cli", "haiku")

        result = app._call_model_image(
            r"C:\image.png", selection=selection)

        self.assertEqual(result, (True, "ok"))
        app._call_claude_vision.assert_called_once_with(
            r"C:\image.png", model="haiku")

    def test_codex_request_bypasses_all_claude_paths(self):
        app = object.__new__(tr.TranslatorApp)
        app._do_provider_translate = unittest.mock.Mock()
        app._warm_translate = unittest.mock.Mock()
        app._stream_claude = unittest.mock.Mock()
        app._call_claude = unittest.mock.Mock()
        meta = {
            "provider": "codex_cli",
            "model": "auto",
            "system_prompt": "Translate.",
        }

        app._do_translate("hello", 7, meta)

        app._do_provider_translate.assert_called_once_with("hello", 7, meta)
        app._warm_translate.assert_not_called()
        app._stream_claude.assert_not_called()
        app._call_claude.assert_not_called()

    def test_history_meta_captures_provider_selection(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.Config({
            tr.CFG.MODEL_PROVIDER: "codex_cli",
            tr.CFG.CODEX_MODEL: "gpt-5.4",
        })
        app._last_input = "hello"
        app._last_origin = "text"
        app._last_class = "text"
        app._provider_cancel_event = tr.threading.Event()
        app._history_kind = unittest.mock.Mock(return_value="text")
        app._cache_signature = unittest.mock.Mock(return_value="sig")
        app._system_prompt_for = unittest.mock.Mock(return_value="prompt")

        meta = app._history_meta()
        app.cfg[tr.CFG.MODEL_PROVIDER] = "claude_cli"

        self.assertEqual(meta["provider"], "codex_cli")
        self.assertEqual(meta["model"], "gpt-5.4")
        self.assertEqual(meta["system_prompt"], "prompt")
        self.assertIs(meta["cancel_event"], app._provider_cancel_event)

    def test_unknown_provider_returns_explicit_error(self):
        app = object.__new__(tr.TranslatorApp)
        ok, result = app._call_model(
            "hello", "Translate.",
            tr.ProviderSelection(provider_id="missing", model=None))
        self.assertFalse(ok)
        self.assertIn("missing", result)

    def test_codex_facade_logs_safe_provider_metrics(self):
        from cc_providers.base import ProviderResult

        app = object.__new__(tr.TranslatorApp)
        provider = unittest.mock.Mock()
        provider.complete.return_value = ProviderResult(
            True, text="ok", metrics=(
                ("spawn_ms", 10), ("total_ms", 500)))
        app._provider_registry = unittest.mock.Mock()
        app._provider_registry.get.return_value = provider

        with unittest.mock.patch.object(tr, "log_perf") as perf:
            result = app._call_model(
                "hello", "Translate.",
                tr.ProviderSelection("codex_cli", "gpt-5.4-mini"))

        self.assertEqual(result, (True, "ok"))
        fields = perf.call_args.args[1]
        self.assertEqual(fields["provider"], "codex_cli")
        self.assertEqual(fields["model"], "gpt-5.4-mini")
        self.assertEqual(fields["chars"], 5)
        self.assertEqual(fields["total_ms"], 500)
        self.assertNotIn("text", fields)

    def test_long_codex_uses_experimental_stream_when_enabled(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.Config({
            tr.CFG.CODEX_STREAMING_EXPERIMENTAL: True,
        })
        app._ss = tr.StreamSession()
        app._stream_codex = unittest.mock.Mock(return_value=True)
        app._call_model = unittest.mock.Mock()
        app._record_history = unittest.mock.Mock()
        app.root = unittest.mock.Mock()
        text = "long text " * 100
        meta = {
            "provider": "codex_cli",
            "model": "gpt-5.4-mini",
            "input": text,
            "system_prompt": "Translate.",
        }

        app._do_provider_translate(text, 1, meta)

        app._stream_codex.assert_called_once()
        app._call_model.assert_not_called()

    def test_codex_route_records_stable_reason_when_stream_not_eligible(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.Config({
            tr.CFG.CODEX_STREAMING_EXPERIMENTAL: True,
        })
        app._ss = tr.StreamSession()
        app._stream_codex = unittest.mock.Mock(return_value=True)
        app._call_model = unittest.mock.Mock(return_value=(True, "ok"))
        app._record_history = unittest.mock.Mock()
        app.root = unittest.mock.Mock()
        meta = {
            "provider": "codex_cli",
            "model": "auto",
            "input": "hello world.",
            "origin": "text",
            "is_code": False,
            "kind": "text",
            "system_prompt": "Translate.",
        }

        app._do_provider_translate("hello world.", 1, meta)

        self.assertEqual(app._last_provider_route, {
            "provider": "codex_cli",
            "model": "auto",
            "mode": "stable_exec",
            "reason": "short_text",
            "error_code": "",
        })

    def test_stable_codex_logs_terminal_dogfood_route(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.Config({
            tr.CFG.CODEX_STREAMING_EXPERIMENTAL: True,
        })
        app._ss = tr.StreamSession()
        app._call_model = unittest.mock.Mock(return_value=(True, "ok"))
        app._record_history = unittest.mock.Mock()
        app.root = unittest.mock.Mock()
        text = "short text"
        meta = {
            "provider": "codex_cli",
            "model": "auto",
            "input": text,
            "system_prompt": "Translate.",
        }

        with unittest.mock.patch.object(tr, "log_perf") as perf:
            app._do_provider_translate(text, 1, meta)

        route_event = next(
            fields for stage, fields in (call.args for call in perf.call_args_list)
            if stage == "provider_route_complete")
        self.assertEqual(route_event["route"], "stable_exec")
        self.assertEqual(route_event["outcome"], "success")
        self.assertNotIn("input", route_event)

    def test_short_codex_skips_experimental_stream(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.Config({
            tr.CFG.CODEX_STREAMING_EXPERIMENTAL: True,
        })
        app._ss = tr.StreamSession()
        app._stream_codex = unittest.mock.Mock(return_value=True)
        app._call_model = unittest.mock.Mock(return_value=(True, "ok"))
        app._record_history = unittest.mock.Mock()
        app.root = unittest.mock.Mock()
        meta = {
            "provider": "codex_cli",
            "model": "auto",
            "input": "hello world.",
            "origin": "text",
            "is_code": False,
            "kind": "text",
            "system_prompt": "Translate.",
        }

        app._do_provider_translate("hello world.", 1, meta)

        app._stream_codex.assert_not_called()
        app._call_model.assert_called_once()

    def test_fast_profile_streams_dictionary_and_short_text(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.Config({
            tr.CFG.CODEX_STREAMING_EXPERIMENTAL: True,
        })
        app._ss = tr.StreamSession()
        app._stream_codex = unittest.mock.Mock(return_value=True)
        app._call_model = unittest.mock.Mock()
        app._record_history = unittest.mock.Mock()
        app.root = unittest.mock.Mock()

        for job_id, text in enumerate(("青提", "A short sentence."), 1):
            meta = {
                "provider": "codex_cli",
                "model": "auto-fast",
                "input": text,
                "system_prompt": "Translate.",
            }
            app._do_provider_translate(text, job_id, meta)

        self.assertEqual(app._stream_codex.call_count, 2)
        app._call_model.assert_not_called()

    def test_fast_profile_selects_runtime_model_by_length(self):
        self.assertEqual(
            tr.codex_request_model("auto-fast", 2), "auto-fast")
        self.assertEqual(
            tr.codex_request_model("auto-fast", 399), "auto-fast")
        self.assertEqual(
            tr.codex_request_model("auto-fast", 400), "gpt-5.4-mini")
        self.assertEqual(
            tr.codex_request_model("auto", 1000), "auto")
        self.assertEqual(
            tr.codex_request_model("auto-fast", 1000, image=True),
            "auto-fast")

    def test_fast_profile_passes_resolved_model_to_stable_provider(self):
        from cc_providers.base import ProviderResult

        app = object.__new__(tr.TranslatorApp)
        app._provider_registry = unittest.mock.Mock()
        provider = app._provider_registry.get.return_value
        provider.complete.return_value = ProviderResult(True, text="ok")

        app._call_model(
            "x" * 400,
            "Translate.",
            tr.ProviderSelection("codex_cli", "auto-fast"),
        )

        request = provider.complete.call_args.args[0]
        self.assertEqual(request.model, "gpt-5.4-mini")

    def test_codex_stream_route_uses_evidence_based_boundary(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.Config({
            tr.CFG.CODEX_STREAMING_EXPERIMENTAL: True,
        })
        app._ss = tr.StreamSession()
        app._stream_codex = unittest.mock.Mock(return_value=True)
        app._call_model = unittest.mock.Mock(return_value=(True, "ok"))
        app._record_history = unittest.mock.Mock()
        app.root = unittest.mock.Mock()

        def meta(text):
            return {
                "provider": "codex_cli",
                "model": "auto",
                "input": text,
                "system_prompt": "Translate.",
            }

        below = ("x " * 199) + "x"
        self.assertEqual(len(below), tr.CODEX_STREAM_MIN_CHARS - 1)
        app._do_provider_translate(below, 1, meta(below))
        app._stream_codex.assert_not_called()
        app._call_model.assert_called_once()

        app._call_model.reset_mock()
        at_boundary = "x " * 200
        self.assertEqual(len(at_boundary), tr.CODEX_STREAM_MIN_CHARS)
        app._do_provider_translate(at_boundary, 2, meta(at_boundary))
        app._stream_codex.assert_called_once()
        app._call_model.assert_not_called()

    def test_codex_stream_failure_before_delta_allows_exec_fallback(self):
        from cc_providers.base import ProviderResult

        app = object.__new__(tr.TranslatorApp)
        app.root = unittest.mock.Mock()
        app._provider_registry = unittest.mock.Mock()
        provider = app._provider_registry.get.return_value
        provider.stream.return_value = ProviderResult(
            False, error_code="appserver_exited")
        app._system_prompt_for = unittest.mock.Mock(return_value="Translate.")
        app._record_history = unittest.mock.Mock()
        app._provider_error_text = unittest.mock.Mock(return_value="failed")
        ss = tr.StreamSession()
        meta = {
            "input": "long text",
            "origin": "text",
            "is_code": False,
            "kind": "text",
        }

        handled = app._stream_codex(
            "long text", 1, ss, meta,
            tr.ProviderSelection("codex_cli", "auto"))

        self.assertFalse(handled)
        app._provider_error_text.assert_not_called()
        self.assertEqual(
            app._last_provider_route["mode"], "stable_fallback")
        self.assertEqual(
            app._last_provider_route["error_code"], "appserver_exited")

    def test_codex_stream_failure_after_delta_does_not_retry(self):
        from cc_providers.base import ProviderResult

        app = object.__new__(tr.TranslatorApp)
        app.root = unittest.mock.Mock()
        app.root.after.side_effect = lambda _delay, callback: callback()
        app._provider_registry = unittest.mock.Mock()
        provider = app._provider_registry.get.return_value

        def stream(_request, on_delta, _cancel_event):
            on_delta("partial")
            return ProviderResult(
                False, error_code="unknown_appserver_event")

        provider.stream.side_effect = stream
        app._system_prompt_for = unittest.mock.Mock(return_value="Translate.")
        app._record_history = unittest.mock.Mock()
        app._provider_error_text = unittest.mock.Mock(
            return_value="protocol changed")
        app._stream_update = unittest.mock.Mock()
        app._cancel_stream_flush = unittest.mock.Mock()
        app._show_result = unittest.mock.Mock()
        app._job_is_current = unittest.mock.Mock(return_value=True)
        ss = tr.StreamSession()
        app._ss = ss
        meta = {
            "input": "long text",
            "origin": "text",
            "is_code": False,
            "kind": "text",
        }

        handled = app._stream_codex(
            "long text", 1, ss, meta,
            tr.ProviderSelection("codex_cli", "auto"))

        self.assertTrue(handled)
        app._stream_update.assert_called_once_with("partial")
        app._provider_error_text.assert_called_once()
        app._show_result.assert_called_once_with(
            False, "protocol changed", 1, record=False)

    def test_codex_render_in_progress_does_not_allow_exec_fallback(self):
        from cc_providers.base import ProviderResult

        app = object.__new__(tr.TranslatorApp)
        render_started = threading.Event()
        finish_render = threading.Event()
        callback_threads = []
        app.root = unittest.mock.Mock()

        def run_callback(_delay, callback):
            callback_thread = threading.Thread(target=callback)
            callback_thread.start()
            callback_threads.append(callback_thread)

        app.root.after.side_effect = run_callback
        app._provider_registry = unittest.mock.Mock()
        provider = app._provider_registry.get.return_value

        def stream(_request, on_delta, _cancel_event):
            on_delta("partial")
            self.assertTrue(render_started.wait(1))
            return ProviderResult(
                False, error_code="unknown_appserver_event")

        provider.stream.side_effect = stream
        app._system_prompt_for = unittest.mock.Mock(return_value="Translate.")
        app._record_history = unittest.mock.Mock()
        app._provider_error_text = unittest.mock.Mock(
            return_value="protocol changed")

        def block_first_render(_text):
            render_started.set()
            self.assertTrue(finish_render.wait(1))

        app._stream_update = unittest.mock.Mock(side_effect=block_first_render)
        app._cancel_stream_flush = unittest.mock.Mock()
        app._show_result = unittest.mock.Mock()
        app._job_is_current = unittest.mock.Mock(return_value=True)
        ss = tr.StreamSession()
        app._ss = ss
        meta = {"input": "long text", "origin": "text",
                "is_code": False, "kind": "text"}

        try:
            handled = app._stream_codex(
                "long text", 1, ss, meta,
                tr.ProviderSelection("codex_cli", "auto"))
        finally:
            finish_render.set()
            for callback_thread in callback_threads:
                callback_thread.join(1)

        self.assertTrue(handled)
        self.assertEqual(app._last_provider_route["mode"], "stream_failed")
        app._provider_error_text.assert_called_once()

    def test_codex_unrendered_delta_can_fall_back_without_late_render(self):
        from cc_providers.base import ProviderResult

        app = object.__new__(tr.TranslatorApp)
        callbacks = []
        app.root = unittest.mock.Mock()
        app.root.after.side_effect = (
            lambda _delay, callback: callbacks.append(callback))
        app._provider_registry = unittest.mock.Mock()
        provider = app._provider_registry.get.return_value

        def stream(_request, on_delta, _cancel_event):
            on_delta("not rendered")
            return ProviderResult(
                False, error_code="unknown_appserver_event")

        provider.stream.side_effect = stream
        app._system_prompt_for = unittest.mock.Mock(return_value="Translate.")
        app._record_history = unittest.mock.Mock()
        app._provider_error_text = unittest.mock.Mock()
        app._stream_update = unittest.mock.Mock()
        app._job_is_current = unittest.mock.Mock(return_value=True)
        ss = tr.StreamSession()
        app._ss = ss
        meta = {"input": "long text", "origin": "text",
                "is_code": False, "kind": "text"}

        handled = app._stream_codex(
            "long text", 1, ss, meta,
            tr.ProviderSelection("codex_cli", "auto"))
        for callback in callbacks:
            callback()

        self.assertFalse(handled)
        app._stream_update.assert_not_called()
        self.assertLessEqual(tr.CODEX_FIRST_FRAME_WAIT_SECONDS, 0.05)

    def test_codex_render_schedule_failure_is_not_logged_as_success(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.Config({
            tr.CFG.CODEX_STREAMING_EXPERIMENTAL: True,
        })
        app._ss = tr.StreamSession()
        app._stream_codex = unittest.mock.Mock(return_value=True)
        app._call_model = unittest.mock.Mock()
        app._record_history = unittest.mock.Mock()
        app.root = unittest.mock.Mock()
        text = "long text " * 100
        meta = {
            "provider": "codex_cli",
            "model": "auto",
            "input": text,
            "system_prompt": "Translate.",
        }

        with unittest.mock.patch.object(tr, "log_perf") as perf:
            app._do_provider_translate(text, 1, meta)

        route_event = next(
            fields for stage, fields in (call.args for call in perf.call_args_list)
            if stage == "provider_route_complete")
        self.assertEqual(route_event["route"], "stream_failed")
        self.assertEqual(route_event["outcome"], "failed")

    def test_codex_success_during_tk_teardown_never_falls_back(self):
        from cc_providers.base import ProviderResult

        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.Config({
            tr.CFG.CODEX_STREAMING_EXPERIMENTAL: True,
        })
        app.root = unittest.mock.Mock()
        app.root.after.side_effect = tr.tk.TclError("destroyed")
        app._provider_registry = unittest.mock.Mock()
        provider = app._provider_registry.get.return_value
        provider.stream.return_value = ProviderResult(
            True, text="final")
        app._system_prompt_for = unittest.mock.Mock(return_value="Translate.")
        app._record_history = unittest.mock.Mock()
        app._call_model = unittest.mock.Mock()
        app._job_is_current = unittest.mock.Mock(return_value=True)
        app._ss = tr.StreamSession()
        text = "long text " * 100
        meta = {"provider": "codex_cli", "model": "auto",
                "input": text, "origin": "text", "is_code": False,
                "kind": "text", "system_prompt": "Translate."}

        app._do_provider_translate(text, 1, meta)

        app._call_model.assert_not_called()

    def test_stale_stream_error_cannot_cancel_new_stream_flush(self):
        from cc_providers.base import ProviderResult

        app = object.__new__(tr.TranslatorApp)
        deferred = []
        call_count = {"value": 0}

        def after(_delay, callback):
            call_count["value"] += 1
            if call_count["value"] == 1:
                callback()
            else:
                deferred.append(callback)

        app.root = unittest.mock.Mock()
        app.root.after.side_effect = after
        app._provider_registry = unittest.mock.Mock()
        provider = app._provider_registry.get.return_value

        def stream(_request, on_delta, _cancel_event):
            on_delta("partial")
            return ProviderResult(
                False, error_code="unknown_appserver_event")

        provider.stream.side_effect = stream
        app._system_prompt_for = unittest.mock.Mock(return_value="Translate.")
        app._record_history = unittest.mock.Mock()
        app._provider_error_text = unittest.mock.Mock(return_value="failed")
        app._stream_update = unittest.mock.Mock()
        app._cancel_stream_flush = unittest.mock.Mock()
        app._show_result = unittest.mock.Mock()
        current_job = {"value": 1}
        app._job_is_current = lambda job_id: job_id == current_job["value"]
        old_ss = tr.StreamSession()
        app._ss = old_ss
        meta = {"input": "long text", "origin": "text",
                "is_code": False, "kind": "text"}

        handled = app._stream_codex(
            "long text", 1, old_ss, meta,
            tr.ProviderSelection("codex_cli", "auto"))
        current_job["value"] = 2
        app._ss = tr.StreamSession()
        for callback in deferred:
            callback()

        self.assertTrue(handled)
        app._cancel_stream_flush.assert_not_called()
        app._show_result.assert_not_called()


# ============================================================
# cc_update paths
# ============================================================

class TestCCUpdatePaths(unittest.TestCase):
    def test_script_path_points_to_translator(self):
        import cc_update
        self.assertTrue(cc_update.SCRIPT_PATH.endswith("translator.pyw"),
                        f"SCRIPT_PATH should end with translator.pyw, got {cc_update.SCRIPT_PATH}")

    def test_script_path_file_exists(self):
        import cc_update
        self.assertTrue(os.path.exists(cc_update.SCRIPT_PATH),
                        f"SCRIPT_PATH file not found: {cc_update.SCRIPT_PATH}")

    def test_pythonw_is_non_empty_string(self):
        import cc_update
        self.assertIsInstance(cc_update.PYTHONW, str)
        self.assertGreater(len(cc_update.PYTHONW), 0)

    def test_version_string_non_empty(self):
        vs = tr.version_string()
        self.assertIsInstance(vs, str)
        self.assertGreater(len(vs), 0)

    def test_release_uses_version_4_major(self):
        import cc_update
        self.assertEqual(cc_update.VERSION_MAJOR, 4)
        self.assertTrue(tr.version_string().startswith("4.0."))

    def test_is_git_deploy_returns_bool(self):
        result = tr.is_git_deploy()
        self.assertIsInstance(result, bool)

    def test_app_dir_same_across_modules(self):
        import cc_update
        # translator.pyw and cc_update.py are co-located; their APP_DIR should match.
        self.assertEqual(os.path.normcase(os.path.abspath(tr.APP_DIR)),
                         os.path.normcase(os.path.abspath(cc_update.APP_DIR)))

    def test_legacy_startup_vbs_path_is_in_startup_dir(self):
        import cc_update
        self.assertTrue(cc_update.LEGACY_STARTUP_VBS.startswith(cc_update.STARTUP_DIR))


# ============================================================
# log_error smoke test (writes to temp dir, not real DATA_DIR)
# ============================================================

class TestLogError(unittest.TestCase):
    def test_log_error_no_crash(self):
        """log_error must never raise, even with weird inputs."""
        tr.log_error("test_location", ValueError("test error"))

    def test_log_error_with_unicode_exc(self):
        tr.log_error("unicode_test", RuntimeError("错误：测试"))

    def test_log_error_writes_to_error_log(self):
        import cc_core
        orig_data_dir = cc_core.DATA_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            cc_core.DATA_DIR = tmpdir
            tr.log_error("write_test", Exception("sentinel_error_xyz"))
            log_path = os.path.join(tmpdir, "error.log")
            self.assertTrue(os.path.exists(log_path),
                            "log_error should create error.log")
            with open(log_path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("write_test", content)
            self.assertIn("sentinel_error_xyz", content)
        cc_core.DATA_DIR = orig_data_dir


class TestPerfLog(unittest.TestCase):
    def test_perf_log_keeps_only_safe_metadata(self):
        import cc_core
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "perf.log")
            with unittest.mock.patch.object(
                    cc_core, "PERF_LOG_PATH", path):
                cc_core.log_perf("provider_complete", {
                    "provider": "codex_cli",
                    "model": "gpt-5.4-mini",
                    "chars": 42,
                    "total_ms": 1234,
                    "input": "private source text",
                    "err": "secret path",
                })
            with open(path, encoding="utf-8") as log_file:
                record = json.loads(log_file.read())

        self.assertEqual(record["stage"], "provider_complete")
        self.assertEqual(record["provider"], "codex_cli")
        self.assertEqual(record["total_ms"], 1234)
        self.assertEqual(record["runtime"], "test")
        self.assertNotIn("input", record)
        self.assertNotIn("err", record)
        self.assertNotIn("private source text", json.dumps(record))

    def test_perf_log_rotates_at_bound(self):
        import cc_core
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "perf.log")
            with open(path, "wb") as log_file:
                log_file.write(b"x" * 100)
            with unittest.mock.patch.object(
                    cc_core, "PERF_LOG_PATH", path), \
                    unittest.mock.patch.object(
                        cc_core, "PERF_LOG_MAX_BYTES", 110):
                cc_core.log_perf("translate_done", {"wall_ms": 5})

            self.assertTrue(os.path.exists(path + ".1"))
            with open(path, encoding="utf-8") as log_file:
                self.assertEqual(
                    json.loads(log_file.read())["wall_ms"], 5)

    def test_dogfood_summary_uses_only_recent_app_route_events(self):
        import cc_core
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "perf.log")
            records = [
                {"ts": "2026-08-05T12:00:00", "runtime": "app",
                 "stage": "provider_route_complete",
                 "provider": "codex_cli", "model": "auto",
                 "route": "streamed", "outcome": "success",
                 "wall_ms": 1000},
                {"ts": "2026-08-05T12:01:00", "runtime": "app",
                 "stage": "provider_route_complete",
                 "provider": "codex_cli", "model": "gpt-5.4-mini",
                 "route": "stable_fallback", "outcome": "success",
                 "wall_ms": 3000},
                {"ts": "2026-08-05T12:02:00", "runtime": "app",
                 "stage": "provider_route_complete",
                 "provider": "codex_cli", "model": "auto",
                 "route": "stream_failed", "outcome": "failed",
                 "wall_ms": 2000},
                {"ts": "2026-08-05T12:03:00", "runtime": "test",
                 "stage": "provider_route_complete",
                 "provider": "codex_cli", "model": "auto",
                 "route": "streamed", "outcome": "success",
                 "wall_ms": 1},
                {"ts": "2026-07-01T12:00:00", "runtime": "app",
                 "stage": "provider_route_complete",
                 "provider": "codex_cli", "model": "auto",
                 "route": "stable_exec", "outcome": "success",
                 "wall_ms": 1},
                {"ts": "2026-08-06T12:00:00", "runtime": "app",
                 "stage": "provider_route_complete",
                 "provider": "codex_cli", "model": "auto",
                 "route": "streamed", "outcome": "success",
                 "wall_ms": 1},
                {"ts": "2026-08-05T12:04:00",
                 "stage": "provider_stream_complete",
                 "provider": "codex_cli", "model": "auto",
                 "route": "streamed", "outcome": "success",
                 "wall_ms": 1},
                {"ts": "2026-08-05T12:05:00", "runtime": "app",
                 "stage": "provider_stream_complete",
                 "provider": "codex_cli", "model": "auto",
                 "ok": True, "first_result_ms": 500, "total_ms": 2500},
                {"ts": "2026-08-05T12:06:00", "runtime": "app",
                 "stage": "provider_stream_complete",
                 "provider": "codex_cli", "model": "auto",
                 "ok": True, "first_result_ms": 800, "total_ms": 2600},
                {"ts": "2026-08-05T12:07:00", "runtime": "app",
                 "stage": "provider_complete",
                 "provider": "codex_cli", "model": "auto",
                 "ok": True, "chars": 500, "total_ms": 2000},
                {"ts": "2026-08-05T12:08:00", "runtime": "app",
                 "stage": "provider_complete",
                 "provider": "codex_cli", "model": "auto",
                 "ok": True, "chars": 600, "total_ms": 3000},
                {"ts": "2026-08-05T12:09:00", "runtime": "app",
                 "stage": "provider_complete",
                 "provider": "codex_cli", "model": "auto",
                 "ok": True, "chars": 20, "total_ms": 100},
            ]
            with open(path, "w", encoding="utf-8") as log_file:
                for record in records:
                    log_file.write(json.dumps(record) + "\n")

            summary = cc_core.summarize_provider_dogfood(
                path=path, now=datetime(2026, 8, 5, 17, 0, 0))

        self.assertEqual(summary["sample_count"], 3)
        self.assertEqual(summary["success_count"], 2)
        self.assertEqual(summary["failed_count"], 1)
        self.assertEqual(summary["route_counts"]["streamed"], 1)
        self.assertEqual(summary["route_counts"]["stable_fallback"], 1)
        self.assertEqual(summary["p50_ms"], 2000)
        self.assertEqual(summary["p95_ms"], 3000)
        self.assertEqual(summary["observation_days"], 1)
        self.assertEqual(summary["stream_first_result_count"], 2)
        self.assertEqual(summary["stream_first_result_p95_ms"], 800)
        self.assertEqual(summary["stable_long_text_count"], 2)
        self.assertEqual(summary["stable_long_text_p95_ms"], 3000)
        self.assertEqual(summary["model_counts"], {
            "auto": 2, "gpt-5.4-mini": 1})

    def _rollout_summary(self, **overrides):
        summary = {
            "sample_count": 200,
            "observation_days": 7,
            "stream_first_result_p95_ms": 800,
            "stable_long_text_p95_ms": 3000,
            "route_counts": {"stream_failed": 0},
        }
        summary.update(overrides)
        return summary

    def test_rollout_gate_collects_until_request_volume_is_met(self):
        import cc_core
        result = cc_core.evaluate_codex_rollout(
            self._rollout_summary(sample_count=199))

        self.assertEqual(result["status"], "collecting")
        self.assertEqual(result["reason"], "request_volume")

    def test_rollout_gate_blocks_post_output_stream_failures(self):
        import cc_core
        result = cc_core.evaluate_codex_rollout(self._rollout_summary(
            route_counts={"stream_failed": 1}))

        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(result["reason"], "post_output_failures")

    def test_rollout_gate_requires_first_text_p95_improvement(self):
        import cc_core
        result = cc_core.evaluate_codex_rollout(self._rollout_summary(
            stream_first_result_p95_ms=3500))

        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(result["reason"], "p95_not_improved")

    def test_rollout_gate_never_auto_approves_manual_safety_checks(self):
        import cc_core
        result = cc_core.evaluate_codex_rollout(self._rollout_summary())

        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(result["reason"], "manual_safety_checks")
        self.assertIn("process_cleanup", result["manual_checks"])
        self.assertIn("cross_request_isolation", result["manual_checks"])


class TestInstallerContracts(unittest.TestCase):
    def test_one_click_installer_includes_both_model_clis(self):
        with open(
                os.path.join(tr.APP_DIR, "install.ps1"),
                encoding="utf-8") as script_file:
            script = script_file.read()

        self.assertIn("@anthropic-ai/claude-code@latest", script)
        self.assertIn("@openai/codex@0.146.0", script)
        self.assertIn("codex login status", script)
        self.assertIn("'codex.exe', 'codex.cmd'", script)
        self.assertNotIn("auth.json", script)

    def test_codex_streaming_setting_uses_beta_label(self):
        self.assertEqual(
            tr.i18n.TRANSLATIONS["zh_CN"][
                "settings.label.codex_streaming"],
            "Codex 长文流式 (Beta)")
        self.assertEqual(
            tr.i18n.TRANSLATIONS["en_US"][
                "settings.label.codex_streaming"],
            "Codex long-text streaming (Beta)")


# ============================================================
# is_single_word edge cases not in existing tests
# ============================================================

class TestIsSingleWordExtra(unittest.TestCase):
    def test_empty_string(self):
        self.assertFalse(tr.is_single_word(""))

    def test_none_does_not_crash(self):
        # None input should return False without raising.
        try:
            result = tr.is_single_word(None)
            self.assertFalse(result)
        except (TypeError, AttributeError):
            self.fail("is_single_word(None) raised unexpectedly")

    def test_mixed_cjk_latin(self):
        # Mixed CJK+Latin as a single short compound: should still be a "word".
        result = tr.is_single_word("AI助手")
        self.assertIsInstance(result, bool)    # must not crash

    def test_tab_is_same_as_space(self):
        # A tab-separated two-token term behaves identically to space-separated.
        self.assertTrue(tr.is_single_word("word\tanother"))   # 2 tokens, same as "machine learning"

    def test_newline_is_sentence(self):
        self.assertFalse(tr.is_single_word("word\nanother"))


# ============================================================
# Smoke-test the entire module loads in isolation (no Tk)
# ============================================================

class TestModuleLoadSmokeTest(unittest.TestCase):
    def test_all_module_level_constants_are_accessible(self):
        constants = [
            "APP_NAME", "APP_DIR", "DATA_DIR", "CONFIG_PATH", "HISTORY_PATH",
            "ICON_PATH", "ICON_PATH_DARK", "ICON_PATH_LIGHT",
            "SUPPORT_IMAGE_PATH",
            "MIN_POPUP_HEIGHT", "MIN_STREAM_VISIBLE_HEIGHT",
            "LOADING_SPINNER", "POPUP_CORNER_RADIUS", "LOADING_CORNER_RADIUS",
            "CENTERED_POPUP_W", "CENTERED_POPUP_H",
            "TRIGGER_POLL_MS", "TRIGGER_SETTLE_MS", "CLIP_RESTORE_MS",
            "DIRECTION_MODES", "DIRECTION_LABELS", "LANGUAGES",
            "DEFAULT_CONFIG", "THEMES", "CFG",
            "ROUND_KEY_COLOR",
        ]
        for c in constants:
            self.assertTrue(hasattr(tr, c), f"translator.pyw missing constant: {c}")

    def test_themes_dict_has_dark_and_light(self):
        self.assertIn("dark", tr.THEMES)
        self.assertIn("light", tr.THEMES)

    def test_both_themes_have_required_keys(self):
        for name in ("dark", "light"):
            for k in REQUIRED_THEME_KEYS:
                self.assertIn(k, tr.THEMES[name],
                              f"THEMES['{name}'] missing key '{k}'")

    def test_popup_layout_labels_is_dict(self):
        self.assertIsInstance(tr.POPUP_LAYOUT_LABELS, dict)
        self.assertGreater(len(tr.POPUP_LAYOUT_LABELS), 0)

    def test_theme_labels_is_dict(self):
        self.assertIsInstance(tr.THEME_LABELS, dict)
        self.assertIn("dark", tr.THEME_LABELS)
        self.assertIn("light", tr.THEME_LABELS)
        self.assertIn("system", tr.THEME_LABELS)

    def test_support_image_path_exists(self):
        self.assertTrue(os.path.exists(tr.SUPPORT_IMAGE_PATH),
                        f"support image missing: {tr.SUPPORT_IMAGE_PATH}")

    def test_fit_box_size_preserves_aspect(self):
        self.assertEqual(tr.fit_box_size(100, 50, 120, 120), (100, 50, 1.0))
        self.assertEqual(tr.fit_box_size(100, 50, 50, 50), (50, 25, 0.5))


class TestSupportAuthorWindow(unittest.TestCase):
    def test_support_strings_present(self):
        self.assertEqual(tr.i18n.TRANSLATIONS["zh_CN"]["about.support_author"], "请作者喝杯咖啡")
        self.assertEqual(tr.i18n.TRANSLATIONS["en_US"]["about.support_author"], "Buy me a coffee")
        self.assertEqual(tr.i18n.TRANSLATIONS["zh_CN"]["support.title"], "请作者喝杯咖啡")
        self.assertEqual(tr.i18n.TRANSLATIONS["en_US"]["support.title"], "Buy me a coffee")
        self.assertEqual(tr.i18n.TRANSLATIONS["zh_CN"]["support.image_missing"], "支持图片暂不可用。")
        self.assertEqual(tr.i18n.TRANSLATIONS["en_US"]["support.image_missing"], "Support image unavailable.")

    def test_despeckle_removes_transparency_key_color(self):
        """Any pixel equal to the rounded-window transparency key would be
        punched transparent by Win32 and leak the background; despeckle must
        remap those to opaque pure black so nothing bleeds through."""
        try:
            from PIL import Image, ImageChops
        except ImportError:
            self.skipTest("Pillow not installed")
        key = tr.ROUND_KEY_COLOR.lstrip("#")
        kr, kg, kb = (int(key[i:i + 2], 16) for i in (0, 2, 4))
        # Build an image that contains the key colour, pure black, and white.
        img = Image.new("RGB", (4, 1), (255, 255, 255))
        img.putpixel((0, 0), (kr, kg, kb))   # exact key colour
        img.putpixel((1, 0), (0, 0, 0))       # pure black
        img.putpixel((2, 0), (kr, kg, kb))   # exact key colour again
        out = tr.TranslatorApp._despeckle_key_color(img)
        # No pixel may still equal the key colour.
        colors = [out.getpixel((x, 0)) for x in range(out.width)]
        self.assertNotIn((kr, kg, kb), colors)
        # Former key pixels became pure black; black/white are untouched.
        self.assertEqual(out.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(out.getpixel((1, 0)), (0, 0, 0))
        self.assertEqual(out.getpixel((2, 0)), (0, 0, 0))
        self.assertEqual(out.getpixel((3, 0)), (255, 255, 255))

    def test_despeckle_noops_for_distinctive_key(self):
        """A non-near-black key can never collide with QR content, so the
        image must be returned unchanged (fast path)."""
        try:
            from PIL import Image, ImageChops
        except ImportError:
            self.skipTest("Pillow not installed")
        import cc_app_settings
        orig = cc_app_settings.ROUND_KEY_COLOR
        try:
            cc_app_settings.ROUND_KEY_COLOR = "#ff00ff"
            img = Image.new("RGB", (2, 1), (0, 0, 0))
            out = tr.TranslatorApp._despeckle_key_color(img)
            self.assertIs(out, img)
        finally:
            cc_app_settings.ROUND_KEY_COLOR = orig


# ============================================================
# OCR screenshot translation (cc_ocr pure functions + wiring)
# ============================================================

import cc_ocr


class TestOCRModule(unittest.TestCase):
    def test_public_api_present(self):
        for name in ("grab_region", "save_region", "local_ocr_available",
                     "available_ocr_languages", "ocr_local",
                     "pick_ocr_result", "set_log_error"):
            self.assertTrue(hasattr(cc_ocr, name),
                            f"cc_ocr missing {name}")

    def test_set_log_error_wires_callback(self):
        captured = {}

        def fake(where, exc):
            captured["where"] = where

        cc_ocr.set_log_error(fake)
        try:
            self.assertIs(cc_ocr._log_error, fake)
        finally:
            cc_ocr.set_log_error(cc_ocr._noop_log_error)

    def test_cjk_detection(self):
        self.assertTrue(cc_ocr._is_cjk("中"))
        self.assertTrue(cc_ocr._is_cjk("あ"))
        self.assertFalse(cc_ocr._is_cjk("a"))
        self.assertFalse(cc_ocr._is_cjk("1"))
        self.assertEqual(cc_ocr._cjk_count("你好abc世界"), 4)
        self.assertEqual(cc_ocr._cjk_count("hello"), 0)

    def test_pick_prefers_cjk_engine_when_cjk_present(self):
        results = [("en-US", "Mixed 123"),
                   ("zh-Hans-CN", "你好世界 Mixed 123")]
        self.assertEqual(cc_ocr.pick_ocr_result(results),
                         "你好世界 Mixed 123")

    def test_pick_prefers_english_when_no_cjk(self):
        results = [("en-US", "Hello world clean"),
                   ("zh-Hans-CN", "He llo worl d")]
        self.assertEqual(cc_ocr.pick_ocr_result(results),
                         "Hello world clean")

    def test_pick_ignores_empty_and_whitespace(self):
        self.assertEqual(
            cc_ocr.pick_ocr_result([("en-US", ""), ("zh-Hans-CN", "   ")]),
            "")

    def test_pick_returns_empty_for_empty_input(self):
        self.assertEqual(cc_ocr.pick_ocr_result([]), "")

    def test_pick_most_cjk_wins(self):
        results = [("zh-Hans-CN", "你好"), ("ja-JP", "你好世界你好")]
        self.assertEqual(cc_ocr.pick_ocr_result(results), "你好世界你好")

    def test_target_tags_include_english_and_chinese(self):
        tags = cc_ocr._target_language_tags(["en-US", "zh-Hans-CN"])
        self.assertIn("en-US", tags)
        self.assertIn("zh-Hans-CN", tags)

    def test_target_tags_adds_extra_when_available(self):
        tags = cc_ocr._target_language_tags(
            ["en-US", "zh-Hans-CN", "ja-JP"], extra_langs=["ja"])
        self.assertIn("ja-JP", tags)

    def test_target_tags_skips_unavailable_extra(self):
        tags = cc_ocr._target_language_tags(
            ["en-US", "zh-Hans-CN"], extra_langs=["ko"])
        self.assertNotIn("ko", tags)
        self.assertNotIn("ko-KR", tags)

    def test_target_tags_no_duplicates(self):
        tags = cc_ocr._target_language_tags(
            ["en-US", "zh-Hans-CN"], extra_langs=["en", "en-US"])
        self.assertEqual(len(tags), len(set(tags)))

    def test_target_tags_fallback_to_available(self):
        tags = cc_ocr._target_language_tags(["fr-FR"])
        self.assertEqual(tags, ["fr-FR"])

    def test_available_languages_returns_list(self):
        self.assertIsInstance(cc_ocr.available_ocr_languages(), list)

    def test_local_ocr_available_returns_bool(self):
        self.assertIsInstance(cc_ocr.local_ocr_available(), bool)


class TestOCRIntegrationInApp(unittest.TestCase):
    def test_ocr_engine_labels_present(self):
        self.assertIn("claude", tr.OCR_ENGINE_LABELS)
        self.assertIn("local", tr.OCR_ENGINE_LABELS)

    def test_default_ocr_engine_in_labels(self):
        self.assertIn(tr.DEFAULT_CONFIG[tr.CFG.OCR_ENGINE],
                      tr.OCR_ENGINE_LABELS)

    def test_ocr_defaults(self):
        self.assertEqual(tr.DEFAULT_CONFIG[tr.CFG.OCR_ENGINE], "claude")
        self.assertIsInstance(
            tr.DEFAULT_CONFIG[tr.CFG.OCR_HOTKEY_ENABLED], bool)

    def test_vision_prompt_is_nonempty_string(self):
        self.assertIsInstance(tr.OCR_VISION_PROMPT, str)
        self.assertGreater(len(tr.OCR_VISION_PROMPT), 0)

    def test_vision_prompt_mentions_layout_preservation(self):
        self.assertIn("换行", tr.OCR_VISION_PROMPT)
        self.assertIn("项目符号", tr.OCR_VISION_PROMPT)
        self.assertIn("编号", tr.OCR_VISION_PROMPT)

    def test_vision_mention_is_quoted(self):
        # DATA_DIR has a space ("CC Translate"); the @mention MUST be quoted so
        # the CLI reads the file instead of breaking at the space. Regression
        # guard for the "I need permission to read the image" bug.
        m = tr.vision_image_mention(r"C:\Users\me\CC Translate\tmp_ocr.png")
        self.assertTrue(m.startswith('@"'))
        self.assertTrue(m.endswith('"'))
        self.assertIn("CC Translate", m)

    def test_vision_mention_uses_forward_slashes(self):
        m = tr.vision_image_mention(r"C:\a\b\img.png")
        self.assertNotIn("\\", m)
        self.assertIn("C:/a/b/img.png", m)


class TestHistoryHelpers(unittest.TestCase):
    def test_history_kind_backcompat_flags(self):
        self.assertEqual(tr.history_entry_kind({"is_code": True}), "code")
        self.assertEqual(tr.history_entry_kind({"is_dict": True}), "dict")
        self.assertEqual(tr.history_entry_kind({}), "text")

    def test_history_tag_uses_ocr_label(self):
        self.assertEqual(
            tr.history_entry_tag({"kind": "ocr"}),
            tr.i18n.get("history.tag.ocr"))

    def test_history_preview_falls_back_to_output(self):
        preview = tr.history_entry_preview(
            {"input": "", "output": "  Hello   world  "}, limit=5)
        self.assertEqual(preview, "Hello")

    def test_filter_history_entries_by_kind_and_query(self):
        entries = [
            {"input": "hello world", "output": "你好世界", "kind": "text"},
            {"input": "def f(): pass", "output": "代码说明", "kind": "code"},
        ]
        self.assertEqual(len(tr.filter_history_entries(entries, kind="code")), 1)
        self.assertEqual(len(tr.filter_history_entries(entries, query="HELLO")), 1)
        self.assertEqual(len(tr.filter_history_entries(entries, query="missing")), 0)


class TestDiagnosticsHelpers(unittest.TestCase):
    def test_infer_backend_defaults_to_subscription(self):
        info = tr.infer_claude_backend({})
        self.assertEqual(info["mode"], "subscription")

    def test_codex_report_uses_provider_specific_streaming_section(self):
        app = object.__new__(tr.TranslatorApp)
        snapshot = {
            "app": {
                "version": "test",
                "git_deploy": True,
                "app_dir": r"C:\app",
                "data_dir": r"C:\data",
                "config_path": r"C:\data\config.json",
                "history_path": r"C:\data\history.json",
                "cwd": r"C:\work",
            },
            "backend": {"label": "Claude subscription", "model": None},
            "selected_provider": {
                "id": "codex_cli",
                "label": "OpenAI GPT (Codex)",
                "model": "gpt-5.4-mini",
                "status": {
                    "version": "codex-cli 0.146.0",
                    "authenticated": True,
                    "auth_method": "chatgpt",
                    "command": r"C:\codex.exe",
                },
                "streaming": {
                    "enabled": True,
                    "version_supported": True,
                    "min_chars": 400,
                    "last_route": {
                        "mode": "stable_fallback",
                        "error_code": "appserver_exited",
                    },
                },
                "dogfood": {
                    "days": 7,
                    "observation_days": 3,
                    "sample_count": 3,
                    "success_count": 2,
                    "cancelled_count": 0,
                    "failed_count": 1,
                    "p50_ms": 2000,
                    "p95_ms": 3000,
                    "route_counts": {
                        "streamed": 1,
                        "stable_exec": 1,
                        "stable_fallback": 1,
                        "stream_cancelled": 0,
                        "stream_failed": 0,
                    },
                    "model_counts": {
                        "auto": 2,
                        "gpt-5.4-mini": 1,
                    },
                    "stream_first_result_count": 1,
                    "stream_first_result_p95_ms": 700,
                    "stable_long_text_count": 1,
                    "stable_long_text_p95_ms": 2500,
                },
            },
            "app_model": "gpt-5.4-mini",
            "claude_cli": {
                "version": "claude test",
                "resolved": r"C:\claude.exe",
            },
            "login": {
                "summary": "Claude login",
                "path": r"C:\claude.json",
            },
            "powershell_policy": {"value": "RemoteSigned"},
            "endpoint_probe": None,
            "model_route_note": "Claude routing",
            "last_result": {
                "ok": None,
                "title": "",
                "preview": "",
            },
            "advice": ["No issue"],
            "actions": ["Retry"],
            "runtime_env": {"ANTHROPIC_API_KEY": "secret"},
            "settings_sources": [],
            "recent_errors": "",
        }

        report = app._format_diagnostics_report(snapshot)

        self.assertIn("CODEX_CMD", report)
        self.assertIn("appserver_exited", report)
        self.assertIn("P50 2000 ms / P95 3000 ms", report)
        self.assertIn("auto: 2", report)
        self.assertIn("3/200", report)
        self.assertIn("700 ms", report)
        self.assertNotIn("CLAUDE_CMD", report)
        self.assertNotIn("ANTHROPIC_API_KEY", report)
        self.assertNotIn("Claude routing", report)

    def test_infer_backend_detects_agent_maestro(self):
        info = tr.infer_claude_backend({
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:23333/api/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "Powered by Agent Maestro",
        })
        self.assertEqual(info["mode"], "agent_maestro")
        self.assertEqual(
            info["label"], tr.i18n.get("diagnostics.backend.agent_maestro"))

    def test_infer_backend_detects_custom_endpoint(self):
        info = tr.infer_claude_backend({
            "ANTHROPIC_BASE_URL": "https://example.com/v1/anthropic",
        })
        self.assertEqual(info["mode"], "custom_endpoint")

    def test_describe_model_routing_without_override(self):
        note = tr.describe_model_routing("sonnet", "subscription", "")
        self.assertEqual(note, tr.i18n.get("diagnostics.routing.no_proxy"))

    def test_describe_model_routing_with_proxy_override(self):
        note = tr.describe_model_routing(
            "sonnet", "agent_maestro", "claude-fable-5")
        self.assertEqual(
            note,
            tr.i18n.get("diagnostics.routing.proxy_override").format(
                backend_model="claude-fable-5"))

    def test_build_diagnostics_actions_for_cli_and_login(self):
        actions = tr.build_diagnostics_actions({
            "backend": {"mode": "subscription"},
            "login": {"ok": False},
            "claude_cli": {"ok": False},
            "endpoint_probe": None,
            "powershell_policy": {"value": "Unrestricted"},
            "last_result": {"ok": None, "detail": "", "preview": ""},
        })
        self.assertIn(tr.i18n.get("diagnostics.action.fix_cli"), actions)
        self.assertIn(
            tr.i18n.get("diagnostics.action.login_subscription"), actions)

    def test_build_diagnostics_actions_timeout_hint(self):
        actions = tr.build_diagnostics_actions({
            "backend": {"mode": "subscription"},
            "login": {"ok": True},
            "claude_cli": {"ok": True},
            "endpoint_probe": None,
            "powershell_policy": {"value": "Unrestricted"},
            "last_result": {
                "ok": False,
                "detail": "OCR timeout, please retry.",
                "preview": "",
            },
        })
        self.assertIn(
            tr.i18n.get("diagnostics.action.retry_after_timeout"), actions)


# ============================================================
# Headless UI smoke tests
# ============================================================
#
# WHY THIS EXISTS: the rest of the suite imports translator.pyw as a module,
# which only executes the top-level `def`s — it never *instantiates* the app or
# *builds* any window. That left a whole class of bugs invisible: a dropped
# `def` line silently merges a method's body into the previous method (still
# valid Python, imports fine, ast.parse passes), and a build-time exception in
# a dialog is swallowed. Both only surface when the window is actually built.
#
# These tests build each real Tk dialog headlessly (root is withdrawn, windows
# are destroyed immediately) and assert it succeeds. That exercises the true
# code path — the same one a user hits — so missing methods, orphaned bodies,
# bad grid/pack calls, and i18n key typos are caught in CI instead of by the
# user. If no display/Tk is available (e.g. a headless Linux CI box), the whole
# class skips rather than failing spuriously.

_SHARED_ROOT = None


def _get_shared_root():
    """Return one process-wide hidden Tk root, created lazily on the main
    thread.

    Reusing a single Tcl interpreter across every UI test avoids the
    ``Tcl_AsyncDelete: async handler deleted by the wrong thread`` abort that
    Tk raises when several interpreters are created and later finalized by the
    garbage collector on a non-main thread. That abort left the interpreter
    with a nonzero exit code even though all tests passed — which matters
    because the auto-updater treats a nonzero test exit as a broken update and
    rolls back. The root is torn down once in ``tearDownModule``."""
    global _SHARED_ROOT
    import tkinter as tk
    if _SHARED_ROOT is not None:
        try:
            if _SHARED_ROOT.winfo_exists():
                return _SHARED_ROOT
        except Exception:
            pass
    root = tk.Tk()
    root.withdraw()
    _SHARED_ROOT = root
    return _SHARED_ROOT


def _make_headless_app():
    """Construct a TranslatorApp without running __init__ (which starts the
    hotkey listener, tray icon, warm pool and background threads). We only wire
    up the minimum state the window builders read, so building a dialog
    exercises the same code a user triggers."""
    app = object.__new__(tr.TranslatorApp)
    app._fresh_install = False
    app.cfg = tr.Config(dict(tr.DEFAULT_CONFIG))
    lang = app.cfg.get(tr.CFG.LANGUAGE) or "en_US"
    tr.i18n.initialize(lang)
    app.theme = tr.resolve_theme(app.cfg)
    app.root = _get_shared_root()
    app.root.withdraw()
    app.settings_win = None
    app.history_win = None
    app.about_win = None
    app.support_win = None
    app.diagnostics_win = None
    app.quick_input_win = None
    app._settings_check = None
    app._setup_scrollbar_style()
    return app


class TestUiSmoke(unittest.TestCase):
    """Build each real dialog headlessly and assert it succeeds. These are the
    tests that would have caught the settings-window crash (a dropped
    `def _install_combo_chevron` line)."""

    @classmethod
    def setUpClass(cls):
        try:
            _get_shared_root()   # probe: create the shared root once, on main thread
        except Exception as e:   # no display / Tk unavailable
            raise unittest.SkipTest(f"Tk not available: {e}")

    def _build(self, method_name):
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        getattr(app, method_name)()
        return app

    @staticmethod
    def _safe_destroy(app):
        """Destroy the dialog windows this app built and release any images it
        cached, but leave the shared Tk root alive (it is torn down once in
        ``tearDownModule``). Clearing the per-app image caches here lets the
        PhotoImage objects be finalized while the interpreter is still alive on
        the main thread, instead of during interpreter shutdown."""
        for name in (
                "quick_input_win", "settings_win", "history_win",
                "about_win", "support_win", "diagnostics_win"):
            w = getattr(app, name, None)
            if w is None:
                continue
            try:
                if tr.tk.Toplevel.winfo_exists(w):
                    w.destroy()
            except Exception:
                pass
        for cache_attr in ("_logo_cache", "_emoji_cache", "_support_img_cache"):
            try:
                cache = getattr(app, cache_attr, None)
                if isinstance(cache, dict):
                    cache.clear()
            except Exception:
                pass
        try:
            app.root.update_idletasks()
        except Exception:
            pass

    def test_settings_window_builds(self):
        app = self._build("_open_settings")
        self.assertTrue(app.settings_win is not None
                        and tr.tk.Toplevel.winfo_exists(app.settings_win),
                        "settings window should exist after _open_settings()")

    def test_settings_comboboxes_share_width(self):
        # Every settings dropdown must line up: they should all declare the same
        # character width so no single field (e.g. the model picker) sticks out.
        app = self._build("_open_settings")

        def walk(w):
            yield w
            for child in w.winfo_children():
                yield from walk(child)

        widths = {int(w.cget("width"))
                  for w in walk(app.settings_win)
                  if isinstance(w, tr.ttk.Combobox)}
        self.assertEqual(len(widths), 1,
                         f"comboboxes should share one width, got {widths}")

    def test_settings_dropdown_list_has_left_padding(self):
        # The popdown listbox needs a flat background-coloured inset so item text
        # isn't flush against the popup's left edge.
        app = self._build("_open_settings")

        def walk(w):
            yield w
            for child in w.winfo_children():
                yield from walk(child)

        combo = next((w for w in walk(app.settings_win)
                      if isinstance(w, tr.ttk.Combobox)), None)
        self.assertIsNotNone(combo, "a settings combobox should exist")
        popdown = combo.tk.call("ttk::combobox::PopdownWindow", combo)
        listbox = f"{popdown}.f.l"
        self.assertGreaterEqual(int(combo.tk.call(listbox, "cget", "-borderwidth")), 4)
        self.assertEqual(str(combo.tk.call(listbox, "cget", "-relief")), "flat")

    def test_settings_restore_defaults_repopulates(self):
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        # Seed non-default values so the restore has something visible to undo.
        app.cfg[tr.CFG.MODEL] = "opus"
        app.cfg[tr.CFG.CLAUDE_MODEL] = "opus"
        app.cfg[tr.CFG.FONT_SIZE] = 20
        app._open_settings()
        win = app.settings_win

        def walk(w):
            yield w
            for child in w.winfo_children():
                yield from walk(child)

        widgets = list(walk(win))

        # Locate the model combobox by its fixed value set (now display labels).
        model_labels = tr.get_model_labels()
        model_combo = None
        for w in widgets:
            if isinstance(w, tr.ttk.Combobox):
                try:
                    vals = list(w.cget("values"))
                except Exception:
                    vals = []
                if model_labels["haiku"] in vals and model_labels["opus"] in vals:
                    model_combo = w
                    break
        self.assertIsNotNone(model_combo, "model combobox should exist")
        self.assertEqual(model_combo.get(), model_labels["opus"])

        # Locate and click the Restore Defaults button.
        label = tr.i18n.get("settings.label.restore_defaults")
        restore_btn = None
        for w in widgets:
            if isinstance(w, tr.tk.Button) and w.cget("text") == label:
                restore_btn = w
                break
        self.assertIsNotNone(restore_btn,
                             "restore-defaults button should exist in footer")
        restore_btn.invoke()

        # The form now shows defaults; nothing is persisted until Save.
        self.assertEqual(model_combo.get(),
                         model_labels[tr.DEFAULT_CONFIG[tr.CFG.MODEL]])
        self.assertEqual(app.cfg[tr.CFG.MODEL], "opus",
                         "restore should not persist until Save is clicked")

    def test_about_window_builds(self):
        self._build("_open_about")

    def test_history_window_builds(self):
        self._build("_open_history")

    def test_diagnostics_window_builds(self):
        self._build("_open_diagnostics")

    def test_quick_input_window_builds(self):
        app = self._build("_open_quick_input")
        self.assertTrue(app.quick_input_win is not None
                        and tr.tk.Toplevel.winfo_exists(app.quick_input_win),
                        "quick input window should exist after _open_quick_input()")
        btn = getattr(app.quick_input_win, "_quick_input_submit_btn", None)
        self.assertTrue(btn is not None and btn.winfo_exists(),
                        "quick input window should expose a visible translate button")

    def test_result_popup_pin_toggle(self):
        # Result popups default to NOT always-on-top; the header pushpin opts in.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        win = app._make_popup("hello world")

        def _kill():
            try:
                if tr.tk.Toplevel.winfo_exists(win):
                    win.destroy()
            except Exception:
                pass
        self.addCleanup(_kill)

        self.assertFalse(getattr(win, "_pinned", None),
                         "result popup should default to not pinned")
        # The popup does a brief topmost "activation pulse" on reveal (so a
        # borderless window actually comes to the foreground and can be sent
        # behind by clicking another app), then releases it after ~90ms. Let
        # that release fire before asserting it is not permanently on top.
        deadline = time.time() + 1.0
        while int(win.attributes("-topmost")) == 1 and time.time() < deadline:
            win.update()
            time.sleep(0.02)
        self.assertEqual(int(win.attributes("-topmost")), 0,
                         "result popup must not be always-on-top by default")
        # Regression guard for the "black corners" bug: the popup must round its
        # corners with the transparent colour key (genuinely transparent), not
        # SetWindowRgn region clipping (which rendered opaque/black here).
        self.assertTrue(hasattr(win, "_round_redraw"),
                        "result popup should use the colour-key rounded card")
        self.assertEqual(
            str(win.wm_attributes("-transparentcolor")).lower(),
            tr.ROUND_KEY_COLOR.lower(),
            "result popup must set its transparent colour key")
        pin_btn = getattr(win, "_pin_btn", None)
        self.assertTrue(pin_btn is not None and pin_btn.winfo_exists(),
                        "result popup header should expose a pin button")

        app._toggle_popup_pin(win, pin_btn)
        self.assertTrue(win._pinned)
        self.assertEqual(int(win.attributes("-topmost")), 1,
                         "clicking the pin should make the popup topmost")

        app._toggle_popup_pin(win, pin_btn)
        self.assertFalse(win._pinned)
        self.assertEqual(int(win.attributes("-topmost")), 0,
                         "clicking the pin again should release topmost")

    def test_centered_popup_reveals_on_screen(self):
        # Regression guard for the off-screen measure/paint reveal and the
        # unified layout: a centered popup must end up on-screen, horizontally
        # centred on the monitor, never left parked at the off-screen (-4000)
        # measurement position.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "centered"
        win = app._make_popup("centered reveal")

        def _kill():
            try:
                if tr.tk.Toplevel.winfo_exists(win):
                    win.destroy()
            except Exception:
                pass
        self.addCleanup(_kill)

        win.update_idletasks()
        wx, wy = win.winfo_x(), win.winfo_y()
        # The off-screen measurement park is at -4000; the popup must have been
        # moved to its real on-screen position, never left at the park.
        self.assertNotEqual((wx, wy), (-4000, -4000),
                            "centered popup must not remain parked off-screen")
        # Unified layout: a centred popup is sized by the SAME content-driven
        # measurement as follow-cursor mode; only its POSITION is centred. So
        # assert it is horizontally centred on the active monitor for its
        # actual (content) width rather than pinned to a fixed centred box.
        rect = tr.get_monitor_rect()
        if rect:
            left, top, right, bottom = rect
            expected_x = left + ((right - left) - win.winfo_width()) // 2
            self.assertLessEqual(
                abs(wx - expected_x), 4,
                "centered popup should be horizontally centred on the monitor")

    def test_dynamic_stream_first_frame_reveals_once_without_size_jump(self):
        # Regression guard for the popup flicker: in dynamic layout the first
        # streaming frame used to reveal the window fitted to the tiny partial
        # chunk (e.g. 150x150) and then immediately re-size/re-position to the
        # stream-grow width (e.g. 630x132) — a visible flash + jump. The build
        # step must now defer its reveal so the ONLY on-screen positioned frame
        # is the single stream-grow geometry.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        app._anim_job = None
        app._last_class = "text"
        app._last_origin = "text"
        app._last_input = "hi"
        app.popup = None
        app._ss = tr.StreamSession()
        app._ss.centered_ready = False
        app.popup = app._make_loading_popup()

        onscreen = []
        orig_geo = tr.tk.Wm.wm_geometry

        def spy(self, newGeometry=None):
            r = orig_geo(self, newGeometry)
            if newGeometry is not None and "+" in str(newGeometry):
                try:
                    self.update_idletasks()
                    x = int(self.winfo_x())
                    mapped = bool(self.winfo_ismapped())
                except Exception:
                    x, mapped = -99999, False
                # Only count genuine on-screen frames (not the -4000 park).
                if mapped and x > -3000:
                    size = str(newGeometry).split("+", 1)[0]
                    onscreen.append(size)
            return r

        tr.tk.Wm.wm_geometry = spy
        tr.tk.Wm.geometry = spy
        try:
            app._stream_update("first partial chunk of streamed text arriving")
        finally:
            tr.tk.Wm.wm_geometry = orig_geo
            tr.tk.Wm.geometry = orig_geo

        def _kill():
            try:
                if app.popup is not None and app.popup.winfo_exists():
                    app.popup.destroy()
            except Exception:
                pass
        self.addCleanup(_kill)

        # The first streaming frame must produce exactly one on-screen size — no
        # fitted-then-grow jump. (Position may still be clamped, but a second
        # distinct on-screen SIZE is the visible flicker we are guarding against.)
        distinct_sizes = list(dict.fromkeys(onscreen))
        self.assertEqual(
            len(distinct_sizes), 1,
            f"dynamic stream first frame must reveal at one on-screen size, "
            f"got sequence {onscreen}")
        # The 'stuck on top' bug: _bring_to_front must make the window the true
        # foreground window via its REAL top-level HWND (not Tk's inner frame),
        # and when that activation succeeds it must NOT leave a topmost pulse
        # behind (which is what made clicking another app fail to send it back).
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        win = tr.tk.Toplevel(app.root)
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        win.overrideredirect(True)
        win.geometry("200x120+300+200")
        win.deiconify()
        win.update_idletasks()

        calls = {}

        def fake_top(hwnd):
            calls["top_arg"] = hwnd
            return 424242

        def fake_activate(hwnd):
            calls["activate_arg"] = hwnd
            return True

        with unittest.mock.patch.object(tr.win32util, "get_toplevel_hwnd",
                                        side_effect=fake_top), \
                unittest.mock.patch.object(tr.win32util, "activate_foreground",
                                           side_effect=fake_activate):
            app._bring_to_front(win)

        self.assertEqual(calls.get("top_arg"), int(win.winfo_id()),
                         "should resolve the real top-level from winfo_id()")
        self.assertEqual(calls.get("activate_arg"), 424242,
                         "should activate the resolved top-level HWND, not the inner frame")
        win.update()
        self.assertEqual(int(win.attributes("-topmost")), 0,
                         "successful activation must not leave the window topmost")

    def test_bring_to_front_falls_back_to_topmost_when_activation_refused(self):
        # If the OS refuses foreground activation, fall back to a brief topmost
        # pulse so the window at least becomes visible.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        win = tr.tk.Toplevel(app.root)
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        win.overrideredirect(True)
        win.geometry("200x120+300+200")
        win.deiconify()
        win.update_idletasks()

        with unittest.mock.patch.object(tr.win32util, "get_toplevel_hwnd",
                                        return_value=1), \
                unittest.mock.patch.object(tr.win32util, "activate_foreground",
                                           return_value=False):
            app._bring_to_front(win)
        win.update()
        self.assertEqual(int(win.attributes("-topmost")), 1,
                         "refused activation should fall back to a topmost pulse")
        # ...which then self-releases.
        deadline = time.time() + 1.0
        while int(win.attributes("-topmost")) == 1 and time.time() < deadline:
            win.update()
            time.sleep(0.02)
        self.assertEqual(int(win.attributes("-topmost")), 0,
                         "fallback topmost pulse must release itself")

    def test_tray_click_action_dispatch_routes_to_configured_action(self):
        # Left-clicking the tray icon must run the configured action, resolved
        # live from config, and fall back to Settings for unknown/legacy values.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))

        cases = {
            "settings": "open_settings",
            "history": "open_history",
            "quick_input": "open_quick_input",
            "screenshot": "_ocr_from_menu",   # dispatched via root.after
            "some_unknown_value": "open_settings",   # legacy/invalid -> Settings
        }
        for action, expected_method in cases.items():
            app.cfg[tr.CFG.TRAY_CLICK_ACTION] = action
            with unittest.mock.patch.object(app, "open_settings") as m_settings, \
                    unittest.mock.patch.object(app, "open_history") as m_history, \
                    unittest.mock.patch.object(app, "open_quick_input") as m_quick, \
                    unittest.mock.patch.object(app, "_ocr_from_menu") as m_ocr, \
                    unittest.mock.patch.object(app.root, "after",
                                               side_effect=lambda ms, fn: fn()):
                app._run_tray_click_action()
            called = {
                "open_settings": m_settings.called,
                "open_history": m_history.called,
                "open_quick_input": m_quick.called,
                "_ocr_from_menu": m_ocr.called,
            }
            self.assertTrue(called[expected_method],
                            f"action {action!r} should call {expected_method}")
            # Exactly one action should fire.
            self.assertEqual(sum(called.values()), 1,
                             f"action {action!r} should trigger exactly one handler")

    def test_result_popup_root_coords_follow_geometry(self):
        # Regression guard for the taskbar-style toggle: result popups must keep
        # sane Tk geometry coordinates (rootx/rooty should match x/y), otherwise
        # drag/resize math treats interior clicks as edge-resize hits.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        win = app._make_popup("coordinate sanity", anchor=(460, 320))

        def _kill():
            try:
                if tr.tk.Toplevel.winfo_exists(win):
                    win.destroy()
            except Exception:
                pass
        self.addCleanup(_kill)

        self.assertEqual(win.winfo_x(), 460)
        self.assertEqual(win.winfo_y(), 320)
        self.assertEqual(
            (win.winfo_rootx(), win.winfo_rooty()),
            (win.winfo_x(), win.winfo_y()),
            "result popup should preserve Tk root coordinates after taskbar styling")

    def test_popup_press_uses_window_coords_when_root_coords_are_bad(self):
        # If winfo_rootx/y are stale (0,0), _popup_press must still use the real
        # window position so a center click doesn't accidentally enter resize mode.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        win = app._make_popup("drag me", anchor=(500, 360))

        def _kill():
            try:
                if tr.tk.Toplevel.winfo_exists(win):
                    win.destroy()
            except Exception:
                pass
        self.addCleanup(_kill)

        app.popup = win
        app._resize_mode = None
        app._resize_start = None

        class _E:
            pass

        e = _E()
        e.x_root = win.winfo_x() + max(24, win.winfo_width() // 3)
        e.y_root = win.winfo_y() + max(24, win.winfo_height() // 3)
        with unittest.mock.patch.object(win, "winfo_x", return_value=win.winfo_x()), \
                unittest.mock.patch.object(win, "winfo_y", return_value=win.winfo_y()), \
                unittest.mock.patch.object(win, "winfo_rootx", return_value=0), \
                unittest.mock.patch.object(win, "winfo_rooty", return_value=0):
            app._popup_press(e)
        self.assertIsNone(
            app._resize_mode,
            "interior click should not be misdetected as resize when root coords are stale")

    def test_dynamic_popup_wrapped_line_does_not_inflate_height(self):
        # Regression guard: a fresh non-streaming (follow-cursor) popup is
        # measured while still withdrawn. If displaylines are counted before the
        # window is mapped, the Text reports a 1px width, wrap="word" folds every
        # character onto its own line, and the count explodes (a 2-line sentence
        # measured as dozens of lines) — producing a hugely over-tall window with
        # a big empty gap and a spurious scrollbar. _size_popup now maps the
        # window off-screen before counting, so the wrapped-line count is real.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        # A single logical line long enough to wrap to ~2 display lines at the
        # ~48-column cap (mirrors the reported screenshot).
        msg = "Rather than streaming paths that are inherently short content"
        win = app._make_popup(msg, anchor=(300, 300))

        def _kill():
            try:
                if tr.tk.Toplevel.winfo_exists(win):
                    win.destroy()
            except Exception:
                pass
        self.addCleanup(_kill)

        # The Text is configured to the true wrapped-line count, not the 22-line
        # max cap. This is DPI-independent: buggy == 22, fixed == a few.
        lines = int(win._text.cget("height"))
        self.assertLess(
            lines, 10,
            f"wrapped short content should size to a few lines, got {lines} "
            f"(a large value means displaylines were counted before mapping)")
        # Such short content must not trigger the overflow scrollbar.
        self.assertFalse(
            win._scroll.winfo_ismapped(),
            "a 2-line result should not show the overflow scrollbar")

    def test_dynamic_popup_last_line_not_clipped(self):
        # Regression guard: _size_popup must reserve the body frame's bottom pad
        # (pady=(0, POPUP_BODY_PAD_BOTTOM)) in the window height. That gap sits
        # inside the rounded card but is NOT part of the Text's own reqheight, so
        # omitting it squeezed the Text below its requested height and clipped the
        # last line's descenders (the "p" in "unmapped" showed only its top half).
        # DPI-independent: the check is reqheight vs the height actually granted.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        for msg in (
            'Real culprit: counting lines when the window is "unmapped"',
            "The quick brown fox jumps over the lazy dog and keeps going until "
            "it wraps onto a second display line for testing purposes",
        ):
            win = app._make_popup(msg, anchor=(300, 300))

            def _kill(w=win):
                try:
                    if tr.tk.Toplevel.winfo_exists(w):
                        w.destroy()
                except Exception:
                    pass
            self.addCleanup(_kill)
            win.update_idletasks()
            win.update()
            text = win._text
            req = text.winfo_reqheight()
            got = text.winfo_height()
            self.assertGreaterEqual(
                got, req,
                f"Text was squeezed ({got}px < requested {req}px) for {msg!r}; "
                f"the missing pixels clip the last line's descenders. "
                f"_size_popup must add POPUP_BODY_PAD_BOTTOM to the window height.")

    def test_dynamic_long_result_widens_short_stays_compact(self):
        # Issue 1: a long follow-cursor result must widen toward the centred
        # card's width so it doesn't wrap into a tall, narrow column; a short
        # result must still hug its content. DPI-independent: relative widths.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        centered = app._centered_width_px()

        short = app._make_popup("hello world", anchor=(300, 300))
        self.addCleanup(lambda w=short: self._safe_win_destroy(w))
        short.update_idletasks()
        short_w = short.winfo_width()

        long_msg = (
            "This is a fairly long non-streaming translation result that used "
            "to be squeezed into a narrow column, making the popup very tall; "
            "it should now widen toward the centred card width so the same text "
            "reads across fewer, longer lines instead of many short ones.")
        wide = app._make_popup(long_msg, anchor=(300, 300))
        self.addCleanup(lambda w=wide: self._safe_win_destroy(w))
        wide.update_idletasks()
        wide_w = wide.winfo_width()

        self.assertLess(
            short_w, centered * 0.6,
            f"a short result should stay compact, got {short_w}px "
            f"(centred card is {centered}px)")
        self.assertGreater(
            wide_w, short_w,
            "a long result must be wider than a short one")
        self.assertGreaterEqual(
            wide_w, centered * 0.85,
            f"a long result should widen close to the centred card width "
            f"({centered}px), got {wide_w}px")

    def test_dynamic_stream_width_matches_centered_card(self):
        # Issue 1: streaming width is locked to the centred card's width from the
        # first frame so long streamed output doesn't scroll forever in a narrow
        # column. DPI-independent: compare against _centered_width_px().
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        app._ss = tr.StreamSession()
        centered = app._centered_width_px()
        win = app._make_popup("first streamed chunk", anchor=(300, 300),
                              reveal=False)
        self.addCleanup(lambda w=win: self._safe_win_destroy(w))
        w, _h = app._size_popup_stream_grow(
            win, "first streamed chunk that keeps growing as the model streams")
        self.assertGreaterEqual(
            w, centered * 0.9,
            f"stream width should match the centred card ({centered}px), "
            f"got {w}px")

    def test_dynamic_stream_opens_modest_then_grows(self):
        # Contract: a streamed popup must OPEN at a modest few-line height — not
        # a one-line sliver, and NOT the tall centred-card height (which would
        # balloon a short summary and then force a collapse). It then only ever
        # grows downward with content, never shrinks. DPI-independent: bound the
        # opening height by line metrics and the centred-card height.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        app._ss = tr.StreamSession()
        floor = app._centered_height_px()
        win = app._make_popup("摘要", anchor=(300, 300), reveal=False)
        self.addCleanup(lambda w=win: self._safe_win_destroy(w))
        # A tiny first chunk opens at the modest few-line floor, NOT a sliver
        # and NOT the tall centred-card height.
        _w, h1 = app._size_popup_stream_grow(win, "## 摘要")
        line_px = max(win._text_font.metrics("linespace") + 6, 14)
        self.assertGreaterEqual(
            h1, 2 * line_px,
            f"first streamed frame should not be a one-line sliver, got {h1}px")
        self.assertLess(
            h1, floor * 0.8,
            f"first streamed frame should open MODEST (a few lines), not at the "
            f"tall centred-card height ({floor}px), got {h1}px")
        # A later, much longer frame must grow beyond the opening, never shrink.
        big = "## 摘要\n" + "\n".join(
            "这是第%d行内容用于测试流式增长" % i for i in range(1, 40))
        _w, h2 = app._size_popup_stream_grow(win, big)
        self.assertGreater(
            h2, h1, "a longer streamed frame must grow the window")
        self.assertGreaterEqual(
            h2, h1, "streaming height must never shrink between frames")

    def test_dynamic_stream_low_cursor_reserves_room_to_grow(self):
        # When the trigger cursor is near the screen bottom, the anchor must be
        # pushed up so there is ROOM to grow into (reservation uses the taller
        # centred-card height) — even though a short first chunk opens modest.
        # So: (1) the window always fits above the screen bottom, and (2) a long
        # stream can grow to near the reserved height instead of being crushed
        # into a short strip above the taskbar.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        reserve = app._centered_height_px()
        app._ss = tr.StreamSession()
        screen_bottom = app.root.winfo_screenheight()
        low_y = screen_bottom - 30
        win = app._make_popup("摘要", anchor=(300, low_y), reveal=False)
        self.addCleanup(lambda w=win: self._safe_win_destroy(w))
        # Short first chunk: opens modest but the anchor is already reserved.
        app._size_popup_stream_grow(win, "## 摘要\n2020年后经济环境变化")
        # A long stream then grows into the reserved room.
        big = "## 摘要\n" + "\n".join(
            "第%d行较长的翻译内容用于占满高度" % i for i in range(1, 40))
        _w, h = app._size_popup_stream_grow(win, big)
        # The monitor the code actually locked onto (may be a work-area rect).
        _l, _t, _r, bottom = app._ss.monitor_rect
        # The whole window (anchor + height) must fit above the screen bottom.
        self.assertLessEqual(
            app._ss.origin_y + h, bottom + 2,
            f"low-cursor stream window (origin {app._ss.origin_y} + h {h}) "
            f"should fit above monitor bottom {bottom}")
        # And room was reserved: a long stream grows to near the reserved height
        # instead of being crushed into a short strip.
        if bottom - 40 >= reserve:  # only when the screen is tall enough
            self.assertGreaterEqual(
                h, reserve * 0.9,
                f"low-cursor stream should reserve room to grow into "
                f"(~{reserve}px), long content only reached {h}px")

    def test_dynamic_stream_drag_survives_growth(self):
        # Regression: while a follow-cursor popup streams in, every frame used to
        # re-apply the anchor position, so a window the user dragged mid-stream
        # snapped straight back to origin on the next frame ("跳回去"). Later
        # frames now resize ONLY (size-only geometry) and keep the top-left where
        # it is, so a mid-stream drag sticks and the card just grows downward.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        app._ss = tr.StreamSession()
        win = app._make_popup("摘要", anchor=(300, 300), reveal=False)
        self.addCleanup(lambda w=win: self._safe_win_destroy(w))
        app.popup = win
        # First streamed frame: positions the window at the anchor, placed=True.
        app._set_popup_text("## 摘要\n第一段内容", stream_grow=True)
        win.update_idletasks(); win.update()
        self.assertTrue(app._ss.placed)
        _x0, _y0 = app._window_xy(win)
        h0 = win.winfo_height()
        # User drags the window to a clearly different spot mid-stream.
        drag_x, drag_y = _x0 + 220, _y0 + 140
        win.geometry(f"+{drag_x}+{drag_y}")
        win.update_idletasks(); win.update()
        # A later, longer streamed frame arrives.
        big = "## 摘要\n" + "\n".join(
            "这是第%d行流式内容" % i for i in range(1, 30))
        app._set_popup_text(big, stream_grow=True)
        win.update_idletasks(); win.update()
        nx, ny = app._window_xy(win)
        # Position must stay where the user dragged it, NOT snap back to origin.
        self.assertLess(
            abs(nx - drag_x), 8,
            f"streamed frame should keep the dragged X {drag_x}, got {nx} "
            f"(snapped back toward origin)")
        self.assertLess(
            abs(ny - drag_y), 8,
            f"streamed frame should keep the dragged Y {drag_y}, got {ny} "
            f"(snapped back toward origin)")
        # And the card should have grown downward (taller), never shrunk.
        self.assertGreaterEqual(
            win.winfo_height(), h0,
            "streamed frame should grow the window height, not shrink it")

    def test_dynamic_stream_scroll_position_survives_frame(self):
        # Regression ("闪来闪去" while scrolling mid-stream): each streamed frame
        # rebuilds the Text (delete + reinsert), which snaps the view to the top.
        # The forced update() calls that measure the line count then PAINTED the
        # top for a frame before the scroll position was restored, so a reader
        # who had scrolled up saw the popup flash to the top and jump back ~20×/s.
        # A scrolled reader's top line must stay put across the next frame.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        app._ss = tr.StreamSession()
        # Force a short viewport so the streamed content overflows and scrolls.
        app._ss.monitor_rect = (0, 0, 900, 320)
        long_msg = "\n".join("第%d行流式内容" % i for i in range(1, 60))
        win = app._make_popup(long_msg, anchor=(100, 60), reveal=False)
        self.addCleanup(lambda w=win: self._safe_win_destroy(w))
        app.popup = win
        app._set_popup_text(long_msg, stream_grow=True)
        win.update_idletasks(); win.update()
        # Content must overflow the short viewport (only part is visible), so the
        # view is genuinely scrollable and a scroll position exists to preserve.
        self.assertLess(
            win._text.yview()[1], 1.0,
            "test setup: streamed content should overflow the short viewport")
        # User scrolls down to read, opting out of stream auto-pin-to-top.
        app._ss.user_scrolled = True
        win._text.yview_moveto(0.5)
        win.update_idletasks(); win.update()
        top_before = int(float(win._text.index("@0,0")))
        self.assertGreater(
            top_before, 1,
            "test setup: expected the view to be scrolled off the first line")
        # A later, longer streamed frame arrives (append-only growth). Call the
        # grow/measure step DIRECTLY: the flicker is the transient repaint at the
        # top that happens INSIDE it (its forced update() calls) before the
        # position is restored. The fix restores the position inside this step,
        # so the view is already back at the reader's line when it returns —
        # rather than sitting at the top waiting for the outer _set_popup_text
        # restore, which is what flashed on screen frame after frame.
        bigger = long_msg + "\n" + "\n".join(
            "追加第%d行" % i for i in range(1, 10))
        app._size_popup_stream_grow(win, bigger)
        win.update_idletasks(); win.update()
        top_after = int(float(win._text.index("@0,0")))
        self.assertEqual(
            top_before, top_after,
            f"scroll position must survive the grow/measure step: top line was "
            f"{top_before}, now {top_after} (view snapped toward top → flicker)")

    def test_dynamic_stream_short_output_stays_compact(self):
        # Root-cause regression (the "先大再小" report): streaming is gated on
        # INPUT length (>= STREAM_MIN_CHARS), but the OUTPUT can be short — a
        # summary compresses a long selection into a few lines, so it streams
        # (long input) yet renders short. It must OPEN compact and STAY compact
        # (grow-only lands exactly on the small content height) — never balloon
        # to the tall centred-card height and then collapse. DPI-independent.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        floor = app._centered_height_px()

        # SHORT streamed output (the summary case).
        app._ss = tr.StreamSession()
        short = "较短的摘要。\n只有两三行。\n没有更多内容。"
        w1 = app._make_popup(short, anchor=(400, 300), reveal=False)
        self.addCleanup(lambda w=w1: self._safe_win_destroy(w))
        _x, h_open = app._size_popup_stream_grow(w1, short)
        _x, h_short_final = app._size_popup_stream_grow(w1, short)
        self.assertLess(
            h_open, floor * 0.8,
            f"short streamed output must OPEN compact, not at the tall "
            f"centred-card height ({floor}px), got {h_open}px")
        # Grow-only: the last frame must never be SMALLER than the opening
        # (no visible shrink), and must stay compact (no big empty gap).
        self.assertGreaterEqual(
            h_short_final, h_open,
            f"streaming must never shrink: open {h_open}px -> final "
            f"{h_short_final}px")
        self.assertLess(
            h_short_final, floor * 0.8,
            f"a short summary must stay compact ({floor}px floor); got "
            f"{h_short_final}px (a value near the floor means the empty-gap or "
            f"balloon bug is back)")

        # LONG streamed output must stay tall (grows into reserved room).
        app._ss = tr.StreamSession()
        longtxt = "\n".join(
            "第%d行较长的翻译内容用于占满高度" % i
            for i in range(1, 40))
        w2 = app._make_popup(longtxt, anchor=(400, 80), reveal=False)
        self.addCleanup(lambda w=w2: self._safe_win_destroy(w))
        app._size_popup_stream_grow(w2, longtxt)
        _x, h_long_final = app._size_popup_stream_grow(w2, longtxt)
        self.assertGreater(
            h_long_final, h_short_final * 1.5,
            f"long streamed output must stay much taller than a short summary "
            f"({h_long_final}px vs {h_short_final}px)")

    def test_cycle_anchor_pins_result_to_trigger_cursor(self):
        # Issue 2: in follow-cursor layout every popup in a translate cycle must
        # anchor to the cursor captured at trigger time (_cycle_anchor), so the
        # result never jumps to wherever the mouse drifted mid-translation. The
        # cycle call sites (_show_result / _stream_update / _stream_finalize) all
        # route through _cycle_popup_anchor() -> _make_popup(anchor=...).
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "dynamic"
        app.popup = None
        app._cycle_anchor = (640, 480)
        # The shared anchor helper must hand back the captured trigger point.
        self.assertEqual(app._cycle_popup_anchor(), (640, 480))
        win = app._make_popup("done", anchor=app._cycle_popup_anchor())
        self.addCleanup(lambda w=win: self._safe_win_destroy(w))
        win.update_idletasks()
        x, y = app._window_xy(win)
        # May be clamped to the monitor, but must track the trigger point — not
        # the live pointer / a re-read cursor.
        self.assertLess(
            abs(x - 640), 400,
            f"result X {x} should track the trigger anchor 640, not a re-read "
            f"cursor")
        self.assertLess(
            abs(y - 480), 400,
            f"result Y {y} should track the trigger anchor 480, not a re-read "
            f"cursor")

    def test_centered_stream_is_centered_and_grows_down(self):
        # Unified layout: centred streaming reuses the SAME content-driven
        # stream-grow sizing as follow-cursor mode; only the POSITION differs
        # — the card is horizontally centred and grows DOWN from a fixed top
        # (方案1: open centred, then extend downward, no recentre jitter).
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "centered"
        app._ss = tr.StreamSession()
        win = app._make_popup("摘要", reveal=False)
        self.addCleanup(lambda w=win: self._safe_win_destroy(w))
        app.popup = win
        app._set_popup_text("## 摘要\n第一段内容", stream_grow=True)
        win.update_idletasks(); win.update()
        x0, y0 = app._window_xy(win)
        h0 = win.winfo_height()
        left, top, right, bottom = app._ss.monitor_rect
        expected_x = left + ((right - left) - win.winfo_width()) // 2
        self.assertLess(
            abs(x0 - expected_x), 6,
            f"centred stream should be horizontally centred: x={x0} "
            f"expected~{expected_x}")
        # A much longer frame grows DOWN: taller, same top, still centred.
        big = "## 摘要\n" + "\n".join("第%d行内容" % i for i in range(1, 40))
        app._set_popup_text(big, stream_grow=True)
        win.update_idletasks(); win.update()
        x1, y1 = app._window_xy(win)
        self.assertGreater(
            win.winfo_height(), h0, "centred stream must grow taller")
        self.assertLess(
            abs(y1 - y0), 8,
            f"centred stream top must stay fixed (grow down), y {y0}->{y1}")
        exp1 = left + ((right - left) - win.winfo_width()) // 2
        self.assertLess(
            abs(x1 - exp1), 6,
            "centred stream must stay horizontally centred as it grows")

    def test_centered_and_dynamic_stream_width_is_identical(self):
        # The whole point of the unification: both layouts share ONE sizing
        # path, so the SAME content yields the SAME (locked) stream width —
        # only the position differs.
        content = ("## 摘要\n"
                   + "\n".join("第%d行内容用于测试" % i for i in range(1, 25)))

        def measure(layout):
            app = _make_headless_app()
            self.addCleanup(lambda a=app: self._safe_destroy(a))
            app.cfg[tr.CFG.POPUP_LAYOUT] = layout
            app._ss = tr.StreamSession()
            win = app._make_popup("摘要", anchor=(400, 200), reveal=False)
            self.addCleanup(lambda w=win: self._safe_win_destroy(w))
            return app._size_popup_stream_grow(win, content)

        wc, _hc = measure("centered")
        wd, _hd = measure("dynamic")
        self.assertEqual(
            wc, wd,
            f"unified stream width should match: centred {wc} vs dynamic {wd}")

    def test_centered_stream_rich_renders_each_frame(self):
        # The user's explicit ask: centred mode must live-render markdown PER
        # frame like follow-cursor mode, not buffer plain text until the final
        # frame. The unified path calls the rich renderer (_fill_text) on every
        # streamed frame.
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        app.cfg[tr.CFG.POPUP_LAYOUT] = "centered"
        app._ss = tr.StreamSession()
        win = app._make_popup("摘要", reveal=False)
        self.addCleanup(lambda w=win: self._safe_win_destroy(w))
        app.popup = win
        real_fill = app._fill_text
        calls = []

        def spy(widget, msg):
            calls.append(msg)
            return real_fill(widget, msg)

        app._fill_text = spy
        # A NON-final streamed frame must already rich-render (not defer).
        app._set_popup_text("## 摘要\n**加粗**的内容", stream_grow=True)
        self.assertTrue(
            calls,
            "centred streaming must rich-render each frame, not defer to the "
            "final frame")

    def _safe_win_destroy(self, w):
        try:
            if tr.tk.Toplevel.winfo_exists(w):
                w.destroy()
        except Exception:
            pass

    def test_critical_ui_methods_exist(self):
        """Guard against orphaned/dropped method definitions: every method the
        window builders call on `self` must be a bound method, not missing."""
        required = [
            "_open_settings", "_open_about", "_open_history",
            "_open_diagnostics", "_open_support_author",
            "open_quick_input", "_open_quick_input",
            "_apply_ime_composition_font",
            "_setup_form_style", "_setup_scrollbar_style",
            "_install_combo_chevron", "_make_chevron_image",
            "_make_help_icon_image", "_help_badge_diameter",
            "_make_tooltip", "_make_toggle",
            "_make_draggable", "_pill_button", "_rounded_shell",
            "_settings_field", "_settings_section",
            "_settings_toggle_row", "_settings_toggle_row_with_action",
            "_confirm_and_uninstall",
        ]
        for name in required:
            self.assertTrue(
                callable(getattr(tr.TranslatorApp, name, None)),
                f"TranslatorApp.{name} is missing or not callable "
                f"(a dropped 'def' line can silently merge it into the "
                f"previous method)")

    def test_help_badge_diameter_tracks_label_metrics(self):
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        fake_font = unittest.mock.Mock()
        fake_font.metrics.return_value = 29
        with unittest.mock.patch.object(tr.tkfont, "Font", return_value=fake_font):
            diameter = app._help_badge_diameter(("Microsoft YaHei UI", 10))
        self.assertEqual(diameter, 22)

    def test_help_icon_uses_requested_pixel_diameter(self):
        app = _make_headless_app()
        self.addCleanup(lambda: self._safe_destroy(app))
        icon = app._make_help_icon_image("#667085", "#667085", "#ffffff",
                                         diameter=22)
        if icon is None:
            self.skipTest("PIL/ImageTk not available")
        self.assertEqual(icon.width(), 22)
        self.assertEqual(icon.height(), 22)


class TestUpdateStatusCopy(unittest.TestCase):
    def _run_check_only_update(self, state, remote_version=None):
        app = _make_headless_app()
        self.addCleanup(lambda: TestUiSmoke._safe_destroy(app))
        seen = []

        def on_status(msg, kind):
            seen.append((msg, kind))

        import cc_app_update
        with unittest.mock.patch.object(
                app.root, "after", side_effect=lambda _ms, fn: fn()), \
                unittest.mock.patch.object(cc_app_update, "is_git_deploy", return_value=True), \
                unittest.mock.patch.object(
                    tr._cc_update, "fetch_remote_branch", return_value=(True, "")), \
                unittest.mock.patch.object(
                    tr._cc_update, "remote_version_string", return_value=remote_version), \
                unittest.mock.patch.object(
                    tr._cc_update, "classify_update_state",
                    return_value=(state, "localsha", "remotesha1234567")):
            app._update_worker(silent=False, on_status=on_status, check_only=True)
        return seen

    def test_ahead_state_reports_known_latest(self):
        seen = self._run_check_only_update("ahead")
        self.assertEqual(seen, [(tr.i18n.get("update.no_update"), "ok")])

    def test_diverged_state_reports_known_latest(self):
        seen = self._run_check_only_update("diverged")
        self.assertEqual(seen, [(tr.i18n.get("update.no_update"), "ok")])

    def test_behind_state_reports_numeric_version(self):
        # A real update available shows the remote's numeric version, not a SHA.
        seen = self._run_check_only_update("behind", remote_version="4.0.321")
        self.assertEqual(
            seen,
            [(tr.i18n.get("update.found_version").format(version="4.0.321"),
              "avail")])

    def test_behind_state_falls_back_to_sha_when_version_unknown(self):
        # If the remote commit count can't be read, fall back to a short SHA
        # so the notice still says something concrete.
        seen = self._run_check_only_update("behind", remote_version=None)
        self.assertEqual(
            seen,
            [(tr.i18n.get("update.found_version").format(version="remotes"),
              "avail")])


class TestQuickInputFallback(unittest.TestCase):
    def _make_app(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.load_config()
        app.cfg[tr.CFG.MAX_CHARS] = 40
        app._clip_seq_before = 20
        app.root = unittest.mock.Mock()
        app._restore_clipboard = unittest.mock.Mock()
        app._show_loading = unittest.mock.Mock()
        app._open_quick_input = unittest.mock.Mock()
        return app

    def test_trigger_opens_quick_input_when_clipboard_not_updated(self):
        app = self._make_app()
        with unittest.mock.patch.object(tr.pyperclip, "paste",
                                        return_value="existing clipboard text"), \
                unittest.mock.patch.object(
                    app, "_clipboard_sequence", return_value=20):
            app._trigger()
        app._open_quick_input.assert_called_once_with()
        app._show_loading.assert_not_called()
        app.root.after.assert_called_once()

    def test_trigger_translates_when_clipboard_updated(self):
        app = self._make_app()
        # Mock the live Win32 focus probe so the test doesn't depend on whatever
        # control happens to be focused during the run (returning None means
        # "unknown", so _trigger falls through to the clipboard-sequence check).
        with unittest.mock.patch.object(tr.pyperclip, "paste",
                                        return_value="hello"), \
                unittest.mock.patch.object(
                    app, "_focused_control_has_selection", return_value=None), \
                unittest.mock.patch.object(
                    app, "_clipboard_sequence", return_value=21):
            app._trigger()
        app._open_quick_input.assert_not_called()
        app._show_loading.assert_called_once_with("hello")


class TestLoadingPopupDismiss(unittest.TestCase):
    """The 'translating…' loading hint auto-dismisses when it loses focus, but
    must ignore the focus churn that fires the instant it is revealed. Otherwise
    closing the quick-input window (which hands the OS foreground to another
    window, racing the popup's own activation) delivers a stray FocusOut that
    would close the hint before the user ever sees it."""

    def _app(self):
        app = object.__new__(tr.TranslatorApp)
        app._dismiss_loading_popup = unittest.mock.Mock()
        return app

    def test_focus_out_before_armed_is_ignored(self):
        app = self._app()
        win = types.SimpleNamespace(_dismiss_armed=False)
        app._on_loading_focus_out(win)
        app._dismiss_loading_popup.assert_not_called()

    def test_focus_out_after_armed_dismisses(self):
        app = self._app()
        win = types.SimpleNamespace(_dismiss_armed=True)
        app._on_loading_focus_out(win)
        app._dismiss_loading_popup.assert_called_once_with()

    def test_missing_flag_defaults_to_not_dismissing(self):
        # A window without the flag at all must not be dismissed (fail safe).
        app = self._app()
        win = types.SimpleNamespace()
        app._on_loading_focus_out(win)
        app._dismiss_loading_popup.assert_not_called()


class _FakePipe:
    """Minimal stand-in for a Popen stdin pipe."""
    def __init__(self):
        self.closed = False
        self.data = ""

    def write(self, s):
        self.data += s

    def close(self):
        self.closed = True


class _FakeStdout:
    """Iterable stand-in for a Popen stdout pipe yielding pre-canned lines."""
    def __init__(self, lines):
        self._it = iter(lines)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._it)

    def close(self):
        self.closed = True


class _FakeProc:
    """Minimal Popen stand-in for exercising _stream_claude deterministically."""
    def __init__(self, lines, returncode=0):
        self.stdin = _FakePipe()
        self.stdout = _FakeStdout(lines)
        self._rc = returncode
        self.returncode = None

    def wait(self):
        self.returncode = self._rc

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _sse(text):
    return json.dumps({"type": "stream_event",
                       "event": {"type": "content_block_delta",
                                 "delta": {"text": text}}})


def _result_event(result, is_error=False):
    return json.dumps({"type": "result", "is_error": is_error,
                       "result": result})


class TestStreamClaudeHardening(unittest.TestCase):
    """Cover the hardened cold streaming path: watchdog/cleanup, terminal
    success validation, and 'partial output is not success'. These paths drive
    the highest-risk subprocess code and previously had no mocked coverage."""

    def _make_app(self, job_id=7):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = {tr.CFG.MODEL: "sonnet",
                   tr.CFG.HISTORY_ENABLED: True,
                   tr.CFG.HISTORY_LIMIT: 100}
        app._ss = tr.StreamSession()
        app._job_id = job_id
        app._last_input = "source text to translate"
        app._last_origin = "text"
        app._last_class = "text"
        app.root = unittest.mock.Mock()
        app._system_prompt_for = lambda text: "SP"
        return app

    def _run_stream(self, lines, returncode=0):
        app = self._make_app()
        meta = {"input": app._last_input, "origin": "text",
                "is_code": False, "kind": "text"}
        proc = _FakeProc(lines, returncode=returncode)
        with unittest.mock.patch.object(tr.subprocess, "Popen",
                                        return_value=proc) as popen, \
                unittest.mock.patch.object(tr, "add_history") as add_history:
            ok = app._stream_claude("x" * 500, app._job_id, app._ss, meta)
        return app, proc, popen, add_history, ok

    def test_success_uses_terminal_result_event(self):
        lines = [_sse("Hello"), _sse(" world"),
                 _result_event("Hello world")]
        app, proc, popen, add_history, ok = self._run_stream(lines)
        self.assertTrue(ok)
        popen.assert_called_once()
        self.assertEqual(
            popen.call_args.args[0],
            [
                tr.CLAUDE_CMD, "-p", "--safe-mode", "--model", "sonnet",
                "--system-prompt", "SP",
                "--output-format", "stream-json",
                "--include-partial-messages", "--verbose",
                "--tools", "",
                "--exclude-dynamic-system-prompt-sections",
                "--no-session-persistence",
            ],
        )
        self.assertEqual(
            popen.call_args.kwargs,
            {
                "stdin": tr.subprocess.PIPE,
                "stdout": tr.subprocess.PIPE,
                "stderr": tr.subprocess.DEVNULL,
                "text": True,
                "encoding": "utf-8",
                "creationflags": tr.subprocess.CREATE_NO_WINDOW,
            },
        )
        self.assertEqual(proc.stdin.data, "<text>\n" + ("x" * 500) + "\n</text>")
        add_history.assert_called_once()
        # First positional arg is the original input text.
        self.assertEqual(add_history.call_args.args[0], app._last_input)
        # Pipes are cleaned up in the finally block.
        self.assertTrue(proc.stdout.closed)
        self.assertTrue(proc.stdin.closed)

    def test_stream_uses_request_snapshot_not_live_config(self):
        app = self._make_app()
        app.cfg[tr.CFG.MODEL] = "opus"
        meta = {
            "input": app._last_input,
            "origin": "text",
            "is_code": False,
            "kind": "text",
            "model": "haiku",
            "system_prompt": "SNAPSHOT PROMPT",
        }
        proc = _FakeProc([_result_event("done")])
        with unittest.mock.patch.object(
                tr.subprocess, "Popen", return_value=proc) as popen, \
                unittest.mock.patch.object(tr, "add_history"):
            ok = app._stream_claude(
                "x" * 500, app._job_id, app._ss, meta)

        self.assertTrue(ok)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertEqual(
            argv[argv.index("--system-prompt") + 1], "SNAPSHOT PROMPT")

    def test_error_result_event_is_failure(self):
        lines = [_sse("partial output"),
                 _result_event("", is_error=True)]
        app, proc, popen, add_history, ok = self._run_stream(lines)
        self.assertFalse(ok)
        add_history.assert_not_called()

    def test_partial_output_with_nonzero_returncode_is_not_success(self):
        # Deltas arrived but the CLI exited nonzero and never sent a result
        # event: the truncated text must NOT be treated as a translation.
        lines = [_sse("half a transl")]
        app, proc, popen, add_history, ok = self._run_stream(
            lines, returncode=1)
        self.assertFalse(ok)
        add_history.assert_not_called()

    def test_malformed_lines_are_skipped(self):
        lines = ["not json at all", "", _sse("Bonjour"),
                 _result_event("Bonjour")]
        app, proc, popen, add_history, ok = self._run_stream(lines)
        self.assertTrue(ok)
        add_history.assert_called_once()

    def test_empty_stream_returns_false(self):
        lines = [_result_event("")]   # no deltas, empty result
        app, proc, popen, add_history, ok = self._run_stream(lines)
        self.assertFalse(ok)
        add_history.assert_not_called()

    def test_history_skipped_when_disabled(self):
        app = self._make_app()
        app.cfg[tr.CFG.HISTORY_ENABLED] = False
        meta = {"input": app._last_input, "origin": "text",
                "is_code": False, "kind": "text"}
        proc = _FakeProc([_sse("Hi"), _result_event("Hi")])
        with unittest.mock.patch.object(tr.subprocess, "Popen",
                                        return_value=proc), \
                unittest.mock.patch.object(tr, "add_history") as add_history:
            ok = app._stream_claude("x" * 500, app._job_id, app._ss, meta)
        self.assertTrue(ok)
        add_history.assert_not_called()

    def test_record_history_uses_meta_not_live_state(self):
        # Simulate a newer request having already overwritten live self._last_*;
        # the persisted entry must still use this job's snapshot, so the input
        # and output can't be mismatched.
        app = self._make_app()
        app._last_input = "the NEW request's text"
        meta = {"input": "the OLD request's text", "origin": "text",
                "is_code": False, "kind": "text"}
        with unittest.mock.patch.object(tr, "add_history") as add_history:
            app._record_history(app._job_id, meta, "old output", is_dict=False)
        add_history.assert_called_once()
        self.assertEqual(add_history.call_args.args[0], "the OLD request's text")

    def test_record_history_skips_stale_job(self):
        app = self._make_app(job_id=8)
        meta = {"input": "x", "origin": "text", "is_code": False, "kind": "text"}
        with unittest.mock.patch.object(tr, "add_history") as add_history:
            app._record_history(3, meta, "out", is_dict=False)   # stale id
        add_history.assert_not_called()


class _FakeCompleted:
    """Stand-in for subprocess.run's CompletedProcess."""
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class TestCallClaudeOneShot(unittest.TestCase):
    """Mock coverage for the one-shot _call_claude subprocess path: JSON
    envelope, plain-text fallback, stderr → humanized error, and timeout.
    This is the highest-risk untested path (external review r7)."""

    def setUp(self):
        tr.i18n.initialize("en_US")

    def _make_app(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = {tr.CFG.MODEL: "sonnet"}
        app._system_prompt_for = lambda text: "SP"
        return app

    def _run(self, completed=None, side_effect=None):
        app = self._make_app()
        kw = {}
        if side_effect is not None:
            kw["side_effect"] = side_effect
        else:
            kw["return_value"] = completed
        with unittest.mock.patch.object(tr.subprocess, "run", **kw) as run:
            ok, result = app._call_claude("hello world")
        return ok, result, run

    def test_json_envelope_result(self):
        ok, result, run = self._run(
            _FakeCompleted(stdout=json.dumps({"result": "你好世界"})))
        self.assertTrue(ok)
        self.assertEqual(result, "你好世界")
        self.assertEqual(
            run.call_args.args[0],
            [
                tr.CLAUDE_CMD, "-p", "--safe-mode", "--model", "sonnet",
                "--system-prompt", "SP",
                "--output-format", "json",
                "--tools", "",
                "--exclude-dynamic-system-prompt-sections",
                "--no-session-persistence",
            ],
        )
        self.assertEqual(
            {key: run.call_args.kwargs[key] for key in (
                "capture_output", "text", "encoding", "timeout",
                "creationflags")},
            {
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "timeout": 60,
                "creationflags": tr.subprocess.CREATE_NO_WINDOW,
            },
        )
        # Payload is passed via stdin, not as an argv element.
        self.assertEqual(run.call_args.kwargs["input"], "<text>\nhello world\n</text>")

    def test_plain_text_fallback_when_not_json(self):
        ok, result, run = self._run(_FakeCompleted(stdout="just plain text"))
        self.assertTrue(ok)
        self.assertEqual(result, "just plain text")

    def test_explicit_model_and_prompt_override_live_config(self):
        app = self._make_app()
        with unittest.mock.patch.object(
                tr.subprocess, "run",
                return_value=_FakeCompleted(
                    stdout=json.dumps({"result": "ok"}))) as run:
            ok, result = app._call_claude(
                "hello", "SNAPSHOT PROMPT", model="haiku")

        self.assertTrue(ok)
        self.assertEqual(result, "ok")
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertEqual(
            argv[argv.index("--system-prompt") + 1], "SNAPSHOT PROMPT")

    def test_empty_result_falls_to_error(self):
        # Valid JSON but empty result, and empty stderr → "no result" error.
        ok, result, run = self._run(
            _FakeCompleted(stdout=json.dumps({"result": "   "})))
        self.assertFalse(ok)
        self.assertEqual(result, tr.i18n.get("error.no_result"))

    def test_stderr_login_required_is_humanized(self):
        ok, result, run = self._run(
            _FakeCompleted(stdout="", stderr="Error: not logged in"))
        self.assertFalse(ok)
        self.assertEqual(result, tr.i18n.get("error.login_required"))

    def test_stderr_rate_limited_is_humanized(self):
        ok, result, run = self._run(
            _FakeCompleted(stdout="", stderr="HTTP 429 rate limit exceeded"))
        self.assertFalse(ok)
        self.assertEqual(result, tr.i18n.get("error.rate_limited"))

    def test_timeout_returns_timeout_message(self):
        ok, result, run = self._run(
            side_effect=tr.subprocess.TimeoutExpired(cmd="claude", timeout=60))
        self.assertFalse(ok)
        self.assertEqual(result, tr.i18n.get("error.translation_timeout"))

    def test_unexpected_exception_is_caught(self):
        ok, result, run = self._run(side_effect=RuntimeError("boom"))
        self.assertFalse(ok)
        self.assertIn("boom", result)


class TestCallClaudeVision(unittest.TestCase):
    """Mock coverage for the vision OCR one-shot path: JSON success, plain-text
    fallback, bad/empty JSON → error, and timeout (external review r7)."""

    def setUp(self):
        tr.i18n.initialize("en_US")

    def _make_app(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = {tr.CFG.MODEL: "sonnet"}
        return app

    def _run(self, completed=None, side_effect=None):
        app = self._make_app()
        kw = {}
        if side_effect is not None:
            kw["side_effect"] = side_effect
        else:
            kw["return_value"] = completed
        with unittest.mock.patch.object(tr.subprocess, "run", **kw) as run:
            ok, result = app._call_claude_vision("C:\\x\\img.png")
        return ok, result, run

    def test_json_result_success(self):
        ok, result, run = self._run(
            _FakeCompleted(stdout=json.dumps({"result": "translated text"})))
        self.assertTrue(ok)
        self.assertEqual(result, "translated text")
        self.assertEqual(
            run.call_args.args[0],
            [
                tr.CLAUDE_CMD, "-p", "--safe-mode", "--model", "sonnet",
                "--system-prompt", tr.OCR_VISION_PROMPT,
                "--output-format", "json",
                "--tools", "",
                "--no-session-persistence",
            ],
        )
        self.assertEqual(run.call_args.kwargs["input"],
                         tr.vision_image_mention("C:\\x\\img.png"))
        self.assertEqual(run.call_args.kwargs["timeout"], 90)
        self.assertEqual(run.call_args.kwargs["creationflags"],
                         tr.subprocess.CREATE_NO_WINDOW)

    def test_plain_text_fallback(self):
        ok, result, run = self._run(_FakeCompleted(stdout="raw output"))
        self.assertTrue(ok)
        self.assertEqual(result, "raw output")

    def test_empty_output_is_error(self):
        ok, result, run = self._run(
            _FakeCompleted(stdout="", stderr="something failed"))
        self.assertFalse(ok)
        self.assertEqual(result,
                         tr.i18n.get("error.translation_failed_with_reason")
                         .format(error="something failed"))

    def test_timeout_returns_ocr_timeout_message(self):
        ok, result, run = self._run(
            side_effect=tr.subprocess.TimeoutExpired(cmd="claude", timeout=90))
        self.assertFalse(ok)
        self.assertEqual(result, tr.i18n.get("error.ocr_timeout"))

    def test_unexpected_exception_is_caught(self):
        ok, result, run = self._run(side_effect=RuntimeError("kaboom"))
        self.assertFalse(ok)
        self.assertIn("kaboom", result)


class TestJobIsolation(unittest.TestCase):
    """Cover the in-flight job guard that stops a superseded request from
    writing its result into a newer request's popup or history."""

    def _bare_app(self, job_id=5):
        app = object.__new__(tr.TranslatorApp)
        app._job_id = job_id
        app._ss = tr.StreamSession()
        app.root = unittest.mock.Mock()
        return app

    def test_begin_job_increments_and_reports_current(self):
        app = self._bare_app(job_id=0)
        jid = app._begin_job()
        self.assertEqual(jid, 1)
        self.assertTrue(app._job_is_current(1))
        self.assertFalse(app._job_is_current(0))
        jid2 = app._begin_job()
        self.assertEqual(jid2, 2)
        self.assertFalse(app._job_is_current(1))

    def test_begin_job_cancels_previous_provider_request(self):
        app = self._bare_app(job_id=0)
        app._begin_job()
        first = app._provider_cancel_event
        app._begin_job()
        self.assertTrue(first.is_set())
        self.assertFalse(app._provider_cancel_event.is_set())

    def test_stream_flush_ignores_stale_job(self):
        app = self._bare_app(job_id=5)
        app._stream_flush(job_id=3)   # stale
        self.assertIsNone(app._ss.flush_job)
        app.root.after.assert_not_called()

    def test_stream_finalize_ignores_stale_job(self):
        app = self._bare_app(job_id=5)
        app._cancel_stream_flush = unittest.mock.Mock()
        app._stream_finalize("done", job_id=3)   # stale
        app._cancel_stream_flush.assert_not_called()

    def test_show_result_ignores_stale_job(self):
        app = self._bare_app(job_id=5)
        app._stop_animation = unittest.mock.Mock()
        app._show_result(True, "translated", job_id=3)   # stale
        app._stop_animation.assert_not_called()

    def test_finish_ocr_local_ignores_stale_job(self):
        app = self._bare_app(job_id=5)
        app._stop_animation = unittest.mock.Mock()
        app._show_loading = unittest.mock.Mock()
        app._finish_ocr_local("recognised text", job_id=3)   # stale
        app._stop_animation.assert_not_called()
        app._show_loading.assert_not_called()

    def test_user_close_invalidates_inflight_job(self):
        # Closing the popup while a translation is streaming must invalidate the
        # current job so later stream frames / _stream_finalize can't re-create
        # the window the user just dismissed.
        app = self._bare_app(job_id=5)
        app._cancel_stream_flush = unittest.mock.Mock()
        app._destroy_popup = unittest.mock.Mock()
        app._user_close_popup()
        self.assertFalse(app._job_is_current(5),
                         "the streaming job must be stale after user close")
        app._destroy_popup.assert_called_once_with()

    def test_stream_finalize_no_repaint_after_user_close(self):
        # End-to-end: a streaming job's final frame arrives AFTER the user closed
        # the popup. It must not build a new popup.
        app = self._bare_app(job_id=5)
        app._cancel_stream_flush = unittest.mock.Mock()
        app._destroy_popup = unittest.mock.Mock()
        app._make_popup = unittest.mock.Mock()
        job_id = app._job_id
        app._user_close_popup()                 # user closes mid-stream
        app._stream_finalize("done", job_id=job_id)   # late final frame
        app._make_popup.assert_not_called()


class TestAtomicWrites(unittest.TestCase):
    """Cover the temp-file + os.replace() atomic persistence that protects
    config/history from truncation on a crash or hard os._exit mid-write."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._path = os.path.join(self._dir, "config.json")
        self._orig = tr.CONFIG_PATH
        tr.CONFIG_PATH = self._path

    def tearDown(self):
        tr.CONFIG_PATH = self._orig
        try:
            import shutil
            shutil.rmtree(self._dir, ignore_errors=True)
        except Exception:
            pass

    def _leftover_tmps(self):
        return [n for n in os.listdir(self._dir) if n.startswith(".tmp_")]

    def test_save_config_writes_valid_json_no_temp_left(self):
        tr.save_config({tr.CFG.THEME: "dark", tr.CFG.FONT_SIZE: 15})
        with open(self._path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data[tr.CFG.THEME], "dark")
        self.assertEqual(self._leftover_tmps(), [])

    def test_failed_write_preserves_original_and_cleans_temp(self):
        # Seed a good file, then make the JSON dump blow up mid-write.
        tr.save_config({tr.CFG.THEME: "light"})
        with unittest.mock.patch.object(
                tr.json, "dump", side_effect=ValueError("boom")):
            tr.save_config({tr.CFG.THEME: "dark"})   # swallowed by log_error
        # Original content survives intact; no partial temp file left behind.
        with open(self._path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data[tr.CFG.THEME], "light")
        self.assertEqual(self._leftover_tmps(), [])

    def test_atomic_write_json_roundtrip(self):
        p = os.path.join(self._dir, "hist.json")
        tr._atomic_write_json(p, [{"a": 1}, {"b": 2}])
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f), [{"a": 1}, {"b": 2}])


class TestShortcutQuoting(unittest.TestCase):
    """Cover that _create_shortcut escapes every interpolated path through
    _ps_squote, so a user/path containing an apostrophe can't break (or inject
    into) the generated PowerShell."""

    def test_create_shortcut_uses_ps_squote_for_all_paths(self):
        import cc_update
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return None

        link = r"C:\Users\O'Brien\Start Menu\CC Translate.lnk"
        with unittest.mock.patch.object(cc_update.subprocess, "run", fake_run), \
                unittest.mock.patch.object(cc_update, "PYTHONW",
                                           r"C:\Py'thon\pythonw.exe"), \
                unittest.mock.patch.object(cc_update, "SCRIPT_PATH",
                                           r"C:\App\translator.pyw"), \
                unittest.mock.patch.object(cc_update, "APP_DIR", r"C:\App"), \
                unittest.mock.patch.object(cc_update, "ICON_PATH",
                                           r"C:\App\icon.ico"):
            cc_update._create_shortcut(link)
        ps = captured["cmd"][-1]
        # The apostrophe paths must appear single-quote-doubled (escaped), never
        # as a bare '...{value}...' that an apostrophe would terminate early.
        self.assertIn("'C:\\Users\\O''Brien\\Start Menu\\CC Translate.lnk'", ps)
        self.assertIn("'C:\\Py''thon\\pythonw.exe'", ps)
        self.assertNotIn("O'Brien'", ps.replace("O''Brien", ""))


class TestTranslateAsTextEscapeHatch(unittest.TestCase):
    """The code-explain popup's '作为文字翻译' button re-runs the original input as
    a plain-text translation, overriding the code heuristic. Exercises the
    unbound mixin methods against a lightweight stub self."""

    def _app(self):
        app = object.__new__(tr.TranslatorApp)
        app._last_input = "print(x)"
        app._last_origin = "text"
        app._last_class = "code"
        app._show_loading = unittest.mock.Mock()
        return app

    def test_translate_as_text_forces_text_class(self):
        app = self._app()
        app._translate_as_text()
        app._show_loading.assert_called_once_with(
            "print(x)", origin="text", force_class="text")

    def test_translate_as_text_preserves_origin(self):
        app = self._app()
        app._last_origin = "ocr"
        app._translate_as_text()
        self.assertEqual(app._show_loading.call_args.kwargs["origin"], "ocr")

    def test_translate_as_text_noop_without_input(self):
        app = self._app()
        app._last_input = ""
        app._translate_as_text()
        app._show_loading.assert_not_called()


class TestMaybeAddAsTextButton(unittest.TestCase):
    """The '作为文字翻译' escape-hatch button appears only on code-explain popups
    and is added at most once."""

    def _win(self):
        made = {}

        def mk(label, cmd):
            btn = unittest.mock.Mock()
            made["label"] = label
            made["cmd"] = cmd
            made["btn"] = btn
            return btn

        win = types.SimpleNamespace()
        win._btn_bar = object()
        win._mk_bar_btn = mk
        return win, made

    def test_added_for_code_class(self):
        app = object.__new__(tr.TranslatorApp)
        app._last_class = "code"
        app._translate_as_text = lambda: None
        win, made = self._win()
        app._maybe_add_as_text_button(win)
        self.assertTrue(getattr(win, "_has_as_text_btn", False))
        self.assertEqual(made["label"], tr.i18n.get("result.as_text"))
        # The button's command is the escape-hatch handler.
        self.assertEqual(made["cmd"], app._translate_as_text)

    def test_skipped_for_non_code(self):
        app = object.__new__(tr.TranslatorApp)
        for cls in ("text", "mixed", "dict"):
            app._last_class = cls
            win, made = self._win()
            app._maybe_add_as_text_button(win)
            self.assertFalse(getattr(win, "_has_as_text_btn", False))
            self.assertEqual(made, {})

    def test_idempotent(self):
        app = object.__new__(tr.TranslatorApp)
        app._last_class = "code"
        app._translate_as_text = lambda: None
        win, made = self._win()
        app._maybe_add_as_text_button(win)
        self.assertTrue(getattr(win, "_has_as_text_btn", False))
        # A second pass must not build the button again.
        made.clear()
        app._maybe_add_as_text_button(win)
        self.assertEqual(made, {})


class _FakeText:
    """Minimal stand-in for the popup's text widget: stores content and answers
    .get(...) so the append helpers can read it back."""

    def __init__(self, content=""):
        self._content = content
        self._rich = False

    def get(self, a, b):
        return self._content


class TestFollowUpAppend(unittest.TestCase):
    """Follow-up actions (retranslation + rewrites) append below the existing
    translation with a labelled divider instead of replacing it, always
    transforming the primary result snapshot, and never write history."""

    def _app(self, primary="ORIG translation"):
        app = object.__new__(tr.TranslatorApp)
        app._last_input = "source text"
        app._last_origin = "text"
        app._last_class = "text"
        text = _FakeText(primary)
        win = types.SimpleNamespace(_text=text)
        app.popup = win
        # _set_popup_text updates the fake store so a later read reflects it,
        # and records the kwargs so tests can assert append=True is forwarded
        # (which drives the scroll-position-preserving path in cc_app_popup).
        app._set_popup_kw = []

        def _fake_set(content, **kw):
            text._content = content
            app._set_popup_kw.append(kw)

        app._set_popup_text = _fake_set
        app._result_title = lambda ok=True: "结果"
        app._remembered = []
        app._remember_result = lambda ok, title, txt: app._remembered.append((ok, title, txt))
        # If any path wrongly tried to write history, this would record it.
        app._add_history = unittest.mock.Mock()
        return app, win, text

    def test_append_preserves_original_and_labels(self):
        app, win, text = self._app(primary="ORIG")
        app._append_result_section("译成日语", "NIHONGO")
        divider = tr.i18n.get("result.section_divider").format(label="译成日语")
        self.assertEqual(text._content, "ORIG" + divider + "NIHONGO")

    def test_append_forwards_append_flag(self):
        # append=True tells _set_popup_text to keep the reader's scroll position
        # instead of snapping back to the top when the result grows.
        app, win, text = self._app(primary="ORIG")
        app._append_result_section("译成日语", "NIHONGO")
        self.assertTrue(app._set_popup_kw)
        self.assertTrue(app._set_popup_kw[-1].get("append"))

    def test_primary_snapshot_stable_across_appends(self):
        app, win, text = self._app(primary="ORIG")
        app._append_result_section("A", "aaa")
        app._append_result_section("B", "bbb")
        # The primary snapshot never changes as the visible text grows.
        self.assertEqual(win._primary_result, "ORIG")
        self.assertTrue(text._content.startswith("ORIG"))
        self.assertIn("aaa", text._content)
        self.assertIn("bbb", text._content)

    def test_primary_text_returns_snapshot_after_growth(self):
        app, win, text = self._app(primary="ORIG")
        self.assertEqual(app._result_primary_text(win), "ORIG")
        # Even if the visible text grows, the snapshot stays the original.
        text._content = "ORIG + appended junk"
        self.assertEqual(app._result_primary_text(win), "ORIG")

    def test_append_remembers_combined_text(self):
        app, win, text = self._app(primary="ORIG")
        app._append_result_section("译成日语", "NIHONGO")
        # Diagnostics 'last result' should reflect the full visible content.
        self.assertTrue(app._remembered)
        self.assertEqual(app._remembered[-1][2], text._content)

    def test_append_does_not_write_history(self):
        app, win, text = self._app()
        app._append_result_section("译成日语", "NIHONGO")
        app._add_history.assert_not_called()

    def test_apply_transform_appends_with_label(self):
        app, win, text = self._app()
        app._append_result_section = unittest.mock.Mock()
        app._apply_result_transform(True, "casual version", "改写为口语")
        app._append_result_section.assert_called_once_with("改写为口语", "casual version")

    def test_apply_transform_noop_on_failure(self):
        app, win, text = self._app()
        app._append_result_section = unittest.mock.Mock()
        app._apply_result_transform(False, "err", "改写为口语")
        app._append_result_section.assert_not_called()

    def test_apply_retranslation_appends_with_label(self):
        app, win, text = self._app()
        app._append_result_section = unittest.mock.Mock()
        app._apply_retranslation(True, "日本語訳", "译成日语")
        app._append_result_section.assert_called_once_with("译成日语", "日本語訳")

    def test_apply_retranslation_noop_on_failure(self):
        app, win, text = self._app()
        app._append_result_section = unittest.mock.Mock()
        app._apply_retranslation(False, "err", "译成日语")
        app._append_result_section.assert_not_called()

    def test_transform_feeds_primary_to_worker(self):
        # After a prior append grows the popup text, a rewrite must still send
        # the PRIMARY translation (not the grown text) to the model.
        import cc_app_results
        app, win, text = self._app(primary="ORIG")
        app._append_result_section("译成日语", "NIHONGO")  # grow the visible text
        captured = {}

        class _FakeThread:
            def __init__(self, target, args, daemon):
                captured["args"] = args

            def start(self):
                pass

        with unittest.mock.patch.object(cc_app_results.threading, "Thread", _FakeThread):
            app._transform_result("concise")
        # _do_transform_result(mode, current, label) — 'current' is the input.
        self.assertEqual(captured["args"][1], "ORIG")

    def test_stale_follow_up_does_not_mutate_new_popup(self):
        app, old_win, _ = self._app(primary="OLD")
        _, new_win, new_text = self._app(primary="NEW")
        app.popup = new_win

        app._apply_result_transform(
            True, "OLD ACTION", "改写", expected_win=old_win)

        self.assertEqual(new_text._content, "NEW")

    def test_code_explanation_snapshots_primary_before_append(self):
        app, win, text = self._app(primary="ORIG")

        app._append_code_explanation(True, "ORIG", "EXPLANATION", win)

        self.assertEqual(win._primary_result, "ORIG")
        self.assertIn("EXPLANATION", text._content)


class TestTranslationCacheLookup(unittest.TestCase):
    """Data layer for the instant-cache feature: an identical earlier selection
    (same text, kind and settings signature) is served from history without a
    re-translation."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8")
        self._path = self._tmp.name
        self._tmp.close()
        os.unlink(self._path)
        self._orig = tr.HISTORY_PATH
        tr.HISTORY_PATH = self._path

    def tearDown(self):
        tr.HISTORY_PATH = self._orig
        try:
            os.unlink(self._path)
        except Exception:
            pass

    def test_exact_hit_returns_output(self):
        tr.add_history("Good morning", "OUT_A", False, 100, kind="text", sig="S")
        self.assertEqual(
            tr.find_cached_translation("Good morning", "text", "S"), "OUT_A")

    def test_miss_on_different_signature(self):
        tr.add_history("Good morning", "OUT_A", False, 100, kind="text", sig="S")
        self.assertIsNone(
            tr.find_cached_translation("Good morning", "text", "OTHER"))

    def test_miss_on_different_kind(self):
        tr.add_history("Good morning", "OUT_A", False, 100, kind="text", sig="S")
        self.assertIsNone(
            tr.find_cached_translation("Good morning", "dict", "S"))

    def test_ocr_kind_never_matches(self):
        tr.add_history("scanned", "OUT_A", False, 100, kind="ocr", sig="S")
        self.assertIsNone(tr.find_cached_translation("scanned", "ocr", "S"))

    def test_hit_ignores_surrounding_whitespace(self):
        tr.add_history("hello world", "OUT_B", False, 100, kind="text", sig="S")
        self.assertEqual(
            tr.find_cached_translation("  hello world  ", "text", "S"), "OUT_B")

    def test_empty_output_entry_is_skipped(self):
        tr.add_history("blank", "", False, 100, kind="text", sig="S")
        self.assertIsNone(tr.find_cached_translation("blank", "text", "S"))

    def test_empty_query_returns_none(self):
        self.assertIsNone(tr.find_cached_translation("   ", "text", "S"))

    def test_legacy_entry_matches_empty_signature_only(self):
        tr.add_history("legacy", "OUT_C", False, 100, kind="text")  # sig -> ""
        self.assertEqual(
            tr.find_cached_translation("legacy", "text", ""), "OUT_C")
        self.assertIsNone(tr.find_cached_translation("legacy", "text", "S"))


class TestCacheSignature(unittest.TestCase):
    """The signature must change whenever a setting that changes a translation's
    output changes, so a stale result is never served under new settings."""

    def _app(self, **over):
        app = object.__new__(tr.TranslatorApp)
        cfg = {tr.CFG.DIRECTION: "auto", tr.CFG.MODEL: "haiku",
               tr.CFG.SUMMARY_ENABLED: False, tr.CFG.LANGUAGE: "zh"}
        cfg.update(over)
        app.cfg = cfg
        return app

    def test_changes_with_direction(self):
        self.assertNotEqual(
            self._app()._cache_signature(),
            self._app(**{tr.CFG.DIRECTION: "zh2en"})._cache_signature())

    def test_changes_with_model(self):
        self.assertNotEqual(
            self._app()._cache_signature(),
            self._app(**{tr.CFG.MODEL: "opus"})._cache_signature())

    def test_changes_with_provider(self):
        self.assertNotEqual(
            self._app()._cache_signature(),
            self._app(**{
                tr.CFG.MODEL_PROVIDER: "codex_cli",
                tr.CFG.CODEX_MODEL: "auto",
            })._cache_signature())

    def test_changes_with_summary(self):
        self.assertNotEqual(
            self._app()._cache_signature(),
            self._app(**{tr.CFG.SUMMARY_ENABLED: True})._cache_signature())

    def test_changes_with_language(self):
        self.assertNotEqual(
            self._app()._cache_signature(),
            self._app(**{tr.CFG.LANGUAGE: "en"})._cache_signature())

    def test_stable_for_identical_settings(self):
        self.assertEqual(
            self._app()._cache_signature(), self._app()._cache_signature())

    def test_codex_signature_includes_current_prompt_revision(self):
        signature = self._app(**{
            tr.CFG.MODEL_PROVIDER: "codex_cli",
            tr.CFG.CODEX_MODEL: "gpt-5.4-mini",
        })._cache_signature()

        self.assertTrue(signature.endswith("|codex-format-v2"))

    def test_fast_auto_profile_has_distinct_cache_signature(self):
        quality = self._app(**{
            tr.CFG.MODEL_PROVIDER: "codex_cli",
            tr.CFG.CODEX_MODEL: "auto",
        })._cache_signature()
        fast = self._app(**{
            tr.CFG.MODEL_PROVIDER: "codex_cli",
            tr.CFG.CODEX_MODEL: "auto-fast",
        })._cache_signature()

        self.assertNotEqual(quality, fast)

    def test_claude_signature_preserves_legacy_shape(self):
        signature = self._app()._cache_signature()

        self.assertEqual(signature, "claude_cli|haiku|auto|sum0|zh")

    def test_old_codex_signature_does_not_match_after_prompt_change(self):
        app = self._app(**{
            tr.CFG.MODEL_PROVIDER: "codex_cli",
            tr.CFG.CODEX_MODEL: "gpt-5.4-mini",
        })
        current = app._cache_signature()
        old = current.rsplit("|", 1)[0]

        self.assertNotEqual(current, old)


class TestCacheShortCircuit(unittest.TestCase):
    """_show_loading serves a cache hit instantly (no worker thread, no loading
    popup) and bypasses the cache for retries, force_class and screenshots."""

    def _app(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = {tr.CFG.HISTORY_ENABLED: True, tr.CFG.HISTORY_LIMIT: 100,
                   tr.CFG.DIRECTION: "auto", tr.CFG.MODEL: "haiku",
                   tr.CFG.SUMMARY_ENABLED: False, tr.CFG.LANGUAGE: "zh"}
        app._job_id = 0
        app._ss = tr.StreamSession()
        app.root = unittest.mock.Mock()
        app._destroy_popup = unittest.mock.Mock()
        app._cancel_stream_flush = unittest.mock.Mock()
        app._show_result = unittest.mock.Mock()
        return app

    def test_hit_shows_result_without_worker(self):
        app = self._app()
        with unittest.mock.patch.object(
                tr, "find_cached_translation", return_value="CACHED") as fc, \
                unittest.mock.patch.object(tr.threading, "Thread") as thread:
            app._show_loading("hello world")
        fc.assert_called_once()
        thread.assert_not_called()
        app.root.after.assert_not_called()
        app._show_result.assert_called_once()
        args, kwargs = app._show_result.call_args
        self.assertEqual(args[0], True)
        self.assertEqual(args[1], "CACHED")
        self.assertFalse(kwargs.get("record", True))

    def test_miss_starts_worker_thread(self):
        app = self._app()
        with unittest.mock.patch.object(
                tr, "find_cached_translation", return_value=None) as fc, \
                unittest.mock.patch.object(tr.threading, "Thread") as thread:
            app._show_loading("hello world")
        fc.assert_called_once()
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()
        app._show_result.assert_not_called()

    def test_retry_bypasses_cache(self):
        app = self._app()
        with unittest.mock.patch.object(
                tr, "find_cached_translation") as fc, \
                unittest.mock.patch.object(tr.threading, "Thread"):
            app._show_loading("hello world", use_cache=False)
        fc.assert_not_called()

    def test_force_class_bypasses_cache(self):
        app = self._app()
        with unittest.mock.patch.object(
                tr, "find_cached_translation") as fc, \
                unittest.mock.patch.object(tr.threading, "Thread"):
            app._show_loading("hello world", force_class="text")
        fc.assert_not_called()

    def test_ocr_bypasses_cache(self):
        app = self._app()
        with unittest.mock.patch.object(
                tr, "find_cached_translation") as fc, \
                unittest.mock.patch.object(tr.threading, "Thread"):
            app._show_loading("hello world", origin="ocr")
        fc.assert_not_called()

    def test_history_disabled_bypasses_cache(self):
        app = self._app()
        app.cfg[tr.CFG.HISTORY_ENABLED] = False
        with unittest.mock.patch.object(
                tr, "find_cached_translation") as fc, \
                unittest.mock.patch.object(tr.threading, "Thread"):
            app._show_loading("hello world")
        fc.assert_not_called()


class TestShowResultRecordFlag(unittest.TestCase):
    """A cache hit reuses the normal result popup but must not re-record history;
    a normal result still records, threading the settings signature through."""

    def _app(self):
        app = object.__new__(tr.TranslatorApp)
        app._job_id = 4
        app.popup = None
        app._last_input = "hello world"
        app._last_origin = "text"
        app._last_class = "text"
        app.cfg = {tr.CFG.HISTORY_ENABLED: True, tr.CFG.HISTORY_LIMIT: 100,
                   tr.CFG.DIRECTION: "auto", tr.CFG.MODEL: "haiku",
                   tr.CFG.SUMMARY_ENABLED: False, tr.CFG.LANGUAGE: "zh"}
        app._stop_animation = unittest.mock.Mock()
        app._destroy_popup = unittest.mock.Mock()
        app._make_popup = unittest.mock.Mock(return_value=unittest.mock.Mock())
        app._maybe_add_explain_button = unittest.mock.Mock()
        app._maybe_add_as_text_button = unittest.mock.Mock()
        app._maybe_add_result_actions_button = unittest.mock.Mock()
        return app

    def test_record_false_skips_history(self):
        app = self._app()
        with unittest.mock.patch.object(tr, "add_history") as add_history:
            app._show_result(True, "CACHED", job_id=4, record=False)
        add_history.assert_not_called()

    def test_record_true_writes_history_with_signature(self):
        app = self._app()
        with unittest.mock.patch.object(tr, "add_history") as add_history:
            app._show_result(True, "FRESH", job_id=4)
        add_history.assert_called_once()
        self.assertEqual(
            add_history.call_args.kwargs.get("sig"), app._cache_signature())


class TestReshowLastResult(unittest.TestCase):
    """Feature B — recall last result. Re-displaying the stored result must reuse
    the normal popup path but never re-translate or re-record history, and must
    be a safe no-op before anything has been translated."""

    def _app(self, ok=True, title="Result", text="hello"):
        app = object.__new__(tr.TranslatorApp)
        app.popup = None
        app._last_result_ok = ok
        app._last_result_title = title
        app._last_result_text = text
        app._destroy_popup = unittest.mock.Mock()
        app._window_xy = unittest.mock.Mock(return_value=(10, 20))
        app._make_popup = unittest.mock.Mock(return_value=unittest.mock.Mock())
        app._maybe_add_explain_button = unittest.mock.Mock()
        app._maybe_add_as_text_button = unittest.mock.Mock()
        app._maybe_add_result_actions_button = unittest.mock.Mock()
        return app

    def test_has_recallable_result_reflects_state(self):
        app = self._app()
        app._last_result_ok = None
        self.assertFalse(app.has_recallable_result())
        app._last_result_ok = True
        self.assertTrue(app.has_recallable_result())
        app._last_result_ok = False   # a stored error is still recallable
        self.assertTrue(app.has_recallable_result())

    def test_reshow_noop_when_nothing_translated(self):
        app = self._app()
        app._last_result_ok = None
        self.assertFalse(app._reshow_last_result())
        app._make_popup.assert_not_called()
        app._destroy_popup.assert_not_called()

    def test_reshow_rebuilds_popup_without_translating_or_recording(self):
        app = self._app(ok=True, title="Result", text="stored body")
        with unittest.mock.patch.object(tr, "add_history") as add_history:
            self.assertTrue(app._reshow_last_result())
        add_history.assert_not_called()
        app._make_popup.assert_called_once()
        args, kwargs = app._make_popup.call_args
        self.assertEqual(args[0], "stored body")
        self.assertFalse(kwargs["is_error"])
        self.assertTrue(kwargs["highlight"])
        self.assertEqual(kwargs["title"], "Result")

    def test_reshow_success_readds_action_buttons(self):
        app = self._app(ok=True)
        app._reshow_last_result()
        app._maybe_add_explain_button.assert_called_once()
        app._maybe_add_as_text_button.assert_called_once()
        app._maybe_add_result_actions_button.assert_called_once()

    def test_reshow_error_omits_success_only_buttons(self):
        app = self._app(ok=False, title="Error", text="it failed")
        app._reshow_last_result()
        args, kwargs = app._make_popup.call_args
        self.assertTrue(kwargs["is_error"])
        self.assertFalse(kwargs["highlight"])
        app._maybe_add_explain_button.assert_called_once()
        app._maybe_add_as_text_button.assert_not_called()
        app._maybe_add_result_actions_button.assert_not_called()

    def test_reshow_reuses_existing_popup_anchor(self):
        app = self._app()
        existing = unittest.mock.Mock()   # an existing window on screen
        app.popup = existing
        app._reshow_last_result()
        app._window_xy.assert_called_once_with(existing)
        self.assertEqual(app._make_popup.call_args.kwargs["anchor"], (10, 20))


class TestTrayMenuRefresh(unittest.TestCase):
    """The tray's "recall last result" item is grayed via an enabled callable
    that pystray only re-evaluates on update_menu(). _remember_result must poke
    the tray so the item un-grays immediately instead of lagging."""

    def _app(self):
        app = object.__new__(tr.TranslatorApp)
        app._last_result_ok = None
        app._last_result_title = ""
        app._last_result_text = ""
        return app

    def test_remember_result_refreshes_tray(self):
        app = self._app()
        app.tray = unittest.mock.Mock()
        app._remember_result(True, "Title", "  body  ")
        self.assertTrue(app._last_result_ok)
        self.assertEqual(app._last_result_title, "Title")
        self.assertEqual(app._last_result_text, "body")   # stripped
        app.tray.update_menu.assert_called_once()

    def test_refresh_tray_menu_noop_without_tray(self):
        app = self._app()
        app.tray = None
        app._refresh_tray_menu()   # must not raise

    def test_refresh_tray_menu_swallows_errors(self):
        app = self._app()
        app.tray = unittest.mock.Mock()
        app.tray.update_menu.side_effect = RuntimeError("boom")
        app._refresh_tray_menu()   # must not raise


class TestAboutCheckUpdate(unittest.TestCase):
    """"Check for updates" moved from the tray to the About window; it must
    close About and route through the shared in-Settings check."""

    def test_about_check_update_closes_and_delegates(self):
        app = object.__new__(tr.TranslatorApp)
        app.about_win = None
        app.check_update_via_settings = unittest.mock.Mock()
        app._about_check_update()
        app.check_update_via_settings.assert_called_once()


def tearDownModule():
    """Tear the shared Tk root down deterministically on the main thread and
    force GC passes so no tkinter object is finalized during interpreter
    shutdown. That shutdown-time finalization on a non-main thread is what
    produced the ``Tcl_AsyncDelete: async handler deleted by the wrong thread``
    abort (and nonzero exit code) even though every test passed."""
    global _SHARED_ROOT
    gc.collect()
    root = _SHARED_ROOT
    _SHARED_ROOT = None
    if root is not None:
        try:
            root.update_idletasks()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
    gc.collect()


if __name__ == "__main__":
    unittest.main(verbosity=2)
