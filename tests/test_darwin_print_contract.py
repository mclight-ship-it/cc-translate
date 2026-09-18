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
