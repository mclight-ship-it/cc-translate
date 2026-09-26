"""Portable mocked contracts; actual Darwin processes have a separate suite."""

from collections import deque
import errno
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cc_providers import darwin_rpc as rpc
from cc_providers.darwin_process import ProcessError


class FakeSelector:
    def __init__(self):
        self.keys = {}
        self.closed = False
        self.timeouts = []
        self.ready = None

    def register(self, stream, events):
        if stream in self.keys:
            raise KeyError("SYNTHETIC_PRIVATE_DUPLICATE")
        self.keys[stream] = SimpleNamespace(fileobj=stream, events=events)

    def unregister(self, stream):
        return self.keys.pop(stream)

    def select(self, timeout):
        self.timeouts.append(timeout)
        if self.ready is not None:
            return self.ready(timeout)
        return [(key, key.events) for key in tuple(self.keys.values())]

    def close(self):
        self.closed = True
        self.keys.clear()


class ReentryGuardLock:
    """Fail a regression instead of hanging the test on a same-thread wait."""

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


class TestDarwinRpcContract(unittest.TestCase):
    def setUp(self):
        self.owner = Mock()
        self.owner.process.stdin.fileno.return_value = 10
        self.owner.process.stdout.fileno.return_value = 11
        self.owner.process.stderr = None
        self.owner.has_exited.return_value = False
        self.selector = FakeSelector()
        self.chunks = deque()
        self.writes = []
        self.read_sizes = []
        self.calls = []
        self.patchers = [
            patch.object(rpc, "OwnedProcess", return_value=self.owner),
            patch.object(rpc.selectors, "DefaultSelector", return_value=self.selector),
            patch.object(rpc.os, "set_blocking"),
            patch.object(rpc.os, "read", side_effect=self.read),
            patch.object(rpc.os, "write", side_effect=self.write),
        ]
        self.factory, self.selector_factory, self.set_blocking, self.os_read, self.os_write = (
            patcher.start() for patcher in self.patchers)
        for patcher in self.patchers:
            self.addCleanup(patcher.stop)

    def read(self, fd, size):
        self.assertEqual(fd, 11)
        self.read_sizes.append(size)
        self.calls.append("read")
        if not self.chunks:
            raise BlockingIOError()
        data = self.chunks.popleft()
        if isinstance(data, BaseException):
            raise data
        if len(data) > size:
            self.chunks.appendleft(data[size:])
        return data[:size]

    def write(self, fd, data):
        self.assertEqual(fd, 10)
        self.calls.append("write")
        self.writes.append(bytes(data))
        return len(data)

    def create(self, max_bytes=8 * 1024 * 1024):
        session = rpc.RpcProcess(["synthetic"], {"SYNTHETIC": "setting"}, "unused",
                                 max_bytes=max_bytes)
        self.addCleanup(self.cleanup, session)
        return session

    def cleanup(self, session):
        if not session._closed:
            session.close()

    def receive(self, session):
        return session.receive(deadline=time.monotonic() + 2)

    def send(self, session, data=b"request\n", **kwargs):
        return session.send(data, deadline=time.monotonic() + 2, **kwargs)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(rpc.RpcError) as raised:
            function(*args, **kwargs)
        self.assertEqual(str(raised.exception), code)
        self.assertIsInstance(raised.exception, ProcessError)
        return raised.exception

    def test_constructor_forwards_rpc_and_nonblocking_streams(self):
        session = self.create()
        self.factory.assert_called_once_with(["synthetic"], {"SYNTHETIC": "setting"}, "unused", rpc=True)
        self.assertEqual(self.set_blocking.call_args_list, [((10, False),), ((11, False),)])
        self.assertEqual(list(self.selector.keys), [self.owner.process.stdout])
        self.assertIs(session.owner, self.owner)
        self.assertIsNone(session.owner.process.stderr)

    def test_real_defaults_allow_eight_megabytes_not_more(self):
        session = self.create()
        self.assert_code("rpc_input_limit", self.send, session, b"x" * (8 * 1024 * 1024 + 1))
        self.os_write.assert_not_called()

    def test_multiline_unicode_and_empty_lines_preserve_bytes(self):
        session = self.create()
        self.chunks.extend([b"\n \t\n\xe4", b"\xb8\xad\nsecond\r\n", b""])
        self.assertEqual([self.receive(session) for _ in range(5)], ["", " \t", "\u4e2d", "second\r", None])
        self.assertEqual(session._received, len(b"\n \t\n\xe4\xb8\xad\nsecond\r\n"))

    def test_send_drains_stdout_before_each_write(self):
        session = self.create()
        self.chunks.append(b"response\n")
        self.send(session)
        self.assertEqual(self.calls[:2], ["read", "write"])
        self.assertEqual(self.receive(session), "response")
        self.assertNotIn(self.owner.process.stdin, self.selector.keys)

    def test_callback_immediately_after_first_positive_partial_write(self):
        session = self.create()
        def partial(fd, data):
            self.calls.append("positive-write")
            return 2
        self.os_write.side_effect = partial
        callback = Mock(side_effect=lambda: self.calls.append("committed"))
        self.send(session, b"abcdef", on_write=callback)
        callback.assert_called_once_with()
        first = self.calls.index("positive-write")
        self.assertEqual(self.calls[first + 1], "committed")
        self.assertEqual(self.os_write.call_count, 3)

    def test_blocked_and_interrupted_writes_do_not_notify_until_positive(self):
        session = self.create()
        callback = Mock()
        self.os_write.side_effect = [BlockingIOError(), InterruptedError(), 8]
        self.send(session, on_write=callback)
        callback.assert_called_once_with()
        self.assertEqual(self.os_write.call_count, 3)

    def test_partial_write_cancel_retains_commit_notification(self):
        session = self.create()
        cancel = threading.Event()
        self.os_write.side_effect = lambda fd, data: 1
        callback = Mock(side_effect=cancel.set)
        self.assert_code("rpc_cancelled", self.send, session, cancel_event=cancel, on_write=callback)
        callback.assert_called_once_with()
        self.assertEqual(self.os_write.call_count, 1)
        self.assertNotIn(self.owner.process.stdin, self.selector.keys)

    def test_partial_write_timeout_retains_commit_notification(self):
        session = self.create()
        clock = [10.0]
        self.os_write.side_effect = lambda fd, data: 1
        callback = Mock(side_effect=lambda: clock.__setitem__(0, 20.0))
        with patch.object(rpc.time, "monotonic", side_effect=lambda: clock[0]):
            self.assert_code("rpc_timeout", session.send, b"frame\n", deadline=15.0, on_write=callback)
        callback.assert_called_once_with()
        self.assertEqual(self.os_write.call_count, 1)

    def test_write_failure_after_partial_frame_keeps_callback_once(self):
        session = self.create()
        self.os_write.side_effect = [1, OSError("SYNTHETIC_PRIVATE_PATH")]
        callback = Mock()
        self.assert_code("rpc_io_failed", self.send, session, on_write=callback)
        callback.assert_called_once_with()

    def test_zero_or_failed_first_write_does_not_notify(self):
        session = self.create()
        for result in (0, -1, OSError("SYNTHETIC_PRIVATE")):
            with self.subTest(result=type(result).__name__):
                self.os_write.side_effect = [result]
                callback = Mock()
                self.assert_code("rpc_io_failed", self.send, session, on_write=callback)
                callback.assert_not_called()

    def test_callback_failure_propagates_after_actual_write_without_replay(self):
        session = self.create()
        self.os_write.side_effect = lambda fd, data: 1
        for error in (ValueError("SYNTHETIC_PRIVATE"), ProcessError("SYNTHETIC_PRIVATE")):
            callback = Mock(side_effect=error)
            with self.assertRaises(type(error)) as raised:
                self.send(session, on_write=callback)
            self.assertIs(raised.exception, error)
            callback.assert_called_once_with()
            self.assertNotIn(self.owner.process.stdin, self.selector.keys)
        self.assertEqual(self.os_write.call_count, 2)

    def test_noncallable_on_write_is_rejected_before_any_io(self):
        session = self.create()
        for data in (b"", b"request\n"):
            with self.assertRaisesRegex(TypeError, "^on_write must be callable$"):
                self.send(session, data, on_write=object())
        self.os_write.assert_not_called()
        self.os_read.assert_not_called()
        self.assertNotIn(self.owner.process.stdin, self.selector.keys)

    def test_empty_input_has_no_write_or_callback(self):
        session = self.create()
        callback = Mock()
        self.send(session, b"", on_write=callback)
        callback.assert_not_called()
        self.os_write.assert_not_called()

    def test_input_budget_boundary_and_type_are_checked_before_writing(self):
        session = self.create(max_bytes=4)
        self.send(session, b"abcd")
        callback = Mock()
        self.assert_code("rpc_input_limit", self.send, session, b"abcde", on_write=callback)
        self.assert_code("rpc_io_failed", self.send, session, "private", on_write=callback)
        callback.assert_not_called()
        self.assertEqual(self.os_write.call_count, 1)

    def test_consumed_and_blank_lines_still_exhaust_total_output_budget(self):
        session = self.create(max_bytes=4)
        for chunk, expected in [(b"\n", ""), (b"a\n", "a"), (b"\n", "")]:
            self.chunks.append(chunk)
            self.assertEqual(self.receive(session), expected)
        self.chunks.append(b"\n")
        self.assert_code("rpc_output_limit", self.receive, session)
        self.assertLessEqual(max(self.read_sizes), 5)

    def test_send_enforces_same_total_output_budget(self):
        session = self.create(max_bytes=4)
        self.chunks.append(b"12345")
        self.assert_code("rpc_output_limit", self.send, session, b"x")
        self.os_write.assert_not_called()

    def test_reset_releases_consumed_budget_but_keeps_buffered_bytes(self):
        session = self.create(max_bytes=8)
        self.chunks.append(b"a\nbb\nc")
        self.assertEqual(self.receive(session), "a")
        self.assertEqual(session._received, 6)
        session.reset_budget()
        self.assertEqual(session._received, 4)
        self.assertEqual(self.receive(session), "bb")
        self.chunks.append(b"dd\n")
        self.assertEqual(self.receive(session), "cdd")
        self.assertEqual(session._received, 7)
        self.chunks.append(b"\n")
        self.assertEqual(self.receive(session), "")
        self.chunks.append(b"\n")
        self.assert_code("rpc_output_limit", self.receive, session)

    def test_reset_preserves_eof_and_truncated_data(self):
        session = self.create()
        self.chunks.extend([b"partial", b""])
        self.assert_code("rpc_truncated", self.receive, session)
        session.reset_budget()
        self.assertEqual(bytes(session._buffer), b"partial")
        self.assert_code("rpc_truncated", self.receive, session)

    def test_invalid_utf8_has_fixed_code(self):
        session = self.create()
        self.chunks.append(b"SYNTHETIC_PRIVATE\xff\n")
        self.assert_code("rpc_invalid_utf8", self.receive, session)

    def test_unterminated_invalid_utf8_is_truncated_not_decoded(self):
        session = self.create()
        self.chunks.extend([b"\xff", b""])
        self.assert_code("rpc_truncated", self.receive, session)

    def test_pre_cancelled_or_expired_operations_do_not_touch_io(self):
        session = self.create()
        cancel = threading.Event()
        cancel.set()
        for method, args in [(session.send, (b"x",)), (session.receive, ())]:
            self.assert_code("rpc_cancelled", method, *args, deadline=time.monotonic() + 1, cancel_event=cancel)
            self.assert_code("rpc_timeout", method, *args, deadline=time.monotonic() - 1)
        self.os_read.assert_not_called()
        self.os_write.assert_not_called()
        self.owner.has_exited.assert_not_called()

    def test_wait_checks_cancel_at_bounded_selector_intervals(self):
        session = self.create()
        cancel = threading.Event()
        def ready(timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 0.05)
            cancel.set()
            return []
        self.selector.ready = ready
        self.assert_code("rpc_cancelled", session.receive, deadline=time.monotonic() + 1, cancel_event=cancel)

    def test_wait_checks_deadline_at_bounded_selector_intervals(self):
        session = self.create()
        clock = [10.0]
        def ready(timeout):
            self.assertLessEqual(timeout, 0.05)
            clock[0] += 0.05
            return []
        self.selector.ready = ready
        with patch.object(rpc.time, "monotonic", side_effect=lambda: clock[0]):
            self.assert_code("rpc_timeout", session.receive, deadline=10.11)
        self.assertEqual(len(self.selector.timeouts), 3)

    def test_receive_reads_eof_even_while_leader_is_alive(self):
        session = self.create()
        self.chunks.append(b"")
        self.assertIsNone(self.receive(session))
        self.assertTrue(session.is_running())
        self.owner.terminate.assert_not_called()

    def test_observed_stdout_eof_refuses_all_later_writes(self):
        session = self.create()
        self.chunks.append(b"")
        self.assertIsNone(self.receive(session))
        callback = Mock()
        for data in (b"", b"forbidden\n"):
            self.assert_code("rpc_closed", self.send, session, data, on_write=callback)
        self.os_write.assert_not_called()
        callback.assert_not_called()
        self.assertTrue(session.is_running())

    def test_stdout_eof_in_same_select_batch_prevents_first_write(self):
        session = self.create()
        self.chunks.append(b"")
        callback = Mock()
        self.assert_code("rpc_closed", self.send, session, on_write=callback)
        self.os_write.assert_not_called()
        callback.assert_not_called()
        self.assertTrue(session._eof)
        self.assertNotIn(self.owner.process.stdin, self.selector.keys)

    def test_stdout_eof_after_partial_send_does_not_submit_more_bytes(self):
        session = self.create()
        self.chunks.extend([BlockingIOError(), b""])
        self.os_write.side_effect = lambda fd, data: 1
        callback = Mock()
        self.assert_code("rpc_closed", self.send, session, on_write=callback)
        self.assertEqual(self.os_write.call_count, 1)
        callback.assert_called_once_with()
        self.assertNotIn(self.owner.process.stdin, self.selector.keys)

    def test_stdout_eof_without_stdin_ready_is_rejected_immediately(self):
        session = self.create()
        self.chunks.append(b"")
        output_key = self.selector.keys[self.owner.process.stdout]
        self.selector.ready = Mock(return_value=[(output_key, output_key.events)])
        self.assert_code("rpc_closed", self.send, session)
        self.selector.ready.assert_called_once()
        self.os_write.assert_not_called()
        self.assertNotIn(self.owner.process.stdin, self.selector.keys)

    def test_early_leader_cleans_group_before_delivering_buffered_lines(self):
        session = self.create()
        self.chunks.append(b"one\ntwo\n")
        self.assertEqual(self.receive(session), "one")
        self.owner.has_exited.return_value = True
        self.owner.terminate.side_effect = lambda: self.calls.append("terminate")
        self.assertEqual(self.receive(session), "two")
        self.assertEqual(self.calls[-1], "terminate")
        self.chunks.append(b"")
        self.assertIsNone(self.receive(session))
        self.owner.process.poll.assert_not_called()
        self.owner.process.wait.assert_not_called()
        self.owner.process.communicate.assert_not_called()

    def test_is_running_uses_nonreaping_owner_and_terminates_exited_group(self):
        session = self.create()
        self.assertTrue(session.is_running())
        self.owner.has_exited.return_value = True
        self.assertFalse(session.is_running())
        self.owner.terminate.assert_called_once_with()
        self.owner.process.poll.assert_not_called()
        self.owner.process.wait.assert_not_called()
        self.owner.process.communicate.assert_not_called()

    def test_send_to_exited_owner_is_closed_without_writing(self):
        session = self.create()
        self.owner.has_exited.return_value = True
        self.assert_code("rpc_closed", self.send, session)
        self.owner.terminate.assert_called_once_with()
        self.os_write.assert_not_called()

    def test_original_supervision_errors_propagate_unchanged(self):
        session = self.create()
        for code in ("probe_failed", "probe_cleanup_failed", "runtime_unavailable"):
            original = ProcessError(code)
            self.owner.has_exited.side_effect = original
            with self.assertRaises(ProcessError) as raised:
                self.receive(session)
            self.assertIs(raised.exception, original)
            self.assertNotIsInstance(raised.exception, rpc.RpcError)

    def test_early_termination_error_is_not_hidden_by_unregister_failure(self):
        session = self.create()
        original = ProcessError("probe_cleanup_failed")
        self.owner.has_exited.return_value = True
        self.owner.terminate.side_effect = original
        with patch.object(self.selector, "unregister", side_effect=OSError("SYNTHETIC_PRIVATE")):
            with self.assertRaises(ProcessError) as raised:
                self.send(session)
        self.assertIs(raised.exception, original)

    def test_os_read_and_selector_errors_are_sanitized(self):
        session = self.create()
        self.chunks.append(OSError("SYNTHETIC_PRIVATE_READ"))
        self.assert_code("rpc_io_failed", self.receive, session)
        self.selector.ready = Mock(side_effect=OSError("SYNTHETIC_PRIVATE_SELECTOR"))
        self.assert_code("rpc_io_failed", self.receive, session)

    def test_interrupted_read_is_retried_without_losing_bytes(self):
        session = self.create()
        self.chunks.extend([InterruptedError(), BlockingIOError(), b"ok\n"])
        self.assertEqual(self.receive(session), "ok")

    def test_unknown_io_and_selector_errors_propagate_and_release_operation_lock(self):
        session = self.create()
        for target in ("read", "write", "select"):
            with self.subTest(target=target):
                original = RuntimeError("SYNTHETIC_PROGRAMMING_ERROR")
                self.os_read.side_effect = self.read
                self.os_write.side_effect = self.write
                self.selector.ready = None
                if target == "read":
                    self.os_read.side_effect = original
                elif target == "write":
                    self.os_write.side_effect = original
                else:
                    self.selector.ready = Mock(side_effect=original)
                with self.assertRaises(RuntimeError) as raised:
                    self.send(session)
                self.assertIs(raised.exception, original)
                self.assertNotIn(self.owner.process.stdin, self.selector.keys)
                self.assertIsNone(session._active_thread)
                session.reset_budget()
        session.close()
        self.owner.close.assert_called_once_with()

    def test_unknown_unregister_error_is_not_silently_suppressed(self):
        session = self.create()
        original = RuntimeError("SYNTHETIC_UNREGISTER_ERROR")
        unregister = self.selector.unregister
        def fail_unregister(stream):
            unregister(stream)
            raise original
        callback_error = ValueError("SYNTHETIC_CALLBACK_ERROR")
        with patch.object(self.selector, "unregister", side_effect=fail_unregister):
            with self.assertRaises(RuntimeError) as raised:
                self.send(session, on_write=Mock(side_effect=callback_error))
        self.assertIs(raised.exception, original)
        self.assertIs(raised.exception.__context__, callback_error)
        self.assertIsNone(session._active_thread)
        session.close()
        self.owner.close.assert_called_once_with()

    def test_known_unregister_error_without_primary_failure_is_sanitized(self):
        session = self.create()
        with patch.object(self.selector, "unregister", side_effect=OSError("SYNTHETIC_PRIVATE")):
            self.assert_code("rpc_io_failed", self.send, session)
        self.assertIsNone(session._active_thread)
        session.close()
        self.owner.close.assert_called_once_with()

    def test_synchronous_callback_close_is_bounded_and_requires_outer_close(self):
        session = self.create()
        session._lock = ReentryGuardLock()
        self.os_write.side_effect = lambda fd, data: 1
        callback = Mock(side_effect=session.close)
        started = time.monotonic()
        self.assert_code("rpc_closed", self.send, session, on_write=callback)
        self.assertLess(time.monotonic() - started, 1)
        callback.assert_called_once_with()
        self.assertEqual(self.os_write.call_count, 1)
        self.assertTrue(session._closing.is_set())
        self.assertFalse(session._closed)
        self.owner.close.assert_not_called()
        self.assertNotIn(self.owner.process.stdin, self.selector.keys)
        session.close()
        session.close()
        self.owner.close.assert_called_once_with()

    def test_synchronous_callback_cannot_interleave_nested_operations(self):
        session = self.create()
        session._lock = ReentryGuardLock()
        def callback():
            self.assert_code("rpc_io_failed", self.send, session, b"nested\n")
            self.assert_code("rpc_io_failed", self.receive, session)
            self.assert_code("rpc_io_failed", session.reset_budget)
            self.assert_code("rpc_io_failed", session.is_running)
        self.send(session, b"outer\n", on_write=callback)
        self.assertEqual(self.writes, [b"outer\n"])
        self.assertIsNone(session._active_thread)
        session.close()

    def test_close_waits_for_active_send_then_rejects_all_operations(self):
        session = self.create()
        entered = threading.Event()
        release = threading.Event()
        errors = []
        def callback():
            entered.set()
            if not release.wait(2):
                raise RuntimeError("synthetic callback was not released")
        def sending():
            try:
                self.send(session, on_write=callback)
            except ProcessError as error:
                errors.append(str(error))
        sender = threading.Thread(target=sending)
        sender.start()
        closer = threading.Thread(target=session.close)
        try:
            self.assertTrue(entered.wait(1))
            closer.start()
            self.assertTrue(session._closing.wait(1))
            self.assertTrue(closer.is_alive())
            self.owner.close.assert_not_called()
            self.assertFalse(self.selector.closed)
            self.assert_code("rpc_closed", self.send, session)
            self.assert_code("rpc_closed", self.receive, session)
            self.assert_code("rpc_closed", session.reset_budget)
            self.assert_code("rpc_closed", session.is_running)
        finally:
            release.set()
            sender.join(2)
            if closer.ident is not None:
                closer.join(2)
        self.assertFalse(sender.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors, ["rpc_closed"])
        self.owner.close.assert_called_once_with()
        session.close()
        self.owner.close.assert_called_once_with()

    def test_operation_waiting_for_lock_can_cancel_or_expire(self):
        session = self.create()
        session._lock.acquire()
        try:
            self.assert_code("rpc_timeout", session.receive, deadline=time.monotonic() + 0.01)
            cancel = threading.Event()
            cancel.set()
            self.assert_code("rpc_cancelled", self.send, session, cancel_event=cancel)
        finally:
            session._lock.release()

    def test_close_failure_is_sticky_and_never_retries_owner(self):
        session = self.create()
        original = ProcessError("probe_cleanup_failed")
        self.owner.close.side_effect = original
        for _ in range(2):
            with self.assertRaises(ProcessError) as raised:
                session.close()
            self.assertIs(raised.exception, original)
        self.assertTrue(self.selector.closed)
        self.owner.close.assert_called_once_with()
        self.assert_code("rpc_closed", self.receive, session)

    def test_selector_close_failure_still_closes_owner_and_is_sticky(self):
        session = self.create()
        original = OSError("SYNTHETIC_PRIVATE")
        with patch.object(self.selector, "close", side_effect=original) as close:
            first = self.assert_code("rpc_cleanup_failed", session.close)
            second = self.assert_code("rpc_cleanup_failed", session.close)
            self.assertIs(second, first)
            self.assertIs(first.__cause__, original)
            close.assert_called_once_with()
        self.owner.close.assert_called_once_with()

    def test_owner_cleanup_failure_takes_priority_over_selector_failure(self):
        session = self.create()
        original = ProcessError("probe_cleanup_failed")
        self.owner.close.side_effect = original
        with patch.object(self.selector, "close", side_effect=OSError(errno.EBADF, "SYNTHETIC_PRIVATE")):
            with self.assertRaises(ProcessError) as raised:
                session.close()
        self.assertIs(raised.exception, original)

    def test_unknown_selector_close_error_still_closes_owner_and_is_sticky(self):
        session = self.create()
        original = RuntimeError("SYNTHETIC_SELECTOR_BUG")
        with patch.object(self.selector, "close", side_effect=original) as close:
            first = self.assert_code("rpc_cleanup_failed", session.close)
            second = self.assert_code("rpc_cleanup_failed", session.close)
            self.assertIs(first, second)
            self.assertIs(first.__cause__, original)
            close.assert_called_once_with()
        self.owner.close.assert_called_once_with()
        self.assert_code("rpc_closed", self.send, session)

    def test_unknown_owner_close_error_is_sticky_without_second_cleanup(self):
        session = self.create()
        original = RuntimeError("SYNTHETIC_OWNER_BUG")
        self.owner.close.side_effect = original
        first = self.assert_code("rpc_cleanup_failed", session.close)
        second = self.assert_code("rpc_cleanup_failed", session.close)
        self.assertIs(first, second)
        self.assertIs(first.__cause__, original)
        self.assertTrue(self.selector.closed)
        self.owner.close.assert_called_once_with()

    def test_baseexception_close_failure_is_recorded_without_repeating_cleanup(self):
        session = self.create()
        original = KeyboardInterrupt()
        self.owner.close.side_effect = original
        first = self.assert_code("rpc_cleanup_failed", session.close)
        second = self.assert_code("rpc_cleanup_failed", session.close)
        self.assertIs(first, second)
        self.assertIs(first.__cause__, original)
        self.assertTrue(self.selector.closed)
        self.owner.close.assert_called_once_with()

    def test_owner_creation_failure_preserves_original_without_selector_leak(self):
        original = ProcessError("probe_unavailable")
        self.factory.side_effect = original
        with self.assertRaises(ProcessError) as raised:
            self.create()
        self.assertIs(raised.exception, original)
        self.selector_factory.assert_not_called()
        self.owner.close.assert_not_called()

    def test_selector_creation_failure_cleans_owned_process(self):
        self.selector_factory.side_effect = OSError("SYNTHETIC_PRIVATE_SELECTOR")
        self.assert_code("rpc_io_failed", self.create)
        self.owner.close.assert_called_once_with()

    def test_each_fd_setup_failure_closes_selector_and_owner(self):
        for failure in ("stdin_fileno", "stdout_fileno", "stdin_nonblock", "stdout_nonblock", "register"):
            with self.subTest(failure=failure):
                self.owner.reset_mock()
                self.owner.process.stdin.fileno.side_effect = None
                self.owner.process.stdout.fileno.side_effect = None
                self.set_blocking.side_effect = None
                self.selector = FakeSelector()
                self.selector_factory.return_value = self.selector
                error = OSError("SYNTHETIC_PRIVATE_FD")
                if failure == "stdin_fileno":
                    self.owner.process.stdin.fileno.side_effect = error
                elif failure == "stdout_fileno":
                    self.owner.process.stdout.fileno.side_effect = error
                elif failure == "stdin_nonblock":
                    self.set_blocking.side_effect = error
                elif failure == "stdout_nonblock":
                    self.set_blocking.side_effect = [None, error]
                else:
                    self.selector.register = Mock(side_effect=error)
                self.assert_code("rpc_io_failed", self.create)
                self.assertTrue(self.selector.closed)
                self.owner.close.assert_called_once_with()

    def test_constructor_cleanup_failure_overrides_setup_failure(self):
        self.set_blocking.side_effect = OSError("SYNTHETIC_PRIVATE")
        original = ProcessError("probe_cleanup_failed")
        self.owner.close.side_effect = original
        with self.assertRaises(ProcessError) as raised:
            self.create()
        self.assertIs(raised.exception, original)
        self.assertTrue(self.selector.closed)

    def test_constructor_selector_cleanup_failure_is_distinct_and_refuses_later_writes(self):
        for exception in (OSError, RuntimeError):
            with self.subTest(exception=exception.__name__):
                self.owner.reset_mock()
                self.selector = FakeSelector()
                self.selector_factory.return_value = self.selector
                self.set_blocking.side_effect = OSError("SYNTHETIC_SETUP_ERROR")
                original = exception("SYNTHETIC_SELECTOR_CLOSE_ERROR")
                session = rpc.RpcProcess.__new__(rpc.RpcProcess)
                with patch.object(self.selector, "close", side_effect=original) as close:
                    first = self.assert_code("rpc_cleanup_failed", session.__init__, ["synthetic"], {}, "unused")
                    self.assertIs(first.__cause__, original)
                    self.assertNotEqual(str(first), "rpc_io_failed")
                    self.assert_code("rpc_closed", self.send, session)
                    second = self.assert_code("rpc_cleanup_failed", session.close)
                    self.assertIs(second, first)
                    close.assert_called_once_with()
                self.owner.close.assert_called_once_with()
                self.os_write.assert_not_called()

    def test_unknown_initialization_exceptions_clean_owned_resources_and_propagate(self):
        for stage in ("selector", "fileno", "nonblock", "register"):
            for exception in (RuntimeError, KeyboardInterrupt):
                with self.subTest(stage=stage, exception=exception.__name__):
                    original = exception("SYNTHETIC_SETUP_BUG")
                    self.owner.reset_mock()
                    self.selector = FakeSelector()
                    self.selector_factory.return_value = self.selector
                    self.selector_factory.side_effect = None
                    self.owner.process.stdin.fileno.side_effect = None
                    self.set_blocking.side_effect = None
                    if stage == "selector":
                        self.selector_factory.side_effect = original
                    elif stage == "fileno":
                        self.owner.process.stdin.fileno.side_effect = original
                    elif stage == "nonblock":
                        self.set_blocking.side_effect = original
                    else:
                        self.selector.register = Mock(side_effect=original)
                    with self.assertRaises(exception) as raised:
                        self.create()
                    self.assertIs(raised.exception, original)
                    self.owner.close.assert_called_once_with()
                    if stage != "selector":
                        self.assertTrue(self.selector.closed)

    def test_unknown_owner_initialization_failure_propagates_before_fd_setup(self):
        original = RuntimeError("SYNTHETIC_OWNER_SETUP_BUG")
        self.factory.side_effect = original
        with self.assertRaises(RuntimeError) as raised:
            self.create()
        self.assertIs(raised.exception, original)
        self.owner.close.assert_not_called()
        self.selector_factory.assert_not_called()

    def test_invalid_budget_rejects_without_spawning(self):
        for maximum in (-1, 1.5, True):
            self.assert_code("rpc_io_failed", self.create, maximum)
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
