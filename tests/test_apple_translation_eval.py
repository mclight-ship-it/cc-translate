import ast
from contextlib import redirect_stdout
import io
import json
import math
from pathlib import Path
import plistlib
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch
import uuid

from tools.macos import apple_translation_eval as evaluation


def report_fixture():
    """Schema unit-test fixture ONLY, never written as a real evaluation result."""
    report = evaluation.new_report("unit-test-not-a-real-run")
    report["metadata"].update(
        host_system="Darwin", host_architecture="arm64", sw_vers="unit-test-only",
        xcode="unit-test-only", sdk_version="unit-test-only", swift="unit-test-only",
        swift_source_sha256="unit-test-only")
    runtime = {
        "run_id": report["run_id"], "engine": "Apple.Translation.TranslationSession",
        "strategy": "system_default", "os_version": "unit-test-only",
        "status": "completed", "supported_languages": ["en", "zh-Hans"],
        "pairs": [], "outputs": [], "cancellations": [],
    }
    for pair in evaluation.PAIR_IDS:
        runtime["pairs"].append({
            "id": pair, "before": "supported", "after": "installed",
            "ready_before_requests": "installed", "prepare_ms": 130,
            "availability_before_ms": 4, "availability_after_ms": 3,
        })
        for repeat in (0, 1):
            for index, case in enumerate(row for row in evaluation.corpus() if row["pair"] == pair):
                runtime["outputs"].append({
                    "case_id": case["id"], "pair": pair, "pass": repeat, "status": "ok",
                    "phase": "first_call_session" if repeat == index == 0 else "retained_session",
                    "elapsed_ms": 200, "since_app_start_ms": 1200,
                    "source_text": case["source"], "target_text": "UNIT TEST PLACEHOLDER",
                })
        runtime["cancellations"].append({
            "pair": pair, "mechanism": "swift_task_cancel", "outcome": "cancelled",
            "cancel_requested": True, "cancel_requested_ms": 20, "elapsed_ms": 23,
            "target_text": "",
        })
    report["runtime"] = runtime
    report["summary"].update(status="completed", completed_outputs=56)
    return report


class CorpusTests(unittest.TestCase):
    def test_balanced_original_corpus_and_manual_rubric(self):
        cases = evaluation.corpus()
        self.assertEqual(len(cases), 28)
        self.assertEqual(len({case["id"] for case in cases}), 28)
        for pair in evaluation.PAIR_IDS:
            rows = [case for case in cases if case["pair"] == pair]
            self.assertEqual(len(rows), 14)
            self.assertTrue(all(case["source"] and case["reference"] and case["rubric"] for case in rows))
            tags = {tag for case in rows for tag in case["tags"]}
            self.assertTrue({"negation", "numbers", "units", "terminology", "mixed", "format",
                             "code", "idiom", "long"} <= tags)
            long_case = next(case for case in rows if "long" in case["tags"])
            self.assertEqual(len(long_case["source"].split("\n\n")), 5)
            self.assertGreater(len(long_case["source"]), 350)
        self.assertEqual(evaluation.manifest(), evaluation.manifest())
        self.assertEqual(len(evaluation.manifest()["sha256"]), 64)
        self.assertIn("do not recommend disabling summary-first", evaluation.manifest()["purpose"])
        self.assertIn("not code explanation", evaluation.manifest()["code_scope"])

    def test_bilingual_references_are_reversed_but_not_sent_to_model(self):
        cases = evaluation.corpus()
        for left, right in zip(cases[::2], cases[1::2]):
            self.assertEqual(left["source"], right["reference"])
            self.assertEqual(right["source"], left["reference"])
        swift = evaluation.SOURCE.read_text(encoding="utf-8")
        self.assertIn("session.translate(item.source)", swift)
        self.assertNotIn("let reference:", swift)


