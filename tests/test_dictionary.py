import os
import hashlib
import sqlite3
import tempfile
import threading
import unittest

from cc_dictionary import (
    DEVELOPMENT_DICTIONARY_PATH,
    DictionaryEntry, DictionaryResult, DictionarySense,
    LocalDictionary, normalize_query,
)
from cc_dictionary_format import (
    INITIAL_SENSE_LIMIT, format_dictionary_result,
    numbered_pinyin_to_tone_marks,
)
from cc_rich import decode_source_details, iter_rich_segments
from cc_dictionary_store import DictionaryStore
from tools.build_dictionary import (
    SCHEMA, SOURCES, derived_english_forms, insert_entry,
)


class DictionaryFixture:
    def __init__(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        conn = sqlite3.connect(self.path)
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)",
            ("fixture", "Fixture source", "1", "Test license",
             "https://example.invalid", "a" * 64))
        insert_entry(
            conn,
            headword="run",
            language="en",
            pronunciation="/rʌn/",
            part_of_speech="verb",
            source_id="fixture",
            provenance="fixture:run",
            priority=10,
            senses=[("跑", "fixture", "fixture:run:sense")],
            forms=[("ran", "fixture:run:past")],
        )
        insert_entry(
            conn,
            headword="中国",
            language="zh",
            pronunciation="Zhōngguó",
            part_of_speech=None,
            source_id="fixture",
            provenance="fixture:china",
            priority=10,
            senses=[("China", "fixture", "fixture:china:sense")],
            aliases=[("中國", "traditional", "fixture:china:traditional")],
        )
        insert_entry(
            conn,
            headword="龘",
            language="zh",
            pronunciation="dá",
            part_of_speech=None,
            source_id="fixture",
            provenance="U+9F98 kDefinition",
            priority=40,
            senses=[(
                "the appearance of a dragon walking",
                "fixture", "U+9F98 kDefinition")],
        )
        conn.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            (
                ("schema_version", "1"),
                ("data_version", "fixture-1"),
                ("content_sha256", "b" * 64),
            ))
        conn.commit()
        conn.close()

    def close(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


class TestDictionaryNormalization(unittest.TestCase):
    def test_nfkc_casefold_and_trim(self):
        self.assertEqual(normalize_query("  ＡＢＣ  "), "abc")
        self.assertEqual(normalize_query("Straße"), "strasse")

    def test_empty_query(self):
        self.assertEqual(normalize_query(" \t"), "")

    def test_conservative_build_time_english_forms(self):
        self.assertEqual(derived_english_forms("child", "n"), ("children",))
        self.assertEqual(derived_english_forms("city", "n"), ("cities",))
        self.assertEqual(derived_english_forms("analysis", "n"), ())
        self.assertEqual(
            derived_english_forms("carry", "v"),
            ("carries", "carried", "carrying"),
        )
        self.assertEqual(
            derived_english_forms("stop", "v"),
            ("stops", "stopped", "stopping"),
        )
        self.assertIn("running", derived_english_forms("run", "v"))
        self.assertEqual(
            derived_english_forms("happy", "adj"),
            ("happier", "happiest"),
        )
        self.assertEqual(derived_english_forms("New York", "n"), ())
        self.assertEqual(derived_english_forms("information", "n"), ())


class TestPinyinFormatting(unittest.TestCase):
    def test_numbered_pinyin_uses_tone_marks(self):
        self.assertEqual(
            numbered_pinyin_to_tone_marks("Zhong1 guo2"), "Zhōng guó")
        self.assertEqual(
            numbered_pinyin_to_tone_marks("nu:3 er2"), "nǚ ér")

    def test_neutral_and_syllabic_tones(self):
        self.assertEqual(numbered_pinyin_to_tone_marks("de5"), "de")
        self.assertEqual(numbered_pinyin_to_tone_marks("m2"), "ḿ")
        self.assertEqual(numbered_pinyin_to_tone_marks("r5"), "r")

    def test_already_marked_or_non_pinyin_text_is_unchanged(self):
        self.assertEqual(
            numbered_pinyin_to_tone_marks("Zhōngguó; /rʌn/"),
            "Zhōngguó; /rʌn/",
        )


class TestLocalDictionary(unittest.TestCase):
    def setUp(self):
        self.fixture = DictionaryFixture()
        self.addCleanup(self.fixture.close)
        self.dictionary = LocalDictionary(self.fixture.path)
        self.addCleanup(self.dictionary.close_thread)

    def test_exact_match_preserves_source_fields(self):
        result = self.dictionary.lookup("RUN")
        self.assertEqual(result.match_type, "exact")
        self.assertEqual(result.headword, "run")
        self.assertEqual(result.pronunciation, "/rʌn/")
        self.assertEqual(result.entries[0].part_of_speech, "verb")
        self.assertEqual(result.senses[0].definition, "跑")
        self.assertEqual(result.senses[0].source_id, "fixture")
        self.assertEqual(result.senses[0].provenance, "fixture:run:sense")
        self.assertTrue(result.is_high_confidence)

    def test_explicit_form_match_without_stemming(self):
        result = self.dictionary.lookup("RAN")
        self.assertEqual(result.headword, "run")
        self.assertEqual(result.match_type, "form")
        self.assertGreaterEqual(result.confidence, 0.9)
        self.assertIsNone(self.dictionary.lookup("running"))

    def test_traditional_alias_match(self):
        result = self.dictionary.lookup("中國")
        self.assertEqual(result.headword, "中国")
        self.assertEqual(result.match_type, "alias")

    def test_unihan_single_character_fallback(self):
        result = self.dictionary.lookup("龘")
        self.assertEqual(result.pronunciation, "dá")
        self.assertIn("dragon", result.senses[0].definition)

    def test_miss_returns_none(self):
        self.assertIsNone(self.dictionary.lookup("not-a-fixture-word"))
        self.assertIsNone(self.dictionary.lookup(""))

    def test_formatter_does_not_invent_examples_or_fields(self):
        result = self.dictionary.lookup("run")
        rendered = format_dictionary_result(result)
        self.assertIn("## run [[cc-instant]]", rendered)
        self.assertIn(
            ("/rʌn/", "rich_pronunciation"),
            iter_rich_segments(rendered))
        self.assertIn("- 跑", rendered)
        source_chunk = next(
            chunk for chunk, tag in iter_rich_segments(rendered)
            if tag == "rich_sources_button")
        self.assertEqual(decode_source_details(source_chunk), [{
            "id": "fixture",
            "label": "Fixture source",
            "version": "1",
            "license": "Test license",
        }])
        self.assertNotIn("Example", rendered)

    def test_formatter_converts_numbered_pinyin_and_deduplicates_senses(self):
        result = self.dictionary.lookup("中国")
        entry = result.entries[0]
        duplicated = result.__class__(
            query=result.query,
            normalized_query=result.normalized_query,
            headword=result.headword,
            pronunciation="Zhong1 guo2",
            senses=entry.senses + entry.senses,
            entries=(entry.__class__(
                headword=entry.headword,
                language=entry.language,
                pronunciation="Zhong1 guo2",
                part_of_speech=entry.part_of_speech,
                senses=entry.senses + entry.senses,
                source_id=entry.source_id,
                source_label=entry.source_label,
                source_version=entry.source_version,
                source_license=entry.source_license,
            ),),
            source_ids=result.source_ids,
            match_type=result.match_type,
            confidence=result.confidence,
        )
        rendered = format_dictionary_result(duplicated)
        self.assertIn(
            ("Zhōng guó", "rich_pronunciation"),
            iter_rich_segments(rendered))
        self.assertEqual(rendered.count("- China"), 1)

    def test_formatter_groups_polyphonic_senses_and_collapses_overflow(self):
        def sense(text):
            return DictionarySense(
                text, "fixture", "fixture:sense",
                "Fixture source", "1", "Test license")

        entries = (
            DictionaryEntry(
                "行", "zh", "xing2", None,
                tuple(sense("to walk %d" % index) for index in range(1, 6)),
                "fixture", "Fixture source", "1", "Test license"),
            DictionaryEntry(
                "行", "zh", "hang2", None,
                (sense("archaic trade guild"), sense("row")),
                "fixture", "Fixture source", "1", "Test license"),
        )
        result = DictionaryResult(
            "行", "行", "行", "xing2",
            tuple(sense for entry in entries for sense in entry.senses),
            entries, ("fixture",), "exact", 1.0)
        collapsed = format_dictionary_result(result)
        expanded = format_dictionary_result(result, expanded=True)

        self.assertIn(
            ("xíng", "rich_pronunciation"), iter_rich_segments(collapsed))
        self.assertIn(
            ("háng", "rich_pronunciation"), iter_rich_segments(collapsed))
        self.assertIn("[[cc-more:2:", collapsed)
        self.assertEqual(collapsed.count("- "), INITIAL_SENSE_LIMIT)
        self.assertIn(
            ("xíng", "rich_pronunciation"), iter_rich_segments(expanded))
        self.assertIn(
            ("háng", "rich_pronunciation"), iter_rich_segments(expanded))
        self.assertNotIn("[[cc-more:", expanded)
        self.assertLess(
            expanded.index("- row"),
            expanded.index("- archaic trade guild"))

    def test_cache_version_includes_query_and_data_contract(self):
        version = self.dictionary.cache_version
        self.assertIn("query-v1", version)
        self.assertIn("fixture-1", version)
        self.assertIn("bbbbbbbbbbbbbbbb", version)


class TestDictionaryStoreFailures(unittest.TestCase):
    def test_missing_database_has_explicit_status(self):
        path = os.path.join(tempfile.gettempdir(), "definitely-missing-cc.sqlite3")
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        store = DictionaryStore(path)
        self.assertFalse(store.status.available)
        self.assertIn("missing", store.status.error)
        with self.assertRaisesRegex(RuntimeError, "missing"):
            store.lookup("word")

    def test_corrupt_database_has_explicit_status(self):
        with tempfile.NamedTemporaryFile(delete=False) as stream:
            stream.write(b"not sqlite")
            path = stream.name
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        store = DictionaryStore(path)
        self.assertFalse(store.status.available)
        self.assertIn("database", store.status.error.lower())

    def test_incompatible_schema_has_explicit_status(self):
        handle, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
        conn.commit()
        conn.close()
        store = DictionaryStore(path)
        self.assertFalse(store.status.available)
        self.assertIn("missing tables", store.status.error)

    def test_wrong_schema_version_is_rejected(self):
        fixture = DictionaryFixture()
        self.addCleanup(fixture.close)
        conn = sqlite3.connect(fixture.path)
        conn.execute(
            "UPDATE metadata SET value = '999' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        store = DictionaryStore(fixture.path)
        self.assertFalse(store.status.available)
        self.assertIn("unsupported schema version", store.status.error)

    def test_expected_artifact_hash_mismatch_is_rejected(self):
        fixture = DictionaryFixture()
        self.addCleanup(fixture.close)
        store = DictionaryStore(fixture.path, "0" * 64)
        self.assertFalse(store.status.available)
        self.assertIn("SHA-256 mismatch", store.status.error)

    def test_connections_are_thread_local_and_read_only(self):
        fixture = DictionaryFixture()
        self.addCleanup(fixture.close)
        store = DictionaryStore(fixture.path)
        outcomes = []

        def worker():
            outcomes.append(bool(store.lookup("run")["exact"]))
            with self.assertRaises(sqlite3.OperationalError):
                store._connection().execute(
                    "INSERT INTO metadata VALUES ('bad', 'bad')")
            store.close_thread()

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes, [True, True, True])


class TestReleaseDictionaryArtifact(unittest.TestCase):
    @unittest.skipUnless(
        os.path.isfile(DEVELOPMENT_DICTIONARY_PATH),
        "full release artifact is not present in this source checkout",
    )
    def test_full_artifact_is_valid_and_contains_all_sources(self):
        dictionary = LocalDictionary(DEVELOPMENT_DICTIONARY_PATH)
        self.assertTrue(dictionary.status.available, dictionary.status.error)
        self.assertGreater(dictionary.status.entry_count, 200_000)
        conn = sqlite3.connect(dictionary.status.path)
        try:
            source_ids = {
                row[0] for row in conn.execute("SELECT id FROM sources")}
            metadata = dict(conn.execute("SELECT key, value FROM metadata"))
            form_count = conn.execute("SELECT COUNT(*) FROM forms").fetchone()[0]
            derived_count = conn.execute(
                "SELECT COUNT(*) FROM forms "
                "WHERE provenance LIKE 'Derived curated English form%'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(source_ids, {source.source_id for source in SOURCES})
        self.assertEqual(metadata["builder_version"], "builder-v3")
        self.assertIn("+forms-2+", metadata["data_version"])
        self.assertEqual(form_count, 17_684)
        self.assertEqual(derived_count, 101)

    @unittest.skipUnless(
        os.path.isfile(DEVELOPMENT_DICTIONARY_PATH),
        "full release artifact is not present in this source checkout",
    )
    def test_full_artifact_representative_queries(self):
        dictionary = LocalDictionary(DEVELOPMENT_DICTIONARY_PATH)
        cases = {
            "hello": ("hello", "exact", "wikdict-eng-zho"),
            "中国": ("中国", "exact", "cc-cedict"),
            "中國": ("中国", "alias", "cc-cedict"),
            "龘": ("龘", "exact", "unihan"),
            "ＡＢＣ": ("ABC", "exact", "wikdict-eng-zho"),
            "2CVs": ("2CV", "form", "wikdict-eng-zho"),
            "children": ("child", "form", "wikdict-eng-zho"),
            "carried": ("carry", "form", "wikdict-eng-zho"),
            "walked": ("walk", "form", "wikdict-eng-zho"),
            "running": ("running", "exact", "wikdict-eng-zho"),
        }
        for query, (headword, match_type, source_id) in cases.items():
            with self.subTest(query=query):
                result = dictionary.lookup(query)
                self.assertIsNotNone(result)
                self.assertEqual(result.headword, headword)
                self.assertEqual(result.match_type, match_type)
                self.assertIn(source_id, result.source_ids)
        self.assertIsNone(dictionary.lookup("thinked"))
        conn = sqlite3.connect(dictionary.status.path)
        try:
            derived_running = conn.execute(
                "SELECT e.headword FROM forms f "
                "JOIN entries e ON e.id = f.entry_id "
                "WHERE f.form_norm = 'running' "
                "AND f.provenance LIKE 'Derived curated English form%'"
            ).fetchall()
        finally:
            conn.close()
        self.assertIn(("run",), derived_running)

    @unittest.skipUnless(
        os.path.isfile(DEVELOPMENT_DICTIONARY_PATH),
        "full release artifact is not present in this source checkout",
    )
    def test_artifact_hash_sidecar_and_notices_match(self):
        root = os.path.dirname(os.path.dirname(__file__))
        artifact = os.path.join(
            root, "data", "dictionary", "cc_dictionary.sqlite3")
        with open(artifact, "rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with open(artifact + ".sha256", "r", encoding="ascii") as stream:
            self.assertEqual(stream.read().split()[0], digest)
        with open(os.path.join(root, "THIRD_PARTY_NOTICES"),
                  "r", encoding="utf-8") as stream:
            self.assertIn(digest, stream.read())

    def test_pinned_source_hashes_and_license_files_are_present(self):
        self.assertEqual(len(SOURCES), 5)
        for source in SOURCES:
            self.assertRegex(source.sha256, r"^[0-9a-f]{64}$")
        root = os.path.dirname(os.path.dirname(__file__))
        license_dir = os.path.join(root, "data", "dictionary", "licenses")
        for name in (
                "CC-BY-SA-3.0.txt",
                "Chinese-Open-Wordnet-LICENSE.txt",
                "Princeton-WordNet-3.0-LICENSE.txt",
                "Unicode-License-v3.txt"):
            path = os.path.join(license_dir, name)
            self.assertTrue(os.path.isfile(path), path)
            self.assertGreater(os.path.getsize(path), 100)
        with open(os.path.join(root, "THIRD_PARTY_NOTICES"),
                  "r", encoding="utf-8") as stream:
            notices = stream.read()
        for source in SOURCES:
            self.assertIn(source.sha256, notices)


if __name__ == "__main__":
    unittest.main()
