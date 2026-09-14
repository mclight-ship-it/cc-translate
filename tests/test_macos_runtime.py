import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from tools.macos import bundle, runtime_matrix as runtime, smoke


class RuntimeMatrixTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / runtime.APP_NAME
        self.app.mkdir()

    def archive(self, *, native_mode=0o755, link=b"python3.12", extra=None):
        path = self.root / runtime.ARCHIVE
        with zipfile.ZipFile(path, "w") as archive:
            for name, mode, data in (
                    ("Contents/MacOS/CCTranslateMac", stat.S_IFREG | native_mode, b"synthetic"),
                    ("Contents/Helpers/python/bin/python3.12", stat.S_IFREG | 0o755, b"synthetic"),
                    ("Contents/Helpers/python/bin/python3", stat.S_IFLNK | 0o777, link)):
                info = zipfile.ZipInfo(runtime.APP_NAME + "/" + name)
                info.create_system = 3
                info.external_attr = mode << 16
                archive.writestr(info, data)
            if extra:
                archive.writestr(extra, b"synthetic")
        return path

    def test_archive_requires_exact_paths_modes_and_relative_link(self):
        runtime.verify_archive(self.archive())
        for options in ({"native_mode": 0o644}, {"link": b"/outside/python"},
                        {"extra": "../escape"}, {"extra": "other/file"}):
            with self.subTest(options=options), self.assertRaises(bundle.BundleError):
                runtime.verify_archive(self.archive(**options))

    def test_archive_rejects_privileged_modes(self):
        with self.assertRaises(bundle.BundleError):
            runtime.verify_archive(self.archive(native_mode=0o4755))

    def test_snapshot_detects_byte_changes_and_added_or_deleted_files(self):
        source = self.app / "resource"
        source.write_bytes(b"before")
        before = runtime.tree_digest(self.app)
        source.write_bytes(b"after")
        self.assertNotEqual(runtime.tree_digest(self.app), before)
        source.write_bytes(b"before")
        self.assertEqual(runtime.tree_digest(self.app), before)
        other = self.app / "unexpected"
        other.write_bytes(b"extra")
        self.assertNotEqual(runtime.tree_digest(self.app), before)
        other.unlink()
        source.unlink()
        self.assertNotEqual(runtime.tree_digest(self.app), before)

    def test_snapshot_includes_modes(self):
        (self.app / "resource").write_bytes(b"unchanged")
        before = runtime.tree_digest(self.app)
        with patch.object(smoke, "snapshot", return_value={"resource": "unchanged"}), \
                patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0o755)):
            first = runtime.tree_digest(self.app)
        with patch.object(smoke, "snapshot", return_value={"resource": "unchanged"}), \
                patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0o644)):
            second = runtime.tree_digest(self.app)
        self.assertNotEqual(first, second)
        self.assertEqual(runtime.tree_digest(self.app), before)

    def source_fixture(self):
        checkout = self.root / "checkout"
        checkout.mkdir()
        (checkout / "cc_synthetic.py").write_bytes(b"synthetic source")
        resources = self.app / "Contents/Resources"
        core = resources / "Core"
        core.mkdir(parents=True)
        (core / "cc_synthetic.py").write_bytes(b"synthetic source")
        manifest = {"source_commit": "a" * 40, "source_tree_dirty": False,
                    "toolchain": {"xcode": "Xcode 16.4\nBuild version test"}}
        bundle.write_json(resources / "source-manifest.json", manifest)
        return checkout, manifest

    def test_source_requires_checkout_bytes_clean_sha_and_original_compiler(self):
        checkout, original = self.source_fixture()
        manifest_file = self.app / "Contents/Resources/source-manifest.json"
        with patch.object(runtime, "ROOT", checkout):
            self.assertEqual(runtime.verify_source(self.app, "a" * 40), (original, 1))
            for key, value in (("source_commit", "b" * 40), ("source_tree_dirty", True),
                               ("toolchain", {"xcode": "Xcode 26.6"})):
                changed = {**original, key: value}
                bundle.write_json(manifest_file, changed)
                with self.subTest(key=key), self.assertRaises(bundle.BundleError):
                    runtime.verify_source(self.app, "a" * 40)
            bundle.write_json(manifest_file, original)
            (checkout / "cc_synthetic.py").write_bytes(b"wrong source")
            with self.assertRaises(bundle.BundleError):
                runtime.verify_source(self.app, "a" * 40)

    def test_receipt_binds_same_run_sha_zip_and_extracted_tree(self):
        checkout, manifest = self.source_fixture()
        archive = self.archive()
        expected_hash = bundle.digest(archive)
        record = {"schema": 1, "source_sha": "a" * 40, "run_id": "123",
                  "archive": runtime.ARCHIVE, "archive_bytes": archive.stat().st_size,
                  "archive_sha256": expected_hash, "tree_sha256": runtime.tree_digest(self.app),
                  "producer_toolchain": manifest["toolchain"]}
        with patch.object(runtime, "ROOT", checkout):
            bundle.write_json(self.root / runtime.RECEIPT, record)
            runtime.verify_receipt(self.app, self.root, "a" * 40, "123", expected_hash)
            for field, value in (("run_id", "124"), ("source_sha", "b" * 40),
                                 ("archive", "latest.zip"), ("archive_sha256", "0" * 64),
                                 ("archive_bytes", 0), ("tree_sha256", "bad"),
                                 ("producer_toolchain", {"xcode": "Xcode 26.6"})):
                with self.subTest(field=field):
                    bundle.write_json(self.root / runtime.RECEIPT, {**record, field: value})
                    with self.assertRaises(bundle.BundleError):
                        runtime.verify_receipt(self.app, self.root, "a" * 40, "123", expected_hash)

    def test_checkout_requires_exact_sha_and_no_changed_or_untracked_sources(self):
        with patch.object(bundle, "run", side_effect=["a" * 40, ""]):
            runtime.verify_checkout("a" * 40)
        for values in (["b" * 40], ["a" * 40, " M test.py"], ["a" * 40, "?? unknown.py"]):
            with patch.object(bundle, "run", side_effect=values), self.assertRaises(bundle.BundleError):
                runtime.verify_checkout("a" * 40)

    def test_runtime_platform_and_harness_toolchain_are_explicit(self):
        developer = str(Path("/Applications/Xcode_16.2.app/Contents/Developer"))
        environment = {"DEVELOPER_DIR": developer, "ImageOS": "macos14", "ImageVersion": "synthetic"}
        outputs = ["14.8.9", "Xcode 16.2\nBuild test", "test-build", "Swift test", "15.2"]
        with patch.object(runtime.sys, "platform", "darwin"), \
                patch.object(runtime.platform, "machine", return_value="arm64"), \
                patch.object(Path, "is_dir", return_value=True), patch.dict(os.environ, environment), \
                patch.object(bundle, "run", side_effect=outputs):
            result = runtime.environment_record(14, "16.2")
        self.assertEqual(result["os_version"], "14.8.9")
        self.assertEqual(result["xcode"], "Xcode 16.2\nBuild test")
        for values in (["26.6.2"], ["14.8.9", "Xcode 16.4"]):
            with patch.object(runtime.sys, "platform", "darwin"), \
                    patch.object(runtime.platform, "machine", return_value="arm64"), \
                    patch.object(Path, "is_dir", return_value=True), patch.dict(os.environ, environment), \
                    patch.object(bundle, "run", side_effect=values), self.assertRaises(bundle.BundleError):
                runtime.environment_record(14, "16.2")

    def test_wrong_architecture_or_missing_toolchain_never_falls_back(self):
        with patch.object(runtime.platform, "machine", return_value="x86_64"), \
                self.assertRaises(bundle.BundleError):
            runtime.environment_record(14, "16.2")
        with patch.object(runtime.sys, "platform", "darwin"), \
                patch.object(runtime.platform, "machine", return_value="arm64"), \
                patch.dict(os.environ, {"DEVELOPER_DIR": "wrong"}), self.assertRaises(bundle.BundleError):
            runtime.environment_record(14, "16.2")

    def test_integration_requires_exact_method_set_and_no_skip_or_failure(self):
        self.assertEqual(runtime.INTEGRATION_TESTS, (
            "testOptionalBundledHelperHandshakeFixtureAndShutdown",
            "testBundledConfigurationLoadSaveNormalizeStopAndReopen",
            "testBundledConfigurationCorruptFileFailsWithoutChangingBytes",
            "testBundledConfigurationCompetingHelperFailsThenTakesReleasedOwnership",
            "testBundledConfigurationWriteAndMigrationBudgetsPreserveReadableData",
            "testBundledHistoryLifecyclePaginationUnicodeAndConfigurationCoexistence",
            "testBundledHistoryCorruptOversizedAndRejectedAddsPreserveBytes",
            "testBundledHistoryCompetingHelpersReleaseBothOwners",
        ))
        methods = "\n".join("Test Case '-[CCTranslateSupportTests.HelperIntegrationTests " + method + "]' " + outcome
                            for method in runtime.INTEGRATION_TESTS for outcome in ("started", "passed"))
        summary = "Executed 8 tests, with 0 failures (0 unexpected)"
        result = runtime.integration_result(methods + "\n" + summary)
        self.assertEqual(result, {"tests_run": 8, "failures": 0, "skipped": 0,
                                  "methods": list(runtime.INTEGRATION_TESTS)})
        for text in ("0 tests passed", summary, methods,
                     methods + "\nExecuted 0 tests, with 0 failures",
                     methods + "\nExecuted 8 tests, with 1 test skipped and 0 failures",
                     methods + "\nExecuted 8 tests, with 1 failures",
                     methods + "\nExecuted 5 tests, with 0 failures",
                     methods + "\nExecuted 4 tests, with 0 failures",
                     methods + "\nExecuted 1 test, with 0 failures",
                     methods + "\n" + methods + "\n" + summary,
                     methods.replace(runtime.INTEGRATION_TESTS[0], "testUnexpected") + "\n" + summary):
            with self.subTest(text=text), self.assertRaises(bundle.BundleError):
                runtime.integration_result(text)

    def test_harness_reuses_original_integration_and_support_without_app_target(self):
        harness = self.root / "harness"
        digest = runtime.prepare_harness(harness)
        self.assertEqual(digest, runtime.tree_digest(harness))
        original = runtime.ROOT / "macos/Tests/CCTranslateSupportTests/HelperIntegrationTests.swift"
        self.assertEqual((harness / "Tests/CCTranslateSupportTests" / original.name).read_bytes(),
                         original.read_bytes())
        self.assertEqual({p.name for p in (harness / "Sources").iterdir()},
                         {"CCTranslateSupport", "CCProcessSupport"})
        self.assertNotIn("executableTarget", (harness / "Package.swift").read_text())
        with self.assertRaises(bundle.BundleError):
            runtime.prepare_harness(harness)

    def test_runtime_failure_writes_not_passed_report_without_running_harness(self):
        output = self.root / "report"
        args = SimpleNamespace(directory=self.root, output=output, source_sha="a" * 40,
                               run_id="123", allow_https=False)
        with patch.object(runtime.subprocess, "run") as process, self.assertRaises(bundle.BundleError):
            runtime.run_runtime(args)
        process.assert_not_called()
        report = json.loads((output / "runtime-report.json").read_bytes())
        self.assertEqual(report["status"], "NOT PASSED")
        self.assertEqual(report["stage"], "identity")

    def test_runtime_report_directory_cannot_modify_app(self):
        args = SimpleNamespace(directory=self.root, output=self.app / "reports")
        with self.assertRaises(bundle.BundleError):
            runtime.run_runtime(args)
        self.assertEqual(list(self.app.iterdir()), [])

    def test_producer_smoke_still_requires_explicit_https_and_xcode164(self):
        with patch.object(smoke, "require_macos") as toolchain, patch.object(smoke, "run_smoke") as exercise:
            self.assertEqual(smoke.main([]), 1)
            toolchain.assert_not_called()
            exercise.assert_not_called()
            exercise.return_value = 0
            self.assertEqual(smoke.main(["--allow-https"]), 0)
            toolchain.assert_called_once()
            exercise.assert_called_once_with(smoke.APP, smoke.BUILD, untested_os=("macOS 14",))
