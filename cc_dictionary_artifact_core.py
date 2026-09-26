"""Pinned dictionary installation with explicit caller-owned paths and lifecycle."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import threading
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
    """Install an explicitly selected artifact in an explicitly supplied directory."""

    def __init__(
            self, directory: str, artifact: DictionaryArtifact,
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

    def inspect(self) -> StoreStatus:
        return self._store_status(self.path)

    @staticmethod
    def _check_cancel(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise DictionaryDownloadCancelled("dictionary download cancelled")

    def install(
            self, progress: Optional[Callable[[int, int], None]] = None,
            cancel_event: Optional[threading.Event] = None, *,
            begin_commit: Optional[Callable[[], bool]] = None) -> StoreStatus:
        """Download to owned staging, then use install_staged's commit boundary."""
        self._check_cancel(cancel_event)
        os.makedirs(self.directory, exist_ok=True)
        handle, temporary_path = tempfile.mkstemp(
            prefix=".dictionary-download-", suffix=".tmp",
            dir=self.directory)
        os.close(handle)
        try:
            request = self._request(
                self.artifact.url,
                headers={
                    "User-Agent": "CC-Translate-dictionary-installer/1",
                    "Accept-Encoding": "identity",
                },
            )
            self._check_cancel(cancel_event)
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
                        self._check_cancel(cancel_event)
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

            installed = self.install_staged(
                temporary_path, cancel_event, begin_commit=begin_commit)
            temporary_path = ""
            return installed
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    def install_staged(
            self, staged_path: str, cancel_event: Optional[threading.Event] = None, *,
            begin_commit: Optional[Callable[[], bool]] = None) -> StoreStatus:
        """Validate and atomically consume an explicit completed file; never network.

        Staging must be an absolute, regular, non-symlink file directly inside the
        install directory, distinct from the installed path. The caller must own
        it exclusively throughout validation/commit and permit a writable handle
        for fsync before commit reservation. Before replacement, failures leave
        staging to the caller; after replacement, the installed file is not rolled
        back. The downloader owns and cleans its own staging separately.

        begin_commit returns False to cancel, or True to reserve a non-cancellable
        commit under the caller's lock. True is not success: replacement/inspection
        can still fail. Cancellation after this boundary never claims rollback.
        """
        self._check_cancel(cancel_event)
        try:
            path = os.fspath(staged_path)
        except TypeError as error:
            raise DictionaryArtifactError("invalid staged dictionary path") from error
        if not isinstance(path, str) or not os.path.isabs(path):
            raise DictionaryArtifactError("staged dictionary path must be absolute")
        path = os.path.abspath(path)
        if (os.path.normcase(os.path.dirname(path)) != os.path.normcase(self.directory)
                or os.path.normcase(path) == os.path.normcase(self.path)):
            raise DictionaryArtifactError("staged dictionary must be separate and inside the install directory")
        try:
            info = os.lstat(path)
        except (OSError, ValueError) as error:
            raise DictionaryArtifactError("staged dictionary is unavailable") from error
        if not stat.S_ISREG(info.st_mode):
            raise DictionaryArtifactError("staged dictionary must be a regular non-symlink file")
        if info.st_size != self.artifact.size:
            raise DictionaryArtifactError(
                "staged dictionary size mismatch: expected %s, got %s"
                % (self.artifact.size, info.st_size))
        status = self._store_status(path)
        if not status.available:
            raise DictionaryArtifactError(
                "downloaded dictionary is invalid: %s" % status.error)
        if status.data_version != self.artifact.data_version:
            raise DictionaryArtifactError(
                "dictionary data version mismatch: expected %s, got %s"
                % (self.artifact.data_version, status.data_version))

        self._check_cancel(cancel_event)
        # Windows FlushFileBuffers needs a writable handle; no bytes are changed.
        with open(path, "r+b") as stream:
            os.fsync(stream.fileno())
        self._check_cancel(cancel_event)
        if begin_commit is not None and not begin_commit():
            raise DictionaryDownloadCancelled("dictionary download cancelled")
        os.replace(path, self.path)
        installed = self.inspect()
        if not installed.available:
            raise DictionaryArtifactError(
                "installed dictionary is invalid: %s" % installed.error)
        return installed

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
    "ARTIFACT_DATA_VERSION", "ARTIFACT_FILENAME", "ARTIFACT_RELEASE_NAME",
    "ARTIFACT_RELEASE_TAG", "ARTIFACT_SHA256", "ARTIFACT_SIZE", "ARTIFACT_URL",
    "DictionaryArtifact", "DictionaryArtifactError", "DictionaryArtifactManager",
    "DictionaryDownloadCancelled",
]