class DownloadTextTests(unittest.TestCase):
    def inspection(self):
        return {
            "pid": 4321, "window_id": 71,
            "bounds": {"X": 100, "Y": 200, "Width": 640, "Height": 332},
            "image_width": 1280, "image_height": 664,
            "texts": [{"text": text, "confidence": 1, "x": 0.75, "y": 0.5}
                      for text in ("Download Languages to Translate", "English (US)",
                                   "Chinese (Mandarin, Simplified)", "Download", "Done")],
        }

    def test_observed_exact_download_in_scoped_language_sheet(self):
        result = evaluation.download_text_target(self.inspection(), 4321, 71)
        self.assertEqual(result, ("Download", (580, 366), (100, 200, 640, 332)))
        data = self.inspection()
        data["texts"] = [row for row in data["texts"] if row["text"] != "Download"]
        self.assertEqual(evaluation.download_text_target(data, 4321, 71)[0], "Done")

    def test_unrelated_low_confidence_or_ambiguous_text_never_clicks(self):
        for change in (
            lambda d: d["texts"].pop(0),
            lambda d: d["texts"].pop(1),
            lambda d: d["texts"][3].update(confidence=0.2),
        ):
            data = self.inspection()
            data["texts"] = [row for row in data["texts"] if row["text"] != "Done"]
            change(data)
            self.assertIsNone(evaluation.download_text_target(data, 4321, 71))

    def test_wrong_owner_shadow_or_invalid_geometry_is_rejected(self):
        for change in (
            lambda d: d.update(pid=1),
            lambda d: d.update(window_id=1),
            lambda d: d.update(image_height=1000),
            lambda d: d["bounds"].update(Width=0),
            lambda d: d["texts"][3].update(x=math.nan),
            lambda d: d["texts"][3].update(y=2),
        ):
            data = self.inspection()
            change(data)
            with self.assertRaises(evaluation.EvalError):
                evaluation.download_text_target(data, 4321, 71)

    def test_visual_click_rechecks_foreground_owner_and_window_geometry(self):
        replies = ["", "", json.dumps(self.inspection()), "clicked_observed_download"]
        with patch.object(evaluation, "command", side_effect=replies) as execute:
            result = evaluation.click_observed_download(
                Path("app"), Path("public-output"), {"window_id": 71},
                4321, 1, evaluation.new_report("unit-test"), {})
        self.assertEqual(result, "clicked_observed_download")
        self.assertEqual(execute.call_args_list[1].args[0][1:6], ["-x", "-o", "-l", "71",
                         str(Path("public-output") / "download-ui-01.png")])
        script = execute.call_args_list[-1].args[0][-1]
        self.assertIn("unix id is 4321", script)
        self.assertIn("if not frontmost", script)
        self.assertIn('whose name is "Apple Translation Evaluation"', script)
        self.assertIn("if position of ownedWindow is not {100, 200}", script)
        self.assertIn("if size of ownedWindow is not {640, 332}", script)
        data = self.inspection()
        data["texts"][3]["confidence"] = 0.2
        self.assertIsNone(evaluation.download_text_target(data, 4321, 71))


