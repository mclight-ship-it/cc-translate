"""Test real Sparkle installs against disposable App copies, never a release feed."""

import argparse
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid
from xml.sax.saxutils import quoteattr

if __package__:
    from .runtime_matrix import tree_digest
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.macos.runtime_matrix import tree_digest

HERE = Path(__file__).resolve().parent
SCENARIOS = ("install", "bad-signature", "wrong-key", "cancel-download", "cancel-install", "download-error")
PREFIX = "dev.cc-translate.update-fixture."

class FixtureCleanupError(RuntimeError):
    pass


def stop_fixture_processes(report_path, app):
    if not report_path.is_file():
        raise FixtureCleanupError("No process ledger; retain fixture files rather than deleting a running app")
    report = json.loads(report_path.read_bytes())
    executable = (app / "Contents/MacOS/CCTranslateMac").resolve()
    for pid in report["owned_pids"]:
        if not isinstance(pid, int) or pid < 2 or pid == os.getpid():
            raise FixtureCleanupError("Invalid fixture PID")
        for attempt in range(60):
            state = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "comm="], capture_output=True)
            if state.returncode == 1:
                break
            if state.returncode != 0 or Path(state.stdout.decode().strip()).resolve() != executable:
                raise FixtureCleanupError("PID identity changed; refusing to signal another process")
            if attempt in (0, 30):
                try:
                    os.kill(pid, signal.SIGTERM if attempt == 0 else signal.SIGKILL)
                except ProcessLookupError:
                    break
            time.sleep(0.1)
        else:
            raise FixtureCleanupError("Owned fixture process did not terminate")


def fixture_info(original, identifier, feed, public_key, version):
    if not identifier.startswith(PREFIX) or identifier == PREFIX or "/" in identifier:
        raise ValueError("isolated fixture identifier required")
    url = urlsplit(feed)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.username or url.password:
        raise ValueError("fixture feed must use a loopback server")
    if len(base64.b64decode(public_key, validate=True)) != 32 or version not in ("1", "2"):
        raise ValueError("invalid fixture key or version")
    return {**original, "CFBundleIdentifier": identifier, "CFBundleVersion": version,
            "SUFeedURL": feed, "SUPublicEDKey": public_key, "SUEnableAutomaticChecks": False,
            "SUAutomaticallyUpdate": False, "SUSendProfileInfo": False,
            "SUVerifyUpdateBeforeExtraction": True,
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True,
                "NSExceptionDomains": {"127.0.0.1": {"NSExceptionAllowsInsecureHTTPLoads": True}}}}


def appcast(url, signature, length):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
        '<channel><title>Disposable update fixture</title><item><title>Fixture build 2</title>'
        '<sparkle:version>2</sparkle:version><sparkle:minimumSystemVersion>14.0</sparkle:minimumSystemVersion>'
        f'<enclosure url={quoteattr(url)} sparkle:edSignature={quoteattr(signature)} '
        f'length="{length}" type="application/octet-stream" /></item></channel></rss>'
    ).encode()


def verify_case(scenario, report, before, after):
    if scenario not in SCENARIOS:
        raise ValueError("unknown fixture scenario")
    assert report["scenario"] == scenario
    assert report["installed_identifier"] == report["expected_identifier"]
    assert report["installed_identifier"].startswith(PREFIX)
    assert report["graceful_cleanup"] and report["cleanup_complete"]
    assert not report["session_in_progress"], "Updater still owns a pending session"
    assert before == after, "Configuration changed during update"
    events = report["events"]
    assert "launched-original" in events and "found" in events
    assert report["offered_build"] == "2"
    assert "permission-request" not in events
    if scenario == "install":
        assert report["outcome"] == "installed" and report["installed_build"] == "2"
        assert report["relaunched"] and report["original_terminated"]
        assert len(report["running_pids_before_cleanup"]) == 1
        assert report["running_pids_before_cleanup"][0] != report["original_pid"]
        assert "ready-to-install" in events and "installed" in events
    else:
        assert report["installed_build"] == "1" and not report["original_terminated"]
        assert report["running_pids_before_cleanup"] == [report["original_pid"]]
        assert "installed" not in events
        if scenario in ("bad-signature", "wrong-key"):
            assert report["outcome"] == "error"
            assert any(error["code"] == 3001 and error["domain"] == "SUSparkleErrorDomain"
                       for error in report["errors"]), "Must fail signature validation, not an unrelated gate"
            assert "ready-to-install" not in events
        elif scenario == "download-error":
            assert report["outcome"] == "error"
            assert any(error["code"] == 2001 for error in report["errors"])
        else:
            assert report["outcome"] == scenario
            assert "error" not in events
            assert ("ready-to-install" in events) == (scenario == "cancel-install")


