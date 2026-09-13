"""Mandatory history-owner regressions using only the app's actual macOS core."""

import errno
import os
from pathlib import Path
import plistlib
import selectors
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


if sys.platform != "darwin":
    raise RuntimeError("This suite requires bundled macOS Python; do not substitute a host run.")

import cc_history
import cc_storage
import cc_macos
from cc_macos import history_fixture, history_owner
from cc_macos.history_owner import HistoryInUseError, MacHistoryOwner


OWNER_SCRIPT = r"""
import os
from pathlib import Path
import signal
import sys
sys.path.insert(0, sys.argv[1])
from cc_macos.history_owner import HistoryForkError, MacHistoryOwner
owner = MacHistoryOwner(Path(sys.argv[2]))
print("acquired", flush=True)
for command in sys.stdin:
    command = command.strip()
    if command == "write":
        owner.add("synthetic input", "synthetic output", False, 5, sig="synthetic-v1")
        print("written", flush=True)
    elif command == "clear":
        owner.clear()
        print("cleared", flush=True)
    elif command == "close":
        owner.close()
        print("closed", flush=True)
    elif command == "fork":
        child = os.fork()
        if child == 0:
            signal.alarm(5)
            try:
                operations = (owner.load, owner.clear,
                              lambda: owner.add("forbidden", "forbidden", False, 1),
                              lambda: owner.find_cached("", "invalid", ""))
                for operation in operations:
                    try:
                        operation()
                    except HistoryForkError:
                        pass
                    else:
                        os._exit(71)
                try:
                    owner.close()
                except HistoryForkError:
                    pass
                else:
                    os._exit(72)
                if owner._fd is not None:
                    os._exit(73)
            except BaseException:
                os._exit(74)
            os._exit(0)
        waited, status = os.waitpid(child, 0)
        if waited != child or os.waitstatus_to_exitcode(status) != 0:
            raise RuntimeError("synthetic fork child failed or was not reaped")
        print("fork-reaped", flush=True)
    elif command == "crash":
        os._exit(23)
    elif command == "exit":
        owner.close()
        print("exiting", flush=True)
        break
    else:
        raise RuntimeError("unexpected synthetic command")
else:
    raise RuntimeError("unexpected synthetic stdin EOF")
"""

SIBLING_SCRIPT = """
import sys
print("sibling-ready", flush=True)
if sys.stdin.readline().strip() != "exit":
    raise RuntimeError("unexpected sibling EOF")
print("sibling-exiting", flush=True)
"""


