"""Source-grounded dictionary grouping and plain presentation without UI imports."""

import re
import unicodedata


PLAIN_FORMATTER_VERSION = "native-plain-v1"
_NUMBERED_PINYIN_SYLLABLE = re.compile(r"([A-Za-z\u00dc\u00fc:]+)([1-5])")
_TONE_MARKS = {
    "a": "a\u0101\u00e1\u01ce\u00e0", "e": "e\u0113\u00e9\u011b\u00e8",
    "i": "i\u012b\u00ed\u01d0\u00ec", "o": "o\u014d\u00f3\u01d2\u00f2",
    "u": "u\u016b\u00fa\u01d4\u00f9", "\u00fc": "\u00fc\u01d6\u01d8\u01da\u01dc",
}
_COMBINING_TONES = ("", "\u0304", "\u0301", "\u030c", "\u0300")
_SPECIALIZED_SENSE = re.compile(
    r"\b(?:archaic|dated|dialect|historical|literary|obsolete|rare|"
    r"slang|surname|variant of)\b|(?:\u53e4\u8bed|\u53e4\u6587|\u65b9\u8a00|"
    r"\u65e7\u79f0|\u7f55\u89c1|\u59d3\u6c0f|\u5f02\u4f53)", re.IGNORECASE,
)
_SPECIALIZED_POS = {"name", "proper noun", "proper-noun", "surname"}


def numbered_pinyin_to_tone_marks(value: str) -> str:
    def convert(match):
        syllable = (match.group(1)
                    .replace("u:", "\u00fc").replace("U:", "\u00dc")
                    .replace("v", "\u00fc").replace("V", "\u00dc"))
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
                 if lowered[i] in _TONE_MARKS), -1)
        if index >= 0:
            vowel = syllable[index]
            marked = _TONE_MARKS[vowel.lower()][tone]
            if vowel.isupper():
                marked = marked.upper()
            return syllable[:index] + marked + syllable[index + 1:]
        consonant = next((i for i, char in enumerate(lowered) if char in "mn"), -1)
        if consonant >= 0:
            marked = unicodedata.normalize("NFC", syllable[consonant] + _COMBINING_TONES[tone])
            return syllable[:consonant] + marked + syllable[consonant + 1:]
        return syllable
    return _NUMBERED_PINYIN_SYLLABLE.sub(convert, value or "")


def _sense_sort_key(index_and_sense):
    index, sense = index_and_sense
    definition = " ".join(sense.definition.split())
    return bool(_SPECIALIZED_SENSE.search(definition)), len(definition) > 100, index


def presentation_groups(result):
    grouped = {}
    for entry_index, entry in enumerate(result.entries):
        pronunciation = numbered_pinyin_to_tone_marks(entry.pronunciation or "")
        key = entry.headword, pronunciation, entry.part_of_speech or ""
        group = grouped.setdefault(key, {
            "headword": entry.headword, "pronunciation": pronunciation,
            "part_of_speech": entry.part_of_speech or "", "first_index": entry_index, "senses": [],
        })
        seen = {(sense.source_id, " ".join(sense.definition.split()).casefold()) for sense in group["senses"]}
        for sense in entry.senses:
            sense_key = sense.source_id, " ".join(sense.definition.split()).casefold()
            if sense_key not in seen:
                group["senses"].append(sense)
                seen.add(sense_key)
    groups = list(grouped.values())
    for group in groups:
        group["senses"] = [sense for _, sense in sorted(enumerate(group["senses"]), key=_sense_sort_key)]
    groups.sort(key=lambda group: (
        group["part_of_speech"].casefold() in _SPECIALIZED_POS, group["first_index"],
    ))
    return groups


def source_details(result):
    sources = []
    for entry in result.entries:
        detailed = [(entry.source_id, entry.source_label, entry.source_version, entry.source_license)] + [
            (sense.source_id, sense.source_label, sense.source_version, sense.source_license) for sense in entry.senses
        ]
        for source_id, source_label, source_version, source_license in detailed:
            source = {"id": source_id, "label": source_label, "version": source_version or "",
                      "license": source_license or ""}
            if source not in sources:
                sources.append(source)
    return sources


def format_dictionary_plain(result, app_language):
    if app_language not in ("en_US", "zh_CN"):
        raise ValueError("unsupported dictionary presentation language")
    lines = [result.headword]
    for group in presentation_groups(result):
        heading = [group["headword"]] if group["headword"] != result.headword else []
        heading.extend(value for value in (group["pronunciation"], group["part_of_speech"]) if value)
        lines.append("")
        if heading:
            lines.append(" - ".join(heading))
        lines.extend("- " + sense.definition for sense in group["senses"])
    sources = source_details(result)
    if sources:
        lines.extend(("", "Sources & licenses:" if app_language == "en_US" else "\u6765\u6e90\u4e0e\u8bb8\u53ef\uff1a"))
        for source in sources:
            lines.append("- " + " | ".join(value for value in (
                source["label"], source["version"], source["license"]) if value))
    return "\n".join(lines).strip()
