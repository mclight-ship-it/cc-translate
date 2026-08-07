"""Capture deterministic CC Translate UI v2 windows with Win32 PrintWindow.

This development-only tool constructs the real Tk window builders without
starting hotkeys, tray processes, model providers, or update checks. It uses
synthetic content and never reads the clipboard or the user's translation
history.
"""

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None


APP_DIR = Path(__file__).resolve().parents[1]
TRANSLATOR_PATH = APP_DIR / "translator.pyw"
SURFACES = (
    "loading",
    "result",
    "error",
    "quick-input",
    "history",
    "settings",
    "diagnostics",
    "ocr-overlay",
    "about",
    "support-author",
    "uninstall",
)
THEMES = ("dark", "light")
_GA_ROOT = 2
_PW_RENDERFULLCONTENT = 2
_DIB_RGB_COLORS = 0
_BI_RGB = 0


def _ensure_app_import_path():
    app_path = os.fspath(APP_DIR)
    if app_path not in sys.path:
        sys.path.insert(0, app_path)


def _require_pillow():
    if Image is None:
        raise RuntimeError(
            "Pillow is required to capture UI v2 windows; install project "
            "dependencies before running this tool")


def _manifest_artifact_path(output_dir, path):
    return path.relative_to(output_dir).as_posix()


class RECT(ctypes.Structure):
    _fields_ = (
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    )


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class BITMAPINFO(ctypes.Structure):
    _fields_ = (
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    )


