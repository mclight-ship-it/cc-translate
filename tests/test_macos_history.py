"""History business contracts using real temporary repositories, never user data."""

import io
import json
import os
from pathlib import Path
import re
import threading
import unittest
from unittest.mock import Mock, patch

import cc_history
from cc_macos import configuration, history
from cc_macos.protocol import MAX_FRAME_BYTES, ProtocolError, decode_frame, encode_frame
from cc_macos.server import Server

if __package__:
    from .test_macos_configuration import _ConfigurationDirectory, message
else:
    from test_macos_configuration import _ConfigurationDirectory, message


def addition(text="synthetic", **changes):
    return {"operation": "history_add", "input": text, "output": "out \u4e2d",
            "is_dict": False, "is_code": False, "kind": "text", "sig": "sig | unchanged",
            "limit": 100, **changes}


class HistoryRequestTests(unittest.TestCase):
    def test_load_optional_filters_keep_required_fields_and_strict_value_types(self):
        original = {"operation": "history_load", "page_size": 1, "cursor": None}
        for filters in ({}, {"query": ""}, {"kind": "all"}, *(
                {"query": " \u4e2d ", "kind": kind} for kind in ("all", "text", "dict", "code", "ocr"))):
            history.validate_history_request(original | filters)
        for filters in ({"query": None}, {"query": 1}, {"query": False}, {"query": []}, {"query": {}},
                        {"kind": None}, {"kind": ""}, {"kind": "ALL"}, {"kind": True}, {"kind": []},
                        {"query": "", "path": "private"}):
            with self.subTest(filters=filters), self.assertRaisesRegex(ProtocolError, "^invalid_payload$"):
                history.validate_history_request(original | filters)
        with self.assertRaisesRegex(ProtocolError, "^invalid_payload$"):
            history.validate_history_request({"operation": "history_load", "query": "", "kind": "all"})

    def test_query_uses_existing_full_history_utf8_budget_before_normalization(self):
        original = {"operation": "history_load", "page_size": 1, "cursor": None}
        for query in ("x" * 24000, "\u4e2d" * 8000, "\U0001f642" * 6000, " " * 24000):
            payload = original | {"query": query}
            history.validate_history_request(payload)
            self.assertEqual(decode_frame(encode_frame(message("query", "request", **payload)))["payload"], payload)
        for query in ("x" * 24001, "\u4e2d" * 8001, "\U0001f642" * 6001, " " * 24001, "\ud800"):
            with self.subTest(length=len(query)), self.assertRaisesRegex(ProtocolError, "^invalid_payload$"):
                history.validate_history_request(original | {"query": query})
        payload = original | {"query": "\0" * 24000}
        history.validate_history_request(payload)
        with self.assertRaisesRegex(ProtocolError, "^frame_too_large$"):
            encode_frame(message("escaped", "request", **payload))

    def test_only_exact_operations_fields_and_kinds_are_allowed(self):
        for kind in ("text", "dict", "code", "ocr"):
            history.validate_history_request(addition(kind=kind))
        for payload in (addition(kind=None), addition(kind="unknown"), addition(path="private"),
                        addition(ts="client"), {"operation": "history_clear", "all": True},
                        {"operation": "history_load", "page_size": 1}):
            with self.subTest(payload=payload), self.assertRaises(ProtocolError):
                history.validate_history_request(payload)

    def test_flags_limits_page_sizes_and_cursor_integers_are_not_coerced(self):
        for key in ("is_dict", "is_code"):
            for bad in (0, 1, None, "true", []):
                with self.assertRaises(ProtocolError):
                    history.validate_history_request(addition(**{key: bad}))
        for bad in (True, 0, -1, 10_001, 1.0, "1", None):
            with self.assertRaises(ProtocolError):
                history.validate_history_request(addition(limit=bad))
        for bad in (True, 0, 101, 1.0, "1"):
            with self.assertRaises(ProtocolError):
                history.validate_history_request({"operation": "history_load", "page_size": bad, "cursor": None})
        for offset in (True, 0, 10_001, 1.0, "1"):
            with self.assertRaises(ProtocolError):
                history.validate_history_request({"operation": "history_load", "page_size": 1,
                                                  "cursor": {"revision": "a" * 64, "offset": offset}})

    def test_utf8_limits_preserve_signature_and_do_not_count_characters(self):
        history.validate_history_request(addition(input="\u4e2d" * 8000, output="", sig="x" * 4096))
        for changes in ({"input": "\u4e2d" * 8001}, {"output": "x" * 24001}, {"sig": "x" * 4097},
                        {"input": None}, {"sig": "\ud800"}):
            with self.assertRaises(ProtocolError):
                history.validate_history_request(addition(**changes))

    def test_record_budget_counts_json_escaping_and_full_envelope(self):
        with self.assertRaisesRegex(ProtocolError, "history_entry_too_large"):
            history.validate_history_request(addition(input="\0" * 12000))
        entry = {"input": ""}
        overhead = len(history.page_frame(history.page_payload([entry], "0" * 64, 10000, 10000), "r" * 64, 2))
        entry["input"] = "x" * (MAX_FRAME_BYTES - overhead)
        history.validate_writable_entry(entry)
        self.assertEqual(len(history.page_frame(history.page_payload([entry], "0" * 64, 10000, 10000),
                                                "r" * 64, 2)), MAX_FRAME_BYTES)
        entry["input"] += "x"
        with self.assertRaisesRegex(ProtocolError, "history_entry_too_large"):
            history.validate_writable_entry(entry)

    def test_cursor_revision_is_exact_lower_hex_and_unknown_fields_fail(self):
        for cursor in ({}, {"revision": "A" * 64, "offset": 1}, {"revision": "g" * 64, "offset": 1},
                       {"revision": "0" * 63, "offset": 1}, {"revision": 0, "offset": 1},
                       {"revision": "0" * 64, "offset": 1, "path": "private"}, []):
            with self.assertRaisesRegex(ProtocolError, "invalid_history_cursor"):
                history.validate_history_request({"operation": "history_load", "page_size": 1, "cursor": cursor})

    def test_unknown_legacy_fields_are_lossless_json_not_config_documents(self):
        history.validate_entry({"kind": "legacy", "ts": None, "future": ["\u4e2d", False, 9007199254740991]})
        history.validate_entry({"input": "x" * 17000})
        for entry in ({"is_dict": 1}, {"ts": []}, {"future": float("inf")}, {"future": 9007199254740992}):
            with self.assertRaises(ValueError):
                history.validate_entry(entry)
        value = 0
        for _ in range(11):
            value = [value]
        history.validate_entry({"future": value})
        with self.assertRaises(ProtocolError):
            history.validate_entry({"future": [value]})


