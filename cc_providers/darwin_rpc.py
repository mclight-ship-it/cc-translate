"""Bounded synchronous stdio transport with a single Darwin process owner."""

from contextlib import contextmanager
import os
import selectors
import sys
import threading
import time

from .darwin_process import OwnedProcess, ProcessError


class RpcError(ProcessError):
    pass


class RpcProcess:
    """Unknown operation errors propagate; cleanup errors are chained and sticky."""

    def __init__(self, args, env, work_dir, *, max_bytes=8 * 1024 * 1024):
        self._lock = threading.Lock()
        self._active_thread = None
        self._closing = threading.Event()
        self._closed = False
        self._close_error = None
        self.owner = None
        self._selector = None
        self._buffer = bytearray()
        self._received = 0
        self._eof = False
        self._max_bytes = max_bytes
        initialized = False
        try:
            if type(max_bytes) is not int or max_bytes < 0:
                raise RpcError("rpc_io_failed")
            self.owner = OwnedProcess(args, env, work_dir, rpc=True)
            try:
                self._selector = selectors.DefaultSelector()
                self._stdin = self.owner.process.stdin
                self._stdout = self.owner.process.stdout
                self._input_fd = self._stdin.fileno()
                self._output_fd = self._stdout.fileno()
                os.set_blocking(self._input_fd, False)
                os.set_blocking(self._output_fd, False)
                self._selector.register(self._stdout, selectors.EVENT_READ)
            except (OSError, ValueError, KeyError):
                raise RpcError("rpc_io_failed") from None
            initialized = True
        finally:
            if not initialized:
                self.close()

    def _check(self, deadline=None, cancel_event=None):
        if self._closing.is_set():
            raise RpcError("rpc_closed")
        if cancel_event is not None and cancel_event.is_set():
            raise RpcError("rpc_cancelled")
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise RpcError("rpc_timeout")
        return 0.05 if remaining is None else min(0.05, remaining)

    @contextmanager
    def _operation(self, deadline=None, cancel_event=None):
        acquired = False
        try:
            self._check(deadline, cancel_event)
            if self._active_thread == threading.get_ident():
                raise RpcError("rpc_io_failed")
            while not acquired:
                acquired = self._lock.acquire(timeout=self._check(deadline, cancel_event))
            self._active_thread = threading.get_ident()
            self._check(deadline, cancel_event)
            yield
        finally:
            if acquired:
                self._active_thread = None
                self._lock.release()

    def _events(self, deadline, cancel_event):
        timeout = self._check(deadline, cancel_event)
        try:
            return self._selector.select(timeout)
        except (OSError, ValueError):
            raise RpcError("rpc_io_failed") from None

    def _running(self):
        if self.owner.has_exited():
            # The unreaped leader still pins its process group. Kill inherited
            # pipe holders before reaping, even when buffered output is ready.
            self.owner.terminate()
            return False
        return True

    def _read(self):
        try:
            data = os.read(self._output_fd, min(16_384, max(1, self._max_bytes - self._received + 1)))
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            raise RpcError("rpc_io_failed") from None
        if not data:
            self._eof = True
            try:
                self._selector.unregister(self._stdout)
            except (OSError, ValueError, KeyError):
                raise RpcError("rpc_io_failed") from None
            return
        self._received += len(data)
        if self._received > self._max_bytes:
            raise RpcError("rpc_output_limit")
        self._buffer.extend(data)

    def send(self, data: bytes, *, deadline, cancel_event=None, on_write=None) -> None:
        with self._operation(deadline, cancel_event):
            if self._eof:
                raise RpcError("rpc_closed")
            if not isinstance(data, bytes):
                raise RpcError("rpc_io_failed")
            if len(data) > self._max_bytes:
                raise RpcError("rpc_input_limit")
            if on_write is not None and not callable(on_write):
                raise TypeError("on_write must be callable")
            pending = memoryview(data)
            if not pending:
                return
            try:
                self._selector.register(self._stdin, selectors.EVENT_WRITE)
            except (OSError, ValueError, KeyError):
                raise RpcError("rpc_io_failed") from None
            failed = True
            notified = False
            try:
                while pending:
                    self._check(deadline, cancel_event)
                    if not self._running():
                        raise RpcError("rpc_closed")
                    events = self._events(deadline, cancel_event)
                    # Drain output before writing: a server can fill stdout
                    # while it is still waiting for the rest of the request.
                    for key, _mask in events:
                        self._check(deadline, cancel_event)
                        if key.fileobj is self._stdout:
                            self._read()
                    if self._eof:
                        raise RpcError("rpc_closed")
                    for key, _mask in events:
                        self._check(deadline, cancel_event)
                        if key.fileobj is not self._stdin:
                            continue
                        try:
                            written = os.write(self._input_fd, pending)
                        except (BlockingIOError, InterruptedError):
                            continue
                        except OSError:
                            raise RpcError("rpc_io_failed") from None
                        if written <= 0:
                            raise RpcError("rpc_io_failed")
                        if not notified:
                            notified = True
                            if on_write is not None:
                                on_write()
                        pending = pending[written:]
                self._check(deadline, cancel_event)
                failed = False
            finally:
                try:
                    self._selector.unregister(self._stdin)
                except (OSError, ValueError, KeyError):
                    if not failed:
                        raise RpcError("rpc_io_failed") from None

    def receive(self, *, deadline, cancel_event=None) -> str | None:
        """Return one UTF-8 line without LF, including empty lines."""
        with self._operation(deadline, cancel_event):
            while True:
                self._check(deadline, cancel_event)
                self._running()
                self._check(deadline, cancel_event)
                end = self._buffer.find(b"\n")
                if end >= 0:
                    line = bytes(self._buffer[:end])
                    del self._buffer[:end + 1]
                    try:
                        return line.decode("utf-8")
                    except UnicodeError:
                        raise RpcError("rpc_invalid_utf8") from None
                if self._eof:
                    if self._buffer:
                        raise RpcError("rpc_truncated")
                    return None
                for key, _mask in self._events(deadline, cancel_event):
                    self._check(deadline, cancel_event)
                    if key.fileobj is self._stdout:
                        self._read()

    def is_running(self) -> bool:
        with self._operation():
            return self._running()

    def reset_budget(self) -> None:
        with self._operation():
            self._received = len(self._buffer)

    def close(self) -> None:
        self._closing.set()
        # A synchronous on_write callback cannot wait for its own operation.
        # Leave closing set: the caller must close again after that operation.
        if self._active_thread == threading.get_ident():
            raise RpcError("rpc_closed")
        with self._lock:
            if self._closed:
                if self._close_error is not None:
                    raise self._close_error
                return
            self._closed = True
            closed = False
            try:
                try:
                    if self._selector is not None:
                        self._selector.close()
                finally:
                    if self.owner is not None:
                        self.owner.close()
                closed = True
            finally:
                if not closed:
                    error = sys.exception()
                    if isinstance(error, ProcessError) and str(error) in {
                            "probe_cleanup_failed", "rpc_cleanup_failed"}:
                        self._close_error = error
                    else:
                        self._close_error = RpcError("rpc_cleanup_failed")
                        raise self._close_error from error
