"""Experimental Codex app-server transport for streamed agent text."""

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time

from .base import ProviderResult
from .codex_cli import (
    _CODEX_CONFIG_OVERRIDES,
    _CREATE_NEW_PROCESS_GROUP,
    _CREATE_NO_WINDOW,
    _MODEL_CONFIG_OVERRIDES,
    _classify_error,
    _kill_process,
    _read_stream,
    _runtime_model,
    _sanitize_detail,
    build_codex_prompt,
)


_SAFE_ITEM_TYPES = {
    "userMessage",
    "agentMessage",
    "reasoning",
    "plan",
    "contextCompaction",
}
_SUPPORTED_CODEX_VERSIONS = {"0.146.0"}
_VERSION_CACHE_MAX_ENTRIES = 8
_version_cache = {}
_version_cache_lock = threading.Lock()
_DEFENDER_HOOK_COMMAND = (
    "trap { exit 0 } $l=(Get-ItemProperty -LiteralPath:"
    "'HKLM:\\SOFTWARE\\Microsoft\\Windows Defender' -Name:"
    "'InstallLocation' -ErrorAction:SilentlyContinue).InstallLocation; "
    "if($l){$p=Join-Path -Path:$l -ChildPath:'DefenderAgentScan.exe'; "
    "if(Test-Path -LiteralPath:$p){& $p}}; exit 0"
)
_APP_SERVER_CONFIG_OVERRIDES = _CODEX_CONFIG_OVERRIDES + (
    "hooks.PermissionRequest=[]",
    "hooks.PostCompact=[]",
    "hooks.PostToolUse=[]",
    "hooks.PreCompact=[]",
    "hooks.PreToolUse=[]",
    "hooks.SessionEnd=[]",
    "hooks.SessionStart=[]",
    "hooks.Stop=[]",
    "hooks.SubagentStart=[]",
    "hooks.SubagentStop=[]",
    "hooks.UserPromptSubmit=[]",
)
_BLOCKED_ITEM_TYPES = {
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "dynamicToolCall",
    "collabAgentToolCall",
    "subAgentActivity",
    "webSearch",
    "imageView",
    "sleep",
    "imageGeneration",
    "hookPrompt",
}
_IGNORED_NOTIFICATIONS = {
    "account/rateLimits/updated",
    "account/updated",
    "thread/started",
    "thread/status/changed",
    "thread/tokenUsage/updated",
    "turn/started",
    "item/reasoning/summaryPartAdded",
    "item/reasoning/summaryTextDelta",
    "item/reasoning/textDelta",
    "item/plan/delta",
    "model/verification",
    "model/safetyBuffering/updated",
    "model/rerouted",
    "mcpServer/startupStatus/updated",
    "remoteControl/status/changed",
    "warning",
    "configWarning",
    "deprecationNotice",
}
_BLOCKED_NOTIFICATIONS = {
    "command/exec/outputDelta",
    "process/outputDelta",
    "process/exited",
    "item/commandExecution/outputDelta",
    "item/commandExecution/terminalInteraction",
    "item/fileChange/outputDelta",
    "item/fileChange/patchUpdated",
    "item/mcpToolCall/progress",
}