class HistoryServiceTests(_ConfigurationDirectory):
    def setUp(self):
        super().setUp()
        self.session.open()
        self.history_path = self.directory / "history.json"

    def call(self, payload, id_="request"):
        return self.session.perform_history(payload, id_, 2)

    def page(self, cursor=None, page_size=100, id_="request", **filters):
        return self.call({"operation": "history_load", "page_size": page_size, "cursor": cursor, **filters}, id_)

    def test_empty_all_filters_preserve_original_shape_revision_order_and_cursor(self):
        entries = [{"input": str(n), "future": [n], "kind": "legacy"} for n in range(3)]
        before = json.dumps(entries).encode()
        self.history_path.write_bytes(before)
        first = self.page(page_size=1)
        for filters in ({}, {"query": ""}, {"kind": "all"}, {"query": " \n\t\u3000", "kind": "all"}):
            self.assertEqual(self.page(page_size=1, **filters), first)
            second = self.page(first["next_cursor"], page_size=1, **filters)
            self.assertEqual(second["entries"], entries[1:2])
            self.assertEqual(second["revision"], first["revision"])
        self.assertEqual(set(first), {"entries", "revision", "total", "next_cursor"})
        self.assertEqual(set(first["next_cursor"]), {"revision", "offset"})
        self.assertEqual(self.history_path.read_bytes(), before)

    def test_search_filters_entire_snapshot_including_unloaded_input_output_and_timestamp(self):
        entries = [{"input": "row " + str(n), "output": "other", "ts": "2026-09-17"} for n in range(125)]
        for n, field in ((120, "input"), (121, "output"), (122, "ts")):
            entries[n][field] = "prefix Stra\u00dfe \u4e16\u754c suffix"
        entries[123]["sig"] = "strasse \u4e16\u754c"
        entries[124]["future"] = "strasse \u4e16\u754c"
        before = json.dumps(entries, ensure_ascii=False).encode()
        self.history_path.write_bytes(before)
        self.assertEqual(self.page()["entries"], entries[:100])
        with patch.object(history, "filter_history_entries", wraps=cc_history.filter_history_entries) as shared:
            first = self.page(page_size=2, query=" \tSTRASSE\u3000\u4e16\u754c\n")
        shared.assert_called_once()
        self.assertEqual(len(shared.call_args.args[0]), 125)
        self.assertEqual(first["entries"], entries[120:122])
        self.assertEqual(first["total"], 3)
        last = self.page(first["next_cursor"], query="strasse \u4e16\u754c", kind="all")
        self.assertEqual(last["entries"], entries[122:123])
        self.assertEqual(last["total"], 3)
        self.assertIsNone(last["next_cursor"])
        self.assertEqual(self.history_path.read_bytes(), before)

    def test_kind_filters_reuse_explicit_and_legacy_precedence_and_query_intersection(self):
        entries = [{"input": "needle0", "kind": "text", "is_code": True, "is_dict": True},
                   {"input": "needle1", "is_code": True, "is_dict": True},
                   {"input": "needle2", "kind": "legacy", "is_dict": True},
                   {"input": "needle3", "kind": "ocr", "is_dict": True},
                   {"input": "other", "kind": "dict"}, {"input": "needle5"}]
        self.history_path.write_text(json.dumps(entries), encoding="utf-8")
        for kind, indices in (("text", [0, 5]), ("code", [1]), ("dict", [2]), ("ocr", [3]),
                               ("all", [0, 1, 2, 3, 5])):
            page = self.page(query="NEEDLE", kind=kind)
            self.assertEqual(page["entries"], [entries[n] for n in indices])
            self.assertEqual(page["total"], len(indices))

    def test_filtered_cursor_binds_canonical_conditions_not_page_size_or_matching_subset_alone(self):
        entries = [{"input": "Alpha beta other", "kind": "dict"} for _ in range(3)] + [{"input": "excluded"}]
        self.history_path.write_text(json.dumps(entries), encoding="utf-8")
        first = self.page(page_size=1, query=" \tALPHA\u3000beta\n")
        cursor = first["next_cursor"]
        last = self.page(cursor, page_size=2, query="alpha beta", kind="all")
        self.assertEqual(last["entries"], entries[1:3])
        self.assertEqual(last["revision"], first["revision"])
        self.assertIsNone(last["next_cursor"])
        for filters in ({}, {"query": "other"}, {"query": "alpha beta", "kind": "dict"}):
            with self.subTest(filters=filters), self.assertRaisesRegex(
                    configuration.ConfigurationError, "^history_cursor_expired$"):
                self.page(cursor, **filters)
        plain = self.page(page_size=1)["next_cursor"]
        with self.assertRaisesRegex(configuration.ConfigurationError, "^history_cursor_expired$"):
            self.page(plain, query="alpha beta")
        with self.assertRaisesRegex(configuration.ConfigurationError, "^invalid_history_cursor$"):
            self.page(cursor | {"offset": 3}, query="alpha beta")

    def test_filtered_cursor_expires_when_excluded_data_or_revision_changes(self):
        self.history_path.write_text(json.dumps([{"input": "match1"}, {"input": "match2"}, {"input": "hidden"}]),
                                     encoding="utf-8")
        for change in ("add", "external_hidden", "external_whitespace", "reopen", "clear"):
            cursor = self.page(page_size=1, query="match")["next_cursor"]
            if change == "add":
                self.call(addition("excluded"))
            elif change == "external_hidden":
                entries = json.loads(self.history_path.read_bytes())
                entries[-1]["output"] = "changed hidden output"
                self.history_path.write_text(json.dumps(entries), encoding="utf-8")
            elif change == "external_whitespace":
                self.history_path.write_bytes(self.history_path.read_bytes() + b" ")
            elif change == "reopen":
                self.session.close()
                self.session = configuration.ConfigurationSession(self.home, self.identity)
                self.addCleanup(self.session.close)
                self.session.open()
            else:
                self.call({"operation": "history_clear"})
            with self.subTest(change=change), self.assertRaisesRegex(
                    configuration.ConfigurationError, "^history_cursor_expired$"):
                self.page(cursor, query="match")

    def test_filtered_unicode_pages_keep_byte_budget_all_results_and_filtered_totals(self):
        entries = [{"input": "match" + str(n), "output": "\u4e2d" * 6000} for n in range(6)]
        stored = [entry for match in entries for entry in (match, {"input": "excluded", "kind": "dict"})]
        before = json.dumps(stored, ensure_ascii=False).encode()
        self.history_path.write_bytes(before)
        cursor, found = None, []
        for _ in range(6):
            page = self.page(cursor, id_="r" * 64, query="match", kind="text")
            self.assertLessEqual(len(history.page_frame(page, "r" * 64, 2)), MAX_FRAME_BYTES)
            self.assertEqual(page["total"], 6)
            self.assertTrue(page["entries"])
            found.extend(page["entries"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertIsNone(cursor)
        self.assertEqual(found, entries)
        self.assertEqual(self.history_path.read_bytes(), before)

    def test_filtered_tail_cursor_removal_preserves_exact_frame_boundary(self):
        entries = [{"input": ""}, {}]
        overhead = len(history.page_frame(history.page_payload(entries, "0" * 64, 2, None), "r" * 64, 2))
        entries[0]["input"] = "x" * (MAX_FRAME_BYTES - overhead)
        self.history_path.write_text(json.dumps([{"kind": "dict"}, *entries]), encoding="utf-8")
        page = self.page(page_size=2, id_="r" * 64, kind="text")
        self.assertEqual(page["entries"], entries)
        self.assertEqual(page["total"], 2)
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(len(history.page_frame(page, "r" * 64, 2)), MAX_FRAME_BYTES)
        entries[0]["input"] += "x"
        before = json.dumps(entries).encode()
        self.history_path.write_bytes(before)
        with self.assertRaisesRegex(configuration.ConfigurationError, "^history_entry_too_large$"):
            self.page(page_size=2, id_="r" * 64, kind="text")
        self.assertEqual(self.history_path.read_bytes(), before)

    def test_filtered_nonmatches_do_not_hide_bad_history_permissions_or_file_limits(self):
        for raw in (b"{", b'[{"input":"other","is_dict":1}]', b'[{"input":"other","future":NaN}]'):
            self.history_path.write_bytes(raw)
            with self.assertRaisesRegex(configuration.ConfigurationError, "^invalid_history$"):
                self.page(query="missing", kind="ocr")
            self.assertEqual(self.history_path.read_bytes(), raw)
        self.history_path.write_bytes(b'[{"input":"other"}]')
        before = self.history_path.read_bytes()
        with patch("cc_macos.history.open", side_effect=PermissionError("PRIVATE"), create=True):
            with self.assertRaisesRegex(configuration.ConfigurationError, "^history_io_failed$"):
                self.page(query="missing")
        with patch.object(history, "MAX_HISTORY_FILE_BYTES", len(before) - 1):
            with self.assertRaisesRegex(configuration.ConfigurationError, "^history_too_large$"):
                self.page(query="missing")
        self.assertEqual(self.history_path.read_bytes(), before)
        self.assertEqual(self.page(query="missing")["entries"], [])

    def test_full_length_query_and_history_disabled_need_no_new_configuration_prerequisite(self):
        text = "\u4e2d" * 8000
        self.call(addition(text))
        self.session.perform({"operation": "config_save", "config": {"history_enabled": False}})
        page = self.page(query=text)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["entries"][0]["input"], text)

    def test_empty_history_is_read_without_creating_a_file_or_config(self):
        page = self.page()
        self.assertEqual(page["entries"], [])
        self.assertEqual(page["total"], 0)
        self.assertIsNone(page["next_cursor"])
        self.assertRegex(page["revision"], r"^[0-9a-f]{64}$")
        self.assertFalse(self.history_path.exists())
        self.assertFalse(self.path.exists())

    def test_add_uses_original_schema_time_order_limit_and_cache_rules(self):
        with patch.object(cc_history.time, "strftime", return_value="2026-09-14 14:00"):
            first = self.call(addition("first", kind="dict", is_dict=True))
            self.call(addition("second", kind="ocr", is_code=True, limit=2))
            self.call(addition("third", kind="code", is_code=True, limit=2))
        self.assertTrue(first["recorded"])
        page = self.page()
        self.assertEqual([e["input"] for e in page["entries"]], ["third", "second"])
        entry = page["entries"][0]
        self.assertEqual(list(entry), ["ts", "input", "output", "is_dict", "is_code", "kind", "sig"])
        self.assertEqual(entry, {"ts": "2026-09-14 14:00", "input": "third", "output": "out \u4e2d",
                                 "is_dict": False, "is_code": True, "kind": "code", "sig": "sig | unchanged"})
        repository = self.session._history._owner
        self.assertEqual(repository.find_cached(" third ", "code", "sig | unchanged"), "out \u4e2d")
        self.assertIsNone(repository.find_cached("second", "ocr", "sig | unchanged"))
        expected = json.dumps(page["entries"], ensure_ascii=False, indent=2).replace("\n", os.linesep).encode()
        self.assertEqual(self.history_path.read_bytes(), expected)

    def test_revision_pagination_expires_after_add_clear_external_change_and_reopen(self):
        for text in ("a", "b", "c"):
            self.call(addition(text))
        first = self.page(page_size=1)
        second = self.page(first["next_cursor"], page_size=1)
        last = self.page(second["next_cursor"], page_size=1)
        self.assertEqual([p["entries"][0]["input"] for p in (first, second, last)], ["c", "b", "a"])
        self.assertIsNone(last["next_cursor"])
        self.session.perform({"operation": "config_save", "config": {"future": "configuration only"}})
        self.assertEqual(self.page(first["next_cursor"], 1)["revision"], first["revision"])
        for operation in ("add", "external", "reopen", "clear"):
            cursor = self.page(page_size=1)["next_cursor"]
            if operation == "add":
                self.call(addition("new"))
            elif operation == "external":
                self.history_path.write_bytes(self.history_path.read_bytes() + b" ")
            elif operation == "reopen":
                self.session.close()
                self.session = configuration.ConfigurationSession(self.home, self.identity)
                self.addCleanup(self.session.close)
                self.session.open()
            else:
                self.call({"operation": "history_clear"})
            with self.subTest(operation=operation), self.assertRaisesRegex(
                    configuration.ConfigurationError, "^history_cursor_expired$"):
                self.page(cursor)
        self.assertFalse(self.history_path.exists())

    def test_identical_successful_add_and_empty_clear_still_advance_revision(self):
        with patch.object(cc_history.time, "strftime", return_value="2026-09-14 14:00"):
            first = self.call(addition(limit=1))
            second = self.call(addition(limit=1))
        self.assertNotEqual(first["revision"], second["revision"])
        first = self.call({"operation": "history_clear"})
        second = self.call({"operation": "history_clear"})
        self.assertNotEqual(first["revision"], second["revision"])

    def test_pages_fit_real_envelope_and_never_silently_drop_unicode_entries(self):
        for n in range(6):
            self.call(addition(str(n), output="\u4e2d" * 6000))
        cursor, found = None, []
        while True:
            page = self.page(cursor, id_="r" * 64)
            raw = history.page_frame(page, "r" * 64, 2)
            self.assertLessEqual(len(raw), MAX_FRAME_BYTES)
            self.assertGreater(len(raw), 16384)
            found.extend(e["input"] for e in page["entries"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(found, ["5", "4", "3", "2", "1", "0"])

    def test_exact_single_entry_frame_boundary_and_one_byte_over_preserve_file(self):
        empty = {"input": ""}
        overhead = len(history.page_frame(history.page_payload([empty], "0" * 64, 1, None), "r" * 64, 2))
        entry = {"input": "x" * (MAX_FRAME_BYTES - overhead)}
        self.history_path.write_text(json.dumps([entry]), encoding="utf-8")
        page = self.page(id_="r" * 64)
        self.assertEqual(len(history.page_frame(page, "r" * 64, 2)), MAX_FRAME_BYTES)
        entry["input"] += "x"
        before = json.dumps([entry]).encode()
        self.history_path.write_bytes(before)
        with self.assertRaisesRegex(configuration.ConfigurationError, "^history_entry_too_large$"):
            self.page(id_="r" * 64)
        self.assertEqual(self.history_path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])

    def test_tail_cursor_removal_can_make_a_larger_prefix_fit_exactly(self):
        for leading in ([], [{}]):
            entries = [*leading, {"input": ""}, {}]
            overhead = len(history.page_frame(
                history.page_payload(entries, "0" * 64, len(entries), None), "r" * 64, 2))
            entries[-2]["input"] = "x" * (MAX_FRAME_BYTES - overhead)
            before = json.dumps(entries).encode()
            self.history_path.write_bytes(before)
            with self.subTest(leading=bool(leading)):
                page = self.page(page_size=len(entries), id_="r" * 64)
                self.assertEqual(len(page["entries"]), len(entries))
                self.assertEqual(page["entries"], entries)
                self.assertIsNone(page["next_cursor"])
                self.assertEqual(len(history.page_frame(page, "r" * 64, 2)), MAX_FRAME_BYTES)
                self.assertEqual(self.history_path.read_bytes(), before)

    def test_bad_file_and_unknown_unsafe_fields_never_become_empty_or_are_overwritten(self):
        for before in (b"{", b"\xff", b"null", b"{}", b"[1]", b'[{"input":1}]',
                       b'[{"x":1,"x":2}]', b'[{"x":NaN}]', b'[{"future":9007199254740992}]'):
            self.history_path.write_bytes(before)
            for operation in (lambda: self.page(), lambda: self.call(addition())):
                with self.subTest(before=before), self.assertRaisesRegex(
                        configuration.ConfigurationError, "^invalid_history$"):
                    operation()
                self.assertEqual(self.history_path.read_bytes(), before)
                self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])
        self.call({"operation": "history_clear"})
        self.assertFalse(self.history_path.exists())

    def test_permission_errors_are_fixed_and_do_not_modify_prior_data(self):
        self.call(addition())
        before = self.history_path.read_bytes()
        with patch("cc_macos.history.open", side_effect=PermissionError("private path"), create=True):
            with self.assertRaisesRegex(configuration.ConfigurationError, "^history_io_failed$"):
                self.page()
        self.assertEqual(self.history_path.read_bytes(), before)

    def test_writer_validates_actual_future_file_before_atomic_replace(self):
        self.call(addition())
        before = self.history_path.read_bytes()
        with patch.object(history, "MAX_HISTORY_FILE_BYTES", len(before)), \
                patch.object(history, "atomic_write_json") as writer:
            with self.assertRaisesRegex(configuration.ConfigurationError, "^history_too_large$"):
                self.call(addition("second"))
            writer.assert_not_called()
        self.assertEqual(self.history_path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])

    def test_oversize_file_and_count_reject_without_truncating(self):
        for before in (b" " * (history.MAX_HISTORY_FILE_BYTES + 1),
                       json.dumps([{}] * (history.MAX_HISTORY_ENTRIES + 1)).encode()):
            self.history_path.write_bytes(before)
            with self.assertRaisesRegex(configuration.ConfigurationError, "^history_too_large$"):
                self.page()
            self.assertEqual(self.history_path.read_bytes(), before)

    def test_invalid_current_revision_offset_is_explicit(self):
        self.call(addition())
        revision = self.page()["revision"]
        with self.assertRaisesRegex(configuration.ConfigurationError, "^invalid_history_cursor$"):
            self.page({"revision": revision, "offset": 1})

    def test_close_rejects_later_history_operations(self):
        self.session.close()
        for payload in (addition(), {"operation": "history_clear"},
                        {"operation": "history_load", "page_size": 1, "cursor": None}):
            with self.assertRaisesRegex(configuration.ConfigurationError, "^history_unavailable$"):
                self.call(payload)


