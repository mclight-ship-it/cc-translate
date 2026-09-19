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


def summarize_idle_resources(report):
    samples = report["samples"]
    if len(samples) != 31 or report["settle_s"] != 5 or report["targets_are_gates"] is not False:
        raise ValueError("idle measurement requires a five-second settle and 31 snapshots")
    identities = None
    times, cpu, rss, footprint = [], [], [], []
    previous_cpu = {}
    for sample in samples:
        elapsed = sample["elapsed_s"]
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid idle sample time")
        if times and elapsed <= times[-1]:
            raise ValueError("idle timestamps must advance")
        processes = sample["processes"]
        if str(report["root_pid"]) not in processes:
            raise ValueError("idle sample is missing the owned App")
        current = {pid: value["start_identity"] for pid, value in processes.items()}
        if identities is not None and current != identities:
            raise ValueError("process identities changed during idle measurement")
        identities = current
        for pid, value in processes.items():
            for key in ("start_identity", "cpu_ns", "rss_bytes", "footprint_bytes"):
                if type(value[key]) is not int or value[key] < 0:
                    raise ValueError("invalid process resource counter")
            if pid in previous_cpu and value["cpu_ns"] < previous_cpu[pid]:
                raise ValueError("process CPU counter regressed")
            previous_cpu[pid] = value["cpu_ns"]
        times.append(elapsed)
        cpu.append(sum(value["cpu_ns"] for value in processes.values()))
        rss.append(sum(value["rss_bytes"] for value in processes.values()))
        footprint.append(sum(value["footprint_bytes"] for value in processes.values()))
    duration = times[-1] - times[0]
    if duration < 30:
        raise ValueError("idle sampling interval was shorter than 30 seconds")
    percentages = [(cpu[index] - cpu[index - 1]) / 1e9 / (times[index] - times[index - 1]) * 100
                   for index in range(1, len(samples))]

    def distribution(values):
        ordered = sorted(values)
        return {"samples": values, "p95": ordered[math.ceil(len(values) * 0.95) - 1],
                "max": ordered[-1], "min": ordered[0]}

    return {
        "duration_s": duration, "process_count": len(identities), "stable_process_tree": True,
        "cpu_percent_one_core": distribution(percentages),
        "cpu_percent_whole_interval": (cpu[-1] - cpu[0]) / 1e9 / duration * 100,
        "rss_bytes": distribution(rss), "physical_footprint_bytes": distribution(footprint),
    }
