"""Synthetic CLI preparation only; requests still use the real translation service."""

import argparse
import json
from pathlib import Path
import sys


SCENARIOS = ("normal", "gated", "controls", "output-limit", "envelope-limit",
             "streaming-migration", "dictionary", "code", "summary", "direction")
INPUT = "  Synthetic translation input, with punctuation and Unicode \U0001f642.\r\n"
OUTPUT = "Synthetic translation \u4e2d\U0001f642"


def _root(root):
    root = Path(root).resolve()
    if (root == Path.cwd().resolve() or not root.is_relative_to(Path.cwd().resolve())
            or any(part.lower().endswith(".app") for part in root.parts)):
        raise ValueError("synthetic_root_required")
    return root


def prepare(root, application_id, scenario="normal", *, result_action=None, target_language=None, origin="text",
            model=None):
    from cc_config import Config, plan_config_migration
    from cc_providers.codex_cli import build_codex_prompt
    from cc_storage import macos_user_paths
    from cc_macos.native_provider_fixture import create_cli
    from cc_macos.translation import snapshot_for_translation, snapshot_for_result_action

    root = _root(root)
    if scenario not in SCENARIOS:
        raise ValueError("synthetic_scenario_required")
    if type(origin) is not str or origin not in ("text", "selection", "ocr"):
        raise ValueError("synthetic_origin_required")
    if result_action is not None and origin != "text":
        raise ValueError("synthetic_origin_not_applicable")
    macos_user_paths(root, application_id)
    root.mkdir(parents=True, exist_ok=True)
    native = root / "native"
    command, environment, _work, _cache, warnings = create_cli(native, "translation_" + scenario)
    if warnings:
        raise ValueError("synthetic_catalog_preparation_failed")
    # Keep the real service's per-application catalog below the existing fixture's
    # strictly checked cache boundary, without symlinks or relaxed catalog checks.
    home = native / "cache" / "synthetic home \u4e2d # %"
    home.mkdir(parents=True)
    environment["HOME"] = str(home)
    environment["TMPDIR"] = str(home)
    config = {"codex_model": "synthetic"}
    text, output = INPUT, OUTPUT
    if scenario == "streaming-migration":
        config["codex_streaming_experimental"] = False
    elif scenario == "dictionary":
        text = "synthetic"
    elif scenario == "code":
        text = "def synthetic(value):\n    return value + 1\n"
    elif scenario == "summary":
        text = ("This synthetic paragraph describes a translation test, its independent "
                "configuration, and the expected outcome. " * 16).strip()
    elif scenario == "direction":
        config["direction"] = "to_ja"
    elif scenario == "controls":
        output = "\u4e2d\U0001f642\t\n\r\u0000\u0001\\\"" * 240
    elif scenario == "output-limit":
        output = "\u0001" * 4_000
    elif scenario == "envelope-limit":
        output = "x" * 12_000
    if model is not None:
        if type(model) is not str or not model or model in ("auto", "auto-fast"):
            raise ValueError("synthetic_custom_model_required")
        # Model settings edit a loaded view, not an unmarked historical config.
        config = Config(config)
        config["codex_model"] = model
    request = {"operation": "translate", "text": text, "app_language": "zh_CN",
               "origin": origin, "use_cache": True, "record_history": True}
    normalized = Config(config)
    plan_config_migration(config, normalized)
    if result_action is None:
        snapshot = snapshot_for_translation(normalized, request)
    else:
        request = {"operation": "result_action", "action": result_action, "text": text,
                   "app_language": "zh_CN", "target_language": target_language}
        snapshot = snapshot_for_result_action(normalized, request)
    expected = {
        "prompt": build_codex_prompt(snapshot.request), "model": snapshot.selection.model,
        "task": snapshot.request.task, "output": output,
        "kind": snapshot.kind, "target_lang": snapshot.target_lang, "summarize": snapshot.summarize,
        "signature": snapshot.sig, "stream": snapshot.stream_enabled,
    }
    (native / "expected-request.json").write_text(
        json.dumps(expected, ensure_ascii=False), encoding="utf-8")
    return {
        "command": command, "home": str(home), "environment": environment,
        "root": str(native), "gate": str(native / "release.gate"),
        "config": config, "request": request, "expected": expected,
    }