class BusinessOwnershipTests(_ConfigurationDirectory):
    def test_second_owner_initialization_failure_closes_first_owner(self):
        error = history.HistoryError("history_in_use")
        with patch.object(configuration, "HistoryService", side_effect=error):
            with self.assertRaisesRegex(configuration.ConfigurationError, "^history_in_use$"):
                self.session.open()
        self.assertIsNone(self.session._owner)
        self.assertIsNone(self.session._history)
        self.assertTrue(self.session._closed)

    def test_partial_initialization_close_error_is_fixed_not_hidden(self):
        owner = Mock()
        owner.close.side_effect = OSError("private failure")
        self.factory.side_effect = None
        self.factory.return_value = owner
        with patch.object(configuration, "HistoryService", side_effect=history.HistoryError("history_in_use")):
            with self.assertRaisesRegex(configuration.ConfigurationError, "^state_io_failed$"):
                self.session.open()
        owner.close.assert_called_once()
        self.session.close()
        owner.close.assert_called_once()

    def test_close_attempts_both_owners_once_when_history_close_fails(self):
        self.session.open()
        config_owner, history_owner = self.session._owner, self.session._history
        with patch.object(history_owner, "close", side_effect=OSError("private failure")) as close_history, \
                patch.object(config_owner, "close", wraps=config_owner.close) as close_config:
            with self.assertRaises(OSError):
                self.session.close()
            self.session.close()
            close_history.assert_called_once()
            close_config.assert_called_once()

    def test_first_owner_failure_never_attempts_history_owner(self):
        self.factory.side_effect = configuration.ConfigInUseError("private failure")
        with patch.object(configuration, "HistoryService") as second:
            with self.assertRaisesRegex(configuration.ConfigurationError, "^config_in_use$"):
                self.session.open()
            second.assert_not_called()


