"""Claude print output: stream text, ignore metadata, require a successful result."""

from cc_macos.protocol import ProtocolError, decode_json_document


MAX_OUTPUT_BYTES = 8 * 1024 * 1024


class ClaudeOutputError(RuntimeError):
    pass


class ClaudeOutput:
    def __init__(self, on_delta):
        if not callable(on_delta):
            raise TypeError("on_delta must be callable")
        self.on_delta = on_delta
        self.result = None
        self.received = 0

    def line(self, line):
        try:
            raw = line.encode("utf-8")
        except (AttributeError, UnicodeError):
            raise ClaudeOutputError("provider_protocol_error") from None
        self.received += len(raw) + 1
        if self.received > MAX_OUTPUT_BYTES:
            raise ClaudeOutputError("translation_output_limit")
        if not raw.strip():
            return
        try:
            message = decode_json_document(raw, max_depth=64, object_required=True)
        except ProtocolError:
            raise ClaudeOutputError("provider_protocol_error") from None
        kind = message.get("type")
        if type(kind) is not str or not kind:
            raise ClaudeOutputError("provider_protocol_error")
        if kind in ("result", "assistant", "stream_event") and self.result is not None:
            raise ClaudeOutputError("provider_protocol_error")
        if kind == "error":
            raise ClaudeOutputError("provider_failed")
        if kind == "result":
            self._result(message)
        elif kind == "assistant":
            if message.get("error"):
                raise ClaudeOutputError("provider_failed")
            body = message.get("message")
            if not isinstance(body, dict):
                raise ClaudeOutputError("provider_protocol_error")
            blocks = body.get("content")
            if not isinstance(blocks, list):
                raise ClaudeOutputError("provider_protocol_error")
            for block in blocks:
                self._block(block)
            # Complete blocks also arrive when partial events are enabled.
            # Do not append their text again; result.result is authoritative.
        elif kind == "stream_event":
            self._event(message.get("event"))
        elif kind == "control_request":
            # This is a one-shot, tool-free request, not an interactive SDK session.
            raise ClaudeOutputError("unsafe_tool_event")

    @staticmethod
    def _block(block):
        if not isinstance(block, dict) or type(block.get("type")) is not str:
            raise ClaudeOutputError("provider_protocol_error")
        if block["type"] in ("tool_use", "server_tool_use"):
            raise ClaudeOutputError("unsafe_tool_event")
        if block["type"] == "text" and type(block.get("text")) is not str:
            raise ClaudeOutputError("provider_protocol_error")

    def _event(self, event):
        if not isinstance(event, dict) or type(event.get("type")) is not str:
            raise ClaudeOutputError("provider_protocol_error")
        kind = event["type"]
        if kind == "error":
            raise ClaudeOutputError("provider_failed")
        if kind == "content_block_start":
            self._block(event.get("content_block"))
        elif kind == "content_block_delta":
            delta = event.get("delta")
            if not isinstance(delta, dict) or type(delta.get("type")) is not str:
                raise ClaudeOutputError("provider_protocol_error")
            if delta["type"] == "input_json_delta":
                raise ClaudeOutputError("unsafe_tool_event")
            if delta["type"] == "text_delta":
                text = delta.get("text")
                if type(text) is not str:
                    raise ClaudeOutputError("provider_protocol_error")
                if text:
                    self.on_delta(text)

    def _result(self, message):
        if "is_error" in message and type(message["is_error"]) is not bool:
            raise ClaudeOutputError("provider_protocol_error")
        if message.get("is_error") or message.get("subtype") != "success":
            raise ClaudeOutputError("provider_failed")
        text = message.get("result")
        if type(text) is not str or not text.strip():
            raise ClaudeOutputError("provider_protocol_error")
        self.result = text

    def finish(self):
        """Call only after the transport drains, cleans up, and confirms exit zero."""
        if self.result is None:
            raise ClaudeOutputError("provider_protocol_error")
        return self.result
