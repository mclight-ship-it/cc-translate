"""Shared summary eligibility heuristics, thresholds and target-language prompts."""

import re

from cc_direction import LANGUAGES


# Text at/above this length streams (progressive render) rather than one-shot,
# and is also the minimum length for the long-text summary feature. Unified so
# "long enough to stream" and "long enough to summarize" mean the same thing.
STREAM_MIN_CHARS = 400
SUMMARY_MIN_CHARS = STREAM_MIN_CHARS

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


def codex_summary_instruction(target_lang):
    """Compact, benchmarked long-text contract for Codex only."""
    sm, tr = summary_headings(target_lang)
    lang_name = LANGUAGES.get(target_lang, (None, "the target language"))[1]
    return (
        f"Translate to {lang_name}. Start with `## {sm}` and 3-5 short `- ` "
        f"bullets, then `## {tr}` and the complete translation. Preserve code "
        "and identifiers exactly. Output only those sections."
    )
