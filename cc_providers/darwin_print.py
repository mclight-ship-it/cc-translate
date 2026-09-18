"""One-shot CLI input/EOF and UTF-8 lines using the existing Darwin group owner."""

import math
import os
import selectors
import time

from .darwin_process import OwnedProcess, ProcessError, check_cancel, close_selector


def stream_output(args, env, work_dir, data, on_line, *, cancel_event, timeout,
                  max_bytes=8 * 1024 * 1024, on_write=None, max_input_bytes=None):
    """Drain both outputs while writing; success requires EOF and a zero exit."""
    if (type(data) is not bytes or type(max_bytes) is not int or max_bytes < 1
            or type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not callable(on_line) or on_write is not None and not callable(on_write)):
        raise ProcessError("probe_invalid_input")
    if max_input_bytes is None:
        max_input_bytes = max_bytes
    if type(max_input_bytes) is not int or max_input_bytes < 1:
        raise ProcessError("probe_invalid_input")
    if len(data) > max_input_bytes:
        raise ProcessError("probe_input_limit")
    check_cancel(cancel_event)
    if timeout <= 0:
        raise ProcessError("probe_timeout")
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    owner = None
    pending = memoryview(data)
    buffer = bytearray()
    received = 0
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

    try:
        owner = OwnedProcess(args, env, work_dir, input_pipe=True)
        for stream in (owner.process.stdout, owner.process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        if pending:
            os.set_blocking(owner.process.stdin.fileno(), False)
            selector.register(owner.process.stdin, selectors.EVENT_WRITE)
            input_open = True
        else:
            owner.process.stdin.close()
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
                    while True:
                        end = buffer.find(b"\n")
                        if end < 0:
                            break
                        line = bytes(buffer[:end])
                        del buffer[:end + 1]
                        emit(line)
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
            if owner is not None:
                owner.close()