class FixtureServer(BaseHTTPRequestHandler):
    def do_GET(self):
        with self.server.routes_lock:
            self.server.requests.append(self.path)
            response = self.server.routes.get(self.path)
        if response is None:
            self.send_error(404)
            return
        status, payload = response
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(payload.stat().st_size if isinstance(payload, Path) else len(payload)))
        self.end_headers()
        try:
            if isinstance(payload, Path):
                with payload.open("rb") as stream:
                    shutil.copyfileobj(stream, self.wfile)
            else:
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            # Download cancellation deliberately closes the fixture connection.
            if "/cancel-" not in self.path:
                raise

    def log_message(self, format, *args):
        pass


@contextmanager
def fixture_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureServer)
    server.routes, server.requests = {}, []
    server.routes_lock = threading.Lock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def command(arguments, **kwargs):
    try:
        return subprocess.run([str(arg) for arg in arguments], check=True, timeout=180, **kwargs)
    except subprocess.CalledProcessError as error:
        if error.stderr:
            sys.stderr.buffer.write(error.stderr)
        raise


def json_command(arguments):
    return json.loads(command(arguments, capture_output=True).stdout)


def sign_bundle(app):
    command(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", app], capture_output=True)
    command(["/usr/bin/codesign", "--verify", "--deep", "--strict", app], capture_output=True)


def configure_copy(original, destination, identifier, feed, key, version):
    shutil.copytree(original, destination, symlinks=True)
    path = destination / "Contents/Info.plist"
    info = fixture_info(plistlib.loads(path.read_bytes()), identifier, feed, key, version)
    path.write_bytes(plistlib.dumps(info))
    sign_bundle(destination)


def compile_tools(app, root):
    signer = root / "sign-fixture"
    command(["/usr/bin/xcrun", "swiftc", HERE / "fixtures/SignUpdate.swift", "-o", signer])
    driver = root / "UpdateFixtureDriver.app"
    executable = driver / "Contents/MacOS/UpdateFixtureDriver"
    executable.parent.mkdir(parents=True)
    frameworks = driver / "Contents/Frameworks"
    frameworks.mkdir()
    shutil.copytree(app / "Contents/Frameworks/Sparkle.framework", frameworks / "Sparkle.framework", symlinks=True)
    (driver / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": PREFIX + uuid.uuid4().hex + ".driver", "CFBundleVersion": "1",
        "CFBundleExecutable": executable.name, "CFBundlePackageType": "APPL",
        "LSUIElement": True, "NSPrincipalClass": "NSApplication",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True,
            "NSExceptionDomains": {"127.0.0.1": {"NSExceptionAllowsInsecureHTTPLoads": True}}},
    }))
    command(["/usr/bin/xcrun", "clang", "-fobjc-arc", "-Werror", "-Wno-deprecated-declarations",
             "-mmacosx-version-min=14.0", "-framework", "AppKit", "-framework", "Sparkle",
             "-F", frameworks, "-Wl,-rpath,@executable_path/../Frameworks",
             HERE / "fixtures/UpdateDriver.m", "-o", executable])
    sign_bundle(driver)
    return signer, executable


def owned_data_directories(home, identifier):
    assert identifier.startswith(PREFIX) and "/" not in identifier
    return [home / "Library" / name / identifier for name in ("Application Support", "Caches")]


