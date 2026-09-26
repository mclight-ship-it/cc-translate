"""Portable one-shot I/O contracts; real Darwin ownership is tested separately."""

from collections import deque
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cc_providers import darwin_print as transport, darwin_process as owned
from cc_providers.darwin_process import ProcessError


class TestDarwinPrintContract(unittest.TestCase):
    def setUp(self):
        self.owner = Mock(finished=False)
        self.streams = [Mock() for _ in range(3)]
        for fd, stream in enumerate(self.streams, 10):
            stream.fileno.return_value = fd
            stream.closed = False
            stream.close.side_effect = lambda s=stream: setattr(s, "closed", True)
        self.stdin, self.stdout, self.stderr = self.streams
        self.owner.process.stdin, self.owner.process.stdout, self.owner.process.stderr = self.streams
        self.owner.process.returncode = 0
        self.chunks = {11: deque([b"answer\n", b""]), 12: deque([b"notice", b""])}
        self.keys = {}
        self.calls = []
        self.lines = []
        self.written = bytearray()
        self.selector = Mock()
        self.selector.register.side_effect = self.register
        self.selector.unregister.side_effect = self.keys.pop
        self.selector.get_map.side_effect = lambda: self.keys
        self.selector.select.side_effect = lambda timeout: [(k, k.events) for k in tuple(self.keys.values())]
        self.selector.close.side_effect = self.keys.clear
        self.owner.has_exited.side_effect = lambda: self.stdin.closed and not any(self.chunks.values())
        self.owner.terminate.side_effect = lambda: setattr(self.owner, "finished", True)
        self.patchers = [
            patch.object(transport, "OwnedProcess", return_value=self.owner),
            patch.object(transport.selectors, "DefaultSelector", return_value=self.selector),
            patch.object(transport.os, "set_blocking"),
            patch.object(transport.os, "read", side_effect=self.read),
            patch.object(transport.os, "write", side_effect=self.write),
        ]
        self.factory, self.selector_factory, self.blocking, self.os_read, self.os_write = (
            patcher.start() for patcher in self.patchers)
        for patcher in self.patchers:
            self.addCleanup(patcher.stop)

    def register(self, stream, events):
        if stream in self.keys:
            raise KeyError("already registered")
        self.keys[stream] = SimpleNamespace(fileobj=stream, events=events)

    def read(self, fd, size):
        self.calls.append(("read", fd))
        if not self.chunks[fd]:
            raise BlockingIOError()
        data = self.chunks[fd].popleft()
        if isinstance(data, BaseException):
            raise data
        if len(data) > size:
            self.chunks[fd].appendleft(data[size:])
        return data[:size]

    def write(self, fd, data):
        self.assertEqual(fd, 10)
        self.calls.append(("write", fd))
        count = min(2, len(data))
        self.written.extend(data[:count])
        return count

    def run_io(self, data=b"request\n", **kwargs):
        return transport.stream_output(
            ["synthetic"], {"HOME": "synthetic"}, "unused", data,
            kwargs.pop("on_line", self.lines.append),
            cancel_event=kwargs.pop("cancel_event", None), timeout=kwargs.pop("timeout", 2),
            **kwargs)

    def assert_code(self, code, **kwargs):
        with self.assertRaises(ProcessError) as raised:
            self.run_io(**kwargs)
        self.assertEqual(str(raised.exception), code)

    def assert_closed(self):
        self.selector.close.assert_called_once_with()
        self.owner.close.assert_called_once_with()

    def test_owned_process_preserves_existing_probe_and_rpc_pipe_choices(self):
        with patch.object(owned, "load_supervision"), patch.object(owned.subprocess, "Popen") as popen:
            for options, stdin, stderr in (
                    ({}, subprocess.DEVNULL, subprocess.PIPE),
                    ({"rpc": True}, subprocess.PIPE, subprocess.DEVNULL),
                    ({"input_pipe": True}, subprocess.PIPE, subprocess.PIPE)):
                owned.OwnedProcess(["synthetic"], {}, "unused", **options)
                self.assertEqual(popen.call_args.kwargs["stdin"], stdin)
                self.assertEqual(popen.call_args.kwargs["stderr"], stderr)
                self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
                self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_partial_writes_drain_both_outputs_and_close_stdin_once(self):
        on_write = Mock(side_effect=lambda: self.calls.append(("submitted", None)))
        self.run_io(on_write=on_write)
        self.factory.assert_called_once_with(
            ["synthetic"], {"HOME": "synthetic"}, "unused", input_pipe=True)
        self.assertEqual(self.blocking.call_args_list, [((11, False),), ((12, False),), ((10, False),)])
        self.assertEqual(self.calls[:4], [("read", 11), ("read", 12), ("write", 10), ("submitted", None)])
        self.assertEqual(self.written, b"request\n")
        self.assertEqual(self.lines, ["answer"])
        on_write.assert_called_once_with()
        self.stdin.close.assert_called_once_with()
        self.owner.terminate.assert_called_once_with()
        self.assert_closed()

    def test_explicit_input_budget_does_not_expand_the_stdout_stderr_budget(self):
        data = b"x" * 100
        self.run_io(data=data, max_bytes=16, max_input_bytes=100)
        self.assertEqual(self.written, data)
        self.assertEqual(self.lines, ["answer"])
        self.assert_closed()

    def test_explicit_input_budget_rejects_oversize_before_allocating(self):
        self.assert_code("probe_input_limit", data=b"request", max_input_bytes=6)
        self.factory.assert_not_called()
        self.selector_factory.assert_not_called()

    def test_invalid_explicit_input_budget_does_not_allocate(self):
        for value in (False, 0, -1, 1.5, "100"):
            self.assert_code("probe_invalid_input", max_input_bytes=value)
        self.factory.assert_not_called()

    def test_small_output_budget_is_still_enforced_with_large_input_allowance(self):
        self.chunks[12] = deque([b"PRIVATE" * 100, b""])
        self.assert_code("probe_output_limit", data=b"x" * 100,
                         max_bytes=16, max_input_bytes=100)
        self.assertEqual(self.lines, ["answer"])
        self.os_write.assert_not_called()
        self.assert_closed()

    def test_utf8_split_across_reads_blank_lines_and_final_line_without_lf(self):
        self.chunks[11] = deque([b"\xe4", b"\xb8", b"\xad\n\nlast", b""])
        self.run_io()
        self.assertEqual(self.lines, ["\u4e2d", "", "last"])
        self.assert_closed()

    def test_injected_owner_and_split_idle_utf8_are_used_without_spawning(self):
        self.chunks[11] = deque([b"\xb8\xad\nlast", b""])
        self.run_io(owned_process=self.owner, initial_stdout=b"init\n\xe4", initial_received=12)
        self.factory.assert_not_called()
        self.assertEqual(self.lines, ["init", "\u4e2d", "last"])
        self.assertEqual(self.written, b"request\n")
        self.assert_closed()

    def test_injected_owner_is_closed_even_for_rejected_input_and_cancellation(self):
        cancel = threading.Event()
        cancel.set()
        for kwargs, code in (({"cancel_event": cancel}, "probe_cancelled"),
                             ({"timeout": 0}, "probe_timeout"),
                             ({"data": "invalid"}, "probe_invalid_input"),
                             ({"initial_stdout": b"x", "initial_received": 0}, "probe_invalid_input"),
                             ({"initial_received": 20, "max_bytes": 16}, "probe_output_limit")):
            with self.subTest(code=code):
                self.owner.close.reset_mock()
                self.assert_code(code, owned_process=self.owner, **kwargs)
                self.owner.close.assert_called_once()
        self.factory.assert_not_called()
        self.selector_factory.assert_not_called()

    def test_injected_owner_selector_creation_failure_still_cleans(self):
        self.selector_factory.side_effect = OSError("synthetic")
        with self.assertRaises(OSError):
            self.run_io(owned_process=self.owner)
        self.factory.assert_not_called()
        self.owner.close.assert_called_once()

    def test_warm_output_bytes_include_stderr_in_foreground_limit(self):
        self.assert_code("probe_output_limit", owned_process=self.owner,
                         initial_received=12, max_bytes=16)
        self.os_write.assert_not_called()
        self.factory.assert_not_called()
        self.assert_closed()

    def test_injected_owner_late_nonzero_exit_does_not_succeed(self):
        self.owner.process.returncode = 7
        self.assert_code("probe_failed", owned_process=self.owner, initial_stdout=b"init\n",
                         initial_received=5)
        self.assertEqual(self.lines, ["init", "answer"])
        self.factory.assert_not_called()
        self.assert_closed()

    def test_injected_owner_cleanup_failure_overrides_success(self):
        self.owner.close.side_effect = ProcessError("probe_cleanup_failed")
        self.assert_code("probe_cleanup_failed", owned_process=self.owner)
        self.assertEqual(self.lines, ["answer"])
        self.assert_closed()

    def test_empty_input_sends_eof_without_submission(self):
        on_write = Mock()
        self.run_io(data=b"", on_write=on_write)
        self.stdin.close.assert_called_once_with()
        self.os_write.assert_not_called()
        on_write.assert_not_called()
        self.assertEqual(self.lines, ["answer"])

    def test_cancel_before_creation_does_not_allocate_a_process_or_selector(self):
        cancel = threading.Event()
        cancel.set()
        self.assert_code("probe_cancelled", cancel_event=cancel)
        self.factory.assert_not_called()
        self.selector_factory.assert_not_called()

    def test_cancel_after_first_partial_write_preserves_submission_and_cleans(self):
        cancel = threading.Event()
        on_write = Mock(side_effect=cancel.set)
        self.assert_code("probe_cancelled", cancel_event=cancel, on_write=on_write)
        self.assertEqual(self.written, b"re")
        on_write.assert_called_once_with()
        self.assert_closed()

    def test_expired_timeout_does_not_start_a_process(self):
        self.assert_code("probe_timeout", timeout=0)
        self.factory.assert_not_called()

    def test_callback_timeout_does_not_write_input_after_the_deadline(self):
        clock = [0]
        with patch.object(transport.time, "monotonic", side_effect=lambda: clock[0]):
            self.assert_code("probe_timeout", timeout=1,
                             on_line=lambda line: clock.__setitem__(0, 2))
        self.os_write.assert_not_called()
        self.assert_closed()

    def test_late_constructor_is_closed_without_writing(self):
        clock = [0]
        def create(*args, **kwargs):
            clock[0] = 2
            return self.owner
        self.factory.side_effect = create
        with patch.object(transport.time, "monotonic", side_effect=lambda: clock[0]):
            self.assert_code("probe_timeout", timeout=1)
        self.os_write.assert_not_called()
        self.assert_closed()

    def test_output_budget_includes_stderr_without_exposing_it(self):
        self.chunks[11] = deque([b"ok\n", b""])
        self.chunks[12] = deque([b"PRIVATE_NOTICE" * 100, b""])
        self.assert_code("probe_output_limit", data=b"x", max_bytes=16)
        self.assertEqual(self.lines, ["ok"])
        self.os_write.assert_not_called()
        self.assert_closed()

    def test_invalid_utf8_never_becomes_replacement_characters(self):
        self.chunks[11] = deque([b"PRIVATE\xff\n"])
        self.assert_code("probe_invalid_utf8")
        self.assertEqual(self.lines, [])
        self.assert_closed()

    def test_nonzero_exit_is_failure_even_after_output(self):
        self.owner.process.returncode = 7
        self.assert_code("probe_failed")
        self.assertEqual(self.lines, ["answer"])
        self.assert_closed()

    def test_leader_exit_before_all_input_is_not_success(self):
        self.owner.has_exited.side_effect = None
        self.owner.has_exited.return_value = True
        self.assert_code("probe_failed")
        self.os_write.assert_not_called()
        self.owner.terminate.assert_called_once_with()
        self.assert_closed()

    def test_broken_pipe_before_a_positive_write_does_not_mark_submission(self):
        on_write = Mock()
        self.os_write.side_effect = BrokenPipeError("PRIVATE")
        self.assert_code("probe_failed", on_write=on_write)
        on_write.assert_not_called()
        self.assert_closed()

    def test_blocked_and_interrupted_io_retry_only_untransferred_bytes(self):
        self.chunks[11].appendleft(InterruptedError())
        self.chunks[12].appendleft(BlockingIOError())
        count = [0]
        def write(fd, data):
            count[0] += 1
            if count[0] <= 2:
                raise (BlockingIOError() if count[0] == 1 else InterruptedError())
            return self.write(fd, data)
        self.os_write.side_effect = write
        on_write = Mock()
        self.run_io(on_write=on_write)
        self.assertEqual(self.written, b"request\n")
        on_write.assert_called_once_with()

    def test_callback_failure_propagates_and_closes_resources(self):
        error = RuntimeError("synthetic callback failure")
        with self.assertRaises(RuntimeError) as raised:
            self.run_io(on_line=Mock(side_effect=error))
        self.assertIs(raised.exception, error)
        self.assert_closed()

    def test_selector_cleanup_failure_does_not_skip_owner_cleanup(self):
        self.selector.close.side_effect = ValueError("PRIVATE")
        self.assert_code("probe_cleanup_failed")
        self.owner.close.assert_called_once_with()

    def test_initialization_failure_still_closes_allocated_owner(self):
        self.blocking.side_effect = OSError("PRIVATE")
        self.assert_code("probe_failed")
        self.assert_closed()

    def test_invalid_input_and_limits_fail_before_process_creation(self):
        for kwargs in ({"data": "not bytes"}, {"max_bytes": True}, {"max_bytes": 0},
                       {"timeout": float("nan")}, {"timeout": float("inf")},
                       {"timeout": True}, {"on_line": None}, {"on_write": 4}):
            with self.subTest(kwargs=kwargs):
                self.assert_code("probe_invalid_input", **kwargs)
        self.assert_code("probe_input_limit", max_bytes=2)
        self.factory.assert_not_called()


