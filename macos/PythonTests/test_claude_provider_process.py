"""Real bundled Claude-provider wiring against an owned synthetic executable only."""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import threading
import time
from unittest.mock import patch

if sys.platform != "darwin":
    raise RuntimeError("Run with the selected app's isolated bundled macOS Python.")

import cc_providers
from cc_providers import claude_darwin as native, claude_jsonl, darwin_print, darwin_process
from cc_providers.base import ProviderRequest
from cc_macos import image_fixture
from owner_process_support import OwnerProcessCase
import test_darwin_print_process as print_support


SCRIPT = r"""
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
work = Path.cwd()
def fallback(signum, frame):
    (work / ("fallback-" + str(os.getpid()))).write_text("synthetic timeout")
    os._exit(91)
signal.signal(signal.SIGALRM, fallback)
signal.alarm(20)
mode = os.environ["SYNTHETIC_MODE"]
args = sys.argv[1:]
(work / "args.json").write_text(json.dumps(args))
with (work / "calls.jsonl").open("a") as calls:
    calls.write(json.dumps(args) + "\n")
assert args[args.index("--input-format") + 1] == "stream-json"
assert args[args.index("--tools") + 1] == ""
assert "--strict-mcp-config" in args and "--setting-sources=" in args
assert "--bare" not in args and "--version" not in args
if mode == "warm":
    import select
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    # Leave a UTF-8 scalar incomplete across the idle-reader/foreground handoff.
    os.write(1, b'{"type":"system","detail":"\xe4')
    os.write(2, b"synthetic warm startup\n")
    readable, _, _ = select.select([sys.stdin.buffer], [], [], 0.1)
    if not readable:
        (work / ("idle-no-input-" + str(os.getpid()))).write_text("ready")
    first = sys.stdin.buffer.read(1)
    (work / ("input-" + str(os.getpid()))).write_text("byte" if first else "eof")
    raw = first + sys.stdin.buffer.read()
    os.write(1, b'\xb8\xad"}\n')
else:
    raw = sys.stdin.buffer.read()
assert raw.endswith(b"\n")
message = json.loads(raw)
assert message["type"] == "user" and message["message"]["role"] == "user"
content = message["message"]["content"]
assert content[0] == {"type": "text", "text": os.environ.get("SYNTHETIC_EXPECTED_TEXT", "translate this")}
record = {"input_bytes": len(raw), "model": [arg for arg in args if arg.startswith("--model=")],
          "pid": os.getpid()}
if mode == "image":
    source = content[1]["source"]
    assert source["type"] == "base64" and source["media_type"] == "image/png"
    record["image_sha256"] = hashlib.sha256(base64.b64decode(source["data"], validate=True)).hexdigest()
else:
    assert len(content) == 1
(work / "request.json").write_text(json.dumps(record))
def emit(value, newline=True):
    data = (json.dumps(value) + ("\n" if newline else "")).encode()
    while data:
        data = data[os.write(1, data):]
def delta(text):
    emit({"type":"stream_event","event":{"type":"content_block_delta",
          "delta":{"type":"text_delta","text":text}}})
emit({"type":"system","subtype":"init","future_metadata":{"version":"not-a-version-gate"}})
os.write(2, b"PRIVATE synthetic stderr\n")
if mode in ("group", "early"):
    ready_r, ready_w = os.pipe()
    if os.fork() == 0:
        os.close(ready_r)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.alarm(20)
        alive = os.open(work / "alive.fifo", os.O_WRONLY)
        os.write(alive, b"alive\n")
        os.write(ready_w, b"ready\n")
        os.close(ready_w)
        while True:
            signal.pause()
    os.close(ready_w)
    assert os.read(ready_r, 6) == b"ready\n"
    os.close(ready_r)
    delta("ready")
    if mode == "early":
        os._exit(0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        signal.pause()
delta("translated")
if mode == "partial":
    sys.exit(0)
if mode == "tool":
    emit({"type":"stream_event","event":{"type":"content_block_start",
          "content_block":{"type":"tool_use","name":"PRIVATE","input":{}}}})
elif mode == "error":
    emit({"type":"result","subtype":"error_during_execution","is_error":True,"result":"PRIVATE"})
else:
    emit({"type":"assistant","message":{"content":[{"type":"text","text":"translated"}]}})
    emit({"type":"result","subtype":"success","is_error":False,"result":"translated"}, newline=False)
sys.exit(7 if mode == "exit7" else 0)
"""


