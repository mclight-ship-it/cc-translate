"""Dictionary lookup policy with an explicit path and hash, without platform defaults."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Optional

from cc_dictionary_store import DictionaryStore, DictionaryStoreError, EntryRow


QUERY_VERSION = "query-v1"
HIGH_CONFIDENCE = 0.90


@dataclass(frozen=True)
class DictionarySense:
    definition: str
    source_id: str
    provenance: str
    source_label: str
    source_version: str
    source_license: str


@dataclass(frozen=True)
class DictionaryEntry:
    headword: str
    language: str
    pronunciation: Optional[str]
    part_of_speech: Optional[str]
    senses: tuple[DictionarySense, ...]
    source_id: str
    source_label: str
    source_version: str
    source_license: str


@dataclass(frozen=True)
class DictionaryResult:
    query: str
    normalized_query: str
    headword: str
    pronunciation: Optional[str]
    senses: tuple[DictionarySense, ...]
    entries: tuple[DictionaryEntry, ...]
    source_ids: tuple[str, ...]
    match_type: str
    confidence: float

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= HIGH_CONFIDENCE and bool(self.senses)


def normalize_query(text: str) -> str:
    """Normalize only equivalences safe for exact dictionary matching."""
    return unicodedata.normalize("NFKC", text or "").strip().casefold()


class LocalDictionary:
    def __init__(self, path: str, expected_sha256: Optional[str]):
        """Callers explicitly provide the pin, or None for an unpinned test store."""
        self.store = DictionaryStore(path, expected_sha256)

    @property
    def status(self):
        return self.store.status

    @property
    def cache_version(self) -> str:
        return "%s:%s" % (QUERY_VERSION, self.store.status.cache_version)

    def lookup(self, query: str) -> Optional[DictionaryResult]:
        normalized = self._normalize_query(query)
        if not normalized:
            return None
        matches = self.store.lookup(normalized)
        selected_type = next(
            (kind for kind in ("exact", "form", "alias") if matches[kind]),
            None)
        if selected_type is None:
            return None
        rows = matches[selected_type]
        confidence = {
            "exact": 1.0,
            "form": 0.96,
            "alias": 0.98,
        }[selected_type]
        entries = tuple(self._entry(row) for row in rows if row.senses)
        if not entries:
            return None
        senses = tuple(
            sense for entry in entries for sense in entry.senses)
        source_ids = tuple(dict.fromkeys(
            source_id
            for entry in entries
            for source_id in (
                entry.source_id,
                *(sense.source_id for sense in entry.senses),
            )))
        return DictionaryResult(
            query=query,
            normalized_query=normalized,
            headword=entries[0].headword,
            pronunciation=entries[0].pronunciation,
            senses=senses,
            entries=entries,
            source_ids=source_ids,
            match_type=selected_type,
            confidence=confidence,
        )

    @staticmethod
    def _normalize_query(query):
        return normalize_query(query)

    def close_thread(self) -> None:
        self.store.close_thread()

    @staticmethod
    def _entry(row: EntryRow) -> DictionaryEntry:
        senses = tuple(DictionarySense(
            definition=sense.definition,
            source_id=sense.source_id,
            provenance=sense.provenance,
            source_label=sense.source_label,
            source_version=sense.source_version,
            source_license=sense.source_license,
        ) for sense in row.senses if sense.definition)
        return DictionaryEntry(
            headword=row.headword,
            language=row.language,
            pronunciation=row.pronunciation,
            part_of_speech=row.part_of_speech,
            senses=senses,
            source_id=row.source_id,
            source_label=row.source_label,
            source_version=row.source_version,
            source_license=row.source_license,
        )


__all__ = [
    "DictionaryEntry", "DictionaryResult", "DictionarySense", "DictionaryStoreError",
    "HIGH_CONFIDENCE", "LocalDictionary", "QUERY_VERSION", "normalize_query",
]
