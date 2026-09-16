"""Pure dictionary contracts with explicit, synthetic, repository-local state."""

import base64
from dataclasses import FrozenInstanceError, replace
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

import cc_dictionary_artifact_core as artifact
import cc_dictionary_lookup as lookup
from cc_dictionary_format import FORMATTER_VERSION, format_dictionary_result
from cc_dictionary_store import SCHEMA, DictionaryStore, DictionaryStoreError, StoreStatus


class _DictionaryDirectory(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix=".dictionary-portable-", dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "synthetic \u4e2d # %.sqlite3"
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                connection.executescript(SCHEMA)
                connection.executemany("INSERT INTO metadata VALUES (?, ?)", (
                    ("schema_version", "1"), ("data_version", "fixture-1"),
                    ("content_sha256", "b" * 64),
                ))
                connection.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)", (
                    "fixture", "Fixture source", "1", "Test license", "https://example.invalid", "a" * 64,
                ))
                connection.executemany("INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                    (1, "run", "run", "en", "/r\u028cn/", "verb", "fixture", "fixture:run", 10),
                    (2, "\u4e2d\u56fd", "\u4e2d\u56fd", "zh", "Zhong1 guo2", None, "fixture", "fixture:china", 10),
                ))
                connection.executemany("INSERT INTO senses VALUES (?, ?, ?, ?, ?, ?)", (
                    (1, 1, 1, "\u8dd1", "fixture", "fixture:run:sense"),
                    (2, 2, 1, "China", "fixture", "fixture:china:sense"),
                ))
                connection.execute("INSERT INTO forms VALUES (?, ?, ?, ?)", ("ran", 1, "ran", "fixture:past"))
                connection.execute("INSERT INTO aliases VALUES (?, ?, ?, ?, ?)", (
                    "\u4e2d\u570b", 2, "\u4e2d\u570b", "traditional", "fixture:traditional",
                ))
        finally:
            connection.close()
        self.payload = self.path.read_bytes()
        self.sha256 = hashlib.sha256(self.payload).hexdigest()


