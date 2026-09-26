"""Real Sparkle upgrade/rejection gates for the exact production-signed release ZIP."""

import base64
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys

from . import bundle, release, update_fixture
from .runtime_matrix import tree_digest


def verify_report(report, scenario, old_build, new_build):
    release.need(report["expected_identifier"] == release.BUNDLE_ID and
                 report["installed_identifier"] == release.BUNDLE_ID, "Sparkle changed release identity")
    release.need(report["graceful_cleanup"] and report["cleanup_complete"] and
                 not report["session_in_progress"], "Sparkle session/process cleanup failed")
    events = report["events"]
    release.need("permission-request" not in events and "found" in events and
                 report["offered_build"] == new_build, "wrong update offered")
    release.need(report["original_pid"] in report["owned_pids"] and
                 set(report["running_pids_before_cleanup"]) <= set(report["owned_pids"]),
                 "unowned update process")
    if scenario == "install":
        release.need(report["outcome"] == "installed" and report["installed_build"] == new_build and
                     report["relaunched"] and report["original_terminated"] and
                     "ready-to-install" in events and "installed" in events and
                     len(report["running_pids_before_cleanup"]) == 1 and
                     report["original_pid"] not in report["running_pids_before_cleanup"],
                     "real release installation/relaunch was not proven")
    else:
        release.need(report["outcome"] == "error" and report["installed_build"] == old_build and
                     not report["original_terminated"] and "ready-to-install" not in events and
                     "installed" not in events and
                     report["running_pids_before_cleanup"] == [report["original_pid"]],
                     "unauthenticated update modified or terminated the host")
        release.need(any(error["code"] == 3002 and error["domain"] == "SUSparkleErrorDomain" and
                         error["description"].startswith("EdDSA signature does not match.")
                         for error in report["errors"]), "rejection was not an EdDSA authenticity failure")


def compile_driver(app, root):
    driver = root / "ReleaseUpdateDriver.app"
    executable = driver / "Contents/MacOS/ReleaseUpdateDriver"
    executable.parent.mkdir(parents=True)
    frameworks = driver / "Contents/Frameworks"
    frameworks.mkdir()
    shutil.copytree(app / "Contents/Frameworks/Sparkle.framework", frameworks / "Sparkle.framework", symlinks=True)
    (driver / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": "dev.cc-translate.release-validation.driver", "CFBundleVersion": "1",
        "CFBundleExecutable": executable.name, "CFBundlePackageType": "APPL",
        "LSUIElement": True, "NSPrincipalClass": "NSApplication",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True,
            "NSExceptionDomains": {"127.0.0.1": {"NSExceptionAllowsInsecureHTTPLoads": True}}},
    }))
    update_fixture.command([
        "/usr/bin/xcrun", "clang", "-fobjc-arc", "-Werror", "-Wno-deprecated-declarations",
        "-DCC_RELEASE_UPDATE_TEST=1", "-mmacosx-version-min=14.0", "-lproc",
        "-framework", "AppKit", "-framework", "Sparkle",
        "-F", bundle.APP / "Contents/Frameworks",
        "-Wl,-rpath,@executable_path/../Frameworks",
        bundle.HERE / "fixtures/UpdateDriver.m", "-o", executable,
    ])
    update_fixture.sign_bundle(driver)
    return executable


def storage_script(root):
    path = root / "preserve-storage.py"
    path.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "app, home, identity, mode = sys.argv[1:]\n"
        "sys.path.insert(0, str(Path(app) / 'Contents/Resources/Core'))\n"
        "from cc_macos.configuration import ConfigurationSession\n"
        "from cc_storage import macos_user_paths\n"
        "session = ConfigurationSession(Path(home), identity)\n"
        "session.open()\n"
        "try:\n"
        "    config = session.perform({'operation': 'config_load'})['config']\n"
        "    if mode == 'seed':\n"
        "        config['font_size'] = 16\n"
        "        config['release_upgrade_marker'] = 'preserved'\n"
        "        session.perform({'operation': 'config_save', 'config': config})\n"
        "    config = session.perform({'operation': 'config_load'})['config']\n"
        "    assert config['font_size'] == 16 and config['release_upgrade_marker'] == 'preserved'\n"
        "    print(json.dumps(config, sort_keys=True))\n"
        "finally:\n"
        "    session.close()\n", encoding="utf-8")
    return path


