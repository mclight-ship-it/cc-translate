"""Windows dictionary defaults around the portable artifact installer."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cc_core import APP_DIR, DATA_DIR
from cc_dictionary_store import DictionaryStore, StoreStatus
from cc_dictionary_artifact_core import (
    ARTIFACT_DATA_VERSION, ARTIFACT_FILENAME, ARTIFACT_RELEASE_NAME,
    ARTIFACT_RELEASE_TAG, ARTIFACT_SHA256, ARTIFACT_SIZE, ARTIFACT_URL,
    DictionaryArtifact, DictionaryArtifactError, DictionaryDownloadCancelled,
    DictionaryArtifactManager as _DictionaryArtifactManager,
)


DICTIONARY_DIR = os.path.join(DATA_DIR, "dictionary")
INSTALLED_DICTIONARY_PATH = os.path.join(DICTIONARY_DIR, ARTIFACT_FILENAME)
DEVELOPMENT_DICTIONARY_PATH = os.path.join(
    APP_DIR, "data", "dictionary", ARTIFACT_FILENAME)


class DictionaryArtifactManager(_DictionaryArtifactManager):
    """Install a pinned release asset without touching the application checkout."""

    def __init__(
            self, directory: str = DICTIONARY_DIR,
            artifact: DictionaryArtifact = DictionaryArtifact(),
            opener: Callable = urlopen, timeout: float = 30.0):
        self.directory = os.path.abspath(directory)
        self.path = os.path.join(self.directory, ARTIFACT_FILENAME)
        self.artifact = artifact
        self._opener = opener
        self.timeout = timeout

    def _store_status(self, path) -> StoreStatus:
        return DictionaryStore(path, self.artifact.sha256).status

    @staticmethod
    def _request(url, headers):
        return Request(url, headers=headers)


__all__ = [
    "ARTIFACT_DATA_VERSION",
    "ARTIFACT_FILENAME",
    "ARTIFACT_RELEASE_NAME",
    "ARTIFACT_RELEASE_TAG",
    "ARTIFACT_SHA256",
    "ARTIFACT_SIZE",
    "ARTIFACT_URL",
    "DEVELOPMENT_DICTIONARY_PATH",
    "DictionaryArtifact",
    "DictionaryArtifactError",
    "DictionaryArtifactManager",
    "DictionaryDownloadCancelled",
    "INSTALLED_DICTIONARY_PATH",
]
