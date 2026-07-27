"""
CC Translate — double-Ctrl+C translation via Claude Code CLI.
Local only. Reuses your Claude Code subscription (no separate API key).

Trigger: press Ctrl+C twice quickly to translate the current selection.
Rendering: floating popup near the cursor. Selectable text, copy button,
draggable by the top bar, closes on Esc or the ✕ button.
System tray icon: left-click runs a configurable action (default Settings;
also History / Screenshot / Quick translation, chosen in Settings); right-click
menu offers pause/resume translation and quit.
"""

import os
import sys
import re
import json
import time
import dataclasses
import queue
import threading
import subprocess
import shutil
import tempfile
import uuid
import ctypes
from ctypes import wintypes
from typing import Any, Dict, List, Optional, Tuple
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

import pyperclip
from pynput import keyboard

import i18n
import win32util
from win32util import get_monitor_rect
from cc_rich import (iter_rich_segments, highlight_code, _PYGMENTS_OK,
                     _iter_inline_segments, _flush_highlighted_fence,
                     _pyg_token_tag, _PygToken)
from cc_warm import (WarmClaude, CLAUDE_CMD, WARM_POOL_ENABLED, WARM_POOL_DEPTH,
                     WARM_UP_MS, WARM_MAX_AGE_S, WARM_SEND_TIMEOUT_S)
import cc_warm as _cc_warm
from cc_update import (
    is_git_deploy, local_head, remote_head, update_available, version_string,
    _format_version,
    is_autostart_enabled, set_autostart, ensure_startmenu_shortcut,
    remove_shortcuts, spawn_uninstaller,
    _spawn_relauncher, _git, GIT_REMOTE, GIT_BRANCH, UPDATE_NET_TIMEOUT,
    LEGACY_STARTUP_VBS, SCRIPT_PATH, PYTHONW, STARTUP_LNK, STARTMENU_LNK,
)
import cc_update as _cc_update
import cc_ocr

# Shared foundation layer (paths, logging, config keys, direction prompts,
# model prompts). Re-exported here so existing tr.<name> references resolve.
from cc_core import (
    APP_NAME, APP_DIR, DATA_DIR, _resolve_data_dir, _user_data_path,
    UPDATE_NOTICE_PATH,
    ICON_PATH, ICON_PATH_DARK, ICON_PATH_LIGHT,
    detect_system_theme, detect_taskbar_theme, tray_icon_path,
    POPUP_CORNER_RADIUS,
    QUICK_INPUT_WINDOW_W, QUICK_INPUT_WINDOW_H,
    log_perf, log_error,
    CFG, DEFAULT_CONFIG,
    LANGUAGES, DIRECTION_MODES, DIRECTION_LABELS_ZH, DIRECTION_LABELS_EN,
    DIRECTION_LABELS, _labels_by_language, get_direction_labels,
    auto_direction_prompt, direction_prompt,
    SYSTEM_SUFFIX, SUMMARY_SUFFIX, DICTIONARY_PROMPT, CODE_EXPLAIN_PROMPT,
    CODE_EXPLAIN_APPEND_PROMPT, RESULT_CONCISE_PROMPT, RESULT_FORMAL_PROMPT,
    RESULT_SUMMARY_PROMPT, RESULT_ACTION_PROMPTS,
    OCR_STRUCTURE_HINT, OCR_VISION_PROMPT, vision_image_mention,
    ROUND_KEY_COLOR, SETTINGS_MIN_W, SETTINGS_COL_MIN_W, SUPPORT_IMAGE_PATH,
    THEMES, resolve_theme_name, resolve_theme,
    THEME_LABELS_ZH, THEME_LABELS_EN,
    POPUP_LAYOUT_LABELS_ZH, POPUP_LAYOUT_LABELS_EN,
    OCR_ENGINE_LABELS_ZH, OCR_ENGINE_LABELS_EN,
    TRAY_CLICK_ACTION_LABELS_ZH, TRAY_CLICK_ACTION_LABELS_EN,
    LANGUAGE_LABELS,
    get_theme_labels, get_popup_layout_labels, get_ocr_engine_labels,
    get_tray_click_action_labels,
    THEME_LABELS, POPUP_LAYOUT_LABELS, OCR_ENGINE_LABELS, TRAY_CLICK_ACTION_LABELS,
    fit_box_size,
)
from cc_app_warm import WarmMixin
from cc_app_update import UpdateMixin
from cc_app_tray import TrayMixin
from cc_app_about import AboutMixin
from cc_app_quickinput import QuickInputMixin
from cc_app_diagnostics import DiagnosticsMixin
from cc_app_history import HistoryMixin
from cc_app_settings import SettingsMixin


def _enable_dpi_awareness():
    """Backwards-compatible shim → win32util.enable_dpi_awareness()."""
    win32util.enable_dpi_awareness()


_enable_dpi_awareness()


CONFIG_PATH = _user_data_path("config.json")
MIN_POPUP_HEIGHT = 150
MIN_STREAM_VISIBLE_HEIGHT = 220
MIN_RESIZE_WIDTH = 280
MIN_RESIZE_HEIGHT = 150
RESIZE_HIT = 18
POPUP_SHELL_PAD = 1  # legacy 1px border inset; popups now use the rounded colour-key card
POPUP_BAR_PAD_X = 12
POPUP_BAR_PAD_TOP = 9
POPUP_BAR_PAD_BOTTOM = 7
POPUP_BODY_PAD_X = 8
POPUP_BODY_PAD_BOTTOM = 10
POPUP_TEXT_PAD_X = 16
POPUP_TEXT_PAD_Y = 12
LOADING_CORNER_RADIUS = 11

# Popup display layouts:
#   "dynamic" — the classic behaviour: the popup appears next to the mouse and
#               is auto-sized to its content (and grows while streaming).
#   "centered" — a fixed-size card centred on the active monitor. Its size does
#               NOT change with content; long results scroll instead. Width is
#               roughly 2x the dynamic popup's max width, at a ~4:3 ratio.
# Sizes are LOGICAL pixels (DPI-scaled at runtime) so the card looks the same
# physical size on any display.
CENTERED_POPUP_W = 552
CENTERED_POPUP_H = 389
HISTORY_WINDOW_W = 720
HISTORY_WINDOW_H = 520

# Hotkey handoff: the global keyboard listener runs on its own thread and must
# never touch Tcl/Tk directly. It drops trigger requests into a queue that the
# main thread drains on a timer, which fixes the "no response then a burst of
# translations" races seen right after startup.
TRIGGER_POLL_MS = 40
TRIGGER_SETTLE_MS = 120
# After a translate trigger, restore the clipboard the user had *before* their
# Ctrl+C, so triggering a translation doesn't clobber their copy/paste workflow.
CLIP_RESTORE_MS = 250

# Loading spinner frames (rotating half-circle). Segoe UI Symbol renders these
# on Windows; the animation cycles through them for a modern indeterminate look.
LOADING_SPINNER = "◐◓◑◒"


HISTORY_FILTER_LABELS_ZH = {
    "all": "全部",
    "text": "译文",
    "dict": "词典",
    "code": "代码",
    "ocr": "截图",
}
HISTORY_FILTER_LABELS_EN = {
    "all": "All",
    "text": "Text",
    "dict": "Dict",
    "code": "Code",
    "ocr": "Screenshot",
}


def get_history_filter_labels():
    return _labels_by_language(HISTORY_FILTER_LABELS_ZH, HISTORY_FILTER_LABELS_EN)


# Backward-compatible static labels used by existing tests and legacy callers.
HISTORY_FILTER_LABELS = HISTORY_FILTER_LABELS_ZH.copy()


# (rich-text rendering: iter_rich_segments, highlight_code etc. live in cc_rich.py)

def is_single_word(text):
    """True if the selection is a word or short term worth a dictionary entry
    rather than a sentence translation. Allows short multi-word terms (e.g.
    "machine learning", "New York") but rejects anything that looks like a
    sentence (line breaks, trailing sentence punctuation, or too long/too many
    tokens)."""
    if not text:
        return False
    t = text.strip()
    if not t or "\n" in t:
        return False
    # A trailing sentence terminator means it's a sentence, not a lookup term.
    if t[-1] in ".!?…。！？，,;；:：":
        return False
    has_cjk = any(ord(c) > 0x2E7F for c in t)
    if has_cjk:
        # A short CJK term with no spaces (words/idioms up to 4 chars, e.g. 青提,
        # 一丝不苟). Longer or spaced runs are treated as sentences.
        return " " not in t and len(t) <= 4
    # Latin: 1–2 alphabetic tokens forming a term (hyphen/apostrophe allowed
    # inside a token), of reasonable length. Digits or a 3rd token → sentence.
    parts = t.split()
    if not (1 <= len(parts) <= 2) or len(t) > 30:
        return False
    return all(p and all(c.isalpha() or c in "-'" for c in p) for p in parts)


# ---- Code detection (local, instant — never calls the model) ---------------
# Regexes that signal a line is program source rather than prose.
_CODE_KEYWORD_RE = re.compile(
    r"\b(?:def|class|function|const|let|var|import|from|export|return|"
    r"public|private|protected|static|void|int|float|double|bool|boolean|"
    r"string|struct|enum|interface|namespace|package|func|fn|impl|trait|"
    r"async|await|yield|lambda|require|include|typedef|template|typename|"
    r"if|elif|else|for|while|switch|case|foreach|try|catch|except|finally|"
    r"throw|throws|new|delete|null|nil|None|True|False|true|false|"
    r"println|printf|console\.log|System\.out)\b")
_CODE_CALL_RE = re.compile(r"[A-Za-z_]\w*\s*\(")           # foo(  bar (
_CODE_OPERATOR_RE = re.compile(r"(?:=>|->|::|\+\+|--|==|!=|<=|>=|&&|\|\||"
                               r"\+=|-=|\*=|/=|:=)")
_CODE_CAMEL_RE = re.compile(r"\b[a-z]+[A-Z]\w*\b")          # getUserById
_CODE_SNAKE_RE = re.compile(r"\b[a-z]+_[a-z]\w*\b")         # user_name
_CODE_SYMBOLS = set("{}[]();<>=+-*/%&|^~")


def _looks_like_code_line(line):
    """Heuristic: does a single line look like source code (vs natural prose)?
    A line rich in CJK is treated as prose regardless of stray symbols."""
    s = line.strip()
    if not s:
        return None   # blank line: neutral, excluded from the ratio
    cjk = sum(1 for c in s if ord(c) > 0x2E7F)
    letters = sum(1 for c in s if c.isalpha())
    # Lines that are mostly Chinese/Japanese are prose, not code.
    if cjk and cjk >= max(2, letters * 0.5):
        return False

    score = 0
    if _CODE_KEYWORD_RE.search(s):
        score += 1
    if _CODE_CALL_RE.search(s):
        score += 1
    if _CODE_OPERATOR_RE.search(s):
        score += 1
    if _CODE_CAMEL_RE.search(s) or _CODE_SNAKE_RE.search(s):
        score += 1
    # Structural cues: ends with an opener/terminator, or is heavily indented.
    if s[-1] in "{};:," or s.endswith("=>"):
        score += 1
    if line[:1] in (" ", "\t") and (len(line) - len(line.lstrip())) >= 2:
        score += 1
    # Symbol density: lots of punctuation is a strong code signal.
    sym = sum(1 for c in s if c in _CODE_SYMBOLS)
    if len(s) and sym / len(s) >= 0.12:
        score += 1

    return score >= 2


def code_ratio(text):
    """Fraction (0.0–1.0) of non-blank lines that look like source code."""
    verdicts = [_looks_like_code_line(ln) for ln in text.split("\n")]
    considered = [v for v in verdicts if v is not None]
    if not considered:
        return 0.0
    return sum(1 for v in considered if v) / len(considered)


# Classification thresholds (see design): mostly-code vs mixed vs prose.
CODE_RATIO_PURE = 0.85     # ≥ this → treat the whole selection as code
CODE_RATIO_MIXED = 0.15    # ≥ this (and < PURE) → prose+code mixed


def classify_selection(text):
    """Return 'code', 'mixed', or 'text' from a fast local heuristic. Never
    calls the model, so it adds no latency to the translation path."""
    t = (text or "").strip()
    if not t:
        return "text"
    r = code_ratio(t)
    if r >= CODE_RATIO_PURE:
        return "code"
    if r >= CODE_RATIO_MIXED:
        return "mixed"
    return "text"


# Text at/above this length streams (progressive render) rather than one-shot,
# and is also the minimum length for the long-text summary feature. Unified so
# "long enough to stream" and "long enough to summarize" mean the same thing.
STREAM_MIN_CHARS = 400
SUMMARY_MIN_CHARS = STREAM_MIN_CHARS

# Hard cap on a single cold streaming round-trip. Mirrors cc_warm's
# WARM_SEND_TIMEOUT_S so a hung Claude CLI can't wedge the translation thread
# (or leak a child process) forever.
STREAM_SEND_TIMEOUT_S = 90

_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_CONFIG_KV_LINE_RE = re.compile(
    r"^\s*(?:-\s*)?[a-z0-9_.-]{2,40}\s*:\s*(?:\S.*)?$")
_CONFIG_ASSIGN_LINE_RE = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_.-]{1,40}\s*=\s*\S+")


def is_summarizable_prose(text):
    """True if `text` is long-form natural-language prose worth summarizing.

    Excludes content where a leading summary adds little value: bullet/numbered
    lists, config/data blobs (JSON/XML/YAML-like), and URL/path dumps. Assumes
    the caller has already confirmed the text is long enough and is neither a
    single-word lookup nor source code."""
    t = (text or "").strip()
    if not t:
        return False
    lines = [ln for ln in t.split("\n") if ln.strip()]
    if not lines:
        return False

    # URL / path dump: most whitespace-separated tokens are links or paths.
    tokens = t.split()
    if tokens:
        linkish = sum(
            1 for w in tokens
            if w.startswith(("http://", "https://", "www."))
            or ("/" in w and len(w) > 8) or ("\\" in w and len(w) > 8))
        if linkish / len(tokens) >= 0.5:
            return False

    # Mostly a list: a leading summary would just restate the list.
    if len(lines) >= 3:
        bullets = sum(1 for ln in lines if _LIST_MARKER_RE.match(ln))
        if bullets / len(lines) >= 0.8:
            return False

    # YAML / INI / env-style key-value blocks are data/config, not prose.
    if len(lines) >= 4:
        kvish = sum(
            1 for ln in lines
            if _CONFIG_KV_LINE_RE.match(ln) or _CONFIG_ASSIGN_LINE_RE.match(ln))
        if kvish / len(lines) >= 0.5:
            return False

    # Config / data blob: high density of structural punctuation that prose
    # (which leans on letters, spaces, commas and periods) never reaches.
    struct = sum(1 for c in t if c in '{}[]":;=<>|')
    if struct / len(t) >= 0.08:
        return False

    # Require some sentence structure so short label-like blobs don't qualify:
    # a sentence terminator anywhere, or at least two prose lines/paragraphs.
    has_terminator = any(c in t for c in ".!?。！？…")
    if not has_terminator and len(lines) < 2:
        return False
    return True


def summary_headings(app_language):
    """(summary_heading, translation_heading) text for the summary sections,
    localized to the app UI language."""
    if app_language == "en_US":
        return ("Summary", "Translation")
    return ("摘要", "译文")


def summary_instruction(app_language):
    """Instruction appended to the translate prompt when the long-text summary
    feature is active. Asks the model to emit a short summary first, then the
    full translation, using two Markdown headings the renderer already styles.

    Strongly emphasizes that BOTH sections must be written in the target
    language (the language being translated INTO), since otherwise smaller
    models tend to write the summary in the source language."""
    sm, tr = summary_headings(app_language)
    return (
        " IMPORTANT OUTPUT FORMAT: because the text is long, structure your "
        "ENTIRE response as exactly two Markdown sections. FIRST, a line with "
        f"the heading `## {sm}` followed by a brief summary of 3-5 short lines "
        f"capturing the key points. THEN, a line with the heading `## {tr}` "
        "followed by the full translation. Use level-2 `##` headings with "
        "exactly those two heading texts. CRITICAL: write BOTH the summary and "
        "the translation in the TARGET language (the language you are "
        "translating INTO), never in the source language.")



