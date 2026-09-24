"""Offline stdlib tests for the macOS archive, license, dyld and smoke rules."""

from copy import deepcopy
from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import plistlib
import queue
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from tools.macos import bundle, smoke


SHARED_CORE_FILES = ("cc_classify.py", "cc_direction.py", "cc_prompts.py", "cc_dictionary_store.py",
                     "cc_dictionary_lookup.py", "cc_dictionary_artifact_core.py", "cc_dictionary_presentation.py",
                     "cc_result_rules.py", "cc_storage.py", "cc_history.py", "cc_config.py",
                     "cc_config_store.py", "cc_request.py", "cc_summary.py")
CONTRACT_FILES = ("__init__.py", "base.py", "registry.py")
CONFIG_FILES = ("codex_config.py", "codex_config_darwin.py", "darwin_process.py", "codex_instructions.txt")
CATALOG_FILES = ("codex_catalog.py",)
NATIVE_FILES = ("codex_cli.py", "codex_jsonl.py", "codex_appserver.py",
                "codex_darwin.py", "darwin_rpc.py", "darwin_print.py",
                "claude_jsonl.py", "claude_darwin.py")
PROVIDER_FILES = CONTRACT_FILES + CONFIG_FILES + CATALOG_FILES + NATIVE_FILES


def member(name, kind=tarfile.REGTYPE, target="", data=b"x"):
    result = tarfile.TarInfo(name)
    result.type = kind
    result.linkname = target
    result.mode = 0o755 if kind == tarfile.DIRTYPE else 0o644
    result.size = len(data) if kind == tarfile.REGTYPE else 0
    return result


class ProjectDirectory(unittest.TestCase):
    def setUp(self):
        bundle.STAGING.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="bundle-test-", dir=bundle.STAGING)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)


class ApplicationVersionTests(unittest.TestCase):
    def setUp(self):
        self.lock = bundle.load_lock()
        self.template = plistlib.loads((bundle.ROOT / "macos/Resources/Info.plist").read_bytes())

    def test_build_override_preserves_source_identity_and_template(self):
        original = deepcopy(self.template)
        info = bundle.package_info(self.template, self.lock, "146")
        self.assertEqual(self.template, original)
        self.assertEqual(info, {**original, "CFBundleVersion": "146"})
        self.assertEqual(info["CFBundleIdentifier"], self.lock["bundle_identifier"])
        self.assertEqual(info["CFBundleDisplayName"], "CC Translate")
        self.assertEqual(bundle.application_metadata(info, self.lock), {
            "bundle_identifier": self.lock["bundle_identifier"],
            "version": original["CFBundleShortVersionString"], "build": "146",
            "architecture": "arm64", "minimum_system_version": "14.0",
        })

    def test_local_build_uses_explicit_template_version_without_environment_inference(self):
        with patch.dict(bundle.os.environ, {"GITHUB_RUN_NUMBER": "999", "GITHUB_RUN_ATTEMPT": "3"}):
            info = bundle.package_info(self.template, self.lock)
        self.assertEqual(info, self.template)
        self.assertIsNot(info, self.template)

    def test_build_numbers_are_decimal_and_successive_workflow_numbers_increase(self):
        first = bundle.package_info(self.template, self.lock, "146")
        second = bundle.package_info(self.template, self.lock, "147")
        self.assertLess(int(first["CFBundleVersion"]), int(second["CFBundleVersion"]))
        for invalid in ["", "0", "01", "-1", "1.2", "1beta", " 146", "146\n", True, 146]:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(bundle.BundleError, "build number"):
                bundle.package_info(self.template, self.lock, invalid)

    def test_missing_or_malformed_marketing_and_build_versions_are_rejected(self):
        for invalid in [None, 1, True, "", "0.1", "0.1.0-beta", "01.2.3", "1.2.3\n"]:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(bundle.BundleError, "marketing version"):
                bundle.package_info({**self.template, "CFBundleShortVersionString": invalid}, self.lock)
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            missing = dict(self.template)
            del missing[key]
            with self.subTest(missing=key), self.assertRaises(bundle.BundleError):
                bundle.package_info(missing, self.lock)

    def test_invalid_build_number_fails_before_asset_download_or_output_mutation(self):
        with patch.object(bundle, "require_macos", return_value=({}, {})), patch.object(
                bundle, "fetch_assets") as fetch, self.assertRaisesRegex(bundle.BundleError, "build number"):
            bundle.build(self.lock, build_number="not-a-build")
        fetch.assert_not_called()

    def test_cli_only_accepts_build_override_for_new_build(self):
        for command in ("inspect", "verify"):
            with self.subTest(command=command), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
                bundle.main([command, "--build-number", "146"])
            self.assertEqual(failure.exception.code, 2)

    def test_workflow_passes_its_run_number_not_an_account_or_attempt(self):
        workflow = (bundle.ROOT / ".github/workflows/macos-p0.yml").read_text(encoding="utf-8")
        self.assertIn("bundle.py build --development --build-number '${{ github.run_number }}'", workflow)
        self.assertNotIn("--build-number '${{ github.run_attempt }}'", workflow)


