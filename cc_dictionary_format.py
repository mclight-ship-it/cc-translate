"""Render structured local dictionary results into CC Translate Markdown-lite."""

from __future__ import annotations

from cc_dictionary_lookup import DictionaryResult
from cc_dictionary_presentation import (
    _NUMBERED_PINYIN_SYLLABLE, _TONE_MARKS, _COMBINING_TONES, _SPECIALIZED_SENSE,
    _SPECIALIZED_POS, _sense_sort_key, numbered_pinyin_to_tone_marks,
    presentation_groups as _presentation_groups, source_details,
)
from cc_rich import (
    INSTANT_RESULT_MARKER, encode_more_senses, encode_pronunciation,
    encode_source_details,
)


FORMATTER_VERSION = "format-v8"
INITIAL_SENSE_LIMIT = 5

def format_dictionary_result(
        result: DictionaryResult, *, expanded: bool = False) -> str:
    """Render only fields carried by the source rows."""
    lines = ["## %s %s" % (result.headword, INSTANT_RESULT_MARKER)]
    groups = _presentation_groups(result)
    pronunciations = tuple(dict.fromkeys(
        group["pronunciation"] for group in groups
        if group["pronunciation"]))
    shared_pronunciation = pronunciations[0] if len(pronunciations) == 1 else ""
    if shared_pronunciation:
        lines.append(encode_pronunciation(shared_pronunciation))

    total_senses = sum(len(group["senses"]) for group in groups)
    allocations = [len(group["senses"]) for group in groups]
    if not expanded and total_senses > INITIAL_SENSE_LIMIT:
        allocations = [
            1 if index < INITIAL_SENSE_LIMIT and group["senses"] else 0
            for index, group in enumerate(groups)
        ]
        remaining = max(0, INITIAL_SENSE_LIMIT - sum(allocations))
        for index, group in enumerate(groups):
            extra = min(
                remaining, len(group["senses"]) - allocations[index])
            allocations[index] += extra
            remaining -= extra
            if not remaining:
                break
    hidden_count = total_senses - sum(allocations)
    for group, allocation in zip(groups, allocations):
        senses = group["senses"][:allocation]
        if not senses:
            continue
        heading_parts = []
        if group["headword"] != result.headword:
            heading_parts.append("**%s**" % group["headword"])
        if len(pronunciations) > 1 and group["pronunciation"]:
            if heading_parts:
                lines.extend(("", "  ·  ".join(heading_parts)))
                heading_parts = []
            lines.extend(("", encode_pronunciation(group["pronunciation"])))
        if group["part_of_speech"]:
            heading_parts.append("*%s*" % group["part_of_speech"])
        if heading_parts:
            lines.extend(("", "  ·  ".join(heading_parts)))
        for sense in senses:
            lines.append("- %s" % sense.definition)
    if hidden_count:
        expanded_text = format_dictionary_result(result, expanded=True)
        lines.extend(("", encode_more_senses(hidden_count, expanded_text)))
    sources = source_details(result)
    if sources:
        lines.extend(("", encode_source_details(sources)))
    return "\n".join(lines).strip()


__all__ = [
    "FORMATTER_VERSION",
    "INITIAL_SENSE_LIMIT",
    "format_dictionary_result",
    "numbered_pinyin_to_tone_marks",
]
