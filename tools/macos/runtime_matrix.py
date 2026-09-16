"""Same-run artifact identity and runtime checks; never builds or signs the tested app."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import zipfile

if __package__:
    from . import bundle, smoke
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.macos import bundle, smoke


ARCHIVE = "CCTranslateMac-P0.zip"
APP_NAME = "CCTranslateMac-P0.app"
RECEIPT = "runtime-receipt.json"
ROOT = Path(__file__).resolve().parents[2]
need = bundle.need
INTEGRATION_TESTS = (
    "testOptionalBundledHelperHandshakeFixtureAndShutdown",
    "testBundledConfigurationLoadSaveNormalizeStopAndReopen",
    "testBundledConfigurationCorruptFileFailsWithoutChangingBytes",
    "testBundledConfigurationCompetingHelperFailsThenTakesReleasedOwnership",
    "testBundledConfigurationWriteAndMigrationBudgetsPreserveReadableData",
    "testBundledHistoryLifecyclePaginationUnicodeAndConfigurationCoexistence",
    "testBundledHistoryCorruptOversizedAndRejectedAddsPreserveBytes",
    "testBundledHistoryCompetingHelpersReleaseBothOwners",
    "testBundledWorkerStartFailureFramesAreDeterminateForAllBusinessOperations",
    "testBundledTranslationConfigurationStreamHistoryCacheAndReopen",
    "testBundledTranslationConcurrentOptoutAndCancellationDrain",
    "testBundledTranslationCorruptionAndOutputBudgets",
    "testBundledTranslationCompetingHelpersForceStopAndReopen",
    "testBundledResultActionsUseNativeProviderWithoutReadingOrWritingHistory",
    "testBundledResultActionCancellationDrainsOwnedGroupsWithoutHistory",
    "testBundledDictionaryInstallLookupCacheHistoryAndLiveOptoutWithoutCLI",
    "testBundledDictionaryInvalidStagingDiscardDisableDeleteAndReopenWithoutCLI",
)


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tree_digest(app):
    content = smoke.snapshot(app)
    return json_digest([
        [name, content[name], stat.S_IMODE((app / name).lstat().st_mode)]
        for name in sorted(content)
    ])


def environment_record(major, xcode):
    need(sys.platform == "darwin" and platform.machine() == "arm64", "arm64 macOS required")
    developer = Path("/Applications") / ("Xcode_" + xcode + ".app") / "Contents/Developer"
    need(os.environ.get("DEVELOPER_DIR") == str(developer) and developer.is_dir(),
         "explicit preinstalled harness Xcode required")
    product = bundle.run(["/usr/bin/sw_vers", "-productVersion"])
    need(product.split(".")[0] == str(major), "runtime OS major mismatch")
    actual_xcode = bundle.run(["/usr/bin/xcodebuild", "-version"])
    need(actual_xcode.splitlines()[0] == "Xcode " + xcode, "harness Xcode version mismatch")
    return {
        "os_version": product,
        "os_build": bundle.run(["/usr/bin/sw_vers", "-buildVersion"]),
        "architecture": platform.machine(),
        "image_os": os.environ["ImageOS"],
        "image_version": os.environ["ImageVersion"],
        "xcode": actual_xcode,
        "swift": bundle.run(["/usr/bin/xcrun", "swift", "--version"]),
        "sdk": bundle.run(["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version"]),
    }


def verify_checkout(sha):
    need(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "invalid source SHA")
    need(bundle.run(["git", "-C", ROOT, "rev-parse", "HEAD"]) == sha, "checkout SHA mismatch")
    need(not bundle.run(["git", "-C", ROOT, "status", "--porcelain"]), "checkout is dirty")


def verify_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = set()
        for entry in archive.infolist():
            safe = bundle.archive_path(entry.filename)
            need(safe.parts[0] in (APP_NAME, "__MACOSX"), "unexpected archive root")
            need(entry.filename not in names, "duplicate archive entry")
            names.add(entry.filename)
            need(not (entry.external_attr >> 16) & 0o7000, "privileged archive mode")
        need(archive.testzip() is None, "archive CRC failed")
        for name in ("Contents/MacOS/CCTranslateMac", "Contents/Helpers/python/bin/python3.12"):
            entry = archive.getinfo(APP_NAME + "/" + name)
            need(stat.S_IMODE(entry.external_attr >> 16) == 0o755, "archive executable mode mismatch")
        link = archive.getinfo(APP_NAME + "/Contents/Helpers/python/bin/python3")
        need(stat.S_ISLNK(link.external_attr >> 16) and archive.read(link) == b"python3.12",
             "archive Python link mismatch")


def verify_source(app, sha):
    contents = app / "Contents"
    manifest = json.loads((contents / "Resources/source-manifest.json").read_bytes())
    need(manifest["source_commit"] == sha and manifest["source_tree_dirty"] is False,
         "bundle source identity mismatch")
    need(manifest["toolchain"]["xcode"].splitlines()[0] == "Xcode 16.4",
         "product was not built with Xcode 16.4")
    core = contents / "Resources/Core"
    sources = [p for p in core.rglob("*") if p.is_file() and
               (p.suffix == ".py" or p.name == "codex_instructions.txt")]
    for path in sources:
        relative = path.relative_to(core)
        source = ROOT / ("cc_macos/launch.py" if relative.as_posix() == "launch.py" else relative)
        need(not path.is_symlink() and source.is_file() and bundle.digest(path) == bundle.digest(source),
             "bundle/checkout source bytes differ")
    return manifest, len(sources)


def verify_receipt(app, directory, sha, run_id, expected_hash):
    receipt = json.loads((directory / RECEIPT).read_bytes())
    need(receipt["schema"] == 1 and receipt["source_sha"] == sha
         and receipt["run_id"] == run_id and receipt["archive"] == ARCHIVE, "receipt identity mismatch")
    archive = directory / ARCHIVE
    need(re.fullmatch(r"[0-9a-f]{64}", expected_hash) is not None, "invalid producer hash")
    need(receipt["archive_sha256"] == expected_hash == bundle.digest(archive), "archive hash mismatch")
    need(receipt["archive_bytes"] == archive.stat().st_size, "archive size mismatch")
    verify_archive(archive)
    manifest, count = verify_source(app, sha)
    need(receipt["producer_toolchain"] == manifest["toolchain"], "producer toolchain receipt mismatch")
    need(tree_digest(app) == receipt["tree_sha256"], "extracted app content/mode/link mismatch")
    return receipt, manifest, count


def prepare_harness(destination):
    need(not destination.exists(), "harness directory already exists")
    destination.mkdir()
    for name in ("CCProcessSupport", "CCTranslateSupport"):
        shutil.copytree(ROOT / "macos/Sources" / name, destination / "Sources" / name)
    test = Path("Tests/CCTranslateSupportTests/HelperIntegrationTests.swift")
    (destination / test).parent.mkdir(parents=True)
    shutil.copy2(ROOT / "macos" / test, destination / test)
    shutil.copy2(ROOT / "tools/macos/RuntimeHarnessPackage.swift", destination / "Package.swift")
    need(bundle.digest(destination / test) == bundle.digest(ROOT / "macos" / test),
         "integration test source changed")
    return tree_digest(destination)


def integration_result(text):
    source = (ROOT / "macos/Tests/CCTranslateSupportTests/HelperIntegrationTests.swift").read_text(encoding="utf-8")
    methods = re.findall(r"\bfunc (test\w+)\(", source)
    need(len(methods) == len(INTEGRATION_TESTS) and set(methods) == set(INTEGRATION_TESTS),
         "integration source method set changed")
    for outcome in ("started", "passed"):
        found = re.findall(
            r"Test Case '-\[CCTranslateSupportTests\.HelperIntegrationTests (\w+)\]' " + outcome, text)
        need(len(found) == len(INTEGRATION_TESTS) and set(found) == set(INTEGRATION_TESTS),
             "XCTest integration method set incomplete or duplicated")
    matches = re.findall(r"Executed (\d+) tests?, with (?:(\d+) tests? skipped and )?(\d+) failures", text)
    need(bool(matches), "XCTest integration not discovered")
    need(all(int(total) == len(INTEGRATION_TESTS) and int(skipped or 0) == 0 and int(failed) == 0
             for total, skipped, failed in matches), "XCTest integration failed/skipped/wrong count")
    timings = re.findall(r"(?m)^CC_TRANSLATE_DICTIONARY_TIMINGS (\{[^\n]+\})\s*$", text)
    need(len(timings) == 1, "dictionary warm lookup measurements missing or duplicated")
    timing = json.loads(timings[0])
    need(isinstance(timing, dict) and
         set(timing) == {"scope", "unit", "samples", "use_cache", "record_history"} and
         timing["scope"] == "config_only_foundation_helper_round_trip_not_gui" and
         timing["unit"] == "ms" and timing["use_cache"] is False and timing["record_history"] is False,
         "dictionary measurement scope mismatch")
    samples = timing["samples"]
    need(isinstance(samples, list) and len(samples) == 8 and
         all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in samples),
         "dictionary measurement samples invalid")
    return {"tests_run": len(INTEGRATION_TESTS), "failures": 0, "skipped": 0,
            "methods": list(INTEGRATION_TESTS), "dictionary_warm_lookup": timing}


def verify_integration(args):
    verify_checkout(args.source_sha)
    report = integration_result((args.directory / "integration-tests.log").read_text(encoding="utf-8"))
    bundle.write_json(args.directory / "integration-tests.json", report)


PRODUCT_DICTIONARY_METHOD = "testBundledWarmDictionaryIntentToNativePaintReportsMeasuredGoal"


def dictionary_product_result(text):
    for outcome in ("started", "passed"):
        methods = re.findall(
            r"Test Case '-\[CCTranslateMacTests\.DictionaryProductIntegrationTests (\w+)\]' " + outcome, text)
        need(methods == [PRODUCT_DICTIONARY_METHOD], "dictionary product test missing or duplicated")
    totals = re.findall(r"Executed (\d+) tests?, with (?:(\d+) tests? skipped and )?(\d+) failures", text)
    need(bool(totals) and all(int(total) == 1 and int(skipped or 0) == 0 and int(failed) == 0
                             for total, skipped, failed in totals), "dictionary product test failed/skipped")
    markers = re.findall(r"(?m)^CC_TRANSLATE_DICTIONARY_PRODUCT_TIMINGS (\{[^\n]+\})\s*$", text)
    need(len(markers) == 1, "dictionary product timing missing or duplicated")
    measurement = json.loads(markers[0])
    need(isinstance(measurement, dict), "dictionary product measurement is not an object")
    expected = {
        "scope": "same_source_model_and_retained_swiftui_appkit_view_with_bundled_configuration_helper",
        "source_cohort": "current Swift package model/view; helper from CC_TRANSLATE_APP; not the packaged GUI process",
        "endpoint": "explicit_model_translate_to_matching_read_only_nstextview_and_offscreen_bitmap_paint",
        "physical_keyboard": False, "full_gui": False, "onscreen_presentation": False,
        "unit": "ms", "warmup_intents": 2, "warm_intents": 10,
        "native_view_goal_ms": 150, "goal_is_asserted": False,
        "query_format_goal_ms": 10, "query_format_directly_measured": False,
        "setup_and_install_included": False, "network_download_measured": False,
        "history_enabled": False, "cache_hits": False, "cli_candidates": 0,
        "poll_interval_ms": 1, "ocr_source_visible": True,
    }
    need(all(type(measurement.get(key)) is type(value) and measurement[key] == value
             for key, value in expected.items()), "dictionary product measurement scope mismatch")
    for key in ("native_paint_samples", "intent_to_lookup_terminal_samples"):
        samples = measurement.get(key)
        need(isinstance(samples, list) and len(samples) == 10 and
             all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in samples),
             "dictionary product samples invalid")
    samples = sorted(measurement["native_paint_samples"])
    p95, maximum = samples[math.ceil(len(samples) * 0.95) - 1], samples[-1]
    need(all(type(measurement.get(key)) in (int, float) for key in ("native_paint_p95", "native_paint_max")) and
         measurement["native_paint_p95"] == p95 and measurement["native_paint_max"] == maximum,
         "dictionary product percentiles do not match samples")
    goal = "met" if p95 <= 150 else "measured_miss"
    need(measurement.get("native_view_goal_result") == goal, "dictionary product goal result mismatch")
    need(all(terminal <= painted for terminal, painted in
             zip(measurement["intent_to_lookup_terminal_samples"], measurement["native_paint_samples"])),
         "dictionary product endpoints out of order")
    return {"tests_run": 1, "failures": 0, "skipped": 0, "measurement": measurement}


def verify_dictionary_product(args):
    verify_checkout(args.source_sha)
    source = ROOT / "macos/Tests/CCTranslateMacTests/DictionaryProductIntegrationTests.swift"
    need(re.findall(r"\bfunc (test\w+)\(", source.read_text(encoding="utf-8")) == [PRODUCT_DICTIONARY_METHOD],
         "dictionary product source test inventory changed")
    report = dictionary_product_result((args.directory / "dictionary-product-tests.log").read_text(encoding="utf-8"))
    report.update(source_sha=args.source_sha, run_id=args.run_id,
                  producer_attempt=os.environ["GITHUB_RUN_ATTEMPT"])
    bundle.write_json(args.directory / "dictionary-product-tests.json", report)


def seal(args):
    verify_checkout(args.source_sha)
    environment = environment_record(15, "16.4")
    app, archive = args.directory / APP_NAME, args.directory / ARCHIVE
    verify_archive(archive)
    manifest, count = verify_source(app, args.source_sha)
    report = {
        "schema": 1, "source_sha": args.source_sha, "run_id": args.run_id,
        "producer_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "archive": ARCHIVE, "archive_sha256": bundle.digest(archive),
        "archive_bytes": archive.stat().st_size, "tree_sha256": tree_digest(app),
        "producer": environment, "producer_toolchain": manifest["toolchain"],
        "source_files_verified": count,
    }
    bundle.write_json(args.directory / RECEIPT, report)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write("archive_sha256=" + report["archive_sha256"] + "\n")
    print(json.dumps(report, indent=2))


def run_runtime(args):
    app = args.directory / APP_NAME
    output = args.output
    need(not output.is_relative_to(app), "runtime reports must be outside the app")
    need(not output.exists(), "runtime report directory already exists")
    output.mkdir(parents=True)
    report = {"status": "NOT PASSED", "source_sha": args.source_sha, "run_id": args.run_id,
              "stage": "identity", "not_tested": ["Finder", "GUI/TCC", "Gatekeeper", "Intel"]}
    before = None
    try:
        need(args.allow_https, "runtime requires explicit --allow-https")
        verify_checkout(args.source_sha)
        need(sys.flags.isolated and sys.dont_write_bytecode, "bundled runtime requires -I -B")
        need(Path(sys.executable).resolve() == (app / "Contents/Helpers/python/bin/python3").resolve(),
             "host Python cannot run runtime validation")
        report["runtime"] = environment_record(args.os_major, args.xcode)
        receipt, manifest, count = verify_receipt(
            app, args.directory, args.source_sha, args.run_id, args.archive_sha256)
        before = tree_digest(app)
        report.update(archive_sha256=receipt["archive_sha256"], tree_sha256=before,
                      producer=receipt["producer"], producer_toolchain=manifest["toolchain"],
                      producer_attempt=receipt["producer_attempt"], source_files_verified=count)
        report["stage"] = "audit-before"
        audited = bundle.audit_bundle(app, bundle.load_lock(), os.environ.copy())
        producer_audit = json.loads((args.directory / "bundle-audit.json").read_bytes())
        need(audited["inventory"] == producer_audit["inventory"], "producer/runtime inventory mismatch")
        report["inventory_entries"] = len(audited["inventory"])
        report["macho_count"] = len(audited["checks"])
        report["resource_hashes"] = len(manifest["resource_hashes"])
        for suite in ("process", "core"):
            report["stage"] = suite
            target = output / (suite + ".json")
            subprocess.run([sys.executable, "-I", "-B", str(ROOT / "tools/macos/bundled_tests.py"),
                            "--app", str(app), "--suite", suite, "--report", str(target)], check=True)
            report[suite] = json.loads(target.read_bytes())
            need(report[suite]["status"] == "passed", "bundled suite did not pass")
        report["stage"] = "https-smoke"
        need(smoke.run_smoke(app, output) == 0, "bundled smoke failed")
        report["smoke"] = json.loads((output / "helper-smoke.json").read_bytes())
        report["stage"] = "integration-harness"
        harness = output / "harness"
        report["harness_source_sha256"] = prepare_harness(harness)
        environment = os.environ.copy()
        environment["CC_TRANSLATE_APP"] = str(app)
        result = subprocess.run(
            ["/usr/bin/xcrun", "swift", "test", "--package-path", str(harness),
             "--triple", "arm64-apple-macosx14.0", "--filter", "HelperIntegrationTests"],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        print(result.stdout, flush=True)
        need(result.returncode == 0, "integration harness compile/run failed")
        report["integration"] = integration_result(result.stdout)
        report["stage"] = "audit-after"
        after = bundle.audit_bundle(app, bundle.load_lock(), os.environ.copy())
        need(after["inventory"] == audited["inventory"] and tree_digest(app) == before,
             "runtime tests modified app bytes/modes/links")
        report.update(status="passed", stage="complete", bundle_unchanged=True)
    finally:
        if before is not None:
            report["bundle_unchanged"] = tree_digest(app) == before
        bundle.write_json(output / "runtime-report.json", report)
    need(report.get("bundle_unchanged") is True, "runtime final app snapshot changed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seal", "run", "integration", "dictionary-product"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--archive-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--os-major", type=int, choices=(14, 26))
    parser.add_argument("--xcode", choices=("16.2", "26.6"))
    parser.add_argument("--allow-https", action="store_true")
    args = parser.parse_args(argv)
    args.directory = args.directory.resolve()
    if args.command == "run":
        if any(value is None for value in (args.output, args.archive_sha256, args.os_major, args.xcode)):
            parser.error("runtime arguments required")
        args.output = args.output.resolve()
    try:
        if args.command == "seal":
            seal(args)
        elif args.command == "integration":
            verify_integration(args)
        elif args.command == "dictionary-product":
            verify_dictionary_product(args)
        else:
            run_runtime(args)
    except (bundle.BundleError, OSError, ValueError, KeyError, zipfile.BadZipFile,
            subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, bundle.BundleError) else type(error).__name__
        print("BLOCKED: runtime validation:", detail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
