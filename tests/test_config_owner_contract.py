"""Portable config-owner wiring; the real flock lifecycle runs in the bundled suites."""

import builtins
import errno
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cc_config_store
from cc_macos import config_owner as native, config_store_fixture, file_owner, history_owner
from cc_storage import macos_user_paths
from tests.test_history_owner_contract import darwin_owner


class ConfigOwnerContractTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix=".cc-config-owner-", dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve()
        self.application_id = "synthetic.config-owner"
        self.directory = macos_user_paths(self.home, self.application_id).application_support
        self.directory.mkdir(parents=True)
        self.path = self.directory / "config.json"

    def create(self):
        return native.MacConfigOwner(self.home, self.application_id)

    def test_history_and_config_use_the_same_ownership_implementation(self):
        self.assertTrue(issubclass(native.MacConfigOwner, cc_config_store.ConfigRepository))
        self.assertIs(native.MacConfigOwner.close, history_owner.MacHistoryOwner.close)
        self.assertIs(native.MacConfigOwner.close, file_owner.MacFileOwner.close)
        self.assertIs(native.MacConfigOwner._ensure_open, history_owner.MacHistoryOwner._ensure_open)

    def test_imports_do_not_access_home_or_acquire_locks(self):
        sources = [compile(Path(module.__file__).read_text(encoding="utf-8"), module.__file__, "exec")
                   for module in (file_owner, native, config_store_fixture)]
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {"fcntl", "cc_core", "tkinter", "cc_providers"}:
                raise AssertionError("forbidden import")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded), \
                patch("builtins.open", side_effect=AssertionError("import opened file")), \
                patch.object(Path, "home", side_effect=AssertionError("implicit home")), \
                patch.object(Path, "resolve", side_effect=AssertionError("import resolved path")), \
                patch.object(os, "getenv", side_effect=AssertionError("environment")), \
                patch.object(os, "open", side_effect=AssertionError("import acquired descriptor")):
            for source in sources:
                exec(source, {"__name__": "synthetic_config_owner_import"})

    def test_non_darwin_fails_before_path_resolution(self):
        with patch.object(native, "sys", SimpleNamespace(platform="win32")), \
                patch.object(native, "macos_user_paths") as paths:
            with self.assertRaisesRegex(RuntimeError, "requires_darwin"):
                self.create()
            paths.assert_not_called()

    def test_home_and_application_identity_are_explicit(self):
        for args in ((), (self.home,)):
            with self.subTest(args=args), self.assertRaises(TypeError):
                native.MacConfigOwner(*args)
        with darwin_owner(native) as (calls, _):
            for home, identity in ((Path("relative"), self.application_id), (self.home, ""),
                                   (self.home, "../escape"), (self.home / "..", self.application_id)):
                with self.subTest(home=home, identity=identity), self.assertRaises(ValueError):
                    native.MacConfigOwner(home, identity)
            calls.open.assert_not_called()

    def test_missing_application_directory_does_not_create_or_fallback(self):
        with darwin_owner(native) as (calls, _):
            with self.assertRaises(FileNotFoundError):
                native.MacConfigOwner(self.home, "synthetic.absent")
            calls.open.assert_not_called()
        self.assertFalse(self.directory.with_name("synthetic.absent").exists())

    def test_lock_path_flags_and_private_mode_are_unchanged(self):
        with darwin_owner(native) as (calls, flock):
            with self.create() as owner:
                self.assertEqual(owner.path, self.path)
                self.assertEqual(owner.lock_path, self.path.with_name("config.json.lock"))
                calls.open.assert_called_once_with(owner.lock_path,
                    calls.O_CREAT | calls.O_RDWR | calls.O_CLOEXEC | calls.O_NOFOLLOW, 0o600)
                flock.flock.assert_called_once_with(701, flock.LOCK_EX | flock.LOCK_NB)
            calls.close.assert_called_once_with(701)

    def test_contention_has_config_error_and_closes_attempt_fd(self):
        for code in (errno.EAGAIN, errno.EACCES):
            with self.subTest(errno=code), darwin_owner(native) as (calls, flock):
                error = OSError(code, "synthetic busy")
                flock.flock.side_effect = error
                with self.assertRaisesRegex(native.ConfigInUseError, "config_in_use") as raised:
                    self.create()
                self.assertIs(raised.exception.__cause__, error)
                calls.close.assert_called_once_with(701)

    def test_fstat_and_nonregular_failures_release_only_owned_fd(self):
        with darwin_owner(native) as (calls, _):
            calls.fstat.side_effect = OSError("synthetic stat")
            with self.assertRaisesRegex(OSError, "synthetic stat"):
                self.create()
            calls.close.assert_called_once_with(701)
        with darwin_owner(native) as (calls, _):
            calls.fstat.return_value.st_mode = stat.S_IFIFO
            with self.assertRaisesRegex(ValueError, "config_lock_regular_file"):
                self.create()
            calls.close.assert_called_once_with(701)

    def test_open_failure_does_not_close_an_unowned_fd(self):
        with darwin_owner(native) as (calls, _):
            calls.open.side_effect = OSError("synthetic open")
            with self.assertRaisesRegex(OSError, "synthetic open"):
                self.create()
            calls.close.assert_not_called()

    def test_fork_load_save_and_enter_reject_before_inherited_lock(self):
        with darwin_owner(native) as (calls, flock):
            owner = self.create()
            calls.getpid.return_value += 1
            owner._lock = Mock()
            owner._lock.__enter__ = Mock(side_effect=AssertionError("inherited lock"))
            for operation in (owner.load, lambda: owner.save(None), owner.__enter__, owner.close):
                with self.assertRaises(native.ConfigForkError):
                    operation()
            calls.close.assert_called_once_with(701)
            flock.flock.assert_called_once_with(701, flock.LOCK_EX | flock.LOCK_NB)

    def test_close_failure_is_visible_and_never_retries_ambiguous_fd(self):
        with darwin_owner(native) as (calls, _):
            owner = self.create()
            calls.close.side_effect = OSError("synthetic close")
            with self.assertRaisesRegex(OSError, "synthetic close"):
                owner.close()
            owner.close()
            calls.close.assert_called_once_with(701)
            self.assertIsNone(owner._fd)
            for operation in (owner.load, lambda: owner.save(None), owner.__enter__):
                with self.assertRaisesRegex(RuntimeError, "closed"):
                    operation()

    def test_owner_load_save_use_real_repository_and_shared_writer(self):
        with darwin_owner(native):
            with self.create() as owner:
                self.assertFalse(self.path.exists())
                owner.load()
                self.assertFalse(self.path.exists())
                owner.save({"font_size": "16", "future": ["keep"]})
                self.assertEqual(owner.load().font_size, 16)
            with self.create() as successor:
                self.assertEqual(successor.load()["future"], ["keep"])

    def test_new_json_symlink_is_rejected_before_operation(self):
        with darwin_owner(native):
            with self.create() as owner, patch.object(Path, "is_symlink", return_value=True):
                for operation in (owner.load, lambda: owner.save({})):
                    with self.assertRaisesRegex(ValueError, "config_symlink"):
                        operation()

    def test_fixture_non_darwin_does_not_create_directories(self):
        with patch.object(config_store_fixture, "sys", SimpleNamespace(platform="win32")), \
                patch.object(Path, "mkdir") as mkdir:
            with self.assertRaisesRegex(RuntimeError, "requires_darwin"):
                config_store_fixture.probe_config_store(self.home, "synthetic.fixture")
            mkdir.assert_not_called()

    def test_fixture_refuses_existing_directory(self):
        sentinel = self.directory / "keep"
        sentinel.write_bytes(b"synthetic")
        with patch.object(config_store_fixture, "sys", SimpleNamespace(platform="darwin")), \
                patch.object(config_store_fixture, "MacConfigOwner") as owner:
            with self.assertRaises(FileExistsError):
                config_store_fixture.probe_config_store(self.home, self.application_id)
            owner.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"synthetic")
