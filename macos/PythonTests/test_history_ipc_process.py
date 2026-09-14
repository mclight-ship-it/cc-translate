"""Real bundled history IPC, stable dual ownership and controlled write/exit races."""

import json
import sys
import unittest

if sys.platform != "darwin":
    raise RuntimeError("History IPC process tests require bundled Darwin Python.")

from cc_macos import history, protocol
from cc_macos.config_owner import ConfigInUseError, MacConfigOwner
from cc_macos.history_owner import HistoryInUseError, MacHistoryOwner
from state_ipc_process_support import StateIPCProcessCase


def addition(text="synthetic", **changes):
    return {"operation": "history_add", "input": text, "output": " out \u4e2d\n",
            "is_dict": False, "is_code": False, "kind": "text", "sig": "unchanged|signature",
            "limit": 100, **changes}


class TestHistoryIPCProcess(StateIPCProcessCase):
    def page(self, process, id_, *, size=100, cursor=None):
        self.send_message(process, id_, "request", operation="history_load", page_size=size, cursor=cursor)
        return self.terminal(process, id_)

    def add(self, process, id_, **changes):
        self.send_message(process, id_, "request", **addition(**changes))
        return self.terminal(process, id_)

    def assert_both_owned(self):
        with self.assertRaises(ConfigInUseError):
            MacConfigOwner(self.home, self.identity)
        with self.assertRaises(HistoryInUseError):
            MacHistoryOwner(self.history_path)

    def assert_both_released(self):
        with MacConfigOwner(self.home, self.identity), MacHistoryOwner(self.history_path):
            pass

    def start_blocked_add(self):
        self.mode = "barrier"
        process = self.spawn()
        self.assertEqual(self.hello(process)["type"], "ready")
        self.send_message(process, "add", "request", **addition("committed"))
        self.assertEqual(self.receive(process)["type"], "accepted")
        self.assertEqual(self.receive(process)["type"], "started")
        self.barrier()
        self.assertFalse(self.history_path.exists())
        self.assertEqual(len(list(self.directory.glob(".tmp_*.json"))), 1)
        return process

    def test_missing_add_config_coexistence_schema_cache_limit_and_reopen(self):
        process = self.spawn()
        self.assertEqual(self.hello(process)["payload"]["fixture"], False)
        page = self.page(process, "empty")["payload"]
        self.assertEqual((page["entries"], page["total"], page["next_cursor"]), ([], 0, None))
        self.assertFalse(self.history_path.exists())
        self.assertFalse(self.path.exists())
        self.send_message(process, "config", "request", operation="config_save", config={"future": "retained"})
        self.assertEqual(self.terminal(process, "config")["payload"], {"saved": True})
        self.assertTrue(self.add(process, "first", text="old")["payload"]["recorded"])
        self.add(process, "second", text="word", kind="dict", is_dict=True, limit=1)
        page = self.page(process, "read")["payload"]
        self.assertEqual(page["total"], 1)
        entry = page["entries"][0]
        self.assertEqual(list(entry), ["ts", "input", "output", "is_dict", "is_code", "kind", "sig"])
        self.assertRegex(entry["ts"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(entry["input"], "word")
        self.assertEqual((entry["kind"], entry["is_dict"], entry["is_code"], entry["sig"]),
                         ("dict", True, False, "unchanged|signature"))
        self.finish_helper(process)
        with MacHistoryOwner(self.history_path) as owner:
            self.assertEqual(owner.find_cached(" word ", "dict", "unchanged|signature"), "out \u4e2d")
            self.assertIsNone(owner.find_cached("word", "ocr", "unchanged|signature"))
        reopened = self.spawn()
        self.hello(reopened)
        self.assertEqual(self.page(reopened, "read")["payload"]["entries"], [entry])
        self.finish_helper(reopened)
        self.assert_both_released()

    def test_unicode_pages_are_bounded_complete_and_in_original_order(self):
        process = self.spawn()
        self.hello(process)
        for n in range(6):
            self.add(process, "add" + str(n), text=str(n), output="\u4e2d" * 6000)
        cursor, inputs = None, []
        for n in range(6):
            id_ = str(n) + "r" * 63
            event = self.page(process, id_, cursor=cursor)
            self.assertEqual(event["type"], "completed")
            wire = protocol.encode_frame(event)
            self.assertLessEqual(len(wire), protocol.MAX_FRAME_BYTES)
            self.assertGreater(len(wire), 16384)
            page = event["payload"]
            inputs.extend(entry["input"] for entry in page["entries"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertIsNone(cursor)
        self.assertEqual(inputs, ["5", "4", "3", "2", "1", "0"])
        self.finish_helper(process)

    def test_cursor_expires_on_add_clear_external_change_and_new_connection(self):
        process = self.spawn()
        self.hello(process)
        for n in range(3):
            self.add(process, "seed" + str(n), text=str(n))
        for n, change in enumerate(("add", "external", "reopen", "clear")):
            cursor = self.page(process, "first" + str(n), size=1)["payload"]["next_cursor"]
            self.assertIsNotNone(cursor)
            if change == "add":
                self.add(process, "change", text="new")
            elif change == "external":
                self.history_path.write_bytes(self.history_path.read_bytes() + b" ")
            elif change == "reopen":
                self.finish_helper(process)
                process = self.spawn()
                self.hello(process)
            else:
                self.send_message(process, "clear", "request", operation="history_clear")
                self.assertTrue(self.terminal(process, "clear")["payload"]["cleared"])
            self.assertEqual(self.page(process, "expired" + str(n), cursor=cursor)["payload"],
                             {"code": "history_cursor_expired"})
        self.assertFalse(self.history_path.exists())
        self.add(process, "after", text="after clear")
        self.assertEqual(self.page(process, "last")["payload"]["entries"][0]["input"], "after clear")
        self.finish_helper(process)

    def test_exact_legacy_entry_envelope_and_one_byte_over_do_not_modify_disk(self):
        process = self.spawn()
        self.hello(process)
        entry = {"input": ""}
        overhead = len(history.page_frame(history.page_payload([entry], "0" * 64, 1, None), "r" * 64, 2))
        entry["input"] = "\u4e2d" * 10000 + "x" * (protocol.MAX_FRAME_BYTES - overhead - 30000)
        original = json.dumps([entry], ensure_ascii=False).encode()
        self.history_path.write_bytes(original)
        event = self.page(process, "r" * 64)
        self.assertEqual(len(protocol.encode_frame(event)), protocol.MAX_FRAME_BYTES)
        self.assertEqual(event["payload"]["entries"], [entry])
        self.assertEqual(self.history_path.read_bytes(), original)
        entry["input"] += "x"
        original = json.dumps([entry], ensure_ascii=False).encode()
        self.history_path.write_bytes(original)
        self.assertEqual(self.page(process, "s" * 64)["payload"], {"code": "history_entry_too_large"})
        self.assertEqual(self.history_path.read_bytes(), original)
        entry["input"] = entry["input"][:-4]
        entries = [entry, {}]
        original = json.dumps(entries, ensure_ascii=False).encode()
        self.history_path.write_bytes(original)
        event = self.page(process, "t" * 64)
        self.assertEqual(len(protocol.encode_frame(event)), protocol.MAX_FRAME_BYTES)
        self.assertEqual(event["payload"]["entries"], entries)
        self.assertIsNone(event["payload"]["next_cursor"])
        self.assertEqual(self.history_path.read_bytes(), original)
        self.finish_helper(process)

    def test_corrupt_and_unsafe_legacy_files_are_not_empty_or_overwritten(self):
        process = self.spawn()
        self.hello(process)
        for n, original in enumerate((b"{", b"\xff", b"{}", b"[1]", b'[{"x":1,"x":2}]',
                                      b'[{"is_code":1}]', b'[{"future":NaN}]')):
            self.history_path.write_bytes(original)
            self.assertEqual(self.page(process, "read" + str(n))["payload"], {"code": "invalid_history"})
            self.assertEqual(self.add(process, "add" + str(n))["payload"], {"code": "invalid_history"})
            self.assertEqual(self.history_path.read_bytes(), original)
            self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])
        self.assert_both_owned()
        self.send_message(process, "clear", "request", operation="history_clear")
        self.assertTrue(self.terminal(process, "clear")["payload"]["cleared"])
        self.finish_helper(process)
        self.assertFalse(self.history_path.exists())

    def test_second_history_owner_failure_releases_acquired_config_owner(self):
        self.directory.mkdir(parents=True)
        with MacHistoryOwner(self.history_path):
            rejected = self.spawn()
            self.assertEqual(self.hello(rejected)["payload"], {"code": "history_in_use"})
            self.finish_helper(rejected, code=2)
            with MacConfigOwner(self.home, self.identity):
                pass
            self.assertFalse(self.path.exists())
            self.assertFalse(self.history_path.exists())
        accepted = self.spawn()
        self.assertEqual(self.hello(accepted)["type"], "ready")
        self.assert_both_owned()
        self.finish_helper(accepted)
        self.assert_both_released()

    def test_started_add_clear_fifo_and_cancelled_queued_add(self):
        process = self.start_blocked_add()
        try:
            self.send_message(process, "cancel_active", "cancel", request_id="add")
            self.assertEqual(self.terminal(process, "cancel_active")["payload"], {"cancel_requested": False})
            self.send_message(process, "queued", "request", **addition("never"))
            self.assertEqual(self.receive(process)["type"], "accepted")
            self.send_message(process, "cancel_queued", "cancel", request_id="queued")
            self.assertEqual(self.terminal(process, "cancel_queued")["payload"], {"cancel_requested": True})
            self.send_message(process, "clear", "request", operation="history_clear")
            self.assertEqual(self.receive(process)["type"], "accepted")
            self.assert_both_owned()
        finally:
            self.release()
        self.assertTrue(self.terminal(process, "clear")["payload"]["cleared"])
        self.assertFalse(self.history_path.exists())
        self.add(process, "after", text="after clear")
        self.finish_helper(process)
        self.assertEqual([entry["input"] for entry in json.loads(self.history_path.read_bytes())], ["after clear"])
        self.assertEqual([e["type"] for pid, e in self.events if pid == process.pid and e["id"] == "queued"],
                         ["accepted", "cancelled"])
        self.assert_both_released()

    def test_eof_shutdown_wait_for_started_add_and_release_both_owners(self):
        for action in ("eof", "shutdown"):
            if self.history_path.exists():
                self.history_path.unlink()
            process = self.start_blocked_add()
            try:
                self.send_message(process, "clear", "request", operation="history_clear")
                self.assertEqual(self.receive(process)["type"], "accepted")
                if action == "eof":
                    process.stdin.close()
                    process.stdin = None
                else:
                    self.send_message(process, "stop", "shutdown")
                self.assertEqual(self.terminal(process, "clear")["type"], "cancelled")
                self.assertIsNone(process.poll())
                self.assert_both_owned()
                self.assertFalse(self.history_path.exists())
            finally:
                self.release()
            self.finish_helper(process)
            self.assertEqual(json.loads(self.history_path.read_bytes())[0]["input"], "committed")
            self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])
            self.assert_both_released()

    def test_lost_stdout_commits_once_and_releases_both_owners(self):
        process = self.start_blocked_add()
        process.stdout.close()
        process.stdout = None
        try:
            self.assert_both_owned()
        finally:
            self.release()
        self.assertEqual(process.wait(timeout=8), 2)
        self.finish_helper(process, code=2, errors=b"cc_macos:pipe_error\n")
        entries = json.loads(self.history_path.read_bytes())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["input"], "committed")
        self.assert_both_released()

    def test_duplicate_during_write_never_replays_or_unlocks_early(self):
        process = self.start_blocked_add()
        try:
            self.send_message(process, "add", "request", **addition("duplicate"))
            self.assertEqual(self.terminal(process, "protocol")["payload"], {"code": "duplicate_id"})
            self.assertIsNone(process.poll())
            self.assert_both_owned()
        finally:
            self.release()
        self.finish_helper(process, code=2, errors=b"cc_macos:duplicate_id\n")
        entries = json.loads(self.history_path.read_bytes())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["input"], "committed")
        self.assert_both_released()

    def test_invalid_fields_and_frame_budget_reject_before_write(self):
        process = self.spawn()
        self.hello(process)
        budget = addition(input="", output="")
        overhead = len(protocol.encode_frame({"v": 1, "id": "invalid6", "type": "request", "payload": budget}))
        budget["input"] = "\0" * ((protocol.MAX_FRAME_BYTES - overhead) // 6)
        for n, payload in enumerate((addition(is_code=1), addition(limit=True), addition(ts="client"),
                                      addition(input="x" * 24001), addition(sig="x" * 4097),
                                      addition(kind="future"), budget,
                                      {"operation": "history_load", "page_size": 1, "cursor": {"revision": "a" * 64, "offset": True}})):
            id_ = "invalid" + str(n)
            self.send_message(process, id_, "request", **payload)
            event = self.terminal(process, id_)
            self.assertEqual((event["type"], event["seq"]), ("failed", 0))
            self.assertFalse(self.history_path.exists())
        raw = b'{"v":1,"id":"oversize","type":"request","payload":{"operation":"history_add","input":"' + b"x" * protocol.MAX_FRAME_BYTES + b'"}}\n'
        try:
            process.stdin.write(raw)
            process.stdin.flush()
        except BrokenPipeError:
            pass
        self.assertEqual(self.terminal(process, "protocol")["payload"], {"code": "frame_too_large"})
        self.finish_helper(process, code=2, errors=b"cc_macos:frame_too_large\n")
        self.assertFalse(self.history_path.exists())
        self.assert_both_released()

    def test_permission_and_file_size_failures_preserve_data_and_ownership(self):
        process = self.spawn()
        self.hello(process)
        self.add(process, "seed")
        original = self.history_path.read_bytes()
        mode = self.history_path.stat().st_mode & 0o777
        self.history_path.chmod(0)
        try:
            self.assertEqual(self.page(process, "denied")["payload"], {"code": "history_io_failed"})
        finally:
            self.history_path.chmod(mode)
        directory_mode = self.directory.stat().st_mode & 0o777
        self.directory.chmod(0o555)
        try:
            self.assertEqual(self.add(process, "write_denied")["payload"], {"code": "history_io_failed"})
        finally:
            self.directory.chmod(directory_mode)
        self.assertEqual(self.history_path.read_bytes(), original)
        self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])
        large = b" " * (history.MAX_HISTORY_FILE_BYTES + 1)
        self.history_path.write_bytes(large)
        self.assertEqual(self.page(process, "large")["payload"], {"code": "history_too_large"})
        self.assertEqual(self.add(process, "large_add")["payload"], {"code": "history_too_large"})
        self.assertEqual(self.history_path.read_bytes(), large)
        self.assert_both_owned()
        self.finish_helper(process)
        self.assert_both_released()

    def test_unknown_legacy_fields_survive_read_and_new_add(self):
        process = self.spawn()
        self.hello(process)
        legacy = {"ts": None, "input": "legacy", "output": "out", "kind": "old-kind",
                  "future": {"values": [False, None, "\u4e2d", 9007199254740991]}}
        self.history_path.write_bytes(json.dumps([legacy], ensure_ascii=False).encode())
        self.assertEqual(self.page(process, "read")["payload"]["entries"], [legacy])
        self.add(process, "add")
        self.assertEqual(self.page(process, "both")["payload"]["entries"][1], legacy)
        self.finish_helper(process)


if __name__ == "__main__":
    unittest.main()
