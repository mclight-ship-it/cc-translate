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
import queue
import threading
import subprocess
import shutil
import tempfile
import uuid
import ctypes
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
    POPUP_CORNER_RADIUS, V2_CORNER_RADIUS,
    QUICK_INPUT_WINDOW_W, QUICK_INPUT_WINDOW_H,
    log_perf, log_error,
    CFG, DEFAULT_CONFIG, STREAM_MIN_CHARS, CODEX_STREAM_MIN_CHARS,
    PROVIDER_PROMPT_REVISIONS, codex_request_model,
    UI_V2_ENV, ui_v2_enabled,
    LANGUAGES, DIRECTION_MODES, DIRECTION_LABELS_ZH, DIRECTION_LABELS_EN,
    DIRECTION_LABELS, _labels_by_language, get_direction_labels,
    auto_direction_prompt, direction_prompt, resolve_target_lang,
    source_is_cjk, source_has_english, CJK_SOURCE_RATIO,
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
    get_tray_click_action_labels, get_model_labels, get_provider_labels,
    get_provider_model_labels, provider_model,
    THEME_LABELS, POPUP_LAYOUT_LABELS, OCR_ENGINE_LABELS, TRAY_CLICK_ACTION_LABELS,
    fit_box_size,
    TRIGGER_POLL_MS, StreamSession,
    is_single_word,
    MIN_POPUP_HEIGHT, MIN_STREAM_VISIBLE_HEIGHT, MIN_RESIZE_WIDTH,
    MIN_RESIZE_HEIGHT, RESIZE_HIT,
    POPUP_BAR_PAD_X, POPUP_BAR_PAD_TOP, POPUP_BAR_PAD_BOTTOM,
    POPUP_BODY_PAD_X, POPUP_BODY_PAD_BOTTOM, POPUP_TEXT_PAD_X, POPUP_TEXT_PAD_Y,
    LOADING_CORNER_RADIUS,
    CENTERED_POPUP_W, CENTERED_POPUP_H, HISTORY_WINDOW_W, HISTORY_WINDOW_H,
    LOADING_SPINNER,
)
from cc_app_warm import WarmMixin
from cc_app_update import UpdateMixin
from cc_app_tray import TrayMixin
from cc_app_about import AboutMixin
from cc_app_quickinput import QuickInputMixin
from cc_app_diagnostics import DiagnosticsMixin
from cc_app_history import HistoryMixin
from cc_app_settings import SettingsMixin
from cc_app_ocr import OcrMixin
from cc_app_results import ResultActionsMixin
from cc_app_popup import PopupMixin
from cc_providers import (
    CLAUDE_PROVIDER, CODEX_PROVIDER,
    ClaudeCliProvider, CodexCliProvider,
    ProviderRegistry, ProviderRequest, ProviderSelection,
)


def _enable_dpi_awareness():
    """Backwards-compatible shim → win32util.enable_dpi_awareness()."""
    win32util.enable_dpi_awareness()


_enable_dpi_awareness()


CONFIG_PATH = _user_data_path("config.json")
POPUP_SHELL_PAD = 1  # legacy 1px border inset; popups now use the rounded colour-key card
# (popup/window layout constants — MIN_*, RESIZE_HIT, POPUP_*_PAD_*,
#  LOADING_CORNER_RADIUS, CENTERED_POPUP_W/H, HISTORY_WINDOW_W/H — live in
#  cc_core.py and are re-exported via the cc_core import above.)

# Hotkey handoff: the global keyboard listener runs on its own thread and must
# never touch Tcl/Tk directly. It drops trigger requests into a queue that the
# main thread drains on a timer, which fixes the "no response then a burst of
# translations" races seen right after startup.
# (TRIGGER_POLL_MS lives in cc_core.py, re-exported below)
TRIGGER_SETTLE_MS = 120
# After a translate trigger, restore the clipboard the user had *before* their
# Ctrl+C, so triggering a translation doesn't clobber their copy/paste workflow.
CLIP_RESTORE_MS = 250

# Loading spinner frames (rotating half-circle). Segoe UI Symbol renders these
# on Windows; the animation cycles through them for a modern indeterminate look.
# (LOADING_SPINNER lives in cc_core.py, re-exported below)


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

# (is_single_word lives in cc_core.py, re-exported via the cc_core import above)


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
_CODE_CALL_RE = re.compile(r"[A-Za-z_]\w*\(")             # foo(  bar(
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


