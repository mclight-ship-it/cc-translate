"""Same-bundle helper -> actual synthetic localImage turn -> explicit private-copy cleanup."""

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import unittest

if sys.platform != "darwin":
    raise RuntimeError("Image IPC tests require bundled Darwin Python; not a host substitute.")

from cc_macos import image, image_fixture, translation_fixture
from cc_storage import macos_user_paths
from state_ipc_process_support import StateIPCProcessCase
import test_translation_ipc_process as translation_process


IMAGE_PROBES = r"""
if mode == "image-drain":
    from cc_macos.image_fixture import PNG_BYTES
    from cc_providers.codex_darwin import DarwinCodexProvider
    actual_execute = DarwinCodexProvider._execute
    def observed_execute(self, request, on_delta, cancel):
        path = Path(request.image_paths[0])
        if request.task != "image" or path.read_bytes() != PNG_BYTES:
            raise RuntimeError("synthetic private image missing before provider")
        try:
            return actual_execute(self, request, on_delta, cancel)
        finally:
            if path.read_bytes() != PNG_BYTES:
                raise RuntimeError("synthetic private image removed before provider drain")
            with (Path(home) / "image-drain.jsonl").open("a", encoding="utf-8") as output:
                output.write('{"retained_through_provider_return":true}\n')
    DarwinCodexProvider._execute = observed_execute
if mode == "image-cleanup-failure":
    from cc_macos.image import OwnedPNG, ImageError
    actual_close = OwnedPNG.close
    def failed_close(self):
        if (Path(home) / "fail-image-cleanup").exists():
            raise ImageError("image_cleanup_failed")
        return actual_close(self)
    OwnedPNG.close = failed_close
"""
HELPER_SCRIPT = translation_process.HELPER_SCRIPT.replace(
    "from cc_macos.server import main", IMAGE_PROBES + "\nfrom cc_macos.server import main")


