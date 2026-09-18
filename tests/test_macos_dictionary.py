"""Native dictionary scheduling, ownership and offline contracts using synthetic data."""

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from cc_config import CFG, DEFAULT_CONFIG
from cc_dictionary_artifact_core import DictionaryArtifact, DictionaryArtifactManager
from cc_dictionary_lookup import LocalDictionary
from cc_dictionary_presentation import format_dictionary_plain, source_details
from cc_dictionary_store import StoreStatus
from cc_macos import configuration, dictionary
from cc_macos.dictionary_probe import create_fixture
from cc_macos.protocol import MAX_FRAME_BYTES, ProtocolError, encode_frame
from cc_macos.server import Server
if __package__:
    from .test_macos_configuration import _ConfigurationDirectory, message
    from .test_macos_translation import EventOutput
else:
    from test_macos_configuration import _ConfigurationDirectory, message
    from test_macos_translation import EventOutput


def lookup_request(**changes):
    return dict(operation="dictionary_lookup", text="synthetic", app_language="en_US",
                origin="text", use_cache=True, record_history=True) | changes


class DictionaryRequestTests(unittest.TestCase):
    def test_six_exact_operations_and_text_bounds(self):
        for operation in dictionary.DICTIONARY_OPERATIONS:
            payload = (lookup_request() if operation == "dictionary_lookup" else
                       {"operation": operation, "ticket": "a" * 32} if operation in (
                           "dictionary_install", "dictionary_discard_install") else {"operation": operation})
            dictionary.validate_dictionary_request(payload)
            with self.assertRaises(ProtocolError):
                dictionary.validate_dictionary_request(payload | {"path": "private"})
        for text in ("a" * 8192, "\u4e2d" * 2730):
            dictionary.validate_dictionary_request(lookup_request(text=text))
        for changes in ({"text": ""}, {"text": True}, {"text": "a" * 8193}, {"text": "\ud800"},
                        {"text": "\u4e2d" * 2731}, {"origin": "ocr"}, {"app_language": []},
                        {"record_history": 1}, {"use_cache": None}, {"url": "https://example.invalid"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ProtocolError, "invalid_dictionary"):
                dictionary.validate_dictionary_request(lookup_request(**changes))
        for ticket in ("", "A" * 32, "a" * 31, "g" * 32, [], None):
            with self.assertRaisesRegex(ProtocolError, "invalid_dictionary_ticket"):
                dictionary.validate_dictionary_request({"operation": "dictionary_install", "ticket": ticket})


class MacDictionaryTests(_ConfigurationDirectory):
    def setUp(self):
        super().setUp()
        self.stdout, self.stderr = EventOutput(), io.StringIO()
        self.server = Server(io.BytesIO(), self.stdout, self.stderr, configuration=self.session)
        self.server._handle(message("hello", "hello"))
        self.addCleanup(self.server._join_workers)
        self.addCleanup(self.server._stop)
        self.service = self.session._dictionary
        self.source = self.home / "source.sqlite3"
        create_fixture(self.source)
        self.data = self.source.read_bytes()
        self.pin = DictionaryArtifact("https://example.invalid/pinned", hashlib.sha256(self.data).hexdigest(),
                                      len(self.data), "synthetic-1")
        self.service.manager = DictionaryArtifactManager(str(self.service.directory), self.pin,
                                                         Mock(side_effect=AssertionError("Python network")))
        self.config = dict(DEFAULT_CONFIG) | {CFG.LOCAL_DICTIONARY_ENABLED: True, CFG.MODEL_PROVIDER: "no-cli"}
        self.session.perform({"operation": "config_save", "config": self.config})
        self.sequence = 0

    def call(self, operation, **fields):
        self.sequence += 1
        id_ = "r" + str(self.sequence)
        self.server._handle(message(id_, "request", operation=operation, **fields))
        self.assertTrue(self.stdout.terminal(id_), id_)
        return self.stdout.result(id_)

    def lookup(self, **changes):
        return self.call(**lookup_request(**changes))

    def stage(self):
        response = self.call("dictionary_prepare_install")
        self.assertEqual(response["type"], "completed")
        value = response["payload"]
        path = Path(value["path"])
        self.assertFalse(path.exists())
        with path.open("xb") as stream:
            stream.write(self.data)
        return value

    def install(self):
        prepared = self.stage()
        installed = self.call("dictionary_install", ticket=prepared["ticket"])
        self.assertEqual(installed["type"], "completed", installed)
        self.assertEqual(installed["payload"]["state"], "ready")
        return installed

    def history(self):
        return self.session.perform_history(
            {"operation": "history_load", "page_size": 100, "cursor": None}, "read", 2)["entries"]

    def test_max_chars_validation_precedes_cached_disabled_and_ineligible_dictionary_routes(self):
        self.install()
        original = self.lookup()["payload"]["result"]
        self.assertEqual(original["history"], "recorded")
        path = self.directory / "history.json"
        before = path.read_bytes()
        for enabled in (True, False):
            for text, limit in (("synthetic", 8), (" synthetic ", 9), ("two words", 8), ("a" * 5001, 5000)):
                with self.subTest(enabled=enabled, limit=limit, size=len(text)):
                    self.session.perform({"operation": "config_save", "config": self.config | {
                        CFG.MAX_CHARS: limit, CFG.LOCAL_DICTIONARY_ENABLED: enabled}})
                    with patch.object(self.service, "_load_store", side_effect=AssertionError("over-limit store")), \
                            patch.object(self.session._history, "find_cached",
                                         side_effect=AssertionError("over-limit cache")):
                        failed = self.lookup(text=text)
                    self.assertEqual(failed["type"], "failed")
                    self.assertEqual(failed["payload"], {"code": "invalid_dictionary"})
                    self.assertEqual(path.read_bytes(), before)
        self.session.perform({"operation": "config_save", "config": self.config | {CFG.MAX_CHARS: 9}})
        cached = self.lookup()["payload"]["result"]
        self.assertEqual((cached["cached"], cached["submitted"], cached["history"]), (True, False, "unchanged"))
        self.assertEqual(cached["text"], original["text"])
        self.assertEqual(path.read_bytes(), before)
        self.service.manager._opener.assert_not_called()

    def test_capabilities_status_and_lookup_work_without_codex_or_implicit_install(self):
        self.assertEqual(self.stdout.events[0]["payload"]["capabilities"], [
            "config_load", "config_save", "history_load", "history_add", "history_clear",
            *dictionary.DICTIONARY_OPERATIONS])
        self.assertFalse(self.service.directory.exists())
        status = self.call("dictionary_status")["payload"]
        self.assertEqual(status, {"state": "not_installed", "enabled": True, "size": self.pin.size,
                                 "sha256": self.pin.sha256, "data_version": self.pin.data_version,
                                 "download_url": self.pin.url, "entry_count": 0})
        self.assertEqual(self.lookup()["payload"], {"status": "unavailable", "result": None})
        self.assertFalse(self.service.directory.exists())
        self.service.manager._opener.assert_not_called()
        self.assertEqual(self.call("translate")["payload"], {"code": "unsupported_operation"})

    def test_install_lookup_cache_attribution_and_delete_have_exact_contracts(self):
        installed = self.install()
        self.assertEqual(installed["payload"]["entry_count"], 1)
        self.assertEqual(self.history(), [])
        first = self.lookup()
        self.assertEqual(first["payload"]["status"], "hit")
        result = first["payload"]["result"]
        self.assertEqual(result, {
            "text": "synthetic\n\nnoun\n- Synthetic definition\n\nSources & licenses:\n- Synthetic source | 1 | Synthetic license",
            "submitted": False, "cached": False, "kind": "dict", "target_lang": None, "summarize": False,
            "history": "recorded", "history_error": None,
            "source_details": [{"id": "fixture", "label": "Synthetic source", "version": "1",
                                "license": "Synthetic license"}],
        })
        entry, = self.history()
        self.assertTrue(entry["is_dict"])
        self.assertFalse(entry["is_code"])
        self.assertIn("native-plain-v1:en_US", entry["sig"])
        self.assertEqual(self.lookup()["payload"]["result"]["history"], "unchanged")
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(self.call("dictionary_delete")["payload"], {"deleted": True, "enabled": False})
        self.assertEqual(self.lookup()["payload"], {"status": "disabled", "result": None})
        self.assertEqual(self.call("dictionary_delete")["payload"], {"deleted": False, "enabled": False})
        self.assertEqual(len(self.history()), 1)
        self.service.manager._opener.assert_not_called()

    def test_miss_ineligible_disabled_and_corrupt_store_do_not_create_history(self):
        self.install()
        self.assertEqual(self.lookup(text="no-such-term")["payload"], {"status": "miss", "result": None})
        self.assertEqual(self.lookup(text="This is a sentence.")["payload"], {"status": "ineligible", "result": None})
        self.assertEqual(self.history(), [])
        Path(self.service.manager.path).write_bytes(b"broken")
        self.assertEqual(self.call("dictionary_status")["payload"]["state"], "invalid")
        self.assertEqual(self.lookup()["payload"], {"status": "unavailable", "result": None})
        self.assertEqual(self.history(), [])
        self.session.perform({"operation": "config_save", "config": self.config | {CFG.LOCAL_DICTIONARY_ENABLED: False}})
        with patch.object(self.service, "_load_store", side_effect=AssertionError("disabled lookup")):
            self.assertEqual(self.lookup()["payload"], {"status": "disabled", "result": None})

    def test_native_plain_has_all_senses_tone_marks_and_entry_and_sense_attribution(self):
        local = LocalDictionary(str(self.source), self.pin.sha256)
        self.addCleanup(local.close_thread)
        result = local.lookup("synthetic")
        senses = tuple(replace(result.senses[0], definition="Definition " + str(i)) for i in range(12))
        senses += (replace(result.senses[0], definition="Separate source", source_id="second",
                           source_label="Second source", source_license="Second license"),)
        entry = replace(result.entries[0], pronunciation="Zhong1 guo2", part_of_speech="noun", senses=senses)
        rendered = format_dictionary_plain(replace(result, entries=(entry,), senses=senses), "zh_CN")
        for sense in senses:
            self.assertIn(sense.definition, rendered)
        self.assertIn("Zh\u014dng gu\u00f3", rendered)
        self.assertIn("Second source | 1 | Second license", rendered)
        self.assertIn("\u6765\u6e90\u4e0e\u8bb8\u53ef", rendered)
        self.assertNotIn("[[cc-", rendered)
        self.assertNotIn("##", rendered)

    def test_structured_sources_preserve_order_and_distinct_versions_without_changing_body_or_history(self):
        self.install()
        local = LocalDictionary(str(self.source), self.pin.sha256)
        self.addCleanup(local.close_thread)
        original = local.lookup("synthetic")
        alternate = replace(original.senses[0], source_version="2", source_license="Literal <license> [link](x)")
        senses = original.senses + (alternate, alternate)
        result = replace(original, entries=(replace(original.entries[0], senses=senses),), senses=senses)
        with patch.object(LocalDictionary, "lookup", return_value=result):
            value = self.lookup()["payload"]["result"]
        self.assertEqual(value["source_details"], source_details(result))
        self.assertEqual([row["version"] for row in value["source_details"]], ["1", "2"])
        self.assertEqual(value["text"], format_dictionary_plain(result, "en_US"))
        entry = self.history()[0]
        self.assertEqual(entry["output"], value["text"])
        self.assertNotIn("source_details", entry)
        self.assertEqual(entry["sig"].split("|")[-1], "native-plain-v1:en_US")

    def test_cache_hit_uses_live_structured_sources_not_cached_text_and_does_not_rewrite_history(self):
        self.install()
        first = self.lookup()["payload"]["result"]
        before = self.history()
        local = LocalDictionary(str(self.source), self.pin.sha256)
        self.addCleanup(local.close_thread)
        original = local.lookup("synthetic")
        result = replace(original, entries=(replace(original.entries[0], source_version="live-2"),))
        with patch.object(LocalDictionary, "lookup", return_value=result):
            cached = self.lookup()["payload"]["result"]
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["text"], first["text"])
        self.assertEqual(cached["source_details"], source_details(result))
        self.assertEqual(cached["source_details"][0]["version"], "live-2")
        self.assertEqual(self.history(), before)

    def test_lookup_commits_latest_lower_limit_but_later_cached_hit_never_trims(self):
        self.install()
        path = self.directory / "history.json"
        entries = [{"input": "older " + str(n), "output": "old", "kind": "text"} for n in range(5)]
        path.write_text(json.dumps(entries), encoding="utf-8")
        before = path.read_bytes()
        entered, release = threading.Event(), threading.Event()
        original = self.service._finish
        def gated(cancel, begin_finish):
            entered.set()
            if not release.wait(3):
                raise AssertionError("synthetic retention gate not released")
            return original(cancel, begin_finish)
        with patch.object(self.service, "_finish", side_effect=gated):
            self.server._handle(message("retention-lookup", "request", **lookup_request(use_cache=False)))
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.call("config_save", config=self.config | {CFG.HISTORY_LIMIT: 2})["payload"],
                                 {"saved": True})
                self.assertEqual(path.read_bytes(), before)
            finally:
                release.set()
            self.assertTrue(self.stdout.terminal("retention-lookup"))
        result = self.stdout.result("retention-lookup")
        self.assertEqual(result["type"], "completed")
        self.assertEqual(result["payload"]["status"], "hit")
        self.assertEqual(result["payload"]["result"]["history"], "recorded")
        stored = self.history()
        self.assertEqual(len(stored), 2)
        self.assertEqual(stored[1:], entries[:1])
        self.assertEqual((stored[0]["input"], stored[0]["kind"]), ("synthetic", "dict"))
        self.assertEqual(stored[0]["output"], result["payload"]["result"]["text"])
        after_commit = path.read_bytes()
        self.assertEqual(self.call("config_save", config=self.config | {CFG.HISTORY_LIMIT: 1})["payload"],
                         {"saved": True})
        cached = self.lookup()["payload"]["result"]
        self.assertEqual((cached["cached"], cached["submitted"], cached["history"]), (True, False, "unchanged"))
        self.assertEqual(path.read_bytes(), after_commit)
        self.service.manager._opener.assert_not_called()

    def test_oversized_source_metadata_fails_before_history_commit_or_finish(self):
        self.install()
        local = LocalDictionary(str(self.source), self.pin.sha256)
        self.addCleanup(local.close_thread)
        original = local.lookup("synthetic")
        for source_id in ("\u6e90" * 22000, '"' * 33000):
            result = replace(original, entries=(replace(original.entries[0], source_id=source_id),))
            with patch.object(LocalDictionary, "lookup", return_value=result), \
                    patch.object(self.service, "_finish", side_effect=AssertionError("premature finish")):
                failed = self.lookup()
            self.assertEqual(failed["type"], "failed")
            self.assertEqual(failed["payload"], {"code": "dictionary_output_limit"})
            self.assertEqual(self.history(), [])
            self.assertNotIn(source_id, self.stderr.getvalue())

    def test_source_metadata_near_frame_limit_is_not_arbitrarily_truncated(self):
        self.install()
        local = LocalDictionary(str(self.source), self.pin.sha256)
        self.addCleanup(local.close_thread)
        original = local.lookup("synthetic")
        source_id = "s" * 64000
        result = replace(original, entries=(replace(original.entries[0], source_id=source_id),))
        with patch.object(LocalDictionary, "lookup", return_value=result):
            terminal = self.lookup(record_history=False)
        self.assertEqual(terminal["type"], "completed")
        self.assertEqual(terminal["payload"]["result"]["source_details"][0]["id"], source_id)
        self.assertLessEqual(len(encode_frame(terminal)), MAX_FRAME_BYTES)
        self.assertEqual(terminal["payload"]["result"]["text"], format_dictionary_plain(result, "en_US"))
        self.assertEqual(self.history(), [])

    def test_disabled_history_never_reads_or_writes_corrupt_history(self):
        self.install()
        expected = self.lookup(use_cache=False, record_history=False)["payload"]
        history = self.directory / "history.json"
        history.write_bytes(b"broken")
        self.session.perform({"operation": "config_save", "config": self.config | {CFG.HISTORY_ENABLED: False}})
        with patch.object(self.session._history, "find_cached", side_effect=AssertionError("disabled cache")) as cache, \
                patch.object(self.session, "perform_history", side_effect=AssertionError("disabled history")) as write:
            for use_cache in (False, True):
                for record_history in (False, True):
                    with self.subTest(use_cache=use_cache, record_history=record_history):
                        response = self.lookup(use_cache=use_cache, record_history=record_history)
                        self.assertEqual(response["type"], "completed")
                        self.assertEqual(response["payload"], expected)
                        self.assertEqual(response["payload"]["result"]["history"], "disabled")
                        self.assertEqual(history.read_bytes(), b"broken")
        cache.assert_not_called()
        write.assert_not_called()
        self.service.manager._opener.assert_not_called()

    def test_enabled_corrupt_or_unreadable_cache_returns_fresh_hit_without_repair(self):
        self.install()
        expected = self.lookup(use_cache=False, record_history=False)["payload"]["result"]
        history = self.directory / "history.json"
        history.write_bytes(b"broken")
        self.session.perform({"operation": "config_save", "config": self.config | {CFG.HISTORY_ENABLED: True}})
        owner = self.session._history._owner
        for failure, code in ((None, "invalid_history"), (OSError("PRIVATE"), "history_io_failed")):
            for record_history in (False, True):
                with self.subTest(code=code, record_history=record_history), \
                        patch.object(owner, "find_cached", wraps=owner.find_cached, side_effect=failure) as cache, \
                        patch.object(self.session, "perform_history", side_effect=AssertionError("history repair")) as write:
                    response = self.lookup(record_history=record_history)
                self.assertEqual(response["type"], "completed")
                self.assertEqual(response["payload"], {
                    "status": "hit", "result": expected | {"history": "failed", "history_error": code},
                })
                cache.assert_called_once()
                write.assert_not_called()
                self.assertEqual(history.read_bytes(), b"broken")
        self.service.manager._opener.assert_not_called()

    def test_fresh_history_write_failures_retain_local_result_and_original_history(self):
        self.install()
        expected = self.lookup(use_cache=False, record_history=False)["payload"]["result"]
        history = self.directory / "history.json"
        history.write_bytes(b"broken")
        owner = self.session._history._owner
        for failure, code in ((None, "invalid_history"), (OSError("PRIVATE"), "history_io_failed")):
            with self.subTest(code=code), \
                    patch.object(owner, "add", wraps=owner.add, side_effect=failure) as write, \
                    patch.object(self.session._history, "find_cached", side_effect=AssertionError("unexpected cache")):
                response = self.lookup(use_cache=False)
            self.assertEqual(response["type"], "completed")
            self.assertEqual(response["payload"], {
                "status": "hit", "result": expected | {"history": "failed", "history_error": code},
            })
            write.assert_called_once()
            self.assertEqual(history.read_bytes(), b"broken")
        self.service.manager._opener.assert_not_called()

    def test_config_load_failures_remain_fatal_at_every_lookup_snapshot(self):
        self.install()
        config = self.session.dictionary_config()
        for failure_at in (1, 2, 3):
            with self.subTest(failure_at=failure_at), \
                    patch.object(self.session, "dictionary_config", side_effect=(
                        [config] * (failure_at - 1) + [configuration.ConfigurationError("invalid_config")])) as capture, \
                    patch.object(self.session, "perform_history", side_effect=AssertionError("unknown settings")) as write:
                response = self.lookup()
            self.assertEqual(response["type"], "failed")
            self.assertEqual(response["payload"], {"code": "invalid_config"})
            self.assertEqual(capture.call_count, failure_at)
            write.assert_not_called()
        self.assertEqual(self.history(), [])

    def test_cancel_after_corrupt_cache_read_does_not_return_partial_success(self):
        self.install()
        history = self.directory / "history.json"
        history.write_bytes(b"broken")
        cancel = threading.Event()
        commit = Mock(return_value=True)
        find_cached = self.session._history.find_cached
        def cancel_read(*args):
            cancel.set()
            return find_cached(*args)
        with patch.object(self.session._history, "find_cached", side_effect=cancel_read), \
                patch.object(self.session, "perform_history", side_effect=AssertionError("cancelled history")) as write, \
                self.assertRaises(dictionary.DictionaryCancelled):
            self.service.perform(lookup_request(), cancel, commit)
        commit.assert_not_called()
        write.assert_not_called()
        self.assertEqual(history.read_bytes(), b"broken")

    def test_locale_signature_is_distinct_and_live_lookup_precedes_cache(self):
        self.install()
        english = self.lookup()["payload"]["result"]
        chinese = self.lookup(app_language="zh_CN")["payload"]["result"]
        self.assertFalse(chinese["cached"])
        self.assertNotEqual(english["text"], chinese["text"])
        self.assertEqual(len(self.history()), 2)
        Path(self.service.manager.path).write_bytes(b"broken")
        self.assertEqual(self.lookup()["payload"], {"status": "unavailable", "result": None})

    def test_ticket_binding_unknown_tokens_discard_and_close_cleanup_only_owned_paths(self):
        prepared = self.stage()
        other = dictionary.DictionaryService(self.session, self.directory)
        self.addCleanup(other.close)
        with self.assertRaisesRegex(dictionary.DictionaryError, "invalid_dictionary_ticket"):
            other.perform({"operation": "dictionary_install", "ticket": prepared["ticket"]},
                          threading.Event(), lambda: True)
        self.assertTrue(Path(prepared["path"]).exists())
        neighbor = self.service.directory / "keep.txt"
        neighbor.write_text("keep", encoding="utf-8")
        self.assertEqual(self.call("dictionary_discard_install", ticket=prepared["ticket"])["payload"], {"discarded": True})
        self.assertEqual(self.call("dictionary_discard_install", ticket=prepared["ticket"])["payload"],
                         {"code": "invalid_dictionary_ticket"})
        pending = self.stage()
        self.server._stop()
        self.server._join_workers()
        self.session.close()
        self.assertFalse(Path(pending["path"]).exists())
        self.assertEqual(neighbor.read_text(encoding="utf-8"), "keep")

    def test_failed_install_preserves_old_file_consumes_ticket_and_never_uses_network(self):
        self.install()
        prepared = self.stage()
        Path(prepared["path"]).write_bytes(b"invalid")
        failed = self.call("dictionary_install", ticket=prepared["ticket"])
        self.assertEqual(failed["payload"], {"code": "dictionary_install_failed"})
        self.assertEqual(Path(self.service.manager.path).read_bytes(), self.data)
        self.assertFalse(Path(prepared["path"]).exists())
        self.assertEqual(self.call("dictionary_install", ticket=prepared["ticket"])["payload"],
                         {"code": "invalid_dictionary_ticket"})
        self.assertEqual(self.history(), [])
        self.service.manager._opener.assert_not_called()

    def test_install_config_failure_does_not_pretend_rollback_or_overwrite_corrupt_config(self):
        prepared = self.stage()
        self.path.write_bytes(b"{broken")
        result = self.call("dictionary_install", ticket=prepared["ticket"])
        self.assertEqual(result["payload"], {"code": "invalid_config"})
        self.assertEqual(Path(self.service.manager.path).read_bytes(), self.data)
        self.assertEqual(self.path.read_bytes(), b"{broken")
        self.assertFalse(Path(prepared["path"]).exists())

    def test_postcommit_inspection_failure_is_io_failure_not_rejected_artifact(self):
        prepared = self.stage()
        with patch.object(self.service.manager, "inspect", return_value=StoreStatus(False, "private", error="PRIVATE")):
            result = self.call("dictionary_install", ticket=prepared["ticket"])
        self.assertEqual(result["payload"], {"code": "dictionary_io_failed"})
        self.assertEqual(Path(self.service.manager.path).read_bytes(), self.data)
        self.assertFalse(Path(prepared["path"]).exists())
        self.assertNotIn("PRIVATE", self.stdout.getvalue().decode())

    def test_hash_lane_does_not_block_config_edits_and_records_only_latest_history_optin(self):
        self.install()
        entered, release = threading.Event(), threading.Event()
        original = self.service._load_store
        def gated():
            entered.set()
            if not release.wait(3):
                raise AssertionError("synthetic gate not released")
            return original()
        with patch.object(self.service, "_load_store", side_effect=gated):
            self.server._handle(message("lookup", "request", **lookup_request()))
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.call("config_save", config=self.config | {
                    CFG.HISTORY_ENABLED: False, CFG.LANGUAGE: "zh_CN", "future": "preserved",
                })["type"], "completed")
            finally:
                release.set()
            self.assertTrue(self.stdout.terminal("lookup"))
        result = self.stdout.result("lookup")["payload"]["result"]
        self.assertEqual(result["history"], "disabled")
        self.assertIn("Sources & licenses:", result["text"])
        self.assertEqual(self.history(), [])

    def test_cancelled_lookup_drains_without_history_or_success(self):
        self.install()
        entered, release = threading.Event(), threading.Event()
        original = self.service._load_store
        def gated():
            entered.set()
            if not release.wait(3):
                raise AssertionError("synthetic gate not released")
            return original()
        with patch.object(self.service, "_load_store", side_effect=gated):
            self.server._handle(message("lookup", "request", **lookup_request()))
            try:
                self.assertTrue(entered.wait(1))
                self.server._handle(message("cancel", "cancel", request_id="lookup"))
                self.assertTrue(self.stdout.result("cancel")["payload"]["cancel_requested"])
                self.assertIn("lookup", self.server._tasks)
            finally:
                release.set()
            self.assertTrue(self.stdout.terminal("lookup"))
        self.assertEqual(self.stdout.result("lookup")["type"], "cancelled")
        self.assertEqual(self.stdout.result("lookup")["payload"], {})
        self.assertEqual(self.history(), [])

    def test_cancelled_install_cleans_staging_and_preserves_previous_artifact(self):
        self.install()
        prepared = self.stage()
        entered, release = threading.Event(), threading.Event()
        original = self.service.manager._store_status
        def gated(path):
            result = original(path)
            entered.set()
            if not release.wait(3):
                raise AssertionError("synthetic gate not released")
            return result
        with patch.object(self.service.manager, "_store_status", side_effect=gated):
            self.server._handle(message("install", "request", operation="dictionary_install", ticket=prepared["ticket"]))
            try:
                self.assertTrue(entered.wait(1))
                self.server._handle(message("cancel", "cancel", request_id="install"))
            finally:
                release.set()
            self.assertTrue(self.stdout.terminal("install"))
        self.assertEqual(self.stdout.result("install")["type"], "cancelled")
        self.assertFalse(Path(prepared["path"]).exists())
        self.assertEqual(Path(self.service.manager.path).read_bytes(), self.data)

    def test_committed_install_rejects_late_cancel_and_preserves_concurrent_config_fields(self):
        prepared = self.stage()
        entered, release = threading.Event(), threading.Event()
        original = self.service._enable
        def gated(enabled):
            entered.set()
            if not release.wait(3):
                raise AssertionError("synthetic gate not released")
            return original(enabled)
        with patch.object(self.service, "_enable", side_effect=gated):
            self.server._handle(message("install", "request", operation="dictionary_install", ticket=prepared["ticket"]))
            try:
                self.assertTrue(entered.wait(1))
                self.server._handle(message("cancel", "cancel", request_id="install"))
                self.assertFalse(self.stdout.result("cancel")["payload"]["cancel_requested"])
                self.call("config_save", config=self.config | {"future": "kept", CFG.DIRECTION: "to_ja"})
            finally:
                release.set()
            self.assertTrue(self.stdout.terminal("install"))
        self.assertEqual(self.stdout.result("install")["type"], "completed")
        self.assertEqual(self.session.dictionary_config()["future"], "kept")
        self.assertEqual(self.session.dictionary_config()[CFG.DIRECTION], "to_ja")

    def test_output_limit_and_lookup_storage_failure_are_fixed_failures_not_misses(self):
        self.install()
        with patch.object(dictionary, "format_dictionary_plain", return_value="x" * 24000):
            self.assertEqual(self.lookup()["payload"], {"code": "dictionary_output_limit"})
        with patch.object(LocalDictionary, "lookup", side_effect=sqlite3.OperationalError("PRIVATE")):
            self.assertEqual(self.lookup()["payload"], {"code": "dictionary_unavailable"})
        self.assertEqual(self.history(), [])
        self.assertNotIn("PRIVATE", self.stdout.getvalue().decode())

    def test_precancelled_prepare_has_no_storage_effect_and_ticket_limit_is_bounded(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(dictionary.DictionaryCancelled):
            self.service.perform({"operation": "dictionary_prepare_install"}, cancel, lambda: True)
        self.assertFalse(self.service.directory.exists())
        with patch.object(dictionary, "MAX_TICKETS", 1):
            self.stage()
            self.assertEqual(self.call("dictionary_prepare_install")["payload"], {"code": "dictionary_busy"})

    def test_prepare_reserves_absent_direct_child_for_exclusive_native_creation(self):
        response = self.call("dictionary_prepare_install")
        self.assertEqual(response["type"], "completed")
        prepared = response["payload"]
        path = Path(prepared["path"])
        self.assertRegex(prepared["ticket"], r"^[0-9a-f]{32}$")
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.parent, self.service.directory)
        self.assertEqual(path.name, ".dictionary-stage-" + prepared["ticket"] + ".sqlite3")
        self.assertTrue(path.parent.is_dir())
        self.assertFalse(path.exists())
        self.assertEqual(prepared, {
            "ticket": prepared["ticket"], "path": str(path), "url": self.pin.url,
            "size": self.pin.size, "sha256": self.pin.sha256, "data_version": self.pin.data_version,
        })
        with path.open("xb") as stream:
            stream.write(self.data)
        installed = self.call("dictionary_install", ticket=prepared["ticket"])
        self.assertEqual(installed["type"], "completed")
        self.assertEqual(installed["payload"]["state"], "ready")
        self.assertFalse(path.exists())
        self.service.manager._opener.assert_not_called()

    def test_absent_staging_can_be_discarded_but_cannot_be_installed(self):
        for operation in ("dictionary_discard_install", "dictionary_install"):
            with self.subTest(operation=operation):
                prepared = self.call("dictionary_prepare_install")["payload"]
                response = self.call(operation, ticket=prepared["ticket"])
                if operation == "dictionary_discard_install":
                    self.assertEqual((response["type"], response["payload"]), ("completed", {"discarded": True}))
                else:
                    self.assertEqual((response["type"], response["payload"]),
                                     ("failed", {"code": "dictionary_install_failed"}))
                self.assertNotIn(prepared["ticket"], self.service._tickets)
                self.assertFalse(Path(prepared["path"]).exists())
                self.assertFalse(Path(self.service.manager.path).exists())

    def test_prepare_rejects_ticket_and_file_collisions_without_claiming_other_files(self):
        with patch.object(dictionary.uuid, "uuid4", return_value=Mock(hex="a" * 32)):
            prepared = self.call("dictionary_prepare_install")["payload"]
            path = Path(prepared["path"])
            self.assertEqual(self.call("dictionary_prepare_install")["payload"], {"code": "dictionary_busy"})
            self.assertEqual(self.service._tickets, {prepared["ticket"]: path})
            self.assertEqual(self.call("dictionary_discard_install", ticket=prepared["ticket"])["payload"],
                             {"discarded": True})
            path.write_bytes(b"unowned")
            self.assertEqual(self.call("dictionary_prepare_install")["payload"], {"code": "dictionary_busy"})
            self.assertEqual(self.service._tickets, {})
            self.assertEqual(path.read_bytes(), b"unowned")

    def test_cancelled_config_snapshot_never_writes_a_migration(self):
        self.path.write_bytes(b'{"local_dictionary_enabled":true,"codex_streaming_experimental":false}')
        before = self.path.read_bytes()
        with self.assertRaises(dictionary.DictionaryCancelled):
            self.service.perform(lookup_request(), threading.Event(), lambda: False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_error_allowlist_matches_swift_and_rejects_invalid_lookup_settings(self):
        self.assertEqual(dictionary.DICTIONARY_FAILURE_CODES, {
            "invalid_dictionary", "dictionary_unavailable", "dictionary_io_failed", "dictionary_busy",
            "invalid_dictionary_ticket", "dictionary_install_failed", "dictionary_output_limit",
            "dictionary_cleanup_failed",
        })
        self.session.perform({"operation": "config_save", "config": self.config | {CFG.MAX_CHARS: 1}})
        self.assertEqual(self.lookup()["payload"], {"code": "invalid_dictionary"})

    def test_cleanup_failure_takes_priority_over_cancel_and_keeps_ticket_for_retry(self):
        prepared = self.stage()
        path = Path(prepared["path"])
        cancel = threading.Event()
        cancel.set()
        with patch.object(Path, "unlink", side_effect=PermissionError("PRIVATE")):
            with self.assertRaisesRegex(dictionary.DictionaryError, "^dictionary_cleanup_failed$"):
                self.service.perform({"operation": "dictionary_install", "ticket": prepared["ticket"]},
                                     cancel, lambda: True)
        self.assertTrue(path.exists())
        self.assertIn(prepared["ticket"], self.service._tickets)
        self.assertEqual(self.call("dictionary_discard_install", ticket=prepared["ticket"])["payload"],
                         {"discarded": True})
        self.assertFalse(path.exists())

    def test_close_reports_cleanup_failure_but_attempts_remaining_tickets_and_releases_owners(self):
        first, second = self.stage(), self.stage()
        unlink = Path.unlink
        def denied(path, *args, **kwargs):
            if path == Path(first["path"]):
                raise PermissionError("PRIVATE")
            return unlink(path, *args, **kwargs)
        self.server._stop()
        self.server._join_workers()
        with patch.object(Path, "unlink", denied), \
                self.assertRaisesRegex(dictionary.DictionaryError, "^dictionary_cleanup_failed$"):
            self.session.close()
        self.assertTrue(Path(first["path"]).exists())
        self.assertFalse(Path(second["path"]).exists())
        self.assertIsNone(self.session._owner)
        self.assertIsNone(self.session._history)
        self.session.close()
        self.assertFalse(Path(first["path"]).exists())

    def test_queued_install_cancel_cleans_ticket_without_started_or_commit(self):
        self.install()
        original = self.service._load_store
        unlink = Path.unlink
        for index, cleanup_failed in enumerate((False, True)):
            prepared = self.stage()
            staged = Path(prepared["path"])
            entered, release = threading.Event(), threading.Event()
            def gated():
                entered.set()
                if not release.wait(3):
                    raise AssertionError("synthetic gate not released")
                return original()
            def cleanup(path, *args, **kwargs):
                if cleanup_failed and path == staged:
                    raise PermissionError("PRIVATE")
                return unlink(path, *args, **kwargs)
            queued, cancel = "queued_" + str(index), "cancel_" + str(index)
            with patch.object(self.service, "_load_store", side_effect=gated), \
                    patch.object(Path, "unlink", cleanup):
                self.server._handle(message("lookup_" + str(index), "request", **lookup_request(record_history=False)))
                try:
                    self.assertTrue(entered.wait(1))
                    self.server._handle(message(queued, "request", operation="dictionary_install", ticket=prepared["ticket"]))
                    self.server._handle(message(cancel, "cancel", request_id=queued))
                    self.assertTrue(self.stdout.result(cancel)["payload"]["cancel_requested"])
                finally:
                    release.set()
                self.assertTrue(self.stdout.terminal(queued))
            events = [event for event in self.stdout.events if event["id"] == queued]
            self.assertEqual([event["type"] for event in events],
                             ["accepted", "failed" if cleanup_failed else "cancelled"])
            if cleanup_failed:
                self.assertEqual(events[-1]["seq"], 1)
                self.assertEqual(events[-1]["payload"], {"code": "dictionary_cleanup_failed"})
                self.assertTrue(staged.exists())
                self.assertIn(prepared["ticket"], self.service._tickets)
                self.assertEqual(self.call("dictionary_discard_install", ticket=prepared["ticket"])["payload"],
                                 {"discarded": True})
            self.assertFalse(staged.exists())
            self.assertEqual(Path(self.service.manager.path).read_bytes(), self.data)

    def test_unexpected_transport_failure_is_connection_failure_not_miss(self):
        with patch.object(self.service, "_status", side_effect=ProtocolError("internal_error")):
            self.server._handle(message("status", "request", operation="dictionary_status"))
            self.assertTrue(self.stdout.terminal("protocol"))
            self.server._join_workers()
        self.assertEqual([e["type"] for e in self.stdout.events if e["id"] == "status"], ["accepted", "started"])
        self.assertEqual(self.stdout.result("protocol")["payload"], {"code": "internal_error"})
        self.assertTrue(self.server._stopping)

    def test_config_only_main_installs_and_restores_termination_handler(self):
        from cc_macos import server
        with patch.object(server, "startup_configuration", return_value=None), patch.object(server, "Server") as factory, \
                patch.object(server.signal, "signal", return_value=server.signal.SIG_DFL) as install:
            factory.return_value._translation_enabled = False
            factory.return_value._dictionary_enabled = True
            factory.return_value.run.return_value = 0
            self.assertEqual(server.main([]), 0)
            self.assertEqual(install.call_count, 2)

    def test_native_imports_do_not_load_provider_windows_or_rich_renderer(self):
        script = r"""
import importlib.abc, pathlib, sys
sys.path.insert(0, sys.argv[1])
blocked = {"cc_core", "cc_providers", "cc_rich", "cc_dictionary", "cc_dictionary_artifact", "tkinter", "win32api"}
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in blocked: raise AssertionError(fullname)
sys.meta_path.insert(0, Guard())
import cc_macos.dictionary, cc_macos.server
assert not blocked.intersection(sys.modules)
"""
        core = Path(configuration.__file__).resolve().parent.parent
        result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(core)],
                                cwd=self.home, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
