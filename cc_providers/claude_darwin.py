"""Explicit one-shot Claude CLI provider; no discovery, version gate, or replay."""

import base64
import json
import math
import os
import stat
import sys
import threading
import time
from types import MappingProxyType

from cc_macos.image import MAX_IMAGE_BYTES, PNG_SIGNATURE
from .base import CLAUDE_PROVIDER, ProviderCapabilities, ProviderRequest, ProviderResult, ProviderStatus
from .claude_jsonl import ClaudeOutput, ClaudeOutputError, MAX_OUTPUT_BYTES
from .codex_catalog import CatalogProbeError
from .darwin_print import stream_output
from .darwin_process import ProcessError, ProviderOperation, absolute_path, check_cancel


MAX_INPUT_BYTES = 128 * 1024 * 1024


def _input(request, operation):
    content = [{"type": "text", "text": request.user_text}]
    if request.image_paths:
        check_cancel(operation)
        # The translation session supplies its validated private PNG, not a CLI @path.
        fd = os.open(request.image_paths[0], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | getattr(os, "O_BINARY", 0))
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
                raise ProcessError("image_unavailable")
            if before.st_size > MAX_IMAGE_BYTES:
                raise ProcessError("image_too_large")
            data = bytearray()
            while len(data) <= before.st_size:
                check_cancel(operation)
                chunk = os.read(fd, min(65_536, before.st_size + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(fd)
            if (len(data) != before.st_size or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns):
                raise ProcessError("image_changed")
            if not data.startswith(PNG_SIGNATURE):
                raise ProcessError("image_unavailable")
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(data).decode("ascii")}})
        finally:
            try:
                os.close(fd)
            except OSError:
                raise ProcessError("provider_cleanup_failed") from None
    check_cancel(operation)
    data = (json.dumps({"type": "user", "message": {"role": "user", "content": content}},
                       ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > MAX_INPUT_BYTES:
        raise ProcessError("probe_input_limit")
    return data


class DarwinClaudeProvider:
    provider_id = CLAUDE_PROVIDER
    capabilities = ProviderCapabilities(text=True, images=True, streaming=True, warm_sessions=False)

    def __init__(self, command, work_dir, *, environment, log_error):
        if sys.platform != "darwin":
            raise ValueError("darwin_required")
        if not callable(log_error):
            raise TypeError("log_error must be callable")
        self.command, self.work_dir = absolute_path(command), absolute_path(work_dir)
        if not isinstance(environment, dict) or any(
                type(key) is not str or type(value) is not str or not key
                or "=" in key or "\0" in key or "\0" in value
                for key, value in environment.items()):
            raise TypeError("explicit_provider_environment_required")
        absolute_path(environment.get("HOME", ""))
        self.env = MappingProxyType(dict(environment))
        self._closing = threading.Event()
        self._operation_lock = threading.RLock()
        self._active_thread = None
        self._fatal = None

    def _command(self, request):
        # Keep OAuth/keychain auth: --bare deliberately does not load those credentials.
        args = [
            self.command, "--print", "--input-format", "stream-json",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--system-prompt", request.system_prompt, "--tools", "",
            "--disallowedTools", "mcp__*", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "dontAsk",
            "--disable-slash-commands", "--setting-sources=",
            "--settings", '{"disableAllHooks":true}', "--no-session-persistence",
        ]
        if request.model:
            args.append("--model=" + request.model)
        return args

    @staticmethod
    def _request(request):
        if not isinstance(request, ProviderRequest):
            raise TypeError("request must be ProviderRequest")
        if (type(request.task) is not str or type(request.image_paths) not in (tuple, list)
                or type(request.user_text) is not str or type(request.system_prompt) is not str
                or type(request.model) not in (str, type(None))
                or type(request.timeout_seconds) not in (int, float)
                or not math.isfinite(request.timeout_seconds) or request.timeout_seconds <= 0):
            raise ValueError("invalid_provider_request")
        if request.task == "image":
            if len(request.image_paths) != 1:
                return None
            paths = tuple(absolute_path(path) for path in request.image_paths)
        elif request.task in ("text", "translation_summary") and not request.image_paths:
            paths = ()
        else:
            return None
        request.user_text.encode("utf-8")
        for value in (request.system_prompt, request.model or "", *paths):
            value.encode("utf-8")
            if "\0" in value:
                raise ValueError("invalid_provider_request")
        return ProviderRequest(request.task, request.model, request.system_prompt, request.user_text,
                               image_paths=paths, timeout_seconds=request.timeout_seconds)

    def complete(self, request, cancel_event=None):
        return self.stream(request, lambda _delta: None, cancel_event)

    def stream(self, request, on_delta, cancel_event=None):
        if not callable(on_delta):
            raise TypeError("on_delta must be callable")
        request = self._request(request)
        if request is None:
            return ProviderResult(False, error_code="unsupported_task", metrics=(("turn_submitted", False),))
        operation = ProviderOperation(request.timeout_seconds, self._closing, cancel_event,
                                      closing_code="cancelled")
        while True:
            code = self._fatal or operation.code()
            if code:
                return ProviderResult(False, error_code=code, metrics=(("turn_submitted", False),))
            if self._operation_lock.acquire(timeout=0.05):
                break
        entered = False
        try:
            if self._active_thread is not None:
                raise RuntimeError("provider_reentrant_request")
            self._active_thread = threading.get_ident()
            entered = True
            output = ClaudeOutput(on_delta)
            text, code = "", ""
            try:
                check_cancel(operation)
                if self._fatal:
                    raise ProcessError(self._fatal)
                data = _input(request, operation)
                check_cancel(operation)
                os.makedirs(self.work_dir, exist_ok=True)
                stream_output(
                    self._command(request), self.env, self.work_dir, data, output.line,
                    cancel_event=operation, timeout=operation.deadline - time.monotonic(),
                    max_bytes=MAX_OUTPUT_BYTES, max_input_bytes=MAX_INPUT_BYTES,
                    on_write=operation.wrote_turn)
                text = output.finish()
            except (ProcessError, ClaudeOutputError) as error:
                code = str(error)
                if "cleanup_failed" in code:
                    self._fatal = "provider_cleanup_failed"
            except OSError:
                code = "provider_failed"
            code = self._fatal or operation.code() or code
            return ProviderResult(not code, text=text if not code else "", error_code=code,
                                  metrics=(("turn_submitted", operation.submitted),))
        finally:
            if entered:
                self._active_thread = None
            self._operation_lock.release()

    def model_catalog(self, cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            raise CatalogProbeError("cancelled")
        raise CatalogProbeError("model_catalog_unavailable")

    def diagnose(self):
        return ProviderStatus(installed=os.path.isfile(self.command), authenticated=None,
                              command=self.command, backend="native_print")

    def shutdown(self, *, require_cleanup=False):
        self._closing.set()
        with self._operation_lock:
            if require_cleanup and (self._fatal or self._active_thread is not None):
                raise ProcessError(self._fatal or "provider_cleanup_failed")
