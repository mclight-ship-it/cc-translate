"""Shared same-bundle config/history helper harness; all homes and barriers are synthetic."""

import os
from pathlib import Path
import plistlib
import select
import tempfile

import cc_config_store
import cc_history
import cc_macos
from cc_macos import configuration, config_owner, history, protocol, server
from cc_storage import macos_user_paths
from owner_process_support import OwnerProcessCase


HELPER_SCRIPT = r"""
import os
from pathlib import Path
import signal
import sys
sys.path.insert(0, sys.argv[1])
home, identity, mode = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
signal.alarm(15)
if mode == "worker-start-failure":
    import threading
    def fail_start(thread):
        if thread.name != "cc-macos-configuration":
            raise AssertionError("unexpected synthetic worker")
        raise RuntimeError("synthetic thread start failure")
    threading.Thread.start = fail_start
if mode == "barrier":
    import cc_storage
    actual_fsync = cc_storage.os.fsync
    first = True
    def controlled_fsync(fd):
        global first
        actual_fsync(fd)
        if first:
            first = False
            with (home / "entered.fifo").open("wb", buffering=0) as entered:
                entered.write(b"x")
            with (home / "release.fifo").open("rb", buffering=0) as release:
                if release.read(1) != b"x":
                    raise RuntimeError("synthetic release missing")
    cc_storage.os.fsync = controlled_fsync
from cc_macos.server import main
arguments = [] if mode == "diagnostic" else ["--config-home", str(home), "--application-id", identity]
raise SystemExit(main(arguments))
"""


class StateIPCProcessCase(OwnerProcessCase):
    bundle_modules = (cc_config_store, cc_history, cc_macos, configuration, config_owner, history, protocol, server)
    owner_script = HELPER_SCRIPT

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with (cls.contents / "Info.plist").open("rb") as stream:
            cls.identity = plistlib.load(stream)["CFBundleIdentifier"]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".state-ipc-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve() / "synthetic home \u4e2d # %"
        self.home.mkdir()
        self.directory = macos_user_paths(self.home, self.identity).application_support
        self.path = self.directory / "config.json"
        self.history_path = self.directory / "history.json"
        self.mode = "configuration"
        self.sequences = {}
        self.events = []
        for name in ("entered.fifo", "release.fifo"):
            os.mkfifo(self.home / name, 0o600)
        self.entered_fd = os.open(self.home / "entered.fifo", os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        self.release_fd = os.open(self.home / "release.fifo", os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        self.addCleanup(os.close, self.entered_fd)
        self.addCleanup(os.close, self.release_fd)

    def process_arguments(self):
        return (self.core, self.home, self.identity, self.mode)

    def send_message(self, process, id_, type_, **payload):
        process.stdin.write(protocol.encode_frame({"v": 1, "id": id_, "type": type_, "payload": payload}))
        process.stdin.flush()

    def record(self, process, value):
        key = process.pid, value["id"]
        self.assertEqual(value["seq"], self.sequences.get(key, -1) + 1)
        self.sequences[key] = value["seq"]
        self.events.append((process.pid, value))
        return value

    def receive(self, process):
        return self.record(process, protocol.decode_frame(
            (self.line(process, limit=protocol.MAX_FRAME_BYTES) + "\n").encode("utf-8")))

    def terminal(self, process, id_):
        for _ in range(20):
            event = self.receive(process)
            if event["id"] == id_ and event["type"] in {"completed", "failed", "cancelled", "ready"}:
                return event
        self.fail("Expected terminal not found in bounded synthetic response sequence.")

    def hello(self, process):
        self.send_message(process, "hello", "hello")
        return self.receive(process)

    def finish_helper(self, process, *, code=0, errors=b""):
        if process.stdin is not None:
            try:
                process.stdin.close()
            except BrokenPipeError:
                self.assertEqual(code, 2)
            finally:
                process.stdin = None
        remaining, diagnostic = process.communicate(timeout=8)
        self.assertEqual(process.wait(timeout=0), code)
        self.assertEqual(diagnostic, errors)
        for raw in (remaining or b"").splitlines():
            self.record(process, protocol.decode_frame(raw + b"\n"))

    def barrier(self):
        self.assertEqual(select.select([self.entered_fd], [], [], 5)[0], [self.entered_fd])
        self.assertEqual(os.read(self.entered_fd, 1), b"x")

    def release(self):
        self.assertEqual(os.write(self.release_fd, b"x"), 1)

    def start_blocked_write(self):
        self.mode = "barrier"
        process = self.spawn()
        self.assertEqual(self.hello(process)["type"], "ready")
        self.send_message(process, "write", "request", operation="config_save", config={"future": "committed"})
        self.assertEqual(self.receive(process)["type"], "accepted")
        self.assertEqual(self.receive(process)["type"], "started")
        self.barrier()
        self.assertFalse(self.path.exists())
        self.assertEqual(len(list(self.directory.glob(".tmp_*.json"))), 1)
        return process
