"""Synthetic app-server executable for explicit diagnostics and process tests."""

import json
import os
from pathlib import Path
import shlex
import sys


def create_cli(directory, mode="normal"):
    directory.mkdir(parents=True, exist_ok=True)
    home = directory / "home"
    home.mkdir()
    (home / "config.toml").write_text("# synthetic fixture\n", encoding="utf-8")
    launcher = directory / "synthetic-codex"
    launcher.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -I -B " +
                        shlex.quote(str(Path(__file__).resolve())) + ' "$@"\n', encoding="utf-8")
    launcher.chmod(0o700)
    environment = {
        "PATH": "/usr/bin:/bin", "HOME": str(home), "CODEX_HOME": str(home),
        "CC_TRANSLATE_CODEX_HOME": str(home), "TMPDIR": str(directory),
        "CC_SYNTHETIC_ROOT": str(directory), "CC_SYNTHETIC_MODE": mode,
        "CC_SYNTHETIC_LAYER": "preserved",
    }
    return str(launcher), environment


def _serve():
    import signal
    import subprocess
    import time

    root = Path(os.environ["CC_SYNTHETIC_ROOT"])
    mode = os.environ["CC_SYNTHETIC_MODE"]
    evidence = {
        "pid": os.getpid(), "group": os.getpgrp(), "session": os.getsid(0),
        "args": sys.argv[1:], "cwd": os.getcwd(),
    }
    (root / "root.json").write_text(json.dumps(evidence), encoding="utf-8")
    if sys.argv[1:3] != ["app-server", "--strict-config"]:
        raise SystemExit(71)
    if mode in {"child", "early", "timeout", "flood"}:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        child_code = (
            "import json,os,pathlib,signal,time; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "pathlib.Path(os.environ['CC_SYNTHETIC_ROOT'],'child.json').write_text("
            "json.dumps({'pid':os.getpid(),'group':os.getpgrp()})); time.sleep(45)"
        )
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", child_code],
                                 stdin=subprocess.DEVNULL)
        while not (root / "child.json").is_file():
            if child.poll() is not None:
                raise SystemExit(72)
            time.sleep(0.01)
    if mode == "timeout":
        time.sleep(45)
        return
    if mode == "flood":
        sys.stdout.buffer.write(b"x" * (8 * 1024 * 1024 + 16_384))
        sys.stdout.buffer.flush()
        time.sleep(45)
        return
    methods = []
    for line in sys.stdin:
        request = json.loads(line)
        methods.append(request["method"])
        (root / "methods.json").write_text(json.dumps(methods), encoding="utf-8")
        if mode == "error":
            response = {"id": request["id"], "error": {"message": "SYNTHETIC_PRIVATE_CONFIG"}}
        elif mode == "malformed":
            sys.stdout.write("not-json\n")
            sys.stdout.flush()
            continue
        elif methods == ["initialize"] and request["params"] == {
                "clientInfo": {"name": "cc-translate-config", "version": "1"}}:
            response = {"id": request["id"], "result": {}}
        elif (methods == ["initialize", "config/read"]
              and set(request["params"]) == {"includeLayers", "cwd"}
              and request["params"]["includeLayers"] is True
              and Path(request["params"]["cwd"]).resolve() == Path.cwd().resolve()):
            evidence["request_cwd"] = request["params"]["cwd"]
            (root / "root.json").write_text(json.dumps(evidence), encoding="utf-8")
            response = {"id": request["id"], "result": {"config": {
                "model_provider": "synthetic", "opaque_layer": os.environ["CC_SYNTHETIC_LAYER"],
                "mcp_servers": {"fixture.node": {"enabled": True}},
            }, "layers": ["synthetic"]}}
        else:
            raise SystemExit(73)
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
        if mode == "early" and len(methods) == 2:
            return


def probe_config(directory, cancel_event=None):
    from cc_providers.codex_config import read_native_config, integration_overrides, CodexConfigError

    command, environment = create_cli(directory)
    result = read_native_config(command, environment, str(directory), cancel_event=cancel_event)
    methods = json.loads((directory / "methods.json").read_text(encoding="utf-8"))
    if (methods != ["initialize", "config/read"]
            or result["config"].get("model_provider") != "synthetic"
            or result["config"].get("opaque_layer") != "preserved"
            or result.get("layers") != ["synthetic"]
            or integration_overrides(result["config"]) != ('mcp_servers={"fixture.node"={enabled=false}}',)):
        raise CodexConfigError("config_fixture_mismatch")
    return {"status": "passed", "fixture": True, "methods_verified": True, "routing_preserved": True}


if __name__ == "__main__":
    _serve()
