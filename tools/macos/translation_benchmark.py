"""Paired, model-free macOS translation-client benchmark of two immutable apps.

The synthetic CLI answers immediately. This measures the real bundled helper and
native-provider client, not model/network latency, translation quality, or GUI paint.
No production process cleanup, timeout, or provider implementation is patched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import queue
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import uuid


BASELINE_SOURCE = "7953587e9a334b5bd8191a64f74d01c9d957765f"
CANDIDATE_SOURCE = "8007747d9e01e451c3e53978d026bab287ba820f"
SCOPE = "synthetic full translation client; not model or physical GUI"
CORPUS = {
    "short": "The public library opens every morning and welcomes readers from the neighborhood.",
    "long": ("A public library provides a quiet place to read, learn, and exchange ideas. "
             "Visitors can borrow books, explore local history, and join community discussions. "
             "The staff help readers find reliable information and discover new interests. " * 16).strip(),
}
CONFIG = {
    "model_provider": "codex_cli", "codex_model": "synthetic",
    "codex_streaming_experimental": True, "direction": "to_zh", "language": "en_US",
    "summary_enabled": False, "labs_defaults_migrated": True,
    "history_enabled": False, "local_dictionary_enabled": False, "max_chars": 8192,
}
MAX_FRAME = 65_536
MAX_EVENTS = 64
TIMEOUT = 30
ROLES = ("baseline", "candidate")
TIMING_FIELDS = {
    "total_ms", "spawn_ms", "initialize_ms", "hook_preflight_ms", "thread_start_ms",
    "turn_start_ms", "first_result_ms", "turn_first_result_ms", "turn_total_ms",
    "version_check_ms", "helper_elapsed_ms",
}
TIMING_FLAGS = {"version_cache_hit", "warm_process_hit", "cache_hit"}
METRICS = ("first_delta_ms", "completed_ms", "preparation_ms",
           "preparation_plus_first_delta_ms", "preparation_plus_completed_ms")


# Only this small driver is supplied by the benchmark. All fixture and product
# imports, including prompt construction and cleanup verification, come from the
# selected immutable app, executed by that app's isolated Python.
WORKER = r"""
import hashlib, json, platform, sys
from pathlib import Path
action, app, root, identity, text, settings, count = sys.argv[1:]
app, root = Path(app).resolve(), Path(root).resolve()
core = app / "Contents" / "Resources" / "Core"
runtime = app / "Contents" / "Resources" / "python"
if (sys.platform != "darwin" or platform.machine() != "arm64"
        or not sys.flags.isolated or not sys.dont_write_bytecode
        or not Path(sys.executable).resolve().is_relative_to(runtime)):
    raise RuntimeError("bundled_darwin_arm64_runtime_required")
sys.path.insert(0, str(core))
from cc_macos import translation_fixture, native_provider_fixture, translation
from cc_config import Config, plan_config_migration
from cc_providers.codex_cli import build_codex_prompt
from cc_storage import macos_user_paths
for module in (translation_fixture, native_provider_fixture, translation):
    if not Path(module.__file__).resolve().is_relative_to(core):
        raise RuntimeError("host_module_substitution")
if action == "prepare":
    fixture = translation_fixture.prepare(root, identity, "normal")
    fixture["config"].update(json.loads(settings))
    fixture["request"].update(text=text, app_language="en_US", use_cache=False, record_history=False)
    config = Config(fixture["config"])
    plan_config_migration(fixture["config"], config)
    snapshot = translation.snapshot_for_translation(config, fixture["request"])
    if (snapshot.summarize or snapshot.dictionary or not snapshot.stream_enabled
            or snapshot.request.model != "synthetic" or snapshot.request.task != "text"):
        raise RuntimeError("uncontrolled_translation_snapshot")
    expected = fixture["expected"]
    expected.update(prompt=build_codex_prompt(snapshot.request), model=snapshot.selection.model,
                    task=snapshot.request.task, kind=snapshot.kind, target_lang=snapshot.target_lang,
                    summarize=snapshot.summarize, signature=snapshot.sig, stream=snapshot.stream_enabled)
    (Path(fixture["root"]) / "expected-request.json").write_text(
        json.dumps(expected, ensure_ascii=False), encoding="utf-8")
    output = {"fixture": fixture, "runtime": {
        "platform": sys.platform, "machine": platform.machine(), "python_version": platform.python_version(),
        "isolated": True, "bytecode_disabled": True, "bundle_modules": True}}