class CodexAppServerProtocolError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class CodexAppServerParser:
    """Strictly parse the subset of app-server v2 used by translation."""

    def __init__(self, on_delta):
        self.on_delta = on_delta
        self.thread_id = None
        self.turn_id = None
        self.final_text = ""
        self.turn_status = None
        self.error_detail = ""
        self.responses = {}
        self.saw_delta = False
        self.last_was_notification = False

    def feed(self, raw_line):
        self.last_was_notification = False
        try:
            message = json.loads(raw_line)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CodexAppServerProtocolError(
                "invalid_appserver_json", str(exc)) from exc
        if not isinstance(message, dict):
            raise CodexAppServerProtocolError(
                "invalid_appserver_message", "message is not an object")

        request_id = message.get("id")
        method = message.get("method")
        if request_id is not None and method:
            raise CodexAppServerProtocolError(
                "unsafe_tool_event", f"server request: {method}")
        if request_id is not None:
            if "error" in message:
                detail = message.get("error")
                raise CodexAppServerProtocolError(
                    "appserver_request_failed", _sanitize_detail(str(detail)))
            self.responses[request_id] = message.get("result")
            return
        if not isinstance(method, str):
            raise CodexAppServerProtocolError(
                "invalid_appserver_message", "missing method")
        self.last_was_notification = True

        params = message.get("params") or {}
        if method in ("hook/started", "hook/completed"):
            self._handle_hook(method, params)
            return
        if method in _BLOCKED_NOTIFICATIONS:
            raise CodexAppServerProtocolError(
                "unsafe_tool_event", f"blocked notification: {method}")
        if method == "item/agentMessage/delta":
            self._check_identity(params)
            delta = params.get("delta")
            if not isinstance(delta, str):
                raise CodexAppServerProtocolError(
                    "invalid_appserver_message", "delta is not text")
            if delta:
                self.saw_delta = True
                self.on_delta(delta)
            return
        if method in ("item/started", "item/completed"):
            self._handle_item(params, completed=method == "item/completed")
            return
        if method == "turn/completed":
            self._check_identity(params)
            turn = params.get("turn") or {}
            self.turn_status = turn.get("status")
            error = turn.get("error")
            if error:
                self.error_detail = _sanitize_detail(str(error))
            return
        if method == "error":
            error = params.get("error") or params
            self.error_detail = _sanitize_detail(str(error))
            return
        if method in _IGNORED_NOTIFICATIONS:
            return
        raise CodexAppServerProtocolError(
            "unknown_appserver_event", method)

    def _check_identity(self, params):
        thread_id = params.get("threadId")
        turn_id = params.get("turnId")
        if thread_id:
            if not self.thread_id:
                raise CodexAppServerProtocolError(
                    "invalid_appserver_message",
                    "thread event arrived before thread/start")
            if thread_id != self.thread_id:
                raise CodexAppServerProtocolError(
                    "invalid_appserver_message", "thread id changed")
        if turn_id:
            if not self.turn_id:
                raise CodexAppServerProtocolError(
                    "invalid_appserver_message",
                    "turn event arrived before turn/start")
            if turn_id != self.turn_id:
                raise CodexAppServerProtocolError(
                    "invalid_appserver_message", "turn id changed")

    def _handle_item(self, params, completed):
        self._check_identity(params)
        item = params.get("item")
        if not isinstance(item, dict):
            raise CodexAppServerProtocolError(
                "invalid_appserver_message", "missing item")
        item_type = item.get("type")
        if item_type in _BLOCKED_ITEM_TYPES:
            raise CodexAppServerProtocolError(
                "unsafe_tool_event", f"blocked item: {item_type}")
        if item_type not in _SAFE_ITEM_TYPES:
            raise CodexAppServerProtocolError(
                "unknown_appserver_item", str(item_type))
        if completed and item_type == "agentMessage":
            text = item.get("text")
            if isinstance(text, str) and item.get("phase") in (None, "final_answer"):
                self.final_text = text

    @staticmethod
    def _handle_hook(method, params):
        run = params.get("run") or {}
        event = run.get("eventName")
        handler = run.get("handlerType")
        source = run.get("source")
        source_path = run.get("sourcePath")
        # App-server 0.146.0 always runs first-party lifecycle hooks even when
        # features.hooks=false and all configurable hook lists are empty.
        # Permit only those non-model-triggered system lifecycle hooks.
        if (source != "system"
                or handler != "command"
                or event not in {
                    "sessionStart", "userPromptSubmit", "stop"
                }
                or not _is_trusted_defender_path(source_path)):
            raise CodexAppServerProtocolError(
                "unsafe_tool_event",
                f"{method}: event={event}, handler={handler}, source={source}")


