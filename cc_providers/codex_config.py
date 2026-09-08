"""Native config inspection and child-only translation restrictions."""

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


class CodexConfigError(ValueError):
    """Only fixed, non-sensitive diagnostic codes may cross this boundary."""


_INSTRUCTIONS = str(Path(__file__).with_name("codex_instructions.txt").resolve())
CODEX_CONFIG_OVERRIDES = (
    'approval_policy="never"',
    "notify=[]",
    'developer_instructions=""',
    'instructions=""',
    'compact_prompt=""',
    "model_instructions_file=" + json.dumps(_INSTRUCTIONS),
    "experimental_compact_prompt_file=" + json.dumps(_INSTRUCTIONS),
    "project_doc_max_bytes=0",
    "project_doc_fallback_filenames=[]",
    "skills.include_instructions=false",
    "skills.bundled.enabled=false",
    "tools.update_plan.enabled=false",
    "tools.experimental_request_user_input.enabled=false",
    "orchestrator.skills.enabled=false",
    "orchestrator.mcp.enabled=false",
    "include_apps_instructions=false",
    "include_collaboration_mode_instructions=false",
    "features.personality=false",
    "features.tool_suggest=false",
    "features.mentions_v2=false",
    "features.auth_elicitation=false",
    "features.shell_tool=false",
    "features.unified_exec=false",
    "features.js_repl=false",
    "features.code_mode=false",
    "features.code_mode_only=false",
    "features.code_mode_host=false",
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
    "features.skill_search=false",
    "features.skill_mcp_dependency_install=false",
    "features.skill_env_var_dependency_prompt=false",
    "features.image_generation=false",
    "features.imagegenext=false",
    "features.browser_use=false",
    "features.browser_use_external=false",
    "features.in_app_browser=false",
    "features.computer_use=false",
    "features.apply_patch_freeform=false",
    "features.search_tool=false",
    "features.tool_search=false",
    "features.default_mode_request_user_input=false",
    "features.request_permissions_tool=false",
    "features.external_agent_memory_import=false",
    "memories.generate_memories=false",
    "memories.use_memories=false",
    'web_search="disabled"',
    "check_for_update_on_startup=false",
    "project_root_markers=[]",
) + tuple("hooks." + event + "=[]" for event in (
    "PermissionRequest", "PostCompact", "PostToolUse", "PreCompact",
    "PreToolUse", "SessionEnd", "SessionStart", "Stop", "SubagentStart",
    "SubagentStop", "UserPromptSubmit",
))


def child_environment():
    env = dict(os.environ)
    value = env.get("CC_TRANSLATE_CODEX_HOME", "").strip()
    if value:
        # Do not silently select another account when an explicit home is bad.
        env["CODEX_HOME"] = os.path.abspath(
            os.path.expandvars(os.path.expanduser(value)))
    elif env.get("CODEX_HOME"):
        env["CODEX_HOME"] = os.path.abspath(env["CODEX_HOME"])
    return env


def validate_home(env):
    home = env.get("CODEX_HOME")
    if home is not None and (not home or not Path(home).is_dir()):
        raise CodexConfigError("codex_home_invalid")
    if env.get("CC_TRANSLATE_CODEX_HOME", "").strip():
        if not (Path(home) / "config.toml").is_file():
            raise CodexConfigError("codex_home_config_missing")


def read_native_config(command, env, work_dir):
    """Read Codex's merged layers without starting a thread, auth helper or MCP.

    The native loader owns precedence and validation. Never log its response:
    provider definitions may contain credentials.
    """
    validate_home(env)
    try:
        os.makedirs(work_dir, exist_ok=True)
    except OSError:
        raise CodexConfigError("workdir_failed") from None
    args = [command, "app-server", "--strict-config"]
    for override in CODEX_CONFIG_OVERRIDES:
        args.extend(("-c", override))
    messages = queue.Queue()
    try:
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            env=env, cwd=work_dir,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        raise CodexConfigError("config_probe_unavailable") from None

    def read():
        try:
            for line in proc.stdout:
                messages.put(line)
        except (OSError, UnicodeError):
            pass
        finally:
            messages.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + 8

    def rpc(method, params, request_id):
        proc.stdin.write(json.dumps(
            {"id": request_id, "method": method, "params": params}) + "\n")
        proc.stdin.flush()
        while True:
            try:
                line = messages.get(timeout=max(.001, deadline - time.monotonic()))
            except queue.Empty:
                raise CodexConfigError("config_probe_timeout") from None
            if line is None:
                raise CodexConfigError("config_invalid")
            try:
                message = json.loads(line)
            except ValueError:
                raise CodexConfigError("config_probe_protocol") from None
            if not isinstance(message, dict):
                raise CodexConfigError("config_probe_protocol")
            if message.get("id") == request_id:
                if "error" in message:
                    raise CodexConfigError("config_invalid")
                return message.get("result")
            if time.monotonic() >= deadline:
                raise CodexConfigError("config_probe_timeout")

    try:
        rpc("initialize", {"clientInfo": {
            "name": "cc-translate-config", "version": "1"}}, 1)
        result = rpc("config/read", {
            "includeLayers": True, "cwd": os.path.abspath(work_dir)}, 2)
        if not isinstance(result, dict) or not isinstance(result.get("config"), dict):
            raise CodexConfigError("config_probe_protocol")
        return result
    except (OSError, UnicodeError):
        raise CodexConfigError("config_probe_failed") from None
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=3)
        reader.join(timeout=1)
        proc.stdin.close()
        proc.stdout.close()


def integration_overrides(config):
    servers = config.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise CodexConfigError("config_invalid")
    # An empty table MERGES; disable every discovered server individually.
    # Quoting keys inside the TOML value also handles names containing dots.
    entries = ", ".join(json.dumps(name) + "={enabled=false}" for name in servers)
    return ("mcp_servers={" + entries + "}",)
