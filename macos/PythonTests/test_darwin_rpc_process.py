"""Real bundled-Python/C-bridge RPC tests; never a host or skipped substitute."""

from contextlib import ExitStack
import errno
import os
from pathlib import Path
import selectors
import signal
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


if sys.platform != "darwin":
    raise RuntimeError("This suite requires bundled macOS Python; do not substitute a host run.")

import cc_providers
from cc_providers import darwin_process as owned, darwin_rpc as rpc
from owner_process_support import OwnerProcessCase


CHILD_SCRIPT = r"""
import os
from pathlib import Path
import signal
import sys
import time
core = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(core))
from cc_providers import darwin_process, darwin_rpc
assert sys.flags.isolated and sys.dont_write_bytecode
assert Path(sys.executable).resolve().is_relative_to(core.parent.parent / "Helpers" / "python")
for module in (darwin_process, darwin_rpc):
    assert Path(module.__file__).resolve() == core.joinpath(*module.__name__.split(".")).with_suffix(".py")
assert Path(os.environ["HOME"]).resolve() == work.parent
assert Path.cwd().resolve() == work
def fallback(signum, frame):
    (work / ("fallback-" + str(os.getpid()))).write_text("synthetic timeout", encoding="ascii")
    os._exit(91)
signal.signal(signal.SIGALRM, fallback)
signal.alarm(20)
def write_all(data, fd=1):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]
mode = sys.argv[3]
if mode in ("group", "early"):
    ready_r, ready_w = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(ready_r)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.alarm(20)
        alive = os.open(work / "alive.fifo", os.O_WRONLY)
        assert os.getpgrp() == os.getppid()
        write_all(b"alive\n", alive)
        write_all(b"ready\n", ready_w)
        os.close(ready_w)
        while True:
            signal.pause()
    os.close(ready_w)
    assert os.read(ready_r, 6) == b"ready\n"
    os.close(ready_r)
    write_all("ready\ntrailing \u4e2d\n".encode("utf-8"))
    if mode == "early":
        os._exit(0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        signal.pause()
elif mode == "backpressure":
    write_all(b"ready\n")
    write_all(b"o" * (2 * 1024 * 1024) + b"\n")
    incoming = sys.stdin.buffer.readline()
    write_all(str(len(incoming)).encode("ascii") + b"\n")
elif mode == "silent":
    write_all(b"ready\n")
    while True:
        signal.pause()
elif mode == "stderr":
    write_all(b"SYNTHETIC_PRIVATE_STDERR" * 65536, 2)
    write_all(b"ready\n")
    while True:
        signal.pause()
elif mode == "truncated":
    write_all(b"complete\npartial")
elif mode == "invalid":
    write_all(b"SYNTHETIC_PRIVATE\xff\n")
elif mode == "blank":
    for line in sys.stdin.buffer:
        write_all(b"\n")
elif mode == "reset":
    assert sys.stdin.buffer.readline() == b"first\n"
    write_all(b"a\nbb\nc")
    assert sys.stdin.buffer.readline() == b"next\n"
    write_all(b"dd\n")
elif mode == "echo":
    for line in sys.stdin.buffer:
        write_all(line)
elif mode == "close-stdin":
    os.close(0)
    write_all(b"ready\n")
    while True:
        signal.pause()
elif mode == "close-stdout":
    write_all(b"ready\n")
    os.close(1)
    (work / "stdout-closed").write_text("ready", encoding="ascii")
    while True:
        signal.pause()
else:
    raise RuntimeError("unknown synthetic mode")
"""


class ReentryGuardLock:
    """Keep same-thread lock regressions bounded without leaking a worker."""

    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None

    def acquire(self, *, timeout):
        if self.thread == threading.get_ident():
            raise AssertionError("The operation attempted a same-thread lock wait.")
        acquired = self.lock.acquire(timeout=timeout)
        if acquired:
            self.thread = threading.get_ident()
        return acquired

    def release(self):
        self.thread = None
        self.lock.release()

    def __enter__(self):
        if not self.acquire(timeout=1):
            raise AssertionError("The test lock wait exceeded its bound.")
        return self

    def __exit__(self, *_args):
        self.release()


