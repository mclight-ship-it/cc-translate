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
import json
import os
import sys
import tempfile
import unittest

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
            "ICON_PATH", "MIN_POPUP_HEIGHT", "MIN_STREAM_VISIBLE_HEIGHT",
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