class TestImageTranslationIPCProcess(StateIPCProcessCase):
    bundle_modules = translation_process.TestTranslationIPCProcess.bundle_modules + (image, image_fixture)
    owner_script = HELPER_SCRIPT
    process_arguments = translation_process.TestTranslationIPCProcess.process_arguments
    terminal = translation_process.TestTranslationIPCProcess.terminal
    rpc = translation_process.TestTranslationIPCProcess.rpc
    turns = translation_process.TestTranslationIPCProcess.turns
    no_cli = translation_process.TestTranslationIPCProcess.no_cli
    configure = translation_process.TestTranslationIPCProcess.configure
    start = translation_process.TestTranslationIPCProcess.start
    stop = translation_process.TestTranslationIPCProcess.stop
    request = translation_process.TestTranslationIPCProcess.request
    until_delta = translation_process.TestTranslationIPCProcess.until_delta
    history = translation_process.TestTranslationIPCProcess.history
    assert_completed = translation_process.TestTranslationIPCProcess.assert_completed
    assert_native_gone = translation_process.TestTranslationIPCProcess.assert_native_gone

    def setUp(self):
        super().setUp()
        self.barriers = self.home
        self.fixture_index = self.configuration_index = 0
        self.prepare()

    def prepare(self, scenario="normal", *, direction="auto"):
        self.fixture_index += 1
        self.fixture = image_fixture.prepare(
            self.barriers / ("image-" + str(self.fixture_index)), self.identity, scenario, direction=direction)
        self.home, self.root = Path(self.fixture["home"]), Path(self.fixture["root"])
        self.directory = macos_user_paths(self.home, self.identity).application_support
        self.path, self.history_path = self.directory / "config.json", self.directory / "history.json"
        self.source = Path(self.fixture["request"]["image_path"])
        self.mode = "translation"

    def copies(self):
        return list((self.directory / "NativeWorkspace").glob(".cc-image-*"))

    def assert_private_copy(self):
        copies = self.copies()
        self.assertEqual(len(copies), 1)
        path = copies[0] / "region.png"
        self.assertNotEqual(path, self.source)
        self.assertEqual(path.read_bytes(), image_fixture.PNG_BYTES)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_explicit_png_turns_have_null_history_no_cache_and_reopen_never_replays(self):
        process = self.start()
        self.no_cli()
        self.assertEqual(self.copies(), [])
        for id_ in ("first", "repeat"):
            self.request(process, id_)
            self.assert_completed(self.terminal(process, id_))
            self.assertEqual(self.copies(), [])
        rows = self.history(process)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["input"] is None and row["kind"] == "ocr" for row in rows))
        self.assertTrue(all(not row["is_dict"] and not row["is_code"] for row in rows))
        self.assertEqual(len(self.turns()), 2)
        self.assertEqual(json.loads((self.root / "image-read.jsonl").read_text().splitlines()[0]),
                         {"verified": True, "task": "image"})
        translation_fixture.verify(self.root)
        for private in (str(self.source), self.fixture["request"]["image_sha256"]):
            self.assertNotIn(private, json.dumps(rows))
            self.assertNotIn(private, json.dumps(self.rpc()))
            self.assertNotIn(private, json.dumps(self.events))
        self.stop(process)
        self.assert_native_gone()
        reopened = self.start(configure=False)
        self.assertEqual(self.history(reopened), rows)
        self.assertEqual(len(self.turns()), 2)
        self.assertEqual(self.copies(), [])
        self.stop(reopened)

    def test_invalid_missing_changed_oversized_and_special_sources_never_start_cli(self):
        process = self.start()
        invalid = self.root / "invalid.png"
        invalid.write_bytes(b"not PNG")
        link = self.root / "link.png"
        link.symlink_to(self.source)
        fifo = self.root / "pipe.png"
        os.mkfifo(fifo, 0o600)
        oversized = self.root / "oversized.png"
        with oversized.open("wb") as stream:
            stream.truncate(image.MAX_IMAGE_BYTES + 1)
        cases = (
            ({"text": "not an image"}, "invalid_image_translation"),
            ({"image_path": str(self.root / "missing.png")}, "image_unavailable"),
            ({"image_path": str(self.root)}, "image_unavailable"),
            ({"image_path": str(link)}, "image_unavailable"),
            ({"image_path": str(fifo)}, "image_unavailable"),
            ({"image_path": str(oversized)}, "image_too_large"),
            ({"image_bytes": image.MAX_IMAGE_BYTES + 1}, "image_too_large"),
            ({"image_bytes": 1}, "image_changed"),
            ({"image_sha256": "0" * 64}, "image_changed"),
            ({"image_path": str(invalid), "image_bytes": invalid.stat().st_size,
              "image_sha256": hashlib.sha256(invalid.read_bytes()).hexdigest()}, "image_unavailable"),
        )
        for index, (changes, code) in enumerate(cases):
            self.request(process, str(index), **changes)
            result = self.terminal(process, str(index))
            self.assertEqual(result["type"], "failed")
            self.assertEqual(result["payload"]["code"], code)
            self.assertIs(result["payload"].get("submitted", False), False)
            self.assertEqual(self.copies(), [])
        self.no_cli()
        self.assertFalse(self.history_path.exists())
        self.assertEqual(self.source.read_bytes(), image_fixture.PNG_BYTES)
        self.stop(process)

    def test_partial_cancel_eof_and_shutdown_retain_copy_through_owned_provider_drain(self):
        for exit_kind in ("cancel", "eof", "shutdown"):
            with self.subTest(exit_kind=exit_kind):
                self.prepare("gated")
                self.mode = "image-drain"
                process = self.start()
                self.request(process)
                self.until_delta(process)
                self.assert_private_copy()
                self.source.write_bytes(b"caller source changed; provider must retain its own PNG")
                if exit_kind == "cancel":
                    self.send_message(process, "cancel", "cancel", request_id="translation")
                    self.assertEqual(self.terminal(process, "cancel")["payload"], {"cancel_requested": True})
                elif exit_kind == "eof":
                    process.stdin.close()
                    process.stdin = None
                else:
                    self.send_message(process, "shutdown", "shutdown")
                event = self.terminal(process, "translation")
                self.assertEqual((event["type"], event["payload"]), ("cancelled", {"submitted": True}))
                self.assertEqual(self.copies(), [])
                self.assertEqual(json.loads((self.home / "image-drain.jsonl").read_text()),
                                 {"retained_through_provider_return": True})
                self.assert_native_gone(descendant=True)
                if exit_kind == "cancel":
                    self.stop(process)
                else:
                    if exit_kind == "shutdown":
                        self.assertEqual(self.terminal(process, "shutdown")["payload"], {})
                    self.finish_helper(process)
                self.assertFalse(self.history_path.exists())
                self.assertTrue(self.source.exists())

    def test_prestart_cancel_has_no_image_read_copy_or_cli_activity(self):
        self.mode = "prestart"
        process = self.start()
        self.request(process)
        self.assertEqual(self.receive(process)["type"], "accepted")
        self.barrier()
        try:
            self.source.unlink()
            self.send_message(process, "cancel", "cancel", request_id="translation")
            self.assertEqual(self.receive(process)["payload"], {})
            self.assertEqual(self.receive(process)["payload"], {"cancel_requested": True})
        finally:
            self.release()
        self.stop(process)
        self.no_cli()
        self.assertEqual(self.copies(), [])
        self.assertEqual([event["type"] for pid, event in self.events
                          if pid == process.pid and event["id"] == "translation"], ["accepted", "cancelled"])

    def test_waiting_image_cancel_removes_only_its_copy_without_submitting_or_interrupting_active_image(self):
        self.prepare("gated")
        process = self.start()
        self.request(process)
        self.until_delta(process)
        self.request(process, "waiting")
        self.assertEqual(self.receive(process)["type"], "accepted")
        self.assertEqual(self.receive(process)["type"], "started")
        deadline = time.monotonic() + 3
        while len(self.copies()) != 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.copies()), 2)
        self.send_message(process, "cancel-waiting", "cancel", request_id="waiting")
        terminals = {}
        while len(terminals) < 2:
            event = self.receive(process)
            if event["type"] in ("completed", "cancelled"):
                terminals[event["id"]] = event
        self.assertEqual(terminals["waiting"]["payload"], {"submitted": False})
        self.assertEqual(terminals["cancel-waiting"]["payload"], {"cancel_requested": True})
        self.assertEqual(len(self.turns()), 1)
        self.assert_private_copy()
        self.send_message(process, "cancel-active", "cancel", request_id="translation")
        terminals = {}
        while len(terminals) < 2:
            event = self.receive(process)
            if event["type"] in ("completed", "cancelled"):
                terminals[event["id"]] = event
        self.assertEqual(terminals["translation"]["payload"], {"submitted": True})
        self.assertEqual(self.copies(), [])
        self.assert_native_gone(descendant=True)
        self.stop(process)

    def test_live_history_optout_fixed_direction_and_source_changes_never_route_to_ocr_text(self):
        self.prepare("gated", direction="to_ja")
        process = self.start()
        self.request(process)
        self.until_delta(process)
        self.assert_private_copy()
        self.configure(process, history_enabled=False, direction="to_de", codex_model="later-choice",
                       summary_enabled=True, local_dictionary_enabled=True)
        self.source.unlink()
        Path(self.fixture["gate"]).touch()
        self.assert_completed(self.terminal(process, "translation"), history="disabled")
        self.assertEqual(self.copies(), [])
        self.assertFalse(self.history_path.exists())
        turn = self.turns()[0]["request"]["params"]
        self.assertEqual(turn["model"], self.fixture["expected"]["model"])
        self.assertEqual(turn["input"][0]["text"], self.fixture["expected"]["prompt"])
        self.assertEqual(turn["input"][1], {"type": "localImage", "path": image_fixture.VERIFIED_IMAGE_PATH})
        self.stop(process)
        self.assert_native_gone(descendant=True)

    def test_image_output_and_stream_budgets_clean_private_copy_without_history(self):
        for scenario in ("output-limit", "envelope-limit"):
            with self.subTest(scenario=scenario):
                self.prepare(scenario)
                process = self.start()
                self.request(process)
                result = self.terminal(process, "translation")
                self.assertEqual((result["type"], result["payload"]),
                                 ("failed", {"code": "translation_output_limit", "submitted": True}))
                self.assertEqual(self.copies(), [])
                self.assertFalse(self.history_path.exists())
                self.assertEqual(len(self.turns()), 1)
                self.stop(process)
                self.assert_native_gone()

    def test_cleanup_failure_reports_submitted_state_and_shutdown_retries_only_owned_copy(self):
        for submitted in (False, True):
            with self.subTest(submitted=submitted):
                self.prepare()
                self.mode = "image-cleanup-failure"
                marker = self.home / "fail-image-cleanup"
                marker.touch()
                process = self.start()
                changes = {} if submitted else {"image_sha256": "0" * 64}
                self.request(process, **changes)
                result = self.terminal(process, "translation")
                self.assertEqual((result["type"], result["payload"]),
                                 ("failed", {"code": "image_cleanup_failed", "submitted": submitted}))
                self.assert_private_copy()
                self.assertFalse(self.history_path.exists())
                marker.unlink()
                self.stop(process)
                self.assertEqual(self.copies(), [])
                self.assertEqual(self.source.read_bytes(), image_fixture.PNG_BYTES)
                if submitted:
                    self.assert_native_gone()
                else:
                    self.no_cli()

    def test_history_commit_starts_only_after_image_removal_and_cancel_is_then_rejected(self):
        self.mode = "committing"
        process = self.start()
        self.request(process)
        self.until_delta(process)
        self.barrier()
        try:
            self.assertEqual(self.copies(), [])
            self.assertFalse(self.history_path.exists())
            self.send_message(process, "cancel", "cancel", request_id="translation")
            self.assertEqual(self.terminal(process, "cancel")["payload"], {"cancel_requested": False})
            self.send_message(process, "shutdown", "shutdown")
        finally:
            self.release()
        self.assert_completed(self.terminal(process, "translation"))
        self.assertEqual(self.terminal(process, "shutdown")["payload"], {})
        self.finish_helper(process)
        self.assertIsNone(json.loads(self.history_path.read_bytes())[0]["input"])
        self.assert_native_gone()


if __name__ == "__main__":
    unittest.main()