class TestDarwinRpcProcess(OwnerProcessCase):
    bundle_modules = (cc_providers, owned, rpc)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.library = cls.contents / "Helpers" / "python" / "lib" / "libCCProcessSupport.dylib"
        bridge = owned.load_supervision()
        if Path(bridge._name).resolve() != cls.library.resolve():
            raise RuntimeError("RPC supervision must load the same app's C dylib.")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".cc-rpc-process-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "synthetic home \u4e2d # %"
        self.home.mkdir()
        self.serial = 0

    def arguments(self, mode):
        self.serial += 1
        directory = self.home / ("process-" + str(self.serial))
        directory.mkdir()
        args = [sys.executable, "-I", "-B", "-c", CHILD_SCRIPT, str(self.core), str(directory), mode]
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "TMPDIR": str(self.home)}
        return args, env, directory

    def create(self, mode="echo", *, max_bytes=8 * 1024 * 1024):
        args, env, directory = self.arguments(mode)
        alive = None
        if mode in ("group", "early"):
            os.mkfifo(directory / "alive.fifo", 0o600)
            alive = os.open(directory / "alive.fifo", os.O_RDONLY | os.O_NONBLOCK)
            self.addCleanup(os.close, alive)
        start = time.monotonic()
        session = rpc.RpcProcess(args, env, directory, max_bytes=max_bytes)
        session.fixture = directory
        session.alive_fd = alive
        session.started_at = start
        self.addCleanup(self.cleanup_session, session)
        return session

    def cleanup_session(self, session):
        if not session._closed:
            session.close()

    def receive(self, session, seconds=5):
        return session.receive(deadline=time.monotonic() + seconds)

    def send(self, session, data, **kwargs):
        session.send(data, deadline=time.monotonic() + 8, **kwargs)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(rpc.RpcError) as raised:
            function(*args, **kwargs)
        self.assertEqual(str(raised.exception), code)
        self.assertIsInstance(raised.exception, owned.ProcessError)
        return raised.exception

    def trace_owner(self, session):
        owner = session.owner
        real = owner.bridge
        events = []
        def signal_group(pid, signum):
            self.assertTrue(owner.owned)
            self.assertIsNone(owner.process.returncode)
            events.append(("signal", signum))
            return real.cc_cli_signal_group(pid, signum)
        owner.bridge = SimpleNamespace(
            cc_cli_has_exited=real.cc_cli_has_exited,
            cc_cli_signal_group=signal_group,
        )
        wait = owner.process.wait
        def recorded_wait(*args, **kwargs):
            events.append(("wait", None))
            return wait(*args, **kwargs)
        patches = [
            patch.object(owner.process, "wait", side_effect=recorded_wait),
            patch.object(owner.process, "poll", side_effect=AssertionError("RPC must not poll")),
            patch.object(owner.process, "communicate", side_effect=AssertionError("RPC must not communicate")),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return events

    def assert_closed(self, session, descriptors):
        self.assertTrue(session.owner.closed)
        self.assertTrue(session.owner.finished)
        self.assertIsNotNone(session.owner.process.returncode)
        for stream in (session.owner.process.stdin, session.owner.process.stdout):
            self.assertTrue(stream.closed)
        self.assertIsNone(session.owner.process.stderr)
        for descriptor in descriptors:
            self.assert_fd_closed(descriptor)
        self.assertFalse(list(session.fixture.glob("fallback-*")))

    def assert_descendant_gone(self, session):
        # A unique FIFO proves this descendant released its descriptors without
        # signalling or looking up a possibly reused PID after leader reaping.
        self.assertEqual(os.read(session.alive_fd, 64), b"alive\n")
        self.assert_fifo_eof(session)

    def assert_fifo_eof(self, session):
        end = time.monotonic() + 3
        # A kqueue registered after FIFO EOF need not report a new readiness
        # event. Nonblocking read distinguishes a live writer (EAGAIN) from EOF.
        while True:
            try:
                remaining = os.read(session.alive_fd, 1)
            except BlockingIOError:
                self.assertLess(time.monotonic(), end,
                                "The TERM-resistant descendant still holds its liveness pipe.")
                time.sleep(0.01)
                continue
            self.assertEqual(remaining, b"")
            break
        self.assertLess(time.monotonic() - session.started_at, 12,
                        "Cleanup must be proved before the 20-second synthetic fallback.")
        self.assertFalse(list(session.fixture.glob("fallback-*")))

    def test_real_fifo_probe_distinguishes_live_writer_from_closed_group(self):
        session = self.create("group")
        self.assertEqual(self.receive(session), "ready")
        self.assertEqual(os.read(session.alive_fd, 64), b"alive\n")
        with self.assertRaises(BlockingIOError):
            os.read(session.alive_fd, 1)
        session.close()
        self.assert_fifo_eof(session)
        self.assertEqual(os.read(session.alive_fd, 1), b"")
        with selectors.DefaultSelector() as selector:
            selector.register(session.alive_fd, selectors.EVENT_READ)
            print("Synthetic FIFO: read EOF verified; late selector ready =",
                  bool(selector.select(0)))
        self.assertFalse(list(session.fixture.glob("fallback-*")))

    def test_real_bundle_bridge_and_nonblocking_rpc_pipes(self):
        session = self.create("silent")
        self.assertEqual(self.receive(session), "ready")
        self.assertEqual(session.owner.bridge.cc_process_support_abi(), 1)
        self.assertEqual(Path(session.owner.bridge._name).resolve(), self.library.resolve())
        self.assertFalse(os.get_blocking(session.owner.process.stdin.fileno()))
        self.assertFalse(os.get_blocking(session.owner.process.stdout.fileno()))
        self.assertIsNone(session.owner.process.stderr)
        self.assertTrue(session.is_running())

    def test_real_send_multiline_unicode_and_no_reader_threads(self):
        threads = {thread.ident for thread in threading.enumerate()}
        session = self.create()
        callback = Mock()
        self.send(session, "first\n\u4e2d \U0001f642\n\nlast\r\n".encode("utf-8"), on_write=callback)
        self.assertEqual([self.receive(session) for _ in range(4)], ["first", "\u4e2d \U0001f642", "", "last\r"])
        callback.assert_called_once_with()
        session.close()
        self.assertEqual({thread.ident for thread in threading.enumerate()}, threads)

    def test_real_stdout_backpressure_is_drained_during_large_send(self):
        session = self.create("backpressure")
        self.assertEqual(self.receive(session), "ready")
        frame = b"i" * (2 * 1024 * 1024) + b"\n"
        callback = Mock()
        self.send(session, frame, on_write=callback)
        self.assertEqual(self.receive(session), "o" * (2 * 1024 * 1024))
        self.assertEqual(self.receive(session), str(len(frame)))
        self.assertIsNone(self.receive(session))
        callback.assert_called_once_with()

    def test_real_stderr_flood_is_devnull_and_never_buffered(self):
        session = self.create("stderr")
        self.assertEqual(self.receive(session), "ready")
        self.assertEqual(session._received, len(b"ready\n"))
        self.assertEqual(bytes(session._buffer), b"")
        self.assertIsNone(session.owner.process.stderr)

    def test_real_empty_and_consumed_lines_count_toward_total_budget(self):
        session = self.create("blank", max_bytes=4)
        for _ in range(4):
            self.send(session, b"x\n")
            self.assertEqual(self.receive(session), "")
        self.send(session, b"x\n")
        self.assert_code("rpc_output_limit", self.receive, session)
        session.close()

    def test_real_input_budget_boundary_rejects_without_notification(self):
        session = self.create(max_bytes=4)
        self.send(session, b"abc\n")
        self.assertEqual(self.receive(session), "abc")
        callback = Mock()
        self.assert_code("rpc_input_limit", self.send, session, b"abcd\n", on_write=callback)
        callback.assert_not_called()

    def test_real_send_also_enforces_stdout_budget(self):
        session = self.create("backpressure", max_bytes=128 * 1024)
        self.assertEqual(self.receive(session), "ready")
        callback = Mock()
        self.assert_code("rpc_output_limit", self.send, session, b"i" * (128 * 1024), on_write=callback)
        self.assertLessEqual(callback.call_count, 1)
        session.close()

    def test_real_reset_preserves_buffer_and_its_budget(self):
        session = self.create("reset", max_bytes=8)
        self.send(session, b"first\n")
        self.assertEqual(self.receive(session), "a")
        self.assertEqual(bytes(session._buffer), b"bb\nc")
        session.reset_budget()
        self.assertEqual(session._received, 4)
        self.assertEqual(self.receive(session), "bb")
        self.send(session, b"next\n")
        self.assertEqual(self.receive(session), "cdd")
        self.assertEqual(session._received, 7)
        self.assertIsNone(self.receive(session))

    def test_real_eof_returns_complete_lines_then_reports_truncation(self):
        session = self.create("truncated")
        self.assertEqual(self.receive(session), "complete")
        self.assert_code("rpc_truncated", self.receive, session)
        session.reset_budget()
        self.assert_code("rpc_truncated", self.receive, session)
        session.close()

    def test_real_invalid_utf8_is_fixed_diagnostic(self):
        session = self.create("invalid")
        self.assert_code("rpc_invalid_utf8", self.receive, session)
        session.close()

    def test_real_receive_timeout_then_owned_cleanup(self):
        session = self.create("group")
        self.assertEqual(self.receive(session), "ready")
        self.assertEqual(self.receive(session), "trailing \u4e2d")
        start = time.monotonic()
        self.assert_code("rpc_timeout", session.receive, deadline=start + 0.15)
        self.assertLess(time.monotonic() - start, 1)
        session.close()
        self.assert_descendant_gone(session)

    def test_real_receive_cancellation_then_owned_cleanup(self):
        session = self.create("group")
        self.assertEqual(self.receive(session), "ready")
        self.assertEqual(self.receive(session), "trailing \u4e2d")
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        start = time.monotonic()
        try:
            self.assert_code("rpc_cancelled", session.receive,
                             deadline=start + 5, cancel_event=cancel)
        finally:
            timer.cancel()
            timer.join(1)
        self.assertFalse(timer.is_alive())
        self.assertLess(time.monotonic() - start, 1)
        session.close()
        self.assert_descendant_gone(session)

    def test_real_partial_send_timeout_calls_on_write_before_failure(self):
        session = self.create("silent")
        self.assertEqual(self.receive(session), "ready")
        callback = Mock()
        start = time.monotonic()
        self.assert_code("rpc_timeout", session.send, b"x" * (2 * 1024 * 1024),
                         deadline=start + 0.2, on_write=callback)
        self.assertLess(time.monotonic() - start, 1)
        callback.assert_called_once_with()
        session.close()

    def test_real_partial_send_cancellation_calls_on_write_before_failure(self):
        session = self.create("silent")
        self.assertEqual(self.receive(session), "ready")
        cancel = threading.Event()
        callback = Mock(side_effect=cancel.set)
        self.assert_code("rpc_cancelled", self.send, session, b"x" * (2 * 1024 * 1024),
                         cancel_event=cancel, on_write=callback)
        callback.assert_called_once_with()
        session.close()

    def test_real_pre_cancelled_send_does_not_write_or_notify(self):
        session = self.create()
        cancel = threading.Event()
        cancel.set()
        callback = Mock()
        self.assert_code("rpc_cancelled", self.send, session, b"forbidden\n",
                         cancel_event=cancel, on_write=callback)
        callback.assert_not_called()
        self.send(session, b"allowed\n")
        self.assertEqual(self.receive(session), "allowed")

    def test_real_noncallable_callback_is_rejected_before_submitting_bytes(self):
        session = self.create()
        with patch.object(rpc.os, "write", wraps=os.write) as write:
            with self.assertRaisesRegex(TypeError, "^on_write must be callable$"):
                self.send(session, b"forbidden\n", on_write=object())
            write.assert_not_called()
        self.send(session, b"allowed\n")
        self.assertEqual(self.receive(session), "allowed")

    def test_real_callback_error_propagates_without_replaying_partial_write(self):
        session = self.create("silent")
        self.assertEqual(self.receive(session), "ready")
        original = ValueError("SYNTHETIC_CALLBACK_BUG")
        callback = Mock(side_effect=original)
        submitted = []
        write = os.write
        def writing(fd, data):
            size = write(fd, data)
            submitted.append(size)
            return size
        with patch.object(rpc.os, "write", side_effect=writing):
            with self.assertRaises(ValueError) as raised:
                self.send(session, b"x" * (2 * 1024 * 1024), on_write=callback)
        self.assertIs(raised.exception, original)
        callback.assert_called_once_with()
        self.assertEqual(len(submitted), 1)
        self.assertGreater(submitted[0], 0)
        self.assertLess(submitted[0], 2 * 1024 * 1024)
        self.assertIsNone(session._active_thread)
        descriptors = [session.owner.process.stdin.fileno(), session.owner.process.stdout.fileno()]
        session.close()
        self.assert_closed(session, descriptors)

    def test_real_observed_stdout_eof_prevents_new_bytes_on_live_stdin(self):
        session = self.create("close-stdout")
        self.assertEqual(self.receive(session), "ready")
        self.assertIsNone(self.receive(session))
        self.assertTrue(session.is_running())
        callback = Mock()
        with patch.object(rpc.os, "write", wraps=os.write) as write:
            for data in (b"", b"forbidden\n"):
                self.assert_code("rpc_closed", self.send, session, data, on_write=callback)
            write.assert_not_called()
        callback.assert_not_called()
        session.close()

    def test_real_same_select_batch_stdout_eof_prevents_stdin_write(self):
        session = self.create("close-stdout")
        self.assertEqual(self.receive(session), "ready")
        deadline = time.monotonic() + 3
        while not (session.fixture / "stdout-closed").is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((session.fixture / "stdout-closed").is_file())
        self.assertFalse(session._eof)
        self.assertTrue(session.is_running())
        callback = Mock()
        batches = []
        select = session._selector.select
        def selecting(timeout):
            batch = select(timeout)
            batches.append({key.fileobj for key, _mask in batch})
            return batch
        with patch.object(rpc.os, "write", wraps=os.write) as write, \
                patch.object(session._selector, "select", side_effect=selecting):
            self.assert_code("rpc_closed", self.send, session, b"forbidden\n", on_write=callback)
            write.assert_not_called()
        self.assertEqual(batches[0], {session.owner.process.stdin, session.owner.process.stdout})
        callback.assert_not_called()
        self.assertTrue(session._eof)
        session.close()

    def test_real_broken_stdin_is_sanitized_without_commit_notification(self):
        session = self.create("close-stdin")
        self.assertEqual(self.receive(session), "ready")
        callback = Mock()
        self.assert_code("rpc_io_failed", self.send, session, b"SYNTHETIC_PRIVATE\n", on_write=callback)
        callback.assert_not_called()
        session.close()

    def test_real_early_leader_cleans_inherited_pipes_before_eof_and_not_sibling(self):
        sibling = self.create("silent")
        self.assertEqual(self.receive(sibling), "ready")
        session = self.create("early")
        events = self.trace_owner(session)
        self.assertEqual(self.receive(session), "ready")
        self.assertEqual(self.receive(session), "trailing \u4e2d")
        self.assertIsNone(self.receive(session))
        self.assertFalse(session.is_running())
        self.assertEqual(session.owner.process.returncode, 0)
        self.assert_descendant_gone(session)
        self.assertEqual(events, [("signal", signal.SIGTERM), ("signal", signal.SIGKILL), ("wait", None)])
        session.close()
        session.close()
        self.assertEqual(len(events), 3)
        self.assertTrue(sibling.is_running(), "Independent sibling must survive owned group cleanup.")

    def test_real_close_cleans_term_resistant_group_and_never_signals_twice(self):
        sibling = self.create("silent")
        self.assertEqual(self.receive(sibling), "ready")
        session = self.create("group")
        events = self.trace_owner(session)
        self.assertEqual(self.receive(session), "ready")
        descriptors = [session.owner.process.stdin.fileno(), session.owner.process.stdout.fileno()]
        session.close()
        self.assert_closed(session, descriptors)
        self.assert_descendant_gone(session)
        first = list(events)
        session.close()
        self.assertEqual(events, first)
        self.assertEqual(events, [("signal", signal.SIGTERM), ("signal", signal.SIGKILL), ("wait", None)])
        self.assertTrue(sibling.is_running())

    def test_real_close_interrupts_receive_and_rejects_post_close_writes(self):
        session = self.create("silent")
        self.assertEqual(self.receive(session), "ready")
        entered = threading.Event()
        result = []
        select = session._selector.select
        def selecting(timeout):
            entered.set()
            return select(timeout)
        def receiving():
            try:
                self.receive(session)
            except owned.ProcessError as error:
                result.append(str(error))
        with patch.object(session._selector, "select", side_effect=selecting):
            worker = threading.Thread(target=receiving)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                session.close()
            finally:
                session._closing.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ["rpc_closed"])
        callback = Mock()
        self.assert_code("rpc_closed", self.send, session, b"forbidden\n", on_write=callback)
        self.assert_code("rpc_closed", self.receive, session)
        self.assert_code("rpc_closed", session.reset_budget)
        self.assert_code("rpc_closed", session.is_running)
        callback.assert_not_called()

    def test_real_close_waits_for_partial_send_callback_before_closing_fds(self):
        session = self.create("silent")
        self.assertEqual(self.receive(session), "ready")
        entered = threading.Event()
        release = threading.Event()
        errors = []
        def callback():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Synthetic callback release timed out.")
        def sending():
            try:
                self.send(session, b"x" * (2 * 1024 * 1024), on_write=callback)
            except owned.ProcessError as error:
                errors.append(str(error))
        def closing():
            try:
                session.close()
            except owned.ProcessError as error:
                errors.append(str(error))
        worker = threading.Thread(target=sending)
        closer = threading.Thread(target=closing)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            closer.start()
            self.assertTrue(session._closing.wait(1))
            self.assertTrue(closer.is_alive())
            self.assertFalse(session.owner.closed)
            self.assertFalse(session.owner.process.stdin.closed)
            self.assert_code("rpc_closed", self.send, session, b"forbidden\n")
            self.assert_code("rpc_closed", session.reset_budget)
        finally:
            release.set()
            worker.join(3)
            if closer.ident is not None:
                closer.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors, ["rpc_closed"])
        self.assertTrue(session.owner.closed)

    def test_real_synchronous_callback_close_rejects_without_deadlocking(self):
        session = self.create("group")
        self.assertEqual(self.receive(session), "ready")
        session._lock = ReentryGuardLock()
        events = self.trace_owner(session)
        callback = Mock(side_effect=session.close)
        started = time.monotonic()
        with patch.object(rpc.os, "write", wraps=os.write) as write:
            self.assert_code("rpc_closed", self.send, session, b"x" * (2 * 1024 * 1024),
                             on_write=callback)
            self.assertEqual(write.call_count, 1)
        self.assertLess(time.monotonic() - started, 1)
        callback.assert_called_once_with()
        self.assertTrue(session._closing.is_set())
        self.assertFalse(session._closed)
        self.assertFalse(session.owner.closed)
        self.assertEqual(events, [])
        session.close()
        self.assert_descendant_gone(session)
        previous = list(events)
        session.close()
        self.assertEqual(events, previous)
        self.assertEqual(events, [("signal", signal.SIGTERM), ("signal", signal.SIGKILL), ("wait", None)])

    def test_real_synchronous_callback_cannot_interleave_nested_rpc_operations(self):
        session = self.create()
        session._lock = ReentryGuardLock()
        def callback():
            self.assert_code("rpc_io_failed", self.send, session, b"nested\n")
            self.assert_code("rpc_io_failed", self.receive, session)
            self.assert_code("rpc_io_failed", session.reset_budget)
            self.assert_code("rpc_io_failed", session.is_running)
        self.send(session, b"outer\n", on_write=callback)
        self.assertEqual(self.receive(session), "outer")
        self.assertIsNone(session._active_thread)
        session.close()

    def test_real_unknown_selector_operation_error_propagates_and_allows_cleanup(self):
        session = self.create("silent")
        self.assertEqual(self.receive(session), "ready")
        original = RuntimeError("SYNTHETIC_SELECTOR_BUG")
        with patch.object(session._selector, "select", side_effect=original):
            with self.assertRaises(RuntimeError) as raised:
                self.receive(session)
        self.assertIs(raised.exception, original)
        self.assertIsNone(session._active_thread)
        descriptors = [session.owner.process.stdin.fileno(), session.owner.process.stdout.fileno()]
        session.close()
        self.assert_closed(session, descriptors)

    def test_real_unknown_unregister_error_propagates_after_partial_submission(self):
        session = self.create("silent")
        self.assertEqual(self.receive(session), "ready")
        original = RuntimeError("SYNTHETIC_UNREGISTER_BUG")
        unregister = session._selector.unregister
        def fail_unregister(stream):
            unregister(stream)
            raise original
        callback_error = ValueError("SYNTHETIC_CALLBACK_BUG")
        callback = Mock(side_effect=callback_error)
        with patch.object(session._selector, "unregister", side_effect=fail_unregister), \
                patch.object(rpc.os, "write", wraps=os.write) as write:
            with self.assertRaises(RuntimeError) as raised:
                self.send(session, b"x" * (2 * 1024 * 1024), on_write=callback)
            self.assertEqual(write.call_count, 1)
        self.assertIs(raised.exception, original)
        self.assertIs(raised.exception.__context__, callback_error)
        callback.assert_called_once_with()
        self.assertIsNone(session._active_thread)
        descriptors = [session.owner.process.stdin.fileno(), session.owner.process.stdout.fileno()]
        session.close()
        self.assert_closed(session, descriptors)

    def test_real_constructor_fd_and_selector_failures_close_every_owned_resource(self):
        for stage in ("selector", "stdin_fileno", "stdout_fileno", "stdin_nonblock", "stdout_nonblock", "register"):
            with self.subTest(stage=stage):
                args, env, directory = self.arguments("silent")
                owner = owned.OwnedProcess(args, env, directory, rpc=True)
                self.addCleanup(owner.close)
                descriptors = [owner.process.stdin.fileno(), owner.process.stdout.fileno()]
                selector = selectors.DefaultSelector()
                self.addCleanup(selector.close)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(rpc, "OwnedProcess", return_value=owner))
                    stack.enter_context(patch.object(rpc.selectors, "DefaultSelector", return_value=selector))
                    private = OSError(errno.EBADF, "SYNTHETIC_PRIVATE_FD")
                    if stage == "selector":
                        stack.enter_context(patch.object(rpc.selectors, "DefaultSelector", side_effect=private))
                    elif stage.endswith("_fileno"):
                        stream = owner.process.stdin if stage.startswith("stdin") else owner.process.stdout
                        stack.enter_context(patch.object(stream, "fileno", side_effect=private))
                    elif stage.endswith("_nonblock"):
                        set_blocking = os.set_blocking
                        fail_fd = descriptors[0 if stage.startswith("stdin") else 1]
                        def fail_nonblock(fd, enabled):
                            if fd == fail_fd:
                                raise private
                            return set_blocking(fd, enabled)
                        stack.enter_context(patch.object(rpc.os, "set_blocking", side_effect=fail_nonblock))
                    else:
                        stack.enter_context(patch.object(selector, "register", side_effect=private))
                    self.assert_code("rpc_io_failed", rpc.RpcProcess, args, env, directory)
                self.assertTrue(owner.closed)
                self.assertTrue(owner.finished)
                self.assertIsNotNone(owner.process.returncode)
                for stream in (owner.process.stdin, owner.process.stdout):
                    self.assertTrue(stream.closed)
                for descriptor in descriptors:
                    self.assert_fd_closed(descriptor)
                if stage != "selector":
                    self.assertIsNone(selector.get_map())

    def test_real_constructor_cleanup_error_is_preserved_over_fd_error(self):
        args, env, directory = self.arguments("silent")
        owner = owned.OwnedProcess(args, env, directory, rpc=True)
        self.addCleanup(owner.close)
        descriptors = [owner.process.stdin.fileno(), owner.process.stdout.fileno()]
        real_close = owner.close
        original = owned.ProcessError("probe_cleanup_failed")
        def fail_after_cleanup():
            real_close()
            raise original
        with patch.object(rpc, "OwnedProcess", return_value=owner), \
                patch.object(rpc.os, "set_blocking", side_effect=OSError("SYNTHETIC_PRIVATE")), \
                patch.object(owner, "close", side_effect=fail_after_cleanup):
            with self.assertRaises(owned.ProcessError) as raised:
                rpc.RpcProcess(args, env, directory)
        self.assertIs(raised.exception, original)
        self.assertTrue(owner.closed)
        for descriptor in descriptors:
            self.assert_fd_closed(descriptor)

    def test_real_unknown_initialization_errors_close_owned_fds_and_propagate(self):
        for exception in (RuntimeError, KeyboardInterrupt):
            with self.subTest(exception=exception.__name__):
                args, env, directory = self.arguments("silent")
                owner = owned.OwnedProcess(args, env, directory, rpc=True)
                self.addCleanup(owner.close)
                descriptors = [owner.process.stdin.fileno(), owner.process.stdout.fileno()]
                selector = selectors.DefaultSelector()
                self.addCleanup(selector.close)
                original = exception("SYNTHETIC_SETUP_BUG")
                with patch.object(rpc, "OwnedProcess", return_value=owner), \
                        patch.object(rpc.selectors, "DefaultSelector", return_value=selector), \
                        patch.object(selector, "register", side_effect=original):
                    with self.assertRaises(exception) as raised:
                        rpc.RpcProcess(args, env, directory)
                self.assertIs(raised.exception, original)
                self.assertTrue(owner.closed)
                self.assertTrue(owner.finished)
                self.assertIsNotNone(owner.process.returncode)
                self.assertIsNone(selector.get_map())
                for descriptor in descriptors:
                    self.assert_fd_closed(descriptor)

    def test_real_cleanup_error_is_sticky_without_second_signal_or_wait(self):
        session = self.create("group")
        self.assertEqual(self.receive(session), "ready")
        events = self.trace_owner(session)
        real_signal = session.owner.bridge.cc_cli_signal_group
        def signal_then_report_failure(pid, signum):
            result = real_signal(pid, signum)
            self.assertEqual(result, 0)
            return errno.EPERM
        session.owner.bridge.cc_cli_signal_group = signal_then_report_failure
        with self.assertRaises(owned.ProcessError) as first:
            session.close()
        self.assertEqual(str(first.exception), "probe_cleanup_failed")
        self.assertNotIsInstance(first.exception, rpc.RpcError)
        self.assert_descendant_gone(session)
        previous = list(events)
        with self.assertRaises(owned.ProcessError) as second:
            session.close()
        self.assertIs(second.exception, first.exception)
        self.assertEqual(events, previous)
        self.assertEqual(events, [("signal", signal.SIGTERM), ("signal", signal.SIGKILL), ("wait", None)])
        self.assertTrue(session.owner.process.stdin.closed)
        self.assertTrue(session.owner.process.stdout.closed)
        self.assert_code("rpc_closed", self.send, session, b"forbidden\n")

    def test_real_selector_close_failure_still_cleans_group_and_stays_visible(self):
        session = self.create("group")
        self.assertEqual(self.receive(session), "ready")
        events = self.trace_owner(session)
        selector_close = session._selector.close
        original = OSError("SYNTHETIC_PRIVATE_CLOSE")
        def fail_after_close():
            selector_close()
            raise original
        with patch.object(session._selector, "close", side_effect=fail_after_close) as close:
            first = self.assert_code("rpc_cleanup_failed", session.close)
            second = self.assert_code("rpc_cleanup_failed", session.close)
            self.assertIs(first, second)
            self.assertIs(first.__cause__, original)
            close.assert_called_once_with()
        self.assert_descendant_gone(session)
        self.assertEqual(events, [("signal", signal.SIGTERM), ("signal", signal.SIGKILL), ("wait", None)])

    def test_real_unknown_selector_close_error_cleans_group_and_is_sticky(self):
        session = self.create("group")
        self.assertEqual(self.receive(session), "ready")
        events = self.trace_owner(session)
        original = RuntimeError("SYNTHETIC_SELECTOR_CLOSE_BUG")
        close = session._selector.close
        def fail_after_close():
            close()
            raise original
        with patch.object(session._selector, "close", side_effect=fail_after_close) as closing:
            first = self.assert_code("rpc_cleanup_failed", session.close)
            second = self.assert_code("rpc_cleanup_failed", session.close)
            self.assertIs(first, second)
            self.assertIs(first.__cause__, original)
            closing.assert_called_once_with()
        self.assert_descendant_gone(session)
        self.assertEqual(events, [("signal", signal.SIGTERM), ("signal", signal.SIGKILL), ("wait", None)])

    def test_real_unknown_owner_close_error_is_sticky_without_repeating_signals(self):
        session = self.create("group")
        self.assertEqual(self.receive(session), "ready")
        events = self.trace_owner(session)
        original = RuntimeError("SYNTHETIC_OWNER_CLOSE_BUG")
        close = session.owner.close
        def fail_after_close():
            close()
            raise original
        with patch.object(session.owner, "close", side_effect=fail_after_close) as closing:
            first = self.assert_code("rpc_cleanup_failed", session.close)
            second = self.assert_code("rpc_cleanup_failed", session.close)
            self.assertIs(first, second)
            self.assertIs(first.__cause__, original)
            closing.assert_called_once_with()
        self.assert_descendant_gone(session)
        self.assertEqual(events, [("signal", signal.SIGTERM), ("signal", signal.SIGKILL), ("wait", None)])
        self.assertTrue(session.owner.process.stdin.closed)
        self.assertTrue(session.owner.process.stdout.closed)

    def test_real_constructor_selector_cleanup_failure_marks_fatal_before_proc_return(self):
        for exception in (OSError, RuntimeError):
            with self.subTest(exception=exception.__name__):
                args, env, directory = self.arguments("silent")
                owner = owned.OwnedProcess(args, env, directory, rpc=True)
                self.addCleanup(owner.close)
                descriptors = [owner.process.stdin.fileno(), owner.process.stdout.fileno()]
                selector = selectors.DefaultSelector()
                self.addCleanup(selector.close)
                selector_close = selector.close
                original = exception("SYNTHETIC_SELECTOR_CLEANUP_ERROR")
                def fail_after_close():
                    selector_close()
                    raise original
                session = rpc.RpcProcess.__new__(rpc.RpcProcess)
                with patch.object(rpc, "OwnedProcess", return_value=owner), \
                        patch.object(rpc.selectors, "DefaultSelector", return_value=selector), \
                        patch.object(rpc.os, "set_blocking", side_effect=OSError("SYNTHETIC_SETUP_ERROR")), \
                        patch.object(selector, "close", side_effect=fail_after_close) as close, \
                        patch.object(owner, "close", wraps=owner.close) as owner_close, \
                        patch.object(rpc.os, "write", wraps=os.write) as write:
                    first = self.assert_code("rpc_cleanup_failed", session.__init__, args, env, directory)
                    self.assertIs(first.__cause__, original)
                    self.assert_code("rpc_closed", self.send, session, b"forbidden\n")
                    second = self.assert_code("rpc_cleanup_failed", session.close)
                    self.assertIs(first, second)
                    close.assert_called_once_with()
                    owner_close.assert_called_once_with()
                    write.assert_not_called()
                self.assertTrue(owner.closed)
                self.assertTrue(owner.finished)
                self.assertIsNotNone(owner.process.returncode)
                self.assertIsNone(selector.get_map())
                for descriptor in descriptors:
                    self.assert_fd_closed(descriptor)


if __name__ == "__main__":
    unittest.main()
