"""Connection-owned history operations with bounded, revision-checked wire pages."""

import hashlib
import json
import os
import re

from cc_history import HistoryRepository, filter_history_entries, normalize_history_query, validate_history_entries
from cc_storage import atomic_write_json
from .history_owner import HistoryForkError, HistoryInUseError, MacHistoryOwner
from .protocol import ProtocolError, VERSION, decode_json_document, encode_frame, validate_json_value


MAX_HISTORY_FILE_BYTES = 8 * 1024 * 1024
MAX_HISTORY_ENTRIES = 10_000
MAX_PAGE_SIZE = 100
MAX_HISTORY_TEXT_BYTES = 24_000
MAX_SIGNATURE_BYTES = 4_096
ENTRY_DEPTH = 13
HISTORY_OPERATIONS = ("history_load", "history_add", "history_clear")
_REVISION = re.compile(r"[0-9a-f]{64}", re.ASCII)
_RECORD_FIELDS = {"input", "output", "is_dict", "is_code", "kind", "sig"}


class HistoryError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def page_payload(entries, revision, total, next_offset):
    cursor = None if next_offset is None else {"revision": revision, "offset": next_offset}
    return {"entries": entries, "revision": revision, "total": total, "next_cursor": cursor}


def page_frame(payload, request_id, sequence):
    return encode_frame({"v": VERSION, "id": request_id, "seq": sequence,
                         "type": "completed", "payload": payload})


def validate_entry(entry):
    validate_history_entries([entry])
    validate_json_value(entry, max_depth=ENTRY_DEPTH, code="invalid_history")


def validate_writable_entry(entry):
    validate_entry(entry)
    try:
        page_frame(page_payload([entry], "0" * 64, MAX_HISTORY_ENTRIES, MAX_HISTORY_ENTRIES),
                   "r" * 64, 2)
    except ProtocolError as error:
        if error.code == "frame_too_large":
            raise ProtocolError("history_entry_too_large") from error
        raise


def validate_history_request(payload):
    operation = payload.get("operation")
    if operation == "history_load":
        required = {"operation", "page_size", "cursor"}
        if not required <= set(payload) or set(payload) - required - {"query", "kind"}:
            raise ProtocolError("invalid_payload")
        query = payload.get("query", "")
        if not isinstance(query, str) or payload.get("kind", "all") not in ("all", "text", "dict", "code", "ocr"):
            raise ProtocolError("invalid_payload")
        try:
            if len(query.encode("utf-8")) > MAX_HISTORY_TEXT_BYTES:
                raise ProtocolError("invalid_payload")
        except UnicodeError as error:
            raise ProtocolError("invalid_payload") from error
        size = payload["page_size"]
        if type(size) is not int or not 1 <= size <= MAX_PAGE_SIZE:
            raise ProtocolError("invalid_payload")
        cursor = payload["cursor"]
        if cursor is not None:
            if (not isinstance(cursor, dict) or set(cursor) != {"revision", "offset"}
                    or not isinstance(cursor["revision"], str) or not _REVISION.fullmatch(cursor["revision"])
                    or type(cursor["offset"]) is not int or not 1 <= cursor["offset"] <= MAX_HISTORY_ENTRIES):
                raise ProtocolError("invalid_history_cursor")
    elif operation == "history_add":
        if set(payload) != _RECORD_FIELDS | {"operation", "limit"}:
            raise ProtocolError("invalid_payload")
        if (type(payload["is_dict"]) is not bool or type(payload["is_code"]) is not bool
                or type(payload["limit"]) is not int or not 1 <= payload["limit"] <= MAX_HISTORY_ENTRIES
                or payload["kind"] not in ("text", "dict", "code", "ocr")):
            raise ProtocolError("invalid_history_record")
        for field, limit in (("input", MAX_HISTORY_TEXT_BYTES), ("output", MAX_HISTORY_TEXT_BYTES),
                             ("sig", MAX_SIGNATURE_BYTES)):
            value = payload[field]
            if not isinstance(value, str):
                raise ProtocolError("invalid_history_record")
            try:
                size = len(value.encode("utf-8"))
            except UnicodeError as error:
                raise ProtocolError("invalid_history_record") from error
            if size > limit:
                raise ProtocolError("invalid_history_record")
        # The actual repository-created timestamp/entry is checked again by the writer.
        validate_writable_entry({"ts": "0000-00-00 00:00",
                                 **{key: payload[key] for key in _RECORD_FIELDS}})
    elif operation == "history_clear":
        if set(payload) != {"operation"}:
            raise ProtocolError("invalid_payload")
    else:
        raise ProtocolError("unsupported_operation")


