import json
import unittest
from unittest.mock import patch

from cc_providers.claude_jsonl import ClaudeOutput, ClaudeOutputError


class TestClaudeOutput(unittest.TestCase):
    def setUp(self):
        self.deltas = []
        self.output = ClaudeOutput(self.deltas.append)

    def feed(self, message):
        self.output.line(json.dumps(message, ensure_ascii=False))

    def event(self, event):
        self.feed({"type": "stream_event", "event": event})

    def delta(self, text):
        self.event({"type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": text}})

    def result(self, text="translated"):
        self.feed({"type": "result", "subtype": "success", "is_error": False, "result": text})

    def assert_error(self, code, callback):
        with self.assertRaises(ClaudeOutputError) as raised:
            callback()
        self.assertEqual(str(raised.exception), code)
        self.assertNotIn("PRIVATE", str(raised.exception))

    def test_partial_unicode_and_complete_blocks_do_not_duplicate_text(self):
        self.event({"type": "message_start", "message": {"id": "new-format-id"}})
        for text in ("hello ", "\u4e16\u754c"):
            self.event({"type": "content_block_start", "content_block": {"type": "text", "text": ""}})
            self.delta(text)
            self.feed({"type": "assistant", "message": {
                "id": "same-id-per-block", "content": [{"type": "text", "text": text}]}})
            self.event({"type": "content_block_stop"})
        self.event({"type": "message_stop"})
        self.result("hello \u4e16\u754c")
        self.assertEqual(self.output.finish(), "hello \u4e16\u754c")
        self.assertEqual(self.deltas, ["hello ", "\u4e16\u754c"])

    def test_terminal_result_without_partial_events_is_supported(self):
        self.feed({"type": "assistant", "message": {
            "content": [{"type": "text", "text": "translated"}]}})
        self.result()
        self.assertEqual(self.output.finish(), "translated")
        self.assertEqual(self.deltas, [])

    def test_result_is_authoritative_without_requiring_partial_text_identity(self):
        self.delta("draft")
        self.result("final")
        self.assertEqual(self.output.finish(), "final")
        self.assertEqual(self.deltas, ["draft"])

    def test_extra_fields_unknown_events_and_metadata_do_not_require_a_cli_version(self):
        self.output.line("\r")
        self.feed({"type": "system", "subtype": "init", "session_id": "any",
                   "version": "future", "tools": [], "new_metadata": {"nested": [True, None]}})
        self.feed({"type": "future_notice", "new_field": "ignored"})
        self.event({"type": "future_progress", "new_field": 42})
        self.event({"type": "content_block_delta", "delta": {
            "type": "thinking_delta", "thinking": "PRIVATE"}})
        self.feed({"type": "result", "subtype": "success", "result": "ok",
                   "new_metadata": {"anything": True}})
        self.feed({"type": "system", "subtype": "future_shutdown_metadata"})
        self.assertEqual(self.output.finish(), "ok")
        self.assertEqual(self.deltas, [])

    def test_assistant_text_and_message_stop_are_not_terminal_success(self):
        self.delta("partial")
        self.feed({"type": "assistant", "message": {
            "content": [{"type": "text", "text": "partial"}]}})
        self.event({"type": "message_stop"})
        self.assert_error("provider_protocol_error", self.output.finish)

    def test_error_results_do_not_promote_partial_output_or_leak_details(self):
        for subtype in ("error_during_execution", "error_max_turns", "future_error"):
            with self.subTest(subtype=subtype):
                self.output = ClaudeOutput(self.deltas.append)
                self.delta("partial")
                self.assert_error("provider_failed", lambda: self.feed({
                    "type": "result", "subtype": subtype, "is_error": True,
                    "result": "PRIVATE", "errors": ["PRIVATE"]}))
                self.assertIsNone(self.output.result)

    def test_explicit_error_wins_over_success_subtype(self):
        self.assert_error("provider_failed", lambda: self.feed({
            "type": "result", "subtype": "success", "is_error": True, "result": "PRIVATE"}))

    def test_non_boolean_error_flag_is_not_treated_as_success(self):
        for value in (0, 1, "", None, "false"):
            with self.subTest(value=value):
                self.assert_error("provider_protocol_error", lambda: self.feed({
                    "type": "result", "subtype": "success", "is_error": value, "result": "PRIVATE"}))

    def test_top_level_assistant_and_stream_errors_fail_without_leaking(self):
        for value in ({"type": "error", "message": "PRIVATE"},
                      {"type": "assistant", "error": "PRIVATE"},
                      {"type": "stream_event", "event": {"type": "error", "error": "PRIVATE"}}):
            self.assert_error("provider_failed", lambda: self.feed(value))

    def test_empty_or_non_text_final_result_is_not_success(self):
        for value in ("", " \n", None, 1, ["PRIVATE"]):
            with self.subTest(value=value):
                self.assert_error("provider_protocol_error", lambda: self.result(value))

    def test_duplicate_terminal_or_late_response_text_is_rejected(self):
        self.result()
        for value in ({"type": "result", "subtype": "success", "result": "different"},
                      {"type": "assistant", "message": {"content": []}},
                      {"type": "stream_event", "event": {"type": "message_start"}}):
            self.assert_error("provider_protocol_error", lambda: self.feed(value))

    def test_tools_are_rejected_at_start_or_complete_block_without_executing_anything(self):
        for kind in ("tool_use", "server_tool_use"):
            block = {"type": kind, "id": "tool", "name": "PRIVATE", "input": {}}
            self.assert_error("unsafe_tool_event", lambda: self.event({
                "type": "content_block_start", "content_block": block}))
            self.assert_error("unsafe_tool_event", lambda: self.feed({
                "type": "assistant", "message": {"content": [block]}}))
        self.assert_error("unsafe_tool_event", lambda: self.event({
            "type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{}"}}))
        self.assert_error("unsafe_tool_event", lambda: self.feed({
            "type": "control_request", "request": {"subtype": "can_use_tool"}}))

    def test_malformed_known_payloads_fail_but_future_block_types_are_ignored(self):
        for value in (
            {}, {"type": []}, {"type": "assistant", "message": []},
            {"type": "assistant", "message": {"content": "wrong"}},
            {"type": "assistant", "message": {"content": [None]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": 1}]}},
            {"type": "stream_event", "event": None},
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": []}},
            {"type": "stream_event", "event": {"type": "content_block_delta",
                                            "delta": {"type": "text_delta", "text": None}}},
        ):
            self.assert_error("provider_protocol_error", lambda: self.feed(value))
        self.feed({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "PRIVATE"}, {"type": "future_nontext_block"}]}})
        self.assertEqual(self.deltas, [])

    def test_invalid_json_duplicate_keys_nonfinite_and_surrogates_are_rejected(self):
        for line in ('not json', '[]', '{"type":"result","type":"system"}',
                     '{"type":"system","extra":NaN}', '{"type":"system","extra":"\\ud800"}',
                     '{"type":"system","extra":' + '[' * 70 + '0' + ']' * 70 + '}'):
            self.assert_error("provider_protocol_error", lambda: self.output.line(line))

    def test_total_stdout_budget_counts_utf8_unknown_metadata_and_blank_lines(self):
        line = '{"type":"future_notice","text":"\u4e2d"}'
        limit = len(line.encode("utf-8")) + 1
        with patch("cc_providers.claude_jsonl.MAX_OUTPUT_BYTES", limit):
            self.output.line(line)
            self.assertEqual(self.output.received, limit)
            self.assert_error("translation_output_limit", lambda: self.output.line(""))

    def test_callback_errors_propagate_without_success_or_silent_retry(self):
        class CallbackError(RuntimeError):
            pass
        def fail(_text):
            raise CallbackError("callback")
        self.output = ClaudeOutput(fail)
        with self.assertRaises(CallbackError):
            self.delta("partial")
        self.assertIsNone(self.output.result)

    def test_constructor_requires_a_callback_and_empty_deltas_are_not_emitted(self):
        with self.assertRaises(TypeError):
            ClaudeOutput(None)
        self.delta("")
        self.assertEqual(self.deltas, [])