def verify(root, *, require_cleanup=False, require_descendant=False):
    import subprocess
    import time

    root = _root(root)
    expected = json.loads((root / "expected-request.json").read_bytes())
    def records(name):
        path = root / name
        return ([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                if path.exists() else [])
    turns = [row for row in records("native-rpc.jsonl")
             if row["kind"] == "provider" and row["request"]["method"] == "turn/start"]
    expected_input = [{"type": "text", "text": expected["prompt"]}]
    if expected["task"] == "image":
        from cc_macos.image_fixture import VERIFIED_IMAGE_PATH
        expected_input.append({"type": "localImage", "path": VERIFIED_IMAGE_PATH})
        if records("image-read.jsonl") != [{"verified": True, "task": "image"}] * len(turns):
            raise ValueError("synthetic_image_evidence_missing")
    if any(row["request"]["params"]["input"] != expected_input
           for row in turns):
        raise ValueError("synthetic_prompt_mismatch")
    processes = [row for name in ("native-processes.jsonl", "version.jsonl", "calls.jsonl")
                 for row in records(name)]
    children = [json.loads(path.read_bytes()) for path in (root / "children").glob("*.json")]
    if require_descendant and not children:
        raise ValueError("synthetic_descendant_missing")
    groups = {row["group"] for row in processes}
    if (any(row["pid"] != row["group"] or row["pid"] != row["session"] for row in processes)
            or any(row["group"] not in groups for row in children)):
        raise ValueError("synthetic_ownership_mismatch")
    if require_cleanup:
        if sys.platform != "darwin" or not processes:
            raise ValueError("synthetic_native_cleanup_evidence_required")
        known_children = {row["pid"] for row in children}
        deadline = time.monotonic() + 5
        while True:
            result = subprocess.run(
                ["/bin/ps", "-axo", "pid=,ppid=,pgid=,stat="], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2, check=True)
            if result.stderr:
                raise ValueError("synthetic_process_inspection_failed")
            rows = [line.split() for line in result.stdout.decode("ascii").splitlines() if line.strip()]
            owned = [row for row in rows if int(row[2]) in groups]
            if all(int(row[0]) in known_children and int(row[1]) == 1 and row[3].startswith("Z")
                   for row in owned):
                break
            if time.monotonic() >= deadline:
                raise ValueError("synthetic_native_group_still_live")
            time.sleep(0.02)
    if (root / "home" / "config.toml").read_text(encoding="utf-8") != (
            'model="synthetic"\nmodel_provider="synthetic-provider"\n'):
        raise ValueError("synthetic_native_configuration_changed")
    return {"submitted_turns": len(turns), "prompt_verified": True,
            "processes": len(processes), "descendants": len(children),
            "cleanup_verified": require_cleanup}


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", type=Path)
    action.add_argument("--verify", type=Path)
    parser.add_argument("--application-id")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    parser.add_argument("--origin", choices=("text", "selection", "ocr"), default="text",
                        help="Origin for prepared text translation requests.")
    parser.add_argument("--result-action",
                        choices=("concise", "formal", "summary", "explain_code", "as_text", "retranslate"))
    parser.add_argument("--target-language")
    parser.add_argument("--model", help="Explicit custom model ID in the synthetic saved configuration.")
    parser.add_argument("--require-cleanup", action="store_true")
    parser.add_argument("--require-descendant", action="store_true")
    arguments = parser.parse_args(arguments)
    if arguments.prepare is not None:
        if arguments.application_id is None:
            parser.error("--prepare requires --application-id")
        result = prepare(arguments.prepare, arguments.application_id, arguments.scenario,
                         result_action=arguments.result_action, target_language=arguments.target_language,
                         origin=arguments.origin, model=arguments.model)
    else:
        result = verify(arguments.verify, require_cleanup=arguments.require_cleanup,
                        require_descendant=arguments.require_descendant)
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
