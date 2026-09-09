"""Build the bundled local dictionary from pinned, license-audited sources.

This developer tool is the only network-aware part of the dictionary feature.
The application itself only opens the resulting SQLite file read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cc_dictionary_store import SCHEMA_VERSION  # noqa: E402


BUILDER_VERSION = "builder-v3"
TEI = "{http://www.tei-c.org/ns/1.0}"
CEDICT_RE = re.compile(
    r"^(?P<traditional>\S+)\s+(?P<simplified>\S+)\s+"
    r"\[(?P<pinyin>[^\]]*)\]\s+/(?P<definitions>.*)/$")

_CURATED_FORMS = {
    ("n", "child"): ("children",),
    ("n", "city"): ("cities",),
    ("n", "foot"): ("feet",),
    ("n", "goose"): ("geese",),
    ("n", "man"): ("men",),
    ("n", "mouse"): ("mice",),
    ("n", "ox"): ("oxen",),
    ("n", "person"): ("people",),
    ("n", "tooth"): ("teeth",),
    ("n", "woman"): ("women",),
    ("v", "be"): ("am", "are", "is", "was", "were", "been", "being"),
    ("v", "begin"): ("begins", "began", "begun", "beginning"),
    ("v", "bring"): ("brings", "brought", "bringing"),
    ("v", "buy"): ("buys", "bought", "buying"),
    ("v", "carry"): ("carries", "carried", "carrying"),
    ("v", "come"): ("comes", "came", "coming"),
    ("v", "do"): ("does", "did", "done", "doing"),
    ("v", "drink"): ("drinks", "drank", "drunk", "drinking"),
    ("v", "eat"): ("eats", "ate", "eaten", "eating"),
    ("v", "fall"): ("falls", "fell", "fallen", "falling"),
    ("v", "feel"): ("feels", "felt", "feeling"),
    ("v", "find"): ("finds", "found", "finding"),
    ("v", "get"): ("gets", "got", "gotten", "getting"),
    ("v", "give"): ("gives", "gave", "given", "giving"),
    ("v", "go"): ("goes", "went", "gone", "going"),
    ("v", "have"): ("has", "had", "having"),
    ("v", "keep"): ("keeps", "kept", "keeping"),
    ("v", "know"): ("knows", "knew", "known", "knowing"),
    ("v", "leave"): ("leaves", "left", "leaving"),
    ("v", "lie"): ("lies", "lay", "lain", "lying"),
    ("v", "make"): ("makes", "made", "making"),
    ("v", "meet"): ("meets", "met", "meeting"),
    ("v", "read"): ("reads", "reading"),
    ("v", "run"): ("runs", "ran", "running"),
    ("v", "say"): ("says", "said", "saying"),
    ("v", "see"): ("sees", "saw", "seen", "seeing"),
    ("v", "speak"): ("speaks", "spoke", "spoken", "speaking"),
    ("v", "stop"): ("stops", "stopped", "stopping"),
    ("v", "take"): ("takes", "took", "taken", "taking"),
    ("v", "think"): ("thinks", "thought", "thinking"),
    ("v", "walk"): ("walks", "walked", "walking"),
    ("v", "write"): ("writes", "wrote", "written", "writing"),
    ("adj", "bad"): ("worse", "worst"),
    ("adj", "far"): ("farther", "farthest", "further", "furthest"),
    ("adj", "good"): ("better", "best"),
    ("adj", "happy"): ("happier", "happiest"),
    ("adj", "little"): ("less", "least"),
    ("adj", "many"): ("more", "most"),
    ("adj", "much"): ("more", "most"),
}
VARIANT_RE = re.compile(r"U\+([0-9A-F]{4,6})")


@dataclass(frozen=True)
class Source:
    source_id: str
    filename: str
    url: str
    sha256: str
    label: str
    version: str
    license: str


SOURCES = (
    Source(
        "wikdict-eng-zho", "eng-zho.tei",
        "https://download.wikdict.com/dictionaries/tei/recommended/eng-zho.tei",
        "9914a68a03fa5f30c627bbc94897130406ff2b71deda3094ef9d3f548d0eb879",
        "WikDict eng-zho", "2025.11.21", "CC BY-SA 3.0 Unported"),
    Source(
        "cc-cedict", "cedict_ts.u8",
        "https://raw.githubusercontent.com/Henry-W/CC-CEDICT/"
        "af4a8319d460b5ba233444e73e0086e939a53acd/src/cedict/cedict_ts.u8",
        "43d1f686ce9f6b208e43b606655d3f6eb757917283bf408b460ba157f939f5e7",
        "CC-CEDICT", "2017-04-28", "CC BY-SA 3.0 Unported"),
    Source(
        "cow", "omw-cmn-2.0.tar.xz",
        "https://github.com/omwn/omw-data/releases/download/v2.0/"
        "omw-cmn-2.0.tar.xz",
        "7d07af60a6ced0cedc4ca114d0b60a796d3f138df0f3be21f6322e53d004e91c",
        "Chinese Open Wordnet", "OMW 2.0", "Chinese Open Wordnet License"),
    Source(
        "pwn", "omw-en-2.0.tar.xz",
        "https://github.com/omwn/omw-data/releases/download/v2.0/"
        "omw-en-2.0.tar.xz",
        "0e09dfb7f096bc3f10b9de68ffecf13839fa22ae46fd9b227cec890d204ca1dc",
        "Princeton WordNet", "3.0 / OMW 2.0", "Princeton WordNet 3.0 License"),
    Source(
        "unihan", "Unihan.zip",
        "https://www.unicode.org/Public/17.0.0/ucd/Unihan.zip",
        "f7a48b2b545acfaa77b2d607ae28747404ce02baefee16396c5d2d7a8ef34b5e",
        "Unicode Unihan", "17.0.0", "Unicode License v3"),
)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    version TEXT NOT NULL,
    license TEXT NOT NULL,
    url TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE TABLE entries (
    id INTEGER PRIMARY KEY,
    headword TEXT NOT NULL,
    headword_norm TEXT NOT NULL,
    language TEXT NOT NULL,
    pronunciation TEXT,
    part_of_speech TEXT,
    source_id TEXT NOT NULL REFERENCES sources(id),
    provenance TEXT NOT NULL,
    priority INTEGER NOT NULL
);
CREATE TABLE senses (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES entries(id),
    ordinal INTEGER NOT NULL,
    definition TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(id),
    provenance TEXT NOT NULL
);
CREATE TABLE forms (
    form_norm TEXT NOT NULL,
    entry_id INTEGER NOT NULL REFERENCES entries(id),
    form TEXT NOT NULL,
    provenance TEXT NOT NULL,
    PRIMARY KEY (form_norm, entry_id, form)
);
CREATE TABLE aliases (
    alias_norm TEXT NOT NULL,
    entry_id INTEGER NOT NULL REFERENCES entries(id),
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    provenance TEXT NOT NULL,
    PRIMARY KEY (alias_norm, entry_id, alias, alias_type)
);
CREATE INDEX entries_headword_norm ON entries(headword_norm, priority);
CREATE INDEX senses_entry_id ON senses(entry_id, ordinal);
CREATE INDEX forms_form_norm ON forms(form_norm);
CREATE INDEX aliases_alias_norm ON aliases(alias_norm);
"""


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip().casefold()


