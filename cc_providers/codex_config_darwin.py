"""Bounded native config RPC with sole ownership of an unreaped Darwin group."""

import json
import os
import selectors
import time

from .codex_config import CodexConfigError
from .darwin_process import OwnedProcess, ProcessError


PROBE_TIMEOUT = 8
MAX_CONFIG_BYTES = 8 * 1024 * 1024


class _ConfigSession:
    def __init__(self, args, env, work_dir, cancel_event=None):
        self.selector = selectors.DefaultSelector()
        self.buffer = bytearray()
        self.received = 0
        self.eof = False
        self.closed = False
        self.cancel_event = cancel_event
        self.deadline = time.monotonic() + PROBE_TIMEOUT
        try:
            self.owner = OwnedProcess(args, env, work_dir, rpc=True)
            self.process = self.owner.process
        except ProcessError:
            self.selector.close()
            raise
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
        self.owner.close()


def read_config(args, env, work_dir, *, cancel_event=None):
    try:
        return _read_config(args, env, work_dir, cancel_event=cancel_event)
    except ProcessError as error:
        raise CodexConfigError("config_" + str(error)) from None


def _read_config(args, env, work_dir, *, cancel_event=None):
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
