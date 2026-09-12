"""Shared direction routing and prompts; callers supply the UI language."""


LANGUAGES = {
    "zh": ("\u4e2d\u6587", "Simplified Chinese"),
    "en": ("\u82f1\u6587", "English"),
    "ja": ("\u65e5\u6587", "Japanese"),
    "ko": ("\u97e9\u6587", "Korean"),
    "fr": ("\u6cd5\u6587", "French"),
    "de": ("\u5fb7\u6587", "German"),
    "es": ("\u897f\u73ed\u7259\u6587", "Spanish"),
}

DIRECTION_MODES = {
    "auto": ("Translate the user's text. If it is Chinese, translate to natural "
             "English; otherwise translate to natural Simplified Chinese."),
}
for _code, (_zh_name, _en_name) in LANGUAGES.items():
    DIRECTION_MODES[f"to_{_code}"] = (
        f"Translate the user's text into natural {_en_name}.")


def auto_direction_prompt(app_language):
    """Build the auto-mode routing prompt from app UI language."""
    if app_language == "en_US":
        return ("Translate the user's text. If it contains any meaningful "
                "English prose, translate the WHOLE text into natural Simplified "
                "Chinese. Only if it has essentially no English (e.g. it is "
                "Chinese or another language) translate it into natural English.")
    return ("Translate the user's text. If it contains any meaningful Chinese "
            "(even when mixed with English words, code or punctuation), translate "
            "the WHOLE text into natural English. Only if it has essentially no "
            "Chinese translate it into natural Simplified Chinese.")


# Keep the established routing threshold, including mixed Chinese/English prose.
CJK_SOURCE_RATIO = 0.34


def _cjk_latin_counts(text):
    """(cjk, latin) character counts. `latin` is ASCII English letters ONLY;
    note str.isalpha() also counts CJK as alphabetic, so it cannot be used to
    tell the two scripts apart."""
    t = text or ""
    cjk = sum(1 for c in t if ord(c) > 0x2E7F)
    latin = sum(1 for c in t if ("a" <= c <= "z") or ("A" <= c <= "Z"))
    return cjk, latin


def source_is_cjk(text):
    """True if `text` reads as a CJK (Chinese) source for auto-routing.

    Robust to English words/code embedded in Chinese prose: CJK need only be a
    meaningful fraction of the Latin letters, not outnumber them. (The old
    ``cjk >= letters`` test flipped to non-CJK the moment ANY English letter
    appeared, so a Chinese selection peppered with code got translated back
    into Chinese.) A stray CJK glyph in otherwise-English text still reads as
    English via the relative floor."""
    cjk, latin = _cjk_latin_counts(text)
    return cjk >= 2 and cjk >= latin * CJK_SOURCE_RATIO


def source_has_english(text):
    """True if `text` has a meaningful amount of Latin (English) prose. The
    en-UI auto pivot is English -> Chinese; else -> English, so predominantly-
    English text (even with embedded CJK) routes to Chinese. Symmetric to
    source_is_cjk."""
    cjk, latin = _cjk_latin_counts(text)
    return latin >= 2 and latin >= cjk * CJK_SOURCE_RATIO


def resolve_target_lang(mode, app_language, text):
    """Resolve the concrete target-language code (a LANGUAGES key) a translation
    will produce, so the summary heading + body can be written in the SAME
    language as the translation instead of the app's UI language.

    - Explicit ``to_xx`` modes translate into a fixed language: return ``xx``.
    - ``auto`` routes by the SOURCE language, so the target is only known once
      we see the text. Mirror the auto routing prompt exactly:
        * zh UI: Chinese source -> ``en``; anything else -> ``zh``.
        * en UI: English (Latin) source -> ``zh``; anything else -> ``en``.
      Source language is detected by CJK-vs-Latin character balance, the same
      cheap heuristic used elsewhere (ord(c) > 0x2E7F ~= CJK)."""
    if mode and mode.startswith("to_"):
        code = mode[3:]
        if code in LANGUAGES:
            return code
    if app_language == "en_US":
        # en UI pivot: any meaningful English -> Chinese; else -> English.
        return "zh" if source_has_english(text) else "en"
    # Kana and Hangul disambiguate Japanese/Korean even with embedded Han.
    has_ja_ko_script = any(
        "\u3040" <= char <= "\u30ff"
        or "\uff65" <= char <= "\uff9f"
        or "\uac00" <= char <= "\ud7af"
        for char in text
    )
    if has_ja_ko_script:
        return "zh"
    return "en" if source_is_cjk(text) else "zh"


def direction_prompt(mode, app_language):
    """Resolve the effective direction prompt for a mode and app language."""
    if mode == "auto":
        return auto_direction_prompt(app_language)
    return DIRECTION_MODES.get(mode, DIRECTION_MODES["auto"])