def derived_english_forms(headword: str, part_of_speech: str):
    """Return reviewed build-time forms; never used as runtime stemming."""
    word = (headword or "").strip()
    pos = (part_of_speech or "").strip().lower()
    if not re.fullmatch(r"[a-z]{3,}", word):
        return ()
    return _CURATED_FORMS.get((pos, word), ())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def acquire(source: Source, cache: Path, allow_download: bool) -> Path:
    path = cache / source.filename
    if not path.exists():
        if not allow_download:
            raise FileNotFoundError(
                "%s is missing; rerun with --download" % path)
        request = urllib.request.Request(
            source.url, headers={"User-Agent": "CC-Translate-dictionary-builder/1"})
        partial = path.with_suffix(path.suffix + ".partial")
        with urllib.request.urlopen(request, timeout=60) as response:
            with partial.open("wb") as output:
                shutil.copyfileobj(response, output)
        partial.replace(path)
    actual = sha256(path)
    if actual != source.sha256:
        raise ValueError(
            "%s SHA-256 mismatch: %s != %s" % (
                source.filename, actual, source.sha256))
    return path


def insert_entry(
        conn, *, headword, language, pronunciation, part_of_speech,
        source_id, provenance, priority, senses, forms=(), aliases=()):
    headword = (headword or "").strip()
    clean_senses = [
        (definition.strip(), sense_source, sense_provenance)
        for definition, sense_source, sense_provenance in senses
        if definition and definition.strip()]
    if not headword or not clean_senses:
        return None
    cursor = conn.execute(
        "INSERT INTO entries "
        "(headword, headword_norm, language, pronunciation, part_of_speech, "
        "source_id, provenance, priority) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (headword, normalize(headword), language, pronunciation or None,
         part_of_speech or None, source_id, provenance, priority))
    entry_id = cursor.lastrowid
    conn.executemany(
        "INSERT INTO senses "
        "(entry_id, ordinal, definition, source_id, provenance) "
        "VALUES (?, ?, ?, ?, ?)",
        [(entry_id, ordinal, definition, sense_source, sense_provenance)
         for ordinal, (definition, sense_source, sense_provenance)
         in enumerate(clean_senses, 1)])
    conn.executemany(
        "INSERT OR IGNORE INTO forms "
        "(form_norm, entry_id, form, provenance) VALUES (?, ?, ?, ?)",
        [(normalize(form), entry_id, form, form_provenance)
         for form, form_provenance in forms
         if form and normalize(form) != normalize(headword)])
    conn.executemany(
        "INSERT OR IGNORE INTO aliases "
        "(alias_norm, entry_id, alias, alias_type, provenance) "
        "VALUES (?, ?, ?, ?, ?)",
        [(normalize(alias), entry_id, alias, alias_type, alias_provenance)
         for alias, alias_type, alias_provenance in aliases
         if alias and normalize(alias) != normalize(headword)])
    return entry_id


