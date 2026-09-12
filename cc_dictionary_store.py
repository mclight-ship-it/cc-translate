"""Read-only SQLite storage for the optional CC Translate dictionary."""

from __future__ import annotations

import os
import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


SCHEMA_VERSION = "1"
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
REQUIRED_METADATA = ("schema_version", "data_version", "content_sha256")
REQUIRED_TABLES = {
    "metadata", "sources", "entries", "senses", "forms", "aliases",
}


class DictionaryStoreError(RuntimeError):
    """Base error for an unavailable or invalid dictionary database."""


class DictionaryCompatibilityError(DictionaryStoreError):
    """The database exists but does not match the supported schema."""


class DictionaryIntegrityError(DictionaryStoreError):
    """The database cannot be read safely."""


@dataclass(frozen=True)
class StoreStatus:
    available: bool
    path: str
    schema_version: str = ""
    data_version: str = ""
    content_sha256: str = ""
    entry_count: int = 0
    source_count: int = 0
    error: str = ""

    @property
    def cache_version(self) -> str:
        if not self.available:
            return "unavailable"
        return "%s:%s:%s" % (
            self.schema_version, self.data_version, self.content_sha256[:16])


@dataclass(frozen=True)
class SenseRow:
    definition: str
    source_id: str
    provenance: str
    source_label: str
    source_version: str
    source_license: str


@dataclass(frozen=True)
class EntryRow:
    entry_id: int
    headword: str
    language: str
    pronunciation: Optional[str]
    part_of_speech: Optional[str]
    source_id: str
    provenance: str
    source_label: str
    source_version: str
    source_license: str
    senses: tuple[SenseRow, ...]


