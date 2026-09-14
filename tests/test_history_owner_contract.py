"""Portable contracts for macOS ownership; flock itself is tested in the app."""

import builtins
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cc_history
from cc_macos import history as business, history_fixture, history_owner as native


@contextmanager
def darwin_owner(module):
    fake_os = SimpleNamespace(
        O_CREAT=os.O_CREAT, O_RDWR=os.O_RDWR, O_CLOEXEC=0x100000,
        O_NOFOLLOW=0x200000, getpid=Mock(return_value=os.getpid()),
        open=Mock(return_value=701), close=Mock(),
        fstat=Mock(return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o600)),
    )
    flock = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8, flock=Mock())
    with patch.object(module, "sys", SimpleNamespace(platform="darwin")), \
            patch.object(module, "os", fake_os), \
            patch.dict(sys.modules, {"fcntl": flock}):
        yield fake_os, flock


class TestHistoryOwnerContract(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix=".cc-history-contract-", dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.path = self.root / "history \u4e2d # %.json"

    def darwin(self):
        return darwin_owner(native)

    def test_import_does_not_load_platform_ui_providers_or_touch_home(self):
        sources = [compile(Path(module.__file__).read_text(encoding="utf-8"),
                           module.__file__, "exec") for module in (native, history_fixture)]
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in {"fcntl", "cc_core", "tkinter", "cc_providers"}:
                raise AssertionError("forbidden import: " + name)
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded_import), \
                patch("builtins.open", side_effect=AssertionError("import opened a file")), \
                patch.object(Path, "home", side_effect=AssertionError("implicit home")), \
                patch.object(Path, "resolve", side_effect=AssertionError("import resolved a path")), \
                patch.object(os.path, "expanduser", side_effect=AssertionError("implicit home")), \
                patch.object(os, "getenv", side_effect=AssertionError("import read environment")), \
                patch.object(os, "open", side_effect=AssertionError("import opened a descriptor")):
            for code in sources:
                exec(code, {"__name__": "synthetic_history_import"})

    def test_non_darwin_fails_before_path_or_fcntl_access(self):
        for platform in ("win32", "linux"):
            with self.subTest(platform=platform), \
                    patch.object(native, "sys", SimpleNamespace(platform=platform)), \
                    patch.object(native, "Path") as path, \
                    patch("builtins.__import__", side_effect=AssertionError("unexpected import")):
                with self.assertRaisesRegex(RuntimeError, "requires_darwin"):
                    native.MacHistoryOwner("unused")
                path.assert_not_called()

    def test_only_explicit_path_is_accepted(self):
        with self.assertRaises(TypeError):
            native.MacHistoryOwner()
        for option in ("reader", "writer", "lock"):
            with self.subTest(option=option), self.assertRaises(TypeError):
                native.MacHistoryOwner(self.path, **{option: Mock()})
        self.assertTrue(issubclass(native.MacHistoryOwner, cc_history.HistoryRepository))

    def test_business_adapter_uses_same_owner_and_fixed_bounded_repository(self):
        with self.darwin() as (calls, flock):
            with business._BusinessHistoryOwner(self.path) as owner:
                self.assertIsInstance(owner, native.MacHistoryOwner)
                self.assertEqual(owner.load(), [])
                self.assertFalse(self.path.exists())
                owner.add("synthetic", "out", False, 2, sig="unchanged|signature")
                self.assertEqual(owner.load()[0]["sig"], "unchanged|signature")
                before = self.path.read_bytes()
                with patch.object(business, "MAX_HISTORY_FILE_BYTES", len(before) - 1):
                    with self.assertRaisesRegex(business.HistoryError, "^history_too_large$"):
                        owner.load()
                self.assertEqual(self.path.read_bytes(), before)
            calls.close.assert_called_once_with(701)
            self.assertEqual([call.args for call in flock.flock.call_args_list], [(701, 6), (701, 8)])
        for option in ("reader", "writer", "lock"):
            with self.subTest(option=option), self.assertRaises(TypeError):
                business._BusinessHistoryOwner(self.path, **{option: Mock()})

    def test_parent_resolution_loop_is_fixed_before_open(self):
        with self.darwin() as (calls, _), \
                patch.object(Path, "resolve", side_effect=RuntimeError("synthetic private path loop")):
            with self.assertRaisesRegex(ValueError, "^history_invalid_parent$"):
                native.MacHistoryOwner(self.path)
            calls.open.assert_not_called()
            calls.close.assert_not_called()

    def test_relative_and_parent_traversal_are_rejected_before_open(self):
        with self.darwin() as (calls, _):
            for path in (Path("history.json"), self.root / ".." / "history.json"):
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, "absolute_path"):
                    native.MacHistoryOwner(path)
            calls.open.assert_not_called()

    def test_lexical_app_path_is_rejected_before_resolve(self):
        with self.darwin() as (calls, _), patch.object(Path, "resolve") as resolve:
            with self.assertRaisesRegex(ValueError, "inside_app"):
                native.MacHistoryOwner(self.root / "Synthetic.APP" / "history.json")
            resolve.assert_not_called()
            calls.open.assert_not_called()

    def test_resolved_app_alias_is_rejected(self):
        with self.darwin() as (calls, _), \
                patch.object(Path, "resolve", return_value=self.root / "Synthetic.app" / "data"):
            with self.assertRaisesRegex(ValueError, "inside_app"):
                native.MacHistoryOwner(self.path)
            calls.open.assert_not_called()

    def test_parent_is_canonicalized_without_creating_directories(self):
        real = self.root / "canonical"
        real.mkdir()
        with self.darwin(), patch.object(Path, "resolve", return_value=real) as resolve:
            with native.MacHistoryOwner(self.path) as owner:
                self.assertEqual(owner.path, real / self.path.name)
                self.assertEqual(owner.lock_path, real / (self.path.name + ".lock"))
            resolve.assert_called_once_with(strict=True)

    def test_missing_parent_does_not_create_or_fallback(self):
        with self.darwin() as (calls, _):
            with self.assertRaises(FileNotFoundError):
                native.MacHistoryOwner(self.root / "absent" / "history.json")
            calls.open.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_non_directory_parent_is_rejected(self):
        self.path.write_text("synthetic", encoding="utf-8")
        with self.darwin() as (calls, _):
            with self.assertRaises(NotADirectoryError):
                native.MacHistoryOwner(self.path / "history.json")
            calls.open.assert_not_called()

    def test_json_symlink_is_rejected_before_open(self):
        with self.darwin() as (calls, _), patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(ValueError, "symlink"):
                native.MacHistoryOwner(self.path)
            calls.open.assert_not_called()

    def test_json_symlink_appearing_during_acquisition_releases_descriptor(self):
        with self.darwin() as (calls, flock), \
                patch.object(Path, "is_symlink", side_effect=(False, True)):
            with self.assertRaisesRegex(ValueError, "symlink"):
                native.MacHistoryOwner(self.path)
            calls.close.assert_called_once_with(701)
            flock.flock.assert_called_once_with(701, flock.LOCK_EX | flock.LOCK_NB)

    def test_lock_open_flags_permissions_and_lazy_fcntl(self):
        with self.darwin() as (calls, flock):
            owner = native.MacHistoryOwner(self.path)
            calls.open.assert_called_once_with(
                self.path.with_name(self.path.name + ".lock"),
                calls.O_CREAT | calls.O_RDWR | calls.O_CLOEXEC | calls.O_NOFOLLOW, 0o600)
            calls.fstat.assert_called_once_with(701)
            flock.flock.assert_called_once_with(701, flock.LOCK_EX | flock.LOCK_NB)
            self.assertIsNone(owner._reader)
            self.assertIsNone(owner._writer)
            owner.close()

    def test_open_failure_does_not_close_an_unowned_descriptor(self):
        with self.darwin() as (calls, flock):
            calls.open.side_effect = OSError(errno.ELOOP, "synthetic lock symlink")
            with self.assertRaises(OSError):
                native.MacHistoryOwner(self.path)
            calls.close.assert_not_called()
            flock.flock.assert_not_called()

    def test_fstat_failure_closes_descriptor(self):
        for error in (OSError("synthetic stat error"), KeyboardInterrupt("synthetic interrupt")):
            with self.subTest(error=type(error).__name__), self.darwin() as (calls, flock):
                calls.fstat.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    native.MacHistoryOwner(self.path)
                self.assertIs(raised.exception, error)
                calls.close.assert_called_once_with(701)
                flock.flock.assert_not_called()

    def test_non_regular_side_file_closes_descriptor(self):
        with self.darwin() as (calls, flock):
            calls.fstat.return_value.st_mode = stat.S_IFIFO | 0o600
            with self.assertRaisesRegex(ValueError, "regular_file"):
                native.MacHistoryOwner(self.path)
            calls.close.assert_called_once_with(701)
            flock.flock.assert_not_called()

    def test_contention_is_explicit_and_closes_only_attempt_descriptor(self):
        for code in (errno.EACCES, errno.EAGAIN):
            with self.subTest(errno=code), self.darwin() as (calls, flock):
                original = OSError(code, "synthetic busy")
                flock.flock.side_effect = original
                with self.assertRaises(native.HistoryInUseError) as raised:
                    native.MacHistoryOwner(self.path)
                self.assertIs(raised.exception.__cause__, original)
                calls.close.assert_called_once_with(701)
                self.assertEqual(flock.flock.call_count, 1)

    def test_other_flock_errors_are_not_reported_as_contention(self):
        with self.darwin() as (calls, flock):
            original = OSError(errno.EIO, "synthetic flock error")
            flock.flock.side_effect = original
            with self.assertRaises(OSError) as raised:
                native.MacHistoryOwner(self.path)
            self.assertIs(raised.exception, original)
            calls.close.assert_called_once_with(701)

    def test_constructor_cleanup_error_remains_observable_without_retry(self):
        with self.darwin() as (calls, flock):
            flock.flock.side_effect = OSError(errno.EAGAIN, "synthetic busy")
            calls.close.side_effect = OSError("synthetic close error")
            with self.assertRaisesRegex(OSError, "close error") as raised:
                native.MacHistoryOwner(self.path)
            self.assertIsInstance(raised.exception.__context__, native.HistoryInUseError)
            calls.close.assert_called_once_with(701)

    def test_close_unlocks_then_closes_once_and_never_unlinks(self):
        with self.darwin() as (calls, flock), patch.object(cc_history.os, "remove") as remove:
            owner = native.MacHistoryOwner(self.path)
            order = []
            flock.flock.side_effect = lambda *args: order.append(("unlock", args))
            calls.close.side_effect = lambda fd: order.append(("close", fd))
            owner.close()
            owner.close()
            self.assertEqual(order, [("unlock", (701, flock.LOCK_UN)), ("close", 701)])
            remove.assert_not_called()

    def test_unlock_failure_still_closes_and_is_not_retried(self):
        with self.darwin() as (calls, flock):
            owner = native.MacHistoryOwner(self.path)
            flock.flock.side_effect = OSError("synthetic unlock error")
            with self.assertRaisesRegex(OSError, "unlock error"):
                owner.close()
            owner.close()
            calls.close.assert_called_once_with(701)
            self.assertIsNone(owner._fd)
            self.assertEqual(flock.flock.call_count, 2)
            with self.assertRaisesRegex(RuntimeError, "closed"):
                owner.load()

    def test_close_failure_is_observable_and_ambiguous_fd_is_not_retried(self):
        with self.darwin() as (calls, _):
            owner = native.MacHistoryOwner(self.path)
            calls.close.side_effect = OSError("synthetic close error")
            with self.assertRaisesRegex(OSError, "close error"):
                owner.close()
            owner.close()
            calls.close.assert_called_once_with(701)
            self.assertIsNone(owner._fd)
            with self.assertRaisesRegex(RuntimeError, "closed"):
                owner.clear()

    def test_all_operations_including_invalid_cache_query_reject_closed_owner(self):
        with self.darwin():
            owner = native.MacHistoryOwner(self.path)
            owner.close()
            operations = (owner.load, owner.clear, owner.__enter__,
                          lambda: owner.add("x", "y", False, 1),
                          lambda: owner.find_cached("", "invalid", ""),
                          lambda: owner.find_cached(None, None, None))
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaisesRegex(RuntimeError, "closed"):
                    operation()

    def test_context_exit_releases_owner_even_on_exception(self):
        with self.darwin() as (calls, flock):
            with self.assertRaisesRegex(ValueError, "synthetic body"):
                with native.MacHistoryOwner(self.path):
                    raise ValueError("synthetic body")
            calls.close.assert_called_once_with(701)
            self.assertEqual(flock.flock.call_args.args, (701, flock.LOCK_UN))

    def test_forked_operations_reject_before_acquiring_inherited_rlock(self):
        with self.darwin() as (calls, flock):
            owner = native.MacHistoryOwner(self.path)
            calls.getpid.return_value += 1
            owner._lock = Mock()
            for operation in (owner.load, owner.clear, owner.__enter__,
                              lambda: owner.add("x", "y", False, 1),
                              lambda: owner.find_cached("", "invalid", "")):
                with self.assertRaises(native.HistoryForkError):
                    operation()
            owner._lock.assert_not_called()
            owner._lock.__enter__ = Mock(side_effect=AssertionError("inherited lock entered"))
            with self.assertRaises(native.HistoryForkError):
                owner.close()
            calls.close.assert_called_once_with(701)
            flock.flock.assert_called_once_with(701, flock.LOCK_EX | flock.LOCK_NB)

    def test_fork_close_drops_local_fd_without_unlock_or_retry(self):
        with self.darwin() as (calls, flock):
            owner = native.MacHistoryOwner(self.path)
            calls.getpid.return_value += 1
            calls.close.side_effect = OSError("synthetic child close error")
            with self.assertRaisesRegex(OSError, "child close error"):
                owner.close()
            with self.assertRaises(native.HistoryForkError):
                owner.close()
            calls.close.assert_called_once_with(701)
            flock.flock.assert_called_once_with(701, flock.LOCK_EX | flock.LOCK_NB)

    def test_owner_uses_shared_strict_reader_and_atomic_writer(self):
        with self.darwin():
            with native.MacHistoryOwner(self.path) as owner, \
                    patch.object(cc_history, "read_history", return_value=[]) as reader, \
                    patch.object(cc_history, "atomic_write_json") as writer:
                owner.add("synthetic", "result", False, 2, sig="v1")
                reader.assert_called_once_with(self.path)
                self.assertEqual(writer.call_args.args[0], self.path)
                self.assertEqual(writer.call_args.args[1][0]["input"], "synthetic")

    def test_strict_corrupt_history_is_preserved(self):
        for data in (b"{", b"{}", b"[1]", b'[{"input": 1}]', b'[{"is_dict": 1}]'):
            with self.subTest(data=data), self.darwin():
                self.path.write_bytes(data)
                with native.MacHistoryOwner(self.path) as owner:
                    for operation in (owner.load, lambda: owner.add("x", "y", False, 1),
                                      lambda: owner.find_cached("x", "text", "")):
                        with self.assertRaises(ValueError):
                            operation()
                    self.assertEqual(self.path.read_bytes(), data)

    def test_read_failure_is_not_treated_as_empty(self):
        self.path.write_bytes(b"[]")
        with self.darwin():
            with native.MacHistoryOwner(self.path) as owner, \
                    patch.object(cc_history, "read_history", side_effect=PermissionError("synthetic")), \
                    patch.object(cc_history, "atomic_write_json") as writer:
                with self.assertRaises(PermissionError):
                    owner.add("x", "y", False, 1)
                writer.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"[]")

    def test_shared_atomic_replace_failure_preserves_data_and_cleans_temporary(self):
        self.path.write_bytes(b"[]")
        with self.darwin():
            with native.MacHistoryOwner(self.path) as owner, \
                    patch("cc_storage.os.replace", side_effect=OSError("synthetic replace error")):
                with self.assertRaisesRegex(OSError, "replace error"):
                    owner.add("x", "y", False, 1)
        self.assertEqual(self.path.read_bytes(), b"[]")
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_symlink_replacement_is_rejected_by_existing_owner(self):
        with self.darwin():
            with native.MacHistoryOwner(self.path) as owner, \
                    patch.object(Path, "is_symlink", return_value=True):
                for operation in (owner.load, owner.clear,
                                  lambda: owner.add("x", "y", False, 1)):
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        operation()

    def test_close_waits_for_inflight_write_then_rejects_operations(self):
        entered, release, waiting, closed = (threading.Event() for _ in range(4))
        failures = []
        original = cc_history.atomic_write_json
        class ObservedLock:
            def __init__(self, inner):
                self.inner = inner

            def __enter__(self):
                if threading.current_thread() is closer:
                    waiting.set()
                self.inner.acquire()
                return self

            def __exit__(self, *args):
                self.inner.release()

        def slow_write(path, entries):
            entered.set()
            if not release.wait(5):
                raise AssertionError("write release was not signalled")
            original(path, entries)
        def run(operation):
            try:
                operation()
            except BaseException as error:
                failures.append(error)
        with self.darwin() as (calls, _):
            owner = native.MacHistoryOwner(self.path)
            def close():
                owner.close()
                closed.set()
            writer = threading.Thread(target=run, args=(lambda: owner.add("x", "y", False, 1),))
            closer = threading.Thread(target=run, args=(close,))
            owner._lock = ObservedLock(owner._lock)
            with patch.object(cc_history, "atomic_write_json", side_effect=slow_write):
                try:
                    writer.start()
                    self.assertTrue(entered.wait(3))
                    closer.start()
                    self.assertTrue(waiting.wait(3))
                    self.assertFalse(closed.is_set())
                    calls.close.assert_not_called()
                finally:
                    release.set()
                    writer.join(5)
                    if closer.ident is not None:
                        closer.join(5)
                    owner.close()
            self.assertFalse(writer.is_alive())
            self.assertFalse(closer.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(closed.is_set())
            self.assertEqual(cc_history.read_history(self.path)[0]["output"], "y")
            with self.assertRaisesRegex(RuntimeError, "closed"):
                owner.find_cached("", "invalid", "")

    def test_fixture_non_darwin_does_not_create_directories(self):
        with patch.object(history_fixture, "sys", SimpleNamespace(platform="win32")):
            with self.assertRaisesRegex(RuntimeError, "requires_darwin"):
                history_fixture.probe_history(self.root, "synthetic.history")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_fixture_refuses_existing_application_directory(self):
        from cc_storage import macos_user_paths
        directory = macos_user_paths(self.root, "synthetic.history").application_support
        directory.mkdir(parents=True)
        sentinel = directory / "keep"
        sentinel.write_bytes(b"synthetic existing")
        with patch.object(history_fixture, "sys", SimpleNamespace(platform="darwin")), \
                patch.object(history_fixture, "MacHistoryOwner") as owner:
            with self.assertRaises(FileExistsError):
                history_fixture.probe_history(self.root, "synthetic.history")
            owner.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"synthetic existing")


if __name__ == "__main__":
    unittest.main()
