"""Windows dictionary defaults around the portable lookup policy."""

from __future__ import annotations

import os

from cc_dictionary_artifact import (
    ARTIFACT_SHA256,
    DEVELOPMENT_DICTIONARY_PATH,
    INSTALLED_DICTIONARY_PATH,
)
from cc_dictionary_store import DictionaryStore, DictionaryStoreError, EntryRow
from cc_dictionary_lookup import (
    DictionaryEntry, DictionaryResult, DictionarySense, HIGH_CONFIDENCE, QUERY_VERSION,
    LocalDictionary as _LocalDictionary, normalize_query,
)


DEFAULT_DICTIONARY_PATH = INSTALLED_DICTIONARY_PATH
DEFAULT_DICTIONARY_HASH_PATH = DEVELOPMENT_DICTIONARY_PATH + ".sha256"


class LocalDictionary(_LocalDictionary):
    def __init__(self, path: str = DEFAULT_DICTIONARY_PATH):
        expected_sha256 = None
        if os.path.abspath(path) == os.path.abspath(DEFAULT_DICTIONARY_PATH):
            expected_sha256 = ARTIFACT_SHA256
        elif os.path.abspath(path) == os.path.abspath(
                DEVELOPMENT_DICTIONARY_PATH):
            try:
                with open(DEFAULT_DICTIONARY_HASH_PATH, "r",
                          encoding="ascii") as stream:
                    expected_sha256 = stream.read().strip().split()[0]
            except (OSError, IndexError):
                expected_sha256 = ""
        self.store = DictionaryStore(path, expected_sha256)

    @property
    def cache_version(self) -> str:
        return "%s:%s" % (QUERY_VERSION, self.store.status.cache_version)

    @staticmethod
    def _normalize_query(query):
        return normalize_query(query)


__all__ = [
    "DEFAULT_DICTIONARY_PATH",
    "DEFAULT_DICTIONARY_HASH_PATH",
    "DEVELOPMENT_DICTIONARY_PATH",
    "DictionaryEntry",
    "DictionaryResult",
    "DictionarySense",
    "DictionaryStoreError",
    "HIGH_CONFIDENCE",
    "LocalDictionary",
    "normalize_query",
]
