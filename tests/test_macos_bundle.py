"""Offline stdlib tests for the macOS archive, license, dyld and smoke rules."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import plistlib
import queue
import shutil
import stat
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from tools.macos import bundle, smoke


SHARED_CORE_FILES = ("cc_classify.py", "cc_direction.py", "cc_prompts.py")


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
                "LSUIElement": True, "LSMinimumSystemVersion": "14.0"}
        binaries = ["MacOS/CCTranslateMac", "Helpers/python/bin/python3",
                    "Helpers/python/lib/libpython3.12.dylib"]
        resources = ["Resources/Core/launch.py", "Resources/Core/cc_macos/__main__.py",
                     "Resources/Core/cacert.pem", "Resources/Licenses/certifi/LICENSE",
                     "Resources/Licenses/certifi/MPL-2.0.txt", "Resources/Licenses/Python/PYTHON.json"]
        resources += ["Resources/Licenses/Python/licenses/" + name
                      for name in lock["required_runtime_licenses"]]
        resources += ["Resources/Core/" + name for name in SHARED_CORE_FILES]
        for name in binaries + resources:
            path = contents / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\xcf\xfa\xed\xfeSYNTHETIC" if name in binaries else b"synthetic fixture")
            path.chmod(0o755 if name in binaries else 0o644)
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        bundle.write_json(contents / "Resources/source-manifest.json", {
            "lock": lock, "certificate_sha256": bundle.digest(contents / "Resources/Core/cacert.pem"),
            "resource_hashes": {name: bundle.digest(contents / name) for name in resources},
        })
        return app

    @staticmethod
    def fake_apple_tool(args, environment=None):
        tool = Path(args[0]).name
        binary = Path(args[-1])
        if tool == "file":
            return "Mach-O 64-bit arm64 (synthetic test fixture)"
        if tool == "lipo":
            return "arm64"
        if args[1] == "-l":
            minimum = "14.0" if binary.name == "CCTranslateMac" else "11.0"
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
        self.assertEqual(len(report["checks"]), 3)
        self.assertEqual(tools.call_count, 12)
        self.assertEqual(report["release_gate"], "NOT PASSED")

    def test_audit_checks_unreferenced_macho_and_rejects_newer_os(self):
        app = self.synthetic_app()
        extra = app / "Contents/Helpers/python/lib/extra.dylib"
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
        binary = app / "Contents/Helpers/python/bin/python3.12"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"synthetic")
        library = app / "Contents/Helpers/python/lib/libpython3.12.dylib"
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
                 "LSUIElement": True, "LSMinimumSystemVersion": "14.0"}
        bundle.validate_plist(plistlib.loads(plistlib.dumps(valid)), lock)
        for key, value in (("CFBundleIdentifier", "production"), ("LSUIElement", 1),
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
            "ssl": {"status": "passed", "certificate_validation": True, "ca_source": "bundle"},
            "https": {"status": "passed", "certificate_verified": True, "host": "www.python.org"},
        }
        smoke.validate_runtime(report, lock)
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
            {"launch.py", "cc_macos", *SHARED_CORE_FILES})
        self.assertFalse(list(core.rglob("__pycache__")))

    def test_missing_shared_core_module_blocks_packaging(self):
        for index, missing in enumerate(SHARED_CORE_FILES):
            source = self.root / f"source{index}"
            source.mkdir()
            for name in SHARED_CORE_FILES:
                if name != missing:
                    (source / name).write_bytes(b"synthetic fixture")
            core = self.root / f"Core{index}"
            with self.subTest(missing=missing), patch.object(bundle, "ROOT", source):
                with self.assertRaisesRegex(bundle.BundleError, "shared core"):
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
