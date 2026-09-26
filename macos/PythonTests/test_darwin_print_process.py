"""Real same-bundle print processes; no Claude installation, login or model call."""

import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

if sys.platform != "darwin":
    raise RuntimeError("Run these tests with the explicit bundled macOS Python.")

import cc_providers
from cc_providers import darwin_print as transport, darwin_process as owned
from owner_process_support import OwnerProcessCase


CHILD_SCRIPT = r"""
import os
from pathlib import Path
import signal
import sys
import time
mode = sys.argv[1]
work = Path.cwd()
def fallback(signum, frame):
    (work / ("fallback-" + str(os.getpid()))).write_text("synthetic timeout")
    os._exit(91)
signal.signal(signal.SIGALRM, fallback)
signal.alarm(20)
def write_all(data, fd=1):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]
if mode in ("group", "early"):
    ready_r, ready_w = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(ready_r)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.alarm(20)
        alive = os.open(work / "alive.fifo", os.O_WRONLY)
        write_all(b"alive\n", alive)
        write_all(b"ready\n", ready_w)
        os.close(ready_w)
        while True:
            signal.pause()
    os.close(ready_w)
    assert os.read(ready_r, 6) == b"ready\n"
    os.close(ready_r)
    write_all(b"ready\n")
    if mode == "early":
        os._exit(0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        signal.pause()
elif mode == "backpressure":
    write_all(b"o" * (2 * 1024 * 1024) + b"\n")
    write_all(b"e" * (2 * 1024 * 1024), 2)
    data = sys.stdin.buffer.read()
    write_all(str(len(data)).encode("ascii") + b"\n")
elif mode == "stderr":
    write_all(b"PRIVATE_NOTICE" * 65536, 2)
    sys.stdin.buffer.read()
elif mode == "invalid":
    write_all(b"PRIVATE\xff\n")
elif mode == "failure":
    sys.stdin.buffer.read()
    write_all(b"partial\n")
    sys.exit(7)
elif mode == "closed-output":
    write_all(b"ready\n")
    os.close(1)
    os.close(2)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        signal.pause()
elif mode == "echo":
    data = sys.stdin.buffer.read()
    write_all(data)
else:
    raise RuntimeError("unknown synthetic mode")
"""


