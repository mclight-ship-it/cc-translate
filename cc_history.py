"""Explicit-path history I/O; callers choose process ownership and error policy."""

import json
import os
import threading
import time

from cc_storage import atomic_write_json


class HistoryFormatError(ValueError):
    pass


def history_entry_kind(entry):
    kind = (entry or {}).get("kind")
    if kind in ("text", "dict", "code", "ocr"):
        return kind
    if (entry or {}).get("is_code"):
        return "code"
    if (entry or {}).get("is_dict"):
        return "dict"
    return "text"


def normalize_history_query(query=""):
    return " ".join((query or "").split()).casefold()


def filter_history_entries(entries, query="", kind="all"):
    if kind not in ("all", "text", "dict", "code", "ocr"):
        kind = "all"
    query = normalize_history_query(query)
    out = []
    for entry in entries or []:
        if kind != "all" and history_entry_kind(entry) != kind:
            continue
        if query:
            hay = "\n".join([
                entry.get("input", "") or "",
                entry.get("output", "") or "",
                entry.get("ts", "") or "",
            ]).casefold()
            if query not in hay:
                continue
        out.append(entry)
    return out


def read_history(path, *, strict=True):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            entries = json.load(stream)
    except FileNotFoundError:
        return []
    return validate_history_entries(entries, strict=strict)


def validate_history_entries(entries, *, strict=True):
    """Validate the existing array schema without selecting a path or changing values."""
    if not isinstance(entries, list):
        if not strict:
            return []
        raise HistoryFormatError("history_array_required")
    if strict:
        for entry in entries:
            if not isinstance(entry, dict):
                raise HistoryFormatError("history_object_required")
            for name in ("ts", "input", "output", "kind", "sig"):
                if entry.get(name) is not None and not isinstance(entry[name], str):
                    raise HistoryFormatError("history_string_required")
            for name in ("is_dict", "is_code"):
                if name in entry and not isinstance(entry[name], bool):
                    raise HistoryFormatError("history_boolean_required")
    return entries


class HistoryRepository:
    """Serializes operations, but alone does not exclude another process."""

    def __init__(self, path, *, lock=None, reader=None, writer=None):
        self._path = path
        self._lock = threading.RLock() if lock is None else lock
        self._reader = reader
        self._writer = writer
        self._closed = False

    @property
    def path(self):
        return self._path

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("history_repository_closed")

    def _read(self):
        return read_history(self.path) if self._reader is None else self._reader()

    def load(self):
        with self._lock:
            self._ensure_open()
            return self._read()

    def add(self, input_text, output_text, is_dict, limit, is_code=False, kind=None, sig=None,
            *, preserve_null_input=False):
        if kind not in ("text", "dict", "code", "ocr"):
            if is_code:
                kind = "code"
            elif is_dict:
                kind = "dict"
            else:
                kind = "text"
        with self._lock:
            self._ensure_open()
            entries = self._read()
            entries.insert(0, {
                "ts": time.strftime("%Y-%m-%d %H:%M"),
                "input": None if preserve_null_input and input_text is None else input_text or "",
                "output": output_text or "",
                "is_dict": bool(is_dict),
                "is_code": bool(is_code),
                "kind": kind,
                "sig": sig or "",
            })
            del entries[max(1, int(limit)):]
            writer = atomic_write_json if self._writer is None else self._writer
            writer(self.path, entries)

    def find_cached(self, text, kind, sig):
        with self._lock:
            self._ensure_open()
            if not text or not text.strip():
                return None
            if kind not in ("text", "dict", "code"):
                return None
            key = text.strip()
            for entry in self._read():
                if (entry.get("kind") == kind
                        and (entry.get("sig") or "") == (sig or "")
                        and (entry.get("input") or "").strip() == key):
                    out = (entry.get("output") or "").strip()
                    if out:
                        return out
            return None

    def clear(self):
        with self._lock:
            self._ensure_open()
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass

    def close(self):
        with self._lock:
            self._closed = True

    def __enter__(self):
        with self._lock:
            self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
