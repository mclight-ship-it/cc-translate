import copy
import itertools
import json
from pathlib import Path
import plistlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cc_classify import classify_selection
from tools.macos import bundle, performance
from tools.macos import performance_metrics as metrics


class PerformanceMetricsTests(unittest.TestCase):
    def test_fixed_corpus_exercises_text_code_mixed_and_long_input(self):
        self.assertEqual({expected for _, _, expected in metrics.CLASSIFICATION_CASES}, {"text", "code", "mixed"})
        self.assertGreater(len(metrics.CLASSIFICATION_CASES[-1][1]), 5000)
        for name, text, expected in metrics.CLASSIFICATION_CASES:
            with self.subTest(name=name):
                self.assertEqual(classify_selection(text), expected)

    def test_nearest_rank_p95_is_not_maximum_or_average(self):
        samples = list(reversed(range(1, 101)))
        result = metrics.summarize(samples, 95)
        self.assertEqual(result["samples_ms"], samples)
        self.assertEqual(result["p95_ms"], 95)
        self.assertEqual(result["max_ms"], 100)
        self.assertEqual(result["target_result"], "met")
        self.assertEqual(metrics.summarize(samples, 94)["target_result"], "measured_miss")
        self.assertEqual(metrics.summarize([7], 5)["p95_ms"], 7)

    def test_invalid_numbers_never_become_successful_measurements(self):
        for values in ([], [True], [float("nan")], [float("inf")], [-1], ["1"]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                metrics.summarize(values, 2)
        for target in (True, 0, -1, float("nan"), float("inf")):
            with self.subTest(target=target), self.assertRaises(ValueError):
                metrics.summarize([1], target)

    def test_first_warmup_and_measured_calls_are_separate_and_all_validated(self):
        operation, validation = Mock(return_value="result"), Mock()
        ticks = iter(itertools.count(0, 1_000_000))
        result = metrics.measure(operation, validation, 2, clock=lambda: next(ticks))
        self.assertEqual(operation.call_count, 1 + metrics.WARMUPS + metrics.SAMPLES)
        self.assertEqual(validation.call_count, operation.call_count)
        self.assertEqual(result["first_ms"], 1)
        self.assertEqual(result["warm"]["samples_ms"], [1] * metrics.SAMPLES)
        metrics.validate_measurement(result, 2)

    def test_bad_results_and_invalid_elapsed_time_fail_without_fallback(self):
        with self.assertRaisesRegex(ValueError, "bad result"):
            metrics.measure(lambda: None, Mock(side_effect=ValueError("bad result")), 2)
        ticks = iter(itertools.count(0, -1))
        with self.assertRaises(ValueError):
            metrics.measure(lambda: None, lambda value: None, 2, clock=lambda: next(ticks))

    def test_reported_statistics_cannot_disagree_with_raw_samples(self):
        value = {"first_ms": 2, "warmup_count": metrics.WARMUPS,
                 "warm": metrics.summarize([1] * metrics.SAMPLES, 2)}
        for key, invalid in (("p95_ms", 99), ("count", 1), ("target_result", "measured_miss")):
            changed = copy.deepcopy(value)
            changed["warm"][key] = invalid
            with self.subTest(key=key), self.assertRaises(ValueError):
                metrics.validate_measurement(changed, 2)


class PerformanceIntegrationTests(unittest.TestCase):
    source = "a" * 40

    def measurement(self, target):
        return {"first_ms": 3, "warmup_count": metrics.WARMUPS,
                "warm": metrics.summarize([target + 1] * metrics.SAMPLES, target)}

    def worker_report(self):
        return {"source_sha": self.source,
                "classification": {name: self.measurement(2) for name, _, _ in metrics.CLASSIFICATION_CASES},
                "dictionary": {query: self.measurement(10) for query in metrics.DICTIONARY_CASES}}

    def test_missing_corpus_source_or_cleanup_cannot_be_reported_passed(self):
        report = self.worker_report() | {
            "resident_ipc": self.measurement(5), "helper_clean_exit": True,
            "temporary_home_removed": True, "bundle_unchanged": True,
        }
        performance.validate_report(report, self.source)
        for changes in ({"source_sha": "b" * 40}, {"classification": {}}, {"dictionary": {}},
                        {"helper_clean_exit": False}, {"temporary_home_removed": False},
                        {"bundle_unchanged": False}):
            with self.subTest(changes=changes), self.assertRaises(bundle.BundleError):
                performance.validate_report(report | changes, self.source)

    def test_runner_uses_bundled_worker_read_only_ipc_and_always_disposes(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "fixture.app"
            resources = app / "Contents/Resources"
            resources.mkdir(parents=True)
            (resources / "source-manifest.json").write_text(json.dumps({"source_commit": self.source}))
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "dev.synthetic.test"}))
            asset = Path(temporary) / "dictionary.sqlite3"
            process = SimpleNamespace(stdout=json.dumps(self.worker_report()), stderr="")
            session = Mock()
            session.expect.side_effect = lambda identifier, event: (
                {"protocol": 1, "capabilities": ["config_load"]} if event == "ready"
                else {"config": {"font_size": 16}} if event == "completed" else {})
            with patch.object(performance.sys, "platform", "darwin"), \
                    patch.object(performance.platform, "machine", return_value="arm64"), \
                    patch.object(performance.subprocess, "run", return_value=process) as run, \
                    patch.object(performance.smoke, "Session", return_value=session) as create:
                result = performance.run_measurements(app / ".." / app.name, asset)
                self.assertFalse(result["targets_are_gates"])
                self.assertEqual(result["dictionary"]["hello"]["warm"]["target_result"], "measured_miss")
                self.assertEqual(run.call_args.args[0][0], str(app.resolve() / "Contents/Resources/python/bin/python3"))
                self.assertEqual(run.call_args.args[0][1:3], ["-I", "-B"])
                self.assertIn("--config-home", create.call_args.args[0])
                sent = session.send.call_args_list
                self.assertEqual(sent[0].args, ("hello", "hello", {}))
                self.assertEqual(len(sent), 2 + metrics.WARMUPS + metrics.SAMPLES)
                self.assertTrue(all(call.args[2] == {"operation": "config_load"} for call in sent[1:]))
                session.finish.assert_called_once()
                session.dispose.assert_called_once()
                session.finish.side_effect = RuntimeError("cleanup failed")
                with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                    performance.run_measurements(app, asset)
                self.assertEqual(session.dispose.call_count, 2)

    def test_host_platform_is_not_native_performance_evidence(self):
        with patch.object(performance.sys, "platform", "win32"), \
                self.assertRaises(bundle.BundleError):
            performance.run_measurements(Path("synthetic.app"), Path("synthetic.sqlite3"))

    def test_workflow_runs_and_retains_measurements_on_producer_and_consumers(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/macos-p0.yml").read_text(encoding="utf-8")
        runtime = (root / "tools/macos/runtime_matrix.py").read_text(encoding="utf-8")
        self.assertIn("tests.test_macos_performance", workflow)
        self.assertIn("python3 -B tools/macos/performance.py --app", workflow)
        self.assertIn("tools/macos/.build/performance.json", workflow)
        self.assertIn("tools/macos/.build/runtime-evidence/performance.json", workflow)
        self.assertIn('report["performance"] = performance.run_measurements(app, Path(asset))', runtime)


if __name__ == "__main__":
    unittest.main()
