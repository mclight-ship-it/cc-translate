"""Explicit native app-server provider with owned, bounded Darwin subprocesses."""

from types import MappingProxyType
import json
import math
import os
import stat
import sys
import threading
import time

from .base import (
    CODEX_PROVIDER, ProviderCapabilities, ProviderRequest, ProviderResult, ProviderStatus,
)
from .codex_appserver import (
    CodexAppServerProtocolError, CodexAppServerTransport,
)
from .codex_catalog import CodexModelCatalog, CatalogError, CatalogProbeError, parse_codex_version
from .codex_config import child_environment, CodexConfigError
from .darwin_process import (
    capture_output, ProcessError, ProviderOperation as _Operation, absolute_path as _absolute,
)
from .darwin_rpc import RpcProcess, RpcError


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _bad_constant(_value):
    raise ValueError("nonfinite_number")


def _native_message(line):
    try:
        message = json.loads(line, object_pairs_hook=_json_pairs, parse_constant=_bad_constant)
        pending = [(message, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 64 or (type(value) is float and not math.isfinite(value)):
                raise ValueError("invalid_tree")
            if isinstance(value, dict):
                for key in value:
                    key.encode("utf-8")
                pending.extend((child, depth + 1) for child in value.values())
            elif isinstance(value, list):
                pending.extend((child, depth + 1) for child in value)
            elif isinstance(value, str):
                value.encode("utf-8")
        if not isinstance(message, dict):
            raise ValueError("invalid_envelope")
    except (ValueError, TypeError, RecursionError):
        raise CodexAppServerProtocolError("invalid_appserver_json") from None
    if not set(message) <= {"id", "method", "params", "result", "error", "jsonrpc", "emittedAtMs"}:
        raise CodexAppServerProtocolError("invalid_appserver_message")
    if "jsonrpc" in message and message["jsonrpc"] != "2.0":
        raise CodexAppServerProtocolError("invalid_appserver_message")
    # Official ServerNotificationEnvelope metadata is Option<i64>, not a deadline.
    timestamp = message.get("emittedAtMs")
    if timestamp is not None and (type(timestamp) is not int or not -(2 ** 63) <= timestamp < 2 ** 63):
        raise CodexAppServerProtocolError("invalid_appserver_message")
    return message


class _NativeTransport(CodexAppServerTransport):
    """Keep the established RPC/tool policy; replace only process and I/O edges."""

    def __init__(self, command, work_dir, env, catalog, *, operation_lock=None, log_error=None):
        super().__init__(command, work_dir, idle_timeout_seconds=600, env=env, catalog=catalog)
        self.operation = None
        self._bound_operation = None
        self._pending_rpc = {}
        self._thread_id = None
        self._turn_id = None
        self._completed_items = set()
        self.cleanup_failed = threading.Event()
        self._owner_operation_lock = operation_lock or threading.RLock()
        self._log_error = log_error
        self._supported_version_identity = None
        self._verified_process = None
        self._process_version_identity = None
        self._operation_version_identity = None
        self._requested_profile = None
        self.version_check_ms = None
        self.version_cache_hit = 0
        self.warm_process_hit = 0

    def ready_for(self, profile):
        ready = super().ready_for(profile)
        if ready:
            self.warm_process_hit = 1
        return ready

    def warm_up(self, request):
        result = super().warm_up(request)
        if result.ok and not self.operation.is_set():
            # The facade operation lock also owns expiry, including ready reuse.
            self._schedule_idle_shutdown()
        return result

    def _executable_identity(self):
        try:
            path = os.path.realpath(self.command)
            info = os.stat(path)
            if not stat.S_ISREG(info.st_mode):
                return None
            with open(path, "rb") as executable:
                magic = executable.read(4)
        except OSError:
            # An unidentifiable command may still be probed, but never cached.
            return None
        if not magic:
            return None
        # Wrapper verification is bound to one initialized process, never a launch.
        native_binary = magic in (
                b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
                b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
                b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")
        return (path, info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns, native_binary)

    def _check_version_operation(self, cancel_event):
        code = self.operation.code()
        if code is None and cancel_event is not None and cancel_event.is_set():
            code = "cancelled"
        if code is not None:
            raise ProcessError(code)

    def _version_supported(self, cancel_event=None, *, allow_resident=True):
        started_at = time.perf_counter()
        self.version_cache_hit = 0
        try:
            self._check_version_operation(cancel_event)
            identity = self._executable_identity()
            self._check_version_operation(cancel_event)
            if identity is not None and identity == self._supported_version_identity:
                self._operation_version_identity = identity
                self.version_cache_hit = 1
                return True
            with self._state_lock:
                verified = self._verified_process
                resident_hit = (
                    allow_resident and identity is not None and not identity[-1]
                    and verified is not None and verified[0] is self._proc
                    and verified[1] == identity
                    and self._profile == self._requested_profile and not self._closed
                    and self._process_running(self._proc))
            self._check_version_operation(cancel_event)
            if resident_hit:
                self._operation_version_identity = identity
                self.version_cache_hit = 1
                return True
            previous_identity = self._supported_version_identity or (
                verified[1] if verified is not None else None)
            self._supported_version_identity = None
            if previous_identity is not None:
                # Do not validate a replacement but keep serving its old process.
                self.stop_current()
                self._check_version_operation(cancel_event)
            output = capture_output(
                [self.command, "--version"], self.env, self.work_dir,
                cancel_event=self.operation, timeout=min(
                    5, self.operation.deadline - time.monotonic()), max_bytes=8192)
            self._check_version_operation(cancel_event)
            version = parse_codex_version(output)
            if version is None:
                raise ProcessError("appserver_version_unreadable")
            if version.prerelease:
                raise ProcessError("appserver_version_prerelease")
            if not version.supported:
                return False
            current_identity = self._executable_identity()
            self._check_version_operation(cancel_event)
            if current_identity != identity:
                raise ProcessError("appserver_executable_changed")
            self._operation_version_identity = identity
            if identity is not None and identity[-1]:
                self._supported_version_identity = identity
            return True
        finally:
            elapsed = max(0, int((time.perf_counter() - started_at) * 1000))
            self.version_check_ms = (self.version_check_ms or 0) + elapsed

    def _remember_verified_process(self):
        with self._state_lock:
            identity = self._process_version_identity
            if self._proc is not None and identity is not None and not identity[-1]:
                self._verified_process = (self._proc, identity)

    def shutdown(self):
        try:
            super().shutdown()
        finally:
            self._supported_version_identity = None
            self._verified_process = None

    def _start_process(self, request, *, cancel_event=None):
        identity = self._operation_version_identity
        if self.version_cache_hit and identity is not None and not identity[-1]:
            # The verified resident can exit between the reuse check and spawn.
            if not self._version_supported(cancel_event, allow_resident=False):
                raise ProcessError("appserver_version_unsupported")
        with self._state_lock:
            if self.cleanup_failed.is_set():
                raise ProcessError("probe_cleanup_failed")
            if self._closed or self.operation.is_set():
                raise RpcError("rpc_cancelled")
            command = self.build_command(request, cancel_event=self.operation)
            identity = self._operation_version_identity
            if identity is not None and self._executable_identity() != identity:
                raise ProcessError("appserver_executable_changed")
            proc = RpcProcess(command, self.env, self.work_dir)
            self._proc = proc
            self._process_version_identity = identity
            self._profile = request.model
            self._pending_rpc.clear()
            self._bound_operation = None
            return proc

    def _stop_process(self, proc):
        with self._state_lock:
            if self._proc is proc:
                self._proc = None
                self._profile = None
                self._bound_operation = None
                self._process_version_identity = None
                self._verified_process = None
        try:
            proc.close()
        except ProcessError:
            self.cleanup_failed.set()
            raise

    def _expire_idle_process(self, generation):
        if not self._owner_operation_lock.acquire(blocking=False):
            # Retain cleanup responsibility until the facade operation finishes.
            self._schedule_idle_shutdown(max_seconds=0.05, expected_generation=generation)
            return
        try:
            try:
                super()._expire_idle_process(generation)
            except ProcessError:
                self.cleanup_failed.set()
                if self._log_error is not None:
                    self._log_error("codex_provider", ProcessError("provider_cleanup_failed"))
        finally:
            self._owner_operation_lock.release()

    def stop_current(self):
        with self._state_lock:
            proc = self._proc
        if proc is not None:
            self._stop_process(proc)

    @staticmethod
    def _process_running(proc):
        return proc.is_running()

    def _bind(self, proc):
        if self._bound_operation is not self.operation:
            self.warm_process_hit = int(self._bound_operation is not None)
            proc.reset_budget()
            self._bound_operation = self.operation
            self._thread_id = None
            self._turn_id = None
            self._completed_items.clear()

    def _send(self, proc, method, params=None, request_id=None):
        if self.cleanup_failed.is_set():
            raise ProcessError("probe_cleanup_failed")
        self._bind(proc)
        envelope = {"method": method}
        if request_id is not None:
            envelope["id"] = request_id
            self._pending_rpc[request_id] = method
        if params is not None:
            envelope["params"] = params
        deadline, cancel = self.operation.deadline, self.operation
        if method == "turn/interrupt":
            self.operation.interrupt_deadline = min(deadline, time.monotonic() + 0.5)
            deadline, cancel = self.operation.interrupt_deadline, self.operation.closing
        proc.send(
            (json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"),
            deadline=deadline, cancel_event=cancel,
            on_write=self.operation.wrote_turn if method == "turn/start" else None)

    def _next_message(self, proc, output_queue, deadline, cancel_event):
        self._bind(proc)
        if self.operation.interrupt_deadline is not None:
            deadline = min(deadline, self.operation.interrupt_deadline)
            cancel = self.operation.closing
        else:
            deadline = min(deadline, self.operation.deadline)
            cancel = self.operation
        try:
            while True:
                line = proc.receive(deadline=deadline, cancel_event=cancel)
                if line is None or line.strip():
                    break
        except RpcError as error:
            if str(error) == "rpc_cancelled":
                return "cancelled", None
            if str(error) == "rpc_timeout":
                return ("cancelled" if self.operation.interrupt_deadline is not None
                        else "timeout"), None
            raise
        if line is None:
            return "eof", None
        message = _native_message(line)
        if "method" in message and "id" in message:
            raise CodexAppServerProtocolError("unsafe_tool_event")
        if "id" in message:
            if not set(message) <= {"id", "result", "error", "jsonrpc"}:
                raise CodexAppServerProtocolError("invalid_appserver_message")
            identifier = message["id"]
            if type(identifier) is not int or identifier not in self._pending_rpc:
                raise CodexAppServerProtocolError("invalid_appserver_message")
            method = self._pending_rpc.pop(identifier)
            if ("result" in message) == ("error" in message):
                raise CodexAppServerProtocolError("invalid_appserver_message")
            if "result" in message:
                result = message["result"]
                if method in ("initialize", "hooks/list", "thread/start", "turn/start"):
                    if not isinstance(result, dict):
                        raise CodexAppServerProtocolError("invalid_appserver_message")
                if method == "hooks/list":
                    data = result.get("data")
                    if not isinstance(data, list):
                        raise CodexAppServerProtocolError("invalid_appserver_message")
                    for entry in data:
                        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                            raise CodexAppServerProtocolError("invalid_appserver_message")
                        for hook in entry["hooks"]:
                            if not isinstance(hook, dict) or type(hook.get("enabled")) is not bool:
                                raise CodexAppServerProtocolError("invalid_appserver_message")
                            if hook["enabled"]:
                                raise CodexAppServerProtocolError("unsafe_tool_event")
                if method in ("thread/start", "turn/start"):
                    value = result.get("thread" if method == "thread/start" else "turn")
                    if not isinstance(value, dict) or type(value.get("id")) is not str or not value["id"]:
                        raise CodexAppServerProtocolError("invalid_appserver_message")
                    if method == "thread/start":
                        self._thread_id = value["id"]
                    else:
                        self._turn_id = value["id"]
        else:
            if not set(message) <= {"method", "params", "jsonrpc", "emittedAtMs"}:
                raise CodexAppServerProtocolError("invalid_appserver_message")
            if type(message.get("method")) is not str:
                raise CodexAppServerProtocolError("invalid_appserver_message")
            if message["method"] in ("hook/started", "hook/completed"):
                raise CodexAppServerProtocolError("unsafe_tool_event")
            params = message.get("params")
            if params is not None and not isinstance(params, dict):
                raise CodexAppServerProtocolError("invalid_appserver_message")
            for key in ("threadId", "turnId"):
                if params and key in params and (type(params[key]) is not str or not params[key]):
                    raise CodexAppServerProtocolError("invalid_appserver_message")
            if params:
                for key in ("turn", "run"):
                    if key in params and not isinstance(params[key], dict):
                        raise CodexAppServerProtocolError("invalid_appserver_message")
            method = message["method"]
            if method in ("item/agentMessage/delta", "item/started", "item/completed",
                          "turn/completed"):
                if (not params or self._thread_id is None or self._turn_id is None
                        or params.get("threadId") != self._thread_id):
                    raise CodexAppServerProtocolError("invalid_appserver_message")
                turn_id = (params.get("turn", {}).get("id") if method == "turn/completed"
                           else params.get("turnId"))
                if turn_id != self._turn_id:
                    raise CodexAppServerProtocolError("invalid_appserver_message")
                if method == "item/agentMessage/delta":
                    item_id = params.get("itemId")
                elif method in ("item/started", "item/completed"):
                    item = params.get("item")
                    item_id = item.get("id") if isinstance(item, dict) else None
                    if not isinstance(item, dict) or type(item.get("type")) is not str:
                        raise CodexAppServerProtocolError("invalid_appserver_message")
                    if item["type"] == "agentMessage":
                        if ("text" in item and type(item["text"]) is not str
                                or item.get("phase") not in (None, "commentary", "final_answer")):
                            raise CodexAppServerProtocolError("invalid_appserver_message")
                else:
                    item_id = None
                if method.startswith("item/") and (type(item_id) is not str or not item_id):
                    raise CodexAppServerProtocolError("invalid_appserver_message")
                if method.startswith("item/"):
                    if item_id in self._completed_items:
                        raise CodexAppServerProtocolError("invalid_appserver_message")
                    if method == "item/completed":
                        self._completed_items.add(item_id)
        return "line", line


class DarwinCodexProvider:
    """Caller-bound native provider; complete also uses app-server, never exec."""

    provider_id = CODEX_PROVIDER
    capabilities = ProviderCapabilities(text=True, images=True, streaming=True, warm_sessions=True)

    def __init__(self, command, work_dir, *, environment, catalog_cache_dir, log_error):
        if sys.platform != "darwin":
            raise ValueError("darwin_required")
        if not callable(log_error):
            raise TypeError("log_error must be callable")
        self.command, self.work_dir = _absolute(command), _absolute(work_dir)
        cache_dir = _absolute(catalog_cache_dir)
        if not isinstance(environment, dict) or any(
                type(key) is not str or type(value) is not str or not key
                or "=" in key or "\0" in key or "\0" in value
                for key, value in environment.items()):
            raise TypeError("explicit_provider_environment_required")
        home = _absolute(environment.get("HOME", ""))
        bound_environment = child_environment(environment=environment)
        if "CODEX_HOME" not in bound_environment:
            bound_environment["CODEX_HOME"] = os.path.join(home, ".codex")
        self.env = MappingProxyType(bound_environment)
        self._catalog = CodexModelCatalog(
            self.command, self.env, cache_dir=cache_dir, work_dir=self.work_dir,
            log_error=log_error, user_home=home)
        self._closing = threading.Event()
        self._operation_lock = threading.RLock()
        self._transport = _NativeTransport(
            self.command, self.work_dir, self.env, self._catalog,
            operation_lock=self._operation_lock, log_error=log_error)
        self._priority_lock = threading.Lock()
        self._prewarm_preempt = threading.Event()
        self._foreground_waiters = 0
        self._active_thread = None
        self._fatal = None

    @staticmethod
    def _request(request):
        if not isinstance(request, ProviderRequest):
            raise TypeError("request must be ProviderRequest")
        if type(request.task) is not str or type(request.image_paths) not in (list, tuple):
            raise ValueError("invalid_provider_request")
        if request.task == "image":
            if len(request.image_paths) != 1:
                return None
            image_paths = tuple(_absolute(path) for path in request.image_paths)
            image_paths[0].encode("utf-8")
        elif request.task in ("text", "translation_summary") and not request.image_paths:
            image_paths = ()
        else:
            return None
        if (type(request.user_text) is not str or type(request.system_prompt) is not str
                or type(request.model) not in (str, type(None))
                or type(request.timeout_seconds) not in (int, float)
                or not 0 < request.timeout_seconds <= sys.float_info.max):
            raise ValueError("invalid_provider_request")
        request.user_text.encode("utf-8")
        request.system_prompt.encode("utf-8")
        return ProviderRequest(
            request.task, request.model, request.system_prompt, request.user_text,
            image_paths=image_paths, timeout_seconds=request.timeout_seconds)

    def complete(self, request, cancel_event=None):
        return self.stream(request, lambda _delta: None, cancel_event)

    def stream(self, request, on_delta, cancel_event=None):
        if not callable(on_delta):
            raise TypeError("on_delta must be callable")
        request = self._request(request)
        if request is None:
            return ProviderResult(False, error_code="unsupported_task", metrics=(("turn_submitted", False),))
        with self._priority_lock:
            self._foreground_waiters += 1
            self._prewarm_preempt.set()
        try:
            return self._execute(request, on_delta, cancel_event)
        finally:
            with self._priority_lock:
                self._foreground_waiters -= 1

    def warm_up(self, model, cancel_event=None):
        request = self._request(ProviderRequest("text", model, "", "", timeout_seconds=10))
        return self._execute(request, None, cancel_event)

    def model_catalog(self, cancel_event=None):
        operation = _Operation(8, self._closing, cancel_event)
        with self._priority_lock:
            self._foreground_waiters += 1
            self._prewarm_preempt.set()
        entered, acquired = False, False
        try:
            while True:
                code = self._failure_code(operation)
                if code is not None:
                    raise CatalogProbeError(code)
                if self._operation_lock.acquire(timeout=0.05):
                    acquired = True
                    break
            if self._active_thread is not None:
                raise RuntimeError("provider_reentrant_request")
            code = self._failure_code(operation)
            if code is not None:
                raise CatalogProbeError(code)
            self._active_thread = threading.get_ident()
            entered = True
            try:
                os.makedirs(self.work_dir, exist_ok=True)
                models = self._catalog.discover(cancel_event=operation)
            except (CatalogError, CatalogProbeError, ProcessError, OSError) as error:
                if "cleanup_failed" in str(error):
                    self._fatal = "provider_cleanup_failed"
                    try:
                        self._transport.stop_current()
                    except ProcessError:
                        self._fatal = "provider_cleanup_failed"
                code = self._failure_code(operation)
                if code is None:
                    code = ("model_catalog_too_large" if str(error) in (
                        "catalog_probe_output_limit", "catalog_output_too_large")
                            else "model_catalog_failed")
                raise CatalogProbeError(code) from None
            code = self._failure_code(operation)
            if code is not None:
                raise CatalogProbeError(code)
            return models
        finally:
            if entered:
                self._active_thread = None
            if acquired:
                self._operation_lock.release()
            with self._priority_lock:
                self._foreground_waiters -= 1

    def _failure_code(self, operation):
        if self._transport.cleanup_failed.is_set():
            return "provider_cleanup_failed"
        return self._fatal or operation.code()

    def _execute(self, request, on_delta, cancel_event):
        operation = _Operation(request.timeout_seconds, self._closing, cancel_event)
        entered = False
        while True:
            code = self._failure_code(operation)
            if code is not None:
                return ProviderResult(False, error_code=code, metrics=(("turn_submitted", False),))
            if self._operation_lock.acquire(timeout=0.05):
                break
        try:
            if self._active_thread is not None:
                raise RuntimeError("provider_reentrant_request")
            code = self._failure_code(operation)
            if code is not None:
                return ProviderResult(False, error_code=code, metrics=(("turn_submitted", False),))
            self._active_thread = threading.get_ident()
            entered = True
            self._transport.operation = operation
            self._transport._operation_version_identity = None
            self._transport._requested_profile = request.model
            self._transport.version_check_ms = None
            self._transport.version_cache_hit = 0
            self._transport.warm_process_hit = 0
            if on_delta is None:
                with self._priority_lock:
                    if self._foreground_waiters:
                        return ProviderResult(False, error_code="cancelled",
                                              metrics=(("turn_submitted", False),))
                    self._prewarm_preempt.clear()
                    self._transport._prewarm_cancel_event.clear()
                    operation.preempt = self._prewarm_preempt
            try:
                os.makedirs(self.work_dir, exist_ok=True)
                result = (self._transport.warm_up(request) if on_delta is None else
                          self._transport.stream(request, on_delta, operation))
            except (ProcessError, CatalogProbeError, CodexConfigError) as error:
                try:
                    self._transport.stop_current()
                except ProcessError:
                    self._fatal = "provider_cleanup_failed"
                if "cleanup_failed" in str(error):
                    self._fatal = "provider_cleanup_failed"
                result = ProviderResult(False, error_code=self._fatal or operation.code() or str(error))
            except OSError:
                result = ProviderResult(False, error_code="workdir_failed")
            if "cleanup_failed" in result.error_code:
                self._fatal = "provider_cleanup_failed"
            values = dict(result.metrics)
            if self._transport.version_check_ms is not None:
                values["version_check_ms"] = self._transport.version_check_ms
                values["version_cache_hit"] = self._transport.version_cache_hit
            values["warm_process_hit"] = self._transport.warm_process_hit
            values["turn_submitted"] = bool(values.get("turn_submitted") or operation.submitted)
            code = self._failure_code(operation) or result.error_code
            if result.ok and not code:
                self._transport._remember_verified_process()
            return ProviderResult(
                result.ok and not code,
                text=result.text if result.ok and not code else "",
                error_code=code or "", metrics=tuple(values.items()))
        finally:
            if entered:
                self._active_thread = None
                self._transport.operation = None
                self._transport._operation_version_identity = None
                self._transport._requested_profile = None
            self._operation_lock.release()

    def diagnose(self):
        result = self.warm_up(None)
        return ProviderStatus(
            installed=os.path.isfile(self.command), authenticated=None, command=self.command,
            error_code=result.error_code, backend="native_appserver")

    def shutdown(self, *, require_cleanup=False):
        self._closing.set()
        with self._operation_lock:
            self._transport.shutdown()
            if require_cleanup and (self._transport.cleanup_failed.is_set() or self._fatal == "provider_cleanup_failed"):
                raise ProcessError("provider_cleanup_failed")