class _BoundedHistoryRepository(HistoryRepository):
    def __init__(self, path):
        self.content_digest = hashlib.sha256(b"").digest()
        super().__init__(path, writer=self._write)

    def _read(self):
        try:
            stream = open(self.path, "rb")
        except FileNotFoundError:
            self.content_digest = hashlib.sha256(b"").digest()
            return []
        with stream:
            raw = stream.read(MAX_HISTORY_FILE_BYTES + 1)
        if len(raw) > MAX_HISTORY_FILE_BYTES:
            raise HistoryError("history_too_large")
        entries = validate_history_entries(decode_json_document(raw, max_depth=ENTRY_DEPTH + 1))
        if len(entries) > MAX_HISTORY_ENTRIES:
            raise HistoryError("history_too_large")
        for entry in entries:
            validate_entry(entry)
        self.content_digest = hashlib.sha256(raw).digest()
        return entries

    def _write(self, path, entries):
        if len(entries) > MAX_HISTORY_ENTRIES:
            raise HistoryError("history_too_large")
        for entry in validate_history_entries(entries):
            validate_writable_entry(entry)
        raw = json.dumps(entries, ensure_ascii=False, indent=2).replace("\n", os.linesep).encode("utf-8")
        if len(raw) > MAX_HISTORY_FILE_BYTES:
            raise HistoryError("history_too_large")
        atomic_write_json(path, entries)
        self.content_digest = hashlib.sha256(raw).digest()

    def clear(self):
        with self._lock:
            super().clear()
            self.content_digest = hashlib.sha256(b"").digest()


class _BusinessHistoryOwner(MacHistoryOwner, _BoundedHistoryRepository):
    """Keep MacHistoryOwner's public path-only API; the business I/O policy is fixed."""


class HistoryService:
    def __init__(self, directory):
        self._epoch = os.urandom(16)
        self._generation = 0
        try:
            self._owner = _BusinessHistoryOwner(directory / "history.json")
        except HistoryInUseError as error:
            raise HistoryError("history_in_use") from error
        except (OSError, ValueError, TypeError) as error:
            raise HistoryError("history_unavailable") from error

    def _revision(self, query="", kind="all"):
        snapshot = self._epoch + self._generation.to_bytes(8, "big") + self._owner.content_digest
        if query or kind != "all":
            # Bind the opaque view revision without enlarging the existing cursor.
            filters = json.dumps([query, kind], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            snapshot += b"\0history-filter-v1\0" + filters
        return hashlib.sha256(snapshot).hexdigest()

    def _page(self, payload, request_id, sequence):
        entries = self._owner.load()
        query = normalize_history_query(payload.get("query", ""))
        kind = payload.get("kind", "all")
        revision = self._revision(query, kind)
        entries = filter_history_entries(entries, query, kind)
        cursor = payload["cursor"]
        if cursor is not None and cursor["revision"] != revision:
            raise HistoryError("history_cursor_expired")
        offset = 0 if cursor is None else cursor["offset"]
        if cursor is not None and offset >= len(entries):
            raise HistoryError("invalid_history_cursor")
        selected = []
        available = entries[offset:offset + payload["page_size"]]
        for entry in available:
            candidate = selected + [entry]
            end = offset + len(candidate)
            response = page_payload(candidate, revision, len(entries), end if end < len(entries) else None)
            try:
                page_frame(response, request_id, sequence)
            except ProtocolError as error:
                if error.code != "frame_too_large":
                    raise
                # The final prefix drops next_cursor, so its size can shrink.
                if end < len(entries) and offset + len(available) == len(entries):
                    tail = page_payload(available, revision, len(entries), None)
                    try:
                        page_frame(tail, request_id, sequence)
                    except ProtocolError as tail_error:
                        if tail_error.code != "frame_too_large":
                            raise
                    else:
                        selected = available
                        break
                if not selected:
                    raise HistoryError("history_entry_too_large") from error
                break
            selected = candidate
        end = offset + len(selected)
        response = page_payload(selected, revision, len(entries), end if end < len(entries) else None)
        page_frame(response, request_id, sequence)
        return response

    def perform(self, payload, request_id, sequence):
        if self._owner is None:
            raise HistoryError("history_unavailable")
        try:
            validate_history_request(payload)
            operation = payload["operation"]
            if operation == "history_load":
                return self._page(payload, request_id, sequence)
            if operation == "history_add":
                self._owner.add(payload["input"], payload["output"], payload["is_dict"], payload["limit"],
                                is_code=payload["is_code"], kind=payload["kind"], sig=payload["sig"])
                self._generation += 1
                return {"recorded": True, "revision": self._revision()}
            self._owner.clear()
            self._generation += 1
            return {"cleared": True, "revision": self._revision()}
        except OSError as error:
            raise HistoryError("history_io_failed") from error
        except HistoryForkError as error:
            raise HistoryError("history_unavailable") from error
        except (ValueError, TypeError, OverflowError) as error:
            code = error.code if isinstance(error, ProtocolError) and error.code == "history_entry_too_large" else "invalid_history"
            raise HistoryError(code) from error

    def find_cached(self, text, kind, sig):
        if self._owner is None:
            raise HistoryError("history_unavailable")
        try:
            return self._owner.find_cached(text, kind, sig)
        except OSError as error:
            raise HistoryError("history_io_failed") from error
        except HistoryForkError as error:
            raise HistoryError("history_unavailable") from error
        except (ValueError, TypeError, OverflowError) as error:
            raise HistoryError("invalid_history") from error

    def close(self):
        owner, self._owner = self._owner, None
        if owner is not None:
            owner.close()