def _load_translator():
    _ensure_app_import_path()
    spec = importlib.util.spec_from_file_location("translator", TRANSLATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {TRANSLATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["translator"] = module
    spec.loader.exec_module(module)
    return module


def parse_surfaces(values):
    requested = []
    for value in values or ("all",):
        for item in value.split(","):
            item = item.strip().lower()
            if not item:
                continue
            if item == "all":
                return list(SURFACES)
            if item not in SURFACES:
                choices = ", ".join(SURFACES)
                raise ValueError(f"Unknown surface {item!r}; choose from {choices}")
            if item not in requested:
                requested.append(item)
    if not requested:
        raise ValueError("At least one surface is required")
    return requested


def parse_themes(value):
    if value == "both":
        return list(THEMES)
    if value not in THEMES:
        raise ValueError(f"Unknown theme {value!r}")
    return [value]


def default_output_dir():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / "cc-translate-ui-v2" / stamp


def synthetic_history():
    return [
        {
            "ts": "2000-01-03 09:30",
            "input": "A small change can make a workflow feel much faster.",
            "output": "\u5c0f\u5e45\u6539\u52a8\u5c31\u80fd\u8ba9\u5de5\u4f5c\u6d41\u7a0b\u611f\u89c9\u5feb\u5f88\u591a\u3002",
            "is_dict": False,
            "is_code": False,
            "kind": "text",
            "sig": "capture-fixture",
        },
        {
            "ts": "2000-01-02 09:24",
            "input": "resilient",
            "output": "adj. \u6709\u97e7\u6027\u7684\uff1b\u80fd\u5feb\u901f\u6062\u590d\u7684",
            "is_dict": True,
            "is_code": False,
            "kind": "dict",
            "sig": "capture-fixture",
        },
        {
            "ts": "2000-01-01 09:16",
            "input": "def greet(name):\n    return f\"Hello, {name}\"",
            "output": "\u8fd9\u4e2a\u51fd\u6570\u63a5\u6536\u540d\u79f0\u5e76\u8fd4\u56de\u95ee\u5019\u8bed\u3002",
            "is_dict": False,
            "is_code": True,
            "kind": "code",
            "sig": "capture-fixture",
        },
    ]


def synthetic_diagnostics_report(tr):
    summary = (
        "OpenAI GPT (Codex) · CLI \u6b63\u5e38 · \u5df2\u767b\u5f55 · "
        "\u6d41\u5f0f\u8f93\u51fa\u5c31\u7eea"
        if tr.i18n.get_language() == "zh_CN"
        else "OpenAI GPT (Codex) · CLI OK · Signed in · Streaming ready"
    )
    if tr.i18n.get_language() == "zh_CN":
        report = (
            "\u3010\u6982\u89c8\u3011\n"
            "- \u7248\u672c: 4.5.244\n"
            "- \u6a21\u578b\u670d\u52a1: OpenAI GPT (Codex)\n"
            "- \u6a21\u578b: \u667a\u80fd\u8def\u7531\uff08\u6781\u901f\uff09\n"
            "- Codex CLI: 0.42.0\n"
            "- \u767b\u5f55\u72b6\u6001: ChatGPT \u8ba2\u9605\u5df2\u8fde\u63a5\n"
            "- Codex \u6d41\u5f0f\u8f93\u51fa: \u5c31\u7eea\n\n"
            "\u3010\u5efa\u8bae\u3011\n"
            "- \u672a\u53d1\u73b0\u660e\u663e\u95ee\u9898\u3002\n\n"
            "\u3010\u540e\u7eed\u64cd\u4f5c\u3011\n"
            "1. \u5982\u7ffb\u8bd1\u5931\u8d25\uff0c\u53ef\u5728\u6b64\u91cd\u65b0\u68c0\u6d4b\u3002\n"
            "2. \u590d\u5236\u8bca\u65ad\u540e\u53ef\u4e0e\u95ee\u9898\u63cf\u8ff0\u4e00\u8d77\u53d1\u9001\u3002\n\n"
            "\u3010\u6700\u8fd1\u9519\u8bef\u3011\n"
            "\u65e0"
        )
    else:
        report = (
            "\u3010Overview\u3011\n"
            "- Version: 4.5.244\n"
            "- Model service: OpenAI GPT (Codex)\n"
            "- Model: Smart routing (fast)\n"
            "- Codex CLI: 0.42.0\n"
            "- Login: ChatGPT subscription connected\n"
            "- Codex streaming: Ready\n\n"
            "\u3010Recommendations\u3011\n"
            "- No obvious issue detected.\n\n"
            "\u3010Next steps\u3011\n"
            "1. Redetect here if a translation fails.\n"
            "2. Copy diagnostics and include them with the issue description.\n\n"
            "\u3010Recent errors\u3011\n"
            "None"
        )
    return summary, report


def synthetic_ocr_screen():
    _require_pillow()
    width, height = 960, 600
    image = Image.new("RGB", (width, height), "#11172d")
    pixels = image.load()
    for y in range(height):
        fy = y / max(1, height - 1)
        for x in range(width):
            fx = x / max(1, width - 1)
            pixels[x, y] = (
                int(22 + 28 * fx),
                int(31 + 32 * (1 - fy)),
                int(62 + 54 * fx + 18 * fy),
            )
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (155, 115, 805, 470), radius=22,
        fill="#f6f8fc", outline="#cdd5e2", width=2)
    draw.rounded_rectangle(
        (190, 155, 720, 310), radius=14,
        fill="#ffffff", outline="#e2e6ee", width=2)
    draw.rectangle((190, 155, 720, 190), fill="#eef2ff")
    try:
        heading_font = ImageFont.truetype("segoeuib.ttf", 26)
        body_font = ImageFont.truetype("segoeui.ttf", 18)
        small_font = ImageFont.truetype("segoeui.ttf", 15)
    except OSError:
        heading_font = body_font = small_font = ImageFont.load_default()
    draw.text(
        (220, 213), "Product Roadmap 2026",
        font=heading_font, fill="#15214a")
    draw.text(
        (220, 257), "H2 release plan and milestones",
        font=body_font, fill="#55627c")
    draw.rounded_rectangle(
        (190, 338, 430, 405), radius=12,
        fill="#e8edff", outline="#cfd8ff", width=2)
    draw.text(
        (214, 360), "Design review  |  Aug 18",
        font=small_font, fill="#33426b")
    draw.rounded_rectangle(
        (458, 338, 720, 405), radius=12,
        fill="#f5eaff", outline="#e8cff8", width=2)
    draw.text(
        (482, 360), "Launch target  |  Sep 12",
        font=small_font, fill="#5d376c")
    return image


def _initialize_window_state(app):
    app._resize_mode = None
    app._resize_start = None


def _new_app(tr, root, theme, language):
    app = object.__new__(tr.TranslatorApp)
    app._fresh_install = False
    app.cfg = tr.Config(dict(tr.DEFAULT_CONFIG))
    app.cfg[tr.CFG.UI_V2] = True
    app.cfg[tr.CFG.THEME] = theme
    app.cfg[tr.CFG.LANGUAGE] = language
    tr.i18n.initialize(language)
    app.theme = tr.resolve_theme(app.cfg)
    app.root = root
    app.settings_win = None
    app.history_win = None
    app.about_win = None
    app.support_win = None
    app._uninstall_win = None
    app.diagnostics_win = None
    app.quick_input_win = None
    app._ocr_selecting = False
    app._ocr_overlay = None
    app.popup = None
    app._settings_check = None
    app._last_class = "text"
    app._last_origin = "text"
    app._last_input = ""
    app._last_result_ok = True
    app._last_result_title = ""
    app._last_result_text = ""
    app._cycle_anchor = None
    _initialize_window_state(app)
    app._setup_scrollbar_style()
    return app


@contextmanager
def _capture_runtime_overrides(tr, settings_module):
    env_name = tr.UI_V2_ENV
    missing = object()
    original_env = os.environ.get(env_name, missing)
    original_history = tr.load_history
    original_autostart = settings_module.is_autostart_enabled
    os.environ[env_name] = "1"
    tr.load_history = synthetic_history
    settings_module.is_autostart_enabled = lambda: False
    try:
        yield
    finally:
        settings_module.is_autostart_enabled = original_autostart
        tr.load_history = original_history
        if original_env is missing:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = original_env


def _build_surface(tr, app, surface):
    if surface == "loading":
        win = app._make_loading_popup()
        app.popup = win
        return win
    if surface == "result":
        message = (
            "\u4e00\u4e2a\u5c0f\u6539\u52a8\u5c31\u80fd\u8ba9\u6574\u4e2a\u5de5\u4f5c"
            "\u6d41\u611f\u89c9\u66f4\u5feb\u3002\n\n"
            "- \u5148\u5904\u7406\u6700\u5e38\u7528\u7684\u8def\u5f84\n"
            "- \u4fdd\u7559\u6e05\u6670\u7684\u52a0\u8f7d\u548c\u9519\u8bef\u72b6\u6001\n"
            "- \u7528\u771f\u5b9e\u7a97\u53e3\u9a8c\u8bc1\u6697\u8272\u4e0e\u4eae\u8272\u4e3b\u9898"
        )
        win = app._make_popup(
            message, title=tr.i18n.get("result.title"), highlight=True)
        app.popup = win
        return win
    if surface == "error":
        message = (
            "\u65e0\u6cd5\u8fde\u63a5\u5230\u6a21\u578b\u670d\u52a1\u3002"
            "\u8bf7\u68c0\u67e5\u672c\u5730 CLI \u72b6\u6001\u540e\u91cd\u8bd5\u3002"
            if tr.i18n.get_language() == "zh_CN"
            else "Unable to connect to the model service. "
                 "Check the local CLI status and try again."
        )
        win = app._make_popup(
            message, is_error=True, title=tr.i18n.get("error.title"))
        app.popup = win
        return win
    if surface == "quick-input":
        app._open_quick_input()
        win = app.quick_input_win
        editor = getattr(win, "_quick_input_text", None)
        if editor is not None:
            editor.insert("1.0", "Translate this sentence with a natural tone.")
        return win
    if surface == "history":
        app._open_history()
        return app.history_win
    if surface == "settings":
        app._open_settings()
        return app.settings_win
    if surface == "diagnostics":
        summary, report = synthetic_diagnostics_report(tr)
        app._refresh_diagnostics_window = lambda win=None: (
            app._apply_diagnostics_report(
                win or app.diagnostics_win, summary, report))
        app._open_diagnostics()
        return app.diagnostics_win
    if surface == "ocr-overlay":
        screen = synthetic_ocr_screen()
        app._virtual_screen_rect = lambda: (0, 0, *screen.size)
        app._open_region_selector(screen_image=screen)
        win = app._ocr_overlay
        win._ocr_set_selection(190, 155, 720, 310)
        return win
    if surface == "about":
        app._open_about()
        return app.about_win
    if surface == "support-author":
        app._open_support_author()
        return app.support_win
    if surface == "uninstall":
        app._perform_uninstall = lambda remove_data=False: False
        app._confirm_and_uninstall()
        return app._uninstall_win
    raise ValueError(f"Unsupported surface {surface!r}")


def _pump(root, delay_ms):
    deadline = time.monotonic() + max(0, delay_ms) / 1000
    while True:
        root.update_idletasks()
        root.update()
        if time.monotonic() >= deadline:
            return
        time.sleep(0.02)


def _root_hwnd(win):
    _require_pillow()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_ancestor = user32.GetAncestor
    get_ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
    get_ancestor.restype = wintypes.HWND
    hwnd = get_ancestor(wintypes.HWND(win.winfo_id()), _GA_ROOT)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    return hwnd


def capture_window(win):
    """Return a Pillow RGB image of a mapped Tk top-level window."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    get_rect = user32.GetWindowRect
    get_rect.argtypes = (wintypes.HWND, ctypes.POINTER(RECT))
    get_rect.restype = wintypes.BOOL
    get_dc = user32.GetWindowDC
    get_dc.argtypes = (wintypes.HWND,)
    get_dc.restype = wintypes.HDC
    release_dc = user32.ReleaseDC
    release_dc.argtypes = (wintypes.HWND, wintypes.HDC)
    release_dc.restype = ctypes.c_int
    print_window = user32.PrintWindow
    print_window.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
    print_window.restype = wintypes.BOOL

    create_dc = gdi32.CreateCompatibleDC
    create_dc.argtypes = (wintypes.HDC,)
    create_dc.restype = wintypes.HDC
    create_bitmap = gdi32.CreateCompatibleBitmap
    create_bitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    create_bitmap.restype = wintypes.HBITMAP
    select_object = gdi32.SelectObject
    select_object.argtypes = (wintypes.HDC, wintypes.HANDLE)
    select_object.restype = wintypes.HANDLE
    get_bits = gdi32.GetDIBits
    get_bits.argtypes = (
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
        ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    )
    get_bits.restype = ctypes.c_int
    delete_object = gdi32.DeleteObject
    delete_object.argtypes = (wintypes.HANDLE,)
    delete_object.restype = wintypes.BOOL
    delete_dc = gdi32.DeleteDC
    delete_dc.argtypes = (wintypes.HDC,)
    delete_dc.restype = wintypes.BOOL

    hwnd = _root_hwnd(win)
    rect = RECT()
    if not get_rect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid window size {width}x{height}")

    window_dc = get_dc(hwnd)
    if not window_dc:
        raise ctypes.WinError(ctypes.get_last_error())
    memory_dc = create_dc(window_dc)
    bitmap = create_bitmap(window_dc, width, height)
    if not memory_dc or not bitmap:
        if bitmap:
            delete_object(bitmap)
        if memory_dc:
            delete_dc(memory_dc)
        release_dc(hwnd, window_dc)
        raise ctypes.WinError(ctypes.get_last_error())

    old_object = select_object(memory_dc, bitmap)
    if not old_object:
        delete_object(bitmap)
        delete_dc(memory_dc)
        release_dc(hwnd, window_dc)
        raise ctypes.WinError(ctypes.get_last_error())
    bitmap_selected = True
    try:
        if not print_window(hwnd, memory_dc, _PW_RENDERFULLCONTENT):
            raise ctypes.WinError(ctypes.get_last_error())
        if not select_object(memory_dc, old_object):
            raise ctypes.WinError(ctypes.get_last_error())
        bitmap_selected = False
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = _BI_RGB
        buffer = (ctypes.c_ubyte * (width * height * 4))()
        lines = get_bits(
            memory_dc, bitmap, 0, height, buffer, ctypes.byref(info),
            _DIB_RGB_COLORS)
        if lines != height:
            raise ctypes.WinError(ctypes.get_last_error())
        return Image.frombuffer(
            "RGB", (width, height), bytes(buffer), "raw", "BGRX", 0, 1)
    finally:
        if bitmap_selected:
            select_object(memory_dc, old_object)
        delete_object(bitmap)
        delete_dc(memory_dc)
        release_dc(hwnd, window_dc)


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _widget_box(widget, win):
    x = widget.winfo_rootx() - win.winfo_rootx()
    y = widget.winfo_rooty() - win.winfo_rooty()
    return x, y, x + widget.winfo_width(), y + widget.winfo_height()


def expand_box(box, padding, bounds):
    left, top, right, bottom = box
    width, height = bounds
    return (
        max(0, left - padding),
        max(0, top - padding),
        min(width, right + padding),
        min(height, bottom + padding),
    )


def settings_left_column_crop_box(label_box, padding, bounds):
    left, top, right, bottom = label_box
    right = max(right, bounds[0] // 2 - padding)
    return expand_box((left, top, right, bottom), padding, bounds)


def settings_provider_crop(tr, win, image):
    labels = {
        tr.i18n.get("settings.label.translate_section"),
        tr.i18n.get("settings.label.model_provider"),
        tr.i18n.get("settings.label.translate_model"),
        tr.i18n.get("settings.label.translate_direction"),
    }
    boxes = []
    for widget in _walk(win):
        try:
            text = str(widget.cget("text"))
        except Exception:
            text = ""
        if text in labels:
            boxes.append(_widget_box(widget, win))
    if not boxes:
        raise RuntimeError("Could not locate the Settings provider region")
    union = (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
    crop_box = settings_left_column_crop_box(union, 28, image.size)
    return image.crop(crop_box), crop_box


def _destroy_app(app):
    for name in (
            "popup", "quick_input_win", "settings_win", "history_win",
            "about_win", "support_win", "diagnostics_win", "_uninstall_win",
            "_ocr_overlay"):
        win = getattr(app, name, None)
        if win is None:
            continue
        try:
            if win.winfo_exists():
                win.destroy()
        except Exception:
            pass
    for name in ("_v2_photo_cache", "_logo_cache", "_emoji_cache"):
        cache = getattr(app, name, None)
        if isinstance(cache, dict):
            cache.clear()


def run_capture(output_dir, surfaces, themes, language, delay_ms):
    _require_pillow()
    tr = _load_translator()
    import cc_app_settings

    root = tr.tk.Tk()
    root.withdraw()
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    try:
        with _capture_runtime_overrides(tr, cc_app_settings):
            for theme in themes:
                for surface in surfaces:
                    app = _new_app(tr, root, theme, language)
                    try:
                        win = _build_surface(tr, app, surface)
                        if win is None:
                            raise RuntimeError(
                                f"{surface} did not create a window")
                        _pump(root, delay_ms)
                        image = capture_window(win)
                        path = output_dir / f"{surface}-{theme}.png"
                        image.save(path)
                        record = {
                            "surface": surface,
                            "theme": theme,
                            "path": _manifest_artifact_path(output_dir, path),
                            "width": image.width,
                            "height": image.height,
                            "ui_scale": round(app._ui_scale(), 3),
                        }
                        if surface == "settings":
                            crop, crop_box = settings_provider_crop(
                                tr, win, image)
                            crop_path = (
                                output_dir / f"settings-provider-{theme}.png")
                            crop.save(crop_path)
                            record["provider_crop"] = {
                                "path": _manifest_artifact_path(
                                    output_dir, crop_path),
                                "box": list(crop_box),
                                "width": crop.width,
                                "height": crop.height,
                            }
                        records.append(record)
                        print(f"captured {surface}/{theme}: {path}")
                    finally:
                        _destroy_app(app)
                        _pump(root, 0)
    finally:
        root.destroy()

    manifest = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": APP_DIR.name,
        "language": language,
        "synthetic_data_only": True,
        "clipboard_access": False,
        "records": records,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest: {manifest_path}")
    return manifest_path


def build_parser():
    parser = argparse.ArgumentParser(
        description="Capture real CC Translate UI v2 Tk windows.")
    parser.add_argument(
        "--surface", action="append", default=[],
        help="Surface name, comma-separated names, or all (default).")
    parser.add_argument(
        "--theme", choices=("dark", "light", "both"), default="both")
    parser.add_argument(
        "--language", choices=("zh_CN", "en_US"), default="zh_CN")
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Destination directory (default: a timestamped temp directory).")
    parser.add_argument(
        "--delay-ms", type=int, default=350,
        help="Tk render settling time before each capture.")
    return parser


def main(argv=None):
    if os.name != "nt":
        raise SystemExit("capture_ui_v2.py requires Windows")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        surfaces = parse_surfaces(args.surface)
        themes = parse_themes(args.theme)
    except ValueError as exc:
        parser.error(str(exc))
    output_dir = (args.output_dir or default_output_dir()).resolve()
    run_capture(
        output_dir, surfaces, themes, args.language, args.delay_ms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