class CodexAppServerTransport:
    """Reuse one app-server process while isolating every request by thread."""

    def __init__(self, command, work_dir, idle_timeout_seconds=300):
        self.command = command
        self.work_dir = work_dir
        self.idle_timeout_seconds = idle_timeout_seconds
        self._stream_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._proc = None
        self._output_queue = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._stderr_chunks = None
        self._profile = None
        self._next_request_id = 1
        self._idle_timer = None
        self._idle_generation = 0
        self._closed = False

    def build_command(self, request):
        command = [
            self.command,
            "app-server",
            "--listen",
            "stdio://",
            "--strict-config",
        ]
        for override in _APP_SERVER_CONFIG_OVERRIDES:
            command.extend(("-c", override))
        for override in _MODEL_CONFIG_OVERRIDES.get(request.model, ()):
            command.extend(("-c", override))
        return command

    def stream(self, request, on_delta, cancel_event=None):
        started_at = time.perf_counter()
        if not self.command:
            return ProviderResult(False, error_code="cli_not_installed")
        if not _supported_appserver_version(self.command):
            return ProviderResult(
                False, error_code="appserver_version_unsupported")
        try:
            os.makedirs(self.work_dir, exist_ok=True)
        except OSError as exc:
            return ProviderResult(
                False, error_code="workdir_failed",
                error_detail=_sanitize_detail(str(exc)))

        deadline = time.monotonic() + max(1.0, request.timeout_seconds)
        if not self._acquire_stream_lock(deadline, cancel_event):
            code = (
                "cancelled"
                if cancel_event is not None and cancel_event.is_set()
                else "timeout"
            )
            return ProviderResult(
                False, error_code=code,
                metrics=(("total_ms", int(
                    (time.perf_counter() - started_at) * 1000)),))

        proc = None
        reusable = False
        spawn_ms = 0
        parser = CodexAppServerParser(on_delta)
        first_event_ms = None
        first_result_ms = None
        initialize_ms = 0
        hook_preflight_ms = None
        thread_start_ms = None
        turn_start_ms = None
        turn_started_at = None
        turn_first_event_ms = None
        turn_first_result_ms = None
        interrupt_sent = False

        def metrics():
            values = [
                ("spawn_ms", spawn_ms),
                ("total_ms", int(
                    (time.perf_counter() - started_at) * 1000)),
            ]
            if first_event_ms is not None:
                values.append(("first_event_ms", first_event_ms))
            if first_result_ms is not None:
                values.append(("first_result_ms", first_result_ms))
            if initialize_ms is not None:
                values.append(("initialize_ms", initialize_ms))
            if hook_preflight_ms is not None:
                values.append(("hook_preflight_ms", hook_preflight_ms))
            if thread_start_ms is not None:
                values.append(("thread_start_ms", thread_start_ms))
            if turn_start_ms is not None:
                values.append(("turn_start_ms", turn_start_ms))
            if turn_first_event_ms is not None:
                values.append(("turn_first_event_ms", turn_first_event_ms))
            if turn_first_result_ms is not None:
                values.append(("turn_first_result_ms", turn_first_result_ms))
            if turn_started_at is not None:
                values.append(("turn_total_ms", int(
                    (time.perf_counter() - turn_started_at) * 1000)))
            return tuple(values)

        def send(method, params=None, request_id=None):
            envelope = {"method": method}
            if request_id is not None:
                envelope["id"] = request_id
            if params is not None:
                envelope["params"] = params
            proc.stdin.write(json.dumps(
                envelope, ensure_ascii=False, separators=(",", ":")) + "\n")
            proc.stdin.flush()

        try:
            self._cancel_idle_timer()
            with self._state_lock:
                if self._closed:
                    return ProviderResult(
                        False, error_code="appserver_shutdown",
                        metrics=metrics())
                proc = self._proc
                output_queue = self._output_queue
                same_profile = self._profile == request.model
            if (proc is None or not same_profile
                    or not self._process_running(proc)):
                if proc is not None:
                    self._stop_process(proc)
                spawn_started_at = time.perf_counter()
                try:
                    proc = self._start_process(request)
                except OSError as exc:
                    return ProviderResult(
                        False, error_code="cli_unavailable",
                        error_detail=_sanitize_detail(str(exc)),
                        metrics=metrics())
                spawn_ms = int(
                    (time.perf_counter() - spawn_started_at) * 1000)
                output_queue = self._output_queue

                initialize_id = self._take_request_id()
                send("initialize", {
                    "clientInfo": {
                        "name": "cc-translate",
                        "version": "experimental-appserver-1",
                    },
                    "capabilities": {"experimentalApi": False},
                }, initialize_id)
                while initialize_id not in parser.responses:
                    kind, payload = self._next_message(
                        proc, output_queue, deadline, cancel_event)
                    if kind != "line":
                        return self._early_result(
                            kind, payload, parser, metrics())
                    parser.feed(payload)
                initialize_ms = int(
                    (time.perf_counter() - started_at) * 1000)
                send("initialized")

            hook_started_at = time.perf_counter()
            hooks_list_id = self._take_request_id()
            send("hooks/list", {
                "cwds": [os.path.abspath(self.work_dir)],
            }, hooks_list_id)
            while hooks_list_id not in parser.responses:
                kind, payload = self._next_message(
                    proc, output_queue, deadline, cancel_event)
                if kind != "line":
                    return self._early_result(
                        kind, payload, parser, metrics())
                parser.feed(payload)
            _validate_hook_preflight(
                parser.responses[hooks_list_id], self.work_dir)
            hook_preflight_ms = int(
                (time.perf_counter() - hook_started_at) * 1000)

            thread_params = {
                "cwd": os.path.abspath(self.work_dir),
                "ephemeral": True,
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "baseInstructions": (
                    "Act only as a translation engine. Never use tools, inspect "
                    "files, execute commands, search, or modify anything."),
                "config": {
                    "mcp_servers": {},
                    "web_search": "disabled",
                },
            }
            runtime_model = _runtime_model(request.model)
            if runtime_model and runtime_model != "auto":
                thread_params["model"] = runtime_model
            thread_started_at = time.perf_counter()
            thread_start_id = self._take_request_id()
            send("thread/start", thread_params, thread_start_id)

            while thread_start_id not in parser.responses:
                kind, payload = self._next_message(
                    proc, output_queue, deadline, cancel_event)
                if kind != "line":
                    return self._early_result(kind, payload, parser, metrics())
                parser.feed(payload)
            thread_result = parser.responses[thread_start_id] or {}
            thread = thread_result.get("thread") or {}
            parser.thread_id = thread.get("id")
            if not parser.thread_id:
                raise CodexAppServerProtocolError(
                    "invalid_appserver_message", "thread/start returned no id")
            thread_start_ms = int(
                (time.perf_counter() - thread_started_at) * 1000)

            turn_params = {
                "threadId": parser.thread_id,
                "input": [{
                    "type": "text",
                    "text": build_codex_prompt(request),
                }],
                "approvalPolicy": "never",
                "sandboxPolicy": {
                    "type": "readOnly",
                    "networkAccess": False,
                },
            }
            if runtime_model and runtime_model != "auto":
                turn_params["model"] = runtime_model
            if request.model == "gpt-5.4-mini":
                turn_params["effort"] = "low"
            turn_started_at = time.perf_counter()
            turn_start_id = self._take_request_id()
            send("turn/start", turn_params, turn_start_id)

            while parser.turn_status is None:
                if (cancel_event is not None and cancel_event.is_set()
                        and parser.turn_id and not interrupt_sent):
                    interrupt_id = self._take_request_id()
                    send("turn/interrupt", {
                        "threadId": parser.thread_id,
                        "turnId": parser.turn_id,
                    }, interrupt_id)
                    interrupt_sent = True
                kind, payload = self._next_message(
                    proc, output_queue, deadline,
                    None if interrupt_sent else cancel_event)
                if kind != "line":
                    return self._early_result(kind, payload, parser, metrics())
                parser.feed(payload)
                elapsed = int((time.perf_counter() - started_at) * 1000)
                turn_elapsed = int(
                    (time.perf_counter() - turn_started_at) * 1000)
                if parser.last_was_notification and first_event_ms is None:
                    first_event_ms = elapsed
                    turn_first_event_ms = turn_elapsed
                if parser.saw_delta and first_result_ms is None:
                    first_result_ms = elapsed
                    turn_first_result_ms = turn_elapsed
                if turn_start_id in parser.responses and not parser.turn_id:
                    turn_result = parser.responses[turn_start_id] or {}
                    turn = turn_result.get("turn") or {}
                    parser.turn_id = turn.get("id")
                    if not parser.turn_id:
                        raise CodexAppServerProtocolError(
                            "invalid_appserver_message",
                            "turn/start returned no id")
                    turn_start_ms = turn_elapsed

            if interrupt_sent or parser.turn_status == "interrupted":
                return ProviderResult(
                    False, error_code="cancelled", metrics=metrics())
            if parser.turn_status != "completed":
                return ProviderResult(
                    False,
                    error_code=_classify_error(parser.error_detail),
                    error_detail=parser.error_detail,
                    metrics=metrics(),
                )
            if not parser.final_text.strip():
                return ProviderResult(
                    False, error_code="no_result", metrics=metrics())
            reusable = True
            return ProviderResult(
                True, text=parser.final_text.strip(), metrics=metrics())
        except CodexAppServerProtocolError as exc:
            return ProviderResult(
                False, error_code=exc.code,
                error_detail=_sanitize_detail(exc.detail),
                metrics=metrics())
        except (OSError, ValueError) as exc:
            return ProviderResult(
                False, error_code="appserver_io_failed",
                error_detail=_sanitize_detail(str(exc)),
                metrics=metrics())
        finally:
            if reusable:
                self._schedule_idle_shutdown()
            elif proc is not None:
                self._stop_process(proc)
            self._stream_lock.release()

    def shutdown(self):
        """Terminate the persistent process and reject future requests."""
        with self._state_lock:
            self._closed = True
            proc = self._proc
        self._cancel_idle_timer()
        if proc is not None:
            self._stop_process(proc)

    def _start_process(self, request):
        with self._state_lock:
            if self._closed:
                raise OSError("app-server transport is shut down")
            proc = subprocess.Popen(
                self.build_command(request),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                creationflags=_CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP,
            )
            output_queue = queue.Queue()
            stderr_chunks = []
            stdout_thread = threading.Thread(
                target=_read_stdout,
                args=(proc.stdout, output_queue),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=_read_stream,
                args=(proc.stderr, stderr_chunks),
                daemon=True,
            )
            self._proc = proc
            self._output_queue = output_queue
            self._stdout_thread = stdout_thread
            self._stderr_thread = stderr_thread
            self._stderr_chunks = stderr_chunks
            self._profile = request.model
            stdout_thread.start()
            stderr_thread.start()
            return proc

    def _stop_process(self, proc):
        with self._state_lock:
            if self._proc is proc:
                stdout_thread = self._stdout_thread
                stderr_thread = self._stderr_thread
                self._proc = None
                self._output_queue = None
                self._stdout_thread = None
                self._stderr_thread = None
                self._stderr_chunks = None
                self._profile = None
            else:
                stdout_thread = None
                stderr_thread = None
        _kill_process(proc)
        if isinstance(getattr(proc, "pid", None), int):
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    stream.close()
                except OSError:
                    pass
        for reader in (stdout_thread, stderr_thread):
            if reader is not None and reader is not threading.current_thread():
                reader.join(timeout=1)

    def _take_request_id(self):
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    def _cancel_idle_timer(self):
        with self._state_lock:
            self._idle_generation += 1
            timer = self._idle_timer
            self._idle_timer = None
        if timer is not None:
            timer.cancel()

    def _schedule_idle_shutdown(self):
        if self.idle_timeout_seconds <= 0:
            return
        with self._state_lock:
            if self._closed or self._proc is None:
                return
            self._idle_generation += 1
            generation = self._idle_generation
            old_timer = self._idle_timer
            timer = threading.Timer(
                self.idle_timeout_seconds,
                self._expire_idle_process,
                args=(generation,),
            )
            timer.daemon = True
            self._idle_timer = timer
        if old_timer is not None:
            old_timer.cancel()
        timer.start()

    def _expire_idle_process(self, generation):
        if not self._stream_lock.acquire(blocking=False):
            return
        try:
            with self._state_lock:
                if generation != self._idle_generation:
                    return
                self._idle_timer = None
                proc = self._proc
            if proc is not None:
                self._stop_process(proc)
        finally:
            self._stream_lock.release()

    def _acquire_stream_lock(self, deadline, cancel_event):
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self._stream_lock.acquire(timeout=min(0.05, remaining)):
                return True

    @staticmethod
    def _process_running(proc):
        poll = getattr(proc, "poll", None)
        return poll() is None if callable(poll) else True

    @staticmethod
    def _next_message(proc, output_queue, deadline, cancel_event):
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return "cancelled", None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout", None
            try:
                kind, payload = output_queue.get(
                    timeout=min(0.1, remaining))
            except queue.Empty:
                if proc.poll() is not None:
                    return "eof", None
                continue
            return kind, payload

    @staticmethod
    def _early_result(kind, payload, parser, metrics):
        if kind == "cancelled":
            code = "cancelled"
        elif kind == "timeout":
            code = "timeout"
        elif kind == "error":
            code = "stdout_failed"
        else:
            code = "appserver_exited"
        return ProviderResult(
            False,
            error_code=code,
            error_detail=_sanitize_detail(str(payload or "")),
            metrics=metrics,
        )


