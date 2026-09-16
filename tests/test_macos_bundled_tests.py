"""Portable contract tests; never launch a macOS interpreter on the host."""

import ast
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.macos import bundled_tests


class ProjectDirectory(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".bundled-tests-", dir=bundled_tests.ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = self.root / "Synthetic.app"
        self.core = self.app / "Contents" / "Resources" / "Core"
        self.core.mkdir(parents=True)
        self.python = self.app / "Contents" / "Helpers" / "python" / "bin" / "python3"
        self.python.parent.mkdir(parents=True)
        self.python.write_bytes(b"synthetic, never executable")

    def module(self, name, root=None, package=False):
        root = self.core if root is None else root
        path = root.joinpath(*name.split("."))
        path = path / "__init__.py" if package else path.with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthetic source\n", encoding="utf-8")
        module = ModuleType(name)
        module.__file__ = str(path)
        if package:
            module.__path__ = [str(path.parent)]
        return module


class RuntimeTests(ProjectDirectory):
    def runtime(self, **overrides):
        values = {
            "platform": "darwin", "executable": str(self.python), "dont_write_bytecode": True,
            "flags": SimpleNamespace(isolated=1, dont_write_bytecode=1),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def validate(self, runtime=None, machine="arm64"):
        with patch.object(bundled_tests, "sys", runtime or self.runtime()), \
                patch.object(bundled_tests.platform, "machine", return_value=machine):
            return bundled_tests.validate_runtime(self.app)

    def test_selected_bundled_isolated_arm64_interpreter_is_accepted(self):
        self.assertEqual(self.validate(), self.core)

    def test_host_sibling_and_other_in_bundle_executables_are_rejected(self):
        others = (
            self.root / "host-python",
            self.root / "Other.app" / "Contents" / "Helpers" / "python" / "bin" / "python3",
            self.python.with_name("unrelated-python"),
        )
        for other in others:
            other.parent.mkdir(parents=True, exist_ok=True)
            other.write_bytes(b"synthetic")
            with self.subTest(executable=other.name), self.assertRaises(bundled_tests.BundledTestsError):
                self.validate(self.runtime(executable=str(other)))

    def test_wrong_platform_or_architecture_is_rejected(self):
        for system, machine in (("win32", "arm64"), ("linux", "arm64"), ("darwin", "x86_64")):
            with self.subTest(system=system, machine=machine), \
                    self.assertRaises(bundled_tests.BundledTestsError):
                self.validate(self.runtime(platform=system), machine)

    def test_isolation_and_both_bytecode_guards_are_required(self):
        for isolated, flag, value in ((0, 1, True), (1, 0, True), (1, 1, False)):
            with self.subTest(isolated=isolated, flag=flag, value=value), \
                    self.assertRaises(bundled_tests.BundledTestsError):
                self.validate(self.runtime(
                    flags=SimpleNamespace(isolated=isolated, dont_write_bytecode=flag),
                    dont_write_bytecode=value))

    def test_missing_expected_interpreter_does_not_fall_back(self):
        self.python.unlink()
        with self.assertRaises(OSError):
            self.validate()

    def test_resolved_interpreter_cannot_escape_through_a_symlink(self):
        outside = self.root / "outside-python"
        outside.write_bytes(b"synthetic")
        original = Path.resolve

        def resolve(path, *args, **kwargs):
            return outside if path == self.python else original(path, *args, **kwargs)

        with patch.object(Path, "resolve", new=resolve), self.assertRaises(bundled_tests.BundledTestsError):
            self.validate()


class InventoryTests(unittest.TestCase):
    def test_complete_original_suite_inventory_and_test_count_floors(self):
        self.assertEqual(bundled_tests.PROCESS_TEST_MODULES, (
            "test_codex_config_process", "test_codex_catalog_process", "test_history_owner_process",
            "test_config_owner_process", "test_configuration_ipc_process", "test_history_ipc_process",
            "test_darwin_rpc_process", "test_native_provider_process", "test_translation_ipc_process",
            "test_dictionary_ipc_process"))
        self.assertEqual(bundled_tests.CORE_TEST_MODULES, (
            "test_classify", "test_is_single_word", "test_direction", "test_classify_import",
            "test_prompts", "test_provider_contracts", "test_dictionary_store_portable",
            "test_catalog_storage_portable", "test_result_rules", "test_storage", "test_history",
            "test_config_rules", "test_config_store", "test_macos_configuration", "test_macos_history",
            "test_request_snapshot", "test_darwin_rpc_contract", "test_codex_darwin",
            "test_summary_rules", "test_macos_translation", "test_codex_version", "test_macos_dictionary"))
        self.assertNotIn("test_dictionary_portable", bundled_tests.CORE_TEST_MODULES,
                         "The Windows formatter test must not pull desktop facades into the bundle.")
        self.assertEqual(bundled_tests.SUITE_MODULES, {
            "process": bundled_tests.PROCESS_TEST_MODULES, "core": bundled_tests.CORE_TEST_MODULES})
        self.assertEqual(bundled_tests.TEST_SUPPORT_MODULES, {
            "process": ("owner_process_support", "state_ipc_process_support"), "core": ()})
        self.assertEqual(bundled_tests.MINIMUM_TEST_COUNTS, {"process": 202, "core": 536})
        for suite_name, names in bundled_tests.SUITE_MODULES.items():
            count = 0
            for name in names:
                source = bundled_tests.test_directory(suite_name) / (name + ".py")
                tree = ast.parse(source.read_text(encoding="utf-8"))
                count += sum(isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
                             for node in ast.walk(tree))
            self.assertGreaterEqual(count, bundled_tests.MINIMUM_TEST_COUNTS[suite_name])

    def test_original_explicit_core_origin_assertions_are_preserved(self):
        self.assertEqual(bundled_tests.PROCESS_BUNDLE_MODULES, (
            "cc_providers.codex_config", "cc_providers.codex_catalog", "cc_providers.darwin_process",
            "cc_providers.darwin_rpc", "cc_providers.codex_darwin", "cc_macos.native_provider_fixture",
            "cc_history", "cc_macos.history_owner", "cc_macos.history_fixture",
            "cc_config", "cc_config_store", "cc_macos.file_owner",
            "cc_macos.config_owner", "cc_macos.config_store_fixture",
            "cc_macos.configuration", "cc_macos.server", "cc_macos.protocol", "cc_macos.history",
            "cc_macos.translation", "cc_macos.translation_fixture", "cc_summary",
            "cc_dictionary_store", "cc_dictionary_lookup", "cc_dictionary_artifact_core",
            "cc_dictionary_presentation", "cc_macos.dictionary"))
        self.assertEqual(bundled_tests.CORE_BUNDLE_MODULES, (
            "cc_classify", "cc_direction", "cc_prompts", "cc_providers", "cc_dictionary_store",
            "cc_providers.codex_catalog", "cc_providers.codex_darwin", "cc_providers.darwin_rpc",
            "cc_result_rules", "cc_storage", "cc_macos.storage_fixture", "cc_history",
            "cc_config", "cc_config_store", "cc_macos.configuration", "cc_macos.server", "cc_macos.protocol",
            "cc_macos.history", "cc_request", "cc_summary", "cc_macos.translation",
            "cc_macos.translation_fixture", "cc_macos.native_provider_fixture",
            "cc_dictionary_lookup", "cc_dictionary_artifact_core", "cc_dictionary_presentation",
            "cc_macos.dictionary"))

    def test_checkout_is_derived_from_script_not_current_directory_or_latest_bundle(self):
        self.assertEqual(bundled_tests.ROOT, Path(bundled_tests.__file__).resolve().parents[2])
        self.assertEqual(bundled_tests.test_directory("core"), bundled_tests.ROOT / "tests")
        self.assertEqual(bundled_tests.test_directory("process"),
                         bundled_tests.ROOT / "macos" / "PythonTests")

    def test_import_has_no_execution_import_path_or_business_module_side_effects(self):
        before_path = sys.path[:]
        before_modules = {name for name in sys.modules if name.startswith("cc_")}
        spec = importlib.util.spec_from_file_location("bundled_tests_import_check", bundled_tests.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch.object(unittest.TextTestRunner, "run") as run, \
                patch.object(tempfile, "TemporaryDirectory") as temporary, \
                patch.object(Path, "write_text") as write:
            spec.loader.exec_module(module)
        self.assertEqual(sys.path, before_path)
        self.assertEqual({name for name in sys.modules if name.startswith("cc_")}, before_modules)
        run.assert_not_called()
        temporary.assert_not_called()
        write.assert_not_called()


class SourceTests(ProjectDirectory):
    def test_exact_source_and_package_search_path_are_accepted(self):
        for name, package in (("cc_storage", False), ("cc_providers", True),
                              ("cc_providers.codex_catalog", False)):
            with self.subTest(name=name):
                module = self.module(name, package=package)
                bundled_tests.verify_module_source(name, module, self.core)

    def test_host_wrong_filename_and_missing_sources_are_rejected(self):
        expected = self.module("cc_storage")
        host = self.module("cc_storage", self.root / "checkout")
        wrong = self.module("cc_wrong")
        for module in (host, wrong, ModuleType("cc_storage"), None):
            with self.subTest(module=module), self.assertRaises(bundled_tests.BundledTestsError):
                bundled_tests.verify_module_source("cc_storage", module, self.core)
        Path(expected.__file__).unlink()
        with self.assertRaises(bundled_tests.BundledTestsError):
            bundled_tests.verify_module_source("cc_storage", expected, self.core)

    def test_package_path_cannot_include_the_checkout(self):
        module = self.module("cc_providers", package=True)
        module.__path__.append(str(self.root / "checkout" / "cc_providers"))
        with self.assertRaises(bundled_tests.BundledTestsError):
            bundled_tests.verify_module_source("cc_providers", module, self.core)

    def test_all_cached_and_transitive_core_dependencies_are_checked(self):
        modules = {
            "cc_storage": self.module("cc_storage"),
            "cc_providers.base": self.module("cc_providers.base"),
            "cc_macos.catalog_fixture": self.module("cc_macos.catalog_fixture", self.root / "host"),
            "unrelated": ModuleType("unrelated"),
        }
        with patch.object(bundled_tests, "sys", SimpleNamespace(modules=modules)):
            with self.assertRaisesRegex(bundled_tests.BundledTestsError, "cc_macos.catalog_fixture"):
                bundled_tests.verify_core_sources(self.core)
            modules["cc_macos.catalog_fixture"] = self.module("cc_macos.catalog_fixture")
            bundled_tests.verify_core_sources(self.core)

    def test_every_requested_test_module_must_have_exact_checkout_origin(self):
        for suite_name, names in bundled_tests.SUITE_MODULES.items():
            names = (*bundled_tests.TEST_SUPPORT_MODULES[suite_name], *names)
            directory = self.root / ("PythonTests" if suite_name == "process" else "tests")
            modules = {name: self.module(name, directory) for name in names}
            with patch.object(bundled_tests, "test_directory", return_value=directory), \
                    patch.object(bundled_tests, "sys", SimpleNamespace(modules=modules)):
                bundled_tests.verify_test_sources(suite_name)
                for name in names:
                    good = modules[name]
                    modules[name] = self.module(name, self.core)
                    with self.subTest(suite=suite_name, module=name), \
                            self.assertRaises(bundled_tests.BundledTestsError):
                        bundled_tests.verify_test_sources(suite_name)
                    modules[name] = good

    def test_loader_gets_full_names_and_validates_before_and_after_loading(self):
        for suite_name, names in bundled_tests.SUITE_MODULES.items():
            count = bundled_tests.MINIMUM_TEST_COUNTS[suite_name]
            suite = Mock(countTestCases=Mock(return_value=count))
            with patch.object(bundled_tests, "import_core_module") as core_import, \
                    patch.object(bundled_tests.importlib, "import_module") as test_import, \
                    patch.object(bundled_tests, "verify_module_source") as source, \
                    patch.object(bundled_tests, "verify_test_sources") as test_sources, \
                    patch.object(bundled_tests, "verify_core_sources") as core_sources, \
                    patch.object(unittest.defaultTestLoader, "loadTestsFromNames", return_value=suite) as load:
                self.assertIs(bundled_tests.load_suite(suite_name, self.core), suite)
                load.assert_called_once_with(names)
                self.assertEqual([call.args[0] for call in core_import.call_args_list],
                                 list(bundled_tests.BUNDLE_MODULES[suite_name]))
                expected_imports = [*bundled_tests.TEST_SUPPORT_MODULES[suite_name], *names]
                self.assertEqual([call.args[0] for call in test_import.call_args_list], expected_imports)
                self.assertEqual([call.args[0] for call in source.call_args_list], expected_imports)
                test_sources.assert_called_once_with(suite_name)
                self.assertEqual(core_sources.call_count, 2)

    def test_test_origin_mismatch_aborts_before_unittest_loader(self):
        directory = self.root / "tests"
        modules = {}
        for name in bundled_tests.CORE_TEST_MODULES:
            modules[name] = self.module(name, directory)
        modules["test_storage"] = self.module("test_storage", self.core)
        with patch.object(bundled_tests, "verify_core_sources"), \
                patch.object(bundled_tests, "import_core_module"), \
                patch.object(bundled_tests, "test_directory", return_value=directory), \
                patch.object(bundled_tests.importlib, "import_module", side_effect=modules.__getitem__), \
                patch.object(unittest.defaultTestLoader, "loadTestsFromNames") as load:
            with self.assertRaisesRegex(bundled_tests.BundledTestsError, "test_storage"):
                bundled_tests.load_suite("core", self.core)
        load.assert_not_called()

    def test_cached_host_dependencies_abort_before_import_or_discovery(self):
        with patch.object(bundled_tests, "verify_core_sources",
                          side_effect=bundled_tests.BundledTestsError("host module")), \
                patch.object(bundled_tests.importlib, "import_module") as imports, \
                patch.object(unittest.defaultTestLoader, "loadTestsFromNames") as load:
            with self.assertRaises(bundled_tests.BundledTestsError):
                bundled_tests.load_suite("core", self.core)
        imports.assert_not_called()
        load.assert_not_called()


class ResultTests(ProjectDirectory):
    def run_main(self, suite_name="core", outcome="passed", discovered=None, storage_error=None):
        count = bundled_tests.MINIMUM_TEST_COUNTS[suite_name] if discovered is None else discovered

        def test_body(test):
            if outcome == "failure":
                test.fail("synthetic failure")
            elif outcome == "error":
                raise ValueError("synthetic error")
            elif outcome == "skip":
                test.skipTest("skips cannot pass bundled gates")

        case = type("SyntheticCase", (unittest.TestCase,), {"test_synthetic": test_body})
        suite = unittest.TestSuite(case("test_synthetic") for _ in range(count))
        report_path = self.root / "reports" / (suite_name + ".json")
        before_path = sys.path[:]
        stream = io.StringIO()

        def load_suite(actual_name, core):
            self.assertEqual(actual_name, suite_name)
            self.assertEqual(core, self.core)
            self.assertEqual(sys.path[:2], [
                str(self.core), str(bundled_tests.test_directory(suite_name))])
            self.assertEqual(sys.path[2:], before_path)
            return suite

        with patch.object(bundled_tests, "validate_runtime", return_value=self.core), \
                patch.object(bundled_tests, "load_suite", side_effect=load_suite) as load, \
                patch.object(bundled_tests, "verify_test_sources"), \
                patch.object(bundled_tests, "verify_core_sources"), \
                patch.object(bundled_tests, "run_storage_fixture", side_effect=storage_error) as storage, \
                redirect_stderr(stream), redirect_stdout(stream):
            code = bundled_tests.main([
                "--app", str(self.app), "--suite", suite_name, "--report", str(report_path)])
        self.assertEqual(sys.path, before_path)
        load.assert_called_once_with(suite_name, self.core)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(set(report), {
            "suite", "status", "tests_run", "failures", "errors", "skipped", "storage_fixture"})
        self.assertNotIn(str(self.root), json.dumps(report))
        return code, report, storage, stream.getvalue()

    def test_success_runs_verbose_tests_and_storage_only_for_core(self):
        for suite_name in bundled_tests.SUITE_MODULES:
            with self.subTest(suite=suite_name):
                code, report, storage, output = self.run_main(suite_name)
                self.assertEqual(code, 0)
                self.assertEqual(report["status"], "passed")
                self.assertEqual(report["tests_run"], bundled_tests.MINIMUM_TEST_COUNTS[suite_name])
                self.assertEqual((report["failures"], report["errors"], report["skipped"]), (0, 0, 0))
                self.assertIn("test_synthetic", output)
                if suite_name == "core":
                    storage.assert_called_once_with(self.app, self.core)
                    self.assertEqual(report["storage_fixture"], "passed")
                else:
                    storage.assert_not_called()
                    self.assertEqual(report["storage_fixture"], "not_run")

    def test_failures_errors_and_skips_write_counts_and_never_run_storage(self):
        count = bundled_tests.MINIMUM_TEST_COUNTS["core"]
        for outcome, field in (("failure", "failures"), ("error", "errors"), ("skip", "skipped")):
            with self.subTest(outcome=outcome):
                code, report, storage, _ = self.run_main(outcome=outcome)
                self.assertEqual(code, 1)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["tests_run"], count)
                self.assertEqual(report[field], count)
                self.assertEqual(report["storage_fixture"], "not_run")
                storage.assert_not_called()

    def test_empty_and_truncated_suites_never_pass(self):
        for suite_name in bundled_tests.SUITE_MODULES:
            for count in (0, bundled_tests.MINIMUM_TEST_COUNTS[suite_name] - 1):
                with self.subTest(suite=suite_name, count=count):
                    code, report, storage, _ = self.run_main(suite_name, discovered=count)
                    self.assertEqual(code, 1)
                    self.assertEqual(report["status"], "failed")
                    self.assertEqual(report["tests_run"], 0)
                    self.assertEqual(report["errors"], 1)
                    storage.assert_not_called()

    def test_fixture_failure_preserves_successful_test_counts_but_is_not_green(self):
        code, report, _, _ = self.run_main(storage_error=OSError(str(self.root)))
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["storage_fixture"], "failed")
        self.assertEqual(report["tests_run"], bundled_tests.MINIMUM_TEST_COUNTS["core"])
        self.assertEqual(report["errors"], 1)

    def test_invalid_interpreter_writes_failed_report_without_loading_suite(self):
        report_path = self.root / "failed.json"
        with patch.object(bundled_tests, "validate_runtime",
                          side_effect=bundled_tests.BundledTestsError("wrong interpreter")), \
                patch.object(bundled_tests, "load_suite") as load, redirect_stderr(io.StringIO()):
            code = bundled_tests.main([
                "--app", str(self.app), "--suite", "process", "--report", str(report_path)])
        self.assertEqual(code, 1)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual((report["status"], report["tests_run"], report["errors"]), ("failed", 0, 1))
        load.assert_not_called()

    def test_expected_boundary_exceptions_write_failed_reports(self):
        errors = (
            bundled_tests.BundledTestsError("source mismatch"), RuntimeError("native runtime"),
            OSError("fixture IO"), ValueError("fixture validation"), KeyError("CFBundleIdentifier"),
            ImportError("missing dependency"), AssertionError("module contract"),
            unittest.SkipTest("unsupported runtime"), bundled_tests.ExpatError("malformed plist"),
            json.JSONDecodeError("malformed JSON", "", 0), SystemExit(0),
        )
        report_path = self.root / "boundary.json"
        for error in errors:
            before_path = sys.path[:]
            with self.subTest(error=type(error).__name__), \
                    patch.object(bundled_tests, "validate_runtime", return_value=self.core), \
                    patch.object(bundled_tests, "load_suite", side_effect=error), \
                    redirect_stderr(io.StringIO()):
                code = bundled_tests.main([
                    "--app", str(self.app), "--suite", "core", "--report", str(report_path)])
                self.assertEqual(code, 1)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual((report["status"], report["tests_run"], report["errors"]),
                                 ("failed", 0, 1))
                self.assertEqual(sys.path, before_path)

    def test_unexpected_exceptions_propagate_without_success_fallback(self):
        class UnexpectedError(Exception):
            pass

        report_path = self.root / "unexpected.json"
        for error in (UnexpectedError("unexpected"), TypeError("bad type"), AttributeError("bad attribute")):
            before_path = sys.path[:]
            with self.subTest(error=type(error).__name__), \
                    patch.object(bundled_tests, "validate_runtime", return_value=self.core), \
                    patch.object(bundled_tests, "load_suite", side_effect=error), \
                    self.assertRaises(type(error)) as raised:
                bundled_tests.main([
                    "--app", str(self.app), "--suite", "core", "--report", str(report_path)])
            self.assertIs(raised.exception, error)
            self.assertEqual(sys.path, before_path)
            self.assertFalse(report_path.exists())

    def test_partial_test_execution_cannot_pass(self):
        for count in (0, 1):
            result = SimpleNamespace(
                testsRun=count, failures=[], errors=[], skipped=[], wasSuccessful=lambda: True)
            with self.subTest(count=count), patch.object(unittest.TextTestRunner, "run", return_value=result):
                code, report, storage, _ = self.run_main()
                self.assertEqual(code, 1)
                self.assertEqual(report["tests_run"], count)
                self.assertEqual(report["status"], "failed")
                storage.assert_not_called()

    def test_unsuccessful_result_without_failure_list_cannot_pass(self):
        result = SimpleNamespace(
            testsRun=bundled_tests.MINIMUM_TEST_COUNTS["core"],
            failures=[], errors=[], skipped=[], wasSuccessful=lambda: False)
        with patch.object(unittest.TextTestRunner, "run", return_value=result):
            code, report, storage, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        storage.assert_not_called()

    def test_late_source_mismatch_keeps_actual_test_counts_and_fails(self):
        error = bundled_tests.BundledTestsError("late module mismatch")
        count = bundled_tests.MINIMUM_TEST_COUNTS["process"]
        with patch.object(bundled_tests, "validate_runtime", return_value=self.core), \
                patch.object(bundled_tests, "load_suite",
                             return_value=Mock(countTestCases=Mock(return_value=count))), \
                patch.object(unittest.TextTestRunner, "run", return_value=SimpleNamespace(
                    testsRun=count, failures=[], errors=[], skipped=[], wasSuccessful=lambda: True)), \
                patch.object(bundled_tests, "verify_test_sources", side_effect=error), \
                redirect_stderr(io.StringIO()):
            report = bundled_tests.run_suite(self.app, "process")
        self.assertEqual((report["status"], report["tests_run"], report["errors"]), ("failed", count, 1))

    def test_report_write_failure_returns_nonzero(self):
        with patch.object(Path, "write_text", side_effect=OSError), \
                patch.object(bundled_tests, "run_suite", return_value={"status": "passed"}), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(bundled_tests.main([
                "--app", str(self.app), "--suite", "core", "--report", str(self.root / "out.json")]), 1)

    def test_report_cannot_write_inside_app(self):
        target = self.app / "report.json"
        with patch.object(bundled_tests, "run_suite") as run, redirect_stderr(io.StringIO()):
            self.assertEqual(bundled_tests.main([
                "--app", str(self.app), "--suite", "core", "--report", str(target)]), 1)
        run.assert_not_called()
        self.assertFalse(target.exists())


class StorageFixtureTests(ProjectDirectory):
    def test_real_storage_entry_uses_plist_identity_and_cleans_home(self):
        from cc_macos.storage_fixture import probe_storage

        identity = "test.synthetic.from-actual-plist"
        (self.app / "Contents" / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": identity}))
        homes = []

        def probe(home, actual_identity):
            homes.append(home)
            self.assertEqual(actual_identity, identity)
            self.assertTrue(home.parent.is_relative_to(self.root))
            probe_storage(home, actual_identity)
            for directory in ("Application Support", "Caches"):
                target = home / "Library" / directory / identity / "synthetic # %.json"
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")),
                                 {"synthetic": "replacement", "items": []})
                self.assertEqual(list(target.parent.iterdir()), [target])

        fixture = SimpleNamespace(probe_storage=Mock(side_effect=probe))
        with patch.object(bundled_tests, "ROOT", self.root), \
                patch.object(bundled_tests, "import_core_module", return_value=fixture) as load, \
                patch.object(bundled_tests, "verify_core_sources") as origins, \
                redirect_stdout(io.StringIO()):
            bundled_tests.run_storage_fixture(self.app, self.core)
        load.assert_called_once_with("cc_macos.storage_fixture", self.core)
        origins.assert_called_once_with(self.core)
        fixture.probe_storage.assert_called_once()
        self.assertEqual(len(homes), 1)
        self.assertFalse(homes[0].parent.exists())

    def test_storage_error_still_removes_the_synthetic_home(self):
        (self.app / "Contents" / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": "test.cleanup"}))
        homes = []

        def fail(home, identity):
            homes.append(home)
            home.mkdir()
            (home / "partial").write_bytes(b"synthetic")
            raise OSError("synthetic failure")

        fixture = SimpleNamespace(probe_storage=fail)
        with patch.object(bundled_tests, "ROOT", self.root), \
                patch.object(bundled_tests, "import_core_module", return_value=fixture), \
                patch.object(bundled_tests, "verify_core_sources"), self.assertRaises(OSError):
            bundled_tests.run_storage_fixture(self.app, self.core)
        self.assertEqual(len(homes), 1)
        self.assertFalse(homes[0].parent.exists())

    def test_missing_bundle_identity_has_no_default_and_does_not_probe(self):
        (self.app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({}))
        fixture = SimpleNamespace(probe_storage=Mock())
        with patch.object(bundled_tests, "import_core_module", return_value=fixture), \
                patch.object(bundled_tests, "verify_core_sources"), self.assertRaises(KeyError):
            bundled_tests.run_storage_fixture(self.app, self.core)
        fixture.probe_storage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
