"""Portable store contracts using only synthetic data, including in the app bundle."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from cc_dictionary_store import SCHEMA, DictionaryStore, DictionaryStoreError
from cc_macos.dictionary_probe import create_fixture, probe_dictionary
from cc_macos import probes


class TestPortableDictionaryStore(unittest.TestCase):
    def test_schema_matches_existing_builder_v3_bytes(self):
        self.assertEqual(hashlib.sha256(SCHEMA.encode("utf-8")).hexdigest(),
                         "2946f5367ba1cb1f2e3da591f683826785cb5aa59e62c6c7a59fe7dfa15dea66")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        name = "synthetic \u4e2d # %.sqlite3"
        if os.name != "nt":
            name += "\\?literal"
        self.path = self.directory / name
        create_fixture(self.path)
        self.before = self.path.read_bytes()
        self.store = DictionaryStore(str(self.path), hashlib.sha256(self.before).hexdigest())
        self.addCleanup(self.store.close_thread)

    def test_native_uri_and_all_match_kinds_keep_source_identity(self):
        self.assertTrue(self.store.status.available, self.store.status.error)
        for query, kind in (("synthetic", "exact"), ("synthetics", "form"), ("synthetic-alias", "alias")):
            matches = self.store.lookup(query)
            self.assertEqual(sum(map(len, matches.values())), 1)
            entry = matches[kind][0]
            self.assertEqual((entry.source_id, entry.source_license, entry.provenance),
                             ("fixture", "Synthetic license", "fixture:entry"))
            self.assertEqual(entry.senses[0].provenance, "fixture:sense")
            self.assertEqual(entry.senses[0].source_license, "Synthetic license")
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_uri_stays_read_only_even_if_query_only_is_disabled(self):
        connection = self.store._connection()
        connection.execute("PRAGMA query_only = OFF")
        with self.assertRaises(sqlite3.OperationalError) as error:
            connection.execute("DELETE FROM entries")
        self.assertEqual(error.exception.sqlite_errorcode, sqlite3.SQLITE_READONLY)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_close_is_idempotent_and_next_lookup_reopens(self):
        before = self.store.lookup("synthetic")
        connection = self.store._connection()
        self.store.close_thread()
        self.store.close_thread()
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        self.assertEqual(self.store.lookup("synthetic"), before)
        self.assertIsNot(self.store._connection(), connection)

    def test_threads_own_and_close_distinct_connections(self):
        main = self.store._connection()
        barrier = threading.Barrier(2)
        def lookup():
            try:
                connection = self.store._connection()
                barrier.wait(timeout=3)
                self.assertEqual(len(self.store.lookup("synthetic")["exact"]), 1)
                return connection
            finally:
                self.store.close_thread()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(lookup) for _ in range(2)]
            connections = [future.result(timeout=5) for future in futures]
        self.assertIsNot(connections[0], connections[1])
        self.assertTrue(all(connection is not main for connection in connections))
        self.assertEqual(main.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_missing_or_mismatched_store_does_not_fall_back(self):
        for store in (DictionaryStore(str(self.directory / "missing")),
                      DictionaryStore(str(self.path), "0" * 64)):
            self.assertFalse(store.status.available)
            with self.assertRaises(DictionaryStoreError):
                store.lookup("synthetic")
        self.assertFalse((self.directory / "missing").exists())

    def test_query_parameters_do_not_become_sql(self):
        self.assertEqual(self.store.lookup("synthetic' OR 1=1 --"),
                         {"exact": [], "form": [], "alias": []})

    def test_explicit_synthetic_probe_exercises_storage(self):
        self.assertEqual(probe_dictionary(self.directory / "probe.sqlite3"), {
            "status": "passed", "read_only": True, "sources_preserved": True, "reopened": True,
        })

    def test_probe_storage_error_exposes_only_fixed_code(self):
        with patch("cc_macos.probes.probe_dictionary",
                   side_effect=DictionaryStoreError("private path not for transport")):
            with self.assertRaises(probes.ProbeError) as error:
                probes.runtime_probe(https=False, cancel=threading.Event())
        self.assertEqual(str(error.exception), "dictionary_probe_failed")