def verify_release_upgrade(app, archive, version, key, signature, output, previous):
    release.need(sys.platform == "darwin" and os.environ.get("GITHUB_ACTIONS") == "true",
                 "production-identity lifecycle must run on a disposable GitHub macOS runner")
    output.mkdir(parents=True)
    home = Path.home()
    owned = [home / "Library" / directory / release.BUNDLE_ID
             for directory in ("Application Support", "Caches")]
    preferences = home / "Library/Preferences" / (release.BUNDLE_ID + ".plist")
    release.need(not any(p.exists() or p.is_symlink() for p in [*owned, preferences]),
                 "production app state already exists; refusing to touch a user's installation")
    driver = compile_driver(app, output)
    probe = storage_script(output)
    original_app = tree_digest(app)
    original_archive = bundle.digest(archive)
    evidence = {"scope": "Exact final ZIP and production Ed25519 key; local transport only. "
                "Not Gatekeeper/notarization/TCC validation.",
                "baseline": "previous published release" if previous else "same release with lower build",
                "public_key": key, "archive_sha256": original_archive, "cases": {}}
    with update_fixture.fixture_server() as server:
        for scenario in ("bad-signature", "install"):
            case = output / scenario
            case.mkdir()
            installed = case / release.APP_NAME
            if previous:
                release.verify_zip(previous["archive"])
                update_fixture.command(["/usr/bin/ditto", "-x", "-k", previous["archive"], case])
            else:
                shutil.copytree(app, installed, symlinks=True)
            info_path = installed / "Contents/Info.plist"
            info = plistlib.loads(info_path.read_bytes())
            release.need(info["CFBundleIdentifier"] == release.BUNDLE_ID and info["SUPublicEDKey"] == key,
                         "upgrade baseline has the wrong production identity/key")
            if not previous:
                info["CFBundleVersion"] = str(int(release.build_number(version)) - 1)
            old_build = info["CFBundleVersion"]
            release.need(int(old_build) < int(release.build_number(version)), "baseline is not older")
            # Only the disposable old host permits loopback. The final archive is never modified.
            info["NSAppTransportSecurity"] = {"NSAllowsLocalNetworking": True,
                "NSExceptionDomains": {"127.0.0.1": {"NSExceptionAllowsInsecureHTTPLoads": True}}}
            info_path.write_bytes(plistlib.dumps(info))
            update_fixture.sign_bundle(installed)
            before_app = tree_digest(installed)
            selected_signature = signature
            if scenario == "bad-signature":
                damaged = bytearray(base64.b64decode(signature))
                damaged[0] ^= 1
                selected_signature = base64.b64encode(damaged).decode()
            base = f"http://127.0.0.1:{server.server_port}/{scenario}"
            with server.routes_lock:
                server.routes[f"/{scenario}/appcast.xml"] = (200, release.appcast(
                    version, key, selected_signature, archive.stat().st_size, original_archive,
                    url=base + "/update.zip"))
                server.routes[f"/{scenario}/update.zip"] = (200, archive)
            report_path = case / "result.json"
            started = False
            created = []
            preferences_created = False
            try:
                for directory in owned:
                    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
                    created.append(directory)
                    (directory / "release-sentinel").write_bytes(b"preserved")
                command = [installed / "Contents/Resources/python/bin/python3", "-I", "-B",
                           probe, installed, home, release.BUNDLE_ID]
                before = update_fixture.json_command([*command, "seed"])
                update_fixture.command(["/usr/bin/defaults", "write", release.BUNDLE_ID,
                                        "releaseUpgradeMarker", "-string", "preserved"])
                preferences_created = True
                started = True
                update_fixture.command([driver, installed, scenario, report_path, base + "/appcast.xml"])
                report = json.loads(report_path.read_bytes())
                verify_report(report, scenario, old_build, release.build_number(version))
                after = update_fixture.json_command([*command, "read"])
                release.need(before == after, "update lost configuration")
                release.need(all((directory / "release-sentinel").read_bytes() == b"preserved"
                                 for directory in owned), "update lost application support/cache data")
                marker = update_fixture.command(["/usr/bin/defaults", "read", release.BUNDLE_ID,
                                                 "releaseUpgradeMarker"], capture_output=True)
                release.need(marker.stdout.strip() == b"preserved", "update lost native preferences")
                if scenario == "install":
                    release.need(tree_digest(installed) == original_app,
                                 "Sparkle did not install the exact final production bundle")
                    update_fixture.command(["/usr/bin/codesign", "--verify", "--deep", "--strict", installed])
                else:
                    release.need(tree_digest(installed) == before_app, "rejected update changed installed files")
                evidence["cases"][scenario] = {
                    "outcome": report["outcome"], "build": report["installed_build"],
                    "configuration_preserved": True, "native_preferences_preserved": True,
                    "production_archive_unchanged": bundle.digest(archive) == original_archive,
                }
            finally:
                if started:
                    # Failure to establish process ownership/cleanup aborts without deleting live files.
                    update_fixture.stop_fixture_processes(report_path, installed)
                for directory in created:
                    shutil.rmtree(directory)
                if preferences_created:
                    update_fixture.command(["/usr/bin/defaults", "delete", release.BUNDLE_ID], capture_output=True)
    release.need(tree_digest(app) == original_app and bundle.digest(archive) == original_archive,
                 "lifecycle mutated production artifacts")
    bundle.write_json(output / "evidence.json", evidence)