class TestPortableDictionaryLookup(_DictionaryDirectory):
    def dictionary(self, expected_sha256=None):
        dictionary = lookup.LocalDictionary(str(self.path), self.sha256 if expected_sha256 is None else expected_sha256)
        self.addCleanup(dictionary.close_thread)
        return dictionary

    def test_explicit_path_and_hash_are_required_without_default_path_discovery(self):
        with self.assertRaises(TypeError):
            lookup.LocalDictionary()
        with self.assertRaises(TypeError):
            lookup.LocalDictionary(str(self.path))
        self.assertFalse(hasattr(lookup, "DEFAULT_DICTIONARY_PATH"))
        unpinned = lookup.LocalDictionary(str(self.path), None)
        self.addCleanup(unpinned.close_thread)
        self.assertTrue(unpinned.status.available)

    def test_nfkc_exact_forms_and_aliases_preserve_structured_source_fields(self):
        dictionary = self.dictionary()
        for query, headword, kind, confidence in (
                (" \uff32\uff35\uff2e ", "run", "exact", 1.0),
                ("RAN", "run", "form", 0.96),
                ("\u4e2d\u570b", "\u4e2d\u56fd", "alias", 0.98)):
            result = dictionary.lookup(query)
            self.assertEqual((result.query, result.headword, result.match_type, result.confidence),
                             (query, headword, kind, confidence))
            self.assertEqual(result.normalized_query, lookup.normalize_query(query))
            self.assertEqual(result.source_ids, ("fixture",))
            self.assertEqual(result.entries[0].source_license, "Test license")
            self.assertTrue(result.senses[0].provenance.startswith("fixture:"))
            self.assertTrue(result.is_high_confidence)
            with self.assertRaises(FrozenInstanceError):
                result.headword = "changed"
        self.assertEqual(lookup.normalize_query("Stra\u00dfe"), "strasse")
        self.assertIsNone(dictionary.lookup("running"))
        self.assertIsNone(dictionary.lookup(" \t"))
        self.assertIsNone(dictionary.lookup("run' OR 1=1 --"))

    def test_match_precedence_is_exact_then_form_then_alias_not_confidence_sort(self):
        dictionary = self.dictionary()
        one = dictionary.store.lookup("run")["exact"][0]
        two = dictionary.store.lookup("\u4e2d\u56fd")["exact"][0]
        for matches, expected in (
                ({"exact": [one], "form": [two], "alias": [two]}, ("run", "exact")),
                ({"exact": [], "form": [one], "alias": [two]}, ("run", "form")),
                ({"exact": [], "form": [], "alias": [two]}, ("\u4e2d\u56fd", "alias"))):
            with patch.object(dictionary.store, "lookup", return_value=matches):
                result = dictionary.lookup("synthetic")
            self.assertEqual((result.headword, result.match_type), expected)
        with patch.object(dictionary.store, "lookup", return_value={
                "exact": [replace(one, senses=())], "form": [two], "alias": []}):
            self.assertIsNone(dictionary.lookup("synthetic"))

    def test_confidence_requires_senses_and_preserves_threshold(self):
        result = self.dictionary().lookup("run")
        self.assertFalse(replace(result, confidence=0.899).is_high_confidence)
        self.assertTrue(replace(result, confidence=0.90).is_high_confidence)
        self.assertFalse(replace(result, senses=()).is_high_confidence)

    def test_pin_failure_and_missing_path_do_not_use_any_fallback(self):
        for pin in ("", "0" * 64, "bad"):
            dictionary = self.dictionary(pin)
            self.assertFalse(dictionary.status.available)
            with self.assertRaises(DictionaryStoreError):
                dictionary.lookup("run")
        missing = self.directory / "missing.sqlite3"
        dictionary = lookup.LocalDictionary(str(missing), self.sha256)
        self.assertFalse(dictionary.status.available)
        self.assertFalse(missing.exists())

    def test_version_close_reopen_and_read_only_behavior_are_unchanged(self):
        dictionary = self.dictionary()
        self.assertEqual(dictionary.cache_version, "query-v1:1:fixture-1:" + "b" * 16)
        expected = dictionary.lookup("run")
        connection = dictionary.store._connection()
        connection.execute("PRAGMA query_only=OFF")
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("DELETE FROM entries")
        dictionary.close_thread()
        dictionary.close_thread()
        self.assertEqual(dictionary.lookup("run"), expected)
        self.assertEqual(self.path.read_bytes(), self.payload)

    def test_existing_formatter_output_bytes_and_revision_are_preserved(self):
        result = self.dictionary().lookup("run")
        def encoded(value):
            return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")
        sources = json.dumps([{"id": "fixture", "label": "Fixture source", "version": "1",
                               "license": "Test license"}], ensure_ascii=False, separators=(",", ":"))
        expected = ("## run [[cc-instant]]\n[[cc-pron:" + encoded("/r\u028cn/") + "]]\n\n"
                    "*verb*\n- \u8dd1\n\n[[cc-sources:" + encoded(sources) + "]]")
        self.assertEqual(format_dictionary_result(result).encode("utf-8"), expected.encode("utf-8"))
        self.assertEqual(format_dictionary_result(result, expanded=True), expected)
        self.assertEqual(FORMATTER_VERSION, "format-v8")
        chinese = format_dictionary_result(self.dictionary().lookup("\u4e2d\u570b"))
        self.assertIn("[[cc-pron:" + encoded("Zh\u014dng gu\u00f3") + "]]", chinese)

    def test_pure_imports_never_resolve_windows_paths_create_state_or_load_provider(self):
        script = r"""
import builtins, importlib.abc, os, pathlib, socket, sqlite3, sys, tempfile, urllib.request
sys.path.insert(0, sys.argv[1])
def forbidden(*args, **kwargs): raise AssertionError("implicit state or network")
blocked = {"cc_core", "cc_dictionary", "cc_dictionary_artifact", "tkinter", "cc_providers", "win32api", "win32gui"}
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in blocked: raise AssertionError("forbidden import: " + fullname)
sys.meta_path.insert(0, BlockImports())
class ForbiddenEnvironment(dict):
    __getitem__ = get = __iter__ = items = keys = values = copy = forbidden
os.environ = ForbiddenEnvironment()
os.getenv = os.makedirs = pathlib.Path.home = pathlib.Path.mkdir = forbidden
tempfile.mkstemp = tempfile.mkdtemp = urllib.request.urlopen = socket.create_connection = forbidden
sqlite3.connect = forbidden
import cc_dictionary_lookup, cc_dictionary_artifact_core, cc_dictionary_format
assert cc_dictionary_lookup.normalize_query(" RUN ") == "run"
assert cc_dictionary_artifact_core.DictionaryArtifact().size == 67948544
assert not blocked.intersection(sys.modules)
"""
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(root)],
                                cwd=self.directory, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(self.directory.iterdir()), {self.path})