class TestDarwinPrintProcess(OwnerProcessCase):
    bundle_modules = (cc_providers, transport, owned)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".cc-print-process-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve() / "synthetic home \u4e2d # %"
        self.home.mkdir()
        self.owners = []
        self.events = []
        self.lines = []
        self.started = time.monotonic()
        self.serial = 0
        self.factory = owned.OwnedProcess
        patcher = patch.object(transport, "OwnedProcess", side_effect=self.create_owner)
        patcher.start()
        self.addCleanup(patcher.stop)

    def create_owner(self, *args, **kwargs):
        owner = self.factory(*args, **kwargs)
        self.addCleanup(owner.close)
        descriptors = [stream.fileno() for stream in
                       (owner.process.stdin, owner.process.stdout, owner.process.stderr)]
        self.owners.append((owner, descriptors))
        bridge = owner.bridge
        def signal_group(pid, signum):
            self.events.append(signum)
            return bridge.cc_cli_signal_group(pid, signum)
        owner.bridge = SimpleNamespace(
            cc_cli_has_exited=bridge.cc_cli_has_exited, cc_cli_signal_group=signal_group)
        wait = owner.process.wait
        def wait_after_signals(*args, **kwargs):
            self.events.append("wait")
            return wait(*args, **kwargs)
        for patcher in (
                patch.object(owner.process, "poll", side_effect=AssertionError("Must not poll")),
                patch.object(owner.process, "communicate", side_effect=AssertionError("Must not communicate")),
                patch.object(owner.process, "wait", side_effect=wait_after_signals)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return owner

    def run_io(self, mode, data=b"", **kwargs):
        self.serial += 1
        self.directory = self.home / ("case-" + str(self.serial))
        self.directory.mkdir()
        if mode in ("group", "early"):
            os.mkfifo(self.directory / "alive.fifo", 0o600)
            self.alive_fd = os.open(self.directory / "alive.fifo", os.O_RDONLY | os.O_NONBLOCK)
            self.addCleanup(os.close, self.alive_fd)
        return transport.stream_output(
            [sys.executable, "-I", "-B", "-c", CHILD_SCRIPT, mode],
            {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "TMPDIR": str(self.home)},
            self.directory, data, kwargs.pop("on_line", self.lines.append),
            cancel_event=kwargs.pop("cancel_event", None), timeout=kwargs.pop("timeout", 10),
            **kwargs)

    def assert_closed(self):
        self.assertEqual(len(self.owners), 1)
        owner, descriptors = self.owners[0]
        self.assertTrue(owner.closed)
        self.assertTrue(owner.finished)
        self.assertIsNotNone(owner.process.returncode)
        self.assertEqual(self.events, [signal.SIGTERM, signal.SIGKILL, "wait"])
        for stream in (owner.process.stdin, owner.process.stdout, owner.process.stderr):
            self.assertTrue(stream.closed)
        for fd in descriptors:
            self.assert_fd_closed(fd)
        self.assertFalse(list(self.directory.glob("fallback-*")))
        self.assertLess(time.monotonic() - self.started, 15)

    def assert_descendant_gone(self):
        self.assertEqual(os.read(self.alive_fd, 64), b"alive\n")
        deadline = time.monotonic() + 3
        while True:
            try:
                remaining = os.read(self.alive_fd, 1)
            except BlockingIOError:
                self.assertLess(time.monotonic(), deadline, "Descendant still holds the unique FIFO.")
                time.sleep(0.01)
                continue
            self.assertEqual(remaining, b"")
            break
        self.assert_closed()

    def test_real_utf8_input_eof_and_final_line_without_newline(self):
        submitted = []
        self.run_io("echo", "\u4e2d\n\nfinal".encode("utf-8"), on_write=lambda: submitted.append(True))
        self.assertEqual(self.lines, ["\u4e2d", "", "final"])
        self.assertEqual(submitted, [True])
        self.assert_closed()

    def test_real_bidirectional_backpressure_drains_stdout_and_stderr(self):
        self.run_io("backpressure", b"x" * (3 * 1024 * 1024))
        self.assertEqual(self.lines, ["o" * (2 * 1024 * 1024), str(3 * 1024 * 1024)])
        self.assert_closed()

    def test_real_stderr_limit_is_bounded_and_not_exposed_as_output(self):
        with self.assertRaisesRegex(owned.ProcessError, "^probe_output_limit$"):
            self.run_io("stderr", max_bytes=128 * 1024)
        self.assertEqual(self.lines, [])
        self.assert_closed()

    def test_real_invalid_utf8_is_not_admitted(self):
        with self.assertRaisesRegex(owned.ProcessError, "^probe_invalid_utf8$"):
            self.run_io("invalid")
        self.assertEqual(self.lines, [])
        self.assert_closed()

    def test_real_nonzero_exit_preserves_partial_output_but_fails(self):
        with self.assertRaisesRegex(owned.ProcessError, "^probe_failed$"):
            self.run_io("failure", b"request")
        self.assertEqual(self.lines, ["partial"])
        self.assertEqual(self.owners[0][0].process.returncode, 7)
        self.assert_closed()

    def test_real_cancellation_closes_term_resistant_descendant_and_all_pipes(self):
        cancel = threading.Event()
        def ready(line):
            self.lines.append(line)
            cancel.set()
        with self.assertRaisesRegex(owned.ProcessError, "^probe_cancelled$"):
            self.run_io("group", on_line=ready, cancel_event=cancel)
        self.assertEqual(self.lines, ["ready"])
        self.assert_descendant_gone()

    def test_real_early_leader_exit_drains_and_closes_inherited_pipes(self):
        self.run_io("early")
        self.assertEqual(self.lines, ["ready"])
        self.assertEqual(self.owners[0][0].process.returncode, 0)
        self.assert_descendant_gone()

    def test_real_output_eof_does_not_mistake_a_live_process_for_success(self):
        with self.assertRaisesRegex(owned.ProcessError, "^probe_timeout$"):
            self.run_io("closed-output", timeout=4)
        self.assertEqual(self.lines, ["ready"])
        self.assert_closed()