class ReportTests(unittest.TestCase):
    def test_complete_schema_does_not_assign_quality(self):
        report = report_fixture()
        evaluation.validate_report(report, require_complete=True)
        self.assertEqual(next(iter(report)), "summary")
        self.assertIsNone(report["summary"]["quality_score"])
        # An empty output is evidence to assess, not an automatically fabricated quality verdict.
        report["runtime"]["outputs"][0]["target_text"] = ""
        evaluation.validate_report(report, require_complete=True)

    def test_missing_forged_or_unbounded_measurements_rejected(self):
        changes = [
            lambda r: r.update(schema_version=2),
            lambda r: r["corpus"]["cases"].pop(),
            lambda r: r["summary"].update(quality_score=1),
            lambda r: r["runtime"].update(engine="a mock provider"),
            lambda r: r["runtime"].update(run_id="another-run"),
            lambda r: r["metadata"].pop("sdk_version"),
            lambda r: r["metadata"].update(host_architecture="x86_64"),
            lambda r: r["runtime"]["outputs"].pop(),
            lambda r: r["runtime"]["outputs"].append(r["runtime"]["outputs"][0]),
            lambda r: r["runtime"]["outputs"][0].update(elapsed_ms=-1),
            lambda r: r["runtime"]["outputs"][0].update(elapsed_ms=math.nan),
            lambda r: r["runtime"]["outputs"][0].update(elapsed_ms=True),
            lambda r: r["runtime"]["outputs"][0].update(target_text=None),
            lambda r: r["runtime"]["outputs"][0].update(source_text="wrong source"),
            lambda r: r["runtime"]["outputs"][1].update(phase="first_call_session"),
            lambda r: r["runtime"]["outputs"].reverse(),
            lambda r: r["runtime"]["pairs"][0].update(before="assumed"),
            lambda r: r["runtime"]["pairs"][0].update(ready_before_requests="supported"),
            lambda r: r["runtime"].update(supported_languages=[]),
            lambda r: r["runtime"]["cancellations"].pop(),
            lambda r: r["runtime"]["cancellations"][0].update(cancel_requested=False),
            lambda r: r["summary"].update(completed_outputs=99),
        ]
        for mutate in changes:
            with self.subTest(mutation=changes.index(mutate)):
                report = report_fixture()
                mutate(report)
                with self.assertRaises(evaluation.EvalError):
                    evaluation.validate_report(report, require_complete=True)

    def test_blocked_run_keeps_error_and_actual_partial_output(self):
        report = report_fixture()
        report["summary"].update(status="blocked", completed_outputs=1)
        report["runtime"].update(status="blocked", blocked_condition="language_preparation_deadline:zh-en")
        report["runtime"]["outputs"] = report["runtime"]["outputs"][:2]
        report["runtime"]["outputs"][1].update(status="error", error={"domain": "example", "code": 5})
        evaluation.validate_report(report)
        report["runtime"] = None
        evaluation.validate_report(report)
        with self.assertRaises(evaluation.EvalError):
            evaluation.validate_report(report, require_complete=True)

    def test_cancellation_race_never_claims_cancelled_when_response_won(self):
        report = report_fixture()
        row = report["runtime"]["cancellations"][0]
        row.update(cancel_requested=False, outcome="completed_before_cancel", target_text="unit test")
        row.pop("cancel_requested_ms")
        evaluation.validate_report(report)
        row.update(outcome="completed_after_cancel")
        with self.assertRaises(evaluation.EvalError):
            evaluation.validate_report(report)

    def test_readiness_only_reports_cannot_contain_translations(self):
        for status in ("availability_only", "prepared"):
            report = report_fixture()
            report["summary"].update(status=status, completed_outputs=0)
            report["runtime"].update(status=status, outputs=[], cancellations=[])
            evaluation.validate_report(report)
            report["runtime"]["outputs"] = report_fixture()["runtime"]["outputs"][:1]
            with self.assertRaises(evaluation.EvalError):
                evaluation.validate_report(report)


class WorkingDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.root = evaluation.HERE / "apple_translation_eval" / (".test-work-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)

    def args(self, *extra):
        return evaluation.parser().parse_args(["--output", str(self.root / "artifacts"), *extra])

    def test_corpus_only_is_portable_and_not_an_evaluation(self):
        with redirect_stdout(io.StringIO()):
            code = evaluation.main(["--output", str(self.root / "corpus"), "--corpus-only"])
        self.assertEqual(code, 0)
        result = json.loads((self.root / "corpus" / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(result["summary"]["status"], "corpus_only")
        self.assertIsNone(result["runtime"])
        self.assertEqual(result["commands"], [])
        evaluation.validate_report(result)

    def test_availability_and_preparation_are_explicit_separate_modes(self):
        self.assertEqual(self.args().mode, "evaluate")
        self.assertEqual(self.args("--availability-only").mode, "availability_only")
        self.assertEqual(self.args("--prepare-only").mode, "prepare_only")
        for mode, flag, terminal in (("availability_only", "--availability-only", "availability_only"),
                                     ("prepare_only", "--prepare-only", "prepared")):
            report = report_fixture()
            runtime = report["runtime"]
            runtime.update(status=terminal, outputs=[], cancellations=[])
            evaluation.write_json(self.root / "app-state.json", runtime)
            report["runtime"] = None
            process = Mock(pid=123, returncode=0)
            process.poll.return_value = 0
            with patch.object(evaluation.subprocess, "Popen", return_value=process):
                evaluation.run_app(self.root / "app", self.root, self.args(flag), report, {})
            request = json.loads((self.root / "input.json").read_text(encoding="utf-8"))
            self.assertEqual(request["mode"], mode)
            self.assertEqual(report["summary"]["status"], terminal)
            self.assertEqual(report["summary"]["completed_outputs"], 0)
            evaluation.validate_report(report)

    def test_all_deadlines_are_bounded_and_existing_output_is_not_overwritten(self):
        for name, (minimum, maximum) in evaluation.LIMITS.items():
            for number in (minimum - 1, maximum + 1):
                with self.subTest(name=name, number=number), self.assertRaises(evaluation.EvalError):
                    evaluation.validate_args(self.args(f"--{name}-timeout", str(number)))
        evaluation.validate_args(self.args())
        args = self.args()
        args.output.mkdir()
        (args.output / "existing.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(evaluation.EvalError):
            evaluation.validate_args(args)
        self.assertEqual((args.output / "existing.txt").read_text(encoding="utf-8"), "keep")
        with self.assertRaises(evaluation.EvalError):
            evaluation.validate_args(self.args("--cancel-delay-ms", "-1"))

    def test_real_build_commands_have_no_package_dependencies_or_provider_calls(self):
        report = evaluation.new_report("build-test")
        results = ["macOS 15.5", "Xcode 16.4", "15.5", "SDK", "Swift version 6.1", "", ""]
        with patch.object(evaluation.sys, "platform", "darwin"), \
                patch.object(evaluation.platform, "machine", return_value="arm64"), \
                patch.object(evaluation.platform, "mac_ver", return_value=("15.5", (), "")), \
                patch.object(evaluation, "command", side_effect=results) as execute:
            binary = evaluation.build_app(self.root, self.args(), report, {})
        self.assertEqual(execute.call_count, 7)
        compile_command = execute.call_args_list[-2].args[0]
        self.assertEqual(compile_command[:4], ["/usr/bin/xcrun", "--sdk", "macosx", "swiftc"])
        self.assertIn("arm64-apple-macos15.0", compile_command)
        self.assertIn("Translation", compile_command)
        self.assertIn(str(evaluation.SOURCE), compile_command)
        self.assertEqual(execute.call_args_list[-1].args[0][0], "/usr/bin/codesign")
        info = plistlib.loads((binary.parent.parent / "Info.plist").read_bytes())
        self.assertEqual(info["CFBundleExecutable"], evaluation.APP_NAME)
        self.assertFalse(info["LSUIElement"])
        self.assertEqual(info["LSMinimumSystemVersion"], "15.0")

    def test_unsupported_host_still_saves_durable_blocked_report(self):
        with patch.object(evaluation.sys, "platform", "win32"), \
                patch.object(evaluation.subprocess, "run") as run, redirect_stdout(io.StringIO()):
            status = evaluation.main(["--output", str(self.root / "unsupported")])
        self.assertEqual(status, 2)
        run.assert_not_called()
        result = json.loads((self.root / "unsupported" / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(result["summary"]["blocked_condition"], "requires_macos_arm64")
        self.assertEqual(result["summary"]["status"], "blocked")
        evaluation.validate_report(result)

    def test_build_failure_saves_bounded_command_diagnostic(self):
        report = evaluation.new_report("failure-test")
        failure = subprocess.CompletedProcess(["swiftc"], 1, "compile", "failed")
        with patch.object(evaluation.subprocess, "run", return_value=failure) as run, \
                self.assertRaises(evaluation.EvalError):
            evaluation.command(["swiftc"], 17, report, log=self.root / "build.log")
        self.assertEqual(run.call_args.kwargs["timeout"], 17)
        self.assertEqual((self.root / "build.log").read_text(encoding="utf-8"), "compilefailed")
        self.assertEqual(report["commands"][0]["returncode"], 1)
        with patch.object(evaluation.subprocess, "run", side_effect=subprocess.TimeoutExpired("xcrun", 3)), \
                self.assertRaises(evaluation.EvalError):
            evaluation.command(["xcrun"], 3, report)
        self.assertTrue(report["commands"][-1]["timed_out"])

    def test_owned_app_launch_input_and_checkpoint_validation(self):
        report = report_fixture()
        state_path = self.root / "app-state.json"
        evaluation.write_json(state_path, report["runtime"])
        report["runtime"] = None
        process = Mock(pid=123, returncode=0)
        process.poll.return_value = 0
        binary = self.root / "Application.app" / "Contents" / "MacOS" / evaluation.APP_NAME
        with patch.object(evaluation.subprocess, "Popen", return_value=process) as launch:
            evaluation.run_app(binary, self.root, self.args(), report, {})
        self.assertEqual(launch.call_args.args[0], [str(binary), str(self.root / "input.json")])
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        request = json.loads((self.root / "input.json").read_text(encoding="utf-8"))
        self.assertEqual(request["cases"], evaluation.corpus())
        self.assertEqual(request["prepare_timeout"], 300)
        self.assertEqual(report["summary"]["status"], "completed")
        evaluation.validate_report(report, require_complete=True)
        with self.assertRaises(evaluation.EvalError):
            evaluation.load_runtime(state_path, "different-run")

    def test_startup_deadline_stops_only_owned_child_and_reports_exact_stage(self):
        report = evaluation.new_report("timeout-test")
        process = Mock(pid=123, returncode=-15)
        process.poll.return_value = None
        with patch.object(evaluation.subprocess, "Popen", return_value=process), \
                patch.object(evaluation.time, "monotonic", side_effect=[0, 21, 22]), \
                self.assertRaisesRegex(evaluation.EvalError, "app_did_not_publish_initial_checkpoint"):
            evaluation.run_app(self.root / "app", self.root, self.args(), report, {})
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=3)
        self.assertTrue(report["commands"][0]["timed_out"])

    def test_overall_deadline_preserves_partial_app_state_and_owned_window(self):
        report = report_fixture()
        runtime = report["runtime"]
        runtime.update(status="running", phase="preparing_languages", window_id=71)
        runtime["outputs"] = runtime["outputs"][:1]
        evaluation.write_json(self.root / "app-state.json", runtime)
        report["runtime"] = None
        report["summary"].update(status="running", completed_outputs=0)
        process = Mock(pid=123, returncode=-15)
        process.poll.return_value = None
        with patch.object(evaluation.subprocess, "Popen", return_value=process), \
                patch.object(evaluation.time, "monotonic", side_effect=[0, 31, 32]), \
                patch.object(evaluation, "capture_window") as capture:
            evaluation.run_app(self.root / "app", self.root,
                               self.args("--run-timeout", "30", "--screenshot-on-block"), report, {})
        self.assertEqual(report["summary"]["status"], "blocked")
        self.assertEqual(report["summary"]["blocked_condition"], "overall_deadline:preparing_languages")
        self.assertEqual(report["summary"]["completed_outputs"], 1)
        self.assertTrue(capture.called)
        self.assertEqual(report["runtime"]["outputs"], runtime["outputs"])
        evaluation.validate_report(report)

    def test_language_permission_error_is_not_reported_as_success(self):
        report = evaluation.new_report("permission-test")
        runtime = {
            "run_id": report["run_id"], "engine": "Apple.Translation.TranslationSession",
            "strategy": "system_default", "os_version": "unit-test-only", "status": "blocked",
            "phase": "blocked", "blocked_condition": "prepare_translation_error:en-zh",
            "pairs": [{"id": "en-zh", "before": "supported", "after": "supported",
                       "prepare_ms": 32, "preparation_error": {"domain": "TranslationError", "code": 1}}],
            "outputs": [], "cancellations": [],
        }
        evaluation.write_json(self.root / "app-state.json", runtime)
        process = Mock(pid=123, returncode=2)
        process.poll.return_value = 2
        with patch.object(evaluation.subprocess, "Popen", return_value=process):
            evaluation.run_app(self.root / "app", self.root, self.args(), report, {})
        self.assertEqual(report["summary"]["status"], "blocked")
        self.assertEqual(report["summary"]["blocked_condition"], "prepare_translation_error:en-zh")
        self.assertEqual(report["summary"]["completed_outputs"], 0)
        evaluation.validate_report(report)

    def test_unresponsive_child_escalates_to_pid_kill_with_finite_wait(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("app", 3), 0]
        evaluation.stop_owned_process(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual([call.kwargs["timeout"] for call in process.wait.call_args_list], [3, 3])

    def test_automation_and_screenshot_never_target_global_desktop_or_consent(self):
        script = evaluation.download_script(4321)
        self.assertIn("unix id is 4321", script)
        self.assertIn('"Download"', script)
        self.assertIn("entire contents of ownedWindow", script)
        self.assertIn("description of node", script)
        self.assertIn("value of node", script)
        self.assertIn("entire contents of node", script)
        self.assertIn("set childNodes to get entire contents of node", script)
        self.assertNotIn("repeat with labelNode in (entire contents", script)
        self.assertIn("(count nodes) > 512", script)
        self.assertIn('textValue is "Download Languages to Translate"', script)
        self.assertIn("if languageSheet and doneButton is not missing value then", script)
        for forbidden in ('"Allow"', '"OK"', "TCC.db", "sudo", "keystroke"):
            self.assertNotIn(forbidden, script)
        with self.assertRaises(evaluation.EvalError):
            evaluation.download_script("all")
        report = evaluation.new_report("screenshot-test")
        with patch.object(evaluation, "command") as execute:
            evaluation.capture_window(self.root, {"window_id": 71}, report)
        argv = execute.call_args.args[0]
        self.assertEqual(argv[:4], ["/usr/sbin/screencapture", "-x", "-l", "71"])
        with patch.object(evaluation, "command") as execute:
            evaluation.capture_window(self.root, None, report)
        execute.assert_not_called()


class IsolationTests(unittest.TestCase):
    def test_only_known_automation_lifecycle_errors_are_retryable(self):
        for code in ("(-1719)", "(-10000)"):
            self.assertTrue(evaluation.retryable_automation_error(
                evaluation.EvalError("command_failed:osascript:1"), code))
        self.assertTrue(evaluation.retryable_automation_error(
            evaluation.EvalError("command_timeout:osascript"), ""))
        for output in ("Not authorized (-1743)", "AX access denied (-25211)", "unknown failure"):
            self.assertFalse(evaluation.retryable_automation_error(
                evaluation.EvalError("command_failed:osascript:1"), output))
        self.assertFalse(evaluation.retryable_automation_error(OSError("missing executable"), ""))

    def test_direct_child_launch_owns_window_and_starts_from_app_delegate(self):
        swift = evaluation.SOURCE.read_text(encoding="utf-8")
        for required in ("NSApplicationDelegate", "applicationDidFinishLaunching",
                         "NSWindow(contentRect:", "NSHostingView(rootView:",
                         "window.makeKeyAndOrderFront(nil)",
                         "Task { @MainActor in await evaluation.begin() }",
                         "withExtendedLifetime(delegate) { application.run() }"):
            self.assertIn(required, swift)
        self.assertNotIn("WindowGroup(", swift)
        self.assertIn("guard !terminal else { return }", swift)
        self.assertIn('phase("blocked")\n            terminal = true', swift)

    def test_only_standard_library_imports_and_public_apple_translation(self):
        source = Path(evaluation.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        self.assertFalse(any(name.startswith(("cc_", "requests", "urllib", "http", "socket"))
                             for name in imports))
        swift = evaluation.SOURCE.read_text(encoding="utf-8")
        for required in ("LanguageAvailability()", "languages.supportedLanguages",
                         "languages.status(from:", ".translationTask(", "session.prepareTranslation()",
                         "session.translate(", "work.cancel()", "ready_before_requests"):
            self.assertIn(required, swift)
        for forbidden in ("URLSession", "Process()", "dlopen(", "dlsym(", "session.cancel()",
                          "highFidelity", "lowLatency", "TCC.db", "NSAppleScript"):
            self.assertNotIn(forbidden, swift)
        self.assertIn("case .unsupported:", swift)
        self.assertIn("while statusName(readiness)", swift)
        self.assertIn("Task.sleep", swift)
        self.assertIn("unknown; no model unload", swift)


if __name__ == "__main__":
    unittest.main()
