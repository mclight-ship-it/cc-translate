"""Private diagnostics or explicit business connections with native translation."""

from __future__ import annotations

from dataclasses import dataclass, field
import queue
import os
import signal
import sys
import threading
import time
from typing import BinaryIO, TextIO

from .probes import ProbeCancelled, ProbeError, runtime_probe
from .configuration import ConfigurationError, startup_configuration, validate_save
from .history import HISTORY_OPERATIONS, validate_history_request
from .protocol import (
    MAX_FRAME_BYTES, MAX_IDS, MAX_TEXT_BYTES, MAX_STREAM_BYTES, RESERVED_ID, VERSION,
    PipeFrameReader, ProtocolError, encode_frame, read_frame, valid_id, validate_client,
)


MAX_WORKERS = 4


class _ReadStop:
    def __init__(self, event):
        self.event = event
        self.signal_number = 0

    def is_set(self):
        return bool(self.signal_number) or self.event.is_set()


@dataclass
class _Request:
    id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    seq: int = 0
    terminal: bool = False
    started: bool = False
    translating: bool = False
    committing: bool = False
    wire_bytes: int = 0


class Server:
    def __init__(self, stdin: BinaryIO, stdout: BinaryIO, stderr: TextIO, *, configuration=None):
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
        self._configuration = configuration
        self._translation_enabled = getattr(configuration, "translation_enabled", False) is True
        self._configuration_queue = queue.Queue()
        self._configuration_worker = None
        self._queue_stopped = False
        self._stop_event = threading.Event()
        self._read_stop = _ReadStop(self._stop_event)
        self._shutdown = None

    def request_termination(self, signal_number, _frame):
        # Signal handlers must not acquire a lock held by interrupted Python code.
        self._read_stop.signal_number = signal_number

    def _log(self, code: str) -> None:
        self.stderr.write(f"cc_macos:{code}\n")
        self.stderr.flush()

    def _send(self, request: _Request, event: str, payload: dict) -> None:
        with self._lock:
            if request.terminal or self._pipe_closed:
                return
            raw = encode_frame({"v": VERSION, "id": request.id, "seq": request.seq,
                                "type": event, "payload": payload})
            if (request.translating and event == "delta"
                    and request.wire_bytes + len(raw) > MAX_STREAM_BYTES - MAX_FRAME_BYTES):
                raise ProtocolError("translation_output_limit")
            try:
                self.stdout.write(raw)
                self.stdout.flush()
            except OSError:
                self._pipe_closed = True
                self._log("pipe_error")
                self._stop()
                # Discard the failed buffered stream so interpreter exit cannot retry its flush.
                try:
                    self.stdout.close()
                except OSError:
                    self._worker_failed = True
                return
            request.seq += 1
            request.wire_bytes += len(raw)
            if event in {"completed", "failed", "cancelled", "ready"}:
                request.terminal = True

    def _stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._stop_event.set()
            for request in self._tasks.values():
                if request.translating and request.started and not request.committing:
                    request.cancel.set()
                elif self._configuration is None or not request.started:
                    request.cancel.set()
                    self._send(request, "cancelled", {})
            if self._configuration_worker is not None and not self._queue_stopped:
                self._queue_stopped = True
                self._configuration_queue.put(None)

    def _join_workers(self) -> None:
        with self._lock:
            workers = tuple(self._workers)
        if self._configuration is not None:
            for worker in workers:
                worker.join()
            return
        deadline = time.monotonic() + 2
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        if any(worker.is_alive() for worker in workers):
            self._worker_failed = True
            self._log("shutdown_timeout")

    def _configuration_loop(self):
        try:
            while True:
                job = self._configuration_queue.get()
                if job is None:
                    return
                request, payload = job
                try:
                    with self._lock:
                        if request.terminal:
                            continue
                        request.started = True
                        self._send(request, "started", {"operation": payload["operation"]})
                    if payload["operation"] in HISTORY_OPERATIONS:
                        result = self._configuration.perform_history(payload, request.id, request.seq)
                    else:
                        result = self._configuration.perform(payload)
                    self._send(request, "completed", result)
                except ConfigurationError as error:
                    self._send(request, "failed", {"code": error.code})
                except (ProtocolError, OSError):
                    self._worker_failed = True
                    self._log("internal_error")
                    self._send(request, "failed", {"code": "internal_error"})
                    self._stop()
                finally:
                    with self._lock:
                        self._tasks.pop(request.id, None)
        finally:
            with self._lock:
                self._workers.discard(threading.current_thread())

    def _queue_configuration(self, request, payload):
        if self._configuration_worker is None:
            worker = threading.Thread(target=self._configuration_loop,
                                      name="cc-macos-configuration", daemon=False)
            self._workers.add(worker)
            try:
                worker.start()
            except RuntimeError:
                self._workers.discard(worker)
                self._tasks.pop(request.id, None)
                self._worker_failed = True
                self._log("worker_start_failed")
                self._send(request, "failed", {"code": "worker_start_failed"})
                return False
            self._configuration_worker = worker
        self._configuration_queue.put((request, payload))
        return True

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

    def _translate(self, request, payload):
        def delta(text):
            self._send(request, "delta", {"text": text, "submitted": True})

        def begin_finish():
            with self._lock:
                if request.cancel.is_set() or request.terminal:
                    return False
                request.committing = True
                return True

        try:
            with self._lock:
                if request.terminal:
                    return
                request.started = True
                self._send(request, "started", {"operation": payload["operation"]})
            execute = (self._configuration.result_action if payload["operation"] == "result_action"
                       else self._configuration.translate)
            event, result = execute(payload, request.cancel, delta, begin_finish)
            self._send(request, event, result)
        except ConfigurationError as error:
            self._send(request, "failed", {"code": error.code, "submitted": error.submitted})
        except (ProtocolError, OSError):
            self._worker_failed = True
            self._log("internal_error")
            self._send(_Request(RESERVED_ID), "failed", {"code": "internal_error"})
            self._stop()
        finally:
            with self._lock:
                self._tasks.pop(request.id, None)
                self._workers.discard(threading.current_thread())

    def _start_translation(self, request, payload):
        worker = threading.Thread(target=self._translate, args=(request, payload),
                                  name="cc-macos-translation", daemon=False)
        self._workers.add(worker)
        try:
            worker.start()
        except RuntimeError:
            self._workers.discard(worker)
            self._tasks.pop(request.id, None)
            self._worker_failed = True
            self._log("worker_start_failed")
            self._send(request, "failed", {"code": "worker_start_failed"})
            return False
        return True

    def _payload_error(self, payload: dict) -> str | None:
        operation = payload.get("operation")
        if self._configuration is not None:
            if operation in ("translate", "result_action") and self._translation_enabled:
                try:
                    validate = (self._configuration.validate_result_action_request
                                if operation == "result_action"
                                else self._configuration.validate_translation_request)
                    validate(payload)
                except ProtocolError as error:
                    return error.code
                return None
            if operation == "config_load":
                return None if set(payload) == {"operation"} else "invalid_payload"
            if operation == "config_save":
                if set(payload) != {"operation", "config"}:
                    return "invalid_payload"
                try:
                    validate_save(payload["config"])
                except ProtocolError as error:
                    return error.code
                return None
            if operation in HISTORY_OPERATIONS:
                try:
                    validate_history_request(payload)
                except ProtocolError as error:
                    return error.code
                return None
            return "unsupported_operation"
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
            if self._configuration is not None:
                try:
                    self._configuration.open()
                except ConfigurationError as error:
                    self._worker_failed = True
                    self._send(control, "failed", {"code": error.code})
                    return False
            self._ready = True
            capabilities = (["fixture", "runtime_probe"] if self._configuration is None
                            else ["config_load", "config_save", *HISTORY_OPERATIONS])
            if self._translation_enabled:
                capabilities.extend(("translate", "result_action"))
            self._send(control, "ready", {
                "protocol": VERSION, "capabilities": capabilities,
                "max_frame_bytes": MAX_FRAME_BYTES, "fixture": self._configuration is None,
                **({"backend": "native_appserver"} if self._translation_enabled else {}),
            })
        elif type_ == "hello":
            raise ProtocolError("duplicate_handshake")
        elif type_ == "shutdown":
            if payload:
                raise ProtocolError("invalid_payload")
            self._stop()
            if self._configuration is None:
                self._send(control, "completed", {})
            else:
                self._shutdown = control
            return False
        elif type_ == "cancel":
            if set(payload) != {"request_id"} or not valid_id(payload["request_id"]):
                raise ProtocolError("invalid_payload")
            with self._lock:
                target = self._tasks.get(payload["request_id"])
                active = (target is not None and not target.terminal
                          and (self._configuration is None or not target.started
                               or target.translating and not target.committing))
                if active:
                    target.cancel.set()
                    if not target.started:
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
                control.translating = payload["operation"] in ("translate", "result_action")
                self._send(control, "accepted", {"operation": payload["operation"]})
                if self._pipe_closed:
                    self._tasks.pop(id_, None)
                    return False
                if self._configuration is not None:
                    if control.translating:
                        return self._start_translation(control, payload)
                    return self._queue_configuration(control, payload)
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
        result = 0
        try:
            reader = (PipeFrameReader(self.stdin, self._read_stop)
                      if self._configuration is not None else None)
            while not self._read_stop.is_set():
                message = reader.read() if reader is not None else read_frame(self.stdin)
                if message is None or self._read_stop.is_set() or not self._handle(message):
                    break
        except ProtocolError as exc:
            self._send(_Request(RESERVED_ID), "failed", {"code": exc.code})
            self._log(exc.code)
            result = 2
        except OSError:
            self._log("pipe_error")
            result = 2
        finally:
            self._stop()
            self._join_workers()
            if self._configuration is not None:
                try:
                    self._configuration.close()
                except (OSError, ConfigurationError) as error:
                    result = 2
                    code = error.code if isinstance(error, ConfigurationError) else "state_io_failed"
                    self._log(code)
                    if self._shutdown is not None:
                        self._send(self._shutdown, "failed", {"code": code})
                else:
                    if self._shutdown is not None:
                        self._send(self._shutdown, "completed", {})
        if self._read_stop.signal_number:
            return 128 + self._read_stop.signal_number
        return 2 if result or self._worker_failed or self._pipe_closed else 0


def main(arguments=None) -> int:
    try:
        configuration = startup_configuration(sys.argv[1:] if arguments is None else arguments,
                                              environment=os.environ)
    except ProtocolError:
        sys.stderr.write("cc_macos:invalid_startup\n")
        return 2
    server = Server(sys.stdin.buffer, sys.stdout.buffer, sys.stderr, configuration=configuration)
    if not server._translation_enabled:
        return server.run()
    previous = signal.signal(signal.SIGTERM, server.request_termination)
    try:
        return server.run()
    finally:
        signal.signal(signal.SIGTERM, previous)