class Config(dict):
    """Typed, self-validating view over the user config.

    Subclasses ``dict`` so every existing access pattern keeps working
    unchanged — ``cfg[key]``, ``cfg.get(key)``, ``cfg[key] = v`` and
    ``json.dump(cfg, ...)`` all behave exactly as before. On top of that it:

      * merges ``DEFAULT_CONFIG`` so every known key is always present, and
      * coerces each known key to the type of its default (a config file that
        somehow holds a wrong-typed value can't crash the UI downstream), and
      * exposes typed read-only properties for the hot keys so new code can
        say ``cfg.model`` instead of ``cfg.get(CFG.MODEL, ...)`` with a
        literal fallback repeated at every call site.

    Unknown keys are preserved untouched for forward-compatibility."""

    def __init__(self, data=None):
        super().__init__(DEFAULT_CONFIG)
        if data:
            self.update(data)
        self._coerce()

    def _coerce(self):
        """Force every known key to the type of its default; on mismatch that
        can't be coerced, fall back to the default rather than keep a value
        that would break a downstream widget."""
        for key, default in DEFAULT_CONFIG.items():
            if key not in self:
                self[key] = default
                continue
            value = self[key]
            try:
                if isinstance(default, bool):
                    # bool is a subclass of int, so test it before int.
                    if isinstance(value, bool):
                        continue
                    if isinstance(value, (int, float)):
                        self[key] = bool(value)
                    elif isinstance(value, str):
                        self[key] = value.strip().lower() in ("1", "true", "yes", "on")
                    else:
                        self[key] = default
                elif isinstance(default, int):
                    self[key] = int(value)
                elif isinstance(default, float):
                    self[key] = float(value)
                elif isinstance(default, str):
                    self[key] = value if isinstance(value, str) else str(value)
            except (TypeError, ValueError):
                self[key] = default

    # ---- Typed accessors (optional convenience; the dict API still works) ----
    @property
    def model(self):
        return self.get(CFG.MODEL, DEFAULT_CONFIG[CFG.MODEL])

    @property
    def direction(self):
        return self.get(CFG.DIRECTION, DEFAULT_CONFIG[CFG.DIRECTION])

    @property
    def theme(self):
        return self.get(CFG.THEME, DEFAULT_CONFIG[CFG.THEME])

    @property
    def font_size(self):
        return self.get(CFG.FONT_SIZE, DEFAULT_CONFIG[CFG.FONT_SIZE])

    @property
    def max_chars(self):
        return self.get(CFG.MAX_CHARS, DEFAULT_CONFIG[CFG.MAX_CHARS])

    @property
    def double_press_window(self):
        return self.get(CFG.DOUBLE_PRESS_WINDOW,
                        DEFAULT_CONFIG[CFG.DOUBLE_PRESS_WINDOW])

    @property
    def popup_layout(self):
        return self.get(CFG.POPUP_LAYOUT, DEFAULT_CONFIG[CFG.POPUP_LAYOUT])

    @property
    def language(self):
        return self.get(CFG.LANGUAGE)

    @property
    def history_enabled(self):
        return self.get(CFG.HISTORY_ENABLED, DEFAULT_CONFIG[CFG.HISTORY_ENABLED])

    @property
    def history_limit(self):
        return self.get(CFG.HISTORY_LIMIT, DEFAULT_CONFIG[CFG.HISTORY_LIMIT])

    @property
    def ocr_engine(self):
        return self.get(CFG.OCR_ENGINE, DEFAULT_CONFIG[CFG.OCR_ENGINE])

    @property
    def summary_enabled(self):
        return self.get(CFG.SUMMARY_ENABLED, DEFAULT_CONFIG[CFG.SUMMARY_ENABLED])


