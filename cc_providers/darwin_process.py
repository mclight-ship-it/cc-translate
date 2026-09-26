"""Private bundled Darwin process ownership shared by bounded native probes."""

import ctypes
import errno
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time


class ProcessError(RuntimeError):
    pass


def absolute_path(value):
    value = os.fspath(value)
    if type(value) is not str or not value or "\0" in value or not os.path.isabs(value):
        raise ValueError("absolute_provider_path_required")
    return value


class ProviderOperation:
    def __init__(self, timeout, closing, cancel_event, preempt=None, *, closing_code="appserver_shutdown"):
        self.deadline = time.monotonic() + timeout
        self.closing = closing
        self.cancel = cancel_event
        self.preempt = preempt
        self.closing_code = closing_code
        self.submitted = False
        self.interrupt_deadline = None

    def code(self):
        if self.closing.is_set():
            return self.closing_code
        if (self.cancel is not None and self.cancel.is_set()) or (
                self.preempt is not None and self.preempt.is_set()):
            return "cancelled"
        if time.monotonic() >= self.deadline:
            return "timeout"
        return None

    def is_set(self):
        return self.code() is not None

    def wrote_turn(self):
        # A partial write cannot prove the remote turn did not start.
        self.submitted = True


def close_selector(selector):
    try:
        selector.close()
    except (OSError, ValueError):
        raise ProcessError("probe_cleanup_failed") from None


def load_supervision():
    core = Path(__file__).resolve().parents[1]
    contents = core.parent.parent
    library = contents / "Resources/python/lib/libCCProcessSupport.dylib"
    if (core.name != "Core" or core.parent.name != "Resources" or contents.name != "Contents"
            or not library.is_file() or library.is_symlink()
            or not library.resolve().is_relative_to(contents.resolve())):
        raise ProcessError("runtime_unavailable")
    try:
        bridge = ctypes.CDLL(str(library))
        bridge.cc_process_support_abi.argtypes = []
        bridge.cc_process_support_abi.restype = ctypes.c_int
        bridge.cc_cli_signal_group.argtypes = [ctypes.c_int, ctypes.c_int]
        bridge.cc_cli_signal_group.restype = ctypes.c_int
        bridge.cc_cli_has_exited.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        bridge.cc_cli_has_exited.restype = ctypes.c_int
        if bridge.cc_process_support_abi() != 1:
            raise ProcessError("runtime_unavailable")
        return bridge
    except (OSError, AttributeError):
        raise ProcessError("runtime_unavailable") from None


class OwnedProcess:
    def __init__(self, args, env, work_dir, *, rpc=False, input_pipe=False):
        self.bridge = load_supervision()
        self.finished = False
        self.closed = False
        self.owned = True
        try:
            self.process = subprocess.Popen(
                args, stdin=subprocess.PIPE if rpc or input_pipe else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL if rpc else subprocess.PIPE,
                env=env, cwd=work_dir, start_new_session=True, bufsize=0,
            )
        except (OSError, ValueError):
            raise ProcessError("probe_unavailable") from None

    def has_exited(self):
        if self.finished:
            return True
        exited = ctypes.c_int()
        error = self.bridge.cc_cli_has_exited(self.process.pid, ctypes.byref(exited))
        if error == errno.ECHILD:
            self.owned = False
            self.process.returncode = -1
            raise ProcessError("probe_cleanup_failed")
        if error:
            raise ProcessError("probe_failed")
        return bool(exited.value)

    def terminate(self):
        if self.finished:
            return
        self.finished = True
        failed = not self.owned
        # Never poll/wait/communicate before the last group signal. A strong
        # Popen reference pins the unreaped leader even after an early exit.
        if self.owned:
            for signum in (signal.SIGTERM, signal.SIGKILL):
                error = self.bridge.cc_cli_signal_group(self.process.pid, signum)
                if error:
                    failed = True
                if error == errno.ECHILD:
                    self.owned = False
                    self.process.returncode = -1
                    break
                if signum == signal.SIGTERM:
                    time.sleep(0.2)
        try:
            if self.owned:
                self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            failed = True
        if failed:
            raise ProcessError("probe_cleanup_failed")

    def close(self):
        if self.closed:
            return
        self.closed = True
        failed = False
        try:
            self.terminate()
        finally:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        failed = True
            if failed:
                raise ProcessError("probe_cleanup_failed")


def check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessError("probe_cancelled")


def capture_output(args, env, work_dir, *, cancel_event, timeout, max_bytes):
    deadline = time.monotonic() + timeout
    owner = None
    selector = selectors.DefaultSelector()
    output = bytearray()
    received = 0
    try:
        check_cancel(cancel_event)
        owner = OwnedProcess(args, env, work_dir)
        for stream in (owner.process.stdout, owner.process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map() or not owner.finished:
            check_cancel(cancel_event)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessError("probe_timeout")
            if not owner.finished and owner.has_exited():
                # Kill inherited pipe holders before waiting for EOF, retaining
                # buffered bytes and the leader's original exit status.
                owner.terminate()
            for key, _ in selector.select(min(0.05, remaining)):
                try:
                    data = os.read(key.fileobj.fileno(), 16_384)
                except BlockingIOError:
                    continue
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                received += len(data)
                if received > max_bytes:
                    raise ProcessError("probe_output_limit")
                if key.fileobj is owner.process.stdout:
                    output.extend(data)
        check_cancel(cancel_event)
        if owner.process.returncode:
            raise ProcessError("probe_failed")
        return bytes(output)
    except (OSError, ValueError):
        raise ProcessError("probe_failed") from None
    finally:
        try:
            close_selector(selector)
        finally:
            if owner is not None:
                owner.close()
