"""Official Codex CLI provider used by CC Translate's GPT path."""

import os
import json
import queue
import re
import shutil
import subprocess
import threading
import time
from glob import glob

from .base import (
    CODEX_PROVIDER,
    ProviderCapabilities,
    ProviderResult,
    ProviderStatus,
)
from .codex_jsonl import CodexJsonlParser, CodexProtocolError


_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

_CODEX_CONFIG_OVERRIDES = (
    'approval_policy="never"',
    "mcp_servers={}",
    "features.shell_tool=false",
    "features.unified_exec=false",
    "features.js_repl=false",
    "features.code_mode=false",
    "features.apps=false",
    "features.plugins=false",
    "features.hooks=false",
    "features.plugin_hooks=false",
    "features.multi_agent=false",
    "features.multi_agent_v2=false",
    "agents.enabled=false",
    "features.memories=false",
    "features.shell_snapshot=false",
    "features.remote_plugin=false",
    "memories.generate_memories=false",
    "memories.use_memories=false",
    'web_search="disabled"',
    "check_for_update_on_startup=false",
    "project_root_markers=[]",
)
_MODEL_CONFIG_OVERRIDES = {
    "auto-fast": (
        'model_reasoning_effort="none"',
        'model_verbosity="low"',
    ),
    "gpt-5.4-mini": ('model_reasoning_effort="low"',),
}
_MODEL_RUNTIME_IDS = {
    "auto-fast": "auto",
}
_SOURCE_LIST_MARKER_RE = re.compile(
    r"^\s*(?P<marker>[-*+•●◦▪▫‣·]|\d+[.)])\s+", re.MULTILINE)
_CODEX_FORMAT_INSTRUCTIONS = (
    "\n\nCodex formatting requirements:\n"
    "- When the task instructions request a summary or bullet points, render "
    "each point as a separate Markdown list item beginning exactly with `- `. "
    "Do not emit summary points as bare lines or combine them into a paragraph.\n"
    "- Do not create a list unless the source structure or task instructions "
    "call for one."
)