# Section headings for the long-text summary, in each SUPPORTED TARGET
# language. The summary is written in the language the text is translated
# INTO, so the heading must match that language too — never the app's UI
# language. Unknown targets fall back to English.
SUMMARY_HEADINGS = {
    "zh": ("摘要", "译文"),
    "en": ("Summary", "Translation"),
    "ja": ("要約", "翻訳"),
    "ko": ("요약", "번역"),
    "fr": ("Résumé", "Traduction"),
    "de": ("Zusammenfassung", "Übersetzung"),
    "es": ("Resumen", "Traducción"),
}


def summary_headings(target_lang):
    """(summary_heading, translation_heading) for the summary sections, in the
    TARGET language (the language being translated INTO), so a zh->en summary
    reads 'Summary'/'Translation' and an en->zh summary reads '摘要'/'译文'.
    ``target_lang`` is a LANGUAGES code (see resolve_target_lang)."""
    return SUMMARY_HEADINGS.get(target_lang, SUMMARY_HEADINGS["en"])


def summary_instruction(target_lang):
    """Instruction appended to the translate prompt when the long-text summary
    feature is active. Asks the model to emit a short summary first, then the
    full translation, using two Markdown headings the renderer already styles.

    The summary MUST be in the target language (the language being translated
    INTO), the same language as the translation — otherwise the two halves come
    out in different languages (e.g. a Chinese summary above an English
    translation). Naming the concrete target language explicitly makes smaller
    models comply far more reliably than a generic 'the target language'."""
    sm, tr = summary_headings(target_lang)
    lang_name = LANGUAGES.get(target_lang, (None, "the target language"))[1]
    return (
        " IMPORTANT OUTPUT FORMAT: because the text is long, structure your "
        "ENTIRE response as exactly two Markdown sections. FIRST, a line with "
        f"the heading `## {sm}` followed by a brief summary of 3-5 short lines "
        f"capturing the key points. THEN, a line with the heading `## {tr}` "
        "followed by the full translation. Use level-2 `##` headings with "
        f"exactly those two heading texts. CRITICAL: write EVERYTHING — the "
        f"heading words, the summary, AND the translation — in {lang_name}. The "
        f"summary must be in {lang_name}, the SAME language as the translation, "
        "never in the source language.")



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
        raw = dict(data or {})
        super().__init__(DEFAULT_CONFIG)
        if data:
            self.update(data)
        if CFG.MODEL_PROVIDER not in raw:
            self[CFG.MODEL_PROVIDER] = DEFAULT_CONFIG[CFG.MODEL_PROVIDER]
        if CFG.CLAUDE_MODEL not in raw:
            self[CFG.CLAUDE_MODEL] = raw.get(
                CFG.MODEL, DEFAULT_CONFIG[CFG.CLAUDE_MODEL])
        if CFG.CODEX_MODEL not in raw:
            self[CFG.CODEX_MODEL] = DEFAULT_CONFIG[CFG.CODEX_MODEL]
        elif self[CFG.CODEX_MODEL] == "gpt-5.4-mini":
            # The former standalone mini option is now an internal branch of
            # smart routing, so migrate saved selections to the complete mode.
            self[CFG.CODEX_MODEL] = "auto-fast"
        # Keep the old key synchronized for one downgrade-compatible release.
        self[CFG.MODEL] = self[CFG.CLAUDE_MODEL]
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
    def model_provider(self):
        return self.get(CFG.MODEL_PROVIDER, DEFAULT_CONFIG[CFG.MODEL_PROVIDER])

    @property
    def claude_model(self):
        return self.get(CFG.CLAUDE_MODEL, DEFAULT_CONFIG[CFG.CLAUDE_MODEL])

    @property
    def codex_model(self):
        return self.get(CFG.CODEX_MODEL, DEFAULT_CONFIG[CFG.CODEX_MODEL])

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
CODEX_FIRST_FRAME_WAIT_SECONDS = 0.05

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
                is_code: bool = False, kind: Optional[str] = None,
                sig: Optional[str] = None) -> None:
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
            "sig": sig or "",
        })
        del entries[max(1, int(limit)):]
        try:
            _atomic_write_json(HISTORY_PATH, entries)
        except Exception as e:
            log_error("add_history", e)


def find_cached_translation(text: str, kind: str, sig: str):
    """Return the stored output of an identical earlier translation, or None.

    A hit requires the same stripped input, the same kind (text/dict/code --
    never ocr, whose input is a screenshot's OCR text on a separate pipeline),
    and the same settings signature, so the cached result is always faithful
    to the current direction/model/summary/language. Lets the app skip a
    re-translation of something the user already translated.
    """
    if not text or not text.strip():
        return None
    if kind not in ("text", "dict", "code"):
        return None
    key = text.strip()
    for entry in load_history():
        if (entry.get("kind") == kind
                and (entry.get("sig") or "") == (sig or "")
                and (entry.get("input") or "").strip() == key):
            out = (entry.get("output") or "").strip()
            if out:
                return out
    return None


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


