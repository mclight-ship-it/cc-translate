"""Fixed synthetic inputs and timing statistics; no platform or product imports."""

import math
import time


WARMUPS = 3
SAMPLES = 100
CLASSIFICATION_CASES = (
    ("english_word", "hello", "text"),
    ("chinese_word", "\u4f60\u597d", "text"),
    ("english_prose", "Please translate this sentence without changing its meaning.", "text"),
    ("chinese_prose", "\u8bf7\u7ffb\u8bd1\u8fd9\u6bb5\u6587\u5b57\uff0c\u4fdd\u7559\u539f\u610f\u3002", "text"),
    ("python", "def add(a, b):\n    return a + b", "code"),
    ("json", '{\n  "enabled": true,\n  "count": 3\n}', "code"),
    ("mixed", "Here is the calculation.\nvalue = 1 + 2\nThe result is useful.", "mixed"),
    ("long_prose", "This is a fixed synthetic paragraph for local classification.\n" * 100, "text"),
)
DICTIONARY_CASES = ("hello", "world", "translation", "\u4f60\u597d", "\u4e16\u754c")


def summarize(samples, target_ms):
    if not samples or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                          for value in samples):
        raise ValueError("timings must be nonempty, finite, nonnegative numbers")
    if type(target_ms) not in (int, float) or not math.isfinite(target_ms) or target_ms <= 0:
        raise ValueError("timing target must be finite and positive")
    ordered = sorted(samples)
    p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
    return {
        "samples_ms": list(samples), "count": len(samples), "p95_ms": p95,
        "max_ms": ordered[-1], "min_ms": ordered[0], "target_ms": target_ms,
        "target_result": "met" if p95 <= target_ms else "measured_miss",
    }


def measure(operation, validate, target_ms, *, clock=time.perf_counter_ns):
    def once():
        start = clock()
        result = operation()
        milliseconds = (clock() - start) / 1_000_000
        validate(result)
        return milliseconds

    first = once()
    for _ in range(WARMUPS):
        once()
    warm = summarize([once() for _ in range(SAMPLES)], target_ms)
    summarize([first], target_ms)
    return {"first_ms": first, "warmup_count": WARMUPS, "warm": warm}


def validate_measurement(value, target_ms):
    if value.get("warmup_count") != WARMUPS:
        raise ValueError("warmup count mismatch")
    summarize([value["first_ms"]], target_ms)
    warm = value["warm"]
    if warm != summarize(warm["samples_ms"], target_ms) or warm["count"] != SAMPLES:
        raise ValueError("timing statistics or sample count mismatch")
