"""Model-free CI measurements; results are not user requirements or timing gates."""

import argparse
import json
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tempfile
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.macos import bundle, smoke
from tools.macos.performance_metrics import (
    CLASSIFICATION_CASES, DICTIONARY_CASES, measure, validate_measurement,
)


def validate_report(report, source):
    bundle.need(report["source_sha"] == source, "performance source mismatch")
    bundle.need(set(report["classification"]) == {case[0] for case in CLASSIFICATION_CASES},
                "classification corpus mismatch")
    bundle.need(set(report["dictionary"]) == set(DICTIONARY_CASES), "dictionary corpus mismatch")
    for section, target in (("classification", 2), ("dictionary", 10)):
        for value in report[section].values():
            validate_measurement(value, target)
    validate_measurement(report["resident_ipc"], 5)
    bundle.need(report["helper_clean_exit"] is True and report["temporary_home_removed"] is True,
                "performance helper or home cleanup incomplete")
    bundle.need(report["bundle_unchanged"] is True, "performance measurement modified the app")


def run_measurements(app, asset):
    app, asset = Path(app).resolve(), Path(asset).resolve()
    bundle.need(sys.platform == "darwin" and platform.machine() == "arm64",
                "performance measurements require arm64 macOS, not a host substitute")
    manifest = json.loads((app / "Contents/Resources/source-manifest.json").read_bytes())
    before = smoke.snapshot(app)
    python = app / "Contents/Resources/python/bin/python3"
    worker = Path(__file__).with_name("fixtures") / "performance_worker.py"
    result = subprocess.run([str(python), "-I", "-B", str(worker), str(app), str(asset)],
                            capture_output=True, text=True, check=True, timeout=60)
    bundle.need(not result.stderr, "unexpected performance worker diagnostics")
    report = json.loads(result.stdout)
    with (app / "Contents/Info.plist").open("rb") as stream:
        identity = plistlib.load(stream)["CFBundleIdentifier"]
    with tempfile.TemporaryDirectory(prefix="cc-performance-") as temporary:
        directory = Path(temporary)
        start = time.perf_counter_ns()
        session = smoke.Session([
            python, "-I", "-B", app / "Contents/Resources/Core/launch.py",
            "--config-home", directory / "home", "--application-id", identity,
        ], directory)
        try:
            sequence = 0

            def load():
                nonlocal sequence
                sequence += 1
                identifier = "timing-" + str(sequence)
                session.send(identifier, "request", {"operation": "config_load"})
                session.expect(identifier, "accepted")
                session.expect(identifier, "started")
                return session.expect(identifier, "completed")

            def ready(value):
                capabilities = value.get("capabilities", [])
                bundle.need(value.get("protocol") == 1 and "config_load" in capabilities
                            and "translate" not in capabilities, "not a configuration-only helper")

            session.send("hello", "hello", {})
            ready(session.expect("hello", "ready"))
            report["helper_startup_to_ready_ms"] = (time.perf_counter_ns() - start) / 1_000_000
            def configuration(value):
                bundle.need(isinstance(value.get("config"), dict) and bool(value["config"]),
                            "configuration IPC returned no configuration")

            report["resident_ipc"] = measure(load, configuration, 5)
            session.finish()
            report["helper_clean_exit"] = True
        finally:
            session.dispose()
    report.update(
        schema=1, status="passed", targets_are_gates=False,
        scope="model-free fixed corpus; no account, model, keyboard or GUI",
        first_sample_scope="first call per case after imports; not cold OS caches",
        warm_scope="same process and SQLite connection; no application result cache or history",
        ipc_endpoint="resident config_load write to parsed completed, including configuration work and host queue/JSON",
        temporary_home_removed=not directory.exists(), bundle_unchanged=smoke.snapshot(app) == before,
    )
    validate_report(report, manifest["source_commit"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_measurements(args.app, args.asset)
    bundle.write_json(args.report, report)
    print(json.dumps({key: report[key] for key in ("status", "source_sha", "scope")}))


if __name__ == "__main__":
    main()
