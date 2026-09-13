"""Explicit synthetic catalog executable; never discover or run a user's CLI."""

import json
import os
from pathlib import Path
import shlex
import sys


def create_cli(root, mode="normal"):
    from .catalog_fixture import create_catalog, PAYLOAD
    from cc_providers.codex_catalog import CodexModelCatalog
    from cc_providers.codex_config import CODEX_CONFIG_OVERRIDES

    storage, warnings = create_catalog(root)
    binary = Path(storage.command)
    binary.write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -I -B " +
        shlex.quote(str(Path(__file__).resolve())) + ' "$@"\n', encoding="utf-8")
    binary.chmod(0o700)
    (root / "payload.json").write_text(json.dumps(PAYLOAD), encoding="utf-8")
    (root / "overrides.json").write_text(json.dumps(CODEX_CONFIG_OVERRIDES), encoding="utf-8")
    environment = {
        "PATH": "/usr/bin:/bin", "HOME": storage.env["CODEX_HOME"],
        "CODEX_HOME": storage.env["CODEX_HOME"], "TMPDIR": str(root),
        "CC_SYNTHETIC_ROOT": str(root), "CC_SYNTHETIC_MODE": mode,
    }
    manager = CodexModelCatalog(
        str(binary), environment, storage.cache_dir, storage.work_dir, log_error=storage._log_error)
    return manager, warnings


def probe_catalog_process(root, cancel_event=None):
    from .catalog_fixture import PAYLOAD
    from cc_providers.codex_catalog import CatalogError, CodexModelCatalog

    manager, warnings = create_cli(root)
    config = Path(manager.env["CODEX_HOME"]) / "config.toml"
    before = config.read_bytes()
    first = manager.overrides(cancel_event=cancel_event)
    calls = root / "calls.jsonl"
    if not first or len(calls.read_text().splitlines()) != 3:
        raise CatalogError("synthetic_catalog_process_cold")
    catalog = Path(json.loads(first[0].split("=", 1)[1]))
    if (json.loads(catalog.read_bytes()) != PAYLOAD
            or manager.overrides("synthetic-small", cancel_event=cancel_event) != first
            or len(calls.read_text().splitlines()) != 3):
        raise CatalogError("synthetic_catalog_process_reuse")
    reopened = CodexModelCatalog(
        manager.command, manager.env, manager.cache_dir, manager.work_dir, log_error=manager._log_error)
    if (reopened.overrides(cancel_event=cancel_event) != first
            or len(calls.read_text().splitlines()) != 4
            or warnings or config.read_bytes() != before):
        raise CatalogError("synthetic_catalog_process_reopen")
    return {"status": "passed", "fixture": True, "process_verified": True,
            "cache_verified": True, "reopen_verified": True}


def _serve():
    import signal
    import subprocess
    import time

    root = Path(os.environ["CC_SYNTHETIC_ROOT"])
    mode = os.environ["CC_SYNTHETIC_MODE"]
    args = sys.argv[1:]
    overrides = [part for value in json.loads((root / "overrides.json").read_bytes())
                 for part in ("-c", value)]
    if args[-len(overrides):] != overrides:
        raise SystemExit(71)
    args = args[:-len(overrides)]
    if args not in (["--version"], ["debug", "models"]) and not (
            len(args) == 4 and args[:3] == ["debug", "models", "-c"]):
        raise SystemExit(72)
    with (root / "calls.jsonl").open("a", encoding="utf-8") as evidence:
        evidence.write(json.dumps({
            "pid": os.getpid(), "group": os.getpgrp(), "session": os.getsid(0),
            "args": sys.argv[1:], "cwd": os.getcwd(),
        }) + "\n")
    if mode == "roundtrip_timeout":
        mode = "timeout" if len(args) == 4 else "normal"
    if mode in {"descendant", "early", "timeout", "stdout_flood", "stderr_flood",
                "combined_flood", "closed_pipes"}:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        (root / "children").mkdir(exist_ok=True)
        child_path = root / "children" / (str(os.getpid()) + ".json")
        code = (
            "import json,os,pathlib,signal,sys,time; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "pathlib.Path(sys.argv[1]).write_text(json.dumps("
            "{'pid':os.getpid(),'group':os.getpgrp()})); time.sleep(45)"
        )
        streams = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if mode == "closed_pipes" else {}
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", code, str(child_path)],
                                 stdin=subprocess.DEVNULL, **streams)
        deadline = time.monotonic() + 3
        while not child_path.is_file():
            if child.poll() is not None or time.monotonic() >= deadline:
                raise SystemExit(74)
            time.sleep(0.01)
    if mode == "early":
        raise SystemExit(75)
    if mode == "nonzero":
        sys.stderr.write("SYNTHETIC_PRIVATE_CATALOG")
        raise SystemExit(73)
    if mode == "closed_pipes":
        os.close(1)
        os.close(2)
        time.sleep(45)
        return
    if mode == "timeout":
        time.sleep(45)
        return
    if mode in {"stdout_flood", "stderr_flood", "combined_flood"}:
        streams = {"stdout_flood": [sys.stdout.buffer], "stderr_flood": [sys.stderr.buffer],
                   "combined_flood": [sys.stdout.buffer, sys.stderr.buffer]}[mode]
        for stream in streams:
            stream.write(b"x" * ((5 if len(streams) == 2 else 9) * 1024 * 1024))
            stream.flush()
        time.sleep(45)
        return
    if args == ["--version"]:
        sys.stdout.write("codex-cli 0.146.0\n")
    elif len(args) == 4:
        key, separator, value = args[3].partition("=")
        if key != "model_catalog_json" or not separator:
            raise SystemExit(76)
        path = Path(json.loads(value))
        if not path.resolve().is_relative_to((root / "cache").resolve()):
            raise SystemExit(77)
        sys.stdout.buffer.write(path.read_bytes())
    else:
        sys.stdout.buffer.write((root / "payload.json").read_bytes())


if __name__ == "__main__":
    _serve()