def build_wikdict(conn, path: Path) -> None:
    context = ET.iterparse(path, events=("end",))
    for _, elem in context:
        if elem.tag != TEI + "entry":
            continue
        form = elem.find(TEI + "form")
        orth = form.findtext(TEI + "orth") if form is not None else None
        if not orth:
            elem.clear()
            continue
        pronunciations = []
        if form is not None:
            pronunciations = [
                text for text in (
                    (pron.text or "").strip()
                    for pron in form.findall(TEI + "pron"))
                if text]
        pos = elem.findtext(TEI + "gramGrp/" + TEI + "pos")
        translations = []
        for citation in elem.findall(
                ".//" + TEI + "cit[@type='trans']"):
            quote = citation.findtext(TEI + "quote")
            if quote and quote.strip() and quote.strip() not in translations:
                translations.append(quote.strip())
        forms = []
        for inflected in form.findall(TEI + "form[@type='infl']"):
            value = inflected.findtext(TEI + "orth")
            if value:
                forms.append((value.strip(), "TEI inflected form"))
        forms.extend((
            value,
            "Derived curated English form from WikDict POS %s" % pos,
        ) for value in derived_english_forms(orth, pos))
        insert_entry(
            conn,
            headword=orth,
            language="en",
            pronunciation="; ".join(dict.fromkeys(pronunciations)),
            part_of_speech=pos,
            source_id="wikdict-eng-zho",
            provenance="TEI entry:%s" % orth,
            priority=10,
            senses=[(value, "wikdict-eng-zho", "TEI translation:%s" % orth)
                    for value in translations],
            forms=forms,
        )
        elem.clear()
    conn.execute(
        "DELETE FROM forms WHERE provenance LIKE 'Derived curated %' "
        "AND form_norm IN ("
        "SELECT f.form_norm FROM forms f "
        "JOIN entries e ON e.id = f.entry_id "
        "GROUP BY f.form_norm HAVING COUNT(DISTINCT e.headword_norm) > 1)")