class TestClaudeProviderProcess(OwnerProcessCase):
    bundle_modules = (cc_providers, native, claude_jsonl, darwin_print, darwin_process, image_fixture)
    setUp = print_support.TestDarwinPrintProcess.setUp
    create_owner = print_support.TestDarwinPrintProcess.create_owner
    assert_closed = print_support.TestDarwinPrintProcess.assert_closed
    assert_descendant_gone = print_support.TestDarwinPrintProcess.assert_descendant_gone

    def provider(self, mode="text"):
        self.directory = self.home / "work"
        self.directory.mkdir()
        script = self.home / "synthetic.py"
        script.write_text(SCRIPT, encoding="utf-8")
        command = self.home / "synthetic-cli"
        command.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable)
                           + " -I -B " + shlex.quote(str(script)) + ' "$@"\n', encoding="utf-8")
        command.chmod(0o700)
        if mode in ("group", "early"):
            os.mkfifo(self.directory / "alive.fifo", 0o600)
            self.alive_fd = os.open(self.directory / "alive.fifo", os.O_RDONLY | os.O_NONBLOCK)
            self.addCleanup(os.close, self.alive_fd)
        provider = native.DarwinClaudeProvider(command, self.directory, environment={
            "HOME": str(self.home), "TMPDIR": str(self.home), "PATH": "/usr/bin:/bin",
            "SYNTHETIC_MODE": mode}, log_error=lambda *_: self.fail("Unexpected provider log"))
        self.addCleanup(provider.shutdown)
        self.assertEqual(self.owners, [])
        self.request = ProviderRequest("text", "future-custom-model", "Translate only.", "translate this",
                                       timeout_seconds=10)
        return provider

    def test_real_cli_arguments_stdin_eof_stream_and_terminal_without_final_newline(self):
        provider = self.provider()
        result = provider.stream(self.request, self.lines.append)
        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(result.text, "translated")
        self.assertEqual(self.lines, ["translated"])
        self.assertTrue(dict(result.metrics)["turn_submitted"])
        record = json.loads((self.directory / "request.json").read_text())
        self.assertEqual(record["model"], ["--model=future-custom-model"])
        self.assertGreater(record["input_bytes"], 0)
        self.assert_closed()

    def test_real_base64_image_larger_than_output_budget_uses_separate_input_budget(self):
        provider = self.provider("image")
        data = (image_fixture.PNG_BYTES[:-12]
                + image_fixture.png_chunk(b"tEXt", b"Comment\0" + b"x" * (6 * 1024 * 1024))
                + image_fixture.PNG_BYTES[-12:])
        image = self.home / "owned.png"
        image.write_bytes(data)
        result = provider.complete(replace(self.request, task="image", image_paths=(str(image),)))
        self.assertTrue(result.ok, result.error_code)
        record = json.loads((self.directory / "request.json").read_text())
        self.assertEqual(record["image_sha256"], hashlib.sha256(data).hexdigest())
        self.assertGreater(record["input_bytes"], native.MAX_OUTPUT_BYTES)
        self.assertEqual(image.read_bytes(), data)
        self.assert_closed()

    def test_real_partial_text_and_exit_zero_without_result_fail(self):
        provider = self.provider("partial")
        result = provider.stream(self.request, self.lines.append)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "provider_protocol_error")
        self.assertEqual(result.text, "")
        self.assertEqual(self.lines, ["translated"])
        self.assertTrue(dict(result.metrics)["turn_submitted"])
        self.assert_closed()

    def test_real_success_result_followed_by_exit_seven_is_not_success(self):
        provider = self.provider("exit7")
        result = provider.complete(self.request)
        self.assertFalse(result.ok)
        self.assertEqual(result.text, "")
        self.assertEqual(result.error_code, "probe_failed")
        self.assertTrue(dict(result.metrics)["turn_submitted"])
        self.assert_closed()

    def test_real_error_result_does_not_return_private_error_as_translation(self):
        provider = self.provider("error")
        result = provider.complete(self.request)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "provider_failed")
        self.assertEqual(result.text, "")
        self.assertEqual(result.error_detail, "")
        self.assert_closed()

    def test_real_tool_record_fails_and_drains_process(self):
        provider = self.provider("tool")
        result = provider.complete(self.request)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "unsafe_tool_event")
        self.assertEqual(result.text, "")
        self.assert_closed()

    def test_real_cancel_releases_resistant_descendant_before_return(self):
        provider = self.provider("group")
        cancel = threading.Event()
        result = provider.stream(self.request, lambda _text: cancel.set(), cancel)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "cancelled")
        self.assertTrue(dict(result.metrics)["turn_submitted"])
        self.assert_descendant_gone()

    def test_real_early_leader_exit_closes_inherited_pipes_and_descendant(self):
        provider = self.provider("early")
        result = provider.complete(self.request)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "provider_protocol_error")
        self.assertTrue(dict(result.metrics)["turn_submitted"])
        self.assert_descendant_gone()

    def test_real_symlink_and_fifo_images_fail_without_starting_cli_or_blocking(self):
        provider = self.provider("image")
        image = self.home / "owned.png"
        image.write_bytes(image_fixture.PNG_BYTES)
        link = self.home / "link.png"
        link.symlink_to(image)
        fifo = self.home / "fifo.png"
        os.mkfifo(fifo, 0o600)
        for path in (link, fifo):
            result = provider.complete(replace(self.request, task="image", image_paths=(str(path),)))
            self.assertFalse(result.ok)
            self.assertFalse(dict(result.metrics)["turn_submitted"])
        self.assertEqual(self.owners, [])
        self.assertFalse((self.directory / "args.json").exists())

    def ready_unused_child(self, provider, cancel_event=None):
        result = provider.warm_up(self.request, cancel_event)
        self.addCleanup(provider.shutdown, require_cleanup=True)
        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(result.text, "")
        self.assertFalse(dict(result.metrics)["turn_submitted"])
        self.assertEqual(len(self.owners), 1)
        warm = provider._warm
        owner = self.owners[0][0]
        marker = self.directory / ("idle-no-input-" + str(owner.process.pid))
        prefix = b'{"type":"system","detail":"\xe4'
        deadline = time.monotonic() + 5
        while (not marker.exists() or marker.read_text() != "ready"
               or bytes(warm.stdout) != prefix
               or warm.received < len(prefix) + len(b"synthetic warm startup\n")):
            self.assertLess(time.monotonic(), deadline, "Synthetic child did not remain input-idle.")
            self.assertFalse(owner.closed)
            time.sleep(0.01)
        self.assertFalse((self.directory / ("input-" + str(owner.process.pid))).exists())
        self.assertFalse((self.directory / "request.json").exists())
        self.assertFalse(owner.process.stdin.closed)
        self.assertIsNone(owner.process.returncode)
        self.assertEqual(self.events, [])
        return warm, owner

    def assert_all_children_closed(self, count):
        self.assertEqual(len(self.owners), count)
        self.assertEqual(self.events, [signal.SIGTERM, signal.SIGKILL, "wait"] * count)
        for owner, descriptors in self.owners:
            self.assertTrue(owner.closed)
            self.assertTrue(owner.finished)
            self.assertIsNotNone(owner.process.returncode)
            for stream in (owner.process.stdin, owner.process.stdout, owner.process.stderr):
                self.assertTrue(stream.closed)
            for fd in descriptors:
                self.assert_fd_closed(fd)
        self.assertFalse(list(self.directory.glob("fallback-*")))
        self.assertLess(time.monotonic() - self.started, 15)

    def test_real_prewarm_sends_neither_stdin_bytes_nor_eof_and_keeps_one_child(self):
        provider = self.provider("warm")
        warm, owner = self.ready_unused_child(provider)
        repeated = provider.warm_up(replace(self.request, user_text="must never be sent"))
        self.assertTrue(repeated.ok, repeated.error_code)
        self.assertFalse(dict(repeated.metrics)["turn_submitted"])
        self.assertEqual(dict(repeated.metrics)["warm_process_hit"], 1)
        self.assertEqual(len(self.owners), 1)
        self.assertIs(provider._warm, warm)
        self.assertFalse((self.directory / ("input-" + str(owner.process.pid))).exists())
        provider.shutdown(require_cleanup=True)
        self.assertFalse(warm._thread.is_alive())
        self.assertFalse((self.directory / "request.json").exists())
        self.assert_closed()

    def test_real_matching_warm_child_is_consumed_once_then_next_request_is_cold(self):
        provider = self.provider("warm")
        warm, first = self.ready_unused_child(provider)
        result = provider.stream(self.request, self.lines.append)
        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(result.text, "translated")
        self.assertEqual(self.lines, ["translated"])
        self.assertTrue(dict(result.metrics)["turn_submitted"])
        self.assertEqual(dict(result.metrics)["warm_process_hit"], 1)
        self.assertEqual(dict(result.metrics)["cold_process_start"], 0)
        self.assertEqual(json.loads((self.directory / "request.json").read_text())["pid"], first.process.pid)
        self.assertFalse(warm._thread.is_alive())
        self.assertIsNone(provider._warm)
        self.assert_closed()

        following = provider.complete(self.request)
        self.assertTrue(following.ok, following.error_code)
        self.assertEqual(dict(following.metrics)["warm_process_hit"], 0)
        self.assertEqual(dict(following.metrics)["cold_process_start"], 1)
        self.assertIsNot(self.owners[1][0], first)
        self.assertEqual(json.loads((self.directory / "request.json").read_text())["pid"],
                         self.owners[1][0].process.pid)
        self.assertEqual(len((self.directory / "calls.jsonl").read_text().splitlines()), 2)
        self.assert_all_children_closed(2)

    def assert_warm_mismatch(self, provider, request):
        warm, first = self.ready_unused_child(provider)
        def create_after_cleanup(*args, **kwargs):
            self.assertTrue(first.closed)
            self.assertTrue(first.finished)
            self.assertFalse(warm._thread.is_alive())
            self.assertEqual(self.events, [signal.SIGTERM, signal.SIGKILL, "wait"])
            return self.create_owner(*args, **kwargs)
        with patch.object(darwin_print, "OwnedProcess", side_effect=create_after_cleanup):
            result = provider.complete(request)
        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(dict(result.metrics)["warm_process_hit"], 0)
        self.assertEqual(dict(result.metrics)["cold_process_start"], 1)
        self.assertFalse((self.directory / ("input-" + str(first.process.pid))).exists())
        args = json.loads((self.directory / "args.json").read_text())
        self.assertEqual(args[args.index("--system-prompt") + 1], request.system_prompt)
        self.assertIn("--model=" + request.model, args)
        self.assert_all_children_closed(2)

    def test_real_warm_model_mismatch_closes_unused_child_before_cold_spawn(self):
        provider = self.provider("warm")
        self.assert_warm_mismatch(provider, replace(self.request, model="different-synthetic-model"))

    def test_real_warm_prompt_mismatch_closes_unused_child_before_cold_spawn(self):
        provider = self.provider("warm")
        self.assert_warm_mismatch(provider, replace(self.request, system_prompt="Different instructions."))

    def test_real_unused_warm_child_expires_without_foreground_or_stdin_submission(self):
        provider = self.provider("warm")
        warm, owner = self.ready_unused_child(provider)
        # Shorten only idle retention after startup; supervision's TERM/KILL grace stays real.
        warm._deadline = time.monotonic() + 0.05
        warm._thread.join(timeout=5)
        self.assertFalse(warm._thread.is_alive())
        self.assertEqual(warm.error, "probe_timeout")
        self.assertFalse(warm.available)
        self.assertFalse((self.directory / ("input-" + str(owner.process.pid))).exists())
        self.assert_closed()

    def test_real_warm_cancellation_drains_unused_child_and_open_stdin(self):
        provider = self.provider("warm")
        cancel = threading.Event()
        warm, owner = self.ready_unused_child(provider, cancel)
        cancel.set()
        warm._thread.join(timeout=5)
        self.assertFalse(warm._thread.is_alive())
        self.assertEqual(warm.error, "probe_cancelled")
        self.assertFalse((self.directory / ("input-" + str(owner.process.pid))).exists())
        self.assert_closed()

    def test_real_shutdown_drains_unused_warm_child_before_returning(self):
        provider = self.provider("warm")
        warm, owner = self.ready_unused_child(provider)
        provider.shutdown(require_cleanup=True)
        self.assertFalse(warm._thread.is_alive())
        self.assertIsNone(provider._warm)
        self.assertFalse((self.directory / ("input-" + str(owner.process.pid))).exists())
        self.assert_closed()