def run_case(app, root, server, signer, driver, key_file, public_key, wrong_key, scenario, output):
    case = root / scenario
    case.mkdir()
    identity = PREFIX + uuid.uuid4().hex
    route = f"/{scenario}-{identity.removeprefix(PREFIX)}"
    base = f"http://127.0.0.1:{server.server_port}{route}"
    current, updated = case / "installed/CCTranslate.app", case / "next/CCTranslate.app"
    configure_copy(app, current, identity, base + "/feed.xml",
                   wrong_key if scenario == "wrong-key" else public_key, "1")
    configure_copy(app, updated, identity, base + "/feed.xml", public_key, "2")
    archive = case / "update.zip"
    command(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", updated, archive])
    signature = json_command([signer, "sign", key_file, archive])["signature"]
    if scenario == "bad-signature":
        raw = bytearray(base64.b64decode(signature))
        raw[0] ^= 1
        signature = base64.b64encode(raw).decode()
    with server.routes_lock:
        server.routes[route + "/feed.xml"] = (200, appcast(base + "/update.zip", signature, archive.stat().st_size))
        server.routes[route + "/update.zip"] = ((503, b"synthetic download failure") if scenario == "download-error"
                                                    else (200, archive))
    home = Path.home().resolve()
    owned = []
    report_path = output / (scenario + ".json")
    driver_started = False
    preferences_seeded = False
    try:
        for directory in owned_data_directories(home, identity):
            directory.mkdir(mode=0o700, parents=False, exist_ok=False)
            owned.append(directory)
        probe = [current / "Contents/Helpers/python/bin/python3", "-I", "-B",
                 HERE / "fixtures/update_storage.py", current, home, identity]
        before = json_command([*probe, "seed"])
        sentinels = [directory / "update-fixture-sentinel" for directory in owned]
        for sentinel in sentinels:
            sentinel.write_bytes(b"isolated preserved data")
        command(["/usr/bin/defaults", "write", identity, "updateFixtureMarker", "-string", "preserved"])
        preferences_seeded = True
        previous_tree = tree_digest(current)
        driver_started = True
        command([driver, current, scenario, report_path])
        report = json.loads(report_path.read_bytes())
        after = json_command([*probe, "read"])
        verify_case(scenario, report, before, after)
        assert all(sentinel.read_bytes() == b"isolated preserved data" for sentinel in sentinels)
        marker = command(["/usr/bin/defaults", "read", identity, "updateFixtureMarker"], capture_output=True)
        assert marker.stdout.strip() == b"preserved"
        resulting_tree = tree_digest(current)
        if scenario != "install":
            assert resulting_tree == previous_tree, "A rejected or cancelled update modified the App"
        else:
            assert resulting_tree != previous_tree
        report.update(configuration_before=before, configuration_after=after,
                      native_preferences_preserved=True, owned_data_sentinels_preserved=True,
                      previous_app_unchanged=scenario != "install",
                      before_tree_sha256=previous_tree, after_tree_sha256=resulting_tree)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        return report
    finally:
        if driver_started:
            stop_fixture_processes(report_path, current)
        # Each directory was exclusively created above under a fresh fixture namespace.
        for directory in owned:
            assert directory.name == identity and identity.startswith(PREFIX)
            shutil.rmtree(directory)
        if preferences_seeded:
            command(["/usr/bin/defaults", "delete", identity], capture_output=True)


def run(app, output):
    if sys.platform != "darwin":
        raise RuntimeError("The signed-update fixture requires macOS")
    app, output = app.resolve(strict=True), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    original = tree_digest(app)
    provenance = json.loads((app / "Contents/Resources/source-manifest.json").read_bytes())
    report = {"status": "failed", "scope": "Disposable copies, ad-hoc identity, ephemeral Ed25519 key, loopback feed; "
              "not a published release, migration between different source versions, Gatekeeper or TCC validation",
              "source": provenance["source_commit"], "original_tree_sha256": original, "cases": {}}
    root = Path(tempfile.mkdtemp(prefix="cc-update-fixture-"))
    cleanup_safe = True
    try:
        root.chmod(0o700)
        signer, driver = compile_tools(app, root)
        key_file, wrong_file = root / "temporary-key", root / "wrong-key"
        try:
            key = json_command([signer, "generate", key_file])["public_key"]
            wrong = json_command([signer, "generate", wrong_file])["public_key"]
            with fixture_server() as server:
                for scenario in SCENARIOS:
                    report["cases"][scenario] = run_case(
                        app, root, server, signer, driver, key_file, key, wrong, scenario, output)
                report["loopback_requests"] = server.requests
        finally:
            for path in (key_file, wrong_file):
                if path.exists():
                    path.unlink()
            report["temporary_keys_removed"] = not key_file.exists() and not wrong_file.exists()
        report["original_bundle_unchanged"] = tree_digest(app) == original
        assert report["original_bundle_unchanged"]
        report["status"] = "passed"
    except FixtureCleanupError:
        cleanup_safe = False
        report["retained_fixture_directory"] = str(root)
        raise
    finally:
        if cleanup_safe:
            shutil.rmtree(root)
        report["fixture_directory_removed"] = not root.exists()
        (output / "update-fixture.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = run(arguments.app, arguments.output)
    print(json.dumps({"status": result["status"], "cases": list(result["cases"]),
                      "scope": result["scope"]}, indent=2))
