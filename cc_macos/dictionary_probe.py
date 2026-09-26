"""Synthetic read-only dictionary probe; never opens the user's dictionary."""

import hashlib
import sqlite3

from cc_dictionary_store import SCHEMA, DictionaryStore, DictionaryStoreError


def create_fixture(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.executescript(SCHEMA)
            connection.executemany("INSERT INTO metadata VALUES (?, ?)", (
                ("schema_version", "1"), ("data_version", "synthetic-1"),
                ("content_sha256", "a" * 64),
            ))
            connection.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)", (
                "fixture", "Synthetic source", "1", "Synthetic license",
                "https://example.invalid", "b" * 64,
            ))
            connection.execute("INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                1, "synthetic", "synthetic", "en", None, "noun", "fixture", "fixture:entry", 1,
            ))
            connection.execute("INSERT INTO senses VALUES (?, ?, ?, ?, ?, ?)", (
                1, 1, 1, "Synthetic definition", "fixture", "fixture:sense",
            ))
            connection.execute("INSERT INTO forms VALUES (?, ?, ?, ?)", (
                "synthetics", 1, "synthetics", "fixture:form",
            ))
            connection.execute("INSERT INTO aliases VALUES (?, ?, ?, ?, ?)", (
                "synthetic-alias", 1, "synthetic-alias", "synthetic", "fixture:alias",
            ))
    finally:
        connection.close()


def probe_dictionary(path):
    create_fixture(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    store = DictionaryStore(str(path), expected_sha256=before)
    try:
        if not store.status.available:
            raise DictionaryStoreError("synthetic dictionary unavailable")
        for query, kind in (("synthetic", "exact"), ("synthetics", "form"), ("synthetic-alias", "alias")):
            matches = store.lookup(query)
            if len(matches[kind]) != 1:
                raise DictionaryStoreError("synthetic dictionary lookup mismatch")
            entry = matches[kind][0]
            if len(entry.senses) != 1 or (entry.source_id, entry.source_license, entry.provenance,
                    entry.senses[0].source_id, entry.senses[0].provenance) != (
                    "fixture", "Synthetic license", "fixture:entry", "fixture", "fixture:sense"):
                raise DictionaryStoreError("synthetic dictionary provenance mismatch")
        # Verify URI read-only mode, not just the additional query_only PRAGMA.
        connection = store._connection()
        connection.execute("PRAGMA query_only = OFF")
        try:
            connection.execute("CREATE TABLE must_not_exist (value TEXT)")
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode != sqlite3.SQLITE_READONLY:
                raise
        else:
            raise DictionaryStoreError("synthetic dictionary accepted a write")
        store.close_thread()
        if store.lookup("synthetic")["exact"] != [entry]:
            raise DictionaryStoreError("synthetic dictionary reopen mismatch")
    finally:
        store.close_thread()
    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
        raise DictionaryStoreError("synthetic dictionary changed")
    return {"status": "passed", "read_only": True, "sources_preserved": True, "reopened": True}
