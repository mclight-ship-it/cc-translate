"""Bounded, short-lived result reuse owned by one native helper session."""

from collections import OrderedDict
from dataclasses import dataclass, field
import math
import time


@dataclass(frozen=True)
class _Entry:
    text: str = field(repr=False)
    created: float
    size: int


class TranslationMemoryCache:
    def __init__(self, *, max_entries=20, max_bytes=512 * 1024, ttl_seconds=300,
                 clock=time.monotonic):
        if (type(max_entries) is not int or max_entries < 1
                or type(max_bytes) is not int or max_bytes < 1
                or type(ttl_seconds) not in (int, float)
                or not math.isfinite(ttl_seconds) or ttl_seconds <= 0):
            raise ValueError("invalid_translation_cache_limits")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries = OrderedDict()
        self._bytes = 0
        self._last_clock = None

    @property
    def count(self):
        self._expire()
        return len(self._entries)

    @property
    def payload_bytes(self):
        self._expire()
        return self._bytes

    def clear(self):
        self._entries.clear()
        self._bytes = 0

    @staticmethod
    def _validate_key(key):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("invalid_translation_cache_key")

    def _expire(self):
        now = self._clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("invalid_translation_cache_clock")
        if self._last_clock is not None and now < self._last_clock:
            self.clear()
        self._last_clock = now
        for key, entry in tuple(self._entries.items()):
            if now - entry.created >= self._ttl:
                self._remove(key)
        return now

    def _remove(self, key):
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry.size

    def discard(self, key):
        self._validate_key(key)
        self._remove(key)

    def get(self, key):
        self._validate_key(key)
        self._expire()
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry.text

    def put(self, key, text):
        self._validate_key(key)
        if type(text) is not str or not text.strip():
            raise ValueError("invalid_translation_cache_text")
        size = len(key) + len(text.encode("utf-8"))
        now = self._expire()
        self._remove(key)
        if size > self._max_bytes:
            return False
        while self._entries and (
                len(self._entries) >= self._max_entries or self._bytes + size > self._max_bytes):
            oldest = next(iter(self._entries))
            self._remove(oldest)
        self._entries[key] = _Entry(text, now, size)
        self._bytes += size
        return True
