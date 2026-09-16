"""Portable, real-file repository and controlled operation-ownership regressions."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import cc_history as history
import cc_storage


class TestHistoryFiltering(unittest.TestCase):
    def test_kind_keeps_explicit_values_then_code_and_dictionary_flags(self):
        for kind in ("text", "dict", "code", "ocr"):
            self.assertEqual(history.history_entry_kind({"kind": kind, "is_code": True, "is_dict": True}), kind)
        for entry, expected in ((None, "text"), ({}, "text"), ({"kind": "future"}, "text"),
                                ({"kind": "future", "is_code": True, "is_dict": True}, "code"),
                                ({"kind": None, "is_dict": True}, "dict"),
                                ({"is_code": "legacy truthy flag"}, "code")):
            self.assertEqual(history.history_entry_kind(entry), expected)

    def test_all_and_legacy_falsy_queries_keep_order_identity_and_values(self):
        entries = [{"input": None, "future": [1]}, {"is_dict": True}, None]
        before = json.dumps(entries)
        for query in ("", None, False, " \t\n\u3000"):
            for kind in ("all", "invalid", None, "DICT"):
                result = history.filter_history_entries(entries, query, kind)
                self.assertEqual(result, entries)
                self.assertIsNot(result, entries)
                for actual, original in zip(result, entries):
                    self.assertIs(actual, original)
        self.assertEqual(json.dumps(entries), before)
        self.assertEqual(history.filter_history_entries(None), [])

    def test_unicode_casefolded_substring_search_covers_input_output_and_timestamp_only(self):
        entries = [{"input": "prefix Stra\u00dfe \u4e16\u754c suffix"}, {"output": "STRASSE \u4e16\u754c"},
                   {"ts": "strasse \u4e16\u754c"}, {"sig": "strasse \u4e16\u754c"},
                   {"future": "strasse \u4e16\u754c"}, {"kind": "text", "input": None}]
        query = " \tSTRASSE\u3000\u4e16\u754c\n"
        self.assertEqual(history.normalize_history_query(query), "strasse \u4e16\u754c")
        self.assertEqual(history.filter_history_entries(entries, query), entries[:3])
        self.assertEqual(history.filter_history_entries(entries, "\u4e16"), entries[:3])

    def test_windows_query_whitespace_is_normalized_but_record_whitespace_and_unicode_are_not(self):
        entries = [{"input": "Hello world"}, {"input": "hello\tworld"}, {"input": "hello", "output": "world"},
                   {"input": "Caf\u00e9"}, {"input": "Cafe\u0301"}, {"input": "word.*"}]
        self.assertEqual(history.filter_history_entries(entries, " HELLO \n world "), entries[:1])
        self.assertEqual(history.filter_history_entries(entries, "CAF\u00c9"), entries[3:4])
        self.assertEqual(history.filter_history_entries(entries, ".*"), entries[5:])
        self.assertEqual(history.filter_history_entries(entries, "hello missing"), [])

    def test_kind_and_query_intersect_with_legacy_fallback(self):
        entries = [{"input": "needle", "is_code": True, "is_dict": True},
                   {"input": "needle", "kind": "text", "is_dict": True},
                   {"output": "needle", "kind": "legacy", "is_dict": True},
                   {"input": "needle", "kind": "ocr"}, {"input": "other", "kind": "dict"}]
        for kind, expected in (("code", entries[:1]), ("text", entries[1:2]),
                               ("dict", entries[2:3]), ("ocr", entries[3:4]), ("all", entries[:4])):
            self.assertEqual(history.filter_history_entries(entries, "NEEDLE", kind), expected)


class ObservedLock:
    def __init__(self):
        self.inner = threading.RLock()
        self.observe = False
        self.waiter = threading.Event()

    def __enter__(self):
        if self.observe:
            self.waiter.set()
        self.inner.acquire()
        return self

    def __exit__(self, *args):
        self.inner.release()


class TestHistoryRepository(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "history \u4e2d # %.json"
        self.repo = history.HistoryRepository(self.path, lock=ObservedLock())
        self.addCleanup(self.repo.close)

    def test_constructor_and_close_do_not_touch_disk(self):
        self.assertEqual(self.repo.path, self.path)
        self.repo.close()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_history_and_reopen(self):
        self.assertEqual(self.repo.load(), [])
        self.repo.add("hello", "\u4e2d", False, 10)
        with history.HistoryRepository(self.path) as reopened:
            self.assertEqual(reopened.load(), self.repo.load())

    def test_exact_serialized_bytes_and_field_order(self):
        with mock.patch.object(history.time, "strftime", return_value="2026-09-13 23:58") as clock:
            self.repo.add(" \u4e2d ", "out\n", False, 3, kind="ocr", sig="rev|0")
        clock.assert_called_once_with("%Y-%m-%d %H:%M")
        expected = [{
            "ts": "2026-09-13 23:58", "input": " \u4e2d ", "output": "out\n",
            "is_dict": False, "is_code": False, "kind": "ocr", "sig": "rev|0",
        }]
        self.assertEqual(self.path.read_text(encoding="utf-8"),
                         json.dumps(expected, ensure_ascii=False, indent=2))
        self.assertEqual(list(self.repo.load()[0]), list(expected[0]))

    def test_kind_flags_limit_and_empty_values(self):
        for kind in (None, "", "invalid", "text", "dict", "code", "ocr"):
            for is_dict in (False, True):
                for is_code in (False, True):
                    with self.subTest(kind=kind, is_dict=is_dict, is_code=is_code):
                        self.repo.add(None, None, is_dict, 0, is_code, kind, None)
                        entry, = self.repo.load()
                        expected = kind if kind in ("text", "dict", "code", "ocr") else (
                            "code" if is_code else "dict" if is_dict else "text")
                        self.assertEqual((entry["kind"], entry["input"], entry["output"], entry["sig"]),
                                         (expected, "", "", ""))

    def test_latest_first_and_limit(self):
        for number in range(7):
            self.repo.add(str(number), "out", False, "3")
        self.assertEqual([entry["input"] for entry in self.repo.load()], ["6", "5", "4"])

    def test_bad_limit_keeps_old_file_and_propagates(self):
        self.repo.add("old", "out", False, 3)
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.repo.add("new", "out", False, "invalid")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".tmp_*")), [])

    def test_cache_matches_stripped_text_kind_signature_and_nonblank_output(self):
        self.repo.add(" key ", " old ", False, 10, kind="text", sig="S")
        self.repo.add("key", "   ", False, 10, kind="text", sig="S")
        self.assertEqual(self.repo.find_cached("  key  ", "text", "S"), "old")
        self.assertIsNone(self.repo.find_cached("key", "dict", "S"))
        self.assertIsNone(self.repo.find_cached("key", "text", "T"))
        self.repo.add("key", "new", False, 10, kind="text", sig="S")
        self.assertEqual(self.repo.find_cached("key", "text", "S"), "new")

    def test_cache_ocr_and_empty_queries_do_not_read(self):
        with mock.patch.object(self.repo, "_read", side_effect=AssertionError("unexpected read")):
            for text, kind in (("", "text"), (None, "text"), ("  ", "dict"), ("x", "ocr"), ("x", "invalid")):
                self.assertIsNone(self.repo.find_cached(text, kind, "sig"))

    def test_legacy_optional_fields_and_unknown_fields_preserved(self):
        legacy = {"input": "legacy", "output": " value ", "kind": "text", "extra": [1]}
        cc_storage.atomic_write_json(self.path, [legacy])
        self.assertEqual(self.repo.find_cached("legacy", "text", None), "value")
        self.repo.add("new", "out", False, 10)
        self.assertEqual(self.repo.load()[1], legacy)
        self.assertEqual(list(self.repo.load()[1]), list(legacy))

    def test_corrupt_file_is_not_overwritten(self):
        for payload in (b"{broken", b"\xff"):
            with self.subTest(payload=payload):
                self.path.write_bytes(payload)
                with self.assertRaises((ValueError, UnicodeError)):
                    self.repo.add("new", "out", False, 3)
                self.assertEqual(self.path.read_bytes(), payload)
                self.assertEqual(list(self.root.glob(".tmp_*")), [])

    def test_invalid_structure_is_not_overwritten(self):
        for payload in ({}, None, 5, [1], [{"input": 1}], [{"sig": []}], [{"is_code": 1}]):
            with self.subTest(payload=payload):
                cc_storage.atomic_write_json(self.path, payload)
                before = self.path.read_bytes()
                with self.assertRaises(history.HistoryFormatError):
                    self.repo.add("new", "out", False, 3)
                self.assertEqual(self.path.read_bytes(), before)

    def test_read_failure_propagates_without_writing(self):
        self.repo.add("old", "out", False, 3)
        before = self.path.read_bytes()
        with mock.patch.object(history, "open", create=True, side_effect=PermissionError("synthetic")):
            with self.assertRaises(PermissionError):
                self.repo.add("new", "out", False, 3)
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_atomic_replace_preserves_file_and_cleans_only_own_temp(self):
        self.repo.add("old", "out", False, 3)
        before = self.path.read_bytes()
        neighbour = self.root / ".tmp_neighbour.json"
        neighbour.write_bytes(b"other operation")
        with mock.patch.object(cc_storage.os, "replace", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                self.repo.add("new", "out", False, 3)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".tmp_*")), [neighbour])

    def test_clear_removes_file_and_missing_clear_is_idempotent(self):
        self.path.write_bytes(b"broken")
        self.repo.clear()
        self.repo.clear()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.repo.load(), [])

    def test_clear_error_keeps_file(self):
        self.repo.add("old", "out", False, 3)
        before = self.path.read_bytes()
        with mock.patch.object(history.os, "remove", side_effect=PermissionError("synthetic")):
            with self.assertRaises(PermissionError):
                self.repo.clear()
        self.assertEqual(self.path.read_bytes(), before)

    def test_close_rejects_every_operation_including_empty_cache(self):
        self.repo.close()
        for operation in (self.repo.load, self.repo.clear, self.repo.__enter__,
                          lambda: self.repo.add("new", "out", False, 3),
                          lambda: self.repo.find_cached("", "ocr", "")):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(RuntimeError, "closed"):
                    operation()
        self.repo.close()

    def test_context_exit_closes_after_body_failure(self):
        with self.assertRaisesRegex(ValueError, "synthetic"):
            with self.repo:
                raise ValueError("synthetic")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.repo.load()

    def test_concurrent_appends_keep_all_entries(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda n: self.repo.add(str(n), "out", False, 20), range(16)))
        self.assertEqual({entry["input"] for entry in self.repo.load()}, {str(n) for n in range(16)})

    def _blocked_writer(self, operation):
        entered, release = threading.Event(), threading.Event()

        def writer(path, data):
            self.repo._lock.observe = True
            entered.set()
            if not release.wait(5):
                raise AssertionError("controlled writer was not released")
            cc_storage.atomic_write_json(path, data)

        self.repo._writer = writer
        with ThreadPoolExecutor(max_workers=2) as workers:
            append = workers.submit(self.repo.add, "in-flight", "out", False, 3)
            try:
                self.assertTrue(entered.wait(3))
                other = workers.submit(operation)
                self.assertTrue(self.repo._lock.waiter.wait(3), "competing operation did not acquire the shared lock")
                self.assertFalse(other.done())
            finally:
                release.set()
            append.result(timeout=3)
            other.result(timeout=3)

    def test_clear_waits_for_ongoing_append_then_removes_it(self):
        self._blocked_writer(self.repo.clear)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.repo.load(), [])
        self.repo.add("after-clear", "new", False, 3)
        self.assertEqual(self.repo.load()[0]["input"], "after-clear")

    def test_close_waits_for_ongoing_append_then_rejects_writes(self):
        self._blocked_writer(self.repo.close)
        self.assertEqual(history.read_history(self.path)[0]["input"], "in-flight")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.repo.add("after-close", "out", False, 3)

    def test_explicit_shared_lock_serializes_separate_facades(self):
        other = history.HistoryRepository(self.path, lock=self.repo._lock)
        self.addCleanup(other.close)
        self._blocked_writer(other.clear)
        self.assertFalse(self.path.exists())