elif action == "verify":
    output = translation_fixture.verify(root, require_cleanup=True)
    if output["submitted_turns"] != int(count) or not output["cleanup_verified"]:
        raise RuntimeError("unexpected_submission_count_or_cleanup")
    home = root / "cache" / "synthetic home \u4e2d # %"
    paths = macos_user_paths(home, identity)
    if (paths.application_support / "history.json").exists():
        raise RuntimeError("unexpected_history_write")
    config = json.loads((paths.application_support / "config.json").read_bytes())
    if any(config.get(key) != value for key, value in json.loads(settings).items()):
        raise RuntimeError("benchmark_settings_changed")
    output["history_absent"] = True
    output["settings_verified"] = True
else:
    raise RuntimeError("unknown_benchmark_worker_action")
print(json.dumps(output, ensure_ascii=False, allow_nan=False))
"""


class BenchmarkError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise BenchmarkError(message)


def require_platform():
    require(sys.platform == "darwin" and platform.machine() == "arm64",
            "benchmark requires Darwin arm64; host substitution is forbidden")


def number(value, *, signed=False):
    require(type(value) in (int, float) and math.isfinite(value) and (signed or value >= 0),
            "invalid timing value")
    return value


def summarize(values, *, signed=False):
    require(bool(values), "empty timing distribution")
    ordered = sorted(number(value, signed=signed) for value in values)
    return {"count": len(values), "median_ms": statistics.median(ordered),
            "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
            "min_ms": ordered[0], "max_ms": ordered[-1]}


def schedule(rounds, warm_requests):
    require(type(rounds) is int and 5 <= rounds <= 10, "rounds must be between 5 and 10")
    require(type(warm_requests) is int and 2 <= warm_requests <= 5,
            "warm requests must be between 2 and 5")
    return [(round_index, case, role, order)
            for round_index in range(rounds)
            for case_index, case in enumerate(CORPUS)
            for order, role in enumerate(ROLES if (round_index + case_index) % 2 == 0 else ROLES[::-1])]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tree_digest(app):
    entries = []
    for path in sorted(app.rglob("*")):
        if path.is_symlink():
            require(path.resolve().is_relative_to(app), "app contains an external symlink")
            value = "link:" + os.readlink(path)
        elif path.is_file():
            value = sha256(path)
        else:
            require(path.is_dir(), "app contains a special file")
            continue
        entries.append([path.relative_to(app).as_posix(), value, path.lstat().st_mode & 0o777])
    require(bool(entries), "app is empty")
    return text_digest(json.dumps(entries, separators=(",", ":")))


def app_identity(app, source, build):
    require(re.fullmatch(r"[0-9a-f]{40}", source) is not None, "source pin must be a full lowercase SHA")
    require(re.fullmatch(r"[1-9][0-9]*", build) is not None, "build pin must be decimal")
    require(app.is_dir() and not app.is_symlink(), "app must be an existing non-symlink directory")
    app = app.resolve()
    contents = app / "Contents"
    manifest = json.loads((contents / "Resources" / "source-manifest.json").read_bytes())
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    require(manifest.get("source_commit") == source and manifest.get("source_tree_dirty") is False,
            "app source pin or clean-source provenance mismatch")
    metadata = manifest.get("application", {})
    require(info.get("CFBundleVersion") == build and metadata.get("build") == build,
            "app build pin mismatch")
    identity = info.get("CFBundleIdentifier")
    require(isinstance(identity, str) and identity == metadata.get("bundle_identifier")
            and re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", identity),
            "app bundle identifier mismatch")
    require(metadata.get("architecture") == "arm64", "app architecture mismatch")
    inventory = manifest.get("resource_hashes")
    require(isinstance(inventory, dict) and bool(inventory), "source resource inventory missing")
    essential = {
        "Resources/Core/launch.py", "Resources/Core/cc_macos/translation_fixture.py",
        "Resources/Core/cc_macos/native_provider_fixture.py",
        "Resources/Core/cc_macos/server.py", "Resources/Core/cc_macos/translation.py",
    }
    require(essential <= set(inventory), "source resource inventory incomplete")
    for name, digest in inventory.items():
        require(isinstance(name, str) and isinstance(digest, str), "invalid source inventory entry")
        relative = PurePosixPath(name)
        require(bool(relative.parts) and not relative.is_absolute() and relative.parts[0] == "Resources"
                and all(part not in ("", ".", "..") for part in relative.parts)
                and "\\" not in name, "unsafe source inventory path")
        path = contents.joinpath(*relative.parts)
        require(path.resolve().is_relative_to(app) and path.is_file() and not path.is_symlink()
                and sha256(path) == digest, "source resource hash mismatch")
    python = contents / "Resources" / "python" / "bin" / "python3"
    require(python.is_file() and python.resolve().is_relative_to(app), "bundled interpreter missing")
    return {"source": source, "build": build, "bundle_identifier": identity,
            "architecture": "arm64", "source_tree_dirty": False,
            "resource_files_verified": len(inventory), "tree_sha256": tree_digest(app)}


def clean_environment(home, scratch):
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "TMPDIR": str(scratch),
            "LANG": "en_US.UTF-8"}


def worker(app, directory, action, root, identity, case, count=0):
    python = app / "Contents" / "Resources" / "python" / "bin" / "python3"
    command = [str(python), "-I", "-B", "-c", WORKER, action, str(app), str(root), identity,
               CORPUS[case], json.dumps(CONFIG), str(count)]
    environment = clean_environment(directory / "worker-home", directory)
    Path(environment["HOME"]).mkdir(exist_ok=True)
    result = subprocess.run(command, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT)
    if result.returncode != 0 or result.stderr:
        # Preserve fixture error codes, not traceback paths, prompts, or pipe data.
        lines = result.stderr.decode("utf-8", errors="replace").splitlines()
        code = re.fullmatch(r"([A-Za-z]{1,64}Error): ([a-z_]{1,64})", lines[-1]) if lines else None
        detail = "; " + code.group(0) if code is not None else ""
        raise BenchmarkError(f"bundled fixture {action} failed (exit={result.returncode}{detail})")
    require(0 < len(result.stdout) <= 262_144, "fixture worker output exceeded bound")
    return strict_json(result.stdout)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise BenchmarkError("nonfinite JSON value")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise BenchmarkError("invalid JSON") from error


def decode_event(raw):
    require(len(raw) <= MAX_FRAME and raw.endswith(b"\n"), "oversized or truncated helper event")
    event = strict_json(raw)
    require(type(event) is dict and set(event) == {"v", "id", "type", "payload", "seq"},
            "invalid helper event envelope")
    require(type(event["v"]) is int and event["v"] == 1, "invalid helper protocol version")
    require(type(event["seq"]) is int and event["seq"] >= 0, "invalid helper event sequence")
    require(isinstance(event["id"], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", event["id"]),
            "invalid helper event ID")
    require(event["type"] in ("ready", "accepted", "started", "delta", "completed", "failed", "cancelled")
            and isinstance(event["payload"], dict), "invalid helper event type or payload")
    return event


class Session:
    """Bounded, timestamped version of smoke.Session's private-pipe lifecycle."""

    def __init__(self, command, directory, environment):
        self.events = queue.Queue(maxsize=MAX_EVENTS)
        self.errors = []
        self.sequences = {}
        self.terminals = set()
        self.stderr_bytes = 0
        self.stdout_done = threading.Event()
        self.started_ns = time.perf_counter_ns()
        self.process = subprocess.Popen(command, cwd=directory, env=environment, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.threads = [threading.Thread(target=self._stdout, daemon=True),
                        threading.Thread(target=self._stderr, daemon=True)]
        for thread in self.threads:
            thread.start()

    def _stdout(self):
        try:
            while raw := self.process.stdout.readline(MAX_FRAME + 1):
                event = decode_event(raw)
                self.events.put_nowait((event, time.perf_counter_ns()))
        except (BenchmarkError, OSError, ValueError, queue.Full):
            self.errors.append("stdout reader failed")
        finally:
            self.stdout_done.set()

    def _stderr(self):
        try:
            while chunk := self.process.stderr.read(4096):
                self.stderr_bytes += len(chunk)
                require(self.stderr_bytes <= 8192, "helper stderr exceeded bound")
        except (BenchmarkError, OSError, ValueError):
            self.errors.append("stderr reader failed")

    def send(self, identifier, kind, payload):
        require(identifier not in self.sequences, "helper request ID reused")
        self.sequences[identifier] = 0
        raw = json.dumps({"v": 1, "id": identifier, "type": kind, "payload": payload},
                         ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
        require(len(raw) <= MAX_FRAME, "helper request exceeds frame bound")
        started_ns = time.perf_counter_ns()
        self.process.stdin.write(raw)
        self.process.stdin.flush()
        return started_ns

    def receive(self, identifier, deadline):
        while True:
            require(not self.errors, "helper pipe reader failed")
            remaining = deadline - time.monotonic()
            require(remaining > 0, "helper event timeout")
            try:
                event, received_ns = self.events.get(timeout=min(0.1, remaining))
                break
            except queue.Empty:
                require(not self.stdout_done.is_set(), "unexpected helper EOF")
        require(event["id"] == identifier and identifier in self.sequences
                and identifier not in self.terminals, "unknown or terminal helper event ID")
        require(event["seq"] == self.sequences[identifier], "non-monotonic helper sequence")
        self.sequences[identifier] += 1
        if event["type"] in ("ready", "completed", "failed", "cancelled"):
            self.terminals.add(identifier)
        if event["type"] == "failed":
            code = event["payload"].get("code", "unknown")
            safe_code = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code) else "unknown"
            raise BenchmarkError(f"helper request failed: {safe_code}")
        require(event["type"] != "cancelled", "helper unexpectedly cancelled the request")
        return event, received_ns

    def expect(self, identifier, kind, deadline, payload=None):
        event, stamp = self.receive(identifier, deadline)
        require(event["type"] == kind, "unexpected helper event order/type")
        require(payload is None or event["payload"] == payload, "unexpected helper event payload")
        return event["payload"], stamp

    def finish(self):
        self.send("shutdown", "shutdown", {})
        self.expect("shutdown", "completed", time.monotonic() + TIMEOUT, {})
        self.process.stdin.close()
        require(self.process.wait(timeout=8) == 0, "helper exited nonzero")
        for thread in self.threads:
            thread.join(timeout=2)
            require(not thread.is_alive(), "helper pipes did not drain")
        require(not self.errors and self.events.empty() and self.stderr_bytes == 0,
                "late helper output or stderr")

    def dispose(self):
        if not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        for thread in self.threads:
            thread.join(timeout=2)
        self.process.stdout.close()
        self.process.stderr.close()


def validate_ready(payload):
    require(set(payload) == {"protocol", "capabilities", "max_frame_bytes", "fixture", "backend"}
            and type(payload["protocol"]) is int and payload["protocol"] == 1
            and type(payload["max_frame_bytes"]) is int and payload["max_frame_bytes"] == MAX_FRAME
            and payload["fixture"] is False and payload["backend"] == "native_appserver",
            "not a native translation helper")
    capabilities = payload["capabilities"]
    require(isinstance(capabilities, list) and all(isinstance(value, str) for value in capabilities)
            and len(set(capabilities)) == len(capabilities)
            and {"translate", "config_save"} <= set(capabilities), "missing translation capabilities")
    return "prewarm" in capabilities


def control(session, identifier, payload, expected):
    start = session.send(identifier, "request", payload)
    deadline = time.monotonic() + TIMEOUT
    for kind in ("accepted", "started"):
        session.expect(identifier, kind, deadline, {"operation": payload["operation"]})
    _, end = session.expect(identifier, "completed", deadline, expected)
    return number((end - start) / 1_000_000)


def validate_completed(payload, expected):
    require(isinstance(payload, dict), "invalid translation result")
    result = dict(payload)
    timings = result.pop("timings", None)
    require(result == {
        "text": expected["output"], "submitted": True, "cached": False,
        "kind": expected["kind"], "target_lang": expected["target_lang"],
        "summarize": False, "history": "disabled", "history_error": None,
    }, "translation result, cache, summary, history, or submission mismatch")
    require(result["submitted"] is True and result["cached"] is False and result["summarize"] is False,
            "non-boolean translation result flags")
    # Build 202 has no instrumentation; absence is recorded, never fabricated.
    if timings is not None:
        require(isinstance(timings, dict) and set(timings) <= TIMING_FIELDS | TIMING_FLAGS
                and {"helper_elapsed_ms", "cache_hit"} <= set(timings), "invalid provider timings")
        for key, value in timings.items():
            require(number(value) <= 3_600_000, "provider timing exceeded bound")
            if key in TIMING_FLAGS:
                require(value in (0, 1), "invalid provider timing flag")
        require(timings["cache_hit"] == 0, "primary translation unexpectedly used a result cache")
    return timings


def translate(session, identifier, fixture):
    request = fixture["request"]
    require(request["use_cache"] is False and request["record_history"] is False,
            "primary benchmark must disable result cache and history")
    start = session.send(identifier, "request", request)
    deadline = time.monotonic() + TIMEOUT
    for kind in ("accepted", "started"):
        session.expect(identifier, kind, deadline, {"operation": "translate"})
    pieces, first = [], None
    for _ in range(MAX_EVENTS):
        event, stamp = session.receive(identifier, deadline)
        payload = event["payload"]
        if event["type"] == "delta":
            require(set(payload) == {"text", "submitted"} and isinstance(payload["text"], str)
                    and payload["submitted"] is True, "invalid translation delta")
            pieces.append(payload["text"])
            require(sum(len(piece.encode("utf-8")) for piece in pieces) <= MAX_FRAME,
                    "translation deltas exceeded bound")
            if payload["text"] and first is None:
                first = stamp
            continue
        require(event["type"] == "completed", "unexpected translation event")
        timings = validate_completed(payload, fixture["expected"])
        require(first is not None and "".join(pieces) == fixture["expected"]["output"],
                "missing first nonempty delta or output mismatch")
        require(start <= first <= stamp, "invalid translation timestamps")
        return {"first_delta_ms": (first - start) / 1_000_000,
                "completed_ms": (stamp - start) / 1_000_000,
                "output_sha256": text_digest(payload["text"]), "submitted": True, "cached": False,
                "provider_timings": timings}
    raise BenchmarkError("translation exceeded event bound")


def submissions(root):
    path = root / "native-rpc.jsonl"
    if not path.exists():
        return 0, 0
    require(path.stat().st_size <= 2_000_000, "synthetic RPC log exceeded bound")
    rows = [strict_json(line) for line in path.read_bytes().splitlines()]
    methods = [row["request"]["method"] for row in rows if row["kind"] == "provider"]
    return methods.count("thread/start"), methods.count("turn/start")


def validate_fixture(prepared, directory, app, case):
    fixture, runtime = prepared["fixture"], prepared["runtime"]
    require(runtime.get("platform") == "darwin" and runtime.get("machine") == "arm64"
            and all(runtime.get(key) is True for key in ("isolated", "bytecode_disabled", "bundle_modules")),
            "fixture runtime provenance mismatch")
    root, home, command = (Path(fixture[key]).resolve() for key in ("root", "home", "command"))
    require(root.is_relative_to(directory) and home.is_relative_to(root) and command.is_relative_to(root)
            and command.is_file() and not command.is_symlink(), "fixture escaped synthetic directory")
    env = fixture["environment"]
    require(set(env) == {"PATH", "HOME", "CODEX_HOME", "TMPDIR", "CC_SYNTHETIC_ROOT", "CC_SYNTHETIC_MODE"}
            and env["PATH"] == "/usr/bin:/bin" and env["HOME"] == str(home)
            and env["TMPDIR"] == str(home) and env["CC_SYNTHETIC_ROOT"] == str(root)
            and env["CC_SYNTHETIC_MODE"] == "translation_normal"
            and Path(env["CODEX_HOME"]).resolve().is_relative_to(root),
            "fixture environment must contain only synthetic paths and no credentials")
    require(fixture["config"] == CONFIG, "uncontrolled fixture configuration")
    require(fixture["request"] == {
        "operation": "translate", "text": CORPUS[case], "app_language": "en_US",
        "origin": "text", "use_cache": False, "record_history": False,
    }, "uncontrolled fixture request")
    expected = fixture["expected"]
    require(expected["model"] == "synthetic" and expected["task"] == "text"
            and expected["stream"] is True and expected["summarize"] is False
            and isinstance(expected["output"], str) and bool(expected["output"]),
            "fixture must not select a real model, summary, or nonstreaming path")
    # The fixture's executable is a tiny shell wrapper around this bundle's
    # interpreter and native_provider_fixture, not a PATH-discovered installed CLI.
    import shlex
    wrapper = command.read_text(encoding="utf-8")
    python = app / "Contents" / "Resources" / "python" / "bin" / "python3"
    lines = wrapper.splitlines()
    require(len(lines) == 2 and lines[0] == "#!/bin/sh", "unexpected synthetic CLI wrapper")
    tokens = shlex.split(lines[1])
    require(len(tokens) == 6 and tokens[0] == "exec" and Path(tokens[1]).resolve() == python.resolve()
            and tokens[2:4] == ["-I", "-B"]
            and Path(tokens[4]).resolve() == (
                app / "Contents" / "Resources" / "Core" / "cc_macos" / "native_provider_fixture.py").resolve()
            and tokens[5] == "$@", "synthetic CLI must execute this bundle's fixture only")
    require(submissions(root) == (0, 0), "fixture preparation submitted user content")
    return fixture


def run_session(app, identity, directory, case, role, round_index, order, warm_requests, report):
    directory.mkdir()
    prepare_start = time.perf_counter_ns()
    prepared = worker(app, directory, "prepare", directory / "fixture",
                      identity["bundle_identifier"], case)
    fixture = validate_fixture(prepared, directory, app, case)
    record = {"role": role, "case": case, "round": round_index, "order": order,
              "fixture_prepare_ms": (time.perf_counter_ns() - prepare_start) / 1_000_000,
              "runtime": prepared["runtime"], "helper_clean_exit": False, "cleanup_verified": False,
              "stage": "helper_start"}
    report["sessions"].append(record)
    command = [str(app / "Contents" / "Resources" / "python" / "bin" / "python3"), "-I", "-B",
               str(app / "Contents" / "Resources" / "Core" / "launch.py"),
               "--config-home", fixture["home"], "--application-id", identity["bundle_identifier"],
               "--codex-command", fixture["command"]]
    environment = clean_environment(fixture["home"], directory)
    environment["CC_TRANSLATE_CODEX_ENV"] = json.dumps(fixture["environment"])
    session = Session(command, directory, environment)
    root = Path(fixture["root"])
    completed = 0
    try:
        record["stage"] = "hello"
        session.send("hello", "hello", {})
        ready, stamp = session.expect("hello", "ready", time.monotonic() + TIMEOUT)
        record["helper_start_to_ready_ms"] = (stamp - session.started_ns) / 1_000_000
        record["prewarm_advertised"] = validate_ready(ready)
        record["stage"] = "config_save"
        record["config_setup_ms"] = control(
            session, "configure", {"operation": "config_save", "config": fixture["config"]}, {"saved": True})
        require(submissions(root) == (0, 0), "setup submitted translation content")
        for index in range(warm_requests + 1):
            prewarm = index > 0 and role == "candidate" and record["prewarm_advertised"]
            preparation_ms = 0.0
            if prewarm:
                record["stage"] = f"prewarm-{index}"
                before = submissions(root)
                preparation_ms = control(session, f"prewarm-{index}",
                                         {"operation": "prewarm", "app_language": "en_US"}, {"warmed": True})
                require(submissions(root) == before, "content-free prewarm submitted a thread or turn")
            record["stage"] = f"translate-{index}"
            sample = translate(session, f"translate-{index}", fixture)
            completed += 1
            require(submissions(root) == (completed, completed), "translation retried or failed to submit once")
            sample.update(
                role=role, case=case, round=round_index, order=order, request_index=index,
                phase="cold" if index == 0 else "retained_helper", prewarm_used=prewarm,
                preparation_ms=preparation_ms,
                preparation_plus_first_delta_ms=preparation_ms + sample["first_delta_ms"],
                preparation_plus_completed_ms=preparation_ms + sample["completed_ms"],
                prompt_sha256=text_digest(fixture["expected"]["prompt"]),
            )
            if index == 0:
                sample["helper_start_plus_setup_plus_completed_ms"] = (
                    record["helper_start_to_ready_ms"] + record["config_setup_ms"] + sample["completed_ms"])
            report["samples"].append(sample)
        record["stage"] = "shutdown"
        session.finish()
        record["helper_clean_exit"] = True
    finally:
        original_error = sys.exc_info()[0]
        try:
            session.dispose()
            verification = worker(app, directory, "verify", root, identity["bundle_identifier"], case, completed)
            record["verification"] = verification
            record["cleanup_verified"] = verification["cleanup_verified"]
            if original_error is None:
                record["stage"] = "passed"
        except (BenchmarkError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            detail = str(error) if isinstance(error, BenchmarkError) else type(error).__name__
            report["errors"].append("session cleanup verification: " + detail)
            if original_error is None:
                raise


def aggregate(report, rounds, warm_requests):
    plan = schedule(rounds, warm_requests)
    sessions = report["sessions"]
    require(len(sessions) == len(plan), "session coverage incomplete")
    require([(row["round"], row["case"], row["role"], row["order"]) for row in sessions] == plan,
            "paired alternating session order mismatch")
    require(all(row.get("helper_clean_exit") is True and row.get("cleanup_verified") is True
                for row in sessions), "helper cleanup incomplete")
    session_index = {(row["case"], row["round"], row["role"]): row for row in sessions}
    indexed = {}
    for sample in report["samples"]:
        key = sample["case"], sample["round"], sample["request_index"], sample["role"]
        require(key not in indexed, "duplicate benchmark sample")
        indexed[key] = sample
    expected_keys = {(case, round_index, index, role)
                     for round_index, case, role, _ in plan for index in range(warm_requests + 1)}
    require(set(indexed) == expected_keys, "request coverage incomplete")
    pairs = []
    for case in CORPUS:
        for round_index in range(rounds):
            for index in range(warm_requests + 1):
                before, after = (indexed[case, round_index, index, role] for role in ROLES)
                require(before["output_sha256"] == after["output_sha256"]
                        and before["prompt_sha256"] == after["prompt_sha256"],
                        "paired requests did not use identical prompts and outputs")
                for row in (before, after):
                    owner = session_index[case, round_index, row["role"]]
                    require(row["submitted"] is True and row["cached"] is False,
                            "primary sample was not a submitted, noncached request")
                    require(row["phase"] == ("cold" if index == 0 else "retained_helper"),
                            "cold/warm sample classification mismatch")
                    should_prewarm = index > 0 and row["role"] == "candidate" and owner["prewarm_advertised"]
                    require(row["prewarm_used"] is should_prewarm,
                            "prewarm did not follow the advertised capability and phase")
                    require(row["order"] == owner["order"], "sample/session order mismatch")
                    require(should_prewarm or row["preparation_ms"] == 0,
                            "unexpected preparation on a non-prewarmed request")
                    require(row["first_delta_ms"] <= row["completed_ms"], "invalid first-delta endpoint")
                    for metric in METRICS:
                        number(row[metric])
                    for endpoint in ("first_delta", "completed"):
                        require(row[f"preparation_plus_{endpoint}_ms"] ==
                                row["preparation_ms"] + row[f"{endpoint}_ms"],
                                "preparation time missing from inclusive endpoint")
                pairs.append({
                    "case": case, "round": round_index, "request_index": index, "phase": before["phase"],
                    "candidate_minus_baseline_ms": {metric: after[metric] - before[metric] for metric in METRICS},
                })
    summaries = {}
    for case in CORPUS:
        summaries[case] = {}
        for phase in ("cold", "retained_helper"):
            group = {}
            for metric in METRICS:
                group[metric] = {
                    **{role: summarize([row[metric] for row in report["samples"]
                                        if row["case"] == case and row["phase"] == phase and row["role"] == role])
                       for role in ROLES},
                    "paired_candidate_minus_baseline": summarize(
                        [row["candidate_minus_baseline_ms"][metric] for row in pairs
                         if row["case"] == case and row["phase"] == phase], signed=True),
                }
            summaries[case][phase] = group
        summaries[case]["helper_start_to_ready_ms"] = {
            role: summarize([row["helper_start_to_ready_ms"] for row in sessions
                             if row["case"] == case and row["role"] == role]) for role in ROLES}
        summaries[case]["helper_start_to_ready_ms"]["paired_candidate_minus_baseline"] = summarize([
            session_index[case, index, "candidate"]["helper_start_to_ready_ms"]
            - session_index[case, index, "baseline"]["helper_start_to_ready_ms"]
            for index in range(rounds)], signed=True)
    return {"pairs": pairs, "summary": summaries}


def write_report(path, report):
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run(args):
    require_platform()
    plan = schedule(args.rounds, args.warm_requests)
    apps = {role: getattr(args, role + "_app").absolute() for role in ROLES}
    require(apps["baseline"].resolve() != apps["candidate"].resolve(), "baseline and candidate apps must differ")
    report_path = args.report.absolute()
    require(report_path.suffix == ".json" and not report_path.is_symlink()
            and report_path.resolve().is_relative_to(Path.cwd().resolve())
            and all(not report_path.resolve().is_relative_to(app.resolve()) for app in apps.values()),
            "report must be a project-local JSON file outside both apps")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": 1, "status": "NOT PASSED", "scope": SCOPE,
        "provider": "codex_cli", "fixture": "bundled cc_macos.native_provider_fixture translation_normal",
        "model_service_contacted": False, "physical_gui_tested": False, "credentials_used": False,
        "rounds": args.rounds, "warm_requests_per_helper": args.warm_requests,
        "host": {"platform": sys.platform, "machine": platform.machine(), "os_version": platform.mac_ver()[0]},
        "corpus": {case: {"text": text, "utf8_bytes": len(text.encode("utf-8")), "sha256": text_digest(text)}
                   for case, text in CORPUS.items()},
        "controls": CONFIG | {"use_cache": False, "record_history": False},
        "method": {
            "clock": "perf_counter_ns; private stdout reader stamps after strict JSON parsing",
            "request_endpoint": "immediately before pipe write to parsed first nonempty delta / completed",
            "startup_endpoint": "before helper Popen to parsed hello-ready; excludes fixture preparation",
            "cold": "new helper, fresh synthetic home and catalog; no prewarm; not cold OS/filesystem caches",
            "retained_helper": "same helper after cold request; candidate prewarms only if advertised",
            "preparation": "completed content-free prewarm before each candidate retained-helper request; "
                           "reported separately AND added to inclusive endpoints; never subtracted from cold",
            "cleanup": "unmodified production cleanup, including its normal 200ms process cleanup grace",
            "statistics": "median and nearest-rank p95; paired deltas are candidate minus baseline",
            "limits": "5..10 rounds, 2..5 retained requests, 30s per operation, no retries or fallbacks",
            "not_measured": ["real model/network translation speed", "translation quality",
                             "physical Mac interaction", "Swift/AppKit rendering",
                             "fresh-copy clipboard activation", "output UI", "Claude provider"],
            "targets_are_gates": False,
        },
        "stage": "identity", "active_session": None, "identities": {}, "sessions": [], "samples": [],
        "temporary_home_removed": False, "bundle_unchanged": False, "errors": [],
    }
    write_report(report_path, report)
    scratch = Path.cwd().resolve() / (".translation-benchmark-" + uuid.uuid4().hex)
    try:
        for role in ROLES:
            report["identities"][role] = app_identity(
                apps[role], getattr(args, role + "_source"), getattr(args, role + "_build"))
            apps[role] = apps[role].resolve()
        scratch.mkdir(mode=0o700)
        for round_index, case, role, order in plan:
            report["stage"] = "measurement"
            report["active_session"] = {"round": round_index, "case": case, "role": role, "order": order}
            run_session(apps[role], report["identities"][role],
                        scratch / f"{round_index}-{case}-{role}", case, role, round_index,
                        order, args.warm_requests, report)
            write_report(report_path, report)
        report["stage"] = "aggregation"
        report.update(aggregate(report, args.rounds, args.warm_requests))
    except (BenchmarkError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report["errors"].append(str(error) if isinstance(error, BenchmarkError) else type(error).__name__)
    finally:
        try:
            if scratch.exists():
                shutil.rmtree(scratch)
            report["temporary_home_removed"] = not scratch.exists()
            unchanged = []
            for role, identity in report["identities"].items():
                same = app_identity(apps[role], identity["source"], identity["build"]) == identity
                unchanged.append(same)
            report["bundle_unchanged"] = len(unchanged) == 2 and all(unchanged)
            require(report["bundle_unchanged"], "bundle identity or bytes changed (or verification incomplete)")
        except (BenchmarkError, OSError, ValueError, KeyError, TypeError) as error:
            report["errors"].append(str(error) if isinstance(error, BenchmarkError) else type(error).__name__)
        if not report["errors"] and report["temporary_home_removed"] and report["bundle_unchanged"]:
            report["status"] = "passed"
            report["stage"] = "passed"
            report["active_session"] = None
        write_report(report_path, report)
    return report


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    for role, source, build in (("baseline", BASELINE_SOURCE, "202"), ("candidate", CANDIDATE_SOURCE, "206")):
        result.add_argument("--" + role + "-app", type=Path, required=True)
        result.add_argument("--" + role + "-source", default=source, help="required full immutable source SHA")
        result.add_argument("--" + role + "-build", default=build, help="required CFBundleVersion")
    result.add_argument("--report", type=Path, required=True, help="project-local output JSON (outside the apps)")
    result.add_argument("--rounds", type=int, default=5, help="paired rounds per corpus, 5..10 (default 5)")
    result.add_argument("--warm-requests", type=int, default=2, help="retained requests per helper, 2..5 (default 2)")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report = run(args)
    except (BenchmarkError, OSError, ValueError) as error:
        detail = str(error) if isinstance(error, BenchmarkError) else type(error).__name__
        print("BLOCKED: " + detail, file=sys.stderr)
        return 1
    print(json.dumps({"status": report["status"], "scope": SCOPE, "errors": report["errors"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
