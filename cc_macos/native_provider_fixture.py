"""Synthetic native app-server; never discovers a CLI, account, or model service."""

import json
import os
from pathlib import Path
import shlex
import sys


TEXT = "Synthetic translation \u4e2d\U0001f642"
SYSTEM_PROMPT = "Synthetic system \u4e2d\U0001f642\n"
USER_TEXT = "  Synthetic input \u4e2d\U0001f642\r\n"
NATIVE_CONFIG = {
    "config": {
        "model": "synthetic", "model_provider": "synthetic-provider",
        "mcp_servers": {"fixture.node": {"enabled": True}},
    },
    "layers": [],
}


def create_cli(root, mode="normal"):
    from cc_macos.catalog_process_fixture import create_cli as create_catalog_cli

    manager, warnings = create_catalog_cli(root)
    binary = Path(manager.command)
    binary.write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -I -B " +
        shlex.quote(str(Path(__file__).resolve())) + ' "$@"\n', encoding="utf-8")
    binary.chmod(0o700)
    environment = dict(manager.env)
    environment["CC_SYNTHETIC_MODE"] = mode
    work = Path(environment["HOME"]) / "work"
    work.mkdir()
    return str(binary), environment, str(work), manager.cache_dir, warnings


def reply_messages(request, cwd, *, thread_id="synthetic-thread", turn_id="synthetic-turn"):
    """The pinned SDK completion has turn.id, not a top-level turnId."""
    method, identifier = request["method"], request.get("id")
    if method == "initialize":
        return [{"id": identifier, "result": {}}]
    if method == "initialized":
        return []
    if method == "config/read":
        if request["params"] != {"includeLayers": True, "cwd": cwd}:
            raise ValueError("synthetic_config_request")
        return [{"id": identifier, "result": NATIVE_CONFIG}]
    if method == "hooks/list":
        if request["params"] != {"cwds": [cwd]}:
            raise ValueError("synthetic_hooks_request")
        return [{"id": identifier, "result": {"data": [{"cwd": cwd, "hooks": []}]}}]
    if method == "thread/start":
        return [{"id": identifier, "result": {"thread": {"id": thread_id}}}]
    identity = {"threadId": thread_id, "turnId": turn_id}
    if method == "turn/start":
        return [
            {"id": identifier, "result": {"turn": {"id": turn_id}}},
            {"method": "item/agentMessage/delta", "params": {
                **identity, "itemId": "synthetic-item", "delta": TEXT}},
            {"method": "item/completed", "params": {
                **identity, "item": {"id": "synthetic-item", "type": "agentMessage",
                                     "phase": "final_answer", "text": TEXT}}},
            {"method": "turn/completed", "params": {
                "threadId": thread_id, "turn": {"id": turn_id, "status": "completed"}}},
        ]
    if method == "turn/interrupt":
        return [
            {"id": identifier, "result": {}},
            {"method": "turn/completed", "params": {
                "threadId": thread_id, "turn": {"id": turn_id, "status": "interrupted"}}},
        ]
    raise ValueError("synthetic_unknown_method")


def _receipt(root, name, value):
    with (root / name).open("a", encoding="utf-8") as target:
        target.write(json.dumps(value, ensure_ascii=False) + "\n")


def _descendant(root):
    import signal
    import subprocess
    import time

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    children = root / "children"
    children.mkdir(exist_ok=True)
    child_path = children / (str(os.getpid()) + ".json")
    code = (
        "import json,os,pathlib,signal,sys,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps("
        "{'pid':os.getpid(),'group':os.getpgrp()}));time.sleep(45)"
    )
    child = subprocess.Popen([sys.executable, "-I", "-B", "-c", code, str(child_path)],
                             stdin=subprocess.DEVNULL)
    deadline = time.monotonic() + 3
    while not child_path.exists() or not child_path.stat().st_size:
        if child.poll() is not None or time.monotonic() >= deadline:
            raise SystemExit(74)
        time.sleep(0.01)


