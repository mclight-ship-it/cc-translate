"""Download and manage the optional per-user dictionary artifact."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cc_core import APP_DIR, DATA_DIR
from cc_dictionary_store import DictionaryStore, StoreStatus


ARTIFACT_FILENAME = "cc_dictionary.sqlite3"
ARTIFACT_SIZE = 67_948_544
ARTIFACT_SHA256 = (
    "3695295d07268725e555471217351c74e4f9375b8152974760fe12b926e1830e")
ARTIFACT_DATA_VERSION = (
    "wikdict-2025.11.21+forms-2+cedict-2017.04.28+omw-2.0+unihan-17.0.0")
ARTIFACT_RELEASE_TAG = "dictionary-v1"
ARTIFACT_RELEASE_NAME = "cc_dictionary-3695295d.sqlite3"
ARTIFACT_URL = (
    "https://github.com/mclight-ship-it/cc-translate/releases/download/"
    f"{ARTIFACT_RELEASE_TAG}/{ARTIFACT_RELEASE_NAME}")
DICTIONARY_DIR = os.path.join(DATA_DIR, "dictionary")
INSTALLED_DICTIONARY_PATH = os.path.join(DICTIONARY_DIR, ARTIFACT_FILENAME)
DEVELOPMENT_DICTIONARY_PATH = os.path.join(
    APP_DIR, "data", "dictionary", ARTIFACT_FILENAME)


class DictionaryArtifactError(RuntimeError):
    """The optional dictionary could not be downloaded or managed."""


class DictionaryDownloadCancelled(DictionaryArtifactError):
    """The user cancelled an in-progress dictionary download."""


@dataclass(frozen=True)
class DictionaryArtifact:
    url: str = ARTIFACT_URL
    sha256: str = ARTIFACT_SHA256
    size: int = ARTIFACT_SIZE
    data_version: str = ARTIFACT_DATA_VERSION


class DictionaryArtifactManager:
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

    def inspect(self) -> StoreStatus:
        return DictionaryStore(self.path, self.artifact.sha256).status

    def install(
            self, progress: Optional[Callable[[int, int], None]] = None,
            cancel_event: Optional[threading.Event] = None) -> StoreStatus:
        os.makedirs(self.directory, exist_ok=True)
        handle, temporary_path = tempfile.mkstemp(
            prefix=".dictionary-download-", suffix=".tmp",
            dir=self.directory)
        os.close(handle)
        try:
            request = Request(
                self.artifact.url,
                headers={
                    "User-Agent": "CC-Translate-dictionary-installer/1",
                    "Accept-Encoding": "identity",
                },
            )
            try:
                response = self._opener(request, timeout=self.timeout)
            except (HTTPError, URLError, OSError) as exc:
                raise DictionaryArtifactError(
                    "cannot download dictionary: %s" % exc) from exc

            with response:
                status_code = getattr(response, "status", None)
                if status_code not in (None, 200):
                    raise DictionaryArtifactError(
                        "dictionary download returned HTTP %s" % status_code)
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise DictionaryArtifactError(
                            "dictionary download has an invalid size") from exc
                    if declared_size != self.artifact.size:
                        raise DictionaryArtifactError(
                            "dictionary download size mismatch: expected %s, got %s"
                            % (self.artifact.size, declared_size))

                digest = hashlib.sha256()
                downloaded = 0
                with open(temporary_path, "wb") as stream:
                    while True:
                        if cancel_event is not None and cancel_event.is_set():
                            raise DictionaryDownloadCancelled(
                                "dictionary download cancelled")
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > self.artifact.size:
                            raise DictionaryArtifactError(
                                "dictionary download exceeds expected size")
                        stream.write(chunk)
                        digest.update(chunk)
                        if progress is not None:
                            progress(downloaded, self.artifact.size)
                    stream.flush()
                    os.fsync(stream.fileno())

            if downloaded != self.artifact.size:
                raise DictionaryArtifactError(
                    "dictionary download is incomplete: expected %s, got %s"
                    % (self.artifact.size, downloaded))
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != self.artifact.sha256:
                raise DictionaryArtifactError(
                    "dictionary SHA-256 mismatch: %s" % actual_sha256)

            status = DictionaryStore(
                temporary_path, self.artifact.sha256).status
            if not status.available:
                raise DictionaryArtifactError(
                    "downloaded dictionary is invalid: %s" % status.error)
            if status.data_version != self.artifact.data_version:
                raise DictionaryArtifactError(
                    "dictionary data version mismatch: expected %s, got %s"
                    % (self.artifact.data_version, status.data_version))

            os.replace(temporary_path, self.path)
            temporary_path = ""
            installed = self.inspect()
            if not installed.available:
                raise DictionaryArtifactError(
                    "installed dictionary is invalid: %s" % installed.error)
            return installed
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    def delete(self) -> bool:
        try:
            os.unlink(self.path)
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise DictionaryArtifactError(
                "cannot delete dictionary: %s" % exc) from exc


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