class TestHistoryOwnerProcess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = Path(sys.executable).resolve()
        contents = next((parent for parent in executable.parents
                         if parent.name == "Contents" and parent.parent.suffix == ".app"), None)
        if contents is None or not executable.is_relative_to(contents / "Helpers" / "python"):
            raise RuntimeError("The history process suite must use the app's bundled Python.")
        cls.contents = contents
        cls.core = contents / "Resources" / "Core"
        for module in (cc_history, cc_storage, cc_macos, history_owner, history_fixture):
            expected = cls.core.joinpath(*module.__name__.split("."))
            expected = expected / "__init__.py" if hasattr(module, "__path__") else expected.with_suffix(".py")
            if Path(module.__file__).resolve() != expected.resolve():
                raise RuntimeError("History process tests imported code outside the app's Core.")
        if Path(__file__).resolve().is_relative_to(contents):
            raise RuntimeError("History process tests must come from the checkout, not the app.")
        if not sys.flags.isolated or not sys.dont_write_bytecode:
            raise RuntimeError("Run the bundled Python with -I -B.")

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix=".cc-history-process-", dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.home = self.root / "synthetic home \u4e2d # %"
        self.home.mkdir()
        self.path = self.home / "history \u4e2d # %.json"
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def spawn(self, script=OWNER_SCRIPT):
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", script, str(self.core), str(self.path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.home, start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.home), "TMPDIR": str(self.home)},
        )
        self.addCleanup(self.cleanup_process, process)
        return process

    def cleanup_process(self, process):
        # Only this Popen's own child may be signalled, and only on failure.
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def line(self, process):
        deadline = time.monotonic() + 8
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(max(0, deadline - time.monotonic())):
                    break
                part = os.read(process.stdout.fileno(), 1)
                if not part:
                    self.fail("Synthetic history child closed stdout before its handshake.")
                if part == b"\n":
                    return output.decode("utf-8")
                output.extend(part)
                self.assertLess(len(output), 256, "Unexpected synthetic protocol output.")
        self.fail("Synthetic history child did not complete its handshake.")

    def send(self, process, command, expected=None):
        process.stdin.write((command + "\n").encode("ascii"))
        process.stdin.flush()
        if expected is not None:
            self.assertEqual(self.line(process), expected)

    def finish(self, process, code=0):
        process.stdin.close()
        process.stdin = None
        output, errors = process.communicate(timeout=8)
        # Assert real exit/reap before accepting any success or takeover result.
        self.assertEqual(process.wait(timeout=0), code)
        self.assertEqual(process.returncode, code)
        self.assertEqual((output, errors), (b"", b""))

    def assert_fd_closed(self, fd):
        with self.assertRaises(OSError) as raised:
            os.fstat(fd)
        self.assertEqual(raised.exception.errno, errno.EBADF)

    def test_real_bundle_id_history_fixture(self):
        with (self.contents / "Info.plist").open("rb") as stream:
            application_id = plistlib.load(stream)["CFBundleIdentifier"]
        self.assertIsInstance(application_id, str)
        report = history_fixture.probe_history(self.home, application_id)
        self.assertEqual(report, {
            "status": "passed", "fixture": True, "history_verified": True,
            "cache_verified": True, "clear_verified": True, "reopen_verified": True,
        })
        directory = cc_storage.macos_user_paths(self.home, application_id).application_support
        self.assertEqual([path.suffix for path in directory.iterdir()], [".lock"])
        with self.assertRaises(FileExistsError):
            history_fixture.probe_history(self.home, application_id)
        print("PASS: bundled history owner fixture", flush=True)

    def test_second_owner_rejected_across_atomic_replacement_clear_and_close(self):
        process = self.spawn()
        self.assertEqual(self.line(process), "acquired")
        lock_stat = self.lock_path.stat()
        with self.assertRaises(HistoryInUseError):
            MacHistoryOwner(self.path)
        self.send(process, "write", "written")
        before = self.path.stat()
        with self.assertRaises(HistoryInUseError):
            MacHistoryOwner(self.path)
        self.send(process, "write", "written")
        self.assertNotEqual(before.st_ino, self.path.stat().st_ino)
        self.assertEqual(len(cc_history.read_history(self.path)), 2)
        with self.assertRaises(HistoryInUseError):
            MacHistoryOwner(self.path)
        self.send(process, "clear", "cleared")
        self.assertFalse(self.path.exists())
        self.assertEqual((self.lock_path.stat().st_dev, self.lock_path.stat().st_ino),
                         (lock_stat.st_dev, lock_stat.st_ino))
        with self.assertRaises(HistoryInUseError):
            MacHistoryOwner(self.path)
        self.send(process, "close", "closed")
        with MacHistoryOwner(self.path) as successor:
            self.assertEqual(successor.load(), [])
            successor.add("after close", "result", False, 1)
        self.send(process, "exit", "exiting")
        self.finish(process)
        self.assertEqual(cc_history.read_history(self.path)[0]["input"], "after close")
        self.assertEqual(self.lock_path.stat().st_ino, lock_stat.st_ino)
        self.assertEqual(stat.S_IMODE(lock_stat.st_mode), 0o600)

    def test_normal_exit_is_reaped_before_takeover(self):
        process = self.spawn()
        self.assertEqual(self.line(process), "acquired")
        self.send(process, "write", "written")
        self.send(process, "exit", "exiting")
        self.finish(process)
        with MacHistoryOwner(self.path) as successor:
            self.assertEqual(successor.find_cached("synthetic input", "text", "synthetic-v1"),
                             "synthetic output")

    def test_os_exit_crash_is_reaped_before_takeover_and_sibling_survives(self):
        sibling = self.spawn(SIBLING_SCRIPT)
        self.assertEqual(self.line(sibling), "sibling-ready")
        process = self.spawn()
        self.assertEqual(self.line(process), "acquired")
        self.send(process, "write", "written")
        lock_inode = self.lock_path.stat().st_ino
        self.send(process, "crash")
        self.finish(process, 23)
        with MacHistoryOwner(self.path) as successor:
            self.assertEqual(successor.load()[0]["input"], "synthetic input")
            self.assertEqual(successor.lock_path.stat().st_ino, lock_inode)
        self.assertIsNone(sibling.poll(), "A separate sibling must survive history-owner exit.")
        self.send(sibling, "exit", "sibling-exiting")
        self.finish(sibling)

    def test_fork_inheritance_is_rejected_and_child_close_does_not_unlock_parent(self):
        process = self.spawn()
        self.assertEqual(self.line(process), "acquired")
        self.send(process, "fork", "fork-reaped")
        with self.assertRaises(HistoryInUseError):
            MacHistoryOwner(self.path)
        self.send(process, "write", "written")
        self.send(process, "exit", "exiting")
        self.finish(process)
        with MacHistoryOwner(self.path) as successor:
            self.assertEqual(len(successor.load()), 1)
            self.assertNotEqual(successor.load()[0]["input"], "forbidden")

    def test_directory_aliases_share_the_same_lock(self):
        alias = self.root / "home alias"
        alias.symlink_to(self.home, target_is_directory=True)
        with MacHistoryOwner(self.path) as owner:
            with self.assertRaises(HistoryInUseError):
                MacHistoryOwner(alias / self.path.name)
            self.assertEqual(owner.path, self.path)
        with MacHistoryOwner(alias / self.path.name) as successor:
            self.assertEqual(successor.path, self.path)
            self.assertEqual(successor.lock_path, self.lock_path)

    def test_missing_and_app_paths_do_not_create_or_open_files(self):
        bundle = self.root / "Synthetic.APP"
        bundle.mkdir()
        alias = self.root / "bundle alias"
        alias.symlink_to(bundle, target_is_directory=True)
        paths = (Path("relative.json"), self.home / ".." / "history.json",
                 bundle / "history.json", alias / "history.json")
        with patch.object(history_owner.os, "open") as opened:
            for path in paths:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    MacHistoryOwner(path)
            with self.assertRaises(FileNotFoundError):
                MacHistoryOwner(self.home / "missing" / "history.json")
            opened.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(list(bundle.iterdir()), [])

    def test_json_and_dangling_json_symlinks_are_rejected(self):
        target = self.home / "synthetic target"
        target.write_bytes(b"[]")
        for destination in (target, self.home / "missing target"):
            with self.subTest(destination=destination):
                self.path.symlink_to(destination)
                try:
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        MacHistoryOwner(self.path)
                    self.assertFalse(self.lock_path.exists())
                finally:
                    self.path.unlink()
        self.assertEqual(target.read_bytes(), b"[]")

    def test_side_file_symlink_is_not_followed_or_removed(self):
        target = self.home / "synthetic lock target"
        target.write_bytes(b"synthetic sentinel")
        self.lock_path.symlink_to(target)
        with self.assertRaises(OSError):
            MacHistoryOwner(self.path)
        self.assertTrue(self.lock_path.is_symlink())
        self.assertEqual(target.read_bytes(), b"synthetic sentinel")

    def test_non_regular_side_file_releases_its_descriptor(self):
        os.mkfifo(self.lock_path, 0o600)
        actual_open = os.open
        opened = []
        def record_open(*args):
            fd = actual_open(*args)
            opened.append(fd)
            return fd
        with patch.object(history_owner.os, "open", side_effect=record_open):
            with self.assertRaisesRegex(ValueError, "regular_file"):
                MacHistoryOwner(self.path)
        self.assertEqual(len(opened), 1)
        self.assert_fd_closed(opened[0])
        self.assertTrue(stat.S_ISFIFO(self.lock_path.stat().st_mode))

    def test_fstat_failure_releases_descriptor_without_unlink(self):
        actual_open = os.open
        opened = []
        def record_open(*args):
            fd = actual_open(*args)
            opened.append(fd)
            return fd
        with patch.object(history_owner.os, "open", side_effect=record_open), \
                patch.object(history_owner.os, "fstat", side_effect=OSError("synthetic fstat error")):
            with self.assertRaisesRegex(OSError, "fstat error"):
                MacHistoryOwner(self.path)
        self.assertEqual(len(opened), 1)
        self.assert_fd_closed(opened[0])
        self.assertTrue(self.lock_path.is_file())
        with MacHistoryOwner(self.path):
            pass

    def test_flock_failure_releases_descriptor_and_next_owner_can_acquire(self):
        import fcntl
        actual_open = os.open
        opened = []
        def record_open(*args):
            fd = actual_open(*args)
            opened.append(fd)
            return fd
        with patch.object(history_owner.os, "open", side_effect=record_open), \
                patch.object(fcntl, "flock", side_effect=OSError(errno.EIO, "synthetic flock error")):
            with self.assertRaises(OSError) as raised:
                MacHistoryOwner(self.path)
        self.assertEqual(raised.exception.errno, errno.EIO)
        self.assertEqual(len(opened), 1)
        self.assert_fd_closed(opened[0])
        with MacHistoryOwner(self.path):
            pass

    def test_rejected_competitor_closes_its_fd_without_unlocking_owner(self):
        process = self.spawn()
        self.assertEqual(self.line(process), "acquired")
        actual_open = os.open
        opened = []
        def record_open(*args):
            fd = actual_open(*args)
            opened.append(fd)
            return fd
        with patch.object(history_owner.os, "open", side_effect=record_open):
            with self.assertRaises(HistoryInUseError):
                MacHistoryOwner(self.path)
        self.assertEqual(len(opened), 1)
        self.assert_fd_closed(opened[0])
        with self.assertRaises(HistoryInUseError):
            MacHistoryOwner(self.path)
        self.send(process, "exit", "exiting")
        self.finish(process)

    def test_strict_bad_json_and_bad_schema_preserve_original_bytes(self):
        for data in (b"{", b"\xff", b"{}", b"[1]", b'[{"input": 7}]', b'[{"is_code": 1}]'):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                with MacHistoryOwner(self.path) as owner:
                    for operation in (owner.load, lambda: owner.add("x", "y", False, 1),
                                      lambda: owner.find_cached("x", "text", "")):
                        with self.assertRaises(ValueError):
                            operation()
                self.assertEqual(self.path.read_bytes(), data)
                self.assertEqual(set(self.home.iterdir()), {self.path, self.lock_path})

    def test_read_failure_does_not_replace_original_history(self):
        self.path.write_bytes(b"[]")
        with MacHistoryOwner(self.path) as owner, \
                patch("builtins.open", side_effect=PermissionError("synthetic read denied")), \
                patch.object(cc_history, "atomic_write_json") as writer:
            for operation in (owner.load, lambda: owner.add("x", "y", False, 1),
                              lambda: owner.find_cached("x", "text", "")):
                with self.assertRaises(PermissionError):
                    operation()
            writer.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"[]")
        self.assertEqual(set(self.home.iterdir()), {self.path, self.lock_path})

    def test_atomic_replace_failure_preserves_old_history_and_no_operation_temps(self):
        self.path.write_bytes(b"[]")
        with MacHistoryOwner(self.path) as owner:
            inode = self.lock_path.stat().st_ino
            with patch("cc_storage.os.replace", side_effect=OSError("synthetic replace failure")):
                with self.assertRaisesRegex(OSError, "replace failure"):
                    owner.add("x", "y", False, 1)
            self.assertEqual(owner.load(), [])
            self.assertEqual(self.path.read_bytes(), b"[]")
            self.assertEqual(self.lock_path.stat().st_ino, inode)
            self.assertEqual(set(self.home.iterdir()), {self.path, self.lock_path})
            with self.assertRaises(HistoryInUseError):
                MacHistoryOwner(self.path)

    def test_close_blocks_until_write_finishes_and_closed_queries_are_rejected(self):
        entered, release, waiting, closed = (threading.Event() for _ in range(4))
        failures = []
        actual_write = cc_history.atomic_write_json
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

        owner = MacHistoryOwner(self.path)
        self.addCleanup(owner.close)
        fd = owner._fd
        def write(path, entries):
            entered.set()
            if not release.wait(8):
                raise AssertionError("synthetic write was not released")
            actual_write(path, entries)
        def run(operation):
            try:
                operation()
            except BaseException as error:
                failures.append(error)
        def close():
            owner.close()
            closed.set()
        writer = threading.Thread(target=run, args=(lambda: owner.add("x", "y", False, 1),))
        closer = threading.Thread(target=run, args=(close,))
        owner._lock = ObservedLock(owner._lock)
        with patch.object(cc_history, "atomic_write_json", side_effect=write):
            try:
                writer.start()
                self.assertTrue(entered.wait(5))
                closer.start()
                self.assertTrue(waiting.wait(5))
                self.assertFalse(closed.is_set())
                self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
                with self.assertRaises(HistoryInUseError):
                    MacHistoryOwner(self.path)
            finally:
                release.set()
                writer.join(8)
                if closer.ident is not None:
                    closer.join(8)
        self.assertFalse(writer.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(closed.is_set())
        self.assert_fd_closed(fd)
        for operation in (owner.load, owner.clear, owner.__enter__,
                          lambda: owner.add("x", "y", False, 1),
                          lambda: owner.find_cached("", "invalid", "")):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                operation()
        with MacHistoryOwner(self.path) as successor:
            self.assertEqual(successor.load()[0]["output"], "y")

    def test_new_json_symlink_is_rejected_by_existing_owner(self):
        target = self.home / "synthetic original"
        target.write_bytes(b"[]")
        with MacHistoryOwner(self.path) as owner:
            self.path.symlink_to(target)
            for operation in (owner.load, owner.clear, lambda: owner.add("x", "y", False, 1)):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    operation()
        self.assertEqual(target.read_bytes(), b"[]")
        self.assertTrue(self.path.is_symlink())

    def test_close_failure_is_visible_and_consumed_descriptor_is_not_retried(self):
        owner = MacHistoryOwner(self.path)
        fd = owner._fd
        actual_close = os.close
        def close_then_fail(descriptor):
            actual_close(descriptor)
            raise OSError("synthetic ambiguous close")
        with patch.object(history_owner.os, "close", side_effect=close_then_fail) as close:
            with self.assertRaisesRegex(OSError, "ambiguous close"):
                owner.close()
            owner.close()
            close.assert_called_once_with(fd)
        self.assert_fd_closed(fd)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            owner.find_cached("", "invalid", "")
        with MacHistoryOwner(self.path):
            pass


if __name__ == "__main__":
    unittest.main()