def _serve():
    import time

    # The launcher is -I: resolve dependencies exclusively beside this fixture.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cc_macos import catalog_process_fixture
    from cc_macos.catalog_fixture import PAYLOAD
    from cc_providers.base import ProviderRequest
    from cc_providers.codex_appserver import _DEFENDER_HOOK_COMMAND
    from cc_providers.codex_cli import build_codex_prompt
    from cc_providers.codex_config import CODEX_CONFIG_OVERRIDES, integration_overrides

    root = Path(os.environ["CC_SYNTHETIC_ROOT"]).resolve()
    cwd = str(Path.cwd().resolve())
    mode, args = os.environ["CC_SYNTHETIC_MODE"], sys.argv[1:]
    if not Path(cwd).is_relative_to(Path(os.environ["HOME"]).resolve()):
        raise SystemExit(70)
    if args == ["--version"]:
        _receipt(root, "version.jsonl", {"pid": os.getpid(), "group": os.getpgrp(),
                                      "session": os.getsid(0), "args": args, "cwd": cwd})
        sys.stdout.write("codex-cli " + ("0.147.0" if mode == "unsupported_version" else "0.146.0") + "\n")
        return
    if args and args[0] in ("--version", "debug"):
        # Reuse the existing catalog version/export/roundtrip and override checks.
        os.environ["CC_SYNTHETIC_MODE"] = "normal"
        catalog_process_fixture._serve()
        return
    config_probe = args[:2] == ["app-server", "--strict-config"]
    prefix = (["app-server", "--strict-config"] if config_probe else
              ["app-server", "--listen", "stdio://", "--strict-config"])
    if args[:len(prefix)] != prefix:
        raise SystemExit(71)
    values = args[len(prefix):]
    if len(values) % 2 or values[::2] != ["-c"] * (len(values) // 2):
        raise SystemExit(72)
    overrides = values[1::2]
    required = list(CODEX_CONFIG_OVERRIDES)
    if not config_probe:
        required.extend(integration_overrides(NATIVE_CONFIG["config"]))
    if overrides[:len(required)] != required:
        raise SystemExit(73)
    if not config_probe:
        remaining = overrides[len(required):]
        if len(remaining) != 1 or not remaining[0].startswith("model_catalog_json="):
            raise SystemExit(76)
        catalog = Path(json.loads(remaining[0].split("=", 1)[1])).resolve()
        if not catalog.is_relative_to(root / "cache") or json.loads(catalog.read_bytes()) != PAYLOAD:
            raise SystemExit(77)
    info = {"pid": os.getpid(), "group": os.getpgrp(), "session": os.getsid(0),
            "args": args, "cwd": cwd, "kind": "config" if config_probe else "provider"}
    _receipt(root, "native-processes.jsonl", info)
    if not config_probe and mode in {
            "descendant", "eof", "timeout", "cancel", "shutdown", "blank_flood"}:
        _descendant(root)
    previous = None
    for line in sys.stdin:
        request = json.loads(line)
        method = request["method"]
        if method == "turn/start":
            expected = build_codex_prompt(ProviderRequest(
                "text", "synthetic", SYSTEM_PROMPT, USER_TEXT))
            if request.get("params", {}).get("input") != [{"type": "text", "text": expected}]:
                raise SystemExit(78)
        # Receipts contain only our synthetic request, never account/config data.
        _receipt(root, "native-rpc.jsonl", {"pid": os.getpid(), "kind": info["kind"],
                                         "request": request})
        responses = reply_messages(request, cwd)
        if not config_probe and method == "initialize":
            if mode == "blank_flood":
                # Long whitespace-only lines exceed the cumulative 8 MiB
                # budget without requiring millions of per-line iterations.
                sys.stdout.buffer.write((b" " * (128 * 1024) + b"\r\n") * 65)
                sys.stdout.buffer.flush()
                time.sleep(45)
                return
            elif mode == "missing_rpc":
                responses = [{"result": {}}]
            elif mode == "wrong_rpc":
                responses[0]["id"] += 100
            elif mode == "duplicate_rpc":
                responses *= 2
            elif mode == "error":
                responses = [{"id": request["id"], "error": {
                    "message": "SYNTHETIC_PRIVATE_PROVIDER"}}]
            elif mode == "duplicate_keys":
                sys.stdout.write('{"id":1,"id":1,"result":{}}\n')
                sys.stdout.flush()
                continue
            elif mode == "nonfinite":
                responses[0]["result"] = {"number": float("nan")}
            elif mode == "unknown_envelope":
                responses[0]["private"] = "SYNTHETIC_PRIVATE_PROVIDER"
            elif mode == "response_params":
                responses[0]["params"] = {}
        if not config_probe and method == "hooks/list":
            if mode == "hooks":
                responses[0]["result"]["data"][0]["hooks"] = [{"enabled": True}]
            elif mode == "defender_hook":
                responses[0]["result"]["data"][0]["hooks"] = [{
                    "enabled": True, "source": "system", "handlerType": "command",
                    "isManaged": True, "trustStatus": "managed",
                    "sourcePath": str(root / "ProgramData" / "Microsoft" /
                                      "Windows Defender" / "Platform" / "synthetic.json"),
                    "command": _DEFENDER_HOOK_COMMAND,
                }]
            elif mode == "late_rpc" and previous is not None:
                responses.insert(0, previous)
        if not config_probe and method == "turn/start":
            if mode == "eof":
                return
            if mode in {"timeout", "cancel", "shutdown"}:
                responses = responses[:1]
            elif mode == "wrong_identity":
                responses[1]["params"]["threadId"] = "synthetic-other"
            elif mode == "missing_identity":
                responses[1]["params"].pop("turnId")
            elif mode == "missing_item":
                responses[1]["params"].pop("itemId")
            elif mode == "missing_completed_item":
                responses[2]["params"]["item"].pop("id")
            elif mode == "missing_started_item":
                responses[2]["method"] = "item/started"
                responses[2]["params"]["item"].pop("id")
            elif mode == "wrong_completion":
                responses[-1]["params"]["turn"]["id"] = "synthetic-other"
            elif mode == "tool":
                responses[1] = {"method": "item/started", "params": {
                    "threadId": "synthetic-thread", "turnId": "synthetic-turn",
                    "item": {"id": "synthetic-item", "type": "commandExecution"}}}
            elif mode == "server_request":
                responses[1] = {"id": 900, "method": "item/commandExecution/requestApproval",
                                "params": {}}
            elif mode == "notification_result":
                responses[1]["result"] = {}
            elif mode == "hook_notification":
                responses[1] = {"method": "hook/started", "params": {"run": {
                    "source": "system", "handlerType": "command",
                    "eventName": "SessionStart", "sourcePath": None}}}
        for response in responses:
            encoded = json.dumps(response, ensure_ascii=False)
            if not config_probe and mode == "blank_crlf":
                sys.stdout.write("\n\r\n \t\r\n" + encoded + "\r\n")
            else:
                sys.stdout.write(encoded + "\n")
            sys.stdout.flush()
            if "id" in response:
                previous = response
        if not config_probe and mode == "timeout" and method == "turn/start":
            time.sleep(45)


if __name__ == "__main__":
    _serve()
