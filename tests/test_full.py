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
import types
import unittest
import unittest.mock

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
        "MODEL", "DOUBLE_PRESS_WINDOW", "FONT_SIZE", "DIRECTION",
        "MAX_CHARS", "THEME", "POPUP_LAYOUT",
        "HISTORY_ENABLED", "HISTORY_LIMIT",
        "AUTO_UPDATE_ENABLED", "AUTO_UPDATE_HOUR",
        "OCR_ENGINE", "OCR_HOTKEY_ENABLED",
        "CLIPBOARD_PROTECTION_ENABLED",
        "AUTOSTART_INITIALIZED",
        "SUMMARY_ENABLED",
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
        self.assertEqual(tr.summary_headings("en_US"), ("Summary", "Translation"))
        self.assertEqual(tr.summary_headings("zh_CN"), ("摘要", "译文"))

    def test_summary_instruction_contains_headings(self):
        instr = tr.summary_instruction("zh_CN")
        self.assertIn("## 摘要", instr)
        self.assertIn("## 译文", instr)
        instr_en = tr.summary_instruction("en_US")
        self.assertIn("## Summary", instr_en)
        self.assertIn("## Translation", instr_en)

    def test_summary_default_off(self):
        self.assertFalse(tr.DEFAULT_CONFIG[tr.CFG.SUMMARY_ENABLED])


class TestShouldSummarize(unittest.TestCase):
    """Exercise TranslatorApp._should_summarize without constructing the full
    app: call the unbound method against a lightweight stub self."""

    def _stub(self, *, enabled=True, last_class="text"):
        ns = types.SimpleNamespace()
        ns.cfg = {tr.CFG.SUMMARY_ENABLED: enabled}
        ns._last_class = last_class
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
        orig_data_dir = tr.DATA_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            tr.DATA_DIR = tmpdir
            tr.log_error("write_test", Exception("sentinel_error_xyz"))
            log_path = os.path.join(tmpdir, "error.log")
            self.assertTrue(os.path.exists(log_path),
                            "log_error should create error.log")
            with open(log_path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("write_test", content)
            self.assertIn("sentinel_error_xyz", content)
        tr.DATA_DIR = orig_data_dir


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
        orig = tr.ROUND_KEY_COLOR
        try:
            tr.ROUND_KEY_COLOR = "#ff00ff"
            img = Image.new("RGB", (2, 1), (0, 0, 0))
            out = tr.TranslatorApp._despeckle_key_color(img)
            self.assertIs(out, img)
        finally:
            tr.ROUND_KEY_COLOR = orig


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
    app.cfg = tr.load_config()
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
    def _run_check_only_update(self, state):
        app = _make_headless_app()
        self.addCleanup(lambda: TestUiSmoke._safe_destroy(app))
        seen = []

        def on_status(msg, kind):
            seen.append((msg, kind))

        with unittest.mock.patch.object(
                app.root, "after", side_effect=lambda _ms, fn: fn()), \
                unittest.mock.patch.object(tr, "is_git_deploy", return_value=True), \
                unittest.mock.patch.object(
                    tr._cc_update, "fetch_remote_branch", return_value=(True, "")), \
                unittest.mock.patch.object(
                    tr._cc_update, "classify_update_state",
                    return_value=(state, "localsha", "remotesha")):
            app._update_worker(silent=False, on_status=on_status, check_only=True)
        return seen

    def test_ahead_state_reports_known_latest(self):
        seen = self._run_check_only_update("ahead")
        self.assertEqual(seen, [(tr.i18n.get("update.no_update"), "ok")])

    def test_diverged_state_reports_known_latest(self):
        seen = self._run_check_only_update("diverged")
        self.assertEqual(seen, [(tr.i18n.get("update.no_update"), "ok")])


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
        add_history.assert_called_once()
        # First positional arg is the original input text.
        self.assertEqual(add_history.call_args.args[0], app._last_input)
        # Pipes are cleaned up in the finally block.
        self.assertTrue(proc.stdout.closed)
        self.assertTrue(proc.stdin.closed)

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
        # Payload is passed via stdin, not as an argv element.
        self.assertEqual(run.call_args.kwargs["input"], "<text>\nhello world\n</text>")

    def test_plain_text_fallback_when_not_json(self):
        ok, result, run = self._run(_FakeCompleted(stdout="just plain text"))
        self.assertTrue(ok)
        self.assertEqual(result, "just plain text")

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