def _read_stdout(stream, output_queue):
    try:
        for line in iter(stream.readline, ""):
            if line.strip():
                output_queue.put(("line", line))
    except OSError as exc:
        output_queue.put(("error", exc))
    finally:
        output_queue.put(("eof", None))


def _supported_appserver_version(command):
    cache_key = _command_fingerprint(command)
    with _version_cache_lock:
        cached = _version_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        completed = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    supported = (
        completed.returncode == 0
        and appserver_version_supported(completed.stdout)
    )
    with _version_cache_lock:
        if len(_version_cache) >= _VERSION_CACHE_MAX_ENTRIES:
            _version_cache.clear()
        _version_cache[cache_key] = supported
    return supported


def _command_fingerprint(command):
    resolved = shutil.which(command) or command
    path = os.path.normcase(os.path.abspath(resolved))
    try:
        stat = os.stat(path)
    except OSError:
        return path, None, None
    return path, stat.st_mtime_ns, stat.st_size


def _clear_appserver_version_cache():
    with _version_cache_lock:
        _version_cache.clear()


def appserver_version_supported(version_text):
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", version_text or "")
    return bool(match and match.group(1) in _SUPPORTED_CODEX_VERSIONS)


def _validate_hook_preflight(result, work_dir):
    """Reject executable hooks before a model turn unless Defender-managed."""
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise CodexAppServerProtocolError(
            "invalid_appserver_message", "hooks/list returned invalid data")
    if len(result["data"]) != 1:
        raise CodexAppServerProtocolError(
            "invalid_appserver_message", "hooks/list cwd count changed")
    expected_cwd = os.path.normcase(os.path.abspath(work_dir))
    for entry in result["data"]:
        if not isinstance(entry, dict):
            raise CodexAppServerProtocolError(
                "invalid_appserver_message", "invalid hooks/list entry")
        if os.path.normcase(os.path.abspath(
                entry.get("cwd") or "")) != expected_cwd:
            raise CodexAppServerProtocolError(
                "invalid_appserver_message", "hooks/list cwd changed")
        if entry.get("errors") or entry.get("warnings"):
            raise CodexAppServerProtocolError(
                "unsafe_tool_event", "hook discovery reported diagnostics")
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            raise CodexAppServerProtocolError(
                "invalid_appserver_message", "hooks/list omitted hooks")
        for hook in hooks:
            if not isinstance(hook, dict):
                raise CodexAppServerProtocolError(
                    "invalid_appserver_message", "invalid hook metadata")
            if not isinstance(hook.get("enabled"), bool):
                raise CodexAppServerProtocolError(
                    "invalid_appserver_message", "hook state is invalid")
            if not hook.get("enabled"):
                continue
            if (hook.get("source") != "system"
                    or hook.get("handlerType") != "command"
                    or hook.get("isManaged") is not True
                    or hook.get("trustStatus") != "managed"
                    or not _is_trusted_defender_path(
                        hook.get("sourcePath"))
                    or hook.get("command") != _DEFENDER_HOOK_COMMAND):
                raise CodexAppServerProtocolError(
                    "unsafe_tool_event", "untrusted configured hook")


def _is_trusted_defender_path(path):
    if not isinstance(path, str) or not path:
        return False
    program_data = os.environ.get("ProgramData", r"C:\ProgramData")
    trusted_root = os.path.normcase(os.path.abspath(os.path.join(
        program_data, "Microsoft", "Windows Defender", "Platform")))
    candidate = os.path.normcase(os.path.abspath(path))
    try:
        return os.path.commonpath((trusted_root, candidate)) == trusted_root
    except ValueError:
        return False
