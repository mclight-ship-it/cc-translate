"""One-shot CLI input/EOF and UTF-8 lines using the existing Darwin group owner."""

import math
import os
import selectors
import sys
import threading
import time

from .darwin_process import OwnedProcess, ProcessError, check_cancel, close_selector


class IdlePrintProcess:
    """One unused child. Only the idle reader owns it until take() joins that reader."""

    def __init__(self, args, env, work_dir, *, closing, cancel_event, on_error,
                 idle_seconds=600, max_bytes=256 * 1024):
        self.owner = None
        self.stdout = bytearray()
        self.received = 0
        self.error = ""
        self._unexpected = None
        self._closing = closing
        self._cancel = cancel_event
        self._on_error = on_error
        self._deadline = time.monotonic() + idle_seconds
        self._max_bytes = max_bytes
        self._stop = threading.Event()
        self._claimed = False
        self._thread = None
        selector = None
        started = False
        try:
            check_cancel(closing)
            check_cancel(cancel_event)
            self.owner = OwnedProcess(args, env, work_dir, input_pipe=True)
            selector = selectors.DefaultSelector()
            for stream in (self.owner.process.stdout, self.owner.process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            self._thread = threading.Thread(target=self._read_idle, args=(selector,),
                                            name="claude-idle-print", daemon=True)
            self._thread.start()
            started = True
        finally:
            if not started:
                try:
                    if selector is not None:
                        close_selector(selector)
                finally:
                    if self.owner is not None:
                        self.owner.close()
                        self.owner = None

    def _check(self):
        check_cancel(self._closing)
        check_cancel(self._cancel)
        if time.monotonic() >= self._deadline:
            raise ProcessError("probe_timeout")
        if self.owner.has_exited():
            raise ProcessError("probe_failed")

    def _read_idle(self, selector):
        completed = False
        try:
            while not self._stop.is_set():
                self._check()
                try:
                    events = selector.select(0.05)
                except (ValueError, KeyError):
                    raise ProcessError("probe_failed") from None
                for key, _mask in events:
                    if self._stop.is_set():
                        break
                    self._check()
                    try:
                        chunk = os.read(key.fileobj.fileno(),
                                        min(16_384, self._max_bytes - self.received + 1))
                    except (BlockingIOError, InterruptedError):
                        continue
                    if not chunk:
                        raise ProcessError("probe_failed")
                    self.received += len(chunk)
                    if self.received > self._max_bytes:
                        raise ProcessError("probe_output_limit")
                    if key.fileobj is self.owner.process.stdout:
                        self.stdout.extend(chunk)
            if self._claimed:
                self._check()
            completed = True
        except ProcessError as error:
            self.error = str(error)
        except OSError:
            self.error = "probe_failed"
        finally:
            try:
                self._finish_idle(selector, completed and self._claimed and not self.error)
            finally:
                # Do not hide a worker bug behind a successful cold fallback at take().
                self._unexpected = sys.exc_info()[1]

    def _finish_idle(self, selector, handoff):
        selector_closed = False
        try:
            try:
                close_selector(selector)
                selector_closed = True
            except ProcessError:
                self.error = "probe_cleanup_failed"
        finally:
            if not handoff or not selector_closed:
                try:
                    self.owner.close()
                except (ProcessError, OSError):
                    self.error = "probe_cleanup_failed"
                finally:
                    self.owner = None
                    self.stdout.clear()
            if self.error:
                self._on_error(self.error)

    @property
    def available(self):
        return (not self._stop.is_set() and not self.error
                and self._thread.is_alive() and time.monotonic() < self._deadline)

    def _join(self):
        self._stop.set()
        self._thread.join()
        self.check_failure()

    def check_failure(self):
        if "cleanup_failed" in self.error:
            raise ProcessError("probe_cleanup_failed")
        if self._unexpected is not None:
            raise self._unexpected

    def take(self):
        self._claimed = True
        self._join()
        owner, self.owner = self.owner, None
        if owner is None:
            return None
        stdout, self.stdout = bytes(self.stdout), bytearray()
        return owner, stdout, self.received

    def close(self):
        self._join()


def stream_output(args, env, work_dir, data, on_line, *, cancel_event, timeout,
                  max_bytes=8 * 1024 * 1024, on_write=None, max_input_bytes=None,
                  owned_process=None, initial_stdout=b"", initial_received=0):
    """Drain both outputs; an injected owner transfers here even on invalid input."""
    try:
        return _stream_output(
            args, env, work_dir, data, on_line, cancel_event=cancel_event, timeout=timeout,
            max_bytes=max_bytes, on_write=on_write, max_input_bytes=max_input_bytes,
            owned_process=owned_process, initial_stdout=initial_stdout,
            initial_received=initial_received)
    finally:
        if owned_process is not None:
            owned_process.close()


def _stream_output(args, env, work_dir, data, on_line, *, cancel_event, timeout,
                   max_bytes, on_write, max_input_bytes, owned_process,
                   initial_stdout, initial_received):
    if (type(data) is not bytes or type(max_bytes) is not int or max_bytes < 1
            or type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not callable(on_line) or on_write is not None and not callable(on_write)
            or type(initial_stdout) is not bytes or type(initial_received) is not int
            or initial_received < len(initial_stdout)):
        raise ProcessError("probe_invalid_input")
    if max_input_bytes is None:
        max_input_bytes = max_bytes
    if type(max_input_bytes) is not int or max_input_bytes < 1:
        raise ProcessError("probe_invalid_input")
    if len(data) > max_input_bytes:
        raise ProcessError("probe_input_limit")
    if initial_received > max_bytes:
        raise ProcessError("probe_output_limit")
    check_cancel(cancel_event)
    if timeout <= 0:
        raise ProcessError("probe_timeout")
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    owner = None
    pending = memoryview(data)
    buffer = bytearray(initial_stdout)
    received = initial_received
    wrote = False
    input_open = False

    def check():
        check_cancel(cancel_event)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProcessError("probe_timeout")
        return min(0.05, remaining)

    def emit(raw):
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise ProcessError("probe_invalid_utf8") from None
        check()
        on_line(text)

    def close_input():
        nonlocal input_open
        if input_open:
            selector.unregister(owner.process.stdin)
            owner.process.stdin.close()
            input_open = False

    def emit_lines():
        while True:
            end = buffer.find(b"\n")
            if end < 0:
                break
            line = bytes(buffer[:end])
            del buffer[:end + 1]
            emit(line)

    try:
        owner = owned_process if owned_process is not None else OwnedProcess(
            args, env, work_dir, input_pipe=True)
        for stream in (owner.process.stdout, owner.process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        if pending:
            os.set_blocking(owner.process.stdin.fileno(), False)
            selector.register(owner.process.stdin, selectors.EVENT_WRITE)
            input_open = True
        else:
            owner.process.stdin.close()
        emit_lines()
        while selector.get_map() or not owner.finished:
            check()
            if not owner.finished and owner.has_exited():
                # Reap only after the last group signal, including inherited pipe holders.
                owner.terminate()
                close_input()
            events = selector.select(check())
            # A CLI can fill either output before reading the rest of its input.
            for key, _mask in events:
                stream = key.fileobj
                if stream is owner.process.stdin:
                    continue
                check()
                try:
                    chunk = os.read(stream.fileno(), min(16_384, max_bytes - received + 1))
                except (BlockingIOError, InterruptedError):
                    continue
                if not chunk:
                    selector.unregister(stream)
                    if stream is owner.process.stdout and buffer:
                        emit(bytes(buffer))
                        buffer.clear()
                    continue
                received += len(chunk)
                if received > max_bytes:
                    raise ProcessError("probe_output_limit")
                if stream is owner.process.stdout:
                    buffer.extend(chunk)
                    emit_lines()
            for key, _mask in events:
                if not input_open or key.fileobj is not owner.process.stdin:
                    continue
                check()
                try:
                    count = os.write(owner.process.stdin.fileno(), pending)
                except (BlockingIOError, InterruptedError):
                    continue
                if count <= 0:
                    raise ProcessError("probe_failed")
                if not wrote:
                    wrote = True
                    if on_write is not None:
                        on_write()
                pending = pending[count:]
                if not pending:
                    close_input()
        check()
        if pending or owner.process.returncode:
            raise ProcessError("probe_failed")
    except (OSError, ValueError, KeyError):
        raise ProcessError("probe_failed") from None
    finally:
        try:
            close_selector(selector)
        finally:
            if owner is not None and owned_process is None:
                owner.close()