def load_config() -> "Config":
    cfg = Config()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = Config(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        log_error("load_config", e)
    return cfg


def _atomic_write_json(path: str, data: Any) -> None:
    """Write JSON to ``path`` atomically.

    Dumps to a uniquely-named temp file in the same directory, flushes+fsyncs it,
    then ``os.replace()``s it over the target. Because the swap is atomic, a
    crash or hard ``os._exit`` mid-write can never leave a truncated/corrupt
    file — readers always see either the old complete file or the new one."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise


def save_config(cfg: Dict[str, Any]) -> None:
    try:
        _atomic_write_json(CONFIG_PATH, cfg)
    except Exception as e:
        log_error("save_config", e)


HISTORY_PATH = _user_data_path("history.json")

# Serialises the read-modify-write in add_history so concurrent translation
# workers (each may append a result) can't interleave and lose entries.
_HISTORY_LOCK = threading.Lock()


def load_history() -> List[Dict[str, Any]]:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        log_error("load_history", e)
        return []


def add_history(input_text: str, output_text: str, is_dict: bool, limit: int,
                is_code: bool = False, kind: Optional[str] = None) -> None:
    if kind not in ("text", "dict", "code", "ocr"):
        if is_code:
            kind = "code"
        elif is_dict:
            kind = "dict"
        else:
            kind = "text"
    with _HISTORY_LOCK:
        entries = load_history()
        entries.insert(0, {
            "ts": time.strftime("%Y-%m-%d %H:%M"),
            "input": input_text or "",
            "output": output_text or "",
            "is_dict": bool(is_dict),
            "is_code": bool(is_code),
            "kind": kind,
        })
        del entries[max(1, int(limit)):]
        try:
            _atomic_write_json(HISTORY_PATH, entries)
        except Exception as e:
            log_error("add_history", e)


def clear_history() -> None:
    try:
        if os.path.exists(HISTORY_PATH):
            os.remove(HISTORY_PATH)
    except Exception as e:
        # One-shot user action ("clear history"): if it fails the user gets no
        # visible feedback, so leave a trace instead of swallowing silently.
        log_error("clear_history", e)


def history_entry_kind(entry):
    kind = (entry or {}).get("kind")
    if kind in ("text", "dict", "code", "ocr"):
        return kind
    if (entry or {}).get("is_code"):
        return "code"
    if (entry or {}).get("is_dict"):
        return "dict"
    return "text"


def history_entry_tag(entry):
    return {
        "text": i18n.get("history.tag.text"),
        "dict": i18n.get("history.tag.dict"),
        "code": i18n.get("history.tag.code"),
        "ocr": i18n.get("history.tag.ocr"),
    }.get(history_entry_kind(entry), i18n.get("history.tag.text"))


def history_entry_preview(entry, limit=24):
    text = (entry.get("input") or "").strip()
    if not text:
        text = (entry.get("output") or "").strip()
    text = " ".join(text.split())
    return (text[:limit] if text else i18n.get("history.preview_empty"))


def filter_history_entries(entries, query="", kind="all"):
    if kind not in ("all", "text", "dict", "code", "ocr"):
        kind = "all"
    query = " ".join((query or "").split()).casefold()
    out = []
    for entry in entries or []:
        if kind != "all" and history_entry_kind(entry) != kind:
            continue
        if query:
            hay = "\n".join([
                entry.get("input", "") or "",
                entry.get("output", "") or "",
                entry.get("ts", "") or "",
            ]).casefold()
            if query not in hay:
                continue
        out.append(entry)
    return out


# Diagnostics helpers live in diagnostics.py (pure, GUI-free, unit-tested).
# Re-imported here so existing call sites (tr.infer_claude_backend, the private
# _load_json_object/_redact_diag_value helpers, and the diagnostics window) keep
# working unchanged.
from diagnostics import (
    load_json_object as _load_json_object,
    redact_diag_value as _redact_diag_value,
    infer_claude_backend,
    describe_model_routing,
    build_diagnostics_actions,
    probe_base_url,
    tail_text_file,
)




# (autostart / shortcut / git-update helpers live in cc_update.py)
# (log_perf / log_error live in cc_core.py)


# Wire log_error into the sub-modules that need it (cc_warm, cc_update).
# Done here — after DATA_DIR is known — rather than at import time in those
# modules, so the log file always resolves to the right user data directory.
_cc_warm.set_log_error(log_error)
_cc_update._log_error = log_error
cc_ocr.set_log_error(log_error)


# ---------------------------------------------------------------------------
# Reliable rounded corners for borderless (overrideredirect) windows.
#
# Previous approach applied a rounded region from Tk's <Configure>/<Map>
# handlers using winfo_width()/height(). During a live drag-resize those
# values lag the requested geometry, so a stale (too-large) region could get
# cached — its rounded corners then fall *outside* the shrunk window and it
# renders square. The fix: subclass the window procedure and re-apply the
# region on WM_WINDOWPOSCHANGED / WM_SIZE, which Windows sends *after* the
# window has actually been resized. GetWindowRect then reports the true final
# size every time, regardless of who triggered the resize (Tk geometry, a
# drag, or a DPI change). This removes all Tk timing/caching races.
# ---------------------------------------------------------------------------
_ROUND_GWLP_WNDPROC = -4
_ROUND_WM_SIZE = 0x0005
_ROUND_WM_WINDOWPOSCHANGED = 0x0047
_ROUND_WM_DPICHANGED = 0x02E0
_ROUND_LRESULT = ctypes.c_ssize_t
_ROUND_WNDPROC = ctypes.WINFUNCTYPE(
    _ROUND_LRESULT, wintypes.HWND, ctypes.c_uint,
    ctypes.c_size_t, ctypes.c_ssize_t)

# hwnd -> {"cb": <WNDPROC>, "old": <old proc ptr>, "radius": int}
# Keeps the ctypes callback alive for the window's whole lifetime (GC of the
# callback while Windows still holds the pointer would crash).
_ROUND_REGISTRY = {}


def _round_apply_region(hwnd, radius):
    """Backwards-compatible shim → win32util.round_apply_region()."""
    win32util.round_apply_region(hwnd, radius)


def _round_prefer_dwm(hwnd):
    """Backwards-compatible shim → win32util.prefer_dwm_rounded()."""
    win32util.prefer_dwm_rounded(hwnd)


def attach_rounded_corners(win, radius):
    """Subclass a Tk Toplevel's window proc so its rounded region is refreshed
    on every real resize. Returns nothing; safe to call once per window."""
    try:
        hwnd = int(win.winfo_id())
    except Exception:
        return
    if hwnd in _ROUND_REGISTRY:
        _ROUND_REGISTRY[hwnd]["radius"] = int(radius)
        _round_apply_region(hwnd, radius)
        return

    user32 = ctypes.windll.user32
    set_ptr = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    call_proc = user32.CallWindowProcW
    set_ptr.restype = ctypes.c_void_p
    set_ptr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    call_proc.restype = _ROUND_LRESULT
    call_proc.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                          ctypes.c_size_t, ctypes.c_ssize_t]

    entry = {"cb": None, "old": None, "radius": int(radius)}

    def _wndproc(h, msg, wparam, lparam):
        old = entry["old"]
        res = call_proc(old, h, msg, wparam, lparam) if old else 0
        if msg in (_ROUND_WM_WINDOWPOSCHANGED, _ROUND_WM_SIZE,
                   _ROUND_WM_DPICHANGED):
            _round_apply_region(h, entry["radius"])
        return res

    cb = _ROUND_WNDPROC(_wndproc)
    entry["cb"] = cb
    old_proc = set_ptr(hwnd, _ROUND_GWLP_WNDPROC,
                       ctypes.cast(cb, ctypes.c_void_p))
    entry["old"] = old_proc
    _ROUND_REGISTRY[hwnd] = entry

    _round_prefer_dwm(hwnd)
    _round_apply_region(hwnd, radius)

    def _cleanup(event=None):
        # Only react to the Toplevel's own destruction, not child widgets.
        if event is not None and event.widget is not win:
            return
        # The HWND is gone after destroy; just drop our references so the
        # ctypes callback can be collected. Defer so any in-flight messages
        # during teardown still have a live callback.
        def _drop():
            _ROUND_REGISTRY.pop(hwnd, None)
        try:
            win.after(0, _drop)
        except Exception:
            _ROUND_REGISTRY.pop(hwnd, None)

    win.bind("<Destroy>", _cleanup, add="+")


# ---------------------------------------------------------------------------
# Rounded borderless windows via a transparent colour key.
#
# SetWindowRgn (above) clips the window to a rounded shape, but on some
# compositors (notably remote-desktop / VM sessions) the clipped-out corners
# render as opaque black instead of compositing through to the desktop. For
# the larger chrome windows (settings, history) we instead paint a rounded
# card on a Canvas and set a transparent colour key, so the corner pixels are
# genuinely see-through. The key is a near-black sentinel so that even if a
# session somehow ignored colour-key transparency, the corners would look the
# same as the old behaviour (no regression).
# ---------------------------------------------------------------------------


def _draw_round_rect(cv, x1, y1, x2, y2, r, **kwargs):
    """Draw a filled rounded rectangle on a Canvas as two rectangles plus four
    corner pie-slices. This gives a crisp, exact-radius arc (a smooth-spline
    polygon collapses the radius and bulges the straight edges). All pieces
    share the caller's ``tags`` so they can be cleared/lowered as one."""
    r = max(0, min(int(r), (x2 - x1) // 2, (y2 - y1) // 2))
    fill = kwargs.get("fill", "")
    tags = kwargs.get("tags")
    base = {"fill": fill, "outline": fill, "width": 0}
    if tags:
        base["tags"] = tags
    cv.create_rectangle(x1 + r, y1, x2 - r, y2, **base)
    cv.create_rectangle(x1, y1 + r, x2, y2 - r, **base)
    d = 2 * r
    arc = {"fill": fill, "outline": fill, "style": "pieslice"}
    if tags:
        arc["tags"] = tags
    cv.create_arc(x1, y1, x1 + d, y1 + d, start=90, extent=90, **arc)
    cv.create_arc(x2 - d, y1, x2, y1 + d, start=0, extent=90, **arc)
    cv.create_arc(x1, y2 - d, x1 + d, y2, start=180, extent=90, **arc)
    cv.create_arc(x2 - d, y2 - d, x2, y2, start=270, extent=90, **arc)


# (WarmClaude class lives in cc_warm.py)

@dataclasses.dataclass
class StreamSession:
    """Holds all mutable state for a single streaming translation session.
    Created fresh for each translation, replacing the 10 individual _stream_*
    instance attributes that were scattered across TranslatorApp."""
    popup_ready: bool = False
    queue: object = dataclasses.field(default_factory=queue.Queue)
    accum: str = ""
    flush_job: object = None  # tkinter after() job ID; None when idle
    cols: int = 0
    fixed_w: int = 0
    max_h: int = 0
    origin_x: object = None  # int once the first frame is placed
    origin_y: object = None  # int once the first frame is placed
    monitor_rect: object = None  # (left, top, right, bottom) or None
    user_scrolled: bool = False  # user moved the view; stop auto-pinning to top
    centered_ready: bool = False  # centred popup's fixed geometry/region already set
    rendered: str = ""  # raw text currently in the popup Text (for append-only streaming)


class TranslatorApp(WarmMixin, UpdateMixin, TrayMixin, AboutMixin,
                    QuickInputMixin, DiagnosticsMixin, HistoryMixin,
                    SettingsMixin):
    def __init__(self):
        # Detect a fresh install *before* loading config: on first run the
        # config file doesn't exist yet. We use this to enable autostart by
        # default for new users (see _run_startup_tasks), without ever
        # re-enabling it for existing users who deliberately turned it off.
        self._fresh_install = not os.path.exists(CONFIG_PATH)
        self.cfg = load_config()
        
        # Initialize i18n (language support)
        lang = self.cfg.get(CFG.LANGUAGE)
        if lang is None:
            # First startup: auto-detect system language
            detected = i18n.detect_system_language()
            self.cfg[CFG.LANGUAGE] = detected
            save_config(self.cfg)
            lang = detected
        
        i18n.initialize(lang)
        self.theme = resolve_theme(self.cfg)
        self.last_c_time = 0.0
        self.ctrl_down = False
        self.win_down = False
        self.shift_down = False
        self._clip_saved = None       # clipboard snapshot taken when Ctrl went down
        self._clip_seq_before = None  # clipboard sequence before Ctrl+C copy
        self._uia = None              # cached IUIAutomation COM object (lazy-init)
        self.popup = None
        self.settings_win = None
        self.history_win = None
        self.diagnostics_win = None
        self.about_win = None
        self.support_win = None
        self.quick_input_win = None
        self.paused = False
        self.tray = None
        self._anim_job = None
        self._last_input = None
        self._last_origin = "text"
        self._last_class = "text"
        self._last_result_ok = None
        self._last_result_title = ""
        self._last_result_text = ""
        self._trigger_queue = queue.Queue()
        self._ocr_queue = queue.Queue()   # Win+Shift+C requests → main thread
        self._ocr_selecting = False       # region-selector overlay is open
        self._ss = StreamSession()
        # Monotonic in-flight job id. Every new translation/OCR request bumps
        # this; stale worker threads compare against it before touching the UI
        # or history, so a slow/hung request can never write its result into a
        # newer request's popup or history ("latest request wins").
        self._job_id = 0
        self._resize_mode = None
        self._resize_start = None
        self._pending_loading_job = None   # after() token for deferred loading popup

        # Self-update state.
        self._update_in_progress = False
        self._nightly_job = None
        self._settings_check = None   # set while the settings window is open

        # Warm process pool state (speed-up). Guarded by _warm_lock.
        # One profile ("translate", "dictionary") keeps up to WARM_POOL_DEPTH
        # pre-warmed WarmClaude processes ready, so the common cold paths get
        # the same head-start as normal translation and back-to-back requests
        # don't fall back to a ~2s cold start while a replacement warms up.
        # _warm_pool: profile -> list[WarmClaude]; _warm_pending: profile -> int
        # counts spawns in flight so refills never over-shoot the target depth.
        self._warm_lock = threading.Lock()
        self._warm_pool = {}         # profile -> list of ready WarmClaude
        self._warm_pending = {}      # profile -> number of spawns in flight
        self._warm_enabled = WARM_POOL_ENABLED

        self.root = tk.Tk()
        # Give every window a real identity so the Windows taskbar / Alt-Tab
        # preview shows "CC Translate" and the app icon instead of Tk's default
        # "tk" title and feather icon. iconbitmap(default=...) also becomes the
        # fallback icon for every Toplevel created afterwards.
        self.root.title(APP_NAME)
        try:
            if os.path.exists(ICON_PATH):
                self.root.iconbitmap(default=ICON_PATH)
        except Exception as e:
            log_error("root_iconbitmap", e)
        self.root.withdraw()

        # Match tk's logical scaling to the real screen DPI so text is crisp
        # and correctly sized after declaring DPI awareness above.
        try:
            dpi = self.root.winfo_fpixels("1i")   # pixels per inch
            self.root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass

        self._setup_scrollbar_style()

        # Start draining hotkey triggers on the main (Tk) thread. This must be
        # running before the listener so early double-presses are handled in
        # order instead of piling up and firing in a burst.
        self.root.after(TRIGGER_POLL_MS, self._pump_triggers)
        self.root.after(TRIGGER_POLL_MS, self._pump_ocr)

        self._start_listener()
        self._start_tray()

        # Pre-warm the first Claude process so the very first translation is
        # fast too. Done in the background so startup stays responsive.
        self._spawn_warm_async()

        # Run shortcut/migration work in background so startup stays responsive
        # and the first hotkey trigger is not blocked by PowerShell startup.
        threading.Thread(target=self._run_startup_tasks, daemon=True).start()

        # Pre-warm the UIA module cache so the first cross-process double-press
        # (e.g. in VS Code) doesn't incur the comtypes typelib-parse delay.
        threading.Thread(target=self._prewarm_uia, daemon=True).start()

        # Arm the nightly auto-update scheduler (a no-op when disabled / not a
        # git deploy — the tick re-checks config each time it fires).
        self._schedule_nightly_update()

        # If we just came back from an auto-update restart, confirm it with a
        # tray balloon once the icon has had a moment to register.
        self.root.after(2500, self._show_update_notice_if_any)

    def _prewarm_uia(self):
        """Pre-parse the UIAutomationCore typelib so the first cross-process
        double-press doesn't stall while comtypes generates its cache."""
        try:
            import comtypes.client
            import io
            import sys as _sys
            _old, _sys.stdout = _sys.stdout, io.StringIO()
            try:
                comtypes.client.GetModule('UIAutomationCore.dll')
            finally:
                _sys.stdout = _old
        except Exception:
            pass

    def _run_startup_tasks(self):
        try:
            ensure_startmenu_shortcut()
            # One-time migration: earlier versions auto-started via QuickTranslate.vbs.
            # Convert that into the new managed .lnk so the setting stays in sync.
            if os.path.exists(LEGACY_STARTUP_VBS) and not is_autostart_enabled():
                set_autostart(True)
            # First-run default: new installs start with autostart ON. Gated by a
            # persistent flag so we only ever do this once — after that the user's
            # choice in Settings is respected and never overridden.
            if not self.cfg.get(CFG.AUTOSTART_INITIALIZED, False):
                if self._fresh_install and not is_autostart_enabled():
                    set_autostart(True)
                self.cfg[CFG.AUTOSTART_INITIALIZED] = True
                save_config(self.cfg)
        except Exception as e:
            log_error("startup_tasks", e)

    # ---------- Self-update ----------
    def _save_config(self, cfg):
        """Persist a config dict via the module-level ``save_config``.

        Thin instance wrapper so mixin modules (e.g. SettingsMixin in
        cc_app_settings) can persist config through ``self`` without importing
        translator.pyw — ``save_config`` stays in this module because it belongs
        to the config-IO family (CONFIG_PATH / Config / _atomic_write_json)."""
        save_config(cfg)

    def _is_busy(self):
        """True when yanking the app out for a restart would disrupt the user:
        a translation popup is showing, or the settings / history window is
        open. Used to defer the unattended nightly update."""
        if self.popup is not None:
            return True
        for w in (getattr(self, "settings_win", None),
                  getattr(self, "history_win", None),
                  getattr(self, "diagnostics_win", None),
                  getattr(self, "quick_input_win", None)):
            try:
                if w is not None and tk.Toplevel.winfo_exists(w):
                    return True
            except Exception:
                pass
        return False

    def _clipboard_sequence(self):
        """Current Windows clipboard sequence number, or None if unavailable."""
        try:
            return int(ctypes.windll.user32.GetClipboardSequenceNumber())
        except Exception:
            return None

    def _setup_scrollbar_style(self):
        """A minimal capsule scrollbar: just a thumb on the right, no arrow
        buttons. The native Windows ttk themes ignore colour options, so we
        base this on 'clam' (which honours them) and strip the layout down to
        the trough + thumb only."""
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        # Remove the up/down arrow buttons — keep only the trough and thumb.
        style.layout("CC.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {
                "sticky": "ns",
                "children": [
                    ("Vertical.Scrollbar.thumb",
                     {"expand": "1", "sticky": "nswe"}),
                ],
            }),
        ])
        style.configure(
            "CC.Vertical.TScrollbar",
            gripcount=0,
            background=self.theme["scroll_thumb"],
            troughcolor=self.theme["trough"],
            bordercolor=self.theme["trough"],
            relief="flat", borderwidth=0,
            width=8,
        )
        style.map(
            "CC.Vertical.TScrollbar",
            background=[("active", self.theme["scroll_thumb_active"]),
                       ("pressed", self.theme["scroll_thumb_active"])],
        )

    # ---------- Hotkey detection ----------
    def _start_listener(self):
        WIN_KEYS = (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r)
        SHIFT_KEYS = (keyboard.Key.shift, keyboard.Key.shift_l,
                      keyboard.Key.shift_r)

        def on_press(key):
            try:
                # Track Win/Shift regardless of pause so the OCR chord below can
                # fire; these are cheap booleans with no side effects.
                if key in WIN_KEYS:
                    self.win_down = True
                elif key in SHIFT_KEYS:
                    self.shift_down = True

                # Win+Shift+C → OCR screenshot translation. Detect by virtual
                # key code (67 = 'C') since modifiers can blank key.char. Ctrl
                # is NOT part of this chord, so it never clashes with the
                # double-Ctrl+C translate trigger below.
                if (getattr(key, "vk", None) == 67
                        and self.win_down and self.shift_down
                        and not self.paused
                        and self.cfg.get(CFG.OCR_HOTKEY_ENABLED, True)):
                    self._ocr_queue.put(time.time())
                    return

                if self.paused:
                    return
                if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                    if not self.ctrl_down:
                        # Record clipboard generation before Ctrl+C so we can tell
                        # whether this trigger actually copied a new selection.
                        self._clip_seq_before = self._clipboard_sequence()
                        # Snapshot clipboard before Ctrl+C so we can restore it
                        # afterwards. Only do this when clipboard protection is
                        # enabled — the snapshot itself is a clipboard read that
                        # can race with system tools like Win+Shift+S.
                        if self.cfg.get(CFG.CLIPBOARD_PROTECTION_ENABLED, False):
                            try:
                                self._clip_saved = pyperclip.paste()
                            except Exception as e:
                                self._clip_saved = None
                                log_error("clip_snapshot", e)
                    self.ctrl_down = True
                elif self.ctrl_down and getattr(key, "char", None) == "\x03":
                    now = time.time()
                    if now - self.last_c_time <= self.cfg[CFG.DOUBLE_PRESS_WINDOW]:
                        self.last_c_time = 0.0
                        # Hand off to the main thread; never touch Tk from here.
                        self._trigger_queue.put(now)
                    else:
                        self.last_c_time = now
            except Exception:
                pass

        def on_release(key):
            if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                self.ctrl_down = False
            elif key in WIN_KEYS:
                self.win_down = False
            elif key in SHIFT_KEYS:
                self.shift_down = False

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()

    # ---------- Trigger ----------
    def _pump_triggers(self):
        """Runs on the Tk main thread. Drains hotkey requests queued by the
        listener thread and coalesces a rapid burst into a single translation
        (the last one wins), then reschedules itself."""
        fired = False
        try:
            while True:
                self._trigger_queue.get_nowait()
                fired = True
        except queue.Empty:
            pass
        if fired and not self.paused:
            # Small settle delay so the Ctrl+C copy lands on the clipboard
            # before we read it.
            self.root.after(TRIGGER_SETTLE_MS, self._trigger)
        self.root.after(TRIGGER_POLL_MS, self._pump_triggers)

    def _focused_control_has_selection(self):
        """Query the currently focused Win32 control for a text selection.

        Uses GetGUIThreadInfo to find the focused HWND, then sends EM_GETSEL
        to ask for the selection range.  For cross-process controls (VS Code,
        Electron, browsers) that ignore EM_GETSEL, falls through to a UIA
        (UI Automation) check which works across process boundaries.

        Returns:
          True  – a non-empty selection was confirmed
          False – the control has NO selection (cursor only)
          None  – unable to determine
        """
        try:
            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize",       ctypes.c_uint),
                    ("flags",        ctypes.c_uint),
                    ("hwndActive",   ctypes.c_void_p),
                    ("hwndFocus",    ctypes.c_void_p),
                    ("hwndCapture",  ctypes.c_void_p),
                    ("hwndMenuOwner", ctypes.c_void_p),
                    ("hwndMoveSize", ctypes.c_void_p),
                    ("hwndCaret",    ctypes.c_void_p),
                    ("rcCaret",      ctypes.c_ubyte * 16),
                ]

            hwnd_fg = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd_fg:
                return None
            tid = ctypes.windll.user32.GetWindowThreadProcessId(hwnd_fg, None)
            gti = GUITHREADINFO()
            gti.cbSize = ctypes.sizeof(GUITHREADINFO)
            if not ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
                return None
            hwnd_focus = gti.hwndFocus
            if not hwnd_focus:
                return None

            # EM_GETSEL (0x00B0): ask the focused edit control for selection.
            # wParam / lParam = pointers to DWORD for start / end positions.
            EM_GETSEL = 0x00B0
            start = ctypes.c_uint(0)
            end   = ctypes.c_uint(0)
            ret = ctypes.windll.user32.SendMessageW(
                hwnd_focus, EM_GETSEL,
                ctypes.byref(start), ctypes.byref(end))
            # EM_GETSEL returns MAKELONG(start, end) for short selections,
            # but start/end via pointers is reliable for all lengths.
            # A return value of 0 with start==end==0 may mean the control
            # ignored the message (cross-process) → fall through to UIA.
            if ret == 0 and start.value == 0 and end.value == 0:
                return self._uia_focused_has_selection()
            return start.value != end.value
        except Exception:
            return None

    def _uia_focused_has_selection(self):
        """Check text selection via Windows UI Automation.

        Works cross-process: handles VS Code (Electron), browsers, and other
        modern apps that don't respond to EM_GETSEL.  Lazy-initialises the
        IUIAutomation COM object once and caches it for subsequent calls.

        Returns True/False/None (None = UIA unavailable or inconclusive).
        """
        UIA_TextPatternId = 10014
        try:
            import comtypes
            import comtypes.client
            import io
            import sys as _sys

            # Lazy-init: GetModule parses the typelib and is slow the first
            # time (~0.5 s); subsequent calls hit the on-disk cache.
            if self._uia is None:
                # Redirect stdout so comtypes doesn't print "generated module"
                _old, _sys.stdout = _sys.stdout, io.StringIO()
                try:
                    _mod = comtypes.client.GetModule('UIAutomationCore.dll')
                finally:
                    _sys.stdout = _old
                try:
                    self._uia = comtypes.CoCreateInstance(
                        _mod.CUIAutomation._reg_clsid_,
                        interface=_mod.IUIAutomation,
                        clsctx=comtypes.CLSCTX_INPROC_SERVER,
                    )
                    self._uia_mod = _mod
                except Exception as e:
                    log_error("uia_create", e)
                    self._uia = False  # mark as permanently failed

            if not self._uia:
                return None

            mod = self._uia_mod
            focused = self._uia.GetFocusedElement()
            if focused is None:
                return None

            try:
                pattern_unk = focused.GetCurrentPattern(UIA_TextPatternId)
                if pattern_unk is None:
                    return None
                text_pat = pattern_unk.QueryInterface(mod.IUIAutomationTextPattern)
                sel_array = text_pat.GetSelection()
                if sel_array.Length == 0:
                    return False
                text_range = sel_array.GetElement(0)
                selected = text_range.GetText(-1)
                return bool(selected)
            except Exception:
                return None

        except Exception as e:
            log_error("uia_selection", e)
            return None

    def _trigger(self):
        # Always invoked on the main thread (via _pump_triggers → after).
        seq_before = self._clip_seq_before
        self._clip_seq_before = None
        try:
            text = pyperclip.paste()
        except Exception as e:
            text = ""
            log_error("trigger_paste", e)
        seq_after = self._clipboard_sequence()
        # The selection is now on the clipboard; put back what the user had
        # before their Ctrl+C so we don't disturb their copy/paste workflow.
        self.root.after(CLIP_RESTORE_MS, self._restore_clipboard)
        text = (text or "").strip()

        # Primary check: ask the focused Win32 control directly whether it has
        # a text selection.  This reliably catches the case where an input field
        # copies its *entire* content on Ctrl+C even though nothing was selected
        # (browser address bars, some custom controls).
        has_sel = self._focused_control_has_selection()
        if has_sel is False:
            # Control confirmed: cursor only, no selection → open quick input.
            self._open_quick_input()
            return

        # Fallback: Ctrl+C without a real text selection leaves clipboard
        # generation unchanged; in that case jump straight to quick-input.
        if (seq_before is not None and seq_after is not None
                and seq_after == seq_before):
            self._open_quick_input()
            return
        if not text:
            self._open_quick_input()
            return
        text = text[: self.cfg[CFG.MAX_CHARS]]
        self._show_loading(text)

    def _restore_clipboard(self):
        """Restore the pre-Ctrl+C clipboard snapshot. Skips when there was no
        snapshot or it was empty/non-text (pyperclip can't round-trip images or
        file lists, so we leave those rather than blanking the clipboard).
        Respects the clipboard protection setting; disabled by default to avoid
        interference with system clipboard tools like Win+Shift+S."""
        if not self.cfg.get(CFG.CLIPBOARD_PROTECTION_ENABLED, False):
            return
        saved = self._clip_saved
        self._clip_saved = None
        if not saved:
            return
        try:
            if pyperclip.paste() != saved:
                pyperclip.copy(saved)
        except Exception as e:
            log_error("restore_clipboard", e)

    # ---------- Translation ----------
    def _begin_job(self):
        """Start a new in-flight request and return its id. Bumping the counter
        invalidates any still-running worker from a previous request."""
        self._job_id += 1
        return self._job_id

    def _job_is_current(self, job_id):
        """True when job_id is the most recently started request. Worker threads
        call this (on the UI thread) before writing any popup/history state."""
        return job_id == self._job_id

    def _cancel_stream_flush(self):
        """Cancel any pending after() flush job and clear the reference."""
        if self._ss.flush_job:
            try:
                self.root.after_cancel(self._ss.flush_job)
            except Exception:
                pass
            self._ss.flush_job = None

    def _show_loading(self, text, origin="text"):
        self._destroy_popup()
        self._last_input = text
        self._last_origin = origin
        self._last_class = classify_selection(text)
        self._cancel_stream_flush()
        self._ss = StreamSession()
        job_id = self._begin_job()
        # Snapshot the history metadata now, on the main thread, so a later
        # request can't overwrite self._last_* before the worker persists this
        # job's result (which would pair the new input with the old output).
        meta = self._history_meta()
        # Defer the loading popup so fast translations (common with the warm pool)
        # skip it entirely.  The small loading card → larger result popup size jump
        # is visible as a flash when translations complete in < 200 ms; with this
        # guard the result popup appears directly without any intermediate card.
        # If the translation takes longer than 200 ms the popup appears normally.
        job_id_capture = job_id

        def _show_loading_popup():
            self._pending_loading_job = None
            # Only show if nothing has arrived yet (popup is still None) and the
            # job that kicked off this timer is still the current job.
            if self.popup is None and self._job_is_current(job_id_capture):
                self.popup = self._make_loading_popup()
                self._animate_loading(0)

        self._pending_loading_job = self.root.after(200, _show_loading_popup)
        threading.Thread(target=self._do_translate, args=(text, job_id, meta),
                         daemon=True).start()

    def _history_meta(self) -> Dict[str, Any]:
        """Capture the per-job history fields from the current self._last_*
        state. Must be called on the main thread at request start; the returned
        dict is then owned by that job's worker thread."""
        return {
            "input": self._last_input,
            "origin": self._last_origin,
            "is_code": self._last_class == "code",
            "kind": self._history_kind(),
        }

    def _animate_loading(self, step):
        """Spin the accent indicator through LOADING_SPINNER frames."""
        win = self.popup
        if not (win and getattr(win, "_spinner", None)):
            return
        try:
            if not win._spinner.winfo_exists():
                return
            win._spinner.config(text=LOADING_SPINNER[step % len(LOADING_SPINNER)])
        except Exception:
            return
        self._anim_job = self.root.after(
            120, lambda: self._animate_loading(step + 1))

    def _stop_animation(self):
        if self._anim_job:
            try:
                self.root.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None

    def _retry(self):
        if self._last_input:
            self._show_loading(self._last_input, origin=self._last_origin)

    # ---------- OCR screenshot translation ----------
    def _virtual_screen_rect(self):
        """(x, y, w, h) of the whole virtual desktop in Windows virtual-screen
        coordinates (origin can be negative on multi-monitor setups)."""
        try:
            gsm = ctypes.windll.user32.GetSystemMetrics
            x = gsm(76)   # SM_XVIRTUALSCREEN
            y = gsm(77)   # SM_YVIRTUALSCREEN
            w = gsm(78)   # SM_CXVIRTUALSCREEN
            h = gsm(79)   # SM_CYVIRTUALSCREEN
            if w > 0 and h > 0:
                return x, y, w, h
        except Exception as e:
            log_error("virtual_screen_rect", e)
        # Fallback: primary screen only.
        return (0, 0, self.root.winfo_screenwidth(),
                self.root.winfo_screenheight())

    def _pump_ocr(self):
        """Main-thread drain of Win+Shift+C requests queued by the listener."""
        fired = False
        try:
            while True:
                self._ocr_queue.get_nowait()
                fired = True
        except queue.Empty:
            pass
        if fired and not self.paused and not self._ocr_selecting:
            self._open_region_selector()
        self.root.after(TRIGGER_POLL_MS, self._pump_ocr)

    def _ocr_from_menu(self):
        """Tray 'screenshot translate' entry — start region selection now
        (ignores pause, since it's an explicit user action)."""
        if not self._ocr_selecting:
            self._open_region_selector()

    def _open_region_selector(self):
        """Full-screen overlay for click-drag region selection. Outside the
        selection is dimmed; the selected area stays at normal brightness so the
        user can verify exactly what will be captured. ESC or a right-click
        cancels; a drag smaller than 10x10 px cancels silently."""
        if self._ocr_selecting:
            return
        vx, vy, vw, vh = self._virtual_screen_rect()

        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        dim_bg = "#101216"
        key_bg = "#00ff00"
        transparent_hole = False
        try:
            # Preferred path on Windows: make the canvas key color transparent,
            # then draw dim masks around the drag box so the selected area is
            # truly undimmed.
            overlay.configure(bg=key_bg, cursor="crosshair")
            overlay.attributes("-transparentcolor", key_bg)
            transparent_hole = True
        except Exception:
            # Fallback for environments that do not support transparentcolor.
            try:
                overlay.attributes("-alpha", 0.28)
            except Exception:
                pass
            overlay.configure(bg=dim_bg, cursor="crosshair")
        overlay.geometry(f"{vw}x{vh}+{vx}+{vy}")
        self._ocr_selecting = True
        self._ocr_overlay = overlay

        canvas_bg = key_bg if transparent_hole else dim_bg
        canvas = tk.Canvas(overlay, bg=canvas_bg, highlightthickness=0,
                           cursor="crosshair")
        canvas.pack(fill="both", expand=True)

        shade_kwargs = {"fill": dim_bg, "outline": ""}
        if transparent_hole:
            shade_kwargs["stipple"] = "gray50"
        shades = [
            canvas.create_rectangle(0, 0, vw, vh, **shade_kwargs),  # top/full
            canvas.create_rectangle(0, 0, 0, 0, **shade_kwargs),    # left
            canvas.create_rectangle(0, 0, 0, 0, **shade_kwargs),    # right
            canvas.create_rectangle(0, 0, 0, 0, **shade_kwargs),    # bottom
        ]

        hint = canvas.create_text(
            vw // 2, 30, fill="#e6e9f0",
            font=("Microsoft YaHei UI", 13),
            text=i18n.get("ocr.drag_select_hint"))

        state = {"sx": 0, "sy": 0, "rect": None}

        def set_dim_hole(x0, y0, x1, y1):
            x0 = max(0, min(vw, x0))
            x1 = max(0, min(vw, x1))
            y0 = max(0, min(vh, y0))
            y1 = max(0, min(vh, y1))
            canvas.coords(shades[0], 0, 0, vw, y0)      # top
            canvas.coords(shades[1], 0, y0, x0, y1)     # left
            canvas.coords(shades[2], x1, y0, vw, y1)    # right
            canvas.coords(shades[3], 0, y1, vw, vh)     # bottom

        def on_down(e):
            state["sx"], state["sy"] = e.x, e.y
            if state["rect"]:
                canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(
                e.x, e.y, e.x, e.y, outline="#7aa2f7", width=2)
            set_dim_hole(e.x, e.y, e.x, e.y)
            canvas.delete(hint)

        def on_drag(e):
            if state["rect"]:
                x0, x1 = sorted((state["sx"], e.x))
                y0, y1 = sorted((state["sy"], e.y))
                canvas.coords(state["rect"], x0, y0, x1, y1)
                set_dim_hole(x0, y0, x1, y1)

        def on_up(e):
            x0, y0 = min(state["sx"], e.x), min(state["sy"], e.y)
            x1, y1 = max(state["sx"], e.x), max(state["sy"], e.y)
            w, h = x1 - x0, y1 - y0
            self._close_region_selector()
            if w < 10 or h < 10:
                return   # accidental click / tiny drag → cancel silently
            # Translate canvas (overlay-local) coords back to virtual-screen
            # coords for the grab. Delay it a beat so the dimming overlay is
            # fully repainted away before we capture the underlying pixels.
            gx, gy = vx + x0, vy + y0
            self.root.after(
                120, lambda: self._capture_and_translate(gx, gy, w, h))

        def cancel(_e=None):
            self._close_region_selector()

        canvas.bind("<Button-1>", on_down)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_up)
        canvas.bind("<Button-3>", cancel)
        overlay.bind("<Escape>", cancel)
        try:
            overlay.grab_set()
        except Exception:
            pass
        overlay.focus_force()

    def _close_region_selector(self):
        self._ocr_selecting = False
        ov = getattr(self, "_ocr_overlay", None)
        self._ocr_overlay = None
        if ov:
            try:
                ov.grab_release()
            except Exception:
                pass
            try:
                ov.destroy()
            except Exception:
                pass

    def _capture_and_translate(self, x, y, w, h):
        """Grab the chosen region, then translate it via the configured OCR
        engine (Claude Vision by default, or offline Windows OCR)."""
        # Unique temp file per capture so overlapping OCR requests can't clobber
        # each other's screenshot (each path is cleaned up by its own worker).
        img_path = os.path.join(DATA_DIR, "tmp_ocr_%s.png" % uuid.uuid4().hex)
        # The overlay is already destroyed; give the compositor one frame to
        # repaint the uncovered screen before we grab it.
        self.root.update_idletasks()
        if not cc_ocr.save_region(x, y, w, h, img_path):
            self._last_input = None
            self._last_origin = "ocr"
            self._last_class = "ocr"
            self._destroy_popup()
            self.popup = self._make_popup(
                i18n.get("error.screenshot_failed"), is_error=True, title=i18n.get("error.title"),
                highlight=False)
            return

        engine = self.cfg.get(CFG.OCR_ENGINE, "claude")
        if engine == "local":
            self._ocr_translate_local(img_path)
        else:
            self._ocr_translate_vision(img_path)

    def _ocr_translate_local(self, img_path):
        """Offline path: recognise text locally, then run it through the normal
        translation pipeline (which reuses dictionary/sentence/code handling).

        Recognition runs on a worker thread (cc_ocr.ocr_local drives a
        synchronous asyncio.run) so a slow or wedged Windows OCR call can never
        freeze the tray app / hotkeys."""
        self._destroy_popup()
        self._last_input = None
        self._last_origin = "ocr"
        self._last_class = "ocr"
        self._cancel_stream_flush()
        self._ss = StreamSession()
        job_id = self._begin_job()
        self.popup = self._make_loading_popup()
        self._animate_loading(0)
        threading.Thread(target=self._do_ocr_local,
                         args=(img_path, job_id), daemon=True).start()

    def _do_ocr_local(self, img_path, job_id):
        """Worker thread: run local OCR, then hand the result back to the UI."""
        text = ""
        try:
            text = cc_ocr.ocr_local(img_path)
        except Exception as e:
            log_error("ocr_local_call", e)
        finally:
            self._cleanup_ocr_temp(img_path)
        text = (text or "").strip()
        self.root.after(0, lambda: self._finish_ocr_local(text, job_id))

    def _finish_ocr_local(self, text, job_id):
        """UI thread: show the recognised text or an error, guarded by job id so
        a superseded OCR request can't overwrite a newer popup."""
        if not self._job_is_current(job_id):
            return
        self._stop_animation()
        if not text:
            self._last_input = None
            self._last_origin = "ocr"
            self._last_class = "ocr"
            self._destroy_popup()
            self.popup = self._make_popup(
                i18n.get("error.no_text_detected"), is_error=True,
                title=i18n.get("tray.screenshot"), highlight=False)
            return
        text = text[: self.cfg[CFG.MAX_CHARS]]
        self._show_loading(text, origin="ocr")

    def _ocr_translate_vision(self, img_path):
        """Default path: send the screenshot to Claude, which reads and
        translates it in one multimodal call. Only the translation is shown."""
        self._destroy_popup()
        self._last_input = None
        self._last_origin = "ocr"
        self._last_class = "ocr"
        self._cancel_stream_flush()
        self._ss = StreamSession()
        job_id = self._begin_job()
        self.popup = self._make_loading_popup()
        self._animate_loading(0)
        threading.Thread(
            target=self._do_translate_vision, args=(img_path, job_id),
            daemon=True).start()

    def _do_translate_vision(self, img_path, job_id):
        ok, result = self._call_claude_vision(img_path)
        self._cleanup_ocr_temp(img_path)
        self.root.after(0, lambda: self._show_result(ok, result, job_id))

    def _cleanup_ocr_temp(self, img_path):
        try:
            if os.path.exists(img_path):
                os.remove(img_path)
        except Exception as e:
            log_error("ocr_temp_cleanup", e)

    def _call_claude_vision(self, img_path: str) -> Tuple[bool, str]:
        """One-shot Claude call that reads the image via the CLI's `@path`
        reference and returns only the translation. Mirrors _call_claude's
        subprocess/JSON handling.

        Two details are essential for the image to actually be read:
          * The `@path` mention is quoted — DATA_DIR contains a space
            ("CC Translate"), and an unquoted mention would break at the space,
            so Claude never sees the file and replies "please share the image".
          * `--tools ""` disables tools, so the CLI attaches the image as a
            multimodal content block instead of routing it through the Read
            tool (which, in safe-mode headless runs, asks for permission and
            returns a "I need permission to read the file" message)."""
        payload = vision_image_mention(img_path)
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                [CLAUDE_CMD, "-p", "--safe-mode", "--model",
                 self.cfg[CFG.MODEL],
                 "--system-prompt", OCR_VISION_PROMPT,
                 "--output-format", "json",
                 "--tools", "",
                 "--no-session-persistence"],
                input=payload,
                capture_output=True, text=True, encoding="utf-8",
                timeout=90,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if proc.stdout:
                out = proc.stdout.strip()
                try:
                    result = json.loads(out).get("result", "").strip()
                    if result:
                        log_perf("ocr_vision_done", {
                            "wall_ms": int((time.perf_counter() - t0) * 1000),
                        })
                        return True, result
                except json.JSONDecodeError:
                    if out:
                        return True, out
            return False, self._humanize_error(proc.stderr or "")
        except subprocess.TimeoutExpired:
            return False, i18n.get("error.ocr_timeout")
        except Exception as e:
            log_error("call_claude_vision", e)
            return False, i18n.get("error.unexpected").format(error=e)

    def _should_summarize(self, text):
        """True if the long-text summary feature should apply to this selection:
        the setting is on, it's natural-language prose long enough to benefit
        from a leading summary, and not a single word, code, a screenshot, or a
        list/config/URL dump.

        Applies to both plain text and mixed prose+code selections (the summary
        prompt keeps any code verbatim). Screenshots (ocr) are excluded for now
        because they take a separate one-shot vision path, not this pipeline."""
        if not self.cfg.get(CFG.SUMMARY_ENABLED, False):
            return False
        if self._last_class not in ("text", "mixed"):
            return False
        if is_single_word(text):
            return False
        if len(text) < SUMMARY_MIN_CHARS:
            return False
        return is_summarizable_prose(text)

    def _system_prompt_for(self, text):
        """Pick the system prompt for the current selection: code explanation
        for pure-code selections, dictionary for single words, otherwise the
        normal translation prompt (optionally with a leading summary)."""
        if self._last_class == "code":
            return CODE_EXPLAIN_PROMPT
        if is_single_word(text):
            return DICTIONARY_PROMPT
        mode = self.cfg.get(CFG.DIRECTION, "auto")
        app_language = self.cfg.get(CFG.LANGUAGE) or i18n.get_language()
        base_prompt = direction_prompt(mode, app_language)
        if self._last_origin == "ocr":
            base_prompt += OCR_STRUCTURE_HINT
        if self._should_summarize(text):
            return base_prompt + summary_instruction(app_language) + SUMMARY_SUFFIX
        return base_prompt + SYSTEM_SUFFIX

    def _result_title(self, ok=True):
        """Title for the result popup, reflecting the active mode."""
        if not ok:
            return i18n.get("error.title")
        if self._last_origin == "ocr":
            return i18n.get("tray.screenshot")
        if self._last_class == "code":
            return i18n.get("result.title_code")
        if self._last_input and is_single_word(self._last_input):
            return i18n.get("result.title_dict")
        return i18n.get("result.title")

    def _history_kind(self):
        if self._last_origin == "ocr":
            return "ocr"
        if self._last_class == "code":
            return "code"
        if self._last_input and is_single_word(self._last_input):
            return "dict"
        return "text"

    def _remember_result(self, ok, title, text):
        self._last_result_ok = bool(ok)
        self._last_result_title = title or ""
        self._last_result_text = (text or "").strip()

    def _do_translate(self, text, job_id, meta):
        # Long, non-dictionary text streams so the translation appears
        # progressively; short text uses the simpler one-shot path.
        t0 = time.perf_counter()
        ss = self._ss   # bind this job's session; a newer job swaps self._ss
        dictionary = is_single_word(text)
        is_code = self._last_class == "code"
        # Summary mode needs a different system prompt than the warm process was
        # spawned with, so it must skip the warm fast-path and take the cold
        # streaming path (which rebuilds the prompt via _system_prompt_for).
        summarize = self._should_summarize(text)

        # Fast path: a pre-warmed process already has the CLI initialised and
        # the right system prompt loaded, so we skip the ~2s cold startup.
        # Normal translation and single-word dictionary lookups each have their
        # own warm profile; code-explain and summary stay cold (rarer, and a
        # different prompt). Any failure falls through to the cold path below,
        # so this is always safe.
        warm_profile = None
        if summarize or is_code:
            warm_profile = None
        elif dictionary:
            warm_profile = "dictionary"
        else:
            warm_profile = "translate"
        if warm_profile is not None:
            if self._warm_translate(text, job_id, ss, meta, warm_profile):
                log_perf("translate_done", {
                    "mode": "warm",
                    "chars": len(text),
                    "wall_ms": int((time.perf_counter() - t0) * 1000),
                    "ok": True,
                })
                return

        mode = "oneshot"
        try:
            if len(text) >= STREAM_MIN_CHARS and not dictionary:
                mode = "stream"
                if self._stream_claude(text, job_id, ss, meta):
                    log_perf("translate_done", {
                        "mode": mode,
                        "chars": len(text),
                        "wall_ms": int((time.perf_counter() - t0) * 1000),
                        "ok": True,
                    })
                    return   # streaming handled display + history
            ok, result = self._call_claude(text)
        except Exception as e:
            ok, result = False, i18n.get("error.unexpected").format(error=e)
            log_error("translate", e)
        log_perf("translate_done", {
            "mode": mode,
            "chars": len(text),
            "wall_ms": int((time.perf_counter() - t0) * 1000),
            "ok": bool(ok),
        })
        self.root.after(0, lambda: self._show_result(ok, result, job_id))

    def _warm_translate(self, text, job_id, ss, meta, profile="translate"):
        """Translate using a pre-warmed process for the given profile, streaming
        deltas through the same display pipeline as _stream_claude. Returns True
        on success, or False to fall back to the cold path. The warm process is
        consumed and a replacement for the same profile is spawned afterwards."""
        warm = self._take_warm(profile)
        if warm is None:
            return False
        ss.popup_ready = False
        t0 = time.perf_counter()
        try:
            def on_delta(txt):
                ss.queue.put(txt)
                self.root.after(0, lambda: self._stream_flush(job_id))

            final = warm.send_and_stream(text, on_delta)
            if not final:
                return False
            self.root.after(0, lambda: self._stream_finalize(final, job_id))
            self._record_history(job_id, meta, final,
                                 is_dict=is_single_word(meta["input"]))
            log_perf("warm_cli_done", {
                "chars": len(text),
                "wall_ms": int((time.perf_counter() - t0) * 1000),
            })
            return True
        except Exception as e:
            log_error("warm_translate", e)
            return False
        finally:
            try:
                warm.close()
            except Exception:
                pass
            self._spawn_warm_async(profile)   # keep this profile warm

    def _record_history(self, job_id: int, meta: Dict[str, Any], final: str,
                        is_dict: bool) -> None:
        """Persist a completed translation using the job-bound metadata snapshot
        (never live self._last_*), so a superseded request can't pair the new
        input with this output. Skipped when the job is stale or history is off."""
        if not self._job_is_current(job_id):
            return
        if not self.cfg.get(CFG.HISTORY_ENABLED, True):
            return
        if not (meta["input"] or meta["origin"] == "ocr"):
            return
        add_history(meta["input"] or "", final, is_dict,
                    self.cfg.get(CFG.HISTORY_LIMIT, 100),
                    is_code=meta["is_code"], kind=meta["kind"])

    def _stream_claude(self, text: str, job_id: int, ss, meta: Dict[str, Any]) -> bool:
        """Stream a long translation via stream-json, updating the popup as
        deltas arrive. Returns True on success, False to fall back to one-shot.

        Hardened like the warm path: a watchdog timer kills a runaway CLI, the
        child process is always cleaned up, and only a non-error terminal
        `result` event (or, failing that, accumulated deltas) counts as success
        — a mid-stream abort no longer passes truncated text off as a result."""
        system_prompt = self._system_prompt_for(text)
        payload = f"<text>\n{text}\n</text>"
        ss.popup_ready = False
        t0 = time.perf_counter()
        proc = None
        killed = {"v": False}
        timer = None
        try:
            proc = subprocess.Popen(
                [CLAUDE_CMD, "-p", "--safe-mode", "--model", self.cfg[CFG.MODEL],
                 "--system-prompt", system_prompt,
                 "--output-format", "stream-json",
                 "--include-partial-messages", "--verbose",
                 "--tools", "",   # no tools needed → smaller prompt, faster API
                 "--exclude-dynamic-system-prompt-sections",
                 "--no-session-persistence"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            def _watchdog(p=proc):
                killed["v"] = True
                try:
                    p.kill()
                except Exception:
                    pass
            timer = threading.Timer(STREAM_SEND_TIMEOUT_S, _watchdog)
            timer.daemon = True
            timer.start()

            proc.stdin.write(payload)
            proc.stdin.close()

            acc = []
            result_text = None
            is_error = False

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                typ = obj.get("type")
                if typ == "stream_event":
                    ev = obj.get("event", {})
                    if ev.get("type") == "content_block_delta":
                        delta = ev.get("delta", {})
                        txt = delta.get("text", "")
                        if txt:
                            acc.append(txt)
                            ss.queue.put(txt)
                            self.root.after(
                                0, lambda: self._stream_flush(job_id))
                elif typ == "result":
                    # Terminal envelope: trust its result and error flag over the
                    # raw deltas so a failed run isn't shown as a translation.
                    is_error = bool(obj.get("is_error"))
                    if not is_error:
                        r = (obj.get("result") or "").strip()
                        if r:
                            result_text = r
                    break
            proc.wait()

            # A killed/timed-out or CLI-reported error run is a failure, even if
            # some partial deltas arrived: fall back to the one-shot path.
            if killed["v"] or is_error or proc.returncode not in (0, None):
                log_perf("stream_cli_incomplete", {
                    "chars": len(text),
                    "killed": killed["v"],
                    "is_error": is_error,
                    "rc": proc.returncode,
                })
                return False

            final = (result_text or "".join(acc)).strip()
            if not final:
                log_perf("stream_cli_empty", {"chars": len(text)})
                return False   # nothing streamed → fall back to one-shot
            self.root.after(0, lambda: self._stream_finalize(final, job_id))
            self._record_history(job_id, meta, final, is_dict=False)
            log_perf("stream_cli_done", {
                "chars": len(text),
                "wall_ms": int((time.perf_counter() - t0) * 1000),
            })
            return True
        except Exception as e:
            log_perf("stream_cli_error", {"chars": len(text), "err": str(e)[:160]})
            log_error("stream_claude", e)
            return False
        finally:
            if timer is not None:
                timer.cancel()
            if proc is not None:
                for stream in (proc.stdout, proc.stdin):
                    try:
                        if stream and not stream.closed:
                            stream.close()
                    except Exception:
                        pass
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass

    def _stream_flush(self, job_id=None):
        """Batch stream chunks on the UI thread to reduce redraw churn/crashes.
        A stale job_id (superseded by a newer request) is ignored so old deltas
        can't leak into the current popup."""
        if job_id is not None and not self._job_is_current(job_id):
            return
        if self._ss.flush_job:
            return

        def do_flush():
            self._ss.flush_job = None
            # A close/supersede between scheduling and running this flush must
            # not repaint: the job may have been invalidated (see
            # _user_close_popup), which _stream_update itself cannot detect.
            if job_id is not None and not self._job_is_current(job_id):
                return
            appended = []
            try:
                while True:
                    appended.append(self._ss.queue.get_nowait())
            except queue.Empty:
                pass
            if not appended:
                return
            self._ss.accum += "".join(appended)
            try:
                self._stream_update(self._ss.accum)
            except Exception:
                # If UI update races with close/destroy, ignore this frame.
                return

        self._ss.flush_job = self.root.after(50, do_flush)

    def _stream_update(self, current):
        """Called on the UI thread as streamed text grows. The first call swaps
        the loading hint for a result popup; later calls only update its text.
        Uses an explicit flag (set synchronously here on the UI thread) so
        queued callbacks can't each re-create the popup."""
        try:
            if not self._ss.popup_ready:
                self._ss.popup_ready = True
                self._stop_animation()
                anchor = None
                if self.popup:
                    try:
                        anchor = self._window_xy(self.popup)
                    except Exception:
                        anchor = None
                self._destroy_popup()
                # Build the popup off-screen but DON'T reveal it yet: the fitted
                # size of this first partial chunk differs from the stream-grow
                # size/position, so revealing here then re-sizing in
                # _set_popup_text would flash a wrong-sized frame (visible
                # flicker + position jump in dynamic layout). Instead let the
                # single _set_popup_text geometry move be the only on-screen frame.
                self.popup = self._make_popup(current, anchor=anchor,
                                              title=self._result_title(),
                                              reveal=False)
                # First stream frame: lock width and initialize grow-only height.
                self._set_popup_text(current, stream_grow=True)
                self._bring_to_front(self.popup)
            else:
                self._set_popup_text(current, stream_grow=True)
        except Exception:
            # UI can be destroyed while stream callbacks are in flight.
            return

    def _stream_finalize(self, final, job_id=None):
        if job_id is not None and not self._job_is_current(job_id):
            return
        self._cancel_stream_flush()
        self._ss.accum = final
        try:
            if self.popup and getattr(self.popup, "_text", None):
                # Final frame keeps stable stream geometry (no shrink/reposition jump).
                if getattr(self.popup._text, "_rich", False):
                    self.popup._text._rich_highlight = True
                self._set_popup_text(final, stream_grow=True, stream_final=True)
                self._maybe_add_explain_button(self.popup)
                self._maybe_add_result_actions_button(self.popup)
                self._remember_result(True, self._result_title(True), final)
                return

            anchor = None
            if self.popup:
                try:
                    anchor = self._window_xy(self.popup)
                except Exception:
                    anchor = None
            self._stop_animation()
            self._destroy_popup()
            self.popup = self._make_popup(final, anchor=anchor,
                                          title=self._result_title(),
                                          highlight=True)
            self._ss.popup_ready = True
            self._set_popup_text(final, stream_grow=True, stream_final=True)
            self._maybe_add_explain_button(self.popup)
            self._maybe_add_result_actions_button(self.popup)
            self._remember_result(True, self._result_title(True), final)
            log_perf("stream_finalize_popup_created", {"chars": len(final)})
        except Exception as e:
            log_error("stream_finalize", e)

    def _call_claude(self, text: str, system_prompt: Optional[str] = None) -> Tuple[bool, str]:
        if system_prompt is None:
            system_prompt = self._system_prompt_for(text)
        # Wrap the selection in tags so a bare word isn't mistaken for an
        # instruction (fixes short inputs returning "请提供要翻译的文本").
        payload = f"<text>\n{text}\n</text>"
        t0 = time.perf_counter()

        try:
            # Pass the text via stdin, NOT as a CLI argument: claude -p treats a
            # newline in an argument as end-of-input and would translate only the
            # first line/paragraph. stdin delivers the whole selection intact.
            proc = subprocess.run(
                [CLAUDE_CMD, "-p", "--safe-mode", "--model", self.cfg[CFG.MODEL],
                 "--system-prompt", system_prompt,
                 "--output-format", "json",
                 "--tools", "",   # no tools needed → smaller prompt, faster API
                 "--exclude-dynamic-system-prompt-sections",
                 "--no-session-persistence"],
                input=payload,
                capture_output=True, text=True, encoding="utf-8",
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if proc.stdout:
                out = proc.stdout.strip()
                # Prefer the JSON envelope's "result"; if the CLI returned plain
                # text instead (happens for some prompts), use it directly.
                try:
                    result = json.loads(out).get("result", "").strip()
                    if result:
                        log_perf("oneshot_cli_done", {
                            "chars": len(text),
                            "wall_ms": int((time.perf_counter() - t0) * 1000),
                        })
                        return True, result
                except json.JSONDecodeError:
                    if out:
                        log_perf("oneshot_cli_plain", {
                            "chars": len(text),
                            "wall_ms": int((time.perf_counter() - t0) * 1000),
                        })
                        return True, out
            log_perf("oneshot_cli_fail", {
                "chars": len(text),
                "wall_ms": int((time.perf_counter() - t0) * 1000),
            })
            return False, self._humanize_error(proc.stderr or "")
        except subprocess.TimeoutExpired:
            log_perf("oneshot_timeout", {"chars": len(text)})
            return False, i18n.get("error.translation_timeout")
        except Exception as e:
            log_perf("oneshot_error", {"chars": len(text), "err": str(e)[:160]})
            log_error("call_claude", e)
            return False, i18n.get("error.unexpected").format(error=e)

    def _humanize_error(self, stderr: Optional[str]) -> str:
        s = (stderr or "").strip()
        low = s.lower()
        if any(k in low for k in ("not logged in", "authentication",
                                  "unauthorized", "please run", "login")):
            return i18n.get("error.login_required")
        if "rate limit" in low or "429" in low:
            return i18n.get("error.rate_limited")
        if not s:
            return i18n.get("error.no_result")
        return i18n.get("error.translation_failed_with_reason").format(error=s[:200])

    def _show_result(self, ok, result, job_id=None):
        if job_id is not None and not self._job_is_current(job_id):
            return
        self._stop_animation()
        anchor = None
        title = self._result_title(ok)
        if self.popup:
            try:
                anchor = self._window_xy(self.popup)
            except Exception:
                anchor = None
        self._destroy_popup()
        self._remember_result(ok, title, result)
        self.popup = self._make_popup(result, anchor=anchor, is_error=not ok,
                                      title=title, highlight=ok)
        self._maybe_add_explain_button(self.popup)
        if ok:
            self._maybe_add_result_actions_button(self.popup)
        if ok and self.cfg.get(CFG.HISTORY_ENABLED, True) and (
                self._last_input or self._last_origin == "ocr"):
            add_history(self._last_input or "", result,
                        is_single_word(self._last_input),
                        self.cfg.get(CFG.HISTORY_LIMIT, 100),
                        is_code=(self._last_class == "code"),
                        kind=self._history_kind())

    def _maybe_add_explain_button(self, win):
        """For a mixed prose+code selection, add a one-shot '解释代码' button to
        the result popup's title bar. Clicking it explains the code portion in
        Chinese and appends that below the existing translation (which is left
        untouched)."""
        if self._last_class != "mixed":
            return
        if not win or getattr(win, "_has_explain_btn", False):
            return
        bar = getattr(win, "_btn_bar", None)
        mk = getattr(win, "_mk_bar_btn", None)
        if bar is None or mk is None:
            return
        try:
            btn = mk(i18n.get("result.explain"), self._explain_code_in_result)
            # Sit to the left of 复制 / ✕ (packed right-to-left).
            btn.pack(side="right", padx=(0, 4))
            win._explain_btn = btn
            win._has_explain_btn = True
        except Exception:
            pass

    def _maybe_add_result_actions_button(self, win):
        """Add a compact post-result actions menu for successful translations.

        The menu groups together alternate target-language retranslation,
        bilingual copy, and one-click rewrites (more concise / more formal /
        key-point summary) so the title bar stays compact even as we add more
        useful follow-up actions."""
        if not win or getattr(win, "_has_actions_btn", False):
            return
        if self._last_class == "code":
            return
        if self._last_input and is_single_word(self._last_input):
            return
        bar = getattr(win, "_btn_bar", None)
        mk = getattr(win, "_mk_bar_btn", None)
        if bar is None or mk is None:
            return
        try:
            t = self.theme
            menu = tk.Menu(
                win, tearoff=0,
                bg=t.get("popup_bg", t["bg"]), fg=t["fg"],
                activebackground=t["accent"], activeforeground="#ffffff",
                bd=0, relief="flat",
                font=("Microsoft YaHei UI", 9))
            if self._last_input:
                for code, (zh_name, en_name) in LANGUAGES.items():
                    if i18n.get_language() == "en_US":
                        lang_name = (i18n.get("result.language_chinese")
                                     if code == "zh" else en_name)
                    else:
                        lang_name = zh_name
                    menu.add_command(
                        label=i18n.get("result.retranslate_to").format(language=lang_name),
                        command=lambda c=code: self._retranslate_to(c))
                menu.add_separator()
                menu.add_command(label=i18n.get("result.copy_bilingual"), command=self._copy_bilingual_result)
                menu.add_separator()
            for mode in ("concise", "formal", "summary"):
                label_key = RESULT_ACTION_PROMPTS[mode][0]
                menu.add_command(
                    label=i18n.get(label_key),
                    command=lambda m=mode: self._transform_result(m))
            btn = mk(i18n.get("result.actions"), lambda: self._show_result_actions_menu(win))
            btn.pack(side="right", padx=(0, 4))
            win._actions_btn = btn
            win._actions_menu = menu
            win._has_actions_btn = True
        except Exception:
            pass

    def _show_result_actions_menu(self, win):
        menu = getattr(win, "_actions_menu", None)
        btn = getattr(win, "_actions_btn", None)
        if menu is None or btn is None:
            return
        try:
            x = btn.winfo_rootx()
            y = btn.winfo_rooty() + btn.winfo_height()
            menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _retranslate_to(self, code):
        src = self._last_input
        prompt = DIRECTION_MODES.get(f"to_{code}")
        if not src or not prompt:
            return
        win = self.popup
        btn = getattr(win, "_actions_btn", None) if win else None
        if btn is not None:
            try:
                btn.config(
                    text=i18n.get("result.processing"), state="disabled",
                    cursor="watch")
            except Exception:
                pass
        threading.Thread(
            target=self._do_retranslate,
            args=(src, prompt + SYSTEM_SUFFIX, code), daemon=True).start()

    def _do_retranslate(self, src, prompt, code):
        try:
            ok, result = self._call_claude(src, prompt)
        except Exception as e:
            ok, result = False, i18n.get("error.unexpected").format(error=e)
        self.root.after(0, lambda: self._apply_retranslation(ok, result, code))

    def _apply_retranslation(self, ok, result, code):
        win = self.popup
        if not win or not getattr(win, "_text", None):
            return
        btn = getattr(win, "_actions_btn", None)
        if ok:
            if getattr(win._text, "_rich", False):
                win._text._rich_highlight = True
            self._set_popup_text(result, resize=True)
            self._remember_result(True, self._result_title(True), result)
            if self.cfg.get(CFG.HISTORY_ENABLED, True) and (
                    self._last_input or self._last_origin == "ocr"):
                add_history(self._last_input or "", result, False,
                            self.cfg.get(CFG.HISTORY_LIMIT, 100),
                            is_code=False, kind=self._history_kind())
        if btn is not None:
            try:
                btn.config(text=i18n.get("result.actions"), state="normal",
                           cursor="hand2")
            except Exception:
                pass

    def _current_popup_text(self):
        if self.popup and getattr(self.popup, "_text", None):
            return self.popup._text.get("1.0", "end-1c")
        return ""

    def _copy_text_content(self, content):
        try:
            pyperclip.copy(content)
            return True
        except Exception as e:
            log_error("copy_text", e)
            return False

    def _flash_popup_button(self, attr, busy_text, reset_text, delay=1200):
        win = self.popup
        btn = getattr(win, attr, None) if win else None
        if btn is None:
            return
        try:
            btn.config(text=busy_text)
            win.after(delay, lambda: (
                self.popup and getattr(self.popup, attr, None)
                and getattr(self.popup, attr).config(text=reset_text)))
        except Exception:
            pass

    def _copy_bilingual_result(self):
        result = self._current_popup_text()
        if not result:
            return
        if self._last_input:
            content = (
                f"{i18n.get('result.source_label')}:\n{self._last_input}\n\n"
                f"{i18n.get('result.output_label')}:\n{result}"
            )
        else:
            content = result
        if self._copy_text_content(content):
            self._flash_popup_button("_actions_btn", i18n.get("result.copied"), i18n.get("result.actions"))

    def _transform_result(self, mode):
        item = RESULT_ACTION_PROMPTS.get(mode)
        current = self._current_popup_text()
        if not item or not current:
            return
        win = self.popup
        btn = getattr(win, "_actions_btn", None) if win else None
        if btn is not None:
            try:
                btn.config(text=i18n.get(item[0]) + "…", state="disabled",
                           cursor="watch")
            except Exception:
                pass
        threading.Thread(target=self._do_transform_result,
                         args=(mode, current), daemon=True).start()

    def _do_transform_result(self, mode, current):
        prompt = RESULT_ACTION_PROMPTS.get(mode, ("", ""))[1]
        try:
            ok, result = self._call_claude(current, prompt)
        except Exception as e:
            ok, result = False, i18n.get("error.unexpected").format(error=e)
        self.root.after(0, lambda: self._apply_result_transform(ok, result))

    def _apply_result_transform(self, ok, result):
        win = self.popup
        if not win or not getattr(win, "_text", None):
            return
        btn = getattr(win, "_actions_btn", None)
        if ok:
            if getattr(win._text, "_rich", False):
                win._text._rich_highlight = True
            self._set_popup_text(result, resize=True)
            self._remember_result(True, self._result_title(True), result)
            if self.cfg.get(CFG.HISTORY_ENABLED, True) and (
                    self._last_input or self._last_origin == "ocr"):
                add_history(self._last_input or "", result,
                            is_single_word(self._last_input),
                            self.cfg.get(CFG.HISTORY_LIMIT, 100),
                            is_code=(self._last_class == "code"),
                            kind=self._history_kind())
        if btn is not None:
            try:
                btn.config(text=i18n.get("result.actions"), state="normal", cursor="hand2")
            except Exception:
                pass

    def _explain_code_in_result(self):
        """Button handler: explain the code in the current result. Runs the
        model off the main thread so the UI stays responsive; this is a
        user-initiated action, not on the translation hot path, so it never
        affects translation speed."""
        win = self.popup
        if not win or not getattr(win, "_text", None):
            return
        btn = getattr(win, "_explain_btn", None)
        if btn is not None:
            try:
                btn.config(text=i18n.get("result.explaining"), state="disabled", cursor="watch")
            except Exception:
                pass
        base = win._text.get("1.0", "end-1c")
        src = self._last_input or base
        threading.Thread(target=self._do_explain_code, args=(src, base),
                         daemon=True).start()

    def _do_explain_code(self, src, base):
        try:
            ok, explanation = self._call_claude(src, CODE_EXPLAIN_APPEND_PROMPT)
        except Exception as e:
            ok, explanation = False, i18n.get("error.unexpected").format(error=e)
        self.root.after(
            0, lambda: self._append_code_explanation(ok, base, explanation))

    def _append_code_explanation(self, ok, base, explanation):
        win = self.popup
        if not win or not getattr(win, "_text", None):
            return
        btn = getattr(win, "_explain_btn", None)
        if not ok:
            if btn is not None:
                try:
                    btn.config(text=i18n.get("result.explain"), state="normal",
                               cursor="hand2")
                except Exception:
                    pass
            explanation = explanation or i18n.get("result.explain_failed")
            return
        divider = i18n.get("result.explain_divider")
        combined = base + divider + explanation
        # Final frame: highlight code blocks in the combined result.
        if getattr(win._text, "_rich", False):
            win._text._rich_highlight = True
        # _set_popup_text branches on layout: centred refits, dynamic resizes.
        self._set_popup_text(combined, resize=True)
        self._remember_result(True, self._result_title(True), combined)
        if btn is not None:
            try:
                btn.config(text=i18n.get("result.explained"), state="disabled",
                           cursor="arrow")
            except Exception:
                pass

    # ---------- Popup ----------
    def _make_loading_popup(self):
        """A compact, modern 'translating' card: an accent-coloured spinner
        next to a muted label. Borderless, rounded, no toolbar/scrollbar."""
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)

        popup_bg = self.theme.get("popup_bg", self.theme["bg"])
        popup_border = self.theme.get("popup_border", self.theme["border"])
        popup_hint = self.theme.get("popup_hint", self.theme["hint_fg"])
        accent = self.theme.get("accent", "#7aa2f7")

        # Rounded corners via a transparent colour key (genuinely transparent on
        # this environment, unlike SetWindowRgn cut-outs which render black).
        card = self._rounded_shell(win, LOADING_CORNER_RADIUS,
                                   popup_bg, popup_border)

        row = tk.Frame(card, bg=popup_bg, bd=0, highlightthickness=0)
        row.pack(padx=20, pady=14)

        spinner = tk.Label(
            row,
            text=LOADING_SPINNER[0],
            bg=popup_bg,
            fg=accent,
            font=("Segoe UI Symbol", 13),
        )
        spinner.pack(side="left", padx=(0, 9))
        win._spinner = spinner

        hint = tk.Label(
            row,
            text=(i18n.get("result.explaining") if self._last_class == "code"
                  else i18n.get("result.processing_screenshot")
                  if self._last_origin == "ocr"
                  else i18n.get("result.processing")),
            bg=popup_bg,
            fg=popup_hint,
            font=("Microsoft YaHei UI", 10),
        )
        hint.pack(side="left")
        win._hint_label = hint

        win.update_idletasks()
        radius = LOADING_CORNER_RADIUS
        w = card.winfo_reqwidth() + 2 * radius
        h = card.winfo_reqheight() + 2 * radius
        if self._is_centered_layout():
            # Centre the small hint where the fixed result card will appear, so
            # there is no positional jump when the result replaces it.
            bw, bh, bx, by = self._centered_box()
            x = bx + (bw - w) // 2
            y = by + (bh - h) // 2
        else:
            x = self.root.winfo_pointerx() + 12
            y = self.root.winfo_pointery() + 18
            x, y = self._clamp_to_monitor(x, y, w, h)
        self._reveal_rounded_window(win, w, h, x, y)
        # Clicking anywhere outside dismisses only the loading hint — but arm
        # that only after a short grace period. Revealing this popup activates
        # it (see _bring_to_front); meanwhile closing the quick-input window
        # hands the OS foreground to another window asynchronously, and that
        # handoff races our activation. The stray FocusOut it delivers would
        # otherwise dismiss the "translating…" hint the instant it appears, so
        # the user never sees it. Only a genuine, later focus loss should close
        # it.
        win._dismiss_armed = False
        win.bind("<FocusOut>", lambda e: self._on_loading_focus_out(win))

        def _arm_loading_dismiss():
            try:
                if tk.Toplevel.winfo_exists(win):
                    win._dismiss_armed = True
            except Exception:
                pass

        try:
            win.after(600, _arm_loading_dismiss)
        except Exception:
            win._dismiss_armed = True
        return win

    def _on_loading_focus_out(self, win):
        """Dismiss the loading hint when it loses focus — but ignore the focus
        churn that immediately follows revealing it (see _make_loading_popup),
        which would otherwise close the hint before the user ever sees it."""
        if getattr(win, "_dismiss_armed", False):
            self._dismiss_loading_popup()

    def _build_popup_header(self, win, frame, *, title, is_error, popup_bg,
                            popup_border, hint, accent, theme):
        # Header = title bar + hairline separator, measured as one unit so the
        # geometry math (which reads win._bar height) accounts for both.
        header = tk.Frame(frame, bg=popup_bg, bd=0, highlightthickness=0)
        header.pack(fill="x")
        win._bar = header

        bar = tk.Frame(header, bg=popup_bg, bd=0, highlightthickness=0)
        bar.pack(fill="x", padx=POPUP_BAR_PAD_X,
                 pady=(POPUP_BAR_PAD_TOP, POPUP_BAR_PAD_BOTTOM))

        title_color = theme["status_err"] if is_error else accent
        logo_img = self._logo_image(15)
        drag_targets = [bar]
        if logo_img:
            logo_lbl = tk.Label(bar, image=logo_img, bg=popup_bg, bd=0,
                                highlightthickness=0)
            logo_lbl.image = logo_img
            logo_lbl.pack(side="left", padx=(0, 6))
            drag_targets.append(logo_lbl)
        title_lbl = tk.Label(bar, text=title if logo_img else "●  " + title,
                             bg=popup_bg, fg=title_color,
                             font=("Microsoft YaHei UI", 9, "bold"))
        title_lbl.pack(side="left")
        drag_targets.append(title_lbl)

        def _mk_btn(txt, cmd, danger=False):
            active_bg = (theme["btn_close_active"] if danger
                         else theme["btn_active"])
            active_fg = "#ffffff" if danger else theme["fg"]
            return self._pill_button(
                bar, txt, cmd,
                bg=popup_bg, fg=hint,
                hover_bg=popup_bg, hover_fg=hint,
                active_bg=active_bg, active_fg=active_fg,
                font=("Microsoft YaHei UI", 9), padx=9, pady=1,
            )

        close_btn = _mk_btn("✕", self._user_close_popup, danger=True)
        close_btn.pack(side="right")

        # Pushpin toggle: keep this result above other windows only when the
        # user asks. Off by default (see _make_popup: win._pinned = False).
        pin_btn = tk.Button(
            bar, text="\uE718",
            command=lambda: self._toggle_popup_pin(win, pin_btn),
            bg=popup_bg, fg=hint, activebackground=popup_bg,
            activeforeground=accent, relief="flat", bd=0, highlightthickness=0,
            font=("Segoe MDL2 Assets", 10), cursor="hand2", padx=9, pady=1)
        pin_btn.pack(side="right", padx=(0, 4))
        pin_btn.bind("<Enter>", lambda e: pin_btn.config(fg=accent))
        pin_btn.bind(
            "<Leave>",
            lambda e: pin_btn.config(
                fg=(accent if getattr(win, "_pinned", False) else hint)))
        win._pin_btn = pin_btn
        self._make_tooltip(pin_btn, i18n.get("result.pin"))

        copy_btn = _mk_btn(i18n.get("result.copy"), self._copy_result)
        copy_btn.pack(side="right", padx=(0, 4))
        win._copy_btn = copy_btn
        win._btn_bar = bar
        win._mk_bar_btn = _mk_btn
        if is_error:
            retry_btn = _mk_btn(i18n.get("result.retranslate"), self._retry)
            retry_btn.pack(side="right", padx=(0, 4))

        tk.Frame(header, bg=popup_border, height=1,
                 bd=0, highlightthickness=0).pack(
                     fill="x", padx=POPUP_BAR_PAD_X)

        # Dragging the header (but not the buttons) moves the window.
        self._make_draggable(tuple(drag_targets), lambda: self.popup,
                             guard=lambda: self._resize_mode)

    def _toggle_popup_pin(self, win, pin_btn):
        """Toggle whether a result popup stays above other windows. Off by
        default; the header pushpin is the only way to turn it on. Failing to
        set the attribute must never crash the popup."""
        try:
            win._pinned = not getattr(win, "_pinned", False)
            win.attributes("-topmost", win._pinned)
        except Exception as e:
            log_error("toggle_pin", e)
            return
        t = self.theme
        accent = t.get("accent", "#7aa2f7")
        hint = t.get("popup_hint", t["hint_fg"])
        pin_btn.config(fg=(accent if win._pinned else hint))

    def _build_popup_body(self, win, frame, *, popup_bg, is_error, highlight):
        body = tk.Frame(frame, bg=popup_bg, bd=0, highlightthickness=0)
        body.pack(fill="both", expand=True,
                  padx=POPUP_BODY_PAD_X, pady=(0, POPUP_BODY_PAD_BOTTOM))

        scroll = ttk.Scrollbar(body, orient="vertical",
                               style="CC.Vertical.TScrollbar")
        text = tk.Text(
            body,
            bg=popup_bg,
            fg=self.theme["fg"],
            font=("Microsoft YaHei UI", self.cfg[CFG.FONT_SIZE]),
            wrap="word",
            relief="flat",
            bd=0,
            padx=POPUP_TEXT_PAD_X,
            pady=POPUP_TEXT_PAD_Y,
            insertwidth=0,
            selectbackground=self.theme["sel_bg"],
            highlightthickness=0,
            spacing1=3,
            spacing2=5,
            spacing3=3,
            width=1,
            height=1,
            yscrollcommand=scroll.set,
        )
        def _on_scrollbar(*args):
            # Dragging/clicking the scrollbar is a manual scroll too, so it must
            # also opt out of stream auto-pin-to-top.
            try:
                self._ss.user_scrolled = True
            except Exception:
                pass
            return text.yview(*args)
        scroll.config(command=_on_scrollbar)
        text.pack(side="left", fill="both", expand=True)
        win._text = text
        win._scroll = scroll
        win._scroll_body = body
        win._text_font = tkfont.Font(font=text.cget("font"))

        # Result popups render markdown-lite rich text; error popups stay plain
        # so a raw error string is never mis-parsed as markup.
        text._rich = not is_error
        if text._rich:
            self._configure_rich_tags(text)
            if highlight:
                # Final (non-streaming) frame: syntax-highlight code blocks.
                text._rich_highlight = True

    def _layout_popup_offscreen(self, win, message, anchor):
        """Measure and fill the popup while it stays parked off-screen, and
        return the final on-screen (w, h, x, y). The window is deliberately NOT
        moved on-screen here: the caller performs a single geometry move once
        the rounded card has been painted, so the reveal never flashes an
        unpainted colour-key (near-black) frame or a mid-measurement resize."""
        off = -4000
        if self._is_centered_layout():
            w, h, x, y = self._centered_box()
            win.geometry(f"{w}x{h}+{off}+{off}")
            self._fill_text(win._text, message)
            try:
                win._text.config(width=1, height=1)
            except Exception:
                pass
            win.update_idletasks()
            first, last = 0.0, 1.0
            try:
                first, last = win._text.yview()
            except Exception:
                pass
            if last < 1.0 - 1e-6 or first > 1e-6:
                win._scroll.pack(side="right", fill="y")
                win._text.bind("<MouseWheel>", self._on_mousewheel)
                win._scroll_body.bind("<MouseWheel>", self._on_mousewheel)
            else:
                win._scroll.pack_forget()
            return w, h, x, y
        w, h = self._size_popup(win, message)
        if anchor is not None:
            x, y = int(anchor[0]), int(anchor[1])
            try:
                px = int(self.root.winfo_pointerx())
                py = int(self.root.winfo_pointery())
                if x == 0 and y == 0 and (px > 24 or py > 24):
                    x, y = px + 12, py + 18
            except Exception:
                pass
        else:
            x = self.root.winfo_pointerx() + 12
            y = self.root.winfo_pointery() + 18
        x, y = self._clamp_to_monitor(x, y, w, h, ref=anchor)
        return w, h, x, y

    def _bind_popup_window_events(self, win):
        win.bind("<Motion>", self._popup_motion)
        win.bind("<ButtonPress-1>", self._popup_press)
        win.bind("<B1-Motion>", self._popup_drag)
        win.bind("<ButtonRelease-1>", self._popup_release)
        win.bind("<Escape>", lambda e: self._user_close_popup())

    def _make_popup(self, message, anchor=None, is_error=False, title=None,
                    highlight=False, reveal=True):
        t = self.theme
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        # Result windows no longer force always-on-top. The user opts in per
        # window via the pin button in the header (see _build_popup_header);
        # default is off so results stop covering everything else.
        win._pinned = False
        # Give the result popup a real taskbar button so it can always be found
        # again when it isn't on top. Ex-style / owner changes must be applied
        # while the window is hidden, before deiconify, to take effect.
        try:
            win.update_idletasks()
            # Keep the Tk owner link for transparent rounded cards: detaching it
            # breaks Tk's reported root coordinates/focus behavior on some hosts.
            win32util.set_taskbar_presence(
                int(win.winfo_id()), True, detach_owner=False)
        except Exception as e:
            log_error("popup_taskbar", e)

        popup_bg = t.get("popup_bg", t["bg"])
        popup_border = t.get("popup_border", t["border"])
        hint = t.get("popup_hint", t["hint_fg"])
        accent = t.get("accent", "#7aa2f7")

        # Rounded corners via a transparent colour key (genuinely transparent on
        # this environment, unlike SetWindowRgn cut-outs which render black).
        card = self._rounded_shell(win, POPUP_CORNER_RADIUS,
                                   popup_bg, popup_border)
        self._build_popup_header(
            win, card, title=(title or i18n.get("result.title")),
            is_error=is_error, popup_bg=popup_bg,
            popup_border=popup_border, hint=hint, accent=accent, theme=t)
        self._build_popup_body(
            win, card, popup_bg=popup_bg, is_error=is_error,
            highlight=highlight)

        # Measure, fill and paint the popup while it is still WITHDRAWN, then
        # reveal it with exactly the same two-step sequence that
        # _reveal_rounded_window uses (the loading / history / settings windows
        # use that helper and never flash).
        #
        # The flash bug: the old code did deiconify() first, THEN measured the
        # text size.  DWM composited the window at its default/small size the
        # moment it was deiconified; moving on-screen later briefly showed that
        # stale composite → visible "small window first, then large" jump.
        #
        # Fix: keep the window WITHDRAWN for the entire measurement phase.
        # _size_popup drives the text widget through a full update() cycle,
        # which works on unmapped windows.  Once we know (w, h) we follow
        # _reveal_rounded_window exactly:
        #   1. geometry(w×h off-screen) + deiconify  → DWM first sees window
        #      at the CORRECT size (no stale small thumbnail)
        #   2. update_idletasks + _round_redraw       → paint rounded card
        #   3. geometry(w×h on-screen)                → position-only move;
        #      DWM composite is already correct, no size change on reveal
        win.geometry("+{}+{}".format(-4000, -4000))
        win.update_idletasks()   # lay out widgets while still withdrawn
        w, h, x, y = self._layout_popup_offscreen(win, message, anchor)

        self._bind_popup_window_events(win)
        self._apply_taskbar_identity(win)
        # Cache the intended on-screen position so _window_xy can report it
        # even while the window is still parked off-screen (needed by the
        # deferred reveal path below, where the caller performs the reveal).
        self._remember_window_xy(win, x, y)

        # Step 1: deiconify at the correct final size, still off-screen.
        # DWM now has a composite at (w, h) — no default-size artefact.
        # Use win.update() (not just update_idletasks) so the canvas
        # <Configure> event fires and cv.winfo_width()/height() settle to
        # their real values before _round_redraw() tries to paint.
        # update_idletasks() only flushes idle tasks; the Configure event
        # that sizes the canvas travels through the normal event queue and
        # can be missed, leaving cv.winfo_width() == 1 so _round_redraw()
        # returns early — the window then reaches the screen as an
        # unpainted white rectangle.
        win.geometry(f"{w}x{h}+-4000+-4000")
        win.deiconify()
        win.update()
        win._round_redraw()

        if not reveal:
            # Deferred reveal: window is mapped off-screen at its correct
            # size; the caller (e.g. streaming first frame) performs the
            # sole on-screen geometry move so no intermediate size is shown.
            return win

        # Step 2: position-only move on-screen — size does not change, so
        # DWM's existing composite is the correct final frame.
        win.geometry(f"{w}x{h}+{x}+{y}")
        self._remember_window_xy(win, x, y)
        win.update_idletasks()
        win._round_redraw()
        self._bring_to_front(win)
        return win

    def _size_popup(self, win, message):
        """Set the message and return the popup's exact (width, height) in px,
        measured from tkinter's own layout of the real text — not estimated.

        The Text width (in char columns) is the longest logical line capped at
        a max; tkinter then reports the precise pixel reqwidth/reqheight, and
        we read the true wrapped line count for the height."""
        text = win._text
        # Content sits inside the rounded colour-key card, inset by the corner
        # radius on every side, so the window must be that much larger than the
        # measured text. (Cancels with the body pad exactly as the old shell-pad
        # inset did, so effective wrapping width is unchanged.)
        shell_pad = POPUP_CORNER_RADIUS

        rect = get_monitor_rect()
        mon_w = (rect[2] - rect[0]) if rect else self.root.winfo_screenwidth()
        # Column cap: a comfortable reading width (~48 cols), but never wider
        # than the monitor allows. Longer text wraps into a readable block
        # instead of one very wide line.
        avg_char_px = max(win._text_font.measure("0"), 7)
        screen_cap = max(24, int((mon_w * 0.9) / avg_char_px))
        max_cols = min(48, screen_cap)

        # Longest logical line in display columns (CJK counts as 2).
        def line_cols(s):
            return sum(2 if ord(c) > 0x2E7F else 1 for c in s)
        longest_cols = max((line_cols(ln) for ln in message.split("\n")),
                           default=1)
        # +2 cols of slack: Text's char-based width vs real CJK glyph width is
        # inexact, and a tight fit makes a line wrap spuriously (a 1-line
        # string measured as 3), leaving the window too tall.
        cols = min(max(longest_cols + 2, 8), max_cols)

        self._fill_text(text, message)
        text.config(width=cols, height=1)
        text.update_idletasks()
        # Pre-stretch the popup to the Text's requested width BEFORE counting
        # wrapped lines. When reusing a popup (streaming), the window is still
        # at its old narrow size, which squeezes the Text and miscounts a
        # 1-line string as several — leaving the final window too tall.
        req_w = text.winfo_reqwidth() + (shell_pad * 2)
        win.geometry(f"{req_w}x1000")
        text.update_idletasks()
        text.update()
        try:
            true_lines = int(text.count("1.0", "end", "displaylines")[0])
        except Exception:
            true_lines = message.count("\n") + 1
        true_lines = max(true_lines, 1)
        max_lines = 22
        display_lines = min(true_lines, max_lines)
        text.config(height=display_lines)

        # Show the scrollbar only when the content is taller than the popup.
        if true_lines > max_lines:
            win._scroll.pack(side="right", fill="y")
            win._text.bind("<MouseWheel>", self._on_mousewheel)
            win._scroll_body.bind("<MouseWheel>", self._on_mousewheel)
        else:
            win._scroll.pack_forget()
        text.update()

        w = text.winfo_reqwidth() + (shell_pad * 2)
        if true_lines > max_lines:
            w += win._scroll.winfo_reqwidth()
        bar_h = win._bar.winfo_reqheight() if getattr(win, "_bar", None) else 26
        h = text.winfo_reqheight() + bar_h + (shell_pad * 2)
        h = max(int(h), MIN_POPUP_HEIGHT)
        return int(w), int(h)

    def _size_popup_stream_grow(self, win, message):
        """Streaming mode: keep width fixed, only allow height to grow."""
        text = win._text
        # Match _size_popup: colour-key card inset is the corner radius.
        shell_pad = POPUP_CORNER_RADIUS

        rect = get_monitor_rect()
        if rect:
            left, top, right, bottom = rect
        else:
            left, top = 0, 0
            right = self.root.winfo_screenwidth()
            bottom = self.root.winfo_screenheight()
        mon_w = right - left
        mon_h = bottom - top

        avg_char_px = max(win._text_font.measure("0"), 7)
        screen_cap = max(24, int((mon_w * 0.9) / avg_char_px))
        # Keep stream width stable and reasonably wide from the first frame.
        preferred_cols = min(max(36, int(screen_cap * 0.7)), 48)
        cols = self._ss.cols or preferred_cols
        self._ss.cols = cols

        self._fill_text(text, message)
        text.config(width=cols, height=1)
        text.update_idletasks()
        text.update()
        try:
            true_lines = int(text.count("1.0", "end", "displaylines")[0])
        except Exception:
            true_lines = message.count("\n") + 1
        true_lines = max(true_lines, 1)

        bar_h = win._bar.winfo_reqheight() if getattr(win, "_bar", None) else 26
        if self._ss.origin_y is not None:
            # Once the stream anchor is fixed, height may only grow downward
            # until the bottom edge is reached; never move the window upward.
            max_popup_h = max(1, int(bottom - self._ss.origin_y - 8))
        else:
            max_popup_h = max(MIN_POPUP_HEIGHT, int(mon_h - 20))
        available_text_h = max(24, max_popup_h - bar_h - (shell_pad * 2))
        line_px = max(win._text_font.metrics("linespace") + 6, 14)
        max_lines_by_height = max(4, int(available_text_h / line_px))

        display_lines = min(true_lines, max_lines_by_height)
        text.config(height=display_lines)

        if true_lines > max_lines_by_height:
            win._scroll.pack(side="right", fill="y")
            win._text.bind("<MouseWheel>", self._on_mousewheel)
            win._scroll_body.bind("<MouseWheel>", self._on_mousewheel)
        else:
            win._scroll.pack_forget()
        text.update()

        w = text.winfo_reqwidth() + (shell_pad * 2)
        if true_lines > max_lines_by_height:
            w += win._scroll.winfo_reqwidth()
        h = text.winfo_reqheight() + bar_h + (shell_pad * 2)

        if not self._ss.fixed_w:
            self._ss.fixed_w = int(w)
        if self._ss.max_h:
            h = max(int(h), self._ss.max_h)

        h = min(int(h), max_popup_h)
        self._ss.max_h = int(h)

        if self._ss.monitor_rect is None:
            cx, cy = self._window_xy(win)
            rect0 = get_monitor_rect((cx, cy))
            self._ss.monitor_rect = rect0 if rect0 else (left, top, right, bottom)

        return int(self._ss.fixed_w), int(h)

    def _remember_window_xy(self, win, x, y):
        """Cache the last geometry position we explicitly applied to `win`.
        Needed because some Win32 style combos can make Tk briefly report (0,0)
        even though the real window is elsewhere."""
        try:
            win._screen_xy = (int(x), int(y))
        except Exception:
            pass

    def _window_xy(self, win):
        """Best-effort screen position for a Toplevel.
        Prefer Tk's x/y, but fall back to cached geometry when Tk reports stale
        origin coordinates for owner-style edge cases."""
        cached = getattr(win, "_screen_xy", None)
        try:
            x = int(win.winfo_x())
            y = int(win.winfo_y())
        except Exception:
            if isinstance(cached, tuple) and len(cached) == 2:
                return int(cached[0]), int(cached[1])
            return 0, 0
        if isinstance(cached, tuple) and len(cached) == 2:
            cx, cy = int(cached[0]), int(cached[1])
            if (x, y) == (0, 0) and (cx, cy) != (0, 0):
                return cx, cy
            # While a popup is parked far off-screen (deferred reveal), Tk
            # reports the park coordinates. Prefer the intended on-screen
            # position we cached so stream anchoring/monitor detection is right.
            if x <= -3000 and cx > -3000:
                return cx, cy
        return x, y

    def _on_mousewheel(self, event):
        if self.popup and getattr(self.popup, "_text", None):
            # A manual scroll opts the user out of stream auto-pin-to-top so a
            # later frame (or the final frame) won't yank the view back up.
            try:
                self._ss.user_scrolled = True
            except Exception:
                pass
            self.popup._text.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _bring_to_front(self, win):
        """Raise a borderless window above other apps AND make it the true
        foreground/active window, so it behaves like a normal window.

        Two failure modes this addresses:
        * A plain lift() only reorders within our own app's z-group, so a window
          sitting behind another application would appear 'stuck' and re-pressing
          the hotkey would feel like the feature failed to launch.
        * Merely *raising* a window (e.g. a topmost pulse) without *activating*
          it leaves Windows in a 'top-but-not-active' state: the window floats on
          top, yet clicking another app won't send it behind until this window is
          clicked once. That is the 'still force-topmost' feeling users hit.

        So we do a real foreground activation on the window's ACTUAL top-level
        HWND (Tk's winfo_id() is only the inner frame — activation on it no-ops).
        Only if the OS refuses activation (rare foreground-lock races) do we fall
        back to a brief, self-releasing topmost pulse purely for visibility."""
        try:
            win.deiconify()
        except Exception:
            pass
        try:
            win.lift()
        except Exception:
            pass
        activated = False
        try:
            top_hwnd = win32util.get_toplevel_hwnd(int(win.winfo_id()))
            activated = win32util.activate_foreground(top_hwnd)
        except Exception:
            activated = False
        try:
            win.focus_set()
        except Exception:
            try:
                win.focus_force()
            except Exception:
                pass
        if activated or getattr(win, "_pinned", False):
            # Real activation succeeded (window is active and can be sent behind
            # by clicking elsewhere), or the window is pinned and legitimately
            # stays topmost — either way, no fallback pulse needed.
            return
        try:
            win.attributes("-topmost", True)
        except Exception:
            return

        def _release_topmost():
            try:
                if tk.Toplevel.winfo_exists(win) and not getattr(
                        win, "_pinned", False):
                    win.attributes("-topmost", False)
            except Exception:
                pass

        try:
            win.after(90, _release_topmost)
        except Exception:
            _release_topmost()

    def _apply_taskbar_identity(self, win, title=None):
        """Give a borderless Toplevel a proper taskbar / Alt-Tab identity so its
        hover preview shows the app name and icon rather than Tk's default
        'tk' title and feather icon."""
        try:
            win.title(title or APP_NAME)
        except Exception:
            pass
        try:
            if os.path.exists(ICON_PATH):
                win.iconbitmap(ICON_PATH)
        except Exception:
            pass

    def _setup_rounded_window(self, win, radius):
        """Attach reliable rounded corners via window-proc subclassing. Windows
        re-applies the region on every real resize (WM_WINDOWPOSCHANGED), so the
        corners can no longer flicker off or get stuck square after a shrink."""
        win._corner_radius = max(0, int(radius))
        attach_rounded_corners(win, win._corner_radius)

    def _rounded_shell(self, win, radius, card_bg, border):
        """Turn a borderless Toplevel into a rounded card using a transparent
        colour key, so its corners are genuinely transparent (verified to work
        in this environment where SetWindowRgn cut-outs render opaque). Returns
        the content Frame to fill; the window reveals via deiconify (colour-key
        transparency is incompatible with -alpha, so don't mix them)."""
        win.configure(bg=ROUND_KEY_COLOR)
        try:
            win.wm_attributes("-transparentcolor", ROUND_KEY_COLOR)
        except Exception:
            pass
        cv = tk.Canvas(win, bg=ROUND_KEY_COLOR, highlightthickness=0, bd=0,
                       takefocus=0)
        cv.pack(fill="both", expand=True)
        card = tk.Frame(cv, bg=card_bg, bd=0, highlightthickness=0)
        item = cv.create_window(radius, radius, anchor="nw", window=card)

        def _redraw(event=None):
            w = cv.winfo_width()
            h = cv.winfo_height()
            if w <= 2 or h <= 2:
                return
            cv.delete("cc_shell")
            _draw_round_rect(cv, 0, 0, w, h, radius,
                             fill=border, outline=border, tags="cc_shell")
            _draw_round_rect(cv, 1, 1, w - 1, h - 1, radius,
                             fill=card_bg, outline=card_bg, tags="cc_shell")
            cv.tag_lower("cc_shell")
            cv.coords(item, radius, radius)
            cv.itemconfigure(item, width=w - 2 * radius, height=h - 2 * radius)

        cv.bind("<Configure>", _redraw)
        win._round_canvas = cv
        win._round_redraw = _redraw
        return card

    def _apply_window_rounding(self, win):
        """Force an immediate corner refresh at the window's current real size.
        Colour-key windows (the common case now) just repaint their rounded
        canvas; the few remaining region-clipped windows re-apply SetWindowRgn."""
        redraw = getattr(win, "_round_redraw", None)
        if redraw is not None:
            try:
                redraw()
            except Exception:
                pass
            return
        radius = int(getattr(win, "_corner_radius", POPUP_CORNER_RADIUS))
        try:
            _round_apply_region(int(win.winfo_id()), radius)
        except Exception:
            pass

    def _clamp_to_monitor(self, x, y, w, h, ref=None):
        """Keep a w×h window fully inside a monitor. The monitor is chosen by
        `ref` (a screen point); defaults to the current cursor position."""
        rect = get_monitor_rect(ref)
        if rect:
            left, top, right, bottom = rect
        else:
            left, top = 0, 0
            right = self.root.winfo_screenwidth()
            bottom = self.root.winfo_screenheight()
        x = max(left + 4, min(x, right - w - 4))
        y = max(top + 4, min(y, bottom - h - 4))
        return x, y

    def _is_centered_layout(self):
        return self.cfg.get(CFG.POPUP_LAYOUT, "centered") == "centered"

    def _scaled_centered_box(self, logical_w, logical_h, min_w=280, min_h=150):
        """Scale a logical window size by DPI and centre it on the active monitor."""
        scale = 1.0
        try:
            scale = self.root.winfo_fpixels("1i") / 96.0
        except Exception:
            pass
        w = int(logical_w * scale)
        h = int(logical_h * scale)
        rect = get_monitor_rect()
        if rect:
            left, top, right, bottom = rect
        else:
            left, top = 0, 0
            right = self.root.winfo_screenwidth()
            bottom = self.root.winfo_screenheight()
        mon_w, mon_h = right - left, bottom - top
        w = max(min_w, min(w, mon_w - 40))
        h = max(min_h, min(h, mon_h - 40))
        x = left + (mon_w - w) // 2
        y = top + (mon_h - h) // 2
        return w, h, x, y

    def _centered_box(self):
        """Fixed popup geometry (w, h, x, y) in physical px, centred on the
        active monitor. Size is a DPI-scaled logical box (~2x the dynamic
        popup at a 4:3 ratio), clamped to fit the monitor."""
        return self._scaled_centered_box(CENTERED_POPUP_W, CENTERED_POPUP_H)

    def _history_box(self):
        """A roomier centred box for the feature-rich history window."""
        return self._scaled_centered_box(HISTORY_WINDOW_W, HISTORY_WINDOW_H)

    def _stream_fill_text(self, text_widget, message, rich):
        """Update the streamed result Text with the least work possible.

        Streamed output only ever grows, so on a normal frame we insert just the
        new tail at the end. Inserting at the end never moves the scroll view and
        never repaints the rest of the document, which is what killed the
        per-frame flicker and the jump-to-top: the old path deleted and reinserted
        the ENTIRE text ~20x/second (every 50ms flush), resetting the view to the
        top each time. During streaming the tail is inserted as plain text; the
        final frame (rich=True) does a single rich rebuild for markdown/inline
        formatting. A rare non-append change (message isn't a superset of what's
        shown) falls back to a full rebuild."""
        prev = self._ss.rendered
        if not rich and prev and message.startswith(prev):
            if len(message) == len(prev):
                return  # nothing new to show
            try:
                text_widget.config(state="normal")
                text_widget.insert("end", message[len(prev):])
                text_widget.config(state="disabled")
                self._ss.rendered = message
                return
            except Exception:
                pass  # fall through to a full rebuild
        if rich:
            self._fill_text(text_widget, message)
        else:
            try:
                text_widget.config(state="normal")
                text_widget.delete("1.0", "end")
                text_widget.insert("1.0", message)
                text_widget.config(state="disabled")
            except Exception:
                self._fill_text(text_widget, message)
        self._ss.rendered = message

    def _fit_centered(self, win, message, scroll_end=False, scroll_top=False,
                      streaming=False, full_rebuild=False):
        """Fill a fixed-size centred popup with text: the window keeps its fixed
        geometry, the Text stretches to fill it, and a scrollbar appears only
        when the content overflows. Used for both result and streaming frames.

        For streaming frames (streaming=True) the fixed geometry, rounded region
        and Text char-sizing are applied only on the FIRST frame; later frames
        append just the new text tail (see _stream_fill_text) and toggle the
        scrollbar. Re-running win.geometry(), the rounded-canvas redraw and a
        whole-document delete+reinsert on every streamed frame made the card look
        like it was constantly refreshing and pulled the view back to the top."""
        text = win._text
        first_setup = not (streaming and getattr(self._ss, "centered_ready", False))
        # Predict whether this frame rebuilds the whole Text (first frame, the
        # final rich rebuild, or a rare non-append change). Only a full rebuild
        # snaps the view to the top, so only then must we capture/restore the
        # user's reading position; a plain append leaves the view untouched.
        will_rebuild = True
        prev_top = None
        if streaming:
            prev = self._ss.rendered
            will_rebuild = (first_setup or full_rebuild
                            or not (prev and message.startswith(prev)))
            if will_rebuild and not first_setup and getattr(
                    self._ss, "user_scrolled", False):
                try:
                    prev_top = text.index("@0,0")
                except Exception:
                    prev_top = None
        if first_setup:
            w, h, x, y = self._centered_box()
            win.geometry(f"{w}x{h}+{x}+{y}")
            self._remember_window_xy(win, x, y)
        if streaming:
            self._stream_fill_text(text, message, rich=full_rebuild)
        else:
            self._fill_text(text, message)
        if first_setup:
            # width/height in chars = 1 so pack(fill=both, expand) lets the Text
            # stretch to the window's fixed pixel size instead of its content size.
            try:
                text.config(width=1, height=1)
            except Exception:
                pass
        win.update_idletasks()
        if scroll_end:
            try:
                text.see("end-1c")
            except Exception:
                pass
        elif prev_top is not None:
            # Restore the user's reading position after a full rebuild.
            try:
                text.yview(prev_top)
            except Exception:
                pass
        elif scroll_top:
            # Pin to the top only on the first frame. Streamed text is appended
            # below the fold, so once the view starts at the top it stays there
            # on its own. Never fight a user who has scrolled themselves.
            if first_setup and not getattr(self._ss, "user_scrolled", False):
                try:
                    text.yview_moveto(0.0)
                except Exception:
                    pass
        first, last = 0.0, 1.0
        try:
            first, last = text.yview()
        except Exception:
            pass
        overflow = last < 1.0 - 1e-6 or first > 1e-6
        try:
            mapped = bool(win._scroll.winfo_ismapped())
        except Exception:
            mapped = False
        if overflow and not mapped:
            win._scroll.pack(side="right", fill="y")
            win._text.bind("<MouseWheel>", self._on_mousewheel)
            win._scroll_body.bind("<MouseWheel>", self._on_mousewheel)
        elif not overflow and mapped:
            win._scroll.pack_forget()
        if first_setup:
            self._apply_window_rounding(win)
            if streaming:
                self._ss.centered_ready = True

    def _resize_hit(self, win, x, y):
        w, h = win.winfo_width(), win.winfo_height()
        # Overrideredirect windows can report slightly off local coordinates,
        # especially near the bottom edge; widen and normalize hit bands.
        hit = RESIZE_HIT
        edge_x = "w" if x <= hit else ("e" if x >= w - hit else "")
        edge_y = "n" if y <= hit else ("s" if y >= h - hit else "")
        if not edge_y and y >= h - (hit * 2):
            edge_y = "s"
        return edge_y + edge_x

    def _resize_cursor(self, mode):
        return {
            "n": "sb_v_double_arrow",
            "s": "sb_v_double_arrow",
            "e": "sb_h_double_arrow",
            "w": "sb_h_double_arrow",
            "nw": "size_nw_se",
            "se": "size_nw_se",
            "ne": "size_ne_sw",
            "sw": "size_ne_sw",
        }.get(mode, "arrow")

    def _popup_motion(self, event):
        win = self.popup
        if not win:
            return
        if self._resize_mode:
            return
        wx, wy = self._window_xy(win)
        lx = event.x_root - wx
        ly = event.y_root - wy
        mode = self._resize_hit(win, lx, ly)
        try:
            win.configure(cursor=self._resize_cursor(mode))
        except Exception:
            pass

    def _popup_press(self, event):
        win = self.popup
        if not win:
            return
        wx, wy = self._window_xy(win)
        lx = event.x_root - wx
        ly = event.y_root - wy
        mode = self._resize_hit(win, lx, ly)
        if not mode:
            self._resize_mode = None
            self._resize_start = None
            return
        ox, oy = self._window_xy(win)
        self._resize_mode = mode
        self._resize_start = (
            event.x_root, event.y_root,
            ox, oy,
            win.winfo_width(), win.winfo_height(),
        )

    def _popup_drag(self, event):
        win = self.popup
        if not (win and self._resize_mode and self._resize_start):
            return
        sx, sy, ox, oy, ow, oh = self._resize_start
        dx, dy = event.x_root - sx, event.y_root - sy
        x, y, w, h = ox, oy, ow, oh

        mode = self._resize_mode
        if "e" in mode:
            w = ow + dx
        if "s" in mode:
            h = oh + dy
        if "w" in mode:
            x = ox + dx
            w = ow - dx
        if "n" in mode:
            y = oy + dy
            h = oh - dy

        w = max(MIN_RESIZE_WIDTH, int(w))
        h = max(MIN_RESIZE_HEIGHT, int(h))

        rect = get_monitor_rect((ox, oy))
        if rect:
            left, top, right, bottom = rect
        else:
            left, top = 0, 0
            right = self.root.winfo_screenwidth()
            bottom = self.root.winfo_screenheight()

        if x < left + 4:
            if "w" in mode:
                w -= (left + 4 - x)
            x = left + 4
        if y < top + 4:
            if "n" in mode:
                h -= (top + 4 - y)
            y = top + 4

        if x + w > right - 4:
            if "e" in mode:
                w = right - 4 - x
            else:
                x = max(left + 4, right - 4 - w)
        if y + h > bottom - 4:
            if "s" in mode:
                h = bottom - 4 - y
            else:
                y = max(top + 4, bottom - 4 - h)

        w = max(MIN_RESIZE_WIDTH, int(w))
        h = max(MIN_RESIZE_HEIGHT, int(h))
        win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
        self._remember_window_xy(win, int(x), int(y))
        # Rounded region is refreshed automatically by the window-proc subclass
        # on WM_WINDOWPOSCHANGED, so no manual (potentially stale) call here.

    def _popup_release(self, _event):
        self._resize_mode = None
        self._resize_start = None

    def _make_draggable(self, widgets, win_getter, guard=None):
        """Bind `widgets` so dragging them moves a borderless window.

        `win_getter` is the target window or a callable returning it (deferred so
        the popup can be resolved at drag time). `guard`, if given, is a callable
        that aborts the drag while truthy (e.g. during a resize).
        """
        off = {"x": 0, "y": 0}

        def _win():
            return win_getter() if callable(win_getter) else win_getter

        def start(e):
            if guard and guard():
                return
            off["x"], off["y"] = e.x, e.y

        def move(e):
            if guard and guard():
                return
            w = _win()
            if w:
                wx, wy = self._window_xy(w)
                nx = int(wx + e.x - off["x"])
                ny = int(wy + e.y - off["y"])
                w.geometry(f"+{nx}+{ny}")
                self._remember_window_xy(w, nx, ny)

        for _w in widgets:
            _w.bind("<Button-1>", start)
            _w.bind("<B1-Motion>", move)

    def _pill_button(self, parent, text_, cmd, *, bg, fg, hover_bg=None,
                     hover_fg=None, active_bg=None, active_fg=None,
                     font=("Microsoft YaHei UI", 10), padx=18, pady=6):
        """Create a flat pill-like button with consistent hover/active behavior."""
        hb = bg if hover_bg is None else hover_bg
        hf = fg if hover_fg is None else hover_fg
        ab = hb if active_bg is None else active_bg
        af = hf if active_fg is None else active_fg
        b = tk.Button(
            parent, text=text_, command=cmd, bg=bg, fg=fg,
            activebackground=ab, activeforeground=af,
            relief="flat", bd=0, highlightthickness=0,
            font=font, cursor="hand2", padx=padx, pady=pady,
        )
        b.bind("<Enter>", lambda e: b.config(bg=hb, fg=hf))
        b.bind("<Leave>", lambda e: b.config(bg=bg, fg=fg))
        return b

    def _make_tooltip(self, widget, text, delay_ms=400):
        """Attach a simple tooltip to a widget. Shows on enter after a delay,
        hides on leave. The tooltip is topmost so it never hides behind a
        borderless -topmost dialog (e.g. the settings window)."""
        tooltip_var = {"job": None, "tooltip": None}

        def show_tooltip(e):
            def do_show():
                try:
                    tt = tk.Toplevel(self.root)
                    tt.wm_overrideredirect(True)
                    tt.attributes("-topmost", True)
                    lbl = tk.Label(tt, text=text, bg="#2b2b2b", fg="#ffffff",
                                   font=("Microsoft YaHei UI", 9),
                                   wraplength=240, justify="left",
                                   padx=10, pady=6, relief="flat", bd=0)
                    lbl.pack()
                    tt.update_idletasks()
                    # Prefer to the right of the icon; if that would run off the
                    # right screen edge, flip to the left side instead.
                    tw = tt.winfo_width()
                    th = tt.winfo_height()
                    sw = widget.winfo_screenwidth()
                    x = widget.winfo_rootx() + widget.winfo_width() + 8
                    if x + tw > sw - 8:
                        x = widget.winfo_rootx() - tw - 8
                    y = widget.winfo_rooty() + (widget.winfo_height() - th) // 2
                    if y < 8:
                        y = 8
                    tt.wm_geometry(f"+{x}+{y}")
                    tt.lift()
                    tooltip_var["tooltip"] = tt
                except Exception:
                    pass

            if tooltip_var["job"]:
                try:
                    self.root.after_cancel(tooltip_var["job"])
                except Exception:
                    pass
            tooltip_var["job"] = self.root.after(delay_ms, do_show)

        def hide_tooltip(e):
            if tooltip_var["job"]:
                try:
                    self.root.after_cancel(tooltip_var["job"])
                except Exception:
                    pass
                tooltip_var["job"] = None
            if tooltip_var["tooltip"]:
                try:
                    tooltip_var["tooltip"].destroy()
                except Exception:
                    pass
                tooltip_var["tooltip"] = None

        widget.bind("<Enter>", show_tooltip)
        widget.bind("<Leave>", hide_tooltip)

    def _mono_family(self):
        """Resolve a monospace family once (VSCode-ish preference order)."""
        cached = getattr(self, "_mono_family_cache", None)
        if cached is not None:
            return cached
        try:
            available = set(tkfont.families(self.root))
        except Exception:
            available = set()
        fam = "Courier New"
        for cand in ("Cascadia Code", "Cascadia Mono", "Consolas",
                     "JetBrains Mono", "Courier New"):
            if cand in available:
                fam = cand
                break
        self._mono_family_cache = fam
        return fam

    def _configure_rich_tags(self, text_widget):
        """Set up the tk.Text tags used by the markdown-lite renderer, coloured
        from the active theme. Heading fonts are only mildly larger so the
        dynamic-layout height math (which reads real reqheight) stays sane."""
        t = self.theme
        base = int(self.cfg[CFG.FONT_SIZE])
        ui = "Microsoft YaHei UI"
        mono = self._mono_family()
        text_widget.tag_configure(
            "rich_code", font=(mono, base), foreground=t["rich_code_fg"],
            background=t["rich_code_bg"])
        text_widget.tag_configure(
            "rich_codeblock", font=(mono, base), foreground=t["rich_code_fg"],
            background=t["rich_code_bg"], lmargin1=10, lmargin2=10)
        text_widget.tag_configure(
            "rich_bold", font=(ui, base, "bold"), foreground=t["rich_bold_fg"])
        text_widget.tag_configure("rich_italic", font=(ui, base, "italic"))
        text_widget.tag_configure(
            "rich_url", foreground=t["rich_url_fg"], underline=True)
        text_widget.tag_configure(
            "rich_bullet", foreground=t["rich_bullet_fg"], font=(ui, base, "bold"))
        text_widget.tag_configure(
            "rich_h1", font=(ui, base + 2, "bold"),
            foreground=t["rich_heading_fg"], spacing1=4, spacing3=2)
        text_widget.tag_configure(
            "rich_h2", font=(ui, base + 1, "bold"),
            foreground=t["rich_heading_fg"], spacing1=3, spacing3=2)
        text_widget.tag_configure(
            "rich_h3", font=(ui, base, "bold"),
            foreground=t["rich_heading_fg"], spacing1=2, spacing3=1)
        # Pygments token tags: mono font on the code-block background so a
        # highlighted block keeps the same card look, just multi-coloured.
        for name in ("keyword", "string", "comment", "number",
                     "func", "operator", "ident"):
            text_widget.tag_configure(
                "rich_tok_" + name, font=(mono, base),
                foreground=t.get("rich_tok_" + name, t["rich_code_fg"]),
                background=t["rich_code_bg"], lmargin1=10, lmargin2=10)

    def _fill_text(self, text_widget, message):
        text_widget.config(state="normal")
        text_widget.delete("1.0", "end")
        if getattr(text_widget, "_rich", False):
            hl = getattr(text_widget, "_rich_highlight", False)
            for chunk, tag in iter_rich_segments(message, highlight=hl):
                if tag:
                    text_widget.insert("end", chunk, tag)
                else:
                    text_widget.insert("end", chunk)
        else:
            text_widget.insert("1.0", message)
        text_widget.config(state="disabled")

    def _copy_result(self):
        if self.popup and getattr(self.popup, "_text", None):
            content = self.popup._text.get("1.0", "end-1c")
            if self._copy_text_content(content):
                self.popup._copy_btn.config(text=i18n.get("result.copied"))
                self.popup.after(
                    1200,
                    lambda: self.popup and self.popup._copy_btn.config(text=i18n.get("result.copy")))
            else:
                self.popup._copy_btn.config(text=i18n.get("result.copy_failed"))
                self.popup.after(
                    1200,
                    lambda: self.popup and self.popup._copy_btn.config(text=i18n.get("result.copy")))

    def _set_popup_text(self, message, resize=True, stream_grow=False,
                        stream_final=False):
        win = self.popup
        if not (win and getattr(win, "_text", None)):
            return
        if self._is_centered_layout():
            # Fixed centred card: never resize or reposition. Just refill the
            # text; overflow scrolls instead of growing the window. While
            # streaming, append the new tail so the reader isn't yanked around;
            # the final frame does one rich rebuild for formatting.
            self._fit_centered(win, message, scroll_top=stream_grow,
                               streaming=stream_grow, full_rebuild=stream_final)
            return
        if stream_grow:
            # The rebuild inside _size_popup_stream_grow (delete + reinsert)
            # snaps the view to the top. Preserve the user's reading position
            # if they scrolled down to follow along.
            prev_top = None
            if getattr(self._ss, "user_scrolled", False):
                try:
                    prev_top = win._text.index("@0,0")
                except Exception:
                    prev_top = None
            w, h = self._size_popup_stream_grow(win, message)

            if self._ss.monitor_rect is None:
                cx0, cy0 = self._window_xy(win)
                rect0 = get_monitor_rect((cx0, cy0))
                self._ss.monitor_rect = rect0 if rect0 else (
                    0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())

            left, top, right, bottom = self._ss.monitor_rect
            min_top = top + 12
            max_y = max(min_top, bottom - h - 8)

            if self._ss.origin_x is None or self._ss.origin_y is None:
                cx, cy = self._window_xy(win)
                if (cx, cy) == (0, 0):
                    cx, cy = left + 12, min_top
                nx = max(left + 4, min(cx, right - w - 4))
                min_visible = min(MIN_STREAM_VISIBLE_HEIGHT, max(80, bottom - top - 20))
                max_origin_y = max(min_top, bottom - min_visible - 8)
                ny = min(max(cy, min_top), max_origin_y)
                self._ss.origin_x, self._ss.origin_y = nx, ny
            else:
                nx = max(left + 4, min(self._ss.origin_x, right - w - 4))
                ny = self._ss.origin_y

            if (bottom - ny - 8) < MIN_POPUP_HEIGHT:
                ny = max(min_top, bottom - MIN_POPUP_HEIGHT - 8)
                if self._ss.origin_y is not None:
                    self._ss.origin_y = ny

            # Streaming first frame: the window is still parked off-screen at
            # the fitted size (_make_popup's measurement left it at req_w×h_fit).
            # Lock the stream-grow size off-screen before moving on-screen so
            # only the position changes in the final geometry call — same
            # single-transition pattern used by _reveal_rounded_window.
            try:
                if int(win.winfo_x()) <= -3000:
                    win.geometry(f"{w}x{h}+-4000+-4000")
                    win.update_idletasks()
            except Exception:
                pass
            win.geometry(f"{w}x{h}+{nx}+{ny}")
            self._remember_window_xy(win, nx, ny)
            self._apply_window_rounding(win)
            if prev_top is not None:
                try:
                    win._text.yview(prev_top)
                except Exception:
                    pass
            return
        if not resize:
            self._fill_text(win._text, message)
            try:
                win._text.see("end-1c")
            except Exception:
                pass
            return
        w, h = self._size_popup(win, message)
        cx, cy = self._window_xy(win)
        x, y = self._clamp_to_monitor(cx, cy, w, h, ref=(cx, cy))
        win.geometry(f"{w}x{h}+{x}+{y}")
        self._remember_window_xy(win, x, y)
        self._apply_window_rounding(win)

    def _dismiss_loading_popup(self):
        """Close only the temporary loading hint; keep translation pipeline alive."""
        win = self.popup
        if not (win and getattr(win, "_hint_label", None)):
            return
        self._stop_animation()
        try:
            win.destroy()
        except Exception:
            pass
        if self.popup is win:
            self.popup = None
        log_perf("loading_dismissed", {"has_stream_data": bool(self._ss.accum)})

    def _user_close_popup(self):
        """User explicitly closed the result/error window (✕ or Esc).

        Closing must also invalidate the in-flight request. Otherwise a
        translation that is still streaming keeps its worker alive, and the next
        stream frame — or _stream_finalize when the response completes —
        re-creates the very window the user just dismissed. Bumping the job id
        makes every outstanding stream callback go stale (see _job_is_current),
        so nothing repaints after the user closes the popup."""
        self._begin_job()
        self._cancel_stream_flush()
        self._destroy_popup()

    def _destroy_popup(self):
        self._stop_animation()
        self._cancel_stream_flush()
        # Cancel any loading popup that hasn't appeared yet.
        pending = getattr(self, '_pending_loading_job', None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except Exception:
                pass
            self._pending_loading_job = None
        self._ss.cols = 0
        self._ss.fixed_w = 0
        self._ss.max_h = 0
        self._ss.origin_x = None
        self._ss.origin_y = None
        self._ss.monitor_rect = None
        self._ss.centered_ready = False
        self._ss.rendered = ""
        self._resize_mode = None
        self._resize_start = None
        if self.popup:
            try:
                self.popup.destroy()
            except Exception:
                pass
            self.popup = None

    def _reveal_rounded_window(self, win, w, h, x, y):
        self._apply_taskbar_identity(win)
        # Map and paint the rounded card OFF-SCREEN first, then move on-screen
        # in a single painted step. Deiconifying at the final position before
        # the canvas is drawn leaks one unpainted colour-key (near-black) frame
        # — the "black flash" seen just before the loading / result card shows.
        off = -4000
        win.geometry(f"{w}x{h}+{off}+{off}")
        win.deiconify()
        win.update_idletasks()
        win._round_redraw()
        win.geometry(f"{w}x{h}+{x}+{y}")
        self._remember_window_xy(win, x, y)
        win.update_idletasks()
        win._round_redraw()
        # Use the same activation as re-focus: a plain lift() only reorders
        # within our own app and leaves a borderless window un-activated, so it
        # visually floats on top until clicked. _bring_to_front gives it a real
        # OS activation (brief topmost pulse, then released) so clicking another
        # app correctly sends this window behind.
        self._bring_to_front(win)

    def run(self):
        self.root.mainloop()


def _acquire_single_instance_mutex():
    """Backwards-compatible shim → win32util.acquire_single_instance_mutex()."""
    return win32util.acquire_single_instance_mutex()


if __name__ == "__main__":
    _single_instance_handle = _acquire_single_instance_mutex()
    if _single_instance_handle is None:
        sys.exit(0)
    TranslatorApp().run()
