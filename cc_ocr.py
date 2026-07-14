"""OCR screenshot capture + local text recognition for CC Translate.

Two responsibilities, both free of any Tk / GUI dependency so they can be
unit-tested in isolation:

  * Screen capture — grab an arbitrary rectangle of the (possibly multi-monitor)
    virtual desktop via Pillow's ImageGrab and save it to a PNG for either
    Claude Vision or the local OCR engine to read.

  * Local OCR — recognise text offline via the Windows.Media.Ocr engine
    (exposed through the `winsdk` package). This is the privacy/offline
    fallback; Claude Vision is the default, higher-quality path handled in
    translator.pyw.

Language strategy (validated empirically on this machine):
  * The English (en-*) engine reads Latin text cleanly but drops CJK entirely.
  * The Chinese (zh-*) engine reads CJK perfectly and Latin roughly.
  * Each engine runs in well under 100 ms, negligible next to a ~3 s
    translation, so we run several and pick the best result by dominant script:
    if any CJK-capable engine found CJK characters we take its output
    (Chinese perfect, English good enough for the translator to clean up);
    otherwise we take the cleanest Latin result.

Public API used by translator.pyw:
    set_log_error(fn)
    grab_region(x, y, w, h) -> PIL.Image
    save_region(x, y, w, h, path) -> bool
    local_ocr_available() -> bool
    available_ocr_languages() -> list[str]
    ocr_local(image_path, extra_langs=None) -> str
"""

import asyncio


# Wired to translator.log_error after DATA_DIR resolves; a no-op until then so
# importing this module never depends on the host app being initialised.
def _noop_log_error(where, exc):
    pass


_log_error = _noop_log_error


def set_log_error(fn):
    global _log_error
    _log_error = fn


# ---------------------------------------------------------------------------
# Optional dependency probing (Pillow for capture, winsdk for local OCR).
# All imports are guarded so a missing package degrades gracefully rather than
# crashing the whole app at import time.
# ---------------------------------------------------------------------------

try:
    from PIL import ImageGrab
    _PIL_OK = True
except Exception:
    ImageGrab = None
    _PIL_OK = False


def _import_winsdk():
    """Return the winsdk OCR symbols we need, or None if winsdk is missing."""
    try:
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.globalization import Language
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.storage import StorageFile, FileAccessMode
        return {
            "OcrEngine": OcrEngine,
            "Language": Language,
            "BitmapDecoder": BitmapDecoder,
            "StorageFile": StorageFile,
            "FileAccessMode": FileAccessMode,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------

def grab_region(x, y, w, h):
    """Grab a rectangle of the virtual desktop and return a PIL Image.

    Coordinates are in virtual-screen space (the same space Tk reports via
    winfo_pointerx/y), so a region spanning secondary monitors works. Returns
    None if Pillow is unavailable or the grab fails."""
    if not _PIL_OK:
        return None
    try:
        bbox = (int(x), int(y), int(x + w), int(y + h))
        return ImageGrab.grab(bbox=bbox, all_screens=True)
    except Exception as e:
        _log_error("ocr_grab_region", e)
        return None


def save_region(x, y, w, h, path):
    """Grab a region and save it as PNG at `path`. Returns True on success."""
    img = grab_region(x, y, w, h)
    if img is None:
        return False
    try:
        img.save(path, "PNG")
        return True
    except Exception as e:
        _log_error("ocr_save_region", e)
        return False


# ---------------------------------------------------------------------------
# Local OCR (Windows.Media.Ocr via winsdk)
# ---------------------------------------------------------------------------

def local_ocr_available():
    """True if offline OCR can run: winsdk imports and at least one recognizer
    language is installed."""
    sdk = _import_winsdk()
    if not sdk:
        return False
    try:
        langs = sdk["OcrEngine"].available_recognizer_languages
        return bool(langs and len(langs) > 0)
    except Exception as e:
        _log_error("ocr_available_probe", e)
        return False


def available_ocr_languages():
    """List of installed OCR recognizer language tags (e.g. ['en-US',
    'zh-Hans-CN']). Empty list if winsdk/OCR is unavailable."""
    sdk = _import_winsdk()
    if not sdk:
        return []
    try:
        langs = sdk["OcrEngine"].available_recognizer_languages
        return [l.language_tag for l in langs]
    except Exception as e:
        _log_error("ocr_list_languages", e)
        return []


def _is_cjk(ch):
    """True for a CJK ideograph or common CJK punctuation/kana/hangul char."""
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF      # Hiragana + Katakana
        or 0x3400 <= o <= 0x4DBF   # CJK Ext-A
        or 0x4E00 <= o <= 0x9FFF   # CJK Unified Ideographs
        or 0xAC00 <= o <= 0xD7A3   # Hangul syllables
        or 0xF900 <= o <= 0xFAFF   # CJK Compatibility Ideographs
        or 0x3000 <= o <= 0x303F   # CJK symbols and punctuation
        or 0xFF00 <= o <= 0xFFEF   # Fullwidth forms
    )


