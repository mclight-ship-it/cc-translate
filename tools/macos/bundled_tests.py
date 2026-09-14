"""Run checkout contracts using only an explicitly selected app's isolated Python."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import platform
import plistlib
import sys
import tempfile
import unittest
from xml.parsers.expat import ExpatError


ROOT = Path(__file__).resolve().parents[2]
PROCESS_TEST_MODULES = (
    "test_codex_config_process",
    "test_codex_catalog_process",
    "test_history_owner_process",
    "test_config_owner_process",
    "test_configuration_ipc_process",
)
CORE_TEST_MODULES = (
    "test_classify",
    "test_is_single_word",
    "test_direction",
    "test_classify_import",
    "test_prompts",
    "test_provider_contracts",
    "test_dictionary_store_portable",
    "test_catalog_storage_portable",
    "test_result_rules",
    "test_storage",
    "test_history",
    "test_config_rules",
    "test_config_store",
    "test_macos_configuration",
)
SUITE_MODULES = {"process": PROCESS_TEST_MODULES, "core": CORE_TEST_MODULES}
TEST_SUPPORT_MODULES = {"process": ("owner_process_support",), "core": ()}
MINIMUM_TEST_COUNTS = {"process": 76, "core": 218}
PROCESS_BUNDLE_MODULES = (
    "cc_providers.codex_config",
    "cc_providers.codex_catalog",
    "cc_providers.darwin_process",
    "cc_history",
    "cc_macos.history_owner",
    "cc_macos.history_fixture",
    "cc_config",
    "cc_config_store",
    "cc_macos.file_owner",
    "cc_macos.config_owner",
    "cc_macos.config_store_fixture",
    "cc_macos.configuration",
    "cc_macos.server",
    "cc_macos.protocol",
)
CORE_BUNDLE_MODULES = (
    "cc_classify",
    "cc_direction",
    "cc_prompts",
    "cc_providers",
    "cc_dictionary_store",
    "cc_providers.codex_catalog",
    "cc_result_rules",
    "cc_storage",
    "cc_macos.storage_fixture",
    "cc_history",
    "cc_config",
    "cc_config_store",
    "cc_macos.configuration",
    "cc_macos.server",
    "cc_macos.protocol",
)
BUNDLE_MODULES = {"process": PROCESS_BUNDLE_MODULES, "core": CORE_BUNDLE_MODULES}


class BundledTestsError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise BundledTestsError(message)


def validate_runtime(app):
    """Refuse host interpreters, including symlinks escaping the selected app."""
    require(sys.platform == "darwin" and platform.machine() == "arm64",
            "Bundled suites require Darwin arm64.")
    require(sys.flags.isolated and sys.flags.dont_write_bytecode and sys.dont_write_bytecode,
            "Invoke the bundled Python with -I -B.")
    app = Path(app).resolve(strict=True)
    require(app.is_dir() and app.suffix == ".app", "An explicit app bundle is required.")
    runtime = app / "Contents" / "Helpers" / "python"
    executable = Path(sys.executable).resolve(strict=True)
    expected = (runtime / "bin" / "python3").resolve(strict=True)
    require(executable.is_file() and executable == expected and executable.is_relative_to(runtime),
            "The interpreter must be this app's Contents/Helpers/python/bin/python3.")
    core = app / "Contents" / "Resources" / "Core"
    require(core.is_dir() and core.resolve(strict=True) == core,
            "Core must be a real directory inside the selected app.")
    return core


def verify_module_source(name, module, root):
    """Check exact files and package search paths, not just a shared path prefix."""
    expected = root.joinpath(*name.split("."))
    package = hasattr(module, "__path__")
    expected = expected / "__init__.py" if package else expected.with_suffix(".py")
    source = getattr(module, "__file__", None)
    require(bool(source) and expected.is_file() and Path(source).resolve() == expected,
            "Module source mismatch: " + name)
    if package:
        require(tuple(Path(path).resolve() for path in module.__path__) == (expected.parent,),
                "Package search path mismatch: " + name)


def verify_core_sources(core):
    # Include cached and transitively imported core modules, not only direct imports.
    for name, module in tuple(sys.modules.items()):
        if name.split(".")[0].startswith("cc_"):
            verify_module_source(name, module, core)


def import_core_module(name, core):
    module = importlib.import_module(name)
    verify_module_source(name, module, core)
    return module


def test_directory(suite_name):
    return ROOT / "macos" / "PythonTests" if suite_name == "process" else ROOT / "tests"


def verify_test_sources(suite_name):
    directory = test_directory(suite_name)
    for name in (*TEST_SUPPORT_MODULES[suite_name], *SUITE_MODULES[suite_name]):
        verify_module_source(name, sys.modules.get(name), directory)


def load_suite(suite_name, core):
    verify_core_sources(core)
    for name in BUNDLE_MODULES[suite_name]:
        import_core_module(name, core)
    for name in (*TEST_SUPPORT_MODULES[suite_name], *SUITE_MODULES[suite_name]):
        module = importlib.import_module(name)
        verify_module_source(name, module, test_directory(suite_name))
    suite = unittest.defaultTestLoader.loadTestsFromNames(SUITE_MODULES[suite_name])
    verify_test_sources(suite_name)
    verify_core_sources(core)
    require(suite.countTestCases() >= MINIMUM_TEST_COUNTS[suite_name],
            "The full bundled suite must actually be discovered.")
    return suite


def run_storage_fixture(app, core):
    fixture = import_core_module("cc_macos.storage_fixture", core)
    verify_core_sources(core)
    identity = plistlib.loads((Path(app) / "Contents" / "Info.plist").read_bytes())["CFBundleIdentifier"]
    with tempfile.TemporaryDirectory(prefix=".cc-storage-fixture-", dir=ROOT) as directory:
        fixture.probe_storage(Path(directory) / "synthetic home", identity)
    print("Bundled storage fixture passed with bundle identity and temporary home")


def run_suite(app, suite_name):
    """Return path-free counts; any discovery, provenance or fixture error fails closed."""
    report = {
        "suite": suite_name, "status": "failed", "tests_run": 0, "failures": 0,
        "errors": 0, "skipped": 0, "storage_fixture": "not_run",
    }
    previous_path = sys.path[:]
    try:
        core = validate_runtime(app)
        sys.path[:0] = [str(core), str(test_directory(suite_name))]
        suite = load_suite(suite_name, core)
        expected_count = suite.countTestCases()
        require(expected_count >= MINIMUM_TEST_COUNTS[suite_name],
                "The full bundled suite must actually be discovered.")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        report.update(tests_run=result.testsRun, failures=len(result.failures),
                      errors=len(result.errors), skipped=len(result.skipped))
        verify_test_sources(suite_name)
        verify_core_sources(core)
        if (not result.wasSuccessful() or report["failures"] or report["errors"]
                or report["skipped"] or result.testsRun != expected_count):
            return report
        if suite_name == "core":
            report["storage_fixture"] = "failed"
            run_storage_fixture(app, core)
            report["storage_fixture"] = "passed"
            verify_core_sources(core)
        report["status"] = "passed"
    except (RuntimeError, OSError, ValueError, KeyError, ImportError, AssertionError,
            unittest.SkipTest, ExpatError, SystemExit) as error:
        report["errors"] += 1
        # Exceptions may contain user/machine paths; keep both JSON and diagnostics bounded.
        message = str(error) if isinstance(error, BundledTestsError) else type(error).__name__
        print("Bundled suite failed: " + message, file=sys.stderr)
    finally:
        sys.path[:] = previous_path
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--suite", required=True, choices=tuple(SUITE_MODULES))
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.report.resolve().is_relative_to(args.app.resolve()):
        print("Bundled suite reports must be outside the app.", file=sys.stderr)
        return 1
    report = run_suite(args.app, args.suite)
    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        print("Bundled suite report could not be written.", file=sys.stderr)
        return 1
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
