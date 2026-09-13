import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cc_storage as storage
from cc_macos.storage_fixture import probe_storage


class MacPathTests(unittest.TestCase):
    def test_explicit_paths_preserve_characters_without_creating_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "\u4e2d space # %"
            with patch.object(Path, "home", side_effect=AssertionError("home lookup")), \
                    patch.object(Path, "resolve", side_effect=AssertionError("filesystem lookup")):
                paths = storage.macos_user_paths(home, "test.synthetic-storage")
            self.assertEqual(paths.application_support,
                             home / "Library" / "Application Support" / "test.synthetic-storage")
            self.assertEqual(paths.caches, home / "Library" / "Caches" / "test.synthetic-storage")
            self.assertFalse(home.exists())
            with self.assertRaises(AttributeError):
                paths.caches = home

    def test_missing_relative_parent_and_bundle_home_are_rejected(self):
        absolute = Path(tempfile.gettempdir())
        for home in ("", ".", "~", "relative", absolute / ".." / "home",
                     absolute / "Test.app" / "Contents", absolute / "TEST.APP"):
            with self.subTest(home=home), self.assertRaises(ValueError):
                storage.macos_user_paths(home, "test.synthetic-storage")

    def test_identity_is_explicit_and_cannot_escape_its_directory(self):
        for identity in ("", ".", "..", "/tmp", "a/b", "a\\b", "a\0b", "a b", None, 1):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                storage.macos_user_paths(Path(tempfile.gettempdir()), identity)


class AtomicJSONTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "\u4e2d # %"
        self.root.mkdir()
        self.target = self.root / "data space # %.json"
        self.previous = b'{"previous":true}'
        self.target.write_bytes(self.previous)
        self.sibling = self.root / ".tmp_other-operation.json"
        self.sibling.write_bytes(b"unrelated")

    def assert_preserved(self):
        self.assertEqual(self.target.read_bytes(), self.previous)
        self.assertEqual(self.sibling.read_bytes(), b"unrelated")
        self.assertEqual(set(self.root.iterdir()), {self.target, self.sibling})

    def test_serialization_bytes_roundtrip_and_replace(self):
        for payload in ({"\u4e2d": "space # %\ntext", "nested": [None, True, 1.5]},
                        [], None, {"not_finite": float("inf")}):
            with self.subTest(payload=payload):
                storage.atomic_write_json(self.target, payload)
                expected = json.dumps(payload, ensure_ascii=False, indent=2)
                self.assertEqual(self.target.read_bytes(), expected.replace("\n", os.linesep).encode())
                with self.target.open(encoding="utf-8") as stream:
                    self.assertEqual(json.load(stream), payload)
                self.assertEqual(set(self.root.iterdir()), {self.target, self.sibling})

    def test_flush_fsync_close_then_replace_with_same_directory_unique_temporary(self):
        events, temporary_names, descriptors = [], [], []
        real_mkstemp, real_fdopen = tempfile.mkstemp, os.fdopen
        real_fsync, real_replace = os.fsync, os.replace

        def create(**kwargs):
            self.assertEqual(kwargs, {"prefix": ".tmp_", "suffix": ".json", "dir": str(self.root)})
            fd, name = real_mkstemp(**kwargs)
            descriptors.append(fd)
            temporary_names.append(name)
            return fd, name

        class Stream:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                result = self.stream.__exit__(*args)
                events.append("stream-close")
                return result

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def flush(self):
                events.append("flush")
                return self.stream.flush()

        def sync(fd):
            events.append("fsync")
            self.assertEqual(self.target.read_bytes(), self.previous)
            return real_fsync(fd)

        def replace(source, target):
            events.append("replace")
            with self.assertRaises(OSError):
                os.fstat(descriptors[-1])
            self.assertEqual(json.loads(Path(source).read_bytes()), {"new": True})
            self.assertEqual(self.target.read_bytes(), self.previous)
            return real_replace(source, target)

        with patch.object(storage.tempfile, "mkstemp", side_effect=create), \
                patch.object(storage.os, "fdopen", side_effect=lambda *a, **k: Stream(real_fdopen(*a, **k))), \
                patch.object(storage.os, "fsync", side_effect=sync), \
                patch.object(storage.os, "replace", side_effect=replace):
            for _ in range(2):
                storage.atomic_write_json(self.target, {"new": True})
                self.target.write_bytes(self.previous)
        self.assertEqual(events, ["flush", "fsync", "stream-close", "replace"] * 2)
        self.assertEqual(len(set(temporary_names)), 2)
        self.assert_preserved()

    def test_temp_creation_failure_does_not_change_old_file(self):
        error = PermissionError("synthetic create")
        with patch.object(storage.tempfile, "mkstemp", side_effect=error):
            with self.assertRaises(PermissionError) as raised:
                storage.atomic_write_json(self.target, {})
        self.assertIs(raised.exception, error)
        self.assert_preserved()

    def test_fdopen_failure_closes_owned_descriptor_and_cleans_only_own_temp(self):
        real_mkstemp = tempfile.mkstemp
        descriptors = []

        def create(**kwargs):
            fd, name = real_mkstemp(**kwargs)
            descriptors.append(fd)
            return fd, name

        error = OSError("synthetic fdopen")
        with patch.object(storage.tempfile, "mkstemp", side_effect=create), \
                patch.object(storage.os, "fdopen", side_effect=error):
            with self.assertRaises(OSError) as raised:
                storage.atomic_write_json(self.target, {})
        self.assertIs(raised.exception, error)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])
        self.assert_preserved()

    def test_partial_serialization_failure_keeps_original_exception_and_bytes(self):
        error = ValueError("synthetic dump")

        def dump(data, stream, **kwargs):
            self.assertEqual(kwargs, {"ensure_ascii": False, "indent": 2})
            stream.write('{"partial":')
            raise error

        with patch.object(storage.json, "dump", side_effect=dump):
            with self.assertRaises(ValueError) as raised:
                storage.atomic_write_json(self.target, {})
        self.assertIs(raised.exception, error)
        self.assert_preserved()

    def test_flush_failure_preserves_old_file_and_closes_descriptor(self):
        real_fdopen = os.fdopen
        error = OSError("synthetic flush")
        descriptors = []

        class Stream:
            def __init__(self, *args, **kwargs):
                descriptors.append(args[0])
                self.stream = real_fdopen(*args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def flush(self):
                raise error

        with patch.object(storage.os, "fdopen", side_effect=Stream):
            with self.assertRaises(OSError) as raised:
                storage.atomic_write_json(self.target, {})
        self.assertIs(raised.exception, error)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])
        self.assert_preserved()

    def test_fsync_and_replace_failures_preserve_old_file_and_cleanup(self):
        for operation in ("fsync", "replace"):
            error = OSError("synthetic " + operation)
            with self.subTest(operation=operation), patch.object(storage.os, operation, side_effect=error):
                with self.assertRaises(OSError) as raised:
                    storage.atomic_write_json(self.target, {})
            self.assertIs(raised.exception, error)
            self.assert_preserved()

    def test_legacy_cleanup_failure_does_not_mask_primary_failure(self):
        error = PermissionError("synthetic replace")
        with patch.object(storage.os, "replace", side_effect=error), \
                patch.object(storage.os, "remove", side_effect=PermissionError("synthetic cleanup")):
            with self.assertRaises(PermissionError) as raised:
                storage.atomic_write_json(self.target, {})
        self.assertIs(raised.exception, error)
        self.assertEqual(self.target.read_bytes(), self.previous)
        self.assertEqual(self.sibling.read_bytes(), b"unrelated")
        self.assertEqual(len(set(self.root.iterdir()) - {self.target, self.sibling}), 1)

    def test_missing_parent_is_not_created_or_replaced_with_a_fallback(self):
        target = self.root / "absent" / "value.json"
        with self.assertRaises(FileNotFoundError):
            storage.atomic_write_json(target, {})
        self.assertFalse(target.parent.exists())
        self.assert_preserved()

    def test_legacy_relative_destination_uses_its_existing_directory(self):
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            storage.atomic_write_json("relative.json", {"relative": True})
            self.assertEqual(json.loads(Path("relative.json").read_bytes()), {"relative": True})
        finally:
            os.chdir(previous)


