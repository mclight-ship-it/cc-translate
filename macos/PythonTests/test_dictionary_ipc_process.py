"""Real config-only NDJSON dictionary operations using the app's bundled Darwin Python."""

import os
from pathlib import Path
import sys
import unittest

if sys.platform != "darwin":
    raise RuntimeError("Dictionary IPC process tests require bundled Darwin Python.")

import cc_dictionary_artifact_core
import cc_dictionary_lookup
import cc_dictionary_presentation
from cc_macos import dictionary
from cc_macos.config_owner import MacConfigOwner
from cc_macos.dictionary_probe import create_fixture
from cc_macos.history_owner import MacHistoryOwner
from state_ipc_process_support import StateIPCProcessCase


SCRIPT = r"""
import hashlib, importlib.abc, os, pathlib, signal, sys, urllib.request
sys.path.insert(0, sys.argv[1])
home, identity, mode = pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4]
signal.alarm(15)
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"cc_core", "cc_providers", "cc_rich", "tkinter", "cc_dictionary", "cc_dictionary_artifact"}:
            raise AssertionError("forbidden native dependency: " + fullname)
sys.meta_path.insert(0, Guard())
def no_network(*args, **kwargs): raise AssertionError("Python networking is forbidden")
urllib.request.urlopen = no_network
from cc_dictionary_artifact_core import DictionaryArtifact, DictionaryArtifactManager
from cc_dictionary_lookup import LocalDictionary
from cc_macos import dictionary
data = (home / "source.sqlite3").read_bytes()
pin = DictionaryArtifact("https://example.invalid/pinned", hashlib.sha256(data).hexdigest(), len(data), "synthetic-1")
dictionary.DictionaryArtifact = lambda: pin
DictionaryArtifactManager.install = no_network
def barrier():
    with (home / "entered.fifo").open("wb", buffering=0) as entered: entered.write(b"x")
    with (home / "release.fifo").open("rb", buffering=0) as release:
        if release.read(1) != b"x": raise AssertionError("synthetic release missing")
if mode in ("install-barrier", "eof-barrier"):
    original = DictionaryArtifactManager._store_status
    def status(self, path):
        result = original(self, path)
        if pathlib.Path(path).name.startswith(".dictionary-stage-"): barrier()
        return result
    DictionaryArtifactManager._store_status = status
elif mode == "lookup-barrier":
    original = LocalDictionary.lookup
    def lookup(self, text):
        barrier()
        return original(self, text)
    LocalDictionary.lookup = lookup
elif mode == "commit-barrier":
    original = dictionary.DictionaryService._enable
    def enable(self, enabled):
        barrier()
        return original(self, enabled)
    dictionary.DictionaryService._enable = enable
elif mode == "dictionary-worker-failure":
    import threading
    def fail(thread): raise RuntimeError("synthetic worker failure")
    threading.Thread.start = fail
from cc_macos.server import main, Server
if mode == "eof-barrier":
    original_stop = Server._stop
    stopped = False
    def stop(self):
        global stopped
        original_stop(self)
        if not stopped:
            stopped = True
            with (home / "entered.fifo").open("wb", buffering=0) as entered: entered.write(b"x")
    Server._stop = stop
raise SystemExit(main([] if mode == "diagnostic" else ["--config-home", str(home), "--application-id", identity]))
"""


