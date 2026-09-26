"""Portable fail-closed contracts; actual Ed25519/Sparkle installation is gated on macOS CI."""

import base64
from copy import deepcopy
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import unittest
from unittest.mock import patch
import urllib.error
import uuid
import zipfile

from tools.macos import bundle, release, release_publish, release_update_test


KEY = "1pjSwxyxn3K0SyCUdWxuh9fVQ9IqPCcLGreVjWSadTk="
SIGNATURE = base64.b64encode(bytes(range(64))).decode()
HASH = "a" * 64


class ReleaseDirectory(unittest.TestCase):
    def setUp(self):
        self.root = bundle.STAGING / ("release-test-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)


class ReleaseIdentityTests(unittest.TestCase):
    def test_versions_are_canonical_and_builds_strictly_increase(self):
        versions = ["0.0.1", "0.1.0", "0.1.999999", "0.2.0", "1.0.0", "2.0.0"]
        builds = [int(release.build_number(value)) for value in versions]
        self.assertEqual(builds, sorted(set(builds)))
        self.assertEqual(release.build_number("0.1.0"), "1000001")
        for value in ("0.0.0", "v0.1.0", "01.2.3", "1.2", "1.0.0-beta", "1.0.1000000",
                      "1.2.3\n", "1.2.3/../x", None):
            with self.subTest(value=value), self.assertRaises(bundle.BundleError):
                release.release_version(value)

    def test_key_is_required_canonical_and_not_zero(self):
        self.assertEqual(release.public_key(KEY), KEY)
        for key in (None, "", "garbage", KEY + "\n", KEY[:-1],
                    base64.b64encode(bytes(32)).decode(), base64.b64encode(bytes(31)).decode()):
            with self.subTest(key=key), self.assertRaises(bundle.BundleError):
                release.public_key(key)

    def test_repository_variable_must_match_tracked_public_key_before_any_native_or_publish_work(self):
        self.assertEqual(release.pinned_public_key(KEY), KEY)
        config = json.loads(release.CHANNEL_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(config["public_ed25519_key"], KEY)
        other = base64.b64encode(b"x" * 32).decode()
        with self.assertRaisesRegex(bundle.BundleError, "tracked release key pin"):
            release.pinned_public_key(other)
        with patch.object(bundle, "require_macos") as native, patch.object(release_publish, "api") as api:
            with self.assertRaisesRegex(bundle.BundleError, "tracked release key pin"):
                release.build_release("0.1.0", other, "unused seed", True)
            with self.assertRaisesRegex(bundle.BundleError, "tracked release key pin"):
                release_publish.publish(Path("."), "0.1.0", other)
            native.assert_not_called()
            api.assert_not_called()

    def test_production_plist_never_mutates_development_or_inherits_unsafe_overrides(self):
        original = plistlib.loads((bundle.ROOT / "macos/Resources/Info.plist").read_bytes())
        source = deepcopy(original)
        source.update(NSAppTransportSecurity={"NSAllowsArbitraryLoads": True},
                      BuilderPath="/Users/example/work/secret", SUEnableAutomaticChecks=True)
        info = release.release_info(source, "0.1.0", KEY)
        self.assertEqual(info["CFBundleIdentifier"], release.BUNDLE_ID)
        self.assertEqual(info["CFBundleIdentifier"], original["CFBundleIdentifier"])
        self.assertEqual(info["CFBundleIdentifier"], bundle.load_lock()["bundle_identifier"])
        self.assertEqual(info["SUPublicEDKey"], KEY)
        self.assertEqual(info["SUFeedURL"], release.FEED_URL)
        self.assertEqual(info["CFBundleVersion"], "1000001")
        self.assertNotIn("BuilderPath", info)
        self.assertNotIn("NSAppTransportSecurity", info)
        for key in ("SUEnableAutomaticChecks", "SUAutomaticallyUpdate", "SUSendProfileInfo"):
            self.assertIs(info[key], False)
        self.assertIs(info["SUVerifyUpdateBeforeExtraction"], True)
        self.assertEqual(original["CFBundleIdentifier"], "dev.cc-translate.macos.probe")
        self.assertNotIn("SUFeedURL", original)

    def test_native_and_packager_use_same_stable_channel(self):
        source = (bundle.ROOT / "macos/Sources/CCTranslateMac/AppUpdates.swift").read_text(encoding="utf-8")
        for value in (release.BUNDLE_ID, release.FEED_URL, release.DOWNLOADS_URL):
            self.assertIn(value, source)
        self.assertNotIn("MACOS_DEVELOPMENT", source)
        self.assertNotIn("/releases/latest/", release.FEED_URL)
        self.assertTrue(release.DOWNLOADS_URL.endswith("/releases/latest"))


class ReleaseFeedTests(ReleaseDirectory):
    def feed(self, version="0.1.0"):
        return release.appcast(version, KEY, SIGNATURE, 123, HASH)

    def test_feed_uses_final_signature_and_immutable_mac_asset(self):
        parsed = release.read_appcast(self.feed(), KEY)
        self.assertEqual(parsed, {
            "version": "0.1.0", "build": "1000001", "signature": SIGNATURE,
            "length": 123, "sha256": HASH, "url": release.asset_url("0.1.0"),
        })

    def test_rejects_missing_keys_signature_version_wrong_host_and_xml_entities(self):
        for old, new in [
            (KEY, ""), (SIGNATURE, ""), ("1000001", "2"),
            ("github.com", "untrusted.invalid"), ("https://github.com", "http://github.com"),
            (HASH, "missing"), ('length="123"', 'length="-1"'),
            ('<channel>', '<!DOCTYPE rss [<!ENTITY data "x">]><channel>'),
        ]:
            with self.subTest(old=old), self.assertRaises((bundle.BundleError,)):
                release.read_appcast(self.feed().replace(old.encode(), new.encode()), KEY)
        with self.assertRaises(bundle.BundleError):
            release.read_appcast(self.feed(), base64.b64encode(b"x" * 32).decode())

    def test_only_first_release_accepts_missing_feed(self):
        missing = urllib.error.HTTPError(release.FEED_URL, 404, "missing", {}, None)
        with patch.object(bundle, "run", return_value="[[]]"), \
                patch.object(release, "download", side_effect=missing):
            self.assertIsNone(release.previous_release("0.1.0", KEY, self.root))
        releases = [[{"tag_name": "macos-v0.1.0", "draft": False, "prerelease": False}]]
        with patch.object(bundle, "run", return_value=json.dumps(releases)), \
                patch.object(release, "download", side_effect=missing), self.assertRaises(bundle.BundleError):
            release.previous_release("0.2.0", KEY, self.root)

    def test_authorization_network_errors_and_existing_tag_fail_closed(self):
        for code in (401, 403, 500, 503):
            with patch.object(bundle, "run", return_value="[[]]"), \
                    patch.object(release, "download", side_effect=urllib.error.HTTPError(
                        release.FEED_URL, code, "error", {}, None)), self.assertRaises(bundle.BundleError):
                release.previous_release("0.1.0", KEY, self.root)
        releases = [[{"tag_name": "macos-v0.1.0", "draft": True, "prerelease": False}]]
        with patch.object(bundle, "run", return_value=json.dumps(releases)), self.assertRaises(bundle.BundleError):
            release.previous_release("0.1.0", KEY, self.root)

    def test_rollback_rejected_before_previous_archive_download(self):
        releases = [[{"tag_name": "macos-v0.2.0", "draft": False, "prerelease": False}]]
        def downloaded(url, path, limit):
            self.assertEqual(url, release.FEED_URL)
            path.write_bytes(self.feed("0.2.0"))
        with patch.object(bundle, "run", return_value=json.dumps(releases)), \
                patch.object(release, "download", side_effect=downloaded), self.assertRaises(bundle.BundleError):
            release.previous_release("0.1.0", KEY, self.root)

    def test_https_redirect_downgrade_is_not_followed(self):
        handler = release.HTTPSOnlyRedirect()
        with self.assertRaises(bundle.BundleError):
            handler.redirect_request(None, None, 302, "", {}, "http://github.com/archive.zip")


class ReleasePayloadTests(ReleaseDirectory):
    def test_channel_config_rejects_missing_pin_private_fields_and_identity_changes(self):
        config_path = self.root / "channel.json"
        config = json.loads(release.CHANNEL_CONFIG.read_text(encoding="utf-8"))
        for mutation in (
            {**config, "private_key": "forbidden"},
            {**config, "bundle_identifier": "changed.identity"},
            {**config, "feed_url": "https://example.invalid/feed.xml"},
            {**config, "schema": True},
            {key: value for key, value in config.items() if key != "public_ed25519_key"},
        ):
            bundle.write_json(config_path, mutation)
            with patch.object(release, "CHANNEL_CONFIG", config_path), self.assertRaises(bundle.BundleError):
                release.pinned_public_key(KEY)

    def test_allowlist_keeps_product_diagnostics_but_removes_unused_fixtures(self):
        for name in ("probes.py", "config_fixture.py", "catalog_fixture.py",
                     "catalog_process_fixture.py", "dictionary_probe.py"):
            self.assertTrue(release.keep_release_path("Contents/Resources/Core/cc_macos/" + name))
        for name in ("translation_fixture.py", "native_provider_fixture.py", "image_fixture.py",
                     "storage_fixture.py", "history_fixture.py", "config_store_fixture.py", "launch.py"):
            self.assertFalse(release.keep_release_path("Contents/Resources/Core/cc_macos/" + name))
        for name in ("README.md", "docs/TODO.md", "audit.json", ".DS_Store",
                     "Contents/Resources/Licenses/Python/PYTHON.json",
                     "Contents/Resources/python/lib/python3.12/test/test_path.py",
                     "Contents/Resources/python/lib/python3.12/__pycache__/os.pyc",
                     "Contents/Frameworks/Sparkle.framework/Headers/SPUUpdater.h",
                     "Contents/Frameworks/Sparkle.framework/Versions/B/Modules/module.modulemap"):
            self.assertFalse(release.keep_release_path(name), name)

    def test_product_import_closure_survives_pruning(self):
        core = self.root / "Core"
        bundle.copy_core_sources(core)
        for path in core.rglob("*.py"):
            if path.relative_to(core).as_posix() not in release.CORE_FILES:
                path.unlink()
        release.validate_core_imports(core)
        (core / "cc_macos/server.py").write_text("from .translation_fixture import prepare\n")
        with self.assertRaisesRegex(bundle.BundleError, "prunes product dependency"):
            release.validate_core_imports(core)

    def test_python_build_metadata_is_allowlisted_not_merely_path_replaced(self):
        path = self.root / "_sysconfigdata__darwin_darwin.py"
        path.write_text("build_time_vars = {'SOABI': 'cpython-312-darwin', 'EXT_SUFFIX': '.so', "
                        "'CFLAGS': '-g /Users/example/build', 'srcdir': '/install/src', "
                        "'Py_DEBUG': 0, 'LIBDIR': '/install/lib'}\n")
        release.sanitize_sysconfig(path)
        text = path.read_text()
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/install/", text)
        self.assertIn("SOABI", text)
        self.assertNotIn("CFLAGS", text)

    def test_minimization_drops_builder_metadata_and_unused_sources_but_retains_about_contract(self):
        app = self.root / release.APP_NAME
        contents = app / "Contents"
        core = contents / "Resources/Core"
        bundle.copy_core_sources(core)
        (core / "cacert.pem").write_bytes(b"certificate")
        template = plistlib.loads((bundle.ROOT / "macos/Resources/Info.plist").read_bytes())
        (contents / "Info.plist").write_bytes(plistlib.dumps(template))
        python = contents / "Resources/python/lib/python3.12"
        python.mkdir(parents=True)
        (python / "_sysconfigdata__darwin_darwin.py").write_text(
            "build_time_vars = {'SOABI':'cpython-312-darwin','EXT_SUFFIX':'.so',"
            "'prefix':'/install','srcdir':'/private/tmp/builder'}\n")
        (app / "internal-todo.md").write_text("must not ship")
        licenses = contents / "Resources/Licenses/Python"
        licenses.mkdir(parents=True)
        (licenses / "PYTHON.json").write_text('{"build":"/Users/example/build"}')
        bundle.write_json(contents / "Resources/source-manifest.json", {
            "toolchain": {"xcode": "Xcode 16.4", "sdk": "15.5", "architecture": "arm64",
                          "directory": "/Users/example/work"},
            "excluded_runtime_members": ["private-build-notes"], "lock": {"private": "/install"},
        })
        release.minimize(app, "0.1.0", KEY, "b" * 40)
        self.assertFalse((app / "internal-todo.md").exists())
        self.assertFalse((licenses / "PYTHON.json").exists())
        self.assertFalse((core / "cc_macos/translation_fixture.py").exists())
        self.assertTrue((core / "cc_macos/catalog_fixture.py").exists())
        manifest = json.loads((contents / "Resources/source-manifest.json").read_bytes())
        self.assertIs(manifest["development_only"], False)
        self.assertEqual(manifest["source_commit"], "b" * 40)
        self.assertNotIn("excluded_runtime_members", manifest)
        self.assertNotIn("directory", manifest["toolchain"])
        self.assertEqual(set(manifest["lock"]), {"python_version"})
        self.assertTrue(manifest["resource_hashes"])

    def test_builder_path_detection_does_not_confuse_public_documentation_urls(self):
        for value in (b'"/Users/person/work/main.swift"', b'"/private/tmp/python.c"',
                      b'"/install/lib/python3.12"', b'"/home/example/work"'):
            self.assertIsNotNone(release.BUILDER_PATH.search(value))
        self.assertIsNone(release.BUILDER_PATH.search(
            b"https://www.ibm.com/knowledgecenter/en/ssw_aix_72/install/binary_compatability.html"))

    def test_archive_rejects_extra_docs_aliases_and_escaping_links(self):
        for extra in ("README.md", "../TODO.md", "CC Translate.app/../../bad", "/absolute"):
            archive = self.root / "test.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("CC Translate.app/Contents/Info.plist", b"plist")
                output.writestr(extra, b"bad")
            with self.subTest(extra=extra), self.assertRaises(bundle.BundleError):
                release.verify_zip(archive)
        archive = self.root / "symlink.zip"
        with zipfile.ZipFile(archive, "w") as output:
            item = zipfile.ZipInfo("CC Translate.app/Contents/link")
            item.external_attr = 0o120777 << 16
            output.writestr(item, "../../../escape")
        with self.assertRaises(bundle.BundleError):
            release.verify_zip(archive)

    def test_release_refuses_missing_key_or_acknowledgement_before_native_actions(self):
        with patch.object(bundle, "require_macos") as native:
            for key, seed, acknowledge in [(None, "seed", True), (KEY, None, True), (KEY, "seed", False)]:
                with self.assertRaises(bundle.BundleError):
                    release.build_release("0.1.0", key, seed, acknowledge)
            native.assert_not_called()


class ReleaseUpgradeTests(unittest.TestCase):
    def report(self, install):
        return {
            "expected_identifier": release.BUNDLE_ID, "installed_identifier": release.BUNDLE_ID,
            "graceful_cleanup": True, "cleanup_complete": True, "session_in_progress": False,
            "events": ["found", "ready-to-install", "installed"] if install else ["found", "error"],
            "offered_build": "1000001", "original_pid": 123, "owned_pids": [123, 456],
            "running_pids_before_cleanup": [456] if install else [123],
            "outcome": "installed" if install else "error",
            "installed_build": "1000001" if install else "1000000",
            "relaunched": install, "original_terminated": install,
            "errors": [{"code": 3002, "domain": "SUSparkleErrorDomain",
                        "description": "EdDSA signature does not match. Update rejected"}],
        }

    def test_real_install_requires_relaunch_and_final_production_identity(self):
        report = self.report(True)
        release_update_test.verify_report(report, "install", "1000000", "1000001")
        for key, value in [("relaunched", False), ("installed_identifier", "fixture"),
                           ("original_terminated", False), ("cleanup_complete", False),
                           ("installed_build", "1000000"), ("running_pids_before_cleanup", [123])]:
            changed = {**report, key: value}
            with self.subTest(key=key), self.assertRaises(bundle.BundleError):
                release_update_test.verify_report(changed, "install", "1000000", "1000001")

    def test_rejection_must_be_crypto_failure_and_leave_original_running(self):
        report = self.report(False)
        release_update_test.verify_report(report, "bad-signature", "1000000", "1000001")
        for key, value in [("errors", []), ("installed_build", "1000001"),
                           ("original_terminated", True), ("events", ["found", "ready-to-install"])]:
            with self.subTest(key=key), self.assertRaises(bundle.BundleError):
                release_update_test.verify_report({**report, key: value}, "bad-signature", "1000000", "1000001")


class ReleasePublicationTests(unittest.TestCase):
    def test_update_api_uses_patch_not_post(self):
        completed = subprocess.CompletedProcess([], 0, stdout='{"id":1}', stderr="")
        with patch.object(subprocess, "run", return_value=completed) as run:
            release_publish.api("releases/1", {"draft": False}, method="PATCH")
        self.assertIn("PATCH", run.call_args.args[0])
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"draft": False})

    def test_workflow_separates_read_only_signing_and_write_only_publication(self):
        workflow = (bundle.ROOT / ".github/workflows/macos-release.yml").read_text(encoding="utf-8")
        self.assertIn("contents: read", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("github.ref_name == github.event.repository.default_branch", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertEqual(workflow.count("secrets.MACOS_SPARKLE_PRIVATE_KEY"), 1)
        self.assertNotIn("--clobber", workflow)
        source = (bundle.HERE / "release_publish.py").read_text(encoding="utf-8")
        self.assertIn('"force": False', source)
        self.assertIn('{"draft": False, "make_latest": "true"}, method="PATCH"', source)
        self.assertIn("published asset verification failed", source)

    def test_atomic_channel_lookup_ignores_similarly_named_branches(self):
        with patch.object(release_publish, "api", return_value=[
            {"ref": "refs/heads/macos-updates-preview", "object": {"sha": "wrong"}},
            {"ref": "refs/heads/macos-updates", "object": {"sha": "current"}},
        ]):
            self.assertEqual(release_publish.channel_head(), "current")
