"""Private bounded cache for background AI dictionary supplements."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time


class DictionaryAiCacheError(RuntimeError):
    pass


class DictionaryAiCache:
    def __init__(self, path: str, limit: int = 128):
        self.path = os.path.abspath(path)
        self.limit = max(1, int(limit))
        self._lock = threading.Lock()

    def get(self, query: str, signature: str):
        key = (query or "").strip()
        if not key or not signature:
            return None
        with self._lock:
            for entry in self._load():
                if (entry["query"] == key
                        and entry["signature"] == signature):
                    return entry["output"]
        return None

    def put(self, query: str, signature: str, output: str) -> None:
        key = (query or "").strip()
        value = (output or "").strip()
        if not key or not signature or not value:
            return
        with self._lock:
            entries = [
                entry for entry in self._load()
                if not (entry["query"] == key
                        and entry["signature"] == signature)
            ]
            entries.insert(0, {
                "query": key,
                "signature": signature,
                "output": value,
                "ts": int(time.time()),
            })
            self._write(entries[:self.limit])

    def _load(self):
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError) as exc:
            raise DictionaryAiCacheError(
                "cannot read AI dictionary cache: %s" % exc) from exc
        if not isinstance(data, list):
            raise DictionaryAiCacheError(
                "AI dictionary cache root must be a list")
        entries = []
        for index, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise DictionaryAiCacheError(
                    "AI dictionary cache entry %s is invalid" % index)
            query = entry.get("query")
            signature = entry.get("signature")
            output = entry.get("output")
            if not all(isinstance(value, str) and value
                       for value in (query, signature, output)):
                raise DictionaryAiCacheError(
                    "AI dictionary cache entry %s is incomplete" % index)
            entries.append(entry)
        return entries

    def _write(self, entries) -> None:
        parent = os.path.dirname(self.path)
        try:
            os.makedirs(parent, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(
                prefix=".dictionary-ai-cache-", suffix=".tmp", dir=parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(entries, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, self.path)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
        except OSError as exc:
            raise DictionaryAiCacheError(
                "cannot write AI dictionary cache: %s" % exc) from exc


__all__ = ["DictionaryAiCache", "DictionaryAiCacheError"]