class TestDictionaryIPCProcess(StateIPCProcessCase):
    owner_script = SCRIPT
    bundle_modules = StateIPCProcessCase.bundle_modules + (
        dictionary, cc_dictionary_lookup, cc_dictionary_artifact_core, cc_dictionary_presentation,
    )

    def setUp(self):
        super().setUp()
        self.source = self.home / "source.sqlite3"
        create_fixture(self.source)
        self.data = self.source.read_bytes()
        self.counter = 0

    def request(self, process, operation, **payload):
        self.counter += 1
        id_ = "r" + str(self.counter)
        self.send_message(process, id_, "request", operation=operation, **payload)
        return self.terminal(process, id_)

    def prepare(self, process):
        response = self.request(process, "dictionary_prepare_install")
        self.assertEqual(response["type"], "completed")
        prepared = response["payload"]
        path = Path(prepared["path"])
        self.assertEqual(path.parent, self.directory / "dictionary")
        self.assertFalse(path.exists())
        with path.open("xb") as stream:
            stream.write(self.data)
        return prepared

    def lookup(self, process, **changes):
        return self.request(process, "dictionary_lookup", **dict(
            text="synthetic", origin="text", app_language="en_US", use_cache=True, record_history=True) | changes)

    def test_config_only_install_lookup_cache_delete_and_owner_release(self):
        process = self.spawn()
        ready = self.hello(process)
        self.assertEqual(ready["payload"]["capabilities"], [
            "config_load", "config_save", "history_load", "history_add", "history_clear",
            *dictionary.DICTIONARY_OPERATIONS])
        status = self.request(process, "dictionary_status")["payload"]
        self.assertEqual(status["state"], "not_installed")
        self.assertFalse((self.directory / "dictionary").exists())
        prepared = self.prepare(process)
        installed = self.request(process, "dictionary_install", ticket=prepared["ticket"])
        self.assertEqual((installed["payload"]["state"], installed["payload"]["enabled"]), ("ready", True))
        self.assertFalse(Path(prepared["path"]).exists())
        result = self.lookup(process)["payload"]
        self.assertEqual(result["status"], "hit")
        self.assertFalse(result["result"]["submitted"])
        self.assertEqual(result["result"]["history"], "recorded")
        self.assertIn("Synthetic source | 1 | Synthetic license", result["result"]["text"])
        self.assertNotIn("[[cc-", result["result"]["text"])
        self.assertEqual(self.lookup(process)["payload"]["result"]["history"], "unchanged")
        self.assertEqual(self.request(process, "dictionary_delete")["payload"], {"deleted": True, "enabled": False})
        self.assertEqual(self.lookup(process)["payload"], {"status": "disabled", "result": None})
        self.send_message(process, "stop", "shutdown")
        self.assertEqual(self.terminal(process, "stop")["type"], "completed")
        with MacConfigOwner(self.home, self.identity), MacHistoryOwner(self.history_path):
            pass
        self.finish_helper(process)

    def test_corrupt_history_returns_local_hit_when_enabled_and_is_ignored_when_disabled(self):
        process = self.spawn()
        self.hello(process)
        prepared = self.prepare(process)
        self.assertEqual(self.request(process, "dictionary_install", ticket=prepared["ticket"])["type"], "completed")
        expected = self.lookup(process, use_cache=False, record_history=False)["payload"]["result"]
        self.history_path.write_bytes(b"broken")
        for enabled in (True, False):
            with self.subTest(history_enabled=enabled):
                config = self.request(process, "config_load")["payload"]["config"]
                config["history_enabled"] = enabled
                self.assertEqual(self.request(process, "config_save", config=config)["type"], "completed")
                response = self.lookup(process)
                self.assertEqual(response["type"], "completed")
                self.assertEqual(response["payload"], {"status": "hit", "result": expected | {
                    "history": "failed" if enabled else "disabled",
                    "history_error": "invalid_history" if enabled else None,
                }})
                self.assertEqual(self.history_path.read_bytes(), b"broken")
        self.finish_helper(process)

    def test_tickets_are_session_bound_and_eof_cleans_only_owned_staging(self):
        process = self.spawn()
        self.hello(process)
        prepared = self.prepare(process)
        neighbor = self.directory / "dictionary" / "keep.txt"
        neighbor.write_text("keep", encoding="utf-8")
        self.finish_helper(process)
        self.assertFalse(Path(prepared["path"]).exists())
        self.assertEqual(neighbor.read_text(encoding="utf-8"), "keep")
        reopened = self.spawn()
        self.hello(reopened)
        self.assertEqual(self.request(reopened, "dictionary_install", ticket=prepared["ticket"])["payload"],
                         {"code": "invalid_dictionary_ticket"})
        self.finish_helper(reopened)

    def test_failed_or_symlinked_install_preserves_old_artifact_and_target(self):
        process = self.spawn()
        self.hello(process)
        prepared = self.prepare(process)
        self.assertEqual(self.request(process, "dictionary_install", ticket=prepared["ticket"])["type"], "completed")
        installed = self.directory / "dictionary" / "cc_dictionary.sqlite3"
        for symlink in (False, True):
            prepared = self.prepare(process)
            path = Path(prepared["path"])
            if symlink:
                path.unlink()
                path.symlink_to(self.source)
            else:
                path.write_bytes(b"broken")
            self.assertEqual(self.request(process, "dictionary_install", ticket=prepared["ticket"])["payload"],
                             {"code": "dictionary_install_failed"})
            self.assertFalse(path.exists())
            self.assertEqual(installed.read_bytes(), self.data)
            self.assertEqual(self.source.read_bytes(), self.data)
        self.finish_helper(process)

    def test_cancel_install_drains_hash_worker_without_blocking_config_lane(self):
        self.mode = "install-barrier"
        process = self.spawn()
        self.hello(process)
        prepared = self.prepare(process)
        self.send_message(process, "install", "request", operation="dictionary_install", ticket=prepared["ticket"])
        self.assertEqual([self.receive(process)["type"], self.receive(process)["type"]], ["accepted", "started"])
        self.barrier()
        self.assertEqual(self.request(process, "config_save", config={"future": "kept"})["type"], "completed")
        self.send_message(process, "cancel", "cancel", request_id="install")
        self.assertEqual(self.terminal(process, "cancel")["payload"], {"cancel_requested": True})
        self.assertTrue(Path(prepared["path"]).exists())
        self.release()
        self.assertEqual(self.terminal(process, "install")["type"], "cancelled")
        self.assertFalse(Path(prepared["path"]).exists())
        self.assertFalse((self.directory / "dictionary" / "cc_dictionary.sqlite3").exists())
        self.finish_helper(process)

    def test_eof_during_validation_waits_for_cleanup_before_releasing_owners(self):
        self.mode = "eof-barrier"
        process = self.spawn()
        self.hello(process)
        prepared = self.prepare(process)
        self.send_message(process, "install", "request", operation="dictionary_install", ticket=prepared["ticket"])
        self.receive(process)
        self.receive(process)
        self.barrier()
        process.stdin.close()
        process.stdin = None
        self.barrier()
        self.release()
        self.finish_helper(process)
        terminal = [event for pid, event in self.events if pid == process.pid and event["id"] == "install"][-1]
        self.assertEqual((terminal["type"], terminal["payload"]), ("cancelled", {}))
        self.assertFalse(Path(prepared["path"]).exists())
        with MacConfigOwner(self.home, self.identity), MacHistoryOwner(self.history_path):
            pass

    def test_postcommit_cancel_is_rejected_and_config_merge_preserves_other_edits(self):
        self.mode = "commit-barrier"
        process = self.spawn()
        self.hello(process)
        prepared = self.prepare(process)
        self.send_message(process, "install", "request", operation="dictionary_install", ticket=prepared["ticket"])
        self.receive(process)
        self.receive(process)
        self.barrier()
        self.send_message(process, "cancel", "cancel", request_id="install")
        self.assertEqual(self.terminal(process, "cancel")["payload"], {"cancel_requested": False})
        self.request(process, "config_save", config={"future": "kept", "history_enabled": False})
        self.release()
        self.assertEqual(self.terminal(process, "install")["type"], "completed")
        loaded = self.request(process, "config_load")["payload"]["config"]
        self.assertEqual(loaded["future"], "kept")
        self.assertFalse(loaded["history_enabled"])
        self.assertTrue(loaded["local_dictionary_enabled"])
        self.finish_helper(process)

    def test_fixed_validation_and_worker_start_errors_have_no_fake_lookup_result(self):
        process = self.spawn()
        self.hello(process)
        self.assertEqual(self.request(process, "dictionary_lookup", text="synthetic", path="private")["payload"],
                         {"code": "invalid_dictionary"})
        self.assertEqual(self.request(process, "dictionary_install", ticket="a" * 32)["payload"],
                         {"code": "invalid_dictionary_ticket"})
        self.finish_helper(process)
        self.mode = "dictionary-worker-failure"
        process = self.spawn()
        self.hello(process)
        failed = self.request(process, "dictionary_status")
        self.assertEqual((failed["seq"], failed["payload"]), (1, {"code": "worker_start_failed"}))
        self.finish_helper(process, code=2, errors=b"cc_macos:worker_start_failed\n")

    def test_diagnostic_connection_does_not_advertise_or_accept_dictionary(self):
        self.mode = "diagnostic"
        process = self.spawn()
        self.assertEqual(self.hello(process)["payload"]["capabilities"], ["fixture", "runtime_probe"])
        self.assertEqual(self.request(process, "dictionary_status")["payload"], {"code": "unsupported_operation"})
        self.finish_helper(process)
        self.assertFalse(self.directory.exists())


if __name__ == "__main__":
    unittest.main()
