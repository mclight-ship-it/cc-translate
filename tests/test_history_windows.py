"""Actual Windows entry points compared with the frozen pre-extraction I/O."""

from concurrent.futures import ThreadPoolExecutor
import itertools
import json
import threading
from unittest import mock

import cc_history as history
import cc_storage
from tests import history_reference
from tests.test_history import ObservedLock
from tests.test_storage_windows import StorageTestCase, tr


class TestWindowsHistoryRepository(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.reference_path = self.root / "reference.json"
        self.reference_log = mock.Mock()
        self.reference = history_reference.namespace(str(self.reference_path), self.reference_log)
        clock = mock.patch.object(history.time, "strftime", return_value="2026-09-13 23:58")
        clock.start()
        self.addCleanup(clock.stop)

    def test_all_public_entries_construct_the_same_repository_with_one_lock(self):
        with mock.patch.object(tr, "HistoryRepository", wraps=history.HistoryRepository) as factory:
            tr.add_history("word", "out", True, 3, kind="dict", sig="sig")
            self.assertEqual(tr.load_history()[0]["input"], "word")
            self.assertEqual(tr.find_cached_translation("word", "dict", "sig"), "out")
            tr.clear_history()
        self.assertGreaterEqual(factory.call_count, 6)
        for call in factory.call_args_list:
            self.assertEqual(call.args[0], str(self.history))
            self.assertIs(call.kwargs["lock"], tr._HISTORY_LOCK)
        self.assertFalse(self.history.exists())
        self.assertIs(tr._atomic_write_json, cc_storage.atomic_write_json)

    def test_add_bytes_match_legacy_matrix(self):
        cases = itertools.product((None, "", "ocr", "text", "dict", "code", "invalid"),
                                  (False, True), (False, True), (None, "sig"), (0, -2, 1, "4"))
        for kind, is_dict, is_code, sig, limit in cases:
            with self.subTest(kind=kind, flags=(is_dict, is_code), sig=sig, limit=limit):
                args = ("\u4e2d # %", " out\n", is_dict, limit)
                kwargs = dict(is_code=is_code, kind=kind, sig=sig)
                self.reference["add_history"](*args, **kwargs)
                tr.add_history(*args, **kwargs)
                self.assertEqual(self.history.read_bytes(), self.reference_path.read_bytes(),
                                 self.log.read_text(encoding="utf-8") if self.log.exists() else "")
        self.reference_log.assert_not_called()
        self.assertFalse(self.log.exists())

    def test_optional_values_and_existing_fields_match_legacy(self):
        legacy = [{"input": "old", "output": "out", "extra": 7}]
        self.seed(self.history, legacy)
        self.seed(self.reference_path, legacy)
        self.reference["add_history"](None, None, False, 3)
        tr.add_history(None, None, False, 3)
        self.assertEqual(self.history.read_bytes(), self.reference_path.read_bytes())
        self.assertEqual(tr.load_history()[1], legacy[0])

    def test_bad_limit_error_and_file_match_legacy(self):
        for limit in ("invalid", None, []):
            with self.subTest(limit=limit):
                for path in (self.history, self.reference_path):
                    self.seed(path, [])
                errors = []
                for operation in (self.reference["add_history"], tr.add_history):
                    try:
                        operation("new", "out", False, limit)
                    except (TypeError, ValueError) as error:
                        errors.append((type(error), str(error)))
                self.assertEqual(len(errors), 2)
                self.assertEqual(errors[0], errors[1])
                self.assertEqual(self.history.read_bytes(), self.reference_path.read_bytes())
                self.assert_no_temps()

    def test_read_compatibility_and_log_categories_match_legacy(self):
        for payload in (b"{", b"\xff", b"{}", b"null", b"[1]", b"[]"):
            with self.subTest(payload=payload):
                self.history.write_bytes(payload)
                self.reference_path.write_bytes(payload)
                self.reference_log.reset_mock()
                with mock.patch.object(tr, "log_error") as logger:
                    self.assertEqual(tr.load_history(), self.reference["load_history"]())
                self.assertEqual(logger.call_count, self.reference_log.call_count)
                if logger.call_count:
                    actual, expected = logger.call_args.args, self.reference_log.call_args.args
                    self.assertEqual((actual[0], type(actual[1]), str(actual[1])),
                                     (expected[0], type(expected[1]), str(expected[1])))
                self.assertEqual(self.history.read_bytes(), payload)

    def test_read_permission_error_keeps_legacy_log_and_empty_view(self):
        self.seed(self.history, [])
        with mock.patch("builtins.open", side_effect=PermissionError("synthetic")), \
                mock.patch.object(tr, "log_error") as logger:
            self.assertEqual(tr.load_history(), self.reference["load_history"]())
        self.assertEqual(logger.call_args.args[0], "load_history")
        self.assertEqual(type(logger.call_args.args[1]), type(self.reference_log.call_args.args[1]))

    def test_legacy_corrupt_read_then_add_policy_is_windows_only(self):
        self.history.write_bytes(b"{broken")
        self.reference_path.write_bytes(b"{broken")
        self.reference["add_history"]("new", "out", False, 3)
        tr.add_history("new", "out", False, 3)
        self.assertEqual(self.history.read_bytes(), self.reference_path.read_bytes())
        self.assertIn("[load_history] JSONDecodeError:", self.log.read_text(encoding="utf-8"))
        self.assertEqual(self.reference_log.call_args.args[0], "load_history")

    def test_writer_error_still_logs_only_add_and_keeps_previous_file(self):
        self.seed(self.history, [])
        self.seed(self.reference_path, [])
        error = RuntimeError("synthetic writer")
        self.reference["_atomic_write_json"] = mock.Mock(side_effect=error)
        with mock.patch.object(tr, "_atomic_write_json", side_effect=error), \
                mock.patch.object(tr, "log_error") as logger:
            tr.add_history("new", "out", False, 3)
            self.reference["add_history"]("new", "out", False, 3)
        logger.assert_called_once_with("add_history", error)
        self.reference_log.assert_called_once_with("add_history", error)
        self.assertEqual(self.history.read_bytes(), self.reference_path.read_bytes())

    def test_public_load_injection_remains_used_by_append_and_cache(self):
        entries = [{"input": "legacy", "output": "cached", "kind": "text", "sig": ""}]
        with mock.patch.object(tr, "load_history", side_effect=lambda: [dict(e) for e in entries]) as loader:
            self.assertEqual(tr.find_cached_translation("legacy", "text", None), "cached")
            tr.add_history("new", "out", False, 10)
        self.assertEqual(loader.call_count, 2)
        self.assertEqual(tr.load_history()[1], entries[0])

    def test_cache_matrix_matches_legacy(self):
        entries = [
            {"input": "word", "output": " ", "kind": "text", "sig": "S"},
            {"input": " word ", "output": " older ", "kind": "text", "sig": "S"},
            {"input": "word", "output": "dictionary", "kind": "dict"},
            {"input": "scan", "output": "OCR", "kind": "ocr"},
        ]
        self.seed(self.history, entries)
        self.seed(self.reference_path, entries)
        for text, kind, sig in itertools.product((None, "", " ", "word", " word ", "scan"),
                                                 ("text", "dict", "code", "ocr", "bad"), (None, "", "S", "T")):
            self.assertEqual(tr.find_cached_translation(text, kind, sig),
                             self.reference["find_cached_translation"](text, kind, sig))

    def test_invalid_cache_entry_error_is_not_swallowed(self):
        self.seed(self.history, [{"input": 1, "kind": "text"}])
        self.seed(self.reference_path, [{"input": 1, "kind": "text"}])
        for operation in (tr.find_cached_translation, self.reference["find_cached_translation"]):
            with self.assertRaises(AttributeError):
                operation("word", "text", "")

    def test_ocr_cache_rejection_preserves_no_read_short_circuit(self):
        with mock.patch.object(tr, "load_history", side_effect=AssertionError("must not read")):
            self.assertIsNone(tr.find_cached_translation("scan", "ocr", "sig"))
            self.assertIsNone(tr.find_cached_translation(" ", "text", "sig"))

    def test_clear_waits_at_the_shared_lock_before_removing_inflight_append(self):
        lock = ObservedLock()
        entered, release = threading.Event(), threading.Event()

        def writer(path, entries):
            lock.observe = True
            entered.set()
            if not release.wait(5):
                raise AssertionError("writer release missing")
            cc_storage.atomic_write_json(path, entries)

        with mock.patch.object(tr, "_HISTORY_LOCK", lock), \
                mock.patch.object(tr, "_atomic_write_json", writer), \
                ThreadPoolExecutor(max_workers=2) as workers:
            append = workers.submit(tr.add_history, "in-flight", "out", False, 10)
            try:
                self.assertTrue(entered.wait(3))
                clear = workers.submit(tr.clear_history)
                self.assertTrue(lock.waiter.wait(3), "clear bypassed the append lock")
                self.assertFalse(clear.done())
            finally:
                release.set()
            append.result(timeout=3)
            clear.result(timeout=3)
        self.assertFalse(self.history.exists())
        self.assertFalse(self.log.exists())
        tr.add_history("after-clear", "allowed", False, 10)
        self.assertEqual(tr.load_history()[0]["input"], "after-clear")

    def test_record_history_keeps_current_enable_and_stale_job_policies(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = {tr.CFG.HISTORY_ENABLED: True, tr.CFG.HISTORY_LIMIT: 2}
        app._job_is_current = lambda job_id: job_id == 3
        meta = {"input": "original", "origin": "text", "is_code": True, "kind": "code", "sig": "sig"}
        app.cfg[tr.CFG.HISTORY_ENABLED] = False
        app._record_history(3, meta, "disabled", False)
        app.cfg[tr.CFG.HISTORY_ENABLED] = True
        app._record_history(2, meta, "stale", False)
        self.assertFalse(self.history.exists())
        app._record_history(3, meta, "kept", False)
        entry, = tr.load_history()
        self.assertEqual((entry["input"], entry["output"], entry["kind"], entry["sig"]),
                         ("original", "kept", "code", "sig"))
        app._record_history(3, dict(meta, input="", origin="ocr", kind="ocr"), "scan", False)
        self.assertEqual(tr.load_history()[0]["kind"], "ocr")
        self.assertIsNone(tr.find_cached_translation("", "ocr", "sig"))

    def test_clear_still_logs_and_preserves_on_remove_failure(self):
        self.seed(self.history, [])
        with mock.patch.object(history.os, "remove", side_effect=PermissionError("synthetic")):
            tr.clear_history()
        self.assertTrue(self.history.exists())
        self.assertIn("[clear_history] PermissionError: synthetic", self.log.read_text(encoding="utf-8"))