class ApplicationIconTests(ProjectDirectory):
    @staticmethod
    def icon_bytes():
        png = bundle.ICON_SOURCE.read_bytes()
        chunk = struct.pack(">4sI", b"ic08", len(png) + 8) + png
        return struct.pack(">4sI", b"icns", len(chunk) + 8) + chunk

    def test_template_keeps_worker_hidden_and_names_real_logo(self):
        info = plistlib.loads((bundle.ROOT / "macos/Resources/Info.plist").read_bytes())
        self.assertIs(info["LSUIElement"], True)
        self.assertEqual(info["CFBundleIconFile"], bundle.ICON_NAME)
        logo = bundle.ICON_SOURCE.read_bytes()
        self.assertEqual(logo[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", logo[16:24]), (256, 256))
        source = (bundle.ROOT / "macos/Sources/CCTranslateMac/Application.swift").read_text(encoding="utf-8")
        self.assertLess(source.index("ClipboardReadWorker.runIfRequested()"),
                        source.index("configureNormalApplication(application)"))
        self.assertIn("application.setActivationPolicy(.accessory)", source)
        self.assertIn("openProductWindows.isEmpty ? .accessory : .regular", source)
        self.assertIn("NSApp.setActivationPolicy(desiredActivationPolicy)", source)
        normal_setup = source.split("static func configureNormalApplication", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("openProduct", normal_setup)
        self.assertNotIn("activate(", normal_setup)
        workflow = (bundle.ROOT / ".github/workflows/macos-p0.yml").read_text(encoding="utf-8")
        self.assertIn("CC_TRANSLATE_DOCK_TEST_APP: ${{ github.workspace }}/tools/macos/.build/CCTranslateMac-P0.app",
                      workflow)

    def test_native_iconset_uses_existing_logo_all_sizes_and_cleans_staging(self):
        contents = self.root / "App.app/Contents"
        environment = {"synthetic": "environment"}
        rendered = []

        def native_tool(args, env):
            self.assertEqual(env, environment)
            if Path(args[0]).name == "sips":
                self.assertEqual(args[1], "--resampleHeightWidth")
                self.assertEqual(args[2], args[3])
                self.assertEqual(args[4], bundle.ICON_SOURCE)
                destination = Path(args[-1])
                destination.write_bytes(bundle.ICON_SOURCE.read_bytes())
                rendered.append((destination.name, args[2]))
            else:
                self.assertEqual(Path(args[0]).name, "iconutil")
                self.assertEqual(len(list(Path(args[-1]).glob("*.png"))), 10)
                Path(args[-2]).write_bytes(self.icon_bytes())
            return ""

        with patch.object(bundle, "BUILD", self.root), patch.object(bundle, "run", side_effect=native_tool):
            bundle.build_icon(contents, environment)
        self.assertEqual(rendered, [(f"icon_{points}x{points}" + ("@2x" if scale == 2 else "") + ".png",
                                    points * scale) for points in (16, 32, 128, 256, 512) for scale in (1, 2)])
        self.assertEqual((contents / "Resources" / bundle.ICON_NAME).read_bytes(), self.icon_bytes())
        self.assertFalse((self.root / "CCTranslate.iconset").exists())

    def test_native_tool_failure_is_not_silenced_and_staging_is_cleaned(self):
        error = subprocess.CalledProcessError(1, "sips")
        with patch.object(bundle, "BUILD", self.root), patch.object(bundle, "run", side_effect=error):
            with self.assertRaises(subprocess.CalledProcessError):
                bundle.build_icon(self.root / "App.app/Contents", {})
        self.assertFalse((self.root / "CCTranslate.iconset").exists())

    def test_missing_or_invalid_generated_icon_fails_build(self):
        for invalid in (None, b"not an icon", b"icns\x00\x00\x00\x11" + b"x" * 30):
            contents = self.root / "App.app/Contents"

            def invalid_tool(args, env):
                if Path(args[0]).name == "iconutil" and invalid is not None:
                    Path(args[-2]).write_bytes(invalid)
                return ""

            with self.subTest(invalid=invalid), patch.object(bundle, "BUILD", self.root), patch.object(
                    bundle, "run", side_effect=invalid_tool), self.assertRaises(bundle.BundleError):
                bundle.build_icon(contents, {})
            self.assertFalse((self.root / "CCTranslate.iconset").exists())


class AuthorSupportResourceTests(ProjectDirectory):
    def test_reuses_exact_windows_qr_image_without_reencoding(self):
        source = bundle.ROOT / "assets/support-author.png"
        self.assertEqual(bundle.SUPPORT_IMAGE_SOURCE, source)
        windows = (bundle.ROOT / "cc_core.py").read_text(encoding="utf-8")
        self.assertIn('SUPPORT_IMAGE_PATH = os.path.join(APP_DIR, "assets", "support-author.png")', windows)
        original = source.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         "73174e37515115d72d72c90985bb6dafe8d40f3d06dad3599614a94681160d4c")
        self.assertEqual(struct.unpack(">II", original[16:24]), (1574, 917))
        contents = self.root / "App.app/Contents"
        bundle.copy_support_image(contents)
        self.assertEqual((contents / "Resources/support-author.png").read_bytes(), original)

    def test_missing_and_linked_original_image_fail_before_output_creation(self):
        contents = self.root / "App.app/Contents"
        with patch.object(bundle, "SUPPORT_IMAGE_SOURCE", self.root / "missing.png"):
            with self.assertRaisesRegex(bundle.BundleError, "author support image missing or linked"):
                bundle.copy_support_image(contents)
        self.assertFalse(contents.exists())
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(bundle.BundleError, "author support image missing or linked"):
                bundle.copy_support_image(contents)
        self.assertFalse(contents.exists())


class ArchiveRulesTests(ProjectDirectory):
    def test_regular_paths_and_internal_links(self):
        records = [member("python/bin/python3.12"),
                   member("python/bin/python3", tarfile.SYMTYPE, "python3.12")]
        self.assertEqual(len(bundle.validate_members(records)), 2)

    def test_malicious_archive_paths(self):
        for path in ("", "../x", "/x", "C:/x", "python\\x", "python//x",
                     "python/./x", "python/../x", "python/a:stream", "python/a\nb",
                     "python/foo.", "python/foo ", "python/NUL.txt", "python/é"):
            with self.subTest(path=path), self.assertRaises(bundle.BundleError):
                bundle.archive_path(path)

    def test_duplicate_case_and_ancestor_aliases(self):
        for names in (("python/x", "python/x"), ("python/x", "python/X"),
                      ("python/a/one", "python/A/two"), ("python/x", "python/x/y")):
            with self.subTest(names=names), self.assertRaises(bundle.BundleError):
                bundle.validate_members([member(name) for name in names])

    def test_special_and_privileged_members(self):
        for kind in (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind), self.assertRaises(bundle.BundleError):
                bundle.validate_members([member("python/special", kind)])
        privileged = member("python/privileged")
        privileged.mode = 0o4755
        with self.assertRaises(bundle.BundleError):
            bundle.validate_members([privileged])

    def test_link_escapes_cycles_missing_targets_and_directory_targets(self):
        bad = [
            [member("python/link", tarfile.SYMTYPE, "/etc/passwd")],
            [member("python/link", tarfile.SYMTYPE, "../other")],
            [member("python/link", tarfile.SYMTYPE, "missing")],
            [member("python/link", tarfile.SYMTYPE, "link")],
            [member("python/a", tarfile.SYMTYPE, "b"), member("python/b", tarfile.SYMTYPE, "a")],
            [member("python/a", tarfile.LNKTYPE, "../../outside")],
            [member("python/a", tarfile.DIRTYPE), member("python/b", tarfile.SYMTYPE, "a")],
            [member("python/a", tarfile.SYMTYPE, "x"), member("python/x"),
             member("python/a/evil")],
        ]
        for records in bad:
            with self.subTest(names=[m.name for m in records]), self.assertRaises(bundle.BundleError):
                bundle.validate_members(records)

    def test_archive_size_limits(self):
        oversized = member("python/large")
        oversized.size = bundle.MAX_MEMBER_BYTES + 1
        with self.assertRaises(bundle.BundleError):
            bundle.validate_members([oversized])
        with patch.object(bundle, "MAX_MEMBERS", 1), self.assertRaises(bundle.BundleError):
            bundle.validate_members([member("python/a"), member("python/b")])
        with patch.object(bundle, "MAX_ARCHIVE_BYTES", 1), self.assertRaises(bundle.BundleError):
            bundle.validate_members([member("python/a"), member("python/b")])

    def test_wheel_symlinks_encryption_and_case_aliases_rejected(self):
        valid = zipfile.ZipInfo("certifi/cacert.pem")
        bundle.validate_wheel_members([valid])
        link = zipfile.ZipInfo("certifi/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        encrypted = zipfile.ZipInfo("certifi/private")
        encrypted.flag_bits = 1
        for infos in ([link], [encrypted], [valid, zipfile.ZipInfo("certifi/../outside")],
                      [valid, zipfile.ZipInfo("Certifi/other")],
                      [valid, zipfile.ZipInfo("certifi/cacert.pem")]):
            with self.subTest(paths=[info.filename for info in infos]), self.assertRaises(bundle.BundleError):
                bundle.validate_wheel_members(infos)

    def test_extract_rejects_all_headers_before_writing(self):
        archive = self.root / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as writer:
            for entry in [member("python/bin/python3"), member("../escape")]:
                writer.addfile(entry, io.BytesIO(b"x"))
        output = self.root / "runtime"
        with self.assertRaises(bundle.BundleError):
            bundle.extract_runtime(archive, output, bundle.load_lock())
        self.assertFalse(output.exists())
        self.assertFalse((self.root / "escape").exists())

    def test_stdlib_profile_excludes_unneeded_packages_and_bytecode(self):
        lock = bundle.load_lock()
        for path in ("python/bin/python3", "python/bin/python3.12",
                     "python/lib/libpython3.12.dylib", "python/lib/python3.12/ssl.py",
                     "python/lib/python3.12/lib-dynload/_sqlite3.cpython-312-darwin.so",
                     "python/lib/python3.12/LICENSE.txt"):
            with self.subTest(path=path):
                self.assertTrue(bundle.keep_runtime(path, lock))
        for path in ("python/lib/python3.12/", "python/bin/pip", "python/lib/tcl9.0/x",
                     "python/lib/python3.12/site-packages/pip/__init__.py",
                     "python/lib/python3.12/ensurepip/_bundled/pip.whl",
                     "python/lib/python3.12/tkinter/__init__.py",
                     "python/lib/python3.12/lib-dynload/_tkinter.cpython-312-darwin.so",
                     "python/lib/python3.12/__pycache__/ssl.pyc", "python/lib/python3.12/x.pyc"):
            with self.subTest(path=path):
                self.assertFalse(bundle.keep_runtime(path, lock))

    def test_hash_failure_is_closed_not_accepted_by_filename(self):
        path = self.root / "runtime"
        path.write_bytes(b"corrupt")
        asset = {"size": 7, "sha256": hashlib.sha256(b"correct").hexdigest()}
        with self.assertRaises(bundle.BundleError):
            bundle.verified_asset(path, asset)
        path.write_bytes(b"correct")
        bundle.verified_asset(path, asset)

    def test_offline_does_not_download(self):
        with patch.object(bundle, "STAGING", self.root), patch(
                "urllib.request.urlopen") as network, self.assertRaises(bundle.BundleError):
            bundle.fetch_assets(bundle.load_lock(), offline=True)
        network.assert_not_called()

    def test_copy_package_excludes_bytecode(self):
        source = self.root / "source"
        (source / "__pycache__").mkdir(parents=True)
        (source / "server.py").write_bytes(b"# synthetic test source\n")
        (source / "server.pyc").write_bytes(b"bytecode")
        (source / "__pycache__/server.pyc").write_bytes(b"bytecode")
        destination = self.root / "Core/cc_macos"
        bundle.copy_sources(source, destination)
        self.assertEqual([p.name for p in destination.iterdir()], ["server.py"])


class LicenseRulesTests(unittest.TestCase):
    def setUp(self):
        self.lock = bundle.load_lock()
        self.licenses = {"licenses/" + name: b"upstream test placeholder"
                         for name in self.lock["required_runtime_licenses"]}
        self.metadata = {
            "python_version": self.lock["python_version"],
            "target_triple": self.lock["python_target"], "apple_sdk_platform": "macosx",
            "apple_sdk_deployment_target": "11.0", "license_path": "licenses/LICENSE.cpython.txt",
            "build_info": {"core": {"links": []}, "extensions": {
                "_ssl": [{"license_paths": ["licenses/LICENSE.openssl-3.txt"],
                          "links": [{"name": "ssl", "path_static": "build/lib/libssl.a"}]}],
            }},
        }

    def test_complete_license_coverage_and_target(self):
        report = bundle.license_coverage(self.metadata, self.licenses, self.lock)
        self.assertIn("licenses/LICENSE.openssl-3.txt", report["required"])

    def test_missing_actual_dependency_license_is_fatal(self):
        for name in self.lock["required_runtime_licenses"]:
            licenses = self.licenses.copy()
            del licenses["licenses/" + name]
            with self.subTest(name=name), self.assertRaises(bundle.BundleError):
                bundle.license_coverage(self.metadata, licenses, self.lock)

    def test_license_txt_alone_does_not_satisfy_dependencies(self):
        with self.assertRaises(bundle.BundleError):
            bundle.license_coverage(
                self.metadata, {"licenses/LICENSE.cpython.txt": b"upstream CPython"}, self.lock)

    def test_bundled_library_without_coverage_is_rejected(self):
        self.metadata["build_info"]["extensions"]["_ssl"][0]["license_paths"] = []
        with self.assertRaises(bundle.BundleError):
            bundle.license_coverage(self.metadata, self.licenses, self.lock)

    def test_zlib_ng_exception_only_for_proven_system_zlib(self):
        node = {"license_paths": ["licenses/LICENSE.zlib-ng.txt", "licenses/LICENSE.zlib.txt"],
                "links": [{"name": "z", "system": True}]}
        self.metadata["build_info"]["extensions"]["zlib"] = [node]
        report = bundle.license_coverage(self.metadata, self.licenses, self.lock)
        self.assertEqual(report["not_applicable"][0]["extension"], "zlib")
        for link in ({"name": "z", "path_static": "build/libz.a"},
                     {"name": "zlib-ng", "system": True},
                     {"name": "z", "system": True, "path_static": "build/libz.a"}):
            node["links"] = [link]
            with self.subTest(link=link), self.assertRaises(bundle.BundleError):
                bundle.license_coverage(self.metadata, self.licenses, self.lock)

    def test_runtime_platform_version_and_arch_must_match(self):
        for key, value in (("python_version", "3.12.0"), ("target_triple", "x86_64-apple-darwin"),
                           ("apple_sdk_platform", "iphoneos"), ("apple_sdk_deployment_target", "15.0")):
            metadata = deepcopy(self.metadata)
            metadata[key] = value
            with self.subTest(key=key), self.assertRaises(bundle.BundleError):
                bundle.license_coverage(metadata, self.licenses, self.lock)

    def test_assets_are_fixed_and_certificate_license_is_separate(self):
        for asset in self.lock["assets"].values():
            self.assertNotIn("latest", asset["url"])
            self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("full_build", self.lock["assets"])
        self.assertIn("mpl", self.lock["assets"])
        self.assertEqual(self.lock["certificate_member"], "certifi/cacert.pem")


class SparklePackagingTests(ProjectDirectory):
    def archive(self, extra=(), omitted=()):
        path = self.root / "sparkle.zip"
        prefix = bundle.SPARKLE_ARCHIVE_ROOT + "/"
        entries = [(name, b"synthetic Mach-O", stat.S_IFREG | 0o755)
                   for name in (*bundle.SPARKLE_HELPERS, "Versions/B/Sparkle")]
        entries += [
            ("Versions/B/Resources/Info.plist", plistlib.dumps({
                "CFBundleIdentifier": "org.sparkle-project.Sparkle",
                "CFBundleShortVersionString": "2.10.0",
            }), stat.S_IFREG | 0o644),
            ("Versions/Current", b"B", stat.S_IFLNK | 0o755),
            ("Resources", b"Versions/Current/Resources", stat.S_IFLNK | 0o755),
        ]
        with zipfile.ZipFile(path, "w") as archive:
            for name, data, mode in (*entries, *extra):
                if name in omitted:
                    continue
                info = zipfile.ZipInfo(prefix + name)
                info.create_system = 3
                info.external_attr = mode << 16
                archive.writestr(info, data)
            archive.writestr("LICENSE", b"Complete synthetic license fixture.")
        lock = deepcopy(bundle.load_lock())
        lock["assets"]["sparkle"].update(size=path.stat().st_size, sha256=bundle.digest(path))
        return path, lock

    def test_archive_records_framework_helpers_links_modes_and_complete_license(self):
        path, lock = self.archive()
        metadata, license_text = bundle.sparkle_archive(path, lock)
        self.assertEqual(metadata["archive_sha256"], bundle.digest(path))
        self.assertEqual(metadata["version"], "2.10.0")
        self.assertEqual(metadata["files"]["Versions/Current"], {"symlink": "B"})
        self.assertTrue(metadata["files"]["Versions/B/Sparkle"]["executable"])
        self.assertFalse(metadata["files"]["Versions/B/Resources/Info.plist"]["executable"])
        self.assertEqual(metadata["license_sha256"], hashlib.sha256(license_text).hexdigest())

    def test_archive_rejects_changed_pinned_bytes(self):
        path, lock = self.archive()
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(bundle.BundleError, "asset size mismatch"):
            bundle.sparkle_archive(path, lock)

    def test_archive_rejects_missing_nested_helper(self):
        path, lock = self.archive(omitted=(bundle.SPARKLE_HELPERS[-1],))
        with self.assertRaisesRegex(bundle.BundleError, "executable inventory"):
            bundle.sparkle_archive(path, lock)

    def test_archive_rejects_links_outside_framework_and_traversing_entries(self):
        for name, target, kind in (
                ("Escape", b"../dSYMs", stat.S_IFLNK),
                ("Versions/B/../Escape", b"fixture", stat.S_IFREG)):
            with self.subTest(name=name):
                path, lock = self.archive(extra=((name, target, kind | 0o644),))
                with self.assertRaises(bundle.BundleError):
                    bundle.sparkle_archive(path, lock)

    def test_archive_rejects_version_drift(self):
        path, lock = self.archive()
        lock["sparkle_version"] = "2.9.0"
        with self.assertRaisesRegex(bundle.BundleError, "version mismatch"):
            bundle.sparkle_archive(path, lock)

    def test_embedding_preserves_vendor_bytes_and_does_not_run_or_sign_anything(self):
        artifacts = self.root / "macos/.build/artifacts/sparkle/Sparkle"
        framework = artifacts / bundle.SPARKLE_ARCHIVE_ROOT
        framework.mkdir(parents=True)
        (framework / "Sparkle").write_bytes(b"synthetic library")
        license_text = b"complete synthetic vendor license"
        metadata = {"files": bundle.framework_inventory(framework)}
        contents = self.root / "Output.app/Contents"
        with patch.object(bundle, "ROOT", self.root), patch.object(bundle, "run") as run:
            bundle.embed_sparkle(contents, metadata, license_text)
        self.assertEqual(bundle.framework_inventory(contents / "Frameworks/Sparkle.framework"),
                         metadata["files"])
        self.assertEqual((contents / "Resources/Licenses/Sparkle/LICENSE").read_bytes(), license_text)
        run.assert_not_called()

    def test_embedding_rejects_missing_or_changed_swiftpm_artifact_before_output(self):
        contents = self.root / "Output.app/Contents"
        with patch.object(bundle, "ROOT", self.root):
            with self.assertRaisesRegex(bundle.BundleError, "missing or ambiguous"):
                bundle.embed_sparkle(contents, {"files": {}}, b"license")
            framework = self.root / "macos/.build/artifacts/sparkle/Sparkle.framework"
            framework.mkdir(parents=True)
            (framework / "Sparkle").write_bytes(b"changed artifact")
            with self.assertRaisesRegex(bundle.BundleError, "differs from"):
                bundle.embed_sparkle(contents, {"files": {}}, b"license")
        self.assertFalse(contents.exists())

    def test_package_and_archive_pins_select_same_vendor_without_app_feed(self):
        lock = bundle.load_lock()
        package = (bundle.ROOT / "macos/Package.swift").read_text(encoding="utf-8")
        pins = json.loads((bundle.ROOT / "macos/Package.resolved").read_bytes())["pins"]
        self.assertIn('exact: "' + lock["sparkle_version"] + '"', package)
        self.assertIn('@executable_path/../Frameworks', package)
        self.assertEqual(pins[0]["state"]["version"], lock["sparkle_version"])
        self.assertEqual(pins[0]["state"]["revision"], "eef1a539a373c1f1a320624b1130fc5de7b2e100")
        info = plistlib.loads((bundle.ROOT / "macos/Resources/Info.plist").read_bytes())
        self.assertNotIn("SUFeedURL", info)
        self.assertNotIn("SUPublicEDKey", info)


class MachORulesTests(ProjectDirectory):
    def setUp(self):
        super().setUp()
        system = patch.object(bundle, "system_library_exists", return_value=True)
        system.start()
        self.addCleanup(system.stop)

    def synthetic_app(self):
        app = self.root / "Synthetic.app"
        contents = app / "Contents"
        lock = bundle.load_lock()
        info = {"CFBundleIdentifier": lock["bundle_identifier"],
                "CFBundleExecutable": "CCTranslateMac", "CFBundlePackageType": "APPL",
                "CFBundleIconFile": bundle.ICON_NAME,
                "LSUIElement": True, "LSMinimumSystemVersion": "14.0",
                "CFBundleShortVersionString": "0.1.0", "CFBundleVersion": "42"}
        binaries = ["MacOS/CCTranslateMac", "Resources/python/bin/python3",
                    "Resources/python/lib/libpython3.12.dylib",
                    "Resources/python/lib/libCCProcessSupport.dylib"]
        binaries += ["Frameworks/Sparkle.framework/" + path
                     for path in (*bundle.SPARKLE_HELPERS, "Versions/B/Sparkle")]
        resources = ["Resources/" + bundle.ICON_NAME, "Resources/" + bundle.SUPPORT_IMAGE_NAME,
                     "Resources/Core/launch.py", "Resources/Core/cc_macos/__main__.py",
                     "Resources/Core/cc_macos/dictionary_probe.py",
                     "Resources/Core/cc_macos/dictionary.py",
                     "Resources/Core/cc_macos/config_fixture.py",
                     "Resources/Core/cc_macos/catalog_fixture.py",
                     "Resources/Core/cc_macos/catalog_process_fixture.py",
                     "Resources/Core/cc_macos/storage_fixture.py",
                     "Resources/Core/cc_macos/history_owner.py",
                     "Resources/Core/cc_macos/history_fixture.py",
                     "Resources/Core/cc_macos/file_owner.py",
                     "Resources/Core/cc_macos/config_owner.py",
                     "Resources/Core/cc_macos/config_store_fixture.py",
                     "Resources/Core/cc_macos/configuration.py",
                     "Resources/Core/cc_macos/history.py",
                     "Resources/Core/cc_macos/image.py",
                     "Resources/Core/cc_macos/image_fixture.py",
                     "Resources/Core/cacert.pem", "Resources/Licenses/certifi/LICENSE",
                     "Resources/Licenses/certifi/MPL-2.0.txt", "Resources/Licenses/Python/PYTHON.json",
                     "Resources/Licenses/Sparkle/LICENSE"]
        resources += ["Resources/Licenses/Python/licenses/" + name
                      for name in lock["required_runtime_licenses"]]
        resources += ["Resources/Core/" + name for name in SHARED_CORE_FILES]
        resources += ["Resources/Core/cc_providers/" + name for name in PROVIDER_FILES]
        for name in binaries + resources:
            path = contents / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\xcf\xfa\xed\xfeSYNTHETIC" if name in binaries else b"synthetic fixture")
            path.chmod(0o755 if name in binaries else 0o644)
        (contents / "Resources" / bundle.ICON_NAME).write_bytes(ApplicationIconTests.icon_bytes())
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        bundle.write_json(contents / "Resources/source-manifest.json", {
            "lock": lock, "certificate_sha256": bundle.digest(contents / "Resources/Core/cacert.pem"),
            "application": bundle.application_metadata(info, lock),
            "sparkle": {
                "version": lock["sparkle_version"],
                "archive_sha256": lock["assets"]["sparkle"]["sha256"],
                "files": bundle.framework_inventory(contents / "Frameworks/Sparkle.framework"),
                "license_sha256": bundle.digest(contents / "Resources/Licenses/Sparkle/LICENSE"),
            },
            "resource_hashes": {name: bundle.digest(contents / name) for name in resources},
        })
        return app

    def test_author_support_image_is_required_and_covered_by_bundle_inventory(self):
        for fault in ("missing", "unrecorded", "changed"):
            with self.subTest(fault=fault):
                app = self.synthetic_app()
                image = app / "Contents/Resources" / bundle.SUPPORT_IMAGE_NAME
                manifest = app / "Contents/Resources/source-manifest.json"
                if fault == "missing":
                    image.unlink()
                elif fault == "changed":
                    image.write_bytes(b"not the recorded image")
                else:
                    metadata = json.loads(manifest.read_bytes())
                    del metadata["resource_hashes"]["Resources/" + bundle.SUPPORT_IMAGE_NAME]
                    bundle.write_json(manifest, metadata)
                with patch.object(bundle, "run", side_effect=self.fake_apple_tool):
                    with self.assertRaises(bundle.BundleError):
                        bundle.audit_bundle(app, bundle.load_lock())
                shutil.rmtree(app)

    @staticmethod
    def fake_apple_tool(args, environment=None):
        tool = Path(args[0]).name
        binary = Path(args[-1])
        if tool == "codesign":
            return ""
        if tool == "file":
            return "Mach-O 64-bit arm64 (synthetic test fixture)"
        if tool == "lipo":
            return "x86_64 arm64" if "Sparkle.framework" in binary.parts else "arm64"
        if args[1] == "-l":
            minimum = "14.0" if binary.name in ("CCTranslateMac", "libCCProcessSupport.dylib") else "11.0"
            result = f"Load command 0\n cmd LC_BUILD_VERSION\n platform 1\n minos {minimum}\n"
            if binary.name == "python3":
                result += "Load command 1\n cmd LC_RPATH\n path @loader_path/../lib (offset 12)\n"
            elif binary.suffix == ".dylib":
                result += f"Load command 1\n cmd LC_ID_DYLIB\n name @rpath/{binary.name} (offset 24)\n"
            return result
        return "file:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1351.0.0)\n"

    def test_audit_invokes_all_apple_tools_for_each_macho(self):
        app = self.synthetic_app()
        with patch.object(bundle, "run", side_effect=self.fake_apple_tool) as tools:
            report = bundle.audit_bundle(app, bundle.load_lock())
        self.assertEqual(len(report["checks"]), 9)
        self.assertEqual(tools.call_count, 37)
        self.assertEqual(report["release_gate"], "NOT PASSED")

    def test_audit_rejects_application_version_changed_after_manifest_creation(self):
        app = self.synthetic_app()
        path = app / "Contents/Info.plist"
        info = plistlib.loads(path.read_bytes())
        info["CFBundleVersion"] = "43"
        path.write_bytes(plistlib.dumps(info))
        with patch.object(bundle, "run") as tools, self.assertRaisesRegex(
                bundle.BundleError, "application version/identity"):
            bundle.audit_bundle(app, bundle.load_lock())
        tools.assert_not_called()

    def test_audit_requires_icon_resource_and_checks_its_manifest_hash(self):
        app = self.synthetic_app()
        icon = app / "Contents/Resources" / bundle.ICON_NAME
        original = icon.read_bytes()
        icon.unlink()
        with patch.object(bundle, "run") as tools, self.assertRaisesRegex(bundle.BundleError, "missing bundle"):
            bundle.audit_bundle(app, bundle.load_lock())
        tools.assert_not_called()
        icon.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with patch.object(bundle, "run") as tools, self.assertRaisesRegex(bundle.BundleError, "source/license content changed"):
            bundle.audit_bundle(app, bundle.load_lock())
        tools.assert_not_called()

    def test_sparkle_modification_is_rejected_before_platform_tools(self):
        app = self.synthetic_app()
        (app / "Contents/Frameworks/Sparkle.framework/Versions/B/Sparkle").write_bytes(b"changed")
        with patch.object(bundle, "run") as tools, self.assertRaisesRegex(
                bundle.BundleError, "framework content changed"):
            bundle.audit_bundle(app, bundle.load_lock())
        tools.assert_not_called()

    def test_sparkle_license_modification_is_rejected_before_platform_tools(self):
        app = self.synthetic_app()
        (app / "Contents/Resources/Licenses/Sparkle/LICENSE").write_bytes(b"shortened")
        with patch.object(bundle, "run") as tools, self.assertRaisesRegex(
                bundle.BundleError, "Sparkle license changed"):
            bundle.audit_bundle(app, bundle.load_lock())
        tools.assert_not_called()

    def test_sparkle_uses_arm_slice_and_readonly_signature_verification(self):
        app = self.synthetic_app()
        with patch.object(bundle, "run", side_effect=self.fake_apple_tool) as tools:
            report = bundle.audit_bundle(app, bundle.load_lock())
        for call in tools.call_args_list:
            args = call.args[0]
            if str(args[-1]).endswith("Sparkle.framework"):
                self.assertEqual(args[1:-1], ["--verify", "--strict", "--deep"])
            elif "Sparkle.framework" in str(args[-1]) and Path(args[0]).name == "otool":
                self.assertEqual(args[2:4], ["-arch", "arm64"])
        vendor = [item for item in report["checks"] if "Sparkle.framework" in item["path"]]
        self.assertEqual(len(vendor), 5)
        self.assertTrue(all(set(item["contained_architectures"]) == {"arm64", "x86_64"} for item in vendor))

    def test_sparkle_helpers_resolve_executable_paths_from_their_own_location(self):
        app = self.synthetic_app()
        helper = app / "Contents/Frameworks/Sparkle.framework" / bundle.SPARKLE_HELPERS[1]

        def tools(args, environment=None):
            result = self.fake_apple_tool(args, environment)
            if Path(args[-1]) == helper and args[1] == "-l":
                result += "Load command 1\n cmd LC_RPATH\n path @executable_path (offset 12)\n"
            if Path(args[-1]) == helper and args[1] == "-L":
                result += "\t@rpath/Updater (compatibility version 1.0.0, current version 1.0.0)\n"
            return result

        with patch.object(bundle, "run", side_effect=tools):
            bundle.audit_bundle(app, bundle.load_lock())

    def test_vendor_exception_does_not_allow_universal_application_binary(self):
        app = self.synthetic_app()

        def tools(args, environment=None):
            if Path(args[0]).name == "lipo" and Path(args[-1]).name == "CCTranslateMac":
                return "x86_64 arm64"
            return self.fake_apple_tool(args, environment)

        with patch.object(bundle, "run", side_effect=tools), self.assertRaisesRegex(
                bundle.BundleError, "unexpected binary architectures"):
            bundle.audit_bundle(app, bundle.load_lock())

    def test_audit_checks_unreferenced_macho_and_rejects_newer_os(self):
        app = self.synthetic_app()
        extra = app / "Contents/Resources/python/lib/extra.dylib"
        extra.write_bytes(b"\xcf\xfa\xed\xfeSYNTHETIC")

        def wrong_arch(args, environment=None):
            if Path(args[0]).name == "lipo" and Path(args[-1]).name == "extra.dylib":
                return "x86_64"
            return self.fake_apple_tool(args, environment)

        with patch.object(bundle, "run", side_effect=wrong_arch), self.assertRaisesRegex(
                bundle.BundleError, "non-arm64"):
            bundle.audit_bundle(app, bundle.load_lock())
        extra.unlink()

        def too_new(args, environment=None):
            return self.fake_apple_tool(args, environment).replace("minos 14.0", "minos 15.0")

        with patch.object(bundle, "run", side_effect=too_new), self.assertRaisesRegex(
                bundle.BundleError, "newer macOS"):
            bundle.audit_bundle(app, bundle.load_lock())

    def test_audit_rejects_modified_full_license(self):
        app = self.synthetic_app()
        license_path = app / "Contents/Resources/Licenses/Python/licenses/LICENSE.openssl-3.txt"
        license_path.write_bytes(b"truncated synthetic license")
        with self.assertRaisesRegex(bundle.BundleError, "source/license content changed"):
            bundle.audit_bundle(app, bundle.load_lock())

    def test_audit_requires_each_shared_core_module(self):
        app = self.synthetic_app()
        for name in SHARED_CORE_FILES:
            with self.subTest(name=name):
                path = app / "Contents/Resources/Core" / name
                path.unlink()
                with self.assertRaisesRegex(bundle.BundleError, "missing bundle resources"):
                    bundle.audit_bundle(app, bundle.load_lock())
                path.write_bytes(b"synthetic fixture")

    def test_audit_rejects_modified_shared_core_modules(self):
        app = self.synthetic_app()
        for name in SHARED_CORE_FILES:
            with self.subTest(name=name):
                path = app / "Contents/Resources/Core" / name
                path.write_bytes(b"modified synthetic module")
                with self.assertRaisesRegex(bundle.BundleError, "source/license content changed"):
                    bundle.audit_bundle(app, bundle.load_lock())
                path.write_bytes(b"synthetic fixture")

    def test_audit_requires_unchanged_provider_contracts(self):
        app = self.synthetic_app()
        for name in PROVIDER_FILES:
            with self.subTest(name=name):
                path = app / "Contents/Resources/Core/cc_providers" / name
                path.unlink()
                with self.assertRaisesRegex(bundle.BundleError, "missing bundle resources"):
                    bundle.audit_bundle(app, bundle.load_lock())
                path.write_bytes(b"modified synthetic contract")
                with self.assertRaisesRegex(bundle.BundleError, "source/license content changed"):
                    bundle.audit_bundle(app, bundle.load_lock())
                path.write_bytes(b"synthetic fixture")

    def test_audit_requires_unchanged_diagnostic_probes(self):
        app = self.synthetic_app()
        for name in ("dictionary_probe.py", "storage_fixture.py", "history_owner.py", "history_fixture.py",
                     "file_owner.py", "config_owner.py", "config_store_fixture.py", "configuration.py", "history.py"):
            with self.subTest(name=name):
                path = app / "Contents/Resources/Core/cc_macos" / name
                path.unlink()
                with self.assertRaisesRegex(bundle.BundleError, "missing bundle resources"):
                    bundle.audit_bundle(app, bundle.load_lock())
                path.write_bytes(b"modified synthetic probe")
                with self.assertRaisesRegex(bundle.BundleError, "source/license content changed"):
                    bundle.audit_bundle(app, bundle.load_lock())
                path.write_bytes(b"synthetic fixture")

    def test_load_commands_macos_build_and_rpath(self):
        output = """file:
Load command 0
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 1
    minos 11.0
      sdk 15.5
Load command 1
      cmd LC_RPATH
  cmdsize 40
     path @loader_path/../lib (offset 12)
Load command 2
      cmd LC_ID_DYLIB
     name @rpath/libpython3.12.dylib (offset 24)
"""
        result = bundle.parse_load_commands(output)
        self.assertEqual(result["minimums"], ["11.0"])
        self.assertEqual(result["rpaths"], ["@loader_path/../lib"])
        self.assertEqual(result["id"], "@rpath/libpython3.12.dylib")

    def test_old_minimum_macos_command_supported(self):
        commands = bundle.parse_load_commands(
            "Load command 0\n cmd LC_VERSION_MIN_MACOSX\n version 11.0\n sdk 15.5\n")
        self.assertEqual(commands["minimums"], ["11.0"])

    def test_invalid_platform_or_missing_minimum_rejected(self):
        for output in ("Load command 0\n cmd LC_BUILD_VERSION\n platform 2\n minos 14.0\n",
                       "Load command 0\n cmd LC_VERSION_MIN_IPHONEOS\n version 14.0\n",
                       "Load command 0\n cmd LC_BUILD_VERSION\n platform 1\n",
                       "Load command 0\n cmd LC_UUID\n"):
            with self.subTest(output=output), self.assertRaises(bundle.BundleError):
                bundle.parse_load_commands(output)

    def test_dependency_parser_and_version_comparison(self):
        dependencies = bundle.parse_dependencies(
            "file:\n\t@rpath/libpython3.12.dylib (compatibility version 3.12.0, current version 3.12.0)\n"
            "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1351.0.0)\n")
        self.assertEqual(len(dependencies), 2)
        self.assertLess(bundle.version("11.0"), bundle.version("14.0"))
        self.assertEqual(bundle.version("14"), bundle.version("14.0.0"))
        for output in ("file:\n malformed", "file:\n\tbad dylib"):
            with self.assertRaises(bundle.BundleError):
                bundle.parse_dependencies(output)

    def test_bundle_rpath_resolution_and_external_rejection(self):
        app = self.root / "Probe.app"
        binary = app / "Contents/Resources/python/bin/python3.12"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"synthetic")
        library = app / "Contents/Resources/python/lib/libpython3.12.dylib"
        library.parent.mkdir()
        library.write_bytes(b"synthetic")
        base = bundle.expand_dyld("@loader_path/../lib", binary, binary, app)
        self.assertEqual(bundle.resolve_dependency(
            "@rpath/libpython3.12.dylib", binary, binary, app, [base]), library.resolve())
        self.assertEqual(bundle.resolve_dependency(
            "/usr/lib/libSystem.B.dylib", binary, binary, app, []), "/usr/lib/libSystem.B.dylib")
        for path in ("/opt/homebrew/lib/libssl.dylib", "/usr/local/lib/libssl.dylib",
                     "/Users/example/lib/libx.dylib", "relative/libx.dylib",
                     "@loader_path/../../../../../../outside", "/usr/lib/../../outside"):
            with self.subTest(path=path), self.assertRaises(bundle.BundleError):
                bundle.expand_dyld(path, binary, binary, app)
        with self.assertRaises(bundle.BundleError):
            bundle.resolve_dependency("@rpath/missing.dylib", binary, binary, app, [base])
        with self.assertRaises(bundle.BundleError):
            bundle.resolve_dependency("@rpath/../outside", binary, binary, app, [base])
        with patch.object(bundle, "system_library_exists", return_value=False):
            with self.assertRaisesRegex(bundle.BundleError, "unresolved"):
                bundle.resolve_dependency("@rpath/missing.dylib", binary, binary, app, ["/usr/lib/swift"])
            with self.assertRaisesRegex(bundle.BundleError, "missing system"):
                bundle.resolve_dependency("/usr/lib/missing.dylib", binary, binary, app, [])

    def test_info_plist_requires_development_identity(self):
        lock = bundle.load_lock()
        bundle.validate_plist(plistlib.loads(
            (bundle.ROOT / "macos/Resources/Info.plist").read_bytes()), lock)
        valid = {"CFBundleIdentifier": "dev.cc-translate.macos.probe",
                 "CFBundleExecutable": "CCTranslateMac", "CFBundlePackageType": "APPL",
                 "CFBundleIconFile": bundle.ICON_NAME,
                 "LSUIElement": True, "LSMinimumSystemVersion": "14.0",
                 "CFBundleShortVersionString": "0.1.0", "CFBundleVersion": "42"}
        bundle.validate_plist(plistlib.loads(plistlib.dumps(valid)), lock)
        for key, value in (("CFBundleIdentifier", "production"), ("LSUIElement", 1),
                           ("CFBundleIconFile", None), ("CFBundleIconFile", "../placeholder.icns"),
                           ("CFBundleExecutable", "python"), ("LSMinimumSystemVersion", "15.0")):
            with self.subTest(key=key), self.assertRaises(bundle.BundleError):
                bundle.validate_plist({**valid, key: value}, lock)

    def test_windows_build_is_explicitly_blocked_before_downloading(self):
        with patch.object(bundle.sys, "platform", "win32"), patch.object(
                bundle, "fetch_assets") as fetch:
            with self.assertRaisesRegex(bundle.BundleError, "arm64 Mac"):
                bundle.build(bundle.load_lock())
            fetch.assert_not_called()


class SmokeContractTests(unittest.TestCase):
    @staticmethod
    def event(**updates):
        frame = {"v": 1, "id": "probe", "type": "completed", "payload": {}, "seq": 0}
        frame.update(updates)
        return json.dumps(frame).encode() + b"\n"

    def test_valid_frame(self):
        self.assertEqual(smoke.decode_event(self.event())["seq"], 0)
        self.assertEqual(smoke.decode_event(self.event(type="started"))["type"], "started")

    def test_ready_matches_exact_p0_contract(self):
        ready = {"protocol": 1, "capabilities": ["fixture", "runtime_probe"],
                 "max_frame_bytes": 65536, "fixture": True}
        smoke.validate_ready(ready)
        for changes in ({"protocol": True}, {"extra": True},
                        {"capabilities": ["fixture", "runtime_probe", "translation"]},
                        {"fixture": False}, {"max_frame_bytes": 65537}, {"max_frame_bytes": 65536.0}):
            with self.subTest(changes=changes), self.assertRaises(smoke.BundleError):
                smoke.validate_ready({**ready, **changes})

    def test_malformed_frames(self):
        for raw in (b"", b"{}\n", self.event()[:-1], b"\xef\xbb\xbf" + self.event(),
                    b"x" * 65536 + b"\n", b"\xff\n",
                    b'{"v":1,"v":1,"id":"x","type":"ready","payload":{},"seq":0}\n',
                    self.event(v=True), self.event(seq=True), self.event(seq=-1),
                    self.event(id="../bad"), self.event(payload=[]), self.event(type="unknown"),
                    self.event(type=[]),
                    self.event(payload={"number": float("inf")}),
                    self.event(payload={"text": "\ud800"}),
                    self.event(payload={"nested": [[[[[[[[[[[[[[[[{}]]]]]]]]]]]]]]]]}),
                    self.event(extra="unknown")):
            with self.subTest(raw=raw[:80]), self.assertRaises(smoke.BundleError):
                smoke.decode_event(raw)

    def test_unknown_ids_duplicate_terminal_and_reordered_sequence_rejected(self):
        for event in (
            {"v": 1, "id": "unknown", "seq": 0, "type": "completed", "payload": {}},
            {"v": 1, "id": "probe", "seq": 1, "type": "accepted", "payload": {}},
        ):
            session = object.__new__(smoke.Session)
            session.events = queue.Queue()
            session.errors = []
            session.sequences = {"probe": 0}
            session.terminals = set()
            session.stdout_done = threading.Event()
            session.events.put(event)
            with self.assertRaises(smoke.BundleError):
                session.receive()
        session.events.put({"v": 1, "id": "probe", "seq": 0, "type": "completed", "payload": {}})
        session.receive()
        session.events.put({"v": 1, "id": "probe", "seq": 1, "type": "completed", "payload": {}})
        with self.assertRaises(smoke.BundleError):
            session.receive()

    def test_probe_requires_real_bundle_sqlite_ssl_and_https_evidence(self):
        lock = bundle.load_lock()
        report = {
            "python": {"version": lock["python_version"], "platform": "darwin", "machine": "arm64",
                       "isolated": True, "bytecode_disabled": True, "bundle_runtime": True},
            "sqlite": {"status": "passed", "read_write": True},
            "dictionary": {"status": "passed", "read_only": True, "sources_preserved": True, "reopened": True},
            "codex_config_fixture": {"status": "passed", "fixture": True,
                                     "methods_verified": True, "routing_preserved": True},
            "catalog_storage_fixture": {"status": "passed", "cli_simulated": True,
                                        "cache_verified": True, "reopen_verified": True},
            "catalog_process_fixture": {"status": "passed", "fixture": True, "process_verified": True,
                                        "cache_verified": True, "reopen_verified": True},
            "ssl": {"status": "passed", "certificate_validation": True, "ca_source": "bundle"},
            "https": {"status": "passed", "certificate_verified": True, "host": "www.python.org"},
        }
        smoke.validate_runtime(report, lock)
        for value in (None, {}, [], "passed"):
            invalid = deepcopy(report)
            invalid["dictionary"] = value
            with self.subTest(dictionary=value), self.assertRaises(smoke.BundleError):
                smoke.validate_runtime(invalid, lock)
        for section, key, value in (("python", "version", "3.12.10"),
                                    ("python", "platform", "win32"),
                                    ("python", "platform", "linux"),
                                    ("python", "machine", "x86_64"),
                                    ("python", "isolated", False),
                                    ("python", "isolated", 1),
                                    ("python", "bytecode_disabled", False),
                                    ("python", "bytecode_disabled", 1),
                                    ("python", "bundle_runtime", False),
                                    ("python", "bundle_runtime", 1),
                                    ("sqlite", "read_write", False),
                                    ("dictionary", "status", "not_run"),
                                    ("dictionary", "read_only", False),
                                    ("dictionary", "read_only", 1),
                                    ("dictionary", "sources_preserved", False),
                                    ("dictionary", "reopened", False),
                                    ("dictionary", "path", "synthetic forbidden path"),
                                    ("codex_config_fixture", "status", "not_run"),
                                    ("codex_config_fixture", "fixture", 1),
                                    ("codex_config_fixture", "methods_verified", False),
                                    ("codex_config_fixture", "routing_preserved", False),
                                    ("codex_config_fixture", "path", "synthetic forbidden path"),
                                    ("catalog_storage_fixture", "status", "not_run"),
                                    ("catalog_storage_fixture", "cli_simulated", 1),
                                    ("catalog_storage_fixture", "cache_verified", False),
                                    ("catalog_storage_fixture", "reopen_verified", False),
                                    ("catalog_storage_fixture", "path", "synthetic forbidden path"),
                                    ("catalog_process_fixture", "status", "not_run"),
                                    ("catalog_process_fixture", "fixture", 1),
                                    ("catalog_process_fixture", "process_verified", False),
                                    ("catalog_process_fixture", "cache_verified", False),
                                    ("catalog_process_fixture", "reopen_verified", 1),
                                    ("catalog_process_fixture", "path", "synthetic forbidden path"),
                                    ("ssl", "ca_source", "system"),
                                    ("ssl", "certificate_validation", False),
                                    ("https", "status", "not_run"),
                                    ("https", "certificate_verified", False)):
            invalid = deepcopy(report)
            invalid[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(smoke.BundleError):
                smoke.validate_runtime(invalid, lock)
        for key in report["python"]:
            invalid = deepcopy(report)
            del invalid["python"][key]
            with self.subTest(missing_python_field=key), self.assertRaises(smoke.BundleError):
                smoke.validate_runtime(invalid, lock)

    def test_workflow_is_development_branch_only_readonly_arm64_and_pinned(self):
        workflow = (bundle.ROOT / ".github/workflows/macos-p0.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertIn("  push:\n    branches:\n      - agents/cc-translate-macos-native\n", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s*(pull_request|schedule|workflow_run):")
        self.assertNotIn("branches: [master", workflow)
        self.assertIn("  contents: read", workflow)
        self.assertIn("runs-on: macos-15\n", workflow)
        self.assertIn('test "$(uname -m)" = arm64', workflow)
        self.assertIn("CC_TRANSLATE_APP: ${{ github.workspace }}/tools/macos/.build/CCTranslateMac-P0.app", workflow)
        self.assertIn("--filter HelperIntegrationTests", workflow)
        self.assertIn("/Applications/Xcode_16.4.app/Contents/Developer", workflow)
        self.assertIn('test -d "$DEVELOPER_DIR"', workflow)
        self.assertNotRegex(workflow, r"(?m)^\s*uses: .*@(main|master|v\d+)\s*$")
        self.assertNotIn("codesign", workflow)
        self.assertNotIn("notarytool", workflow)
        self.assertNotIn('CC_TRANSLATE_DICTIONARY_TEST_ASSET: ${{ runner.temp }}', workflow)
        self.assertEqual(workflow.count('fixture="$RUNNER_TEMP/cc-translate-dictionary-fixture.sqlite3"'), 2)
        self.assertEqual(workflow.count('echo "CC_TRANSLATE_DICTIONARY_TEST_ASSET=$fixture" >> "$GITHUB_ENV"'), 2)
        self.assertEqual(workflow.count('--download-to "$fixture" --allow-download'), 2)
        self.assertEqual(workflow.count("--filter DictionaryProductIntegrationTests"), 1)
        self.assertIn("dictionary-product-tests.json", workflow)
        native = workflow.index("      - name: Native Swift tests")
        native_log = workflow.index("      - name: Retain original native unit log", native)
        native_end = workflow.index("      - name: Retain native view renders", native)
        native_body = workflow[native:native_log]
        self.assertIn("        shell: bash\n", native_body)
        self.assertIn("          set -euo pipefail\n", native_body)
        self.assertEqual(native_body.count(" | tee "), 4)
        self.assertEqual(native_body.count("tee -a tools/macos/.build/native-unit-tests.log"), 3)
        self.assertIn("cat tools/macos/.build/native-disclosure-check.log | tee -a", native_body)
        self.assertIn("grep -q 'Executed 14 tests' tools/macos/.build/native-disclosure-check.log", native_body)
        self.assertIn("          focused_status=0\n", native_body)
        self.assertIn("            focused_status=1\n", native_body)
        self.assertIn("          exit \"$focused_status\"\n", native_body)
        focused = native_body.split("--filter '", 1)[1].split("'", 1)[0].split("|")
        self.assertEqual(set(focused), {
            "ProductRenderingTests.testCustomModelDetailsAreCollapsedUntilOpenedThroughNativeControl",
            "ProductRenderingTests.testInputLimitSettingsRenderLargeSavedValueAndSeparateByteBudgetInEnglishLight",
            "DockApplicationTests.testNewerManualTranslationRetainsFocusWhenAutomaticOCRIsDiscarded",
            "DockApplicationTests.testAutomaticScreenshotCompletionDoesNotReopenWindowsAfterHide",
            "FeedbackSettingsTests.testScreenshotModePickerChangesTheSavedPreferenceThroughNativeControl",
            "InputLimitInteractionTests.testNativeInvalidSavedValueCorrectionFailureReloadAndMismatchKeepDraft",
            "ProductRenderingTests.testAboutSupportEntryRendersAtMinimumWidthInEnglishAndChinese",
            "ProductRenderingTests.testPlainPasteFullSettingsRenderNativeOwnAppDispatchAndMissingEditorWithoutExternalPaste",
            "ProductRenderingTests.testScreenshotImageModeSettingsRenderInBothLanguages",
            "DockApplicationTests.testDockIdentityFollowsOpenWindowsAndReturnsWhenReopened",
            "NativeResultPlacementModelTests.testMissingSelectionOpensQuickInputAndInvalidTextKeepsExplicitErrorWithoutLateReopening",
            "NativeResultPlacementModelTests.testClosedResultStaysClosedForLateHelperEventsUntilANewSelection",
            "ProductRenderingTests.testCaptureInputCountersRenderBothIndependentLimitsAndVisibleActionInChineseDark",
            "ProductRenderingTests.testImageCreationRollbackFailureRendersRecoveryBeforeAnyHelperStarts",
        })
        self.assertEqual(len(focused), 14)
        log_upload = workflow[native_log:native_end]
        self.assertIn("        if: always()\n", log_upload)
        self.assertIn("name: cc-translate-native-unit-log-${{ github.sha }}", log_upload)
        self.assertIn("path: tools/macos/.build/native-unit-tests.log", log_upload)
        self.assertIn("tools/macos/.build/ui-screenshots/**/*.png", workflow)
        self.assertIn("tools/macos/.build/ui-screenshots/**/*.txt", workflow)
        for product in ("CCTranslateMac", "CCClipboardTestProducer"):
            build = "swift build --package-path macos --triple arm64-apple-macosx14.0 --product " + product
            self.assertLess(native_body.index(build), native_body.index("swift test --package-path macos"))
        self.assertIn("        id: native_units\n", workflow[native:native_end])
        self.assertIn("        continue-on-error: true\n", workflow[native:native_end])
        self.assertEqual(workflow.count("continue-on-error:"), 1)
        gate = workflow.index("      - name: Require native Swift unit success before archiving or publishing")
        archive = workflow.index("      - name: Recheck immutable bundle and preserve executable modes in zip")
        gate_body = workflow[gate:archive]
        self.assertIn("        if: always()\n", gate_body)
        self.assertIn("NATIVE_UNIT_OUTCOME: ${{ steps.native_units.outcome }}", gate_body)
        self.assertIn('test "$NATIVE_UNIT_OUTCOME" = success\n', gate_body)
        self.assertNotIn("steps.native_units.conclusion", gate_body)
        bundle_build = workflow.index("      - name: Build and audit development-only bundle")
        signed_update = workflow.index("      - name: Disposable signed Sparkle update lifecycle")
        signed_evidence = workflow.index("      - name: Retain disposable update lifecycle evidence")
        self.assertLess(bundle_build, signed_update)
        self.assertLess(signed_update, signed_evidence)
        self.assertLess(signed_evidence, native)
        self.assertLess(native_end, gate)
        self.assertLess(workflow.index("      - name: Native Foundation.Process"), gate)
        self.assertLess(gate, archive)
        self.assertLess(archive, workflow.index("      - name: Seal exact same-run archive"))
        self.assertLess(gate, workflow.index("      - name: Retain synthetic development artifact"))
        uploads = workflow[workflow.index("      - name: Retain synthetic development artifact"):]
        self.assertNotIn("path: ${{ runner.temp }}", uploads)
        self.assertNotIn("cc-translate-dictionary-fixture.sqlite3\n            tools/", uploads)

    def test_apple_translation_evaluation_is_explicit_and_not_a_product_build(self):
        workflow = (bundle.ROOT / ".github/workflows/macos-p0.yml").read_text(encoding="utf-8")
        self.assertIn("      apple_translation_evaluation:\n", workflow)
        self.assertIn("inputs.translation_benchmark != true && inputs.apple_translation_evaluation != true", workflow)
        self.assertIn("inputs.translation_benchmark && !inputs.apple_translation_evaluation", workflow)
        job = workflow.split("\n  apple_translation_evaluation:\n", 1)[1].split("\n  runtime:\n", 1)[0]
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.apple_translation_evaluation", job)
        self.assertIn('test "$OTHER_BENCHMARK_REQUESTED" != true', job)
        self.assertIn("- os: macos-15", job)
        self.assertIn("- os: macos-26", job)
        self.assertIn("timeout-minutes: 25", job)
        self.assertIn("-m unittest -v tests.test_apple_translation_eval", job)
        self.assertIn("-m tools.macos.apple_translation_eval", job)
        self.assertIn("--accept-download --screenshot-on-block", job)
        self.assertIn("        if: always()\n", job)
        self.assertIn("apple-translation-evaluation/*.json", job)
        for forbidden in ("bundle.py build", "CCTranslateMac-P0.zip", "continue-on-error",
                          "secrets.", "write-all", "tccutil", "login.keychain"):
            self.assertNotIn(forbidden, job)


class HelperBundleIntegrationTests(ProjectDirectory):
    def test_shared_core_is_packaged_without_windows_entry(self):
        core = self.root / "Core"
        bundle.copy_core_sources(core)
        self.assertEqual(bundle.SHARED_CORE_MODULES, SHARED_CORE_FILES)
        for name in SHARED_CORE_FILES:
            with self.subTest(name=name):
                self.assertEqual((core / name).read_bytes(), (bundle.ROOT / name).read_bytes())
        self.assertEqual(
            {path.name for path in core.iterdir()},
            {"launch.py", "cc_macos", "cc_providers", *SHARED_CORE_FILES})
        self.assertEqual(bundle.PROVIDER_CONTRACT_FILES, CONTRACT_FILES)
        self.assertEqual(bundle.PROVIDER_CONFIG_FILES, CONFIG_FILES)
        self.assertEqual(bundle.PROVIDER_CATALOG_FILES, CATALOG_FILES)
        self.assertEqual({path.name for path in (core / "cc_providers").iterdir()}, set(PROVIDER_FILES))
        for name in PROVIDER_FILES:
            self.assertEqual(
                (core / "cc_providers" / name).read_bytes(),
                (bundle.ROOT / "cc_providers" / name).read_bytes())
        self.assertFalse(list(core.rglob("__pycache__")))
        self.assertFalse(list(core.rglob("*.sqlite3")), "The optional pinned database is not an app resource")
        for name in ("cc_dictionary.py", "cc_dictionary_artifact.py", "cc_core.py", "cc_rich.py"):
            self.assertFalse((core / name).exists(), "Do not package Windows facades for native dictionary tests")
        self.assertEqual((core / "cc_macos/dictionary.py").read_bytes(),
                         (bundle.ROOT / "cc_macos/dictionary.py").read_bytes())

    def test_missing_shared_core_module_blocks_packaging(self):
        for index, missing in enumerate(SHARED_CORE_FILES):
            source = self.root / f"source{index}"
            source.mkdir()
            for name in SHARED_CORE_FILES:
                if name != missing:
                    (source / name).write_bytes(b"synthetic fixture")
            (source / "cc_providers").mkdir()
            for name in PROVIDER_FILES:
                (source / "cc_providers" / name).write_bytes(b"synthetic fixture")
            core = self.root / f"Core{index}"
            with self.subTest(missing=missing), patch.object(bundle, "ROOT", source):
                with self.assertRaisesRegex(bundle.BundleError, "shared core"):
                    bundle.copy_core_sources(core)
            self.assertFalse(core.exists())

    def test_missing_provider_contract_blocks_packaging(self):
        for index, missing in enumerate(PROVIDER_FILES):
            source = self.root / f"source{index}"
            (source / "cc_providers").mkdir(parents=True)
            for name in SHARED_CORE_FILES:
                (source / name).write_bytes(b"synthetic fixture")
            for name in PROVIDER_FILES:
                if name != missing:
                    (source / "cc_providers" / name).write_bytes(b"synthetic fixture")
            core = self.root / f"Core{index}"
            with self.subTest(missing=missing), patch.object(bundle, "ROOT", source):
                with self.assertRaisesRegex(bundle.BundleError, "provider contract"):
                    bundle.copy_core_sources(core)
            self.assertFalse(core.exists())

    def test_packaged_sources_and_smoke_consumer_with_real_host_helper(self):
        core = self.root / "Core"
        bundle.copy_core_sources(core)
        session = smoke.Session([sys.executable, "-I", "-B", core / "launch.py"], self.root)
        try:
            session.send("h", "hello", {})
            smoke.validate_ready(session.expect("h", "ready"))
            session.send("f", "request", {"operation": "fixture", "text": "synthetic", "delay_ms": 0})
            session.expect("f", "accepted")
            delta = session.expect("f", "delta")
            result = session.expect("f", "completed")
            self.assertEqual(delta, result)
            self.assertTrue(result["fixture"])

            session.send("r", "request", {"operation": "runtime_probe", "https": False})
            session.expect("r", "accepted")
            report = session.expect("r", "completed")
            self.assertTrue(report["python"]["isolated"])
            self.assertTrue(report["python"]["bytecode_disabled"])
            self.assertTrue(report["sqlite"]["read_write"])
            self.assertTrue(report["dictionary"]["read_only"])
            self.assertTrue(report["dictionary"]["reopened"])
            self.assertEqual(report["https"]["status"], "not_run")
            with self.assertRaises(bundle.BundleError):
                smoke.validate_runtime(report, bundle.load_lock())

            session.send("pending", "request", {"operation": "fixture", "text": "synthetic",
                                                "delay_ms": 2000})
            session.expect("pending", "accepted")
            session.close_input()
            session.expect("pending", "cancelled")
            session.finish()
            self.assertFalse(list(core.rglob("__pycache__")))
        finally:
            session.dispose()


if __name__ == "__main__":
    unittest.main()
