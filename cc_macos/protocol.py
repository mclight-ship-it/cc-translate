"""Versioned, bounded NDJSON for private parent/child pipes."""

from __future__ import annotations

import json
import math
import re
from typing import BinaryIO


VERSION = 1
MAX_FRAME_BYTES = 65_536
MAX_TEXT_BYTES = 8_192
MAX_IDS = 4_096
MAX_DEPTH = 16
RESERVED_ID = "protocol"
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}", re.ASCII)
_CLIENT_TYPES = {"hello", "request", "cancel", "shutdown"}


class ProtocolError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value)) and value != RESERVED_ID


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate_key")
        result[key] = value
    return result


def _nonfinite(_value: str) -> None:
    raise ProtocolError("nonfinite_number")


def decode_frame(raw: bytes) -> dict:
    if len(raw) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    if not raw.endswith(b"\n"):
        raise ProtocolError("truncated_frame")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                           parse_constant=_nonfinite)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, ProtocolError):
            raise
        raise ProtocolError("invalid_json") from exc
    if not isinstance(value, dict):
        raise ProtocolError("invalid_envelope")
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise ProtocolError("message_too_deep")
        if isinstance(item, dict):
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise ProtocolError("invalid_unicode") from exc
        elif isinstance(item, float) and not math.isfinite(item):
            raise ProtocolError("nonfinite_number")
    return value


def read_frame(stream: BinaryIO) -> dict | None:
    raw = stream.readline(MAX_FRAME_BYTES + 1)
    return decode_frame(raw) if raw else None


def validate_client(message: dict) -> None:
    if set(message) != {"v", "id", "type", "payload"}:
        raise ProtocolError("invalid_envelope")
    if type(message["v"]) is not int or message["v"] != VERSION:
        raise ProtocolError("unsupported_version")
    if not valid_id(message["id"]):
        raise ProtocolError("invalid_id")
    if not isinstance(message["type"], str) or message["type"] not in _CLIENT_TYPES:
        raise ProtocolError("unsupported_message")
    if not isinstance(message["payload"], dict):
        raise ProtocolError("invalid_payload")


def encode_frame(message: dict) -> bytes:
    try:
        raw = (json.dumps(message, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ProtocolError("invalid_output") from exc
    if len(raw) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    return raw