def build_cedict(conn, path: Path) -> None:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if raw.startswith("#"):
                continue
            match = CEDICT_RE.match(raw.strip())
            if not match:
                raise ValueError(
                    "invalid CC-CEDICT record at line %s" % line_number)
            data = match.groupdict()
            definitions = [
                value.strip() for value in data["definitions"].split("/")
                if value.strip()]
            aliases = []
            if data["traditional"] != data["simplified"]:
                aliases.append((
                    data["traditional"], "traditional",
                    "CC-CEDICT line %s traditional form" % line_number))
            provenance = "CC-CEDICT line %s" % line_number
            insert_entry(
                conn,
                headword=data["simplified"],
                language="zh",
                pronunciation=data["pinyin"],
                part_of_speech=None,
                source_id="cc-cedict",
                provenance=provenance,
                priority=10,
                senses=[(value, "cc-cedict", provenance)
                        for value in definitions],
                aliases=aliases,
            )


def extract_member(archive: Path, member: str, destination: Path) -> Path:
    with tarfile.open(archive, "r:xz") as package:
        info = package.getmember(member)
        extracted = package.extractfile(info)
        if extracted is None:
            raise ValueError("missing archive member %s" % member)
        with destination.open("wb") as output:
            shutil.copyfileobj(extracted, output)
    return destination


def pwn_definitions(path: Path) -> dict[str, str]:
    definitions = {}
    for _, elem in ET.iterparse(path, events=("end",)):
        if elem.tag != "Synset":
            continue
        synset_id = elem.get("id", "")
        definition = elem.findtext("Definition")
        if synset_id.startswith("omw-en-") and definition:
            definitions[synset_id[len("omw-en-"):]] = definition.strip()
        elem.clear()
    return definitions


def build_cow(conn, cmn_path: Path, en_path: Path) -> None:
    definitions = pwn_definitions(en_path)
    for _, elem in ET.iterparse(cmn_path, events=("end",)):
        if elem.tag != "LexicalEntry":
            continue
        lemma = elem.find("Lemma")
        if lemma is None:
            elem.clear()
            continue
        headword = lemma.get("writtenForm", "")
        pos = lemma.get("partOfSpeech")
        senses = []
        for sense in elem.findall("Sense"):
            synset_id = sense.get("synset", "")
            suffix = synset_id[len("omw-cmn-"):] if synset_id.startswith(
                "omw-cmn-") else ""
            definition = definitions.get(suffix)
            if definition:
                senses.append((
                    definition, "pwn",
                    "PWN synset omw-en-%s aligned by COW sense %s" % (
                        suffix, sense.get("id", ""))))
        insert_entry(
            conn,
            headword=headword,
            language="zh",
            pronunciation=None,
            part_of_speech=pos,
            source_id="cow",
            provenance="COW lexical entry %s" % elem.get("id", ""),
            priority=30,
            senses=senses,
        )
        elem.clear()


