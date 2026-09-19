import base64
from copy import deepcopy
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from tools.macos import update_fixture as fixture


class SignedUpdateFixtureTests(unittest.TestCase):
    def report(self, scenario):
        result = {
            "scenario": scenario, "graceful_cleanup": True, "cleanup_complete": True,
            "session_in_progress": False, "events": ["launched-original", "found", "download"],
            "offered_build": "2", "original_pid": 101, "running_pids_before_cleanup": [101],
            "original_terminated": False, "installed_build": "1", "outcome": scenario,
            "installed_identifier": fixture.PREFIX + "unique", "expected_identifier": fixture.PREFIX + "unique",
        }
        if scenario == "install":
            result.update(outcome="installed", installed_build="2", relaunched=True,
                          original_terminated=True, running_pids_before_cleanup=[102])
            result["events"] += ["ready-to-install", "installed"]
        elif scenario in ("bad-signature", "wrong-key", "download-error"):
            result.update(outcome="error", errors=[{
                "domain": "SUSparkleErrorDomain", "code": 2001 if scenario == "download-error" else 3001
            }])
            result["events"].append("error")
        elif scenario == "cancel-install":
            result["events"].append("ready-to-install")
        return result

    def test_fixture_identity_and_loopback_exceptions_do_not_mutate_product_info(self):
        original = {"CFBundleIdentifier": "dev.cc-translate.macos.probe", "CFBundleVersion": "154",
                    "SUEnableAutomaticChecks": False}
        saved = deepcopy(original)
        key = base64.b64encode(bytes(32)).decode()
        result = fixture.fixture_info(original, fixture.PREFIX + "unique", "http://127.0.0.1:1234/feed", key, "1")
        self.assertEqual(original, saved)
        self.assertEqual(result["CFBundleIdentifier"], fixture.PREFIX + "unique")
        self.assertTrue(result["SUVerifyUpdateBeforeExtraction"])
        self.assertFalse(result["SUEnableAutomaticChecks"])
        self.assertNotIn("NSAppTransportSecurity", original)
        self.assertEqual(result["SUPublicEDKey"], key)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "original.app"
            destination = Path(directory) / "fixture.app"
            contents = source / "Contents"
            contents.mkdir(parents=True)
            info = contents / "Info.plist"
            info.write_bytes(plistlib.dumps(original))
            runtime = contents / "Helpers/python/lib/python3.12"
            runtime.mkdir(parents=True)
            (runtime / "sentinel.py").write_bytes(b"preserved runtime")
            before = fixture.tree_digest(source)
            with patch.object(fixture, "command") as signing:
                fixture.configure_copy(source, destination, fixture.PREFIX + "unique",
                                       "http://127.0.0.1:1234/feed", key, "1")
            self.assertEqual(fixture.tree_digest(source), before)
            self.assertEqual(plistlib.loads((destination / "Contents/Info.plist").read_bytes()), result)
            self.assertEqual((destination / runtime.relative_to(source) / "sentinel.py").read_bytes(),
                             b"preserved runtime")
            self.assertEqual([call.args[0] for call in signing.call_args_list], [
                ["/usr/bin/codesign", "--force", "--sign", "-", destination],
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", destination],
            ])

    def test_only_disposable_identity_local_feed_and_real_key_shape_are_allowed(self):
        key = base64.b64encode(bytes(32)).decode()
        arguments = [{}, fixture.PREFIX + "unique", "http://127.0.0.1:1234/feed", key, "1"]
        for index, values in (
            (1, ("dev.cc-translate.macos.probe", fixture.PREFIX, fixture.PREFIX + "../escape")),
            (2, ("https://example.invalid/feed", "file:///tmp/feed", "http://127.0.0.1/feed",
                 "http://localhost:1234/feed")),
            (3, (base64.b64encode(bytes(31)).decode(), "not base64")),
            (4, ("0", "154", "")),
        ):
            for value in values:
                changed = list(arguments)
                changed[index] = value
                with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                    fixture.fixture_info(*changed)

    def test_generated_feed_carries_exact_version_signature_size_and_escaped_url(self):
        signature = base64.b64encode(bytes(64)).decode()
        url = "http://127.0.0.1:1234/update.zip?first=1&second=2"
        root = ElementTree.fromstring(fixture.appcast(url, signature, 123))
        item = root.find("channel/item")
        ns = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"
        self.assertEqual(item.find(ns + "version").text, "2")
        enclosure = item.find("enclosure")
        self.assertEqual(enclosure.attrib["url"], url)
        self.assertEqual(enclosure.attrib[ns + "edSignature"], signature)
        self.assertEqual(enclosure.attrib["length"], "123")

    def test_each_scenario_requires_its_actual_terminal_outcome(self):
        for scenario in fixture.SCENARIOS:
            report = self.report(scenario)
            fixture.verify_case(scenario, report, {"retained": 1}, {"retained": 1})
            report["outcome"] = "unrelated failure"
            with self.subTest(scenario=scenario), self.assertRaises(AssertionError):
                fixture.verify_case(scenario, report, {}, {})

    def test_success_requires_replacement_process_exit_relaunch_and_new_build(self):
        for key, value in (
            ("relaunched", False), ("original_terminated", False), ("installed_build", "1"),
            ("running_pids_before_cleanup", [101]), ("running_pids_before_cleanup", []),
            ("installed_identifier", fixture.PREFIX + "different"),
        ):
            report = self.report("install")
            report[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(AssertionError):
                fixture.verify_case("install", report, {}, {})

    def test_invalid_signature_cannot_pass_for_a_transport_or_configuration_failure(self):
        for scenario in ("bad-signature", "wrong-key"):
            for code in (1, 1002, 2001, 3002):
                report = self.report(scenario)
                report["errors"][0]["code"] = code
                with self.subTest(scenario=scenario, code=code), self.assertRaises(AssertionError):
                    fixture.verify_case(scenario, report, {}, {})

    def test_no_case_can_pass_with_changed_data_pending_session_or_forced_cleanup(self):
        for scenario in fixture.SCENARIOS:
            report = self.report(scenario)
            with self.assertRaises(AssertionError):
                fixture.verify_case(scenario, report, {"retained": 1}, {"retained": 2})
            for key, value in (("graceful_cleanup", False), ("cleanup_complete", False),
                               ("session_in_progress", True)):
                changed = deepcopy(report)
                changed[key] = value
                with self.subTest(scenario=scenario, key=key), self.assertRaises(AssertionError):
                    fixture.verify_case(scenario, changed, {}, {})

    def test_cleanup_signals_only_ledger_pids_after_exact_executable_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report.json"
            app = root / "CCTranslate.app"
            report.write_text(json.dumps({"owned_pids": [321]}))
            expected = str((app / "Contents/MacOS/CCTranslateMac").resolve()).encode()
            alive = subprocess.CompletedProcess([], 0, expected, b"")
            gone = subprocess.CompletedProcess([], 1, b"", b"")
            with patch.object(fixture.subprocess, "run", side_effect=[alive, gone]), \
                 patch.object(fixture.os, "kill") as kill, patch.object(fixture.time, "sleep"):
                fixture.stop_fixture_processes(report, app)
                kill.assert_called_once_with(321, fixture.signal.SIGTERM)
            unrelated = subprocess.CompletedProcess([], 0, str(root / "another-process").encode(), b"")
            with patch.object(fixture.subprocess, "run", return_value=unrelated), \
                 patch.object(fixture.os, "kill") as kill:
                with self.assertRaises(fixture.FixtureCleanupError):
                    fixture.stop_fixture_processes(report, app)
                kill.assert_not_called()
            report.write_text(json.dumps({"owned_pids": [], "launch_requested": True}))
            with self.assertRaises(fixture.FixtureCleanupError):
                fixture.stop_fixture_processes(report, app)