class DictionaryStore:
    """Validate once, then provide per-thread read-only SQLite connections."""

    def __init__(self, path: str, expected_sha256: Optional[str] = None):
        self.path = os.path.abspath(path)
        self.expected_sha256 = (
            expected_sha256.lower() if expected_sha256 else expected_sha256)
        self._local = threading.local()
        self._status = self._inspect()

    @property
    def status(self) -> StoreStatus:
        return self._status

    def _connect(self) -> sqlite3.Connection:
        uri = Path(self.path).as_uri() + "?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=1.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            return conn
        except sqlite3.Error as exc:
            raise DictionaryStoreError(
                "cannot open dictionary database read-only: %s" % exc) from exc

    def _inspect(self) -> StoreStatus:
        if not os.path.isfile(self.path):
            return StoreStatus(
                available=False, path=self.path,
                error="dictionary database is missing")
        try:
            if self.expected_sha256 is not None:
                if not self.expected_sha256:
                    raise DictionaryIntegrityError(
                        "artifact SHA-256 sidecar is missing or empty")
                if not (len(self.expected_sha256) == 64 and all(
                        char in "0123456789abcdef"
                        for char in self.expected_sha256)):
                    raise DictionaryIntegrityError(
                        "artifact SHA-256 sidecar is invalid")
                digest = hashlib.sha256()
                with open(self.path, "rb") as stream:
                    for chunk in iter(
                            lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                actual = digest.hexdigest()
                if actual != self.expected_sha256:
                    raise DictionaryIntegrityError(
                        "artifact SHA-256 mismatch: %s" % actual)
            conn = self._connect()
            try:
                tables = {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                missing = sorted(REQUIRED_TABLES - tables)
                if missing:
                    raise DictionaryCompatibilityError(
                        "missing tables: %s" % ", ".join(missing))
                metadata = dict(conn.execute(
                    "SELECT key, value FROM metadata").fetchall())
                absent = [key for key in REQUIRED_METADATA
                          if not metadata.get(key)]
                if absent:
                    raise DictionaryCompatibilityError(
                        "missing metadata: %s" % ", ".join(absent))
                if metadata["schema_version"] != SCHEMA_VERSION:
                    raise DictionaryCompatibilityError(
                        "unsupported schema version %s (expected %s)" % (
                            metadata["schema_version"], SCHEMA_VERSION))
                entry_count = conn.execute(
                    "SELECT COUNT(*) FROM entries").fetchone()[0]
                source_count = conn.execute(
                    "SELECT COUNT(*) FROM sources").fetchone()[0]
                return StoreStatus(
                    available=True,
                    path=self.path,
                    schema_version=metadata["schema_version"],
                    data_version=metadata["data_version"],
                    content_sha256=metadata["content_sha256"],
                    entry_count=entry_count,
                    source_count=source_count,
                )
            finally:
                conn.close()
        except DictionaryStoreError as exc:
            return StoreStatus(
                available=False, path=self.path, error=str(exc))
        except sqlite3.Error as exc:
            return StoreStatus(
                available=False, path=self.path,
                error="invalid SQLite database: %s" % exc)

    def _connection(self) -> sqlite3.Connection:
        if not self._status.available:
            raise DictionaryStoreError(self._status.error)
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = self._connect()
            self._local.connection = conn
        return conn

    def close_thread(self) -> None:
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            conn.close()
            del self._local.connection

    def lookup(self, normalized: str) -> Dict[str, List[EntryRow]]:
        """Return exact headword, explicit form, and explicit alias matches."""
        conn = self._connection()
        matches: Dict[str, List[EntryRow]] = {
            "exact": [], "form": [], "alias": [],
        }
        queries = {
            "exact": (
                "SELECT DISTINCT e.id FROM entries e "
                "WHERE e.headword_norm = ? ORDER BY e.priority, e.id"),
            "form": (
                "SELECT DISTINCT e.id FROM forms f "
                "JOIN entries e ON e.id = f.entry_id "
                "WHERE f.form_norm = ? ORDER BY e.priority, e.id"),
            "alias": (
                "SELECT DISTINCT e.id FROM aliases a "
                "JOIN entries e ON e.id = a.entry_id "
                "WHERE a.alias_norm = ? ORDER BY e.priority, e.id"),
        }
        seen: set[int] = set()
        for match_type in ("exact", "form", "alias"):
            ids = [
                int(row[0]) for row in conn.execute(
                    queries[match_type], (normalized,)).fetchall()
                if int(row[0]) not in seen
            ]
            seen.update(ids)
            matches[match_type] = self._load_entries(conn, ids)
        return matches

    @staticmethod
    def _load_entries(
            conn: sqlite3.Connection, entry_ids: List[int]) -> List[EntryRow]:
        rows: List[EntryRow] = []
        for entry_id in entry_ids:
            entry = conn.execute(
                "SELECT e.id, e.headword, e.language, e.pronunciation, "
                "e.part_of_speech, e.source_id, e.provenance, "
                "s.label, s.version, s.license "
                "FROM entries e JOIN sources s ON s.id = e.source_id "
                "WHERE e.id = ?",
                (entry_id,),
            ).fetchone()
            if entry is None:
                raise DictionaryIntegrityError(
                    "entry %s disappeared during lookup" % entry_id)
            senses = tuple(SenseRow(
                definition=row["definition"],
                source_id=row["source_id"],
                provenance=row["provenance"],
                source_label=row["label"],
                source_version=row["version"],
                source_license=row["license"],
            ) for row in conn.execute(
                "SELECT n.definition, n.source_id, n.provenance, "
                "s.label, s.version, s.license "
                "FROM senses n JOIN sources s ON s.id = n.source_id "
                "WHERE n.entry_id = ? ORDER BY n.ordinal, n.id",
                (entry_id,)).fetchall())
            rows.append(EntryRow(
                entry_id=int(entry["id"]),
                headword=entry["headword"],
                language=entry["language"],
                pronunciation=entry["pronunciation"],
                part_of_speech=entry["part_of_speech"],
                source_id=entry["source_id"],
                provenance=entry["provenance"],
                source_label=entry["label"],
                source_version=entry["version"],
                source_license=entry["license"],
                senses=senses,
            ))
        return rows
