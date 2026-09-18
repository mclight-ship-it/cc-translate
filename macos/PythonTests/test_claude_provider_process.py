"""Real bundled Claude-provider wiring against an owned synthetic executable only."""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import threading

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
raw = sys.stdin.buffer.read()
assert raw.endswith(b"\n")
message = json.loads(raw)
assert message["type"] == "user" and message["message"]["role"] == "user"
content = message["message"]["content"]
assert content[0] == {"type": "text", "text": os.environ.get("SYNTHETIC_EXPECTED_TEXT", "translate this")}
record = {"input_bytes": len(raw), "model": [arg for arg in args if arg.startswith("--model=")]}
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
