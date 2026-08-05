"""Strict parser for the stable ``codex exec --json`` event stream."""

import json


class CodexProtocolError(ValueError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


_TOP_LEVEL_EVENTS = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "item.started",
    "item.updated",
    "item.completed",
    "error",
}

_SAFE_ITEM_TYPES = {"agent_message", "reasoning"}
_BLOCKED_ITEM_TYPES = {
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "collab_tool_call",
    "web_search",
}


def parse_codex_jsonl(output):
    """Return the final agent message or raise a fail-closed protocol error."""
    parser = CodexJsonlParser()
    for line_number, raw_line in enumerate((output or "").splitlines(), 1):
        parser.feed(raw_line, line_number)
    return parser.finish()


class CodexJsonlParser:
    """Incremental parser so callers can stop a process on its first tool event."""

    def __init__(self):
        self.final_text = ""
        self.remote_error = ""
        self.saw_event = False

    def feed(self, raw_line, line_number):
        line = (raw_line or "").strip()
        if not line:
            return
        self.saw_event = True
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexProtocolError(
                "invalid_jsonl", f"line {line_number}: {exc.msg}") from exc
        if not isinstance(event, dict):
            raise CodexProtocolError(
                "invalid_event", f"line {line_number}: event is not an object")

        event_type = event.get("type")
        if event_type not in _TOP_LEVEL_EVENTS:
            raise CodexProtocolError(
                "unknown_event", f"line {line_number}: {event_type!r}")

        if event_type in {"turn.failed", "error"}:
            self.remote_error = _event_error_text(event)
            return

        if event_type.startswith("item."):
            item = event.get("item")
            if not isinstance(item, dict):
                raise CodexProtocolError(
                    "invalid_item", f"line {line_number}: missing item object")
            item_type = item.get("type")
            if item_type == "error":
                message = item.get("message")
                if message:
                    self.remote_error = str(message)
                return
            if item_type in _BLOCKED_ITEM_TYPES:
                raise CodexProtocolError(
                    "unsafe_tool_event",
                    f"line {line_number}: blocked item {item_type}")
            if item_type not in _SAFE_ITEM_TYPES:
                raise CodexProtocolError(
                    "unknown_item",
                    f"line {line_number}: unsupported item {item_type!r}")
            if event_type == "item.completed" and item_type == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    self.final_text = text.strip()

    def finish(self):
        if self.final_text:
            return self.final_text
        if self.remote_error:
            raise CodexProtocolError("remote_error", self.remote_error)
        if not self.saw_event:
            raise CodexProtocolError(
                "empty_output", "Codex returned no JSONL events")
        raise CodexProtocolError(
            "no_result", "Codex returned no agent message")


def _event_error_text(event):
    error = event.get("error")
    if isinstance(error, dict):
        for key in ("message", "detail", "code"):
            value = error.get(key)
            if value:
                return str(value)
    if error:
        return str(error)
    for key in ("message", "detail"):
        value = event.get(key)
        if value:
            return str(value)
    return "Codex request failed"