def unihan_records(archive: Path):
    fields = {}
    with zipfile.ZipFile(archive) as package:
        with package.open("Unihan_Readings.txt") as raw:
            for encoded in raw:
                line = encoded.decode("utf-8").rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                codepoint, field, value = line.split("\t", 2)
                if field in ("kDefinition", "kMandarin"):
                    fields.setdefault(codepoint, {})[field] = value
        with package.open("Unihan_Variants.txt") as raw:
            for encoded in raw:
                line = encoded.decode("utf-8").rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                codepoint, field, value = line.split("\t", 2)
                if field in ("kSimplifiedVariant", "kTraditionalVariant"):
                    variants = [
                        chr(int(match.group(1), 16))
                        for match in VARIANT_RE.finditer(value)]
                    fields.setdefault(codepoint, {}).setdefault(
                        field, []).extend(variants)
    return fields


def build_unihan(conn, archive: Path) -> None:
    for codepoint, fields in unihan_records(archive).items():
        definition = fields.get("kDefinition")
        if not definition:
            continue
        character = chr(int(codepoint[2:], 16))
        aliases = []
        for field, alias_type in (
                ("kSimplifiedVariant", "simplified"),
                ("kTraditionalVariant", "traditional")):
            aliases.extend((
                alias, alias_type, "%s %s" % (codepoint, field))
                for alias in fields.get(field, []))
        insert_entry(
            conn,
            headword=character,
            language="zh",
            pronunciation=fields.get("kMandarin"),
            part_of_speech=None,
            source_id="unihan",
            provenance="%s kDefinition" % codepoint,
            priority=40,
            senses=[(definition, "unihan", "%s kDefinition" % codepoint)],
            aliases=aliases,
        )


def content_fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(BUILDER_VERSION.encode("ascii"))
    for source in SOURCES:
        digest.update(source.source_id.encode("ascii"))
        digest.update(source.sha256.encode("ascii"))
    return digest.hexdigest()


def build(output: Path, cache: Path, allow_download: bool) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    paths = {
        source.source_id: acquire(source, cache, allow_download)
        for source in SOURCES
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_suffix(output.suffix + ".building")
    if temp_output.exists():
        temp_output.unlink()
    with tempfile.TemporaryDirectory(prefix="cc-dictionary-") as temp_dir:
        temp = Path(temp_dir)
        cmn_xml = extract_member(
            paths["cow"], "omw-cmn/omw-cmn.xml", temp / "omw-cmn.xml")
        en_xml = extract_member(
            paths["pwn"], "omw-en/omw-en.xml", temp / "omw-en.xml")
        conn = sqlite3.connect(temp_output)
        try:
            conn.executescript(SCHEMA)
            conn.executemany(
                "INSERT INTO sources "
                "(id, label, version, license, url, sha256) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(source.source_id, source.label, source.version,
                  source.license, source.url, source.sha256)
                 for source in SOURCES])
            build_wikdict(conn, paths["wikdict-eng-zho"])
            build_cedict(conn, paths["cc-cedict"])
            build_cow(conn, cmn_xml, en_xml)
            build_unihan(conn, paths["unihan"])
            data_version = "wikdict-2025.11.21+forms-2+" \
                           "cedict-2017.04.28+omw-2.0+unihan-17.0.0"
            conn.executemany(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                (
                    ("schema_version", SCHEMA_VERSION),
                    ("data_version", data_version),
                    ("content_sha256", content_fingerprint()),
                    ("builder_version", BUILDER_VERSION),
                ))
            conn.commit()
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise ValueError("built database integrity check failed: %s" % result)
            conn.execute("VACUUM")
        finally:
            conn.close()
    temp_output.replace(output)
    artifact_sha256 = sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        artifact_sha256 + "  " + output.name + "\n", encoding="ascii")
    print("Built %s" % output)
    print("SHA-256 %s" % artifact_sha256)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache", type=Path, required=True,
        help="directory containing pinned source artifacts")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "data" / "dictionary" / "cc_dictionary.sqlite3")
    parser.add_argument(
        "--download", action="store_true",
        help="download missing pinned artifacts (never used by the app)")
    args = parser.parse_args()
    build(args.output.resolve(), args.cache.resolve(), args.download)


if __name__ == "__main__":
    main()