class IdleProgrammingError(RuntimeError):
    pass


class TestIdlePrintContract(unittest.TestCase):
    def setUp(self):
        self.owner = Mock()
        self.owner.has_exited.return_value = False
        self.stdin, self.stdout, self.stderr = (Mock() for _ in range(3))
        for fd, stream in enumerate((self.stdin, self.stdout, self.stderr), 10):
            stream.fileno.return_value = fd
        self.owner.process.stdin = self.stdin
        self.owner.process.stdout = self.stdout
        self.owner.process.stderr = self.stderr
        self.chunks = {11: deque(), 12: deque()}
        self.keys = {}
        self.read_seen = threading.Event()
        self.wake = threading.Event()
        self.selector = Mock()
        self.selector.register.side_effect = lambda stream, mask: self.keys.update(
            {stream.fileno(): SimpleNamespace(fileobj=stream)})
        self.selector.select.side_effect = self.select
        self.closing, self.cancel = threading.Event(), threading.Event()
        self.errors = Mock()
        self.patchers = [
            patch.object(transport, "OwnedProcess", return_value=self.owner),
            patch.object(transport.selectors, "DefaultSelector", return_value=self.selector),
            patch.object(transport.os, "set_blocking"),
            patch.object(transport.os, "read", side_effect=self.read),
            patch.object(transport.os, "write", side_effect=AssertionError("Idle write")),
        ]
        self.factory, self.selector_factory, self.blocking, self.os_read, self.os_write = (
            patcher.start() for patcher in self.patchers)
        for patcher in self.patchers:
            self.addCleanup(patcher.stop)

    def select(self, timeout):
        self.wake.wait(timeout)
        self.wake.clear()
        return [(self.keys[fd], 1) for fd, chunks in self.chunks.items() if chunks]

    def read(self, fd, size):
        value = self.chunks[fd].popleft()
        if isinstance(value, BaseException):
            raise value
        self.read_seen.set()
        return value

    def start(self, **kwargs):
        warm = transport.IdlePrintProcess(
            ["synthetic", "--print"], {"HOME": "synthetic"}, "unused",
            closing=self.closing, cancel_event=self.cancel, on_error=self.errors, **kwargs)
        self.addCleanup(lambda: self.finish(warm))
        return warm

    def finish(self, warm):
        try:
            warm.close()
        except (ProcessError, IdleProgrammingError):
            pass
        self.assertFalse(warm._thread.is_alive())

    def test_idle_drains_both_outputs_without_stdin_write_close_or_turn(self):
        self.chunks[11].extend([b'{"type":"system","text":"\xe4'])
        self.chunks[12].extend([b"private warning"])
        self.wake.set()
        warm = self.start()
        self.assertTrue(self.read_seen.wait(2))
        owner, prefix, count = warm.take()
        self.assertIs(owner, self.owner)
        self.assertTrue(prefix.startswith(b'{"type":"system"'))
        self.assertGreaterEqual(count, len(prefix))
        self.assertNotIn(10, self.keys)
        self.os_write.assert_not_called()
        self.stdin.close.assert_not_called()
        self.owner.close.assert_not_called()
        self.selector.close.assert_called_once()
        self.assertIsNone(warm.take())
        owner.close()

    def test_idle_expiry_closes_without_a_foreground_request(self):
        warm = self.start(idle_seconds=0)
        warm._thread.join(2)
        self.assertFalse(warm.available)
        self.assertIsNone(warm.take())
        self.owner.close.assert_called_once()
        self.assertEqual(warm.error, "probe_timeout")
        self.os_write.assert_not_called()

    def test_idle_cancel_and_shutdown_each_drain_child(self):
        for event in (self.cancel, self.closing):
            with self.subTest(event=event):
                self.owner.close.reset_mock()
                self.selector.close.reset_mock()
                warm = self.start()
                event.set()
                self.wake.set()
                warm._thread.join(2)
                self.assertFalse(warm._thread.is_alive())
                self.assertIsNone(warm.take())
                self.owner.close.assert_called_once()
                self.selector.close.assert_called_once()
                event.clear()
        self.os_write.assert_not_called()

    def test_claim_detaches_warm_cancellation_without_closing_transferred_owner(self):
        warm = self.start()
        owner, _prefix, _received = warm.take()
        self.cancel.set()
        warm.close()
        self.owner.close.assert_not_called()
        owner.close()

    def test_dead_or_closed_pipe_is_not_a_reusable_process(self):
        self.chunks[11].append(b"")
        self.wake.set()
        warm = self.start()
        warm._thread.join(2)
        self.assertEqual(warm.error, "probe_failed")
        self.assertIsNone(warm.take())
        self.owner.close.assert_called_once()

    def test_idle_output_is_bounded_and_discarded(self):
        self.chunks[12].append(b"private" * 100)
        self.wake.set()
        warm = self.start(max_bytes=16)
        warm._thread.join(2)
        self.assertEqual(warm.error, "probe_output_limit")
        self.assertIsNone(warm.take())
        self.assertEqual(warm.stdout, b"")
        self.owner.close.assert_called_once()

    def test_background_cleanup_failure_is_reported_and_take_fails_closed(self):
        self.owner.close.side_effect = ProcessError("probe_cleanup_failed")
        warm = self.start(idle_seconds=0)
        warm._thread.join(2)
        self.errors.assert_called_once_with("probe_cleanup_failed")
        with self.assertRaisesRegex(ProcessError, "probe_cleanup_failed"):
            warm.take()
        self.owner.close.assert_called_once()

    def test_selector_cleanup_failure_closes_owner_instead_of_handing_off(self):
        self.selector.close.side_effect = ValueError("private")
        warm = self.start()
        with self.assertRaisesRegex(ProcessError, "probe_cleanup_failed"):
            warm.take()
        self.owner.close.assert_called_once()
        self.errors.assert_called_once_with("probe_cleanup_failed")

    def test_setup_failure_closes_both_selector_and_child(self):
        self.blocking.side_effect = OSError("private")
        with self.assertRaises(OSError):
            self.start()
        self.owner.close.assert_called_once()
        self.selector.close.assert_called_once()
        self.os_write.assert_not_called()

    def test_unexpected_worker_error_escapes_and_take_does_not_hide_it(self):
        error = IdleProgrammingError("synthetic reader bug")
        self.chunks[11].append(error)
        self.wake.set()
        with patch.object(threading, "excepthook") as hook:
            warm = self.start()
            warm._thread.join(2)
            self.assertFalse(warm._thread.is_alive())
            self.assertIs(hook.call_args.args[0].exc_value, error)
        with self.assertRaises(IdleProgrammingError) as raised:
            warm.take()
        self.assertIs(raised.exception, error)
        self.assertIsNone(warm.owner)
        self.assertEqual(warm.error, "")
        self.owner.close.assert_called_once()
        self.selector.close.assert_called_once()
        self.errors.assert_not_called()

    def test_known_idle_read_error_closes_and_marks_child_unavailable(self):
        self.chunks[11].append(OSError("synthetic read failure"))
        self.wake.set()
        warm = self.start()
        warm._thread.join(2)
        self.assertEqual(warm.error, "probe_failed")
        self.assertIsNone(warm.take())
        self.owner.close.assert_called_once()
        self.selector.close.assert_called_once()

    def test_known_idle_selector_error_closes_and_marks_child_unavailable(self):
        self.selector.select.side_effect = ValueError("synthetic closed selector")
        warm = self.start()
        warm._thread.join(2)
        self.assertEqual(warm.error, "probe_failed")
        self.assertIsNone(warm.take())
        self.owner.close.assert_called_once()
        self.selector.close.assert_called_once()

    def test_unexpected_check_error_during_claim_aborts_handoff_and_closes_owner(self):
        entered, release = threading.Event(), threading.Event()
        error = IdleProgrammingError("synthetic handoff bug")
        def select(timeout):
            entered.set()
            self.assertTrue(release.wait(3))
            return []
        self.selector.select.side_effect = select
        with patch.object(threading, "excepthook") as hook:
            warm = self.start()
            try:
                self.assertTrue(entered.wait(2))
                self.owner.has_exited.side_effect = error
                warm._claimed = True
                warm._stop.set()
            finally:
                release.set()
            with self.assertRaises(IdleProgrammingError) as raised:
                warm.take()
            self.assertIs(raised.exception, error)
            self.assertIs(hook.call_args.args[0].exc_value, error)
        self.owner.close.assert_called_once()
        self.selector.close.assert_called_once()
        self.assertIsNone(warm.owner)
        self.errors.assert_not_called()

    def test_unexpected_selector_cleanup_error_still_closes_claimed_owner(self):
        error = IdleProgrammingError("synthetic selector bug")
        self.selector.close.side_effect = error
        with patch.object(threading, "excepthook") as hook:
            warm = self.start()
            with self.assertRaises(IdleProgrammingError) as raised:
                warm.take()
            self.assertIs(raised.exception, error)
            self.assertIs(hook.call_args.args[0].exc_value, error)
        self.owner.close.assert_called_once()
        self.assertIsNone(warm.owner)

    def test_unexpected_owner_cleanup_error_propagates_instead_of_cold_fallback(self):
        error = IdleProgrammingError("synthetic owner bug")
        self.owner.close.side_effect = error
        with patch.object(threading, "excepthook") as hook:
            warm = self.start(idle_seconds=0)
            with self.assertRaises(IdleProgrammingError) as raised:
                warm.take()
            self.assertIs(raised.exception, error)
            self.assertIs(hook.call_args.args[0].exc_value, error)
        self.owner.close.assert_called_once()
        self.selector.close.assert_called_once()

    def test_unexpected_constructor_error_cleans_and_reraises(self):
        error = IdleProgrammingError("synthetic setup bug")
        self.blocking.side_effect = error
        with self.assertRaises(IdleProgrammingError) as raised:
            self.start()
        self.assertIs(raised.exception, error)
        self.owner.close.assert_called_once()
        self.selector.close.assert_called_once()
