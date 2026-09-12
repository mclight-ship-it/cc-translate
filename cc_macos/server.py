"""A small probe service, deliberately not a translation/provider adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
import sys
import threading
import time
from typing import BinaryIO, TextIO

from .probes import ProbeCancelled, ProbeError, runtime_probe
from .protocol import (
    MAX_FRAME_BYTES, MAX_IDS, MAX_TEXT_BYTES, RESERVED_ID, VERSION,
    ProtocolError, encode_frame, read_frame, valid_id, validate_client,
)


MAX_WORKERS = 4


@dataclass
class _Request:
    id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    seq: int = 0
    terminal: bool = False


class Server:
    def __init__(self, stdin: BinaryIO, stdout: BinaryIO, stderr: TextIO):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self._lock = threading.RLock()
        self._ids: set[str] = set()
        self._tasks: dict[str, _Request] = {}
        self._workers: set[threading.Thread] = set()
        self._ready = False
        self._stopping = False
        self._pipe_closed = False
        self._worker_failed = False

    def _log(self, code: str) -> None:
        self.stderr.write(f"cc_macos:{code}\n")
        self.stderr.flush()

    def _send(self, request: _Request, event: str, payload: dict) -> None:
        with self._lock:
            if request.terminal or self._pipe_closed:
                return
            raw = encode_frame({"v": VERSION, "id": request.id, "seq": request.seq,
                                "type": event, "payload": payload})
            try:
                self.stdout.write(raw)
                self.stdout.flush()
            except OSError:
                self._pipe_closed = True
                self._log("pipe_error")
                self._stop()
                return
            request.seq += 1
            if event in {"completed", "failed", "cancelled", "ready"}:
                request.terminal = True

    def _stop(self) -> None:
        with self._lock:
            self._stopping = True
            for request in self._tasks.values():
                request.cancel.set()
                self._send(request, "cancelled", {})

    def _join_workers(self) -> None:
        with self._lock:
            workers = tuple(self._workers)
        deadline = time.monotonic() + 2
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        if any(worker.is_alive() for worker in workers):
            self._worker_failed = True
            self._log("shutdown_timeout")

    def _perform(self, request: _Request, payload: dict) -> None:
        try:
            if payload["operation"] == "fixture":
                if not request.cancel.wait(payload.get("delay_ms", 25) / 1000):
                    text = "[Synthetic fixture - not a translation]\n" + payload["text"]
                    self._send(request, "delta", {"text": text, "fixture": True})
                    self._send(request, "completed", {"text": text, "fixture": True})
            else:
                report = runtime_probe(https=payload.get("https", False), cancel=request.cancel)
                self._send(request, "completed", report)
        except ProbeCancelled:
            self._send(request, "cancelled", {})
        except ProbeError as exc:
            self._send(request, "failed", {"code": exc.code})
        except (ProtocolError, OSError):
            with self._lock:
                self._worker_failed = True
            self._log("internal_error")
            self._send(request, "failed", {"code": "internal_error"})
        finally:
            with self._lock:
                if request.cancel.is_set():
                    self._send(request, "cancelled", {})
                self._tasks.pop(request.id, None)
                self._workers.discard(threading.current_thread())

    @staticmethod
    def _payload_error(payload: dict) -> str | None:
        operation = payload.get("operation")
        if operation == "fixture":
            if set(payload) - {"operation", "text", "delay_ms"}:
                return "invalid_payload"
            text = payload.get("text")
            delay = payload.get("delay_ms", 25)
            if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
                return "invalid_text"
            if type(delay) is not int or not 0 <= delay <= 2000:
                return "invalid_delay"
        elif operation == "runtime_probe":
            if set(payload) - {"operation", "https"} or type(payload.get("https", False)) is not bool:
                return "invalid_payload"
        else:
            return "unsupported_operation"
        return None

    def _handle(self, message: dict) -> bool:
        validate_client(message)
        id_, type_, payload = message["id"], message["type"], message["payload"]
        if id_ in self._ids:
            raise ProtocolError("duplicate_id")
        if len(self._ids) >= MAX_IDS:
            raise ProtocolError("session_limit")
        self._ids.add(id_)
        control = _Request(id_)
        if not self._ready:
            if type_ != "hello" or payload:
                raise ProtocolError("handshake_required")
            self._ready = True
            self._send(control, "ready", {
                "protocol": VERSION, "capabilities": ["fixture", "runtime_probe"],
                "max_frame_bytes": MAX_FRAME_BYTES, "fixture": True,
            })
        elif type_ == "hello":
            raise ProtocolError("duplicate_handshake")
        elif type_ == "shutdown":
            if payload:
                raise ProtocolError("invalid_payload")
            self._stop()
            self._send(control, "completed", {})
            return False
        elif type_ == "cancel":
            if set(payload) != {"request_id"} or not valid_id(payload["request_id"]):
                raise ProtocolError("invalid_payload")
            with self._lock:
                target = self._tasks.get(payload["request_id"])
                active = target is not None and not target.terminal
                if active:
                    target.cancel.set()
                    self._send(target, "cancelled", {})
                self._send(control, "completed", {"cancel_requested": active})
        elif type_ == "request":
            error = self._payload_error(payload)
            if error:
                self._send(control, "failed", {"code": error})
                return True
            with self._lock:
                if len(self._tasks) >= MAX_WORKERS:
                    self._send(control, "failed", {"code": "busy"})
                    return True
                self._tasks[id_] = control
                self._send(control, "accepted", {"operation": payload["operation"]})
                worker = threading.Thread(target=self._perform, args=(control, payload),
                                          name="cc-macos-probe", daemon=True)
                self._workers.add(worker)
                try:
                    worker.start()
                except RuntimeError:
                    self._workers.discard(worker)
                    self._tasks.pop(id_)
                    self._worker_failed = True
                    self._log("worker_start_failed")
                    self._send(control, "failed", {"code": "worker_start_failed"})
        return True

    def run(self) -> int:
        try:
            while not self._stopping:
                message = read_frame(self.stdin)
                if message is None or not self._handle(message):
                    break
        except ProtocolError as exc:
            self._send(_Request(RESERVED_ID), "failed", {"code": exc.code})
            self._log(exc.code)
            return 2
        except OSError:
            self._log("pipe_error")
            return 2
        finally:
            self._stop()
            self._join_workers()
        return 2 if self._worker_failed or self._pipe_closed else 0


def main() -> int:
    return Server(sys.stdin.buffer, sys.stdout.buffer, sys.stderr).run()
