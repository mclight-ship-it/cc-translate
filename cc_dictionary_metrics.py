"""Aggregate-only, in-memory metrics for local dictionary routing."""

from __future__ import annotations

import threading
from collections import Counter, deque


class DictionaryMetrics:
    def __init__(self, latency_limit: int = 512):
        self._lock = threading.Lock()
        self._counts = Counter()
        self._latencies = deque(maxlen=max(1, int(latency_limit)))

    def record(self, outcome: str, wall_ms: float | None = None) -> None:
        if outcome not in {"hit", "miss", "weak", "error", "disabled"}:
            raise ValueError("unsupported dictionary outcome: %s" % outcome)
        with self._lock:
            self._counts[outcome] += 1
            if wall_ms is not None and outcome != "disabled":
                self._latencies.append(max(0.0, float(wall_ms)))

    def snapshot(self) -> dict:
        with self._lock:
            counts = dict(self._counts)
            latencies = sorted(self._latencies)
        attempts = sum(counts.get(key, 0) for key in (
            "hit", "miss", "weak", "error"))
        return {
            "attempts": attempts,
            "hit_rate": (
                counts.get("hit", 0) * 100.0 / attempts
                if attempts else None
            ),
            "p50_ms": self._percentile(latencies, 0.50),
            "p95_ms": self._percentile(latencies, 0.95),
            "outcomes": {
                key: counts.get(key, 0)
                for key in ("hit", "miss", "weak", "error", "disabled")
            },
            "retained_latencies": len(latencies),
        }

    @staticmethod
    def _percentile(values, percentile):
        if not values:
            return None
        return values[int((len(values) - 1) * percentile)]


__all__ = ["DictionaryMetrics"]