class StorageFixtureTests(unittest.TestCase):
    def test_fixture_refuses_bundle_home_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "Synthetic.app"
            contents = bundle / "Contents"
            contents.mkdir(parents=True)
            sentinel = contents / "resource"
            sentinel.write_bytes(b"immutable")
            with self.assertRaises(ValueError):
                probe_storage(contents, "test.synthetic-storage")
            self.assertEqual(list(contents.iterdir()), [sentinel])
            self.assertEqual(sentinel.read_bytes(), b"immutable")

    def test_explicit_temporary_home_real_writes_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "\u4e2d # %"
            probe_storage(home, "test.synthetic-storage")
            paths = storage.macos_user_paths(home, "test.synthetic-storage")
            self.assertNotEqual(paths.application_support, paths.caches)
            for root in (paths.application_support, paths.caches):
                files = list(root.iterdir())
                self.assertEqual(len(files), 1)
                self.assertEqual(json.loads(files[0].read_bytes()), {"synthetic": "replacement", "items": []})

    def test_fixture_cannot_overwrite_an_existing_application_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            paths = storage.macos_user_paths(home, "test.synthetic-storage")
            paths.application_support.mkdir(parents=True)
            sentinel = paths.application_support / "synthetic # %.json"
            sentinel.write_bytes(b"existing data")
            with self.assertRaises(FileExistsError):
                probe_storage(home, "test.synthetic-storage")
            self.assertEqual(sentinel.read_bytes(), b"existing data")
            self.assertFalse(paths.caches.exists())
