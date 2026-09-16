import hashlib
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from cc_dictionary import (
    DEFAULT_DICTIONARY_PATH, DEVELOPMENT_DICTIONARY_PATH,
)
from cc_dictionary_artifact import (
    ARTIFACT_DATA_VERSION, ARTIFACT_SHA256, ARTIFACT_SIZE,
    INSTALLED_DICTIONARY_PATH, DictionaryArtifact,
    DictionaryArtifactError, DictionaryArtifactManager,
    DictionaryDownloadCancelled,
)
from tests.test_dictionary import DictionaryFixture


class TestDictionaryArtifactManager(unittest.TestCase):
    def setUp(self):
        self.fixture = DictionaryFixture()
        self.addCleanup(self.fixture.close)
        self.install_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.install_dir.cleanup)
        with open(self.fixture.path, "rb") as stream:
            payload = stream.read()
        self.fixture_sha256 = hashlib.sha256(payload).hexdigest()
        self.artifact = DictionaryArtifact(
            url=Path(self.fixture.path).as_uri(),
            sha256=self.fixture_sha256,
            size=len(payload),
            data_version="fixture-1",
        )
        self.manager = DictionaryArtifactManager(
            self.install_dir.name, self.artifact)

    def test_construction_and_inspection_do_not_download(self):
        calls = []

        def opener(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("network access is not expected")

        manager = DictionaryArtifactManager(
            self.install_dir.name, self.artifact, opener=opener)
        status = manager.inspect()
        self.assertFalse(status.available)
        self.assertEqual(calls, [])

    def test_windows_facade_keeps_request_store_and_filesystem_patch_seams(self):
        import cc_dictionary_artifact as windows
        with patch.object(windows, "Request", wraps=windows.Request) as request, \
                patch.object(windows, "DictionaryStore", wraps=windows.DictionaryStore) as store, \
                patch.object(windows.os, "replace", wraps=os.replace) as replace_file:
            status = self.manager.install()
        self.assertTrue(status.available)
        request.assert_called_once()
        self.assertEqual(store.call_count, 2)
        self.assertEqual(store.call_args.args, (self.manager.path, self.artifact.sha256))
        replace_file.assert_called_once()
        self.assertEqual(replace_file.call_args.args[1], self.manager.path)

    def test_windows_facade_exposes_optional_precommit_cancellation(self):
        commit = Mock(return_value=False)
        with self.assertRaises(DictionaryDownloadCancelled):
            self.manager.install(begin_commit=commit)
        commit.assert_called_once_with()
        self.assertFalse(os.path.exists(self.manager.path))
        self.assertEqual(os.listdir(self.install_dir.name), [])

    def test_install_streams_validates_and_atomically_installs(self):
        progress = []
        status = self.manager.install(
            lambda downloaded, total: progress.append((downloaded, total)))
        self.assertTrue(status.available, status.error)
        self.assertEqual(status.data_version, "fixture-1")
        self.assertTrue(os.path.isfile(self.manager.path))
        self.assertTrue(progress)
        self.assertEqual(progress[-1], (
            self.artifact.size, self.artifact.size))
        self.assertEqual([
            name for name in os.listdir(self.install_dir.name)
            if name.startswith(".dictionary-download-")
        ], [])

    def test_hash_failure_preserves_existing_valid_artifact(self):
        self.manager.install()
        with open(self.manager.path, "rb") as stream:
            before = stream.read()
        invalid = DictionaryArtifact(
            url=self.artifact.url,
            sha256="0" * 64,
            size=self.artifact.size,
            data_version=self.artifact.data_version,
        )
        manager = DictionaryArtifactManager(self.install_dir.name, invalid)
        with self.assertRaisesRegex(DictionaryArtifactError, "SHA-256"):
            manager.install()
        with open(self.manager.path, "rb") as stream:
            self.assertEqual(stream.read(), before)

    def test_wrong_data_version_is_rejected(self):
        incompatible = DictionaryArtifact(
            url=self.artifact.url,
            sha256=self.artifact.sha256,
            size=self.artifact.size,
            data_version="other-version",
        )
        manager = DictionaryArtifactManager(
            self.install_dir.name, incompatible)
        with self.assertRaisesRegex(DictionaryArtifactError, "version mismatch"):
            manager.install()
        self.assertFalse(os.path.exists(manager.path))

    def test_cancel_removes_temporary_download(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(DictionaryDownloadCancelled):
            self.manager.install(cancel_event=cancel)
        self.assertFalse(os.path.exists(self.manager.path))
        self.assertEqual(os.listdir(self.install_dir.name), [])

    def test_delete_removes_only_installed_artifact(self):
        self.manager.install()
        neighbor = os.path.join(self.install_dir.name, "keep.txt")
        with open(neighbor, "w", encoding="utf-8") as stream:
            stream.write("keep")
        self.assertTrue(self.manager.delete())
        self.assertFalse(os.path.exists(self.manager.path))
        self.assertTrue(os.path.isfile(neighbor))
        self.assertFalse(self.manager.delete())


class TestProductionArtifactMetadata(unittest.TestCase):
    def test_windows_facade_reexports_pins_and_errors_without_changing_defaults(self):
        import cc_dictionary_artifact as windows
        import cc_dictionary_artifact_core as portable
        for name in ("DictionaryArtifact", "DictionaryArtifactError", "DictionaryDownloadCancelled"):
            self.assertIs(getattr(windows, name), getattr(portable, name))
        for name in ("ARTIFACT_FILENAME", "ARTIFACT_SIZE", "ARTIFACT_SHA256", "ARTIFACT_DATA_VERSION",
                     "ARTIFACT_RELEASE_TAG", "ARTIFACT_RELEASE_NAME", "ARTIFACT_URL"):
            self.assertEqual(getattr(windows, name), getattr(portable, name))
        with patch.object(windows.os, "makedirs", side_effect=AssertionError("implicit mkdir")):
            manager = windows.DictionaryArtifactManager()
        self.assertEqual(manager.path, windows.INSTALLED_DICTIONARY_PATH)
        self.assertEqual(manager.directory, windows.DICTIONARY_DIR)
        self.assertEqual(manager.artifact, portable.DictionaryArtifact())

    def test_default_lookup_uses_per_user_artifact(self):
        self.assertEqual(DEFAULT_DICTIONARY_PATH, INSTALLED_DICTIONARY_PATH)
        self.assertNotEqual(DEFAULT_DICTIONARY_PATH, DEVELOPMENT_DICTIONARY_PATH)

    @unittest.skipUnless(
        os.path.isfile(DEVELOPMENT_DICTIONARY_PATH),
        "full release artifact is not present in this source checkout",
    )
    def test_pinned_metadata_matches_release_artifact(self):
        self.assertEqual(os.path.getsize(DEVELOPMENT_DICTIONARY_PATH),
                         ARTIFACT_SIZE)
        with open(DEVELOPMENT_DICTIONARY_PATH, "rb") as stream:
            self.assertEqual(
                hashlib.file_digest(stream, "sha256").hexdigest(),
                ARTIFACT_SHA256)
        from cc_dictionary import LocalDictionary
        dictionary = LocalDictionary(DEVELOPMENT_DICTIONARY_PATH)
        self.addCleanup(dictionary.close_thread)
        self.assertTrue(dictionary.status.available, dictionary.status.error)
        self.assertEqual(dictionary.status.data_version, ARTIFACT_DATA_VERSION)


if __name__ == "__main__":
    unittest.main()