# (WarmClaude class lives in cc_warm.py)
# (StreamSession dataclass lives in cc_core.py, re-exported below)
# (Rounded-window plumbing + popup methods live in cc_app_popup.py / PopupMixin)


class TranslatorApp(WarmMixin, UpdateMixin, TrayMixin, AboutMixin,
                    QuickInputMixin, DiagnosticsMixin, HistoryMixin,
                    SettingsMixin, OcrMixin, ResultActionsMixin, PopupMixin):
    def __init__(self):
        # Detect a fresh install *before* loading config: on first run the
        # config file doesn't exist yet. We use this to enable autostart by
        # default for new users (see _run_startup_tasks), without ever
        # re-enabling it for existing users who deliberately turned it off.
        self._fresh_install = not os.path.exists(CONFIG_PATH)
        self.cfg = load_config()
        self._provider_registry = ProviderRegistry()
        self._provider_registry.register(ClaudeCliProvider(
            self._call_claude, self._call_claude_vision, CLAUDE_CMD))
        self._provider_registry.register(CodexCliProvider())
        self._provider_cancel_event = None
        
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
        self._last_provider_route = {}
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
        # Cursor position captured once when a translation is triggered, in
        # follow-cursor layout. Every popup in that cycle (the "translating"
        # hint and the result window, streaming or not) anchors here so the
        # result never jumps to a new spot when the mouse moves mid-flight.
        self._cycle_anchor = None

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
        self._warm_generation = 0    # invalidates spawns after reset/disable
        self._warm_enabled = (
            WARM_POOL_ENABLED
            and self.cfg.get(CFG.MODEL_PROVIDER) == CLAUDE_PROVIDER
        )

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

    def _provider_selection(self):
        cfg = getattr(self, "cfg", {})
        provider_id = cfg.get(
            CFG.MODEL_PROVIDER, DEFAULT_CONFIG[CFG.MODEL_PROVIDER])
        return ProviderSelection(
            provider_id=provider_id,
            model=provider_model(cfg, provider_id),
        )

    def _provider_status(self, provider_id):
        return self._provider_registry.get(provider_id).diagnose()

    def _provider_error_text(self, result):
        messages = {
            "cli_not_installed": "error.codex_not_installed",
            "login_required": "error.codex_login_required",
            "rate_limited": "error.rate_limited",
            "model_unavailable": "error.codex_model_unavailable",
            "timeout": "error.translation_timeout",
            "cancelled": "error.cancelled",
            "unsafe_tool_event": "error.codex_security_blocked",
            "unknown_event": "error.codex_protocol_changed",
            "unknown_item": "error.codex_protocol_changed",
            "invalid_jsonl": "error.codex_protocol_changed",
            "invalid_appserver_json": "error.codex_protocol_changed",
            "invalid_appserver_message": "error.codex_protocol_changed",
            "unknown_appserver_event": "error.codex_protocol_changed",
            "unknown_appserver_item": "error.codex_protocol_changed",
            "no_result": "error.no_result",
            "empty_output": "error.no_result",
        }
        key = messages.get(result.error_code)
        if key:
            return i18n.get(key)
        detail = result.error_detail or result.error_code
        if detail:
            return i18n.get("error.translation_failed_with_reason").format(
                error=detail[:200])
        return i18n.get("error.no_result")

    def _call_model(self, text, system_prompt, selection=None,
                    cancel_event=None):
        selection = selection or self._provider_selection()
        if selection.provider_id == CLAUDE_PROVIDER:
            return self._call_claude(
                text, system_prompt, model=selection.model)
        if selection.provider_id != CODEX_PROVIDER:
            return False, i18n.get("error.unknown_provider").format(
                provider=selection.provider_id)
        request = ProviderRequest(
            task="text",
            model=codex_request_model(selection.model, len(text)),
            system_prompt=system_prompt,
            user_text=text,
        )
        result = self._provider_registry.get(CODEX_PROVIDER).complete(
            request, cancel_event)
        log_perf("provider_complete", {
            "provider": selection.provider_id,
            "model": selection.model or "auto",
            "task": request.task,
            "chars": len(text),
            "ok": result.ok,
            "cancelled": result.error_code == "cancelled",
            "error_code": result.error_code or None,
            **dict(result.metrics),
        })
        if result.ok:
            return True, result.text
        return False, self._provider_error_text(result)

    def _call_model_image(self, img_path, selection=None, cancel_event=None):
        selection = selection or self._provider_selection()
        if selection.provider_id == CLAUDE_PROVIDER:
            return self._call_claude_vision(
                img_path, model=selection.model)
        if selection.provider_id != CODEX_PROVIDER:
            return False, i18n.get("error.unknown_provider").format(
                provider=selection.provider_id)
        request = ProviderRequest(
            task="image",
            model=selection.model,
            system_prompt=OCR_VISION_PROMPT,
            user_text="Translate the attached image while preserving its structure.",
            image_paths=(img_path,),
            timeout_seconds=90,
        )
        result = self._provider_registry.get(CODEX_PROVIDER).complete(
            request, cancel_event)
        log_perf("provider_complete", {
            "provider": selection.provider_id,
            "model": selection.model or "auto",
            "task": request.task,
            "images": len(request.image_paths),
            "ok": result.ok,
            "cancelled": result.error_code == "cancelled",
            "error_code": result.error_code or None,
            **dict(result.metrics),
        })
        if result.ok:
            return True, result.text
        return False, self._provider_error_text(result)

    def _add_history(self, *args, **kwargs):
        """Append a history entry via the module-level ``add_history``.

        Thin instance wrapper so mixin modules (e.g. ResultActionsMixin in
        cc_app_results) can record history through ``self`` without importing
        translator.pyw — ``add_history`` stays in this module because it belongs
        to the history-IO family (HISTORY_PATH / load_history / _atomic_write_json
        / _HISTORY_LOCK)."""
        return add_history(*args, **kwargs)

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
        previous = getattr(self, "_provider_cancel_event", None)
        if previous is not None:
            previous.set()
        self._job_id += 1
        self._provider_cancel_event = threading.Event()
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

    def _show_loading(self, text, origin="text", force_class=None, use_cache=True):
        self._destroy_popup()
        # Capture the cursor ONCE, at the moment translation is triggered, so the
        # whole cycle (loading hint + result window) anchors to where the user was
        # working — not to wherever the mouse drifts while the request is in
        # flight. Only meaningful in follow-cursor layout; the centred layout
        # ignores it. Reads on the UI thread here, before any await/defer.
        if not self._is_centered_layout():
            try:
                self._cycle_anchor = (self.root.winfo_pointerx() + 12,
                                      self.root.winfo_pointery() + 18)
            except Exception:
                self._cycle_anchor = None
        else:
            self._cycle_anchor = None
        self._last_input = text
        self._last_origin = origin
        # force_class lets a user override the heuristic — e.g. the code-explain
        # popup's "作为文字翻译" button re-runs a misclassified selection as text.
        self._last_class = force_class or classify_selection(text)
        # Instant path: if an identical earlier selection (same text, kind and
        # settings) is already in history, show that stored result immediately
        # instead of paying for another translation. Skipped for explicit
        # retries and translate-as-text overrides (force_class) and for
        # screenshots (ocr), which take the vision pipeline.
        if (use_cache and force_class is None and origin != "ocr"
                and self.cfg.get(CFG.HISTORY_ENABLED, True)):
            cached = find_cached_translation(
                text, self._history_kind(), self._cache_signature())
            if cached is not None:
                job_id = self._begin_job()
                selection = self._provider_selection()
                if selection.provider_id == CODEX_PROVIDER:
                    self._set_provider_route(
                        job_id, selection, "cache")
                log_perf("cache_hit", {"chars": len(text or ""),
                                       "kind": self._history_kind()})
                self._show_result(True, cached, job_id, record=False)
                return
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
        selection = self._provider_selection()
        return {
            "input": self._last_input,
            "origin": self._last_origin,
            "is_code": self._last_class == "code",
            "kind": self._history_kind(),
            "sig": self._cache_signature(),
            "provider": selection.provider_id,
            "model": selection.model,
            "direction": self.cfg.get(CFG.DIRECTION, "auto"),
            "summarize": self._should_summarize(self._last_input or ""),
            "system_prompt": self._system_prompt_for(self._last_input or ""),
            "cancel_event": getattr(self, "_provider_cancel_event", None),
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
            self._show_loading(self._last_input, origin=self._last_origin,
                               use_cache=False)

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
        if self._last_origin == "ocr":
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
            # Summary + translation must share ONE language: the language this
            # text is translated INTO, not the app UI language. In auto mode
            # that depends on the source, so resolve it from mode + text.
            target_lang = resolve_target_lang(mode, app_language, text)
            return base_prompt + summary_instruction(target_lang) + SUMMARY_SUFFIX
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

    def _cache_signature(self) -> str:
        """A compact fingerprint of the settings that change a translation's
        output -- provider prompt contract, direction, model, summary and app
        language. A cached result is only reused when this matches, so a stored
        translation is never served under settings that would produce a
        different one.
        """
        selection = self._provider_selection()
        fields = [
            selection.provider_id,
            str(selection.model or "auto"),
            str(self.cfg.get(CFG.DIRECTION, "auto")),
            "sum1" if self.cfg.get(CFG.SUMMARY_ENABLED, False) else "sum0",
            str(self.cfg.get(CFG.LANGUAGE) or i18n.get_language()),
        ]
        prompt_revision = PROVIDER_PROMPT_REVISIONS.get(
            selection.provider_id, "")
        if prompt_revision:
            fields.append(prompt_revision)
        return "|".join(fields)

    def _remember_result(self, ok, title, text):
        self._last_result_ok = bool(ok)
        self._last_result_title = title or ""
        self._last_result_text = (text or "").strip()
        # Refresh the tray so the "recall last result" item un-grays right away
        # rather than lagging until the next unrelated menu rebuild.
        self._refresh_tray_menu()

    def _do_translate(self, text, job_id, meta):
        provider_id = meta.get("provider", CLAUDE_PROVIDER)
        if provider_id != CLAUDE_PROVIDER:
            self._do_provider_translate(text, job_id, meta)
            return
        # Long, non-dictionary text streams so the translation appears
        # progressively; short text uses the simpler one-shot path.
        t0 = time.perf_counter()
        ss = self._ss   # bind this job's session; a newer job swaps self._ss
        dictionary = is_single_word(text)
        is_code = bool(meta.get(
            "is_code", self._last_class == "code"))
        # Summary mode needs a different system prompt than the warm process was
        # spawned with, so it must skip the warm fast-path and take the cold
        # streaming path (which rebuilds the prompt via _system_prompt_for).
        summarize = bool(meta.get(
            "summarize", self._should_summarize(text)))

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
            ok, result = self._call_claude(
                text,
                meta.get("system_prompt"),
                model=meta.get("model"),
            )
        except Exception as e:
            ok, result = False, i18n.get("error.unexpected").format(error=e)
            log_error("translate", e)
        log_perf("translate_done", {
            "mode": mode,
            "chars": len(text),
            "wall_ms": int((time.perf_counter() - t0) * 1000),
            "ok": bool(ok),
        })
        if ok:
            self._record_history(
                job_id, meta, result,
                is_dict=is_single_word(meta.get("input", "")))
        self.root.after(
            0, lambda: self._show_result(
                ok, result, job_id, record=False))

    def _do_provider_translate(self, text, job_id, meta):
        selection = ProviderSelection(
            provider_id=meta.get("provider", ""),
            model=meta.get("model"),
        )
        cancel_event = meta.get("cancel_event")
        t0 = time.perf_counter()
        dictionary = is_single_word(text)
        stream_enabled = self.cfg.get(
            CFG.CODEX_STREAMING_EXPERIMENTAL,
            DEFAULT_CONFIG[CFG.CODEX_STREAMING_EXPERIMENTAL])
        fast_profile = selection.model == "auto-fast"
        stream_eligible = (
            selection.provider_id == CODEX_PROVIDER
            and stream_enabled
            and (fast_profile or (
                len(text) >= CODEX_STREAM_MIN_CHARS
                and not dictionary)))
        if selection.provider_id == CODEX_PROVIDER and not stream_eligible:
            reason = (
                "disabled" if not stream_enabled
                else "dictionary" if dictionary
                else "short_text")
            self._set_provider_route(
                job_id, selection, "stable_exec", reason)
        if stream_eligible:
            try:
                stream_handled = self._stream_codex(
                    text, job_id, self._ss, meta, selection)
                if stream_handled:
                    route = dict(getattr(
                        self, "_last_provider_route", {}) or {})
                    mode = route.get("mode") or "stream_failed"
                    stream_ok = mode == "streamed"
                    stream_cancelled = mode == "stream_cancelled"
                    self._log_provider_route_completion(
                        selection, text, t0, stream_ok, stream_cancelled,
                        route=mode,
                        error_code=route.get("error_code") or "")
                    log_perf("translate_done", {
                        "mode": "provider_stream",
                        "provider": selection.provider_id,
                        "model": selection.model,
                        "chars": len(text),
                        "wall_ms": int(
                            (time.perf_counter() - t0) * 1000),
                        "ok": stream_ok,
                        "cancelled": stream_cancelled,
                    })
                    return
            except Exception as exc:
                self._set_provider_route(
                    job_id, selection, "stream_failed", "unexpected")
                self._log_provider_route_completion(
                    selection, text, t0, False, False,
                    route="stream_failed", error_code="unexpected")
                log_error("codex_stream", exc)
                error_text = i18n.get(
                    "error.unexpected").format(error=exc)
                try:
                    self.root.after(
                        0, lambda: self._show_result(
                            False, error_text, job_id, record=False))
                except tk.TclError:
                    pass
                return
        try:
            ok, result = self._call_model(
                text,
                meta.get("system_prompt") or self._system_prompt_for(text),
                selection,
                cancel_event,
            )
        except Exception as exc:
            ok = False
            result = i18n.get("error.unexpected").format(error=exc)
            log_error("provider_translate", exc)
        if ok:
            self._record_history(
                job_id, meta, result,
                is_dict=is_single_word(meta.get("input", "")))
        log_perf("translate_done", {
            "mode": "provider",
            "provider": selection.provider_id,
            "model": selection.model,
            "chars": len(text),
            "wall_ms": int((time.perf_counter() - t0) * 1000),
            "ok": bool(ok),
        })
        route = dict(getattr(self, "_last_provider_route", {}) or {})
        cancelled = bool(cancel_event and cancel_event.is_set() and not ok)
        self._log_provider_route_completion(
            selection, text, t0, bool(ok), cancelled,
            route=route.get("mode") or "stable_exec",
            error_code=route.get("error_code") or "")
        self.root.after(
            0, lambda: self._show_result(
                ok, result, job_id, record=False))

    def _set_provider_route(
            self, job_id, selection, mode, reason="", error_code=""):
        if hasattr(self, "_job_id") and job_id != self._job_id:
            return
        self._last_provider_route = {
            "provider": selection.provider_id,
            "model": selection.model or "auto",
            "mode": mode,
            "reason": reason,
            "error_code": error_code,
        }

    def _log_provider_route_completion(
            self, selection, text, started_at, ok, cancelled,
            route, error_code=""):
        if cancelled:
            outcome = "cancelled"
        elif ok:
            outcome = "success"
        else:
            outcome = "failed"
        log_perf("provider_route_complete", {
            "provider": selection.provider_id,
            "model": selection.model or "auto",
            "route": route,
            "outcome": outcome,
            "chars": len(text),
            "wall_ms": int((time.perf_counter() - started_at) * 1000),
            "ok": bool(ok),
            "cancelled": bool(cancelled),
            "error_code": (
                error_code or
                ("cancelled" if cancelled else "provider_failed" if not ok
                 else None)),
        })

    def _stream_codex(self, text, job_id, ss, meta, selection):
        """Stream an eligible Codex request through experimental app-server.

        A failure before any visible delta falls back to stable ``codex exec``.
        Once output is visible, a failure is surfaced rather than issuing a
        duplicate model request.
        """
        system_prompt = (
            meta.get("system_prompt") or self._system_prompt_for(text))
        request = ProviderRequest(
            task="text",
            model=codex_request_model(selection.model, len(text)),
            system_prompt=system_prompt,
            user_text=text,
            timeout_seconds=90.0,
        )
        cancel_event = meta.get("cancel_event")
        received_delta = threading.Event()
        rendered_delta = threading.Event()
        schedule_failed = threading.Event()
        stream_active = threading.Event()
        stream_active.set()
        first_frame_lock = threading.Lock()
        first_frame_state = {"value": "pending"}
        first_frame_scheduled = {"value": False}
        ss.popup_ready = False

        def render_first_frame():
            with first_frame_lock:
                if (not stream_active.is_set()
                        or not self._job_is_current(job_id)
                        or self._ss is not ss):
                    first_frame_state["value"] = "abandoned"
                    return
                first_frame_state["value"] = "rendering"
            appended = []
            try:
                while True:
                    appended.append(ss.queue.get_nowait())
            except queue.Empty:
                pass
            if not appended:
                with first_frame_lock:
                    first_frame_state["value"] = "failed"
                return
            ss.accum += "".join(appended)
            try:
                self._stream_update(ss.accum)
            except Exception:
                with first_frame_lock:
                    first_frame_state["value"] = "failed"
                return
            with first_frame_lock:
                first_frame_state["value"] = "rendered"
                rendered_delta.set()

        def on_delta(delta):
            received_delta.set()
            ss.queue.put(delta)
            if not first_frame_scheduled["value"]:
                first_frame_scheduled["value"] = True
                try:
                    self.root.after(0, render_first_frame)
                except tk.TclError:
                    schedule_failed.set()
                    return
                # Give Tk one frame to render without stalling protocol parsing
                # for a full second when the UI thread is temporarily busy.
                rendered_delta.wait(CODEX_FIRST_FRAME_WAIT_SECONDS)
                return
            if not rendered_delta.is_set():
                return
            try:
                self.root.after(0, lambda: self._stream_flush(job_id))
            except tk.TclError:
                schedule_failed.set()

        result = self._provider_registry.get(CODEX_PROVIDER).stream(
            request, on_delta, cancel_event)
        log_perf("provider_stream_complete", {
            "provider": selection.provider_id,
            "model": selection.model or "auto",
            "task": request.task,
            "chars": len(text),
            "ok": result.ok,
            "cancelled": result.error_code == "cancelled",
            "error_code": result.error_code or None,
            **dict(result.metrics),
        })
        if result.ok:
            self._set_provider_route(
                job_id, selection, "streamed")
            stream_active.clear()
            final = result.text
            self._record_history(
                job_id, meta, final,
                is_dict=is_single_word(meta.get("input", "")))
            try:
                self.root.after(
                    0, lambda: self._stream_finalize(final, job_id))
            except tk.TclError:
                pass
            return True
        if result.error_code == "cancelled":
            self._set_provider_route(
                job_id, selection, "stream_cancelled")
            stream_active.clear()
            return True
        if not received_delta.is_set():
            self._set_provider_route(
                job_id, selection, "stable_fallback", "pre_output_failure",
                result.error_code)
            return False
        with first_frame_lock:
            frame_claimed = first_frame_state["value"] in {
                "rendering", "rendered",
            }
            if not frame_claimed:
                first_frame_state["value"] = "abandoned"
                stream_active.clear()
        if not frame_claimed:
            while True:
                try:
                    ss.queue.get_nowait()
                except queue.Empty:
                    break
            if schedule_failed.is_set() or not self._job_is_current(job_id):
                self._set_provider_route(
                    job_id, selection, "stream_failed",
                    "render_unavailable", result.error_code)
                return True
            self._set_provider_route(
                job_id, selection, "stable_fallback", "pre_output_failure",
                result.error_code)
            return False
        self._set_provider_route(
            job_id, selection, "stream_failed", "after_output_failure",
            result.error_code)
        stream_active.clear()
        error_text = self._provider_error_text(result)

        def show_stream_error():
            if not self._job_is_current(job_id) or self._ss is not ss:
                return
            self._cancel_stream_flush()
            while True:
                try:
                    ss.queue.get_nowait()
                except queue.Empty:
                    break
            self._show_result(
                False, error_text, job_id, record=False)

        try:
            self.root.after(0, show_stream_error)
        except tk.TclError:
            pass
        return True

    def _warm_translate(self, text, job_id, ss, meta, profile="translate"):
        """Translate using a pre-warmed process for the given profile, streaming
        deltas through the same display pipeline as _stream_claude. Returns True
        on success, or False to fall back to the cold path. The warm process is
        consumed and a replacement for the same profile is spawned afterwards."""
        if profile == "dictionary":
            expected_key = ("dictionary", meta.get("model"))
        else:
            expected_key = (
                "translate", meta.get("model"), meta.get("direction"))
        warm = self._take_warm(profile, expected_key=expected_key)
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
                    is_code=meta["is_code"], kind=meta["kind"],
                    sig=meta.get("sig", ""))

    def _stream_claude(self, text: str, job_id: int, ss, meta: Dict[str, Any]) -> bool:
        """Stream a long translation via stream-json, updating the popup as
        deltas arrive. Returns True on success, False to fall back to one-shot.

        Hardened like the warm path: a watchdog timer kills a runaway CLI, the
        child process is always cleaned up, and only a non-error terminal
        `result` event (or, failing that, accumulated deltas) counts as success
        — a mid-stream abort no longer passes truncated text off as a result."""
        system_prompt = (
            meta.get("system_prompt") or self._system_prompt_for(text))
        model = meta.get("model") or self.cfg[CFG.MODEL]
        payload = f"<text>\n{text}\n</text>"
        ss.popup_ready = False
        t0 = time.perf_counter()
        proc = None
        killed = {"v": False}
        timer = None
        try:
            proc = subprocess.Popen(
                [CLAUDE_CMD, "-p", "--safe-mode", "--model", model,
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
                anchor = self._cycle_popup_anchor()
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
                self._maybe_add_as_text_button(self.popup)
                self._maybe_add_result_actions_button(self.popup)
                self._remember_result(True, self._result_title(True), final)
                return

            anchor = self._cycle_popup_anchor()
            self._stop_animation()
            self._destroy_popup()
            self.popup = self._make_popup(final, anchor=anchor,
                                          title=self._result_title(),
                                          highlight=True)
            self._ss.popup_ready = True
            self._set_popup_text(final, stream_grow=True, stream_final=True)
            self._maybe_add_explain_button(self.popup)
            self._maybe_add_as_text_button(self.popup)
            self._maybe_add_result_actions_button(self.popup)
            self._remember_result(True, self._result_title(True), final)
            log_perf("stream_finalize_popup_created", {"chars": len(final)})
        except Exception as e:
            log_error("stream_finalize", e)

    def _call_claude(self, text: str, system_prompt: Optional[str] = None,
                     *, model: Optional[str] = None) -> Tuple[bool, str]:
        if system_prompt is None:
            system_prompt = self._system_prompt_for(text)
        model = model or self.cfg[CFG.MODEL]
        # Wrap the selection in tags so a bare word isn't mistaken for an
        # instruction (fixes short inputs returning "请提供要翻译的文本").
        payload = f"<text>\n{text}\n</text>"
        t0 = time.perf_counter()

        try:
            # Pass the text via stdin, NOT as a CLI argument: claude -p treats a
            # newline in an argument as end-of-input and would translate only the
            # first line/paragraph. stdin delivers the whole selection intact.
            proc = subprocess.run(
                [CLAUDE_CMD, "-p", "--safe-mode", "--model", model,
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

    def _cycle_popup_anchor(self):
        """Anchor shared by every popup in the current translate cycle: the
        cursor captured once when translation was triggered (_show_loading), so
        the loading hint and the result never jump apart if the mouse moves
        while the translation is in flight. Falls back to the live loading
        popup's position, then to None (let the popup read the live cursor)."""
        a = getattr(self, "_cycle_anchor", None)
        if a is not None:
            return a
        if self.popup:
            try:
                return self._window_xy(self.popup)
            except Exception:
                return None
        return None

    def _show_result(self, ok, result, job_id=None, record=True):
        if job_id is not None and not self._job_is_current(job_id):
            return
        self._stop_animation()
        title = self._result_title(ok)
        anchor = self._cycle_popup_anchor()
        self._destroy_popup()
        self._remember_result(ok, title, result)
        self.popup = self._make_popup(result, anchor=anchor, is_error=not ok,
                                      title=title, highlight=ok)
        self._maybe_add_explain_button(self.popup)
        if ok:
            self._maybe_add_as_text_button(self.popup)
            self._maybe_add_result_actions_button(self.popup)
        if record and ok and self.cfg.get(CFG.HISTORY_ENABLED, True) and (
                self._last_input or self._last_origin == "ocr"):
            add_history(self._last_input or "", result,
                        is_single_word(self._last_input),
                        self.cfg.get(CFG.HISTORY_LIMIT, 100),
                        is_code=(self._last_class == "code"),
                        kind=self._history_kind(),
                        sig=self._cache_signature())

    def has_recallable_result(self):
        """True when there is a stored result that can be re-displayed."""
        return self._last_result_ok is not None

    def _reshow_last_result(self):
        """Re-display the last translation/explanation popup without
        re-translating, re-recording history, or spending any tokens. Used to
        recover a result the user accidentally dismissed or lost behind another
        window. Reuses the stored ``_last_result_*`` state and the normal
        _make_popup display path so the recalled window (and its action buttons)
        looks exactly like the original. No-op when nothing has been translated
        yet. Must run on the main Tk thread."""
        if not self.has_recallable_result():
            return False
        anchor = None
        if self.popup:
            try:
                anchor = self._window_xy(self.popup)
            except Exception:
                anchor = None
        self._destroy_popup()
        ok = self._last_result_ok
        self.popup = self._make_popup(self._last_result_text, anchor=anchor,
                                      is_error=not ok,
                                      title=self._last_result_title,
                                      highlight=ok)
        self._maybe_add_explain_button(self.popup)
        if ok:
            self._maybe_add_as_text_button(self.popup)
            self._maybe_add_result_actions_button(self.popup)
        return True

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self._shutdown_model_processes()

    def _shutdown_model_processes(self):
        """Cancel active work and terminate all reusable model processes."""
        cancel_event = getattr(self, "_provider_cancel_event", None)
        if cancel_event is not None:
            cancel_event.set()
        self.close_warm_pool()
        registry = getattr(self, "_provider_registry", None)
        if registry is None:
            return
        try:
            registry.shutdown()
        except Exception as exc:
            log_error("provider_shutdown", exc)


def _acquire_single_instance_mutex():
    """Backwards-compatible shim → win32util.acquire_single_instance_mutex()."""
    return win32util.acquire_single_instance_mutex()


if __name__ == "__main__":
    _single_instance_handle = _acquire_single_instance_mutex()
    if _single_instance_handle is None:
        sys.exit(0)
    TranslatorApp().run()
