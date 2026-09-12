"""Bounded native config RPC with sole ownership of an unreaped Darwin group."""

import json
import errno
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from .codex_config import CodexConfigError


PROBE_TIMEOUT = 8
MAX_CONFIG_BYTES = 8 * 1024 * 1024


def _load_supervision():
    import ctypes

    core = Path(__file__).resolve().parents[1]
    contents = core.parent.parent
    library = contents / "Helpers/python/lib/libCCProcessSupport.dylib"
    if (core.name != "Core" or core.parent.name != "Resources" or contents.name != "Contents"
            or not library.is_file() or library.is_symlink()
            or not library.resolve().is_relative_to(contents.resolve())):
        raise CodexConfigError("config_runtime_unavailable")
    try:
        bridge = ctypes.CDLL(str(library))
        bridge.cc_process_support_abi.argtypes = []
        bridge.cc_process_support_abi.restype = ctypes.c_int
        bridge.cc_cli_signal_group.argtypes = [ctypes.c_int, ctypes.c_int]
        bridge.cc_cli_signal_group.restype = ctypes.c_int
        if bridge.cc_process_support_abi() != 1:
            raise CodexConfigError("config_runtime_unavailable")
        return bridge
    except (OSError, AttributeError):
        raise CodexConfigError("config_runtime_unavailable") from None


class _ConfigSession:
    def __init__(self, args, env, work_dir, cancel_event=None):
        self.bridge = _load_supervision()
        self.selector = selectors.DefaultSelector()
        self.buffer = bytearray()
        self.received = 0
        self.eof = False
        self.closed = False
        self.cancel_event = cancel_event
        self.deadline = time.monotonic() + PROBE_TIMEOUT
        try:
            self.process = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, cwd=work_dir, start_new_session=True, bufsize=0,
            )
        except (OSError, ValueError):
            self.selector.close()
            raise CodexConfigError("config_probe_unavailable") from None
        try:
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
        except (OSError, ValueError):
            self.close()
            raise CodexConfigError("config_probe_failed") from None

    def _check(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise CodexConfigError("config_probe_cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CodexConfigError("config_probe_timeout")
        return remaining

    def _events(self):
        return self.selector.select(min(0.05, self._check()))

    def _read(self):
        try:
            data = os.read(self.process.stdout.fileno(), 16_384)
        except BlockingIOError:
            return
        if not data:
            self.eof = True
            self.selector.unregister(self.process.stdout)
            return
        self.received += len(data)
        if self.received > MAX_CONFIG_BYTES:
            raise CodexConfigError("config_probe_output_limit")
        self.buffer.extend(data)

    def rpc(self, method, params, identifier):
        pending = memoryview((json.dumps({
            "id": identifier, "method": method, "params": params,
        }) + "\n").encode("utf-8"))
        self.selector.register(self.process.stdin, selectors.EVENT_WRITE)
        try:
            while pending:
                for key, _ in self._events():
                    if key.fileobj is self.process.stdout:
                        self._read()
                    else:
                        try:
                            written = os.write(self.process.stdin.fileno(), pending)
                        except BlockingIOError:
                            continue
                        if written <= 0:
                            raise CodexConfigError("config_probe_failed")
                        pending = pending[written:]
        finally:
            self.selector.unregister(self.process.stdin)
        while True:
            self._check()
            if b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                self.buffer = rest
                try:
                    message = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeError, RecursionError):
                    raise CodexConfigError("config_probe_protocol") from None
                if not isinstance(message, dict):
                    raise CodexConfigError("config_probe_protocol")
                if type(message.get("id")) is bool:
                    raise CodexConfigError("config_probe_protocol")
                if message.get("id") == identifier:
                    if "error" in message:
                        raise CodexConfigError("config_invalid")
                    return message.get("result")
            elif self.eof:
                raise CodexConfigError("config_invalid")
            else:
                for _key, _ in self._events():
                    self._read()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.selector.close()
        failed = False
        owned = True
        # No Popen poll/wait/communicate may run before the final group signal.
        # The strong reference keeps Popen's destructor from reaping our leader.
        for signum in (signal.SIGTERM, signal.SIGKILL):
            error = self.bridge.cc_cli_signal_group(self.process.pid, signum)
            if error != 0:
                failed = True
            if error == errno.ECHILD:
                owned = False
                self.process.returncode = -1
                break
            if signum == signal.SIGTERM:
                time.sleep(0.2)
        try:
            if owned:
                self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            failed = True
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except OSError:
                failed = True
        if failed:
            raise CodexConfigError("config_probe_cleanup_failed")


def read_config(args, env, work_dir, *, cancel_event=None):
    try:
        session = _ConfigSession(args, env, work_dir, cancel_event)
    except OSError:
        raise CodexConfigError("config_probe_unavailable") from None
    try:
        session.rpc("initialize", {"clientInfo": {
            "name": "cc-translate-config", "version": "1"}}, 1)
        result = session.rpc("config/read", {
            "includeLayers": True, "cwd": os.path.abspath(work_dir)}, 2)
        if not isinstance(result, dict) or not isinstance(result.get("config"), dict):
            raise CodexConfigError("config_probe_protocol")
        return result
    except (OSError, UnicodeError):
        raise CodexConfigError("config_probe_failed") from None
    finally:
        session.close()
