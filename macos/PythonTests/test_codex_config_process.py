"""Mandatory Mac-only process tests, executed by the real bundled Python."""

import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from cc_macos.config_fixture import create_cli
import cc_macos
from cc_providers.codex_config import CODEX_CONFIG_OVERRIDES, CodexConfigError, read_native_config
from cc_providers.codex_config_darwin import _load_supervision


if sys.platform != "darwin":
    raise RuntimeError("This suite requires bundled macOS Python; do not substitute a host run.")


class TestOwnedNativeConfig(unittest.TestCase):
    def test_bridge_is_real_and_correct_abi(self):
        self.assertEqual(_load_supervision().cc_process_support_abi(), 1)

    def test_native_config_with_term_resistant_descendant(self):
        self.exercise("child")

    def test_early_leader_exit_keeps_group_owned_until_cleanup(self):
        self.exercise("early")

    def test_deadline_cleans_silent_cli_and_descendants(self):
        self.exercise("timeout", "config_probe_timeout")

    def test_unbounded_output_is_rejected_and_group_cleaned(self):
        self.exercise("flood", "config_probe_output_limit")

    def test_cancellation_cleans_owned_group_before_return(self):
        self.exercise("timeout", "config_probe_cancelled", cancel=True)

    def test_native_error_detail_does_not_escape(self):
        self.exercise("error", "config_invalid")

    def test_malformed_json_is_rejected(self):
        self.exercise("malformed", "config_probe_protocol")

    def test_helper_eof_cancels_actual_config_group_before_exit(self):
        script = """
import sys
sys.path.insert(0, sys.argv[1])
from cc_macos import config_fixture
original = config_fixture.create_cli
config_fixture.create_cli = lambda directory, mode="normal": original(directory, "timeout")
from cc_macos.server import Server
raise SystemExit(Server(sys.stdin.buffer, sys.stdout.buffer, sys.stderr).run())
"""
        with tempfile.TemporaryDirectory(prefix="cc-config-eof-") as directory:
            root = Path(directory)
            core = Path(cc_macos.__file__).resolve().parent.parent
            helper = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", script, str(core)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin", "HOME": directory, "TMPDIR": directory},
            )
            try:
                for identifier, kind, payload in (
                        ("h", "hello", {}), ("r", "request", {"operation": "runtime_probe"})):
                    helper.stdin.write((json.dumps({
                        "v": 1, "id": identifier, "type": kind, "payload": payload,
                    }) + "\n").encode("utf-8"))
                helper.stdin.flush()
                deadline = time.monotonic() + 5
                children = []
                while time.monotonic() < deadline:
                    children = list(root.rglob("child.json"))
                    if children and children[0].stat().st_size:
                        break
                    time.sleep(0.02)
                self.assertEqual(len(children), 1, "Synthetic native config child did not start.")
                child = json.loads(children[0].read_text())
                leader = json.loads(children[0].with_name("root.json").read_text())
                helper.stdin.close()
                helper.stdin = None
                output, errors = helper.communicate(timeout=5)
                self.assertEqual((helper.returncode, errors), (0, b""))
                events = [json.loads(line) for line in output.splitlines()]
                self.assertTrue(any(event["id"] == "r" and event["type"] == "cancelled" for event in events))
                self.assert_gone(leader["pid"])
                self.assert_gone(child["pid"])
            finally:
                if helper.poll() is None:
                    helper.kill()
                    helper.wait(timeout=3)
                for stream in (helper.stdin, helper.stdout, helper.stderr):
                    if stream is not None:
                        stream.close()

    def exercise(self, mode, expected_error=None, cancel=False):
        with tempfile.TemporaryDirectory(prefix="cc-config-owned-") as directory:
            root = Path(directory) / "synthetic work \u4e2d"
            command, environment = create_cli(root, mode)
            unrelated = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time; time.sleep(45)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            before_threads = {thread.ident for thread in threading.enumerate()}
            event = threading.Event()
            stop_waiter = threading.Event()
            def cancel_when_ready():
                while not stop_waiter.wait(0.01):
                    if (root / "child.json").is_file() and (root / "child.json").stat().st_size:
                        event.set()
                        return
            waiter = threading.Thread(target=cancel_when_ready) if cancel else None
            if waiter is not None:
                waiter.start()
            try:
                if expected_error:
                    with self.assertRaises(CodexConfigError) as error:
                        read_native_config(command, environment, str(root), cancel_event=event)
                    self.assertEqual(str(error.exception), expected_error)
                    self.assertNotIn("SYNTHETIC_PRIVATE", str(error.exception))
                else:
                    result = read_native_config(command, environment, str(root))
                    self.assertEqual(result["config"]["model_provider"], "synthetic")
                    self.assertEqual(result["config"]["opaque_layer"], "preserved")
                    self.assertEqual(result["layers"], ["synthetic"])
                    self.assertEqual(json.loads((root / "methods.json").read_text()),
                                     ["initialize", "config/read"])
                info = json.loads((root / "root.json").read_text(encoding="utf-8"))
                self.assertEqual(info["pid"], info["group"])
                self.assertEqual(info["pid"], info["session"])
                self.assertEqual(Path(info["cwd"]).resolve(), root.resolve())
                if expected_error is None:
                    self.assertEqual(info["request_cwd"], str(root.absolute()))
                self.assertEqual(info["args"][:2], ["app-server", "--strict-config"])
                self.assertEqual(info["args"][2:], [
                    part for override in CODEX_CONFIG_OVERRIDES for part in ("-c", override)])
                self.assert_gone(info["pid"])
                if mode in {"child", "early", "timeout", "flood"}:
                    child = json.loads((root / "child.json").read_text(encoding="utf-8"))
                    self.assertEqual(child["group"], info["pid"])
                    self.assert_gone(child["pid"])
                self.assertIsNone(unrelated.poll(), "A sibling must survive owned cleanup.")
                if waiter is not None:
                    waiter.join(timeout=2)
                    self.assertFalse(waiter.is_alive())
                self.assertEqual({thread.ident for thread in threading.enumerate()}, before_threads)
            finally:
                stop_waiter.set()
                if waiter is not None:
                    waiter.join(timeout=2)
                    self.assertFalse(waiter.is_alive())
                unrelated.terminate()
                unrelated.wait(timeout=3)

    def assert_gone(self, pid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError as error:
                if error.errno == errno.ESRCH:
                    return
                raise
            time.sleep(0.02)
        self.fail("Synthetic owned process is still alive or unreaped.")