class _Response(io.BytesIO):
    def __init__(self, data, *, status=200, length=None):
        super().__init__(data)
        self.status = status
        self.headers = {} if length is None else {"Content-Length": length}


class TestPortableDictionaryInstaller(_DictionaryDirectory):
    def setUp(self):
        super().setUp()
        self.pin = artifact.DictionaryArtifact(
            url="https://example.invalid/pinned", sha256=self.sha256,
            size=len(self.payload), data_version="fixture-1")
        self.destination = self.directory / "installed"
        self.response = _Response(self.payload, length=str(len(self.payload)))
        self.opener = Mock(return_value=self.response)
        self.manager = artifact.DictionaryArtifactManager(str(self.destination), self.pin, self.opener)

    def assert_no_staging(self):
        if self.destination.exists():
            self.assertEqual(list(self.destination.glob(".dictionary-download-*")), [])

    def test_explicit_directory_and_pin_are_required_and_construction_has_no_io(self):
        with self.assertRaises(TypeError):
            artifact.DictionaryArtifactManager()
        with self.assertRaises(TypeError):
            artifact.DictionaryArtifactManager(str(self.destination))
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.manager.inspect().available)
        self.opener.assert_not_called()
        self.assertFalse(self.destination.exists())
        self.assertFalse(hasattr(artifact, "INSTALLED_DICTIONARY_PATH"))
        self.assertEqual(artifact.DictionaryArtifact().sha256,
                         "3695295d07268725e555471217351c74e4f9375b8152974760fe12b926e1830e")
        with self.assertRaises(FrozenInstanceError):
            self.pin.size = 0

    def test_install_preserves_bytes_headers_progress_pin_and_final_status(self):
        progress = []
        commit = Mock(return_value=True)
        status = self.manager.install(lambda done, total: progress.append((done, total)), begin_commit=commit)
        self.assertTrue(status.available)
        self.assertEqual((status.path, status.data_version), (self.manager.path, "fixture-1"))
        self.assertEqual(Path(self.manager.path).read_bytes(), self.payload)
        self.assertEqual(progress[-1], (len(self.payload), len(self.payload)))
        self.opener.assert_called_once()
        request = self.opener.call_args.args[0]
        self.assertEqual(request.full_url, self.pin.url)
        self.assertEqual(dict(request.header_items()), {
            "User-agent": "CC-Translate-dictionary-installer/1", "Accept-encoding": "identity",
        })
        self.assertEqual(self.opener.call_args.kwargs, {"timeout": 30.0})
        self.assertTrue(self.response.closed)
        commit.assert_called_once_with()
        self.assert_no_staging()

    def test_completed_staged_file_installs_without_network_or_new_staging(self):
        self.destination.mkdir()
        staged = self.destination / "native-completed.sqlite3"
        staged.write_bytes(self.payload)
        commit = Mock(return_value=True)
        with patch.object(artifact.tempfile, "mkstemp", side_effect=AssertionError("unexpected staging")), \
                patch.object(artifact.os, "makedirs", side_effect=AssertionError("unexpected mkdir")):
            status = self.manager.install_staged(staged, begin_commit=commit)
        self.assertTrue(status.available)
        self.assertEqual((status.path, status.data_version), (self.manager.path, "fixture-1"))
        self.assertEqual(Path(self.manager.path).read_bytes(), self.payload)
        self.assertFalse(staged.exists())
        self.opener.assert_not_called()
        commit.assert_called_once_with()

    def test_downloader_reuses_the_same_public_staged_commit(self):
        with patch.object(self.manager, "install_staged", wraps=self.manager.install_staged) as install_staged:
            self.assertTrue(self.manager.install().available)
        install_staged.assert_called_once()
        self.assertEqual(Path(install_staged.call_args.args[0]).parent, self.destination)
        self.assertEqual(install_staged.call_args.args[1], None)
        self.assertEqual(install_staged.call_args.kwargs, {"begin_commit": None})

    def test_staged_file_syncs_writable_handle_and_closes_it_before_commit_and_replace(self):
        self.destination.mkdir()
        staged = self.destination / "native-completed.sqlite3"
        staged.write_bytes(self.payload)
        order, streams = [], []
        fsync, replace_file = os.fsync, os.replace
        def open_staged(path, mode):
            stream = open(path, mode)
            streams.append(stream)
            return stream
        def sync(fd):
            self.assertTrue(streams[-1].writable())
            self.assertEqual(fd, streams[-1].fileno())
            order.append("sync")
            fsync(fd)
        def commit():
            self.assertTrue(streams[-1].closed)
            order.append("commit")
            return True
        def replace_staged(source, destination):
            order.append("replace")
            replace_file(source, destination)
        with patch.object(artifact, "open", side_effect=open_staged, create=True) as open_file, \
                patch.object(artifact.os, "fsync", side_effect=sync), \
                patch.object(artifact.os, "replace", side_effect=replace_staged):
            self.assertTrue(self.manager.install_staged(staged, begin_commit=commit).available)
        open_file.assert_called_once_with(str(staged), "r+b")
        self.assertEqual(order, ["sync", "commit", "replace"])
        self.assertEqual(Path(self.manager.path).read_bytes(), self.payload)
        self.assertFalse(staged.exists())
        self.opener.assert_not_called()

    def test_staged_sync_failure_preserves_installed_file_and_staging_without_reserving_commit(self):
        self.destination.mkdir()
        installed = Path(self.manager.path)
        installed.write_bytes(self.payload)
        staged = self.destination / "native-completed.sqlite3"
        staged.write_bytes(self.payload)
        commit = Mock(return_value=True)
        failure = OSError("synthetic sync failure")
        with patch.object(artifact.os, "fsync", side_effect=failure) as sync, \
                patch.object(artifact.os, "replace") as replace_file, \
                self.assertRaises(OSError) as raised:
            self.manager.install_staged(staged, begin_commit=commit)
        self.assertIs(raised.exception, failure)
        sync.assert_called_once()
        commit.assert_not_called()
        replace_file.assert_not_called()
        self.assertEqual(installed.read_bytes(), self.payload)
        self.assertEqual(staged.read_bytes(), self.payload)
        self.opener.assert_not_called()

    def test_staged_cancel_during_sync_preserves_files_without_reserving_commit(self):
        self.destination.mkdir()
        installed = Path(self.manager.path)
        installed.write_bytes(self.payload)
        staged = self.destination / "native-completed.sqlite3"
        staged.write_bytes(self.payload)
        cancel = threading.Event()
        commit = Mock(return_value=True)
        fsync = os.fsync
        def cancel_sync(fd):
            fsync(fd)
            cancel.set()
        with patch.object(artifact.os, "fsync", side_effect=cancel_sync) as sync, \
                patch.object(artifact.os, "replace") as replace_file, \
                self.assertRaises(artifact.DictionaryDownloadCancelled):
            self.manager.install_staged(staged, cancel, begin_commit=commit)
        sync.assert_called_once()
        commit.assert_not_called()
        replace_file.assert_not_called()
        self.assertEqual(installed.read_bytes(), self.payload)
        self.assertEqual(staged.read_bytes(), self.payload)
        self.opener.assert_not_called()

    def test_staged_path_must_be_owned_regular_absolute_and_distinct_from_installed_path(self):
        self.destination.mkdir()
        Path(self.manager.path).write_bytes(b"previous")
        for staged in (None, b"path", "relative.sqlite3", self.path,
                       self.destination / "missing.sqlite3", self.destination / "subdirectory",
                       Path(self.manager.path)):
            if staged == self.destination / "subdirectory":
                staged.mkdir()
            with self.subTest(staged=str(staged)), self.assertRaises(artifact.DictionaryArtifactError):
                self.manager.install_staged(staged)
        self.assertEqual(Path(self.manager.path).read_bytes(), b"previous")
        self.assertEqual(self.path.read_bytes(), self.payload)
        self.opener.assert_not_called()

    def test_staged_symlink_is_rejected_without_consuming_its_target(self):
        self.destination.mkdir()
        staged = self.destination / "linked.sqlite3"
        try:
            staged.symlink_to(self.path)
        except (OSError, NotImplementedError) as error:
            self.skipTest("Symlink creation unavailable: " + type(error).__name__)
        with self.assertRaisesRegex(artifact.DictionaryArtifactError, "regular non-symlink"):
            self.manager.install_staged(staged)
        self.assertTrue(staged.is_symlink())
        self.assertEqual(self.path.read_bytes(), self.payload)
        self.opener.assert_not_called()

    def test_staged_size_hash_schema_and_version_checks_keep_caller_owned_file_on_failure(self):
        self.destination.mkdir()
        Path(self.manager.path).write_bytes(b"previous")
        staged = self.destination / "native-completed.sqlite3"
        cases = (
            (self.payload[:-1], self.pin, "staged dictionary size mismatch"),
            (self.payload, replace(self.pin, sha256="0" * 64), "SHA-256 mismatch"),
            (b"broken", replace(self.pin, size=6, sha256=hashlib.sha256(b"broken").hexdigest()),
             "downloaded dictionary is invalid"),
            (self.payload, replace(self.pin, data_version="other"), "data version mismatch"),
        )
        for data, pin, error in cases:
            staged.write_bytes(data)
            manager = artifact.DictionaryArtifactManager(str(self.destination), pin, self.opener)
            commit = Mock(return_value=True)
            with self.subTest(error=error), self.assertRaisesRegex(artifact.DictionaryArtifactError, error):
                manager.install_staged(staged, begin_commit=commit)
            self.assertEqual(staged.read_bytes(), data)
            self.assertEqual(Path(manager.path).read_bytes(), b"previous")
            commit.assert_not_called()
        self.opener.assert_not_called()

    def test_staged_cancel_and_commit_refusal_leave_cleanup_with_caller(self):
        self.destination.mkdir()
        staged = self.destination / "native-completed.sqlite3"
        staged.write_bytes(self.payload)
        cancel = threading.Event()
        cancel.set()
        commit = Mock(return_value=True)
        with patch.object(self.manager, "_store_status", side_effect=AssertionError("unexpected validation")), \
                self.assertRaises(artifact.DictionaryDownloadCancelled):
            self.manager.install_staged(staged, cancel, begin_commit=commit)
        commit.assert_not_called()
        cancel.clear()
        with self.assertRaises(artifact.DictionaryDownloadCancelled):
            self.manager.install_staged(staged, cancel, begin_commit=lambda: False)
        self.assertEqual(staged.read_bytes(), self.payload)
        self.assertFalse(Path(self.manager.path).exists())
        self.opener.assert_not_called()

    def test_staged_commit_reservation_ignores_late_cancel_and_does_not_claim_rollback(self):
        self.destination.mkdir()
        staged = self.destination / "native-completed.sqlite3"
        staged.write_bytes(self.payload)
        cancel = threading.Event()
        def reserve():
            cancel.set()
            return True
        self.assertTrue(self.manager.install_staged(staged, cancel, begin_commit=reserve).available)
        self.assertFalse(staged.exists())
        self.assertEqual(Path(self.manager.path).read_bytes(), self.payload)
        self.opener.assert_not_called()

    def test_staged_replace_failure_keeps_staging_but_postcommit_failure_keeps_installed_file(self):
        self.destination.mkdir()
        staged = self.destination / "native-completed.sqlite3"
        staged.write_bytes(self.payload)
        with patch.object(artifact.os, "replace", side_effect=OSError("synthetic replace")), \
                self.assertRaisesRegex(OSError, "^synthetic replace$"):
            self.manager.install_staged(staged, begin_commit=lambda: True)
        self.assertEqual(staged.read_bytes(), self.payload)
        self.assertFalse(Path(self.manager.path).exists())
        with patch.object(self.manager, "inspect", return_value=StoreStatus(False, self.manager.path, error="synthetic")), \
                self.assertRaisesRegex(artifact.DictionaryArtifactError, "installed dictionary is invalid"):
            self.manager.install_staged(staged, begin_commit=lambda: True)
        self.assertFalse(staged.exists())
        self.assertEqual(Path(self.manager.path).read_bytes(), self.payload)
        self.opener.assert_not_called()

    def test_precancel_never_creates_directory_or_opens_network(self):
        cancel = threading.Event()
        cancel.set()
        commit = Mock(return_value=True)
        with patch.object(artifact.os, "makedirs", side_effect=AssertionError("mkdir")), \
                self.assertRaises(artifact.DictionaryDownloadCancelled):
            self.manager.install(cancel_event=cancel, begin_commit=commit)
        self.opener.assert_not_called()
        commit.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_cancel_before_open_cleans_staging_and_never_opens_network(self):
        cancel = threading.Event()
        request = self.manager._request
        def cancelling_request(*args, **kwargs):
            cancel.set()
            return request(*args, **kwargs)
        with patch.object(self.manager, "_request", side_effect=cancelling_request), \
                self.assertRaises(artifact.DictionaryDownloadCancelled):
            self.manager.install(cancel_event=cancel)
        self.opener.assert_not_called()
        self.assert_no_staging()

    def test_stream_cancel_closes_response_and_preserves_previous_file(self):
        self.destination.mkdir()
        Path(self.manager.path).write_bytes(b"previous")
        cancel = threading.Event()
        commit = Mock(return_value=True)
        with self.assertRaises(artifact.DictionaryDownloadCancelled):
            self.manager.install(lambda _done, _total: cancel.set(), cancel, begin_commit=commit)
        self.assertEqual(Path(self.manager.path).read_bytes(), b"previous")
        self.assertTrue(self.response.closed)
        commit.assert_not_called()
        self.assert_no_staging()

    def test_cancel_after_validation_prevents_replace_and_commit_callback(self):
        cancel = threading.Event()
        status_for = self.manager._store_status
        def validated(path):
            status = status_for(path)
            self.assertTrue(status.available)
            cancel.set()
            return status
        commit = Mock(return_value=True)
        with patch.object(self.manager, "_store_status", side_effect=validated), \
                patch.object(artifact.os, "replace") as replace_file, \
                self.assertRaises(artifact.DictionaryDownloadCancelled):
            self.manager.install(cancel_event=cancel, begin_commit=commit)
        replace_file.assert_not_called()
        commit.assert_not_called()
        self.assert_no_staging()

    def test_commit_refusal_preserves_previous_file_after_complete_verification(self):
        self.destination.mkdir()
        Path(self.manager.path).write_bytes(b"previous")
        def refuse():
            staged, = self.destination.glob(".dictionary-download-*")
            self.assertTrue(DictionaryStore(str(staged), self.sha256).status.available)
            self.assertEqual(Path(self.manager.path).read_bytes(), b"previous")
            return False
        with patch.object(artifact.os, "replace") as replace_file, \
                self.assertRaises(artifact.DictionaryDownloadCancelled):
            self.manager.install(begin_commit=refuse)
        replace_file.assert_not_called()
        self.assertEqual(Path(self.manager.path).read_bytes(), b"previous")
        self.assert_no_staging()

    def test_reserved_commit_ignores_later_cancel_without_pretending_rollback(self):
        cancel = threading.Event()
        def reserve():
            cancel.set()
            return True
        self.assertTrue(self.manager.install(cancel_event=cancel, begin_commit=reserve).available)
        self.assertEqual(Path(self.manager.path).read_bytes(), self.payload)
        self.assert_no_staging()

    def test_cancel_during_replace_does_not_change_success_to_cancelled(self):
        cancel = threading.Event()
        replace_file = artifact.os.replace
        def replace_then_cancel(source, destination):
            replace_file(source, destination)
            cancel.set()
        with patch.object(artifact.os, "replace", side_effect=replace_then_cancel):
            self.assertTrue(self.manager.install(cancel_event=cancel).available)
        self.assertEqual(Path(self.manager.path).read_bytes(), self.payload)

    def test_postcommit_inspection_failure_reports_error_without_removing_installed_file(self):
        inspect = Mock(return_value=StoreStatus(False, self.manager.path, error="synthetic"))
        with patch.object(self.manager, "inspect", inspect), \
                self.assertRaisesRegex(artifact.DictionaryArtifactError, "^installed dictionary is invalid: synthetic$"):
            self.manager.install(begin_commit=lambda: True)
        self.assertEqual(Path(self.manager.path).read_bytes(), self.payload)
        self.assert_no_staging()

    def test_replace_failure_keeps_original_error_previous_file_and_cleans_staging(self):
        self.destination.mkdir()
        Path(self.manager.path).write_bytes(b"previous")
        with patch.object(artifact.os, "replace", side_effect=OSError("synthetic replace")), \
                self.assertRaisesRegex(OSError, "^synthetic replace$"):
            self.manager.install(begin_commit=lambda: True)
        self.assertEqual(Path(self.manager.path).read_bytes(), b"previous")
        self.assert_no_staging()

    def test_header_size_hash_schema_and_data_version_fail_before_commit(self):
        cases = (
            (self.payload, self.pin, 503, None, "returned HTTP 503"),
            (self.payload, self.pin, 200, "bad", "invalid size"),
            (self.payload, self.pin, 200, "1", "size mismatch"),
            (self.payload[:-1], self.pin, 200, None, "incomplete"),
            (self.payload + b"x", self.pin, 200, None, "exceeds expected size"),
            (self.payload, replace(self.pin, sha256="0" * 64), 200, None, "SHA-256 mismatch"),
            (b"broken", replace(self.pin, size=6, sha256=hashlib.sha256(b"broken").hexdigest()), 200, None,
             "downloaded dictionary is invalid"),
            (self.payload, replace(self.pin, data_version="other"), 200, None, "data version mismatch"),
        )
        self.destination.mkdir()
        Path(self.manager.path).write_bytes(b"previous")
        for data, pin, status, length, error in cases:
            with self.subTest(error=error):
                response = _Response(data, status=status, length=length)
                manager = artifact.DictionaryArtifactManager(str(self.destination), pin, Mock(return_value=response))
                commit = Mock(return_value=True)
                with self.assertRaisesRegex(artifact.DictionaryArtifactError, error):
                    manager.install(begin_commit=commit)
                commit.assert_not_called()
                self.assertEqual(Path(manager.path).read_bytes(), b"previous")
                self.assertTrue(response.closed)
                self.assert_no_staging()

    def test_opener_and_callback_failures_clean_staging_and_keep_error_contracts(self):
        self.opener.side_effect = URLError("synthetic")
        with self.assertRaisesRegex(artifact.DictionaryArtifactError, "^cannot download dictionary:"):
            self.manager.install()
        self.assert_no_staging()
        self.opener.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "^synthetic progress$"):
            self.manager.install(Mock(side_effect=RuntimeError("synthetic progress")))
        self.assert_no_staging()
        self.manager._opener = Mock(return_value=_Response(self.payload))
        with self.assertRaisesRegex(RuntimeError, "^synthetic commit$"):
            self.manager.install(begin_commit=Mock(side_effect=RuntimeError("synthetic commit")))
        self.assert_no_staging()

    def test_delete_only_removes_installed_file_and_does_not_use_network(self):
        self.destination.mkdir()
        Path(self.manager.path).write_bytes(b"synthetic")
        neighbor = self.destination / "keep.txt"
        neighbor.write_text("keep", encoding="utf-8")
        self.assertTrue(self.manager.delete())
        self.assertFalse(self.manager.delete())
        self.assertEqual(neighbor.read_text(encoding="utf-8"), "keep")
        self.opener.assert_not_called()
        with patch.object(artifact.os, "unlink", side_effect=OSError("synthetic")), \
                self.assertRaisesRegex(artifact.DictionaryArtifactError, "^cannot delete dictionary: synthetic$"):
            self.manager.delete()


if __name__ == "__main__":
    unittest.main()
