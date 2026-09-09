"""Render structured local dictionary results into CC Translate Markdown-lite."""

from __future__ import annotations

import re
import unicodedata

from cc_dictionary import DictionaryResult
from cc_rich import (
    INSTANT_RESULT_MARKER, encode_more_senses, encode_pronunciation,
    encode_source_details,
)


FORMATTER_VERSION = "format-v8"
INITIAL_SENSE_LIMIT = 5

_NUMBERED_PINYIN_SYLLABLE = re.compile(r"([A-Za-zÜü:]+)([1-5])")
_TONE_MARKS = {
    "a": "aāáǎà",
    "e": "eēéěè",
    "i": "iīíǐì",
    "o": "oōóǒò",
    "u": "uūúǔù",
    "ü": "üǖǘǚǜ",
}
_COMBINING_TONES = ("", "\u0304", "\u0301", "\u030c", "\u0300")
_SPECIALIZED_SENSE = re.compile(
    r"\b(?:archaic|dated|dialect|historical|literary|obsolete|rare|"
    r"slang|surname|variant of)\b|(?:古语|古文|方言|旧称|罕见|姓氏|异体)",
    re.IGNORECASE,
)
_SPECIALIZED_POS = {
    "name", "proper noun", "proper-noun", "surname",
}


def numbered_pinyin_to_tone_marks(value: str) -> str:
    """Convert source-provided numbered Pinyin without changing other text."""
    def convert(match):
        syllable = (match.group(1)
                    .replace("u:", "ü").replace("U:", "Ü")
                    .replace("v", "ü").replace("V", "Ü"))
        tone = int(match.group(2))
        if tone == 5:
            return syllable

        lowered = syllable.lower()
        if "a" in lowered:
            index = lowered.index("a")
        elif "e" in lowered:
            index = lowered.index("e")
        elif "ou" in lowered:
            index = lowered.index("o")
        else:
            index = next(
                (i for i in range(len(syllable) - 1, -1, -1)
                 if lowered[i] in _TONE_MARKS),
                -1,
            )
        if index >= 0:
            vowel = syllable[index]
            marked = _TONE_MARKS[vowel.lower()][tone]
            if vowel.isupper():
                marked = marked.upper()
            return syllable[:index] + marked + syllable[index + 1:]

        consonant = next(
            (i for i, char in enumerate(lowered) if char in "mn"), -1)
        if consonant >= 0:
            marked = unicodedata.normalize(
                "NFC", syllable[consonant] + _COMBINING_TONES[tone])
            return syllable[:consonant] + marked + syllable[consonant + 1:]
        return syllable

    return _NUMBERED_PINYIN_SYLLABLE.sub(convert, value or "")


def _sense_sort_key(index_and_sense):
    index, sense = index_and_sense
    definition = " ".join(sense.definition.split())
    return (
        bool(_SPECIALIZED_SENSE.search(definition)),
        len(definition) > 100,
        index,
    )


def _presentation_groups(result):
    grouped = {}
    for entry_index, entry in enumerate(result.entries):
        pronunciation = numbered_pinyin_to_tone_marks(
            entry.pronunciation or "")
        key = (
            entry.headword,
            pronunciation,
            entry.part_of_speech or "",
        )
        group = grouped.setdefault(key, {
            "headword": entry.headword,
            "pronunciation": pronunciation,
            "part_of_speech": entry.part_of_speech or "",
            "first_index": entry_index,
            "senses": [],
        })
        seen = {
            (sense.source_id,
             " ".join(sense.definition.split()).casefold())
            for sense in group["senses"]
        }
        for sense in entry.senses:
            sense_key = (
                sense.source_id,
                " ".join(sense.definition.split()).casefold(),
            )
            if sense_key not in seen:
                group["senses"].append(sense)
                seen.add(sense_key)
    groups = list(grouped.values())
    for group in groups:
        group["senses"] = [
            sense for _, sense in sorted(
                enumerate(group["senses"]), key=_sense_sort_key)
        ]
    groups.sort(key=lambda group: (
        group["part_of_speech"].casefold() in _SPECIALIZED_POS,
        group["first_index"],
    ))
    return groups


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
    sources = []
    for entry in result.entries:
        detailed = [(
            entry.source_id, entry.source_label, entry.source_version,
            entry.source_license,
        )] + [(
            sense.source_id, sense.source_label, sense.source_version,
            sense.source_license,
        ) for sense in entry.senses]
        for source_id, source_label, source_version, source_license in detailed:
            source = {
                "id": source_id,
                "label": source_label,
                "version": source_version or "",
                "license": source_license or "",
            }
            if source not in sources:
                sources.append(source)
    if sources:
        lines.extend(("", encode_source_details(sources)))
    return "\n".join(lines).strip()


__all__ = [
    "FORMATTER_VERSION",
    "INITIAL_SENSE_LIMIT",
    "format_dictionary_result",
    "numbered_pinyin_to_tone_marks",
]
