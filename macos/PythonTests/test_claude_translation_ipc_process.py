"""Actual bundled helper -> Claude provider -> synthetic CLI -> cache/history."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import time

if sys.platform != "darwin":
    raise RuntimeError("Claude helper integration requires the selected bundled macOS Python.")

from cc_macos import translation, image_fixture
from cc_providers import claude_darwin, claude_jsonl
from state_ipc_process_support import StateIPCProcessCase
import test_claude_provider_process as claude_support


HELPER = r"""
import os, signal, sys
sys.path.insert(0, sys.argv[1])
home, identity, command, environment = sys.argv[2:]
signal.alarm(35)
os.environ["CC_TRANSLATE_CLAUDE_ENV"] = environment
from cc_macos.server import main
raise SystemExit(main(["--config-home", home, "--application-id", identity,
                      "--claude-command", command]))
"""


class TestClaudeTranslationIPCProcess(StateIPCProcessCase):
    bundle_modules = StateIPCProcessCase.bundle_modules + (
        translation, claude_darwin, claude_jsonl, image_fixture)
    owner_script = HELPER

    def setUp(self):
        super().setUp()
        script = self.home / "synthetic.py"
        script.write_text(claude_support.SCRIPT, encoding="utf-8")
        self.command = self.home / "synthetic-cli"
        self.command.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable)
                                + " -I -B " + shlex.quote(str(script)) + ' "$@"\n', encoding="utf-8")
        self.command.chmod(0o700)
        self.work = self.directory / "NativeWorkspace"
        self.environment = {"HOME": str(self.home), "TMPDIR": str(self.home),
                            "PATH": "/usr/bin:/bin", "SYNTHETIC_MODE": "text"}

    def process_arguments(self):
        return self.core, self.home, self.identity, self.command, json.dumps(self.environment)

    def start(self, mode="text"):
        self.environment["SYNTHETIC_MODE"] = mode
        process = self.spawn()
        hello = self.hello(process)
        self.assertEqual(hello["type"], "ready")
        self.assertEqual(hello["payload"]["backend"], "native_print")
        self.send_message(process, "config", "request", operation="config_save", config={
            "model_provider": "claude_cli", "claude_model": "claude-only-model",
            "codex_model": "must-not-be-used", "summary_enabled": False,
            "history_enabled": True, "labs_defaults_migrated": True})
        self.assertEqual(self.terminal(process, "config")["type"], "completed")
        return process

    def request(self, process, identifier="translate", **changes):
        payload = dict(operation="translate", text="translate this", app_language="en_US",
                       origin="text", use_cache=True, record_history=True) | changes
        self.send_message(process, identifier, "request", **payload)

    def history(self, process):
        self.send_message(process, "history", "request", operation="history_load", page_size=100, cursor=None)
        return self.terminal(process, "history")["payload"]["entries"]

    def stop(self, process):
        self.send_message(process, "shutdown", "shutdown")
        self.assertEqual(self.terminal(process, "shutdown")["type"], "completed")
        self.finish_helper(process)

    def calls(self):
        return [json.loads(line) for line in (self.work / "calls.jsonl").read_text().splitlines()]

    def test_real_claude_bootstrap_translation_cache_history_and_unavailable_catalog(self):
        process = self.start()
        self.assertFalse((self.work / "calls.jsonl").exists())
        self.request(process, "first")
        result = self.terminal(process, "first")
        self.assertEqual(result["type"], "completed")
        self.assertEqual(result["payload"]["text"], "translated")
        self.assertTrue(result["payload"]["submitted"])
        self.assertEqual(result["payload"]["history"], "recorded")
        self.request(process, "cached")
        cached = self.terminal(process, "cached")["payload"]
        self.assertTrue(cached["cached"])
        self.assertFalse(cached["submitted"])
        self.send_message(process, "catalog", "request", operation="model_catalog")
        self.assertEqual(self.terminal(process, "catalog")["payload"], {"code": "model_catalog_unavailable"})
        entries = self.history(process)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["sig"].startswith("claude_cli|claude-only-model|"))
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("--model=claude-only-model", self.calls()[0])
        self.assertNotIn("--version", self.calls()[0])
        self.stop(process)

    def test_real_claude_ocr_and_all_result_actions_preserve_history_contract(self):
        process = self.start()
        self.request(process, "ocr", origin="ocr")
        self.assertEqual(self.terminal(process, "ocr")["payload"]["kind"], "ocr")
        for action in translation.RESULT_ACTIONS:
            self.send_message(process, action, "request", operation="result_action", action=action,
                              text="translate this", app_language="en_US",
                              target_language="ja" if action == "retranslate" else None)
            result = self.terminal(process, action)
            self.assertEqual(result["type"], "completed")
            self.assertEqual(result["payload"]["history"], "disabled")
        self.assertEqual(len(self.history(process)), 1)
        self.assertEqual(len(self.calls()), 7)
        self.stop(process)

    def test_real_claude_image_roundtrip_cleans_owned_png_before_history_terminal(self):
        self.environment["SYNTHETIC_EXPECTED_TEXT"] = "Translate the attached image while preserving its structure."
        process = self.start("image")
        source = self.home / "captured.png"
        source.write_bytes(image_fixture.PNG_BYTES)
        digest = hashlib.sha256(image_fixture.PNG_BYTES).hexdigest()
        self.send_message(process, "image", "request", operation="translate_image", image_path=str(source),
                          image_bytes=len(image_fixture.PNG_BYTES), image_sha256=digest,
                          app_language="en_US", record_history=True)
        result = self.terminal(process, "image")
        self.assertEqual(result["type"], "completed")
        self.assertEqual(result["payload"]["kind"], "ocr")
        self.assertTrue(result["payload"]["submitted"])
        self.assertEqual(json.loads((self.work / "request.json").read_text())["image_sha256"], digest)
        self.assertFalse(list(self.work.glob(".cc-image-*")))
        self.assertEqual(source.read_bytes(), image_fixture.PNG_BYTES)
        entries = self.history(process)
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0]["input"])
        self.assertTrue(entries[0]["sig"].startswith("claude_cli|claude-only-model|"))
        self.stop(process)

    def test_real_claude_cancel_drains_resistant_group_before_terminal_and_owner_release(self):
        self.work.mkdir(parents=True)
        fifo = self.work / "alive.fifo"
        os.mkfifo(fifo, 0o600)
        fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, fd)
        process = self.start("group")
        started = time.monotonic()
        self.request(process)
        self.assertEqual(self.receive(process)["type"], "accepted")
        self.assertEqual(self.receive(process)["type"], "started")
        self.assertEqual(self.receive(process)["payload"]["text"], "ready")
        self.send_message(process, "cancel", "cancel", request_id="translate")
        terminals = {}
        for _ in range(5):
            event = self.receive(process)
            if event["type"] in ("cancelled", "completed"):
                terminals[event["id"]] = event
            if len(terminals) == 2:
                break
        self.assertEqual(terminals["translate"]["type"], "cancelled")
        self.assertEqual(terminals["translate"]["payload"], {"submitted": True})
        self.assertEqual(terminals["cancel"]["payload"], {"cancel_requested": True})
        self.assertEqual(os.read(fd, 64), b"alive\n")
        self.assertEqual(os.read(fd, 1), b"", "Descendant must release the FIFO before terminal.")
        self.assertLess(time.monotonic() - started, 15)
        self.assertFalse(list(self.work.glob("fallback-*")))
        self.assertEqual(self.history(process), [])
        self.stop(process)
        reopened = self.start()
        self.stop(reopened)