class HistorySchedulingTests(_ConfigurationDirectory):
    def test_filtered_load_observes_committed_add_in_existing_history_fifo(self):
        output = io.BytesIO()
        server = Server(io.BytesIO(), output, io.StringIO(), configuration=self.session)
        server._handle(message("hello", "hello"))
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        actual_write, actual_send = history.atomic_write_json, server._send

        def blocked(path, entries):
            entered.set()
            self.assertTrue(release.wait(3))
            actual_write(path, entries)

        def observed(request, event, payload):
            actual_send(request, event, payload)
            if request.id == "search" and event == "completed":
                completed.set()

        with patch.object(history, "atomic_write_json", side_effect=blocked), \
                patch.object(server, "_send", side_effect=observed):
            try:
                server._handle(message("add", "request", **addition("needle")))
                self.assertTrue(entered.wait(3))
                server._handle(message("search", "request", operation="history_load",
                                       page_size=1, cursor=None, query="NEEDLE", kind="text"))
                self.assertFalse(completed.is_set())
            finally:
                release.set()
                finished = completed.wait(3)
                server._stop()
                server._join_workers()
        events = [decode_frame(line + b"\n") for line in output.getvalue().splitlines()]
        self.assertTrue(finished)
        result = next(e["payload"] for e in events if e["id"] == "search" and e["type"] == "completed")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["entries"][0]["input"], "needle")
        self.assertLess(next(i for i, e in enumerate(events) if e["id"] == "add" and e["type"] == "completed"),
                        next(i for i, e in enumerate(events) if e["id"] == "search" and e["type"] == "started"))

    def test_started_add_then_clear_are_fifo_and_queued_cancel_never_writes(self):
        output = io.BytesIO()
        server = Server(io.BytesIO(), output, io.StringIO(), configuration=self.session)
        self.assertTrue(server._handle(message("hello", "hello")))
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        actual_write = history.atomic_write_json
        actual_send = server._send

        def blocked(path, entries):
            entered.set()
            self.assertTrue(release.wait(3))
            actual_write(path, entries)

        def observed(request, event, payload):
            actual_send(request, event, payload)
            if request.id == "clear" and event == "completed":
                completed.set()

        with patch.object(history, "atomic_write_json", side_effect=blocked), \
                patch.object(server, "_send", side_effect=observed):
            try:
                server._handle(message("add", "request", **addition()))
                self.assertTrue(entered.wait(3))
                server._handle(message("cancel_active", "cancel", request_id="add"))
                server._handle(message("queued", "request", **addition("never")))
                server._handle(message("cancel_queued", "cancel", request_id="queued"))
                server._handle(message("clear", "request", operation="history_clear"))
                self.assertFalse(completed.is_set())
            finally:
                release.set()
            self.assertTrue(completed.wait(3))
        server._stop()
        server._join_workers()
        self.session.close()
        events = [decode_frame(line + b"\n") for line in output.getvalue().splitlines()]
        self.assertEqual([e["type"] for e in events if e["id"] == "add"], ["accepted", "started", "completed"])
        self.assertEqual([e["type"] for e in events if e["id"] == "queued"], ["accepted", "cancelled"])
        self.assertEqual(next(e["payload"] for e in events if e["id"] == "cancel_active"),
                         {"cancel_requested": False})
        self.assertFalse((self.directory / "history.json").exists())
