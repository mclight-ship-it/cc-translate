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


def _validate_provider_catalog(root, remaining, model):
    from cc_macos.catalog_fixture import PAYLOAD

    if model not in {entry["slug"] for entry in PAYLOAD["models"]}:
        # A custom ID absent from optional metadata stays native; it is not a gate.
        if remaining:
            raise SystemExit(76)
        return
    if len(remaining) != 1 or not remaining[0].startswith("model_catalog_json="):
        raise SystemExit(76)
    catalog = Path(json.loads(remaining[0].split("=", 1)[1])).resolve()
    if not catalog.is_relative_to(root / "cache") or json.loads(catalog.read_bytes()) != PAYLOAD:
        raise SystemExit(77)


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


def _translation_gate(root, cwd):
    """Wait for an explicit test gate, but continue servicing real cancellation."""
    import select
    import time

    deadline = time.monotonic() + 30
    while not (root / "release.gate").exists():
        if time.monotonic() >= deadline:
            raise SystemExit(79)
        if not select.select([sys.stdin], [], [], 0.05)[0]:
            continue
        line = sys.stdin.readline()
        if not line:
            return False
        request = json.loads(line)
        if request.get("method") != "turn/interrupt":
            raise SystemExit(80)
        _receipt(root, "native-rpc.jsonl",
                 {"pid": os.getpid(), "kind": "provider", "request": request})
        for response in reply_messages(request, cwd):
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        return False
    return True


def _serve():
    import time

    # The launcher is -I: resolve dependencies exclusively beside this fixture.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cc_macos import catalog_process_fixture
    from cc_providers.base import ProviderRequest
    from cc_providers.codex_appserver import _DEFENDER_HOOK_COMMAND
    from cc_providers.codex_cli import build_codex_prompt
    from cc_providers.codex_config import CODEX_CONFIG_OVERRIDES, integration_overrides

    root = Path(os.environ["CC_SYNTHETIC_ROOT"]).resolve()
    cwd = str(Path.cwd().resolve())
    mode, args = os.environ["CC_SYNTHETIC_MODE"], sys.argv[1:]
    translation = (json.loads((root / "expected-request.json").read_bytes())
                   if mode.startswith("translation_") else None)
    if not Path(cwd).is_relative_to(Path(os.environ["HOME"]).resolve()):
        raise SystemExit(70)
    version_file = root / "version-output.bin"
    version_output = (version_file.read_bytes() if version_file.is_file() else
                      b"codex-cli 0.145.0\n" if mode == "unsupported_version" else
                      b"codex-cli 0.146.0\n")
    if args == ["--version"]:
        _receipt(root, "version.jsonl", {"pid": os.getpid(), "group": os.getpgrp(),
                                      "session": os.getsid(0), "args": args, "cwd": cwd})
        if mode == "version_timeout":
            time.sleep(45)
            return
        sys.stdout.buffer.write(version_output)
        return
    if args and args[0] in ("--version", "debug"):
        # Reuse the existing catalog version/export/roundtrip and override checks.
        os.environ["CC_SYNTHETIC_MODE"] = "normal"
        catalog_process_fixture._serve(version_output=version_output)
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
    selected_model = translation["model"] if translation is not None else "synthetic"
    if not config_probe:
        required.extend(integration_overrides(NATIVE_CONFIG["config"]))
        if selected_model == "gpt-5.4-mini":
            required.append('model_reasoning_effort="low"')
    if overrides[:len(required)] != required:
        raise SystemExit(73)
    if not config_probe:
        remaining = overrides[len(required):]
        _validate_provider_catalog(root, remaining, selected_model)
    info = {"pid": os.getpid(), "group": os.getpgrp(), "session": os.getsid(0),
            "args": args, "cwd": cwd, "kind": "config" if config_probe else "provider"}
    _receipt(root, "native-processes.jsonl", info)
    if not config_probe and mode in {
            "descendant", "eof", "timeout", "cancel", "shutdown", "blank_flood",
            "translation_gated"}:
        _descendant(root)
    previous = None
    for line in sys.stdin:
        request = json.loads(line)
        method = request["method"]
        if translation is not None and method == "thread/start":
            if request.get("params", {}).get("model") != translation["model"]:
                raise SystemExit(81)
        if method == "turn/start":
            expected = (translation["prompt"] if translation is not None else
                        build_codex_prompt(ProviderRequest(
                            "text", "synthetic", SYSTEM_PROMPT, USER_TEXT)))
            if request.get("params", {}).get("input") != [{"type": "text", "text": expected}]:
                raise SystemExit(78)
        # Receipts contain only our synthetic request, never account/config data.
        _receipt(root, "native-rpc.jsonl", {"pid": os.getpid(), "kind": info["kind"],
                                         "request": request})
        responses = reply_messages(request, cwd)
        if translation is not None and method == "turn/start":
            text = translation["output"]
            responses[1]["params"]["delta"] = text
            responses[2]["params"]["item"]["text"] = text
            if mode == "translation_envelope-limit":
                deltas = [{"method": "item/agentMessage/delta", "params": {
                    **responses[1]["params"], "delta": character}} for character in text]
                responses = [responses[0], *deltas, *responses[2:]]
            if mode == "translation_gated":
                for response in responses[:2]:
                    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
                if not _translation_gate(root, cwd):
                    return
                responses = responses[2:]
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
            if mode in ("timestamped", "invalid_timestamp"):
                responses.insert(0, {
                    "method": "remoteControl/status/changed",
                    "params": {"status": "disabled", "serverName": "synthetic",
                               "installationId": "synthetic", "environmentId": None},
                    "emittedAtMs": (1789560000000 if mode == "timestamped" else
                                    "SYNTHETIC_PRIVATE_TIMESTAMP"),
                })
            elif mode == "hooks":
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
            elif mode in {"duplicate_item", "late_item_delta", "restarted_item"}:
                late = json.loads(json.dumps(
                    responses[1] if mode == "late_item_delta" else responses[2]))
                if mode == "duplicate_item":
                    late["params"]["item"]["text"] = "SYNTHETIC_PRIVATE_REPLACEMENT"
                elif mode == "restarted_item":
                    late["method"] = "item/started"
                responses = [responses[0], responses[2], late, responses[-1]]
            elif mode in {"invalid_item_type", "invalid_item_phase"}:
                key = "type" if mode == "invalid_item_type" else "phase"
                responses[2]["params"]["item"][key] = []
                responses = [responses[0], responses[2], responses[-1]]
            elif mode == "multiple_items":
                started = json.loads(json.dumps(responses[2]))
                started["method"] = "item/started"
                started["params"]["item"].update(text="", phase=None)
                delta = json.loads(json.dumps(responses[1]))
                delta["params"].update(itemId="synthetic-second", delta="Synthetic second")
                final = json.loads(json.dumps(responses[2]))
                final["params"]["item"].update(id="synthetic-second", text="Synthetic second")
                responses = [responses[0], started, *responses[1:3], delta, final, responses[-1]]
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
            if mode == "timestamped" and "method" in response:
                response["emittedAtMs"] = 1789560000000
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
