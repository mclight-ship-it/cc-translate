import hashlib
import os
from pathlib import Path
import tempfile
import threading
import unittest

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