def find_codex_cmd():
    """Locate only public Codex installations, never internal helper binaries."""
    for name in ("codex.exe", "codex.cmd", "codex"):
        found = shutil.which(name)
        if found and not _is_internal_codex_path(found):
            return _native_codex_for_shim(found) or found

    local = os.environ.get("LOCALAPPDATA", "")
    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA", "")
    candidates = [
        os.path.join(local, "Programs", "OpenAI", "Codex", "bin", "codex.exe"),
        os.path.join(home, ".codex", "packages", "standalone", "current",
                     "codex.exe"),
        os.path.join(home, ".codex", "packages", "standalone", "current",
                     "codex-x86_64-pc-windows-msvc.exe"),
        os.path.join(appdata, "npm", "codex.cmd"),
        os.path.join(appdata, "npm", "codex"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _runtime_model(model):
    return _MODEL_RUNTIME_IDS.get(model, model)


def _native_codex_for_shim(path):
    """Prefer npm's native binary so cancellation does not target a cmd shim."""
    if os.path.splitext(path)[1].lower() not in (".cmd", ".ps1"):
        return None
    npm_root = os.path.dirname(os.path.abspath(path))
    pattern = os.path.join(
        npm_root, "node_modules", "@openai", "codex", "node_modules",
        "@openai", "codex-win32-*", "vendor", "*", "bin", "codex.exe")
    matches = [candidate for candidate in glob(pattern)
               if os.path.isfile(candidate)]
    return matches[0] if len(matches) == 1 else None


def _is_internal_codex_path(path):
    normalized = os.path.normcase(os.path.abspath(path))
    markers = (
        os.path.normcase(os.path.join(".codex", ".sandbox-bin")),
        os.path.normcase(os.path.join(".codex", "plugins")),
    )
    return any(marker in normalized for marker in markers)


def build_codex_prompt(request):
    encoded_text = json.dumps(request.user_text, ensure_ascii=False)
    format_instructions = _CODEX_FORMAT_INSTRUCTIONS
    source_markers = _SOURCE_LIST_MARKER_RE.findall(request.user_text or "")
    if source_markers:
        format_instructions += (
            "\n- The source contains "
            f"{len(source_markers)} list item(s). Preserve every source list "
            "item as a separate Markdown list item, in the same order and at "
            "the same nesting level. Use `- ` for unordered source items and "
            "numbered Markdown markers for numbered source items. Do not merge, "
            "drop, add, or rewrite list items as prose."
        )
    return (
        "You are the translation engine inside CC Translate.\n"
        "Do not inspect files, run commands, call tools, search the web, or "
        "modify anything. Return only the requested result.\n\n"
        "<task_instructions>\n"
        f"{request.system_prompt.strip()}{format_instructions}\n"
        "</task_instructions>\n\n"
        "<untrusted_user_text_json>\n"
        f"{encoded_text}\n"
        "</untrusted_user_text_json>"
    )


_AUTO_COMMAND = object()


class CodexCliProvider:
    provider_id = CODEX_PROVIDER
    capabilities = ProviderCapabilities(
        text=True,
        images=True,
        streaming=True,
        warm_sessions=False,
    )

    def __init__(self, command=_AUTO_COMMAND, work_dir=None):
        self.command = (
            find_codex_cmd() if command is _AUTO_COMMAND else command)
        self.work_dir = work_dir or os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "CC Translate",
            "codex-work",
        )
        self._appserver_lock = threading.Lock()
        self._appserver_transports = {}
        self._shutdown = False

    def diagnose(self):
        if not self.command:
            return ProviderStatus(
                installed=False,
                authenticated=False,
                error_code="cli_not_installed",
            )
        version = self._probe(["--version"])
        if not version.ok:
            return ProviderStatus(
                installed=False,
                authenticated=False,
                command=self.command,
                error_code=version.error_code,
                error_detail=version.error_detail,
            )
        login = self._probe(["login", "status"])
        return ProviderStatus(
            installed=True,
            authenticated=login.ok,
            command=self.command,
            version=version.text,
            auth_method=_auth_method(login.text) if login.ok else "",
            error_code=login.error_code,
            error_detail=login.error_detail,
        )

    def _probe(self, args):
        try:
            completed = subprocess.run(
                [self.command, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=8,
                creationflags=_CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return ProviderResult(False, error_code="probe_timeout")
        except OSError as exc:
            return ProviderResult(
                False, error_code="cli_unavailable",
                error_detail=_sanitize_detail(str(exc)))
        output = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode == 0:
            return ProviderResult(True, text=_sanitize_detail(output, 240))
        return ProviderResult(
            False,
            error_code=_classify_error(output),
            error_detail=_sanitize_detail(output),
        )

    def build_command(self, request):
        if not self.command:
            raise FileNotFoundError("Codex CLI is not installed")
        command = [
            self.command,
            "exec",
            "--json",
            "--strict-config",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-C",
            self.work_dir,
        ]
        for override in _CODEX_CONFIG_OVERRIDES:
            command.extend(("-c", override))
        runtime_model = _runtime_model(request.model)
        if runtime_model and runtime_model != "auto":
            command.extend(("-m", runtime_model))
        for override in _MODEL_CONFIG_OVERRIDES.get(request.model, ()):
            command.extend(("-c", override))
        for image_path in request.image_paths:
            command.extend(("-i", image_path))
        command.append("-")
        return command

    def complete(self, request, cancel_event=None):
        started_at = time.perf_counter()
        if not self.command:
            return ProviderResult(False, error_code="cli_not_installed")
        try:
            os.makedirs(self.work_dir, exist_ok=True)
        except OSError as exc:
            return ProviderResult(
                False, error_code="workdir_failed",
                error_detail=_sanitize_detail(str(exc)))

        command = self.build_command(request)
        flags = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                creationflags=flags,
            )
        except OSError as exc:
            return ProviderResult(
                False, error_code="cli_unavailable",
                error_detail=_sanitize_detail(str(exc)))
        spawn_ms = int((time.perf_counter() - started_at) * 1000)
        first_event_ms = None
        first_result_ms = None

        def current_metrics():
            metrics = [
                ("spawn_ms", spawn_ms),
                ("total_ms", int(
                    (time.perf_counter() - started_at) * 1000)),
            ]
            if first_event_ms is not None:
                metrics.append(("first_event_ms", first_event_ms))
            if first_result_ms is not None:
                metrics.append(("first_result_ms", first_result_ms))
            return tuple(metrics)

        deadline = time.monotonic() + max(1.0, request.timeout_seconds)
        prompt = build_codex_prompt(request)
        if cancel_event is not None and cancel_event.is_set():
            _kill_process(proc)
            return ProviderResult(
                False, error_code="cancelled", metrics=current_metrics())
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError as exc:
            _kill_process(proc)
            return ProviderResult(
                False, error_code="stdin_failed",
                error_detail=_sanitize_detail(str(exc)),
                metrics=current_metrics())

        output_queue = queue.Queue()
        stderr_chunks = []
        stdout_thread = threading.Thread(
            target=_read_stdout_lines,
            args=(proc.stdout, output_queue),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_read_stream,
            args=(proc.stderr, stderr_chunks),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        parser = CodexJsonlParser()
        line_number = 0
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _kill_process(proc)
                return ProviderResult(
                    False, error_code="cancelled",
                    metrics=current_metrics())
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_process(proc)
                return ProviderResult(
                    False, error_code="timeout",
                    metrics=current_metrics())
            try:
                kind, payload = output_queue.get(
                    timeout=min(0.1, remaining))
            except queue.Empty:
                continue
            if kind == "eof":
                break
            if kind == "error":
                _kill_process(proc)
                return ProviderResult(
                    False, error_code="stdout_failed",
                    error_detail=_sanitize_detail(str(payload)),
                    metrics=current_metrics())
            line_number += 1
            try:
                parser.feed(payload, line_number)
            except CodexProtocolError as exc:
                _kill_process(proc)
                return ProviderResult(
                    False,
                    error_code=exc.code,
                    error_detail=_sanitize_detail(exc.detail),
                    metrics=current_metrics(),
                )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            if first_event_ms is None:
                first_event_ms = elapsed_ms
            if first_result_ms is None and parser.final_text:
                first_result_ms = elapsed_ms

        remaining = max(0.1, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _kill_process(proc)
            return ProviderResult(
                False, error_code="timeout", metrics=current_metrics())
        stderr_thread.join(timeout=1)
        stderr = _sanitize_detail("".join(stderr_chunks))
        if proc.returncode != 0:
            return ProviderResult(
                False,
                error_code=_classify_error(stderr),
                error_detail=stderr,
                metrics=current_metrics(),
            )
        try:
            text = parser.finish()
        except CodexProtocolError as exc:
            error_code = (
                _classify_error(exc.detail)
                if exc.code == "remote_error" else exc.code)
            return ProviderResult(
                False,
                error_code=error_code,
                error_detail=_sanitize_detail(exc.detail),
                metrics=current_metrics(),
            )
        return ProviderResult(
            True,
            text=text,
            metrics=current_metrics(),
        )

    def stream(self, request, on_delta, cancel_event=None):
        from .codex_appserver import CodexAppServerTransport

        with self._appserver_lock:
            if self._shutdown:
                return ProviderResult(
                    False, error_code="appserver_shutdown")
            transport = self._appserver_transports.get(request.model)
            if transport is None:
                transport = CodexAppServerTransport(
                    self.command, self.work_dir)
                self._appserver_transports[request.model] = transport
        return transport.stream(request, on_delta, cancel_event)

    def shutdown(self):
        with self._appserver_lock:
            self._shutdown = True
            transports = tuple(self._appserver_transports.values())
            self._appserver_transports.clear()
        for transport in transports:
            transport.shutdown()


def _kill_process(proc):
    pid = getattr(proc, "pid", None)
    if os.name == "nt" and isinstance(pid, int):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=3,
                creationflags=_CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _read_stdout_lines(stream, output_queue):
    try:
        for line in iter(stream.readline, ""):
            output_queue.put(("line", line))
    except OSError as exc:
        output_queue.put(("error", exc))
    finally:
        output_queue.put(("eof", None))


def _read_stream(stream, chunks):
    try:
        chunks.append(stream.read())
    except OSError:
        return


def _auth_method(output):
    low = (output or "").lower()
    if "chatgpt" in low:
        return "chatgpt"
    if "api" in low:
        return "api"
    if "access token" in low:
        return "access_token"
    return "unknown"


def _classify_error(detail):
    low = (detail or "").lower()
    if any(token in low for token in (
            "not logged in", "login required", "authentication", "unauthorized")):
        return "login_required"
    if "rate limit" in low or "429" in low or "quota" in low:
        return "rate_limited"
    if "model" in low and any(token in low for token in (
            "not found", "not available", "permission", "access")):
        return "model_unavailable"
    if any(token in low for token in (
            "network", "connection", "proxy", "dns", "timed out")):
        return "network_error"
    return "request_failed"


def _sanitize_detail(detail, limit=400):
    text = (detail or "").replace("\r", " ").replace("\n", " ").strip()
    home = os.path.expanduser("~")
    if home and home != "~":
        text = text.replace(home, "%USERPROFILE%")
        text = text.replace(home.replace("\\", "/"), "%USERPROFILE%")
    return text[:limit]
