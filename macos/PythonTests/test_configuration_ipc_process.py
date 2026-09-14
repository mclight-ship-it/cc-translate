"""Real same-bundle helper configuration IPC, with test-only fsync barriers."""

import json
import os
from pathlib import Path
import plistlib
import select
import sys
import tempfile
import unittest

if sys.platform != "darwin":
    raise RuntimeError("Configuration IPC process tests require bundled Darwin Python.")

import cc_config_store
import cc_macos
from cc_macos import configuration, config_owner, protocol, server
from cc_macos.config_owner import ConfigInUseError, MacConfigOwner
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


class TestConfigurationIPCProcess(OwnerProcessCase):
    bundle_modules = (cc_config_store, cc_macos, configuration, config_owner, protocol, server)
    owner_script = HELPER_SCRIPT

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with (cls.contents / "Info.plist").open("rb") as stream:
            cls.identity = plistlib.load(stream)["CFBundleIdentifier"]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".configuration-ipc-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve() / "synthetic home \u4e2d # %"
        self.home.mkdir()
        self.directory = macos_user_paths(self.home, self.identity).application_support
        self.path = self.directory / "config.json"
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

    def test_default_diagnostic_connection_does_not_create_application_support(self):
        self.mode = "diagnostic"
        process = self.spawn()
        ready = self.hello(process)
        self.assertEqual(ready["payload"]["capabilities"], ["fixture", "runtime_probe"])
        self.assertIs(ready["payload"]["fixture"], True)
        self.send_message(process, "fixture", "request", operation="fixture", text="synthetic", delay_ms=0)
        self.assertEqual(self.terminal(process, "fixture")["type"], "completed")
        self.send_message(process, "load", "request", operation="config_load")
        self.assertEqual(self.terminal(process, "load")["payload"], {"code": "unsupported_operation"})
        self.finish_helper(process)
        self.assertFalse(self.directory.exists())

    def test_missing_save_load_shutdown_and_reopen_use_actual_bundle_identity(self):
        process = self.spawn()
        ready = self.hello(process)
        self.assertEqual(ready["payload"]["capabilities"], ["config_load", "config_save"])
        self.assertIs(ready["payload"]["fixture"], False)
        self.send_message(process, "missing", "request", operation="config_load")
        self.assertEqual(self.terminal(process, "missing")["payload"]["config"]["font_size"], 12)
        self.assertFalse(self.path.exists())
        raw = {"font_size": "16", "future": {"kept": ["\u4e2d", False, 3]}}
        self.send_message(process, "save", "request", operation="config_save", config=raw)
        self.assertEqual(self.terminal(process, "save")["payload"], {"saved": True})
        self.assertEqual(json.loads(self.path.read_bytes()), raw)
        self.send_message(process, "read", "request", operation="config_load")
        result = self.terminal(process, "read")["payload"]["config"]
        self.assertEqual(result["font_size"], 16)
        self.assertEqual(result["future"], raw["future"])
        self.send_message(process, "stop", "shutdown")
        self.assertEqual(self.terminal(process, "stop")["type"], "completed")
        # The shutdown terminal is sent only after relinquishing the side-file lock.
        with MacConfigOwner(self.home, self.identity) as successor:
            self.assertEqual(successor.load()["future"], raw["future"])
        self.finish_helper(process)
        reopened = self.spawn()
        self.assertEqual(self.hello(reopened)["type"], "ready")
        self.send_message(reopened, "read", "request", operation="config_load")
        self.assertEqual(self.terminal(reopened, "read")["payload"]["config"]["future"], raw["future"])
        self.finish_helper(reopened)
        self.assertEqual(set(path.name for path in self.directory.iterdir()), {"config.json", "config.json.lock"})

    def test_bad_or_unrepresentable_disk_data_returns_fixed_error_without_migration(self):
        process = self.spawn()
        self.hello(process)
        for index, original in enumerate((b"{", b"\xff", b"[]", b'{"x":1,"x":2}',
                                          b'{"font_size":"bad"}', b'{"x":Infinity}',
                                          ('{"x":"' + "x" * protocol.MAX_CONFIG_BYTES + '"}').encode())):
            with self.subTest(case=index):
                self.path.write_bytes(original)
                id_ = "read" + str(index)
                self.send_message(process, id_, "request", operation="config_load")
                self.assertEqual(self.terminal(process, id_)["payload"], {"code": "invalid_config"})
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])
        self.finish_helper(process)

    def test_two_live_helpers_compete_and_exit_allows_a_new_owner(self):
        first, second = self.spawn(), self.spawn()
        self.assertEqual(self.hello(first)["type"], "ready")
        failed = self.hello(second)
        self.assertEqual(failed["type"], "failed")
        self.assertEqual(failed["payload"], {"code": "config_in_use"})
        self.finish_helper(second, code=2)
        self.finish_helper(first)
        third = self.spawn()
        self.assertEqual(self.hello(third)["type"], "ready")
        self.finish_helper(third)

    def test_initialization_symlink_loop_returns_fixed_error_without_stderr_path(self):
        library = self.home / "Library"
        library.symlink_to("Library", target_is_directory=True)
        try:
            process = self.spawn()
            failed = self.hello(process)
            self.assertEqual(failed["type"], "failed")
            self.assertEqual(failed["payload"], {"code": "config_unavailable"})
            self.finish_helper(process, code=2)
            self.assertTrue(library.is_symlink())
            self.assertEqual(os.readlink(library), "Library")
        finally:
            library.unlink()
        self.assertFalse(self.directory.exists())

    def test_cancel_at_fsynced_temp_keeps_write_but_cancels_queued_request(self):
        process = self.start_blocked_write()
        try:
            self.send_message(process, "queued", "request", operation="config_save", config={"future": "never"})
            self.assertEqual(self.receive(process)["type"], "accepted")
            self.send_message(process, "cancel", "cancel", request_id="write")
            self.assertEqual(self.terminal(process, "cancel")["payload"], {"cancel_requested": False})
            self.send_message(process, "cancel-queued", "cancel", request_id="queued")
            self.assertEqual(self.terminal(process, "cancel-queued")["payload"], {"cancel_requested": True})
            with self.assertRaises(ConfigInUseError):
                MacConfigOwner(self.home, self.identity)
        finally:
            self.release()
        self.assertEqual(self.terminal(process, "write")["payload"], {"saved": True})
        self.send_message(process, "late-cancel", "cancel", request_id="write")
        self.assertEqual(self.terminal(process, "late-cancel")["payload"], {"cancel_requested": False})
        self.finish_helper(process)
        self.assertEqual(json.loads(self.path.read_bytes()), {"future": "committed"})
        queued = [value["type"] for pid, value in self.events if pid == process.pid and value["id"] == "queued"]
        self.assertEqual(queued, ["accepted", "cancelled"])

    def test_eof_and_shutdown_do_not_release_owner_before_fsynced_write_finishes(self):
        for action in ("eof", "shutdown"):
            with self.subTest(action=action):
                if self.path.exists():
                    self.path.unlink()
                process = self.start_blocked_write()
                try:
                    self.send_message(process, "queued", "request", operation="config_save", config={"future": "never"})
                    self.assertEqual(self.receive(process)["type"], "accepted")
                    if action == "eof":
                        process.stdin.close()
                        process.stdin = None
                    else:
                        self.send_message(process, "stop", "shutdown")
                    cancelled = self.terminal(process, "queued")
                    self.assertEqual(cancelled["type"], "cancelled")
                    self.assertIsNone(process.poll())
                    self.assertFalse(self.path.exists())
                    with self.assertRaises(ConfigInUseError):
                        MacConfigOwner(self.home, self.identity)
                finally:
                    self.release()
                self.finish_helper(process)
                with MacConfigOwner(self.home, self.identity) as successor:
                    self.assertEqual(successor.load()["future"], "committed")
                self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])

    def test_duplicate_id_during_write_is_fatal_without_replaying_or_early_unlock(self):
        process = self.start_blocked_write()
        try:
            self.send_message(process, "write", "request", operation="config_save", config={"future": "duplicate"})
            failed = self.terminal(process, "protocol")
            self.assertEqual(failed["payload"], {"code": "duplicate_id"})
            self.assertIsNone(process.poll())
            with self.assertRaises(ConfigInUseError):
                MacConfigOwner(self.home, self.identity)
        finally:
            self.release()
        self.finish_helper(process, code=2, errors=b"cc_macos:duplicate_id\n")
        self.assertEqual(json.loads(self.path.read_bytes()), {"future": "committed"})

    def test_invalid_and_oversize_request_payloads_are_rejected_before_started_or_write(self):
        process = self.spawn()
        self.hello(process)
        payloads = (
            {"operation": "config_load", "path": str(self.path)},
            {"operation": "config_save", "config": []},
            {"operation": "config_save", "config": {"x": "x" * protocol.MAX_CONFIG_BYTES}},
            {"operation": "config_save", "config": {"font_size": "bad"}},
            {"operation": "config_save", "config": {"x": protocol.MAX_CONFIG_NUMBER + 1}},
            {"operation": "config_save", "config": {}, "fixture": True},
        )
        for index, payload in enumerate(payloads):
            id_ = "invalid" + str(index)
            self.send_message(process, id_, "request", **payload)
            failed = self.terminal(process, id_)
            self.assertEqual(failed["type"], "failed")
            self.assertEqual(failed["seq"], 0)
            self.assertFalse(self.path.exists())
        self.finish_helper(process)

    def test_malformed_business_frames_keep_original_global_strict_limits(self):
        frames = (
            (b'{"v":1,"id":"x","type":"request","payload":{"operation":"config_save","config":{"x":1,"x":2}}}\n',
             "duplicate_key"),
            (b'{"v":1,"id":"x","type":"request","payload":{"operation":"config_save","config":{"x":"\\ud800"}}}\n',
             "invalid_unicode"),
            (b'{"x":"' + b"x" * protocol.MAX_FRAME_BYTES + b'"}\n', "frame_too_large"),
        )
        for raw, code in frames:
            with self.subTest(code=code):
                process = self.spawn()
                self.hello(process)
                try:
                    process.stdin.write(raw)
                    process.stdin.flush()
                except BrokenPipeError:
                    self.assertEqual(code, "frame_too_large")
                self.assertEqual(self.terminal(process, "protocol")["payload"], {"code": code})
                self.finish_helper(process, code=2, errors=("cc_macos:" + code + "\n").encode())
                self.assertFalse(self.path.exists())

    def test_lost_stdout_after_started_write_exits_with_committed_file_not_rollback(self):
        process = self.start_blocked_write()
        process.stdout.close()
        process.stdout = None
        try:
            with self.assertRaises(ConfigInUseError):
                MacConfigOwner(self.home, self.identity)
        finally:
            self.release()
        # Keep stdin open: the helper must observe its pipe failure without waiting for EOF.
        self.assertEqual(process.wait(timeout=8), 2)
        self.finish_helper(process, code=2, errors=b"cc_macos:pipe_error\n")
        self.assertEqual(json.loads(self.path.read_bytes()), {"future": "committed"})
        with MacConfigOwner(self.home, self.identity):
            pass


if __name__ == "__main__":
    unittest.main()