def _cjk_count(s):
    return sum(1 for ch in s if _is_cjk(ch))


def pick_ocr_result(results):
    """Choose the best OCR text from per-engine results.

    `results` is a list of (lang_tag, text). Strategy: if any engine captured
    CJK characters, the image contains CJK, so return the output of the engine
    that captured the most CJK (Chinese-native engines read CJK cleanly and
    Latin acceptably). Otherwise the text is Latin-only, so return the longest
    (cleanest) result, preferring an English engine on ties."""
    non_empty = [(tag, (txt or "").strip())
                 for tag, txt in results if (txt or "").strip()]
    if not non_empty:
        return ""

    with_cjk = [(tag, txt, _cjk_count(txt)) for tag, txt in non_empty]
    with_cjk = [row for row in with_cjk if row[2] > 0]
    if with_cjk:
        # Most CJK characters wins; ties broken by longer text.
        best = max(with_cjk, key=lambda r: (r[2], len(r[1])))
        return best[1]

    # Latin-only: prefer an English engine, else the longest result.
    def score(row):
        tag, txt = row
        return (1 if tag.lower().startswith("en") else 0, len(txt))

    return max(non_empty, key=score)[1]


def _target_language_tags(available, extra_langs=None):
    """Pick which installed engines to run: always include an English and a
    Chinese engine when present (the guaranteed baseline), plus any caller-
    supplied extra language tags (e.g. the system UI language) that are
    actually installed. Order is deterministic for stable test assertions."""
    avail_lower = {t.lower(): t for t in available}

    def find_prefix(prefix):
        for low, orig in avail_lower.items():
            if low.startswith(prefix):
                return orig
        return None

    chosen = []
    for prefix in ("en", "zh"):
        tag = find_prefix(prefix)
        if tag and tag not in chosen:
            chosen.append(tag)

    for tag in (extra_langs or []):
        # Match the requested extra against installed engines by prefix so
        # 'ja' or 'ja-JP' both resolve to the installed Japanese engine.
        low = tag.lower()
        match = avail_lower.get(low) or find_prefix(low.split("-")[0])
        if match and match not in chosen:
            chosen.append(match)

    # Nothing matched but engines exist: fall back to whatever is installed.
    if not chosen and available:
        chosen = list(available)
    return chosen


async def _recognize_all(sdk, image_path, tags):
    """Load the image once and run every requested engine against it, returning
    a list of (lang_tag, text)."""
    file = await sdk["StorageFile"].get_file_from_path_async(str(image_path))
    stream = await file.open_async(sdk["FileAccessMode"].READ)
    decoder = await sdk["BitmapDecoder"].create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()

    results = []
    for tag in tags:
        try:
            engine = sdk["OcrEngine"].try_create_from_language(
                sdk["Language"](tag))
            if engine is None:
                continue
            recognized = await engine.recognize_async(bitmap)
            results.append((tag, recognized.text or ""))
        except Exception as e:
            _log_error("ocr_recognize_engine", e)
    return results


def ocr_local(image_path, extra_langs=None):
    """Recognise text in `image_path` offline and return the best result.

    Guarantees English + Chinese coverage; `extra_langs` (e.g. the system UI
    language) is added only when the corresponding engine is installed. Returns
    an empty string if OCR is unavailable or nothing was recognised."""
    sdk = _import_winsdk()
    if not sdk:
        return ""
    try:
        available = available_ocr_languages()
        if not available:
            return ""
        tags = _target_language_tags(available, extra_langs)
        results = asyncio.run(_recognize_all(sdk, image_path, tags))
        return pick_ocr_result(results)
    except Exception as e:
        _log_error("ocr_local", e)
        return ""
