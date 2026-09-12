"""Explicit network-enabled smoke of the BUNDLED helper, never the host Python."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

if __package__:
    from .bundle import APP, BUILD, BundleError, digest, load_lock, need, require_macos, write_json
else:
    from bundle import APP, BUILD, BundleError, digest, load_lock, need, require_macos, write_json
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cc_macos.protocol import MAX_FRAME_BYTES, ProtocolError, decode_frame


MAX_FRAME = MAX_FRAME_BYTES
TERMINALS = {"ready", "completed", "failed", "cancelled"}


def decode_event(raw):
    try:
        event = decode_frame(raw)
    except ProtocolError as error:
        raise BundleError("malformed helper event") from error
    need(isinstance(event, dict) and set(event) == {"v", "id", "type", "payload", "seq"},
         "invalid helper event envelope")
    need(type(event["v"]) is int and event["v"] == 1, "helper protocol mismatch")
    need(isinstance(event["id"], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", event["id"]),
         "invalid helper event ID")
    need(type(event["seq"]) is int and event["seq"] >= 0, "invalid helper sequence")
    need(isinstance(event["type"], str) and
         event["type"] in {"ready", "accepted", "delta", "completed", "cancelled", "failed"},
         "unknown helper event")
    need(isinstance(event["payload"], dict), "invalid helper payload")
    return event


def validate_runtime(report, lock):
    need(isinstance(report, dict), "runtime probe result is not an object")
    need(all(isinstance(report.get(key), dict) for key in ("python", "sqlite", "ssl", "https")),
         "runtime probe sections are not objects")
    python = report.get("python", {})
    need(python.get("version") == lock["python_version"] and python.get("platform") == "darwin"
         and python.get("machine") == "arm64", "probe executed the wrong Python runtime")
    need(all(python.get(key) is True for key in ("isolated", "bytecode_disabled", "bundle_runtime")),
         "helper is not isolated bundle Python with bytecode disabled")
    sqlite = report.get("sqlite", {})
    need(sqlite.get("status") == "passed" and sqlite.get("read_write") is True,
         "actual SQLite read/write not confirmed")
    dictionary = report.get("dictionary")
    need(isinstance(dictionary, dict) and
         set(dictionary) == {"status", "read_only", "sources_preserved", "reopened"} and
         dictionary.get("status") == "passed" and
         all(dictionary.get(key) is True for key in ("read_only", "sources_preserved", "reopened")),
         "synthetic dictionary storage and lifecycle not confirmed")
    config = report.get("codex_config_fixture")
    need(isinstance(config, dict) and set(config) == {
         "status", "fixture", "methods_verified", "routing_preserved"} and config["status"] == "passed"
         and all(config[key] is True for key in ("fixture", "methods_verified", "routing_preserved")),
         "synthetic native config process not confirmed")
    catalog = report.get("catalog_storage_fixture")
    need(isinstance(catalog, dict) and set(catalog) == {
         "status", "cli_simulated", "cache_verified", "reopen_verified"} and catalog["status"] == "passed"
         and all(catalog[key] is True for key in ("cli_simulated", "cache_verified", "reopen_verified")),
         "synthetic catalog storage not confirmed")
    ssl = report.get("ssl", {})
    need(ssl.get("status") == "passed" and ssl.get("certificate_validation") is True
         and ssl.get("ca_source") == "bundle", "bundled CA SSL verification not confirmed")
    https = report.get("https", {})
    need(https.get("status") == "passed" and https.get("certificate_verified") is True
         and https.get("host") == "www.python.org", "real fixed-endpoint HTTPS probe not passed")


def validate_ready(ready):
    need(isinstance(ready, dict) and
         set(ready) == {"protocol", "capabilities", "max_frame_bytes", "fixture"} and
         type(ready["protocol"]) is int and ready["protocol"] == 1 and
         type(ready["max_frame_bytes"]) is int and ready["max_frame_bytes"] == MAX_FRAME and
         ready["fixture"] is True and
         ready["capabilities"] == ["fixture", "runtime_probe"], "helper handshake mismatch")


class Session:
    def __init__(self, command, directory):
        environment = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(directory / "home"), "TMPDIR": str(directory),
            "LANG": "en_US.UTF-8",
        }
        if os.name == "nt":
            # Path.home ignores HOME on Windows; retain the boundary that catalog
            # skips when checking parent project layers. CODEX_HOME stays synthetic.
            environment["USERPROFILE"] = str(Path.home())
        (directory / "home").mkdir()
        self.process = subprocess.Popen(
            [str(arg) for arg in command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=directory, env=environment, bufsize=0,
        )
        self.events = queue.Queue(maxsize=64)
        self.errors = []
        self.sequences = {}
        self.terminals = set()
        self.stderr_bytes = 0
        self.stdout_done = threading.Event()
        self.threads = [
            threading.Thread(target=self._stdout, daemon=True),
            threading.Thread(target=self._stderr, daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def _stdout(self):
        try:
            while raw := self.process.stdout.readline(MAX_FRAME + 1):
                self.events.put_nowait(decode_event(raw))
        except (BundleError, OSError, queue.Full) as error:
            self.errors.append(type(error).__name__)
        finally:
            self.stdout_done.set()

    def _stderr(self):
        try:
            while chunk := self.process.stderr.read(4096):
                self.stderr_bytes += len(chunk)
                need(self.stderr_bytes <= 8192, "helper stderr exceeded bound")
        except (BundleError, OSError) as error:
            self.errors.append(type(error).__name__)

    def send(self, identifier, kind, payload):
        need(identifier not in self.sequences, "smoke attempted ID reuse")
        self.sequences[identifier] = 0
        frame = json.dumps({"v": 1, "id": identifier, "type": kind, "payload": payload},
                           ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        need(len(frame) <= MAX_FRAME, "oversized smoke request")
        self.process.stdin.write(frame)
        self.process.stdin.flush()

    def receive(self, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            need(not self.errors, "helper pipe/JSON reader failed")
            try:
                event = self.events.get(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
                break
            except queue.Empty:
                need(time.monotonic() < deadline, "helper event timeout")
                need(not self.stdout_done.is_set(), "unexpected helper EOF")
        identifier = event["id"]
        need(identifier in self.sequences and identifier not in self.terminals, "unknown/terminal event ID")
        need(event["seq"] == self.sequences[identifier], "non-monotonic helper sequence")
        self.sequences[identifier] += 1
        if event["type"] in TERMINALS:
            self.terminals.add(identifier)
        need(event["type"] != "failed", "helper returned failed (payload intentionally not logged)")
        return event

    def expect(self, identifier, kind, timeout=20):
        event = self.receive(timeout)
        need(event["id"] == identifier and event["type"] == kind, "unexpected helper event order/type")
        return event["payload"]

    def close_input(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()

    def finish(self):
        self.close_input()
        need(self.process.wait(timeout=5) == 0, "helper exited nonzero")
        for thread in self.threads:
            thread.join(timeout=2)
            need(not thread.is_alive(), "helper pipe did not close")
        need(not self.errors and self.events.empty(), "late/malformed helper output")
        need(self.stderr_bytes == 0, "unexpected helper stderr (content intentionally not logged)")

    def dispose(self):
        self.close_input()
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        for stream in (self.process.stdout, self.process.stderr):
            stream.close()
        for thread in self.threads:
            thread.join(timeout=2)


def snapshot(app):
    return {
        path.relative_to(app).as_posix(): ("link:" + os.readlink(path) if path.is_symlink() else digest(path))
        for path in app.rglob("*") if path.is_file() or path.is_symlink()
    }


def exercise(session, lock):
    session.send("hello", "hello", {})
    ready = session.expect("hello", "ready")
    validate_ready(ready)
    text = "P0 synthetic IPC fixture \u4e2d"
    session.send("fixture", "request", {"operation": "fixture", "text": text})
    accepted = session.expect("fixture", "accepted")
    need(accepted.get("operation") == "fixture", "fixture not accepted")
    deltas = []
    while True:
        event = session.receive()
        need(event["id"] == "fixture", "fixture event ID mismatch")
        payload = event["payload"]
        need(payload.get("fixture") is True and isinstance(payload.get("text"), str),
             "missing synthetic fixture marker")
        if event["type"] == "delta":
            deltas.append(payload["text"])
        else:
            need(event["type"] == "completed" and text in payload["text"]
                 and "".join(deltas) == payload["text"], "fixture result/delta mismatch")
            break
    session.send("runtime", "request", {"operation": "runtime_probe", "https": True})
    accepted = session.expect("runtime", "accepted")
    need(accepted.get("operation") == "runtime_probe", "runtime probe not accepted")
    report = session.expect("runtime", "completed")
    validate_runtime(report, lock)
    session.send("cancel_target", "request", {"operation": "fixture", "text": "synthetic", "delay_ms": 2000})
    session.expect("cancel_target", "accepted")
    session.send("cancel_control", "cancel", {"request_id": "cancel_target"})
    pending = {"cancel_target", "cancel_control"}
    while pending:
        event = session.receive(timeout=5)
        need(event["id"] in pending, "unexpected explicit cancellation event")
        if event["id"] == "cancel_target":
            need(event["type"] == "cancelled", "delayed fixture was not cancelled")
        else:
            need(event["type"] == "completed" and event["payload"].get("cancel_requested") is True,
                 "cancel control not acknowledged")
        pending.remove(event["id"])
    session.send("eof_target", "request", {"operation": "fixture", "text": "synthetic", "delay_ms": 2000})
    session.expect("eof_target", "accepted")
    session.close_input()
    session.expect("eof_target", "cancelled", timeout=5)
    session.finish()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-https", action="store_true", help="explicitly contact www.python.org")
    args = parser.parse_args(argv)
    session = None
    scratch_created = False
    scratch = BUILD / "smoke-scratch"
    report_path = BUILD / "helper-smoke.json"
    try:
        need(args.allow_https, "bundled smoke requires explicit --allow-https")
        require_macos()
        need(APP.is_dir(), "development bundle missing; build it first")
        need(not APP.is_symlink() and not BUILD.is_symlink(), "symlinked development output")
        need(not scratch.exists() and not scratch.is_symlink(), "stale smoke scratch directory")
        before = snapshot(APP)
        scratch.mkdir()
        scratch_created = True
        write_json(report_path, {"status": "NOT PASSED", "development_only": True})
        command = [APP / "Contents/Helpers/python/bin/python3", "-I", "-B",
                   APP / "Contents/Resources/Core/launch.py"]
        session = Session(command, scratch)
        report = exercise(session, load_lock())
        need(snapshot(APP) == before, "helper modified the bundle")
        need(set(scratch.iterdir()) == {scratch / "home"} and not any((scratch / "home").iterdir()),
             "helper left probe files or wrote user configuration")
        write_json(report_path, {
            "status": "passed", "development_only": True, "runtime": report,
            "handshake": True, "fixture": True, "explicit_cancel": True, "eof_cancel": True,
            "bundle_unchanged": True, "probe_files_cleaned": True, "release_gate": "NOT PASSED",
            "not_tested": ["native UI", "Finder", "TCC", "Developer ID", "notarization", "macOS 14"],
        })
        print("Bundled helper handshake/fixture/SQLite/SSL/HTTPS/cancel/EOF passed.")
        print("No Finder, GUI/TCC, minimum-OS or signing claim.")
        return 0
    except (BundleError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # Do not report pipe content, fixture text or host-private paths.
        detail = str(error) if isinstance(error, BundleError) else type(error).__name__
        print("BLOCKED: bundled helper smoke failed:", detail, file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.dispose()
        if scratch_created and scratch.is_dir() and not scratch.is_symlink():
            shutil.rmtree(scratch)


if __name__ == "__main__":
    raise SystemExit(main())
