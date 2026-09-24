import copy
import json
import math
from pathlib import Path
import plistlib
import queue
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.macos import translation_benchmark as benchmark


def expected_result():
    return {"output": "Synthetic translation 中🙂", "kind": "text", "target_lang": "zh"}


def completion(**changes):
    return {
        "text": expected_result()["output"], "submitted": True, "cached": False,
        "kind": "text", "target_lang": "zh", "summarize": False,
        "history": "disabled", "history_error": None,
    } | changes


def event(kind, payload, sequence=0, identifier="translate"):
    return {"v": 1, "id": identifier, "type": kind, "payload": payload, "seq": sequence}


def completed_report():
    report = {"sessions": [], "samples": []}
    for round_index, case, role, order in benchmark.schedule(5, 2):
        report["sessions"].append({
            "round": round_index, "case": case, "role": role, "order": order,
            "helper_start_to_ready_ms": 3, "helper_clean_exit": True, "cleanup_verified": True,
            "prewarm_advertised": role == "candidate",
        })
        for index in range(3):
            prepared = index > 0 and role == "candidate"
            first, end = (20, 230) if role == "baseline" else (10, 220)
            preparation = 5 if prepared else 0
            report["samples"].append({
                "round": round_index, "case": case, "role": role, "order": order, "request_index": index,
                "phase": "cold" if index == 0 else "retained_helper",
                "first_delta_ms": first, "completed_ms": end, "preparation_ms": preparation,
                "preparation_plus_first_delta_ms": first + preparation,
                "preparation_plus_completed_ms": end + preparation,
                "output_sha256": "output", "prompt_sha256": "prompt",
                "submitted": True, "cached": False, "prewarm_used": prepared,
            })
    return report


class TranslationBenchmarkStatisticsTests(unittest.TestCase):
    def test_nearest_rank_p95_and_median_keep_raw_pair_sign(self):
        distribution = benchmark.summarize(list(range(1, 101)))
        self.assertEqual(distribution, {
            "count": 100, "median_ms": 50.5, "p95_ms": 95, "min_ms": 1, "max_ms": 100,
        })
        self.assertEqual(benchmark.summarize([-10, -2, 0, 5, 20], signed=True)["median_ms"], 0)
        self.assertEqual(benchmark.summarize([1, 2, 3, 4, 5])["p95_ms"], 5)
        for values in ([], [True], [-1], [math.nan], [math.inf], ["1"]):
            with self.subTest(values=values), self.assertRaises(benchmark.BenchmarkError):
                benchmark.summarize(values)

    def test_default_schedule_has_five_cold_and_ten_retained_per_role_and_corpus(self):
        plan = benchmark.schedule(5, 2)
        self.assertEqual(len(plan), 20)
        for case in benchmark.CORPUS:
            for role in benchmark.ROLES:
                self.assertEqual(sum(row[1:3] == (case, role) for row in plan), 5)
        for case in benchmark.CORPUS:
            first_roles = [role for _, name, role, order in plan if name == case and order == 0]
            self.assertTrue(all(left != right for left, right in zip(first_roles, first_roles[1:])))
        report = completed_report()
        self.assertEqual(len(report["samples"]), 60)
        result = benchmark.aggregate(report, 5, 2)
        self.assertEqual(len(result["pairs"]), 30)
        cold = result["summary"]["short"]["cold"]["first_delta_ms"]
        warm = result["summary"]["long"]["retained_helper"]["first_delta_ms"]
        self.assertEqual(cold["baseline"]["count"], 5)
        self.assertEqual(warm["candidate"]["count"], 10)
        self.assertEqual(warm["paired_candidate_minus_baseline"]["median_ms"], -10)
        inclusive = result["summary"]["long"]["retained_helper"]["preparation_plus_first_delta_ms"]
        self.assertEqual(inclusive["paired_candidate_minus_baseline"]["median_ms"], -5)

    def test_bounded_repetitions_cannot_be_reduced_to_one_sample_or_unbounded(self):
        for rounds, warm in ((1, 2), (4, 2), (11, 2), (5, 0), (5, 1), (5, 6), (True, 2)):
            with self.subTest(rounds=rounds, warm=warm), self.assertRaises(benchmark.BenchmarkError):
                benchmark.schedule(rounds, warm)

    def test_coverage_order_cleanup_duplicates_and_partial_runs_are_not_success(self):
        mutations = [
            lambda value: value["sessions"].pop(),
            lambda value: value["sessions"].reverse(),
            lambda value: value["sessions"][0].update(helper_clean_exit=False),
            lambda value: value["sessions"][0].update(cleanup_verified=False),
            lambda value: value["samples"].pop(),
            lambda value: value["samples"].append(value["samples"][0]),
            lambda value: value["samples"][0].update(output_sha256="different"),
            lambda value: value["samples"][0].update(prompt_sha256="different"),
            lambda value: value["samples"][0].update(submitted=False),
            lambda value: value["samples"][0].update(cached=True),
            lambda value: value["samples"][0].update(phase="retained_helper"),
            lambda value: value["samples"][0].update(prewarm_used=True),
            lambda value: value["samples"][4].update(prewarm_used=False),
            lambda value: value["samples"][0].update(order=1),
            lambda value: value["samples"][0].update(first_delta_ms=500),
            lambda value: value["samples"][0].update(preparation_ms=math.nan),
            lambda value: value["samples"][0].update(preparation_plus_completed_ms=1),
        ]
        for mutation in mutations:
            report = completed_report()
            mutation(report)
            with self.subTest(mutation=mutation), self.assertRaises(benchmark.BenchmarkError):
                benchmark.aggregate(report, 5, 2)

    def test_public_corpus_is_bounded_english_and_not_summary_or_dictionary(self):
        self.assertLess(len(benchmark.CORPUS["short"]), 120)
        self.assertGreater(len(benchmark.CORPUS["long"]), 3000)
        for text in benchmark.CORPUS.values():
            self.assertTrue(text.isascii())
            self.assertLessEqual(len(text.encode("utf-8")), 8192)
            self.assertGreater(len(text.split()), 5)
        for setting in ("history_enabled", "summary_enabled", "local_dictionary_enabled"):
            self.assertIs(benchmark.CONFIG[setting], False)
        self.assertEqual(benchmark.CONFIG["codex_model"], "synthetic")
        self.assertIs(benchmark.CONFIG["codex_streaming_experimental"], True)


class TranslationBenchmarkProtocolTests(unittest.TestCase):
    def test_strict_envelope_rejects_duplicate_nonfinite_and_unknown_events(self):
        valid = event("delta", {"text": "synthetic", "submitted": True})
        self.assertEqual(benchmark.decode_event(json.dumps(valid).encode() + b"\n"), valid)
        malformed = [
            b'{"v":1,"v":1}\n', b'{"x":NaN}\n', b'[]\n', b'{}\n', b'\xff\n',
            json.dumps(valid).encode(), b"x" * (benchmark.MAX_FRAME + 1) + b"\n",
        ]
        for change in ({"v": True}, {"seq": True}, {"seq": -1}, {"id": "../private"},
                       {"type": "unknown"}, {"payload": []}, {"extra": 1}):
            malformed.append(json.dumps(valid | change).encode() + b"\n")
        for raw in malformed:
            with self.subTest(raw=raw[:70]), self.assertRaises(benchmark.BenchmarkError):
                benchmark.decode_event(raw)

    def test_handshake_requires_native_service_and_only_advertised_prewarm(self):
        ready = {
            "protocol": 1, "capabilities": ["translate", "config_save"],
            "max_frame_bytes": benchmark.MAX_FRAME, "fixture": False, "backend": "native_appserver",
        }
        self.assertFalse(benchmark.validate_ready(ready))
        self.assertTrue(benchmark.validate_ready(ready | {"capabilities": ready["capabilities"] + ["prewarm"]}))
        for change in ({"fixture": True}, {"backend": "fixture"}, {"protocol": True},
                       {"capabilities": ["config_load"]}, {"capabilities": ["translate", "translate"]},
                       {"max_frame_bytes": 1}):
            with self.subTest(change=change), self.assertRaises(benchmark.BenchmarkError):
                benchmark.validate_ready(ready | change)

    def test_baseline_uninstrumented_results_and_candidate_timings_are_distinct(self):
        self.assertIsNone(benchmark.validate_completed(completion(), expected_result()))
        timings = {"cache_hit": 0, "helper_elapsed_ms": 230, "warm_process_hit": 1}
        self.assertEqual(benchmark.validate_completed(completion(timings=timings), expected_result()), timings)
        for changes in ({"submitted": False}, {"cached": True}, {"summarize": True}, {"history": "recorded"},
                        {"text": "wrong"}, {"submitted": 1}, {"history_error": "invalid_history"},
                        {"extra": 1}, {"timings": {}}, {"timings": timings | {"account": 1}},
                        {"timings": timings | {"cache_hit": 1}},
                        {"timings": timings | {"total_ms": math.nan}},
                        {"timings": timings | {"warm_process_hit": True}}):
            with self.subTest(changes=changes), self.assertRaises(benchmark.BenchmarkError):
                benchmark.validate_completed(completion(**changes), expected_result())

    def translation_session(self, events):
        session = Mock()
        session.send.return_value = 1_000_000
        session.receive.side_effect = events
        fixture = {"request": {"use_cache": False, "record_history": False}, "expected": expected_result()}
        return session, fixture

    def test_first_nonempty_delta_is_timed_and_all_output_is_verified(self):
        text = expected_result()["output"]
        session, fixture = self.translation_session([
            (event("delta", {"text": "", "submitted": True}), 2_000_000),
            (event("delta", {"text": text[:5], "submitted": True}), 3_000_000),
            (event("delta", {"text": text[5:], "submitted": True}), 4_000_000),
            (event("completed", completion()), 7_000_000),
        ])
        result = benchmark.translate(session, "translate", fixture)
        self.assertEqual(result["first_delta_ms"], 2)
        self.assertEqual(result["completed_ms"], 6)
        self.assertIsNone(result["provider_timings"])
        self.assertEqual(session.send.call_count, 1)
        self.assertEqual([call.args[1] for call in session.expect.call_args_list], ["accepted", "started"])

    def test_missing_delta_inconsistent_result_or_unsubmitted_delta_fail_without_retry(self):
        good_delta = (event("delta", {"text": expected_result()["output"], "submitted": True}), 3_000_000)
        good_end = (event("completed", completion()), 7_000_000)
        cases = [
            [good_end],
            [(event("delta", {"text": "wrong", "submitted": True}), 3_000_000), good_end],
            [(event("delta", {"text": "", "submitted": True}), 3_000_000), good_end],
            [(event("delta", {"text": "wrong", "submitted": False}), 3_000_000), good_end],
            [good_delta, (event("completed", completion(cached=True)), 7_000_000)],
            [good_delta, (event("started", {}), 7_000_000)],
            [good_delta, (event("completed", completion()), 2_000_000)],
        ]
        for events in cases:
            session, fixture = self.translation_session(events)
            with self.subTest(events=events), self.assertRaises(benchmark.BenchmarkError):
                benchmark.translate(session, "translate", fixture)
            self.assertEqual(session.send.call_count, 1)

    def test_control_prewarm_is_content_free_and_awaits_its_terminal(self):
        session = Mock()
        session.send.return_value = 1_000_000
        session.expect.side_effect = [({}, 2_000_000), ({}, 3_000_000), ({"warmed": True}, 4_000_000)]
        duration = benchmark.control(session, "prepare", {"operation": "prewarm", "app_language": "en_US"},
                                     {"warmed": True})
        self.assertEqual(duration, 3)
        self.assertEqual(session.send.call_args.args[2], {"operation": "prewarm", "app_language": "en_US"})
        self.assertEqual([call.args[1] for call in session.expect.call_args_list],
                         ["accepted", "started", "completed"])

    def wire_session(self, events):
        session = benchmark.Session.__new__(benchmark.Session)
        session.events = queue.Queue()
        session.errors, session.sequences, session.terminals = [], {"translate": 0}, set()
        session.stdout_done = threading.Event()
        for item in events:
            session.events.put((item, 1))
        return session

    def test_pipe_sequence_ids_terminal_and_failed_results_are_strict(self):
        import time
        for item in (event("accepted", {}, 1), event("accepted", {}, identifier="other"),
                     event("failed", {"code": "provider_version_unsupported"}), event("cancelled", {})):
            session = self.wire_session([item])
            with self.subTest(item=item), self.assertRaises(benchmark.BenchmarkError):
                session.receive("translate", time.monotonic() + 1)
        session = self.wire_session([event("completed", {}), event("delta", {}, 1)])
        session.receive("translate", time.monotonic() + 1)
        with self.assertRaisesRegex(benchmark.BenchmarkError, "terminal"):
            session.receive("translate", time.monotonic() + 1)

    def test_normal_shutdown_drains_and_unexpected_late_output_fails(self):
        session = benchmark.Session.__new__(benchmark.Session)
        session.process = Mock()
        session.process.wait.return_value = 0
        session.threads = [Mock()]
        session.threads[0].is_alive.return_value = False
        session.events, session.errors, session.stderr_bytes = queue.Queue(), [], 0
        session.send, session.expect = Mock(), Mock()
        session.finish()
        session.send.assert_called_once_with("shutdown", "shutdown", {})
        session.process.stdin.close.assert_called_once()
        session.process.terminate.assert_not_called()
        session.process.kill.assert_not_called()
        session.events.put(event("delta", {}))
        with self.assertRaisesRegex(benchmark.BenchmarkError, "late"):
            session.finish()


class TranslationBenchmarkIdentityTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix=".translation-benchmark-test-", dir=Path.cwd())
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.app = self.root / "Synthetic.app"
        resources = self.app / "Contents" / "Resources"
        core = resources / "Core"
        (core / "cc_macos").mkdir(parents=True)
        for relative in ("launch.py", "cc_macos/translation.py", "cc_macos/server.py",
                         "cc_macos/translation_fixture.py", "cc_macos/native_provider_fixture.py"):
            (core / relative).write_text("# synthetic identity only\n", encoding="utf-8")
        python = resources / "python" / "bin" / "python3"
        python.parent.mkdir(parents=True)
        python.write_text("not executable; never launch on host", encoding="utf-8")
        self.info = {"CFBundleVersion": "202", "CFBundleIdentifier": "dev.synthetic.benchmark"}
        self.manifest = {
            "source_commit": benchmark.BASELINE_SOURCE, "source_tree_dirty": False,
            "application": {"build": "202", "bundle_identifier": self.info["CFBundleIdentifier"],
                            "architecture": "arm64"},
            "resource_hashes": {path.relative_to(self.app / "Contents").as_posix(): benchmark.sha256(path)
                                for path in core.rglob("*.py")},
        }
        self.save()

    def save(self):
        (self.app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(self.info))
        (self.app / "Contents" / "Resources" / "source-manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8")

    def test_immutable_full_source_build_inventory_and_runtime_bytes_are_checked(self):
        identity = benchmark.app_identity(self.app, benchmark.BASELINE_SOURCE, "202")
        self.assertEqual(identity["source"], benchmark.BASELINE_SOURCE)
        self.assertEqual(identity["build"], "202")
        self.assertEqual(identity["resource_files_verified"], 5)
        python = self.app / "Contents" / "Resources" / "python" / "bin" / "python3"
        python.write_text("changed runtime bytes", encoding="utf-8")
        self.assertNotEqual(benchmark.app_identity(self.app, benchmark.BASELINE_SOURCE, "202")["tree_sha256"],
                            identity["tree_sha256"])
        (self.app / "Contents" / "Resources" / "Core" / "launch.py").write_text("changed source")
        with self.assertRaisesRegex(benchmark.BenchmarkError, "hash"):
            benchmark.app_identity(self.app, benchmark.BASELINE_SOURCE, "202")

    def test_wrong_source_build_dirty_tree_or_missing_fixture_inventory_are_rejected(self):
        original = copy.deepcopy(self.manifest)
        changes = [
            {"source_commit": benchmark.CANDIDATE_SOURCE},
            {"source_tree_dirty": True},
            {"application": self.manifest["application"] | {"build": "206"}},
            {"application": self.manifest["application"] | {"architecture": "x86_64"}},
            {"resource_hashes": {}},
            {"resource_hashes": self.manifest["resource_hashes"] | {"Resources/../../outside": "fake"}},
        ]
        for change in changes:
            self.manifest = original | change
            self.save()
            with self.subTest(change=change), self.assertRaises(benchmark.BenchmarkError):
                benchmark.app_identity(self.app, benchmark.BASELINE_SOURCE, "202")
        self.manifest = original
        self.info["CFBundleVersion"] = "206"
        self.save()
        with self.assertRaisesRegex(benchmark.BenchmarkError, "build"):
            benchmark.app_identity(self.app, benchmark.BASELINE_SOURCE, "202")

    def test_cli_pins_defaults_and_no_platform_bypass_option(self):
        args = benchmark.parser().parse_args(
            ["--baseline-app", str(self.app), "--candidate-app", "Candidate.app", "--report", "report.json"])
        self.assertEqual((args.baseline_source, args.baseline_build), (benchmark.BASELINE_SOURCE, "202"))
        self.assertEqual((args.candidate_source, args.candidate_build), (benchmark.CANDIDATE_SOURCE, "206"))
        self.assertEqual((args.rounds, args.warm_requests), (5, 2))
        self.assertNotIn("--allow-host", benchmark.parser().format_help())
        if sys.platform != "darwin":
            with self.assertRaisesRegex(benchmark.BenchmarkError, "Darwin arm64"):
                benchmark.run(args)
            self.assertFalse((self.root / "report.json").exists())

    def test_clean_environment_does_not_copy_host_credentials_or_python_paths(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-test", "ANTHROPIC_API_KEY": "synthetic-test",
                                      "PYTHONPATH": "synthetic-host-modules", "CODEX_HOME": "private"}):
            environment = benchmark.clean_environment(self.root / "home", self.root)
        self.assertEqual(set(environment), {"PATH", "HOME", "TMPDIR", "LANG"})
        self.assertEqual(environment["HOME"], str(self.root / "home"))
        self.assertEqual(environment["TMPDIR"], str(self.root))

    def test_worker_command_uses_app_python_isolation_and_bundled_core_only(self):
        # This tests command construction, not native performance. No fake Darwin
        # platform and no host execution of the Darwin-only fixture are used.
        response = SimpleNamespace(returncode=0, stdout=b'{"synthetic":true}', stderr=b"")
        with patch.object(benchmark.subprocess, "run", return_value=response) as run:
            result = benchmark.worker(self.app, self.root, "prepare", self.root / "fixture",
                                      self.info["CFBundleIdentifier"], "short")
        self.assertEqual(result, {"synthetic": True})
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [
            str(self.app / "Contents" / "Resources" / "python" / "bin" / "python3"), "-I", "-B", "-c"])
        self.assertIs(command[4], benchmark.WORKER)
        self.assertEqual(run.call_args.kwargs["cwd"], self.root)
        self.assertEqual(run.call_args.kwargs["timeout"], 30)
        compile(benchmark.WORKER, "<bundled benchmark worker>", "exec")
        self.assertIn('sys.path.insert(0, str(core))', benchmark.WORKER)
        self.assertIn('require_cleanup=True', benchmark.WORKER)
        self.assertNotIn("sys.platform =", benchmark.WORKER)
        self.assertNotIn("time.sleep =", benchmark.WORKER)
        self.assertNotIn("warm_up =", benchmark.WORKER)

    def test_worker_failure_or_diagnostics_have_no_fallback_and_do_not_leak_output(self):
        for response in (
            SimpleNamespace(returncode=1, stdout=b"synthetic-private", stderr=b"synthetic-private"),
            SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"synthetic-private"),
        ):
            with patch.object(benchmark.subprocess, "run", return_value=response) as run:
                with self.assertRaises(benchmark.BenchmarkError) as error:
                    benchmark.worker(self.app, self.root, "prepare", self.root / "fixture",
                                     self.info["CFBundleIdentifier"], "short")
                self.assertNotIn("synthetic-private", str(error.exception))
                run.assert_called_once()

    def test_worker_failure_reports_safe_fixture_code_without_traceback_or_private_paths(self):
        response = SimpleNamespace(
            returncode=1, stdout=b"",
            stderr=b'Traceback: synthetic-private-path\nRuntimeError: unexpected_submission_count_or_cleanup\n',
        )
        with patch.object(benchmark.subprocess, "run", return_value=response):
            with self.assertRaisesRegex(benchmark.BenchmarkError, "unexpected_submission_count_or_cleanup") as error:
                benchmark.worker(self.app, self.root, "verify", self.root / "fixture",
                                 self.info["CFBundleIdentifier"], "short")
        self.assertNotIn("synthetic-private-path", str(error.exception))
        self.assertIn("exit=1", str(error.exception))

    def prepared_fixture(self):
        root = self.root / "native"
        home = root / "cache" / "home"
        home.mkdir(parents=True)
        (root / "home").mkdir()
        command = root / "synthetic-cli"
        import shlex
        python = self.app / "Contents" / "Resources" / "python" / "bin" / "python3"
        native = self.app / "Contents" / "Resources" / "Core" / "cc_macos" / "native_provider_fixture.py"
        command.write_text("#!/bin/sh\nexec " + shlex.quote(str(python)) + " -I -B "
                           + shlex.quote(str(native)) + ' "$@"\n', encoding="utf-8")
        fixture = {
            "root": str(root), "home": str(home), "command": str(command),
            "config": dict(benchmark.CONFIG),
            "request": {"operation": "translate", "text": benchmark.CORPUS["short"], "app_language": "en_US",
                        "origin": "text", "use_cache": False, "record_history": False},
            "expected": expected_result() | {"model": "synthetic", "task": "text", "stream": True,
                                             "summarize": False, "prompt": "synthetic prompt"},
            "environment": {"PATH": "/usr/bin:/bin", "HOME": str(home), "CODEX_HOME": str(root / "home"),
                            "TMPDIR": str(home), "CC_SYNTHETIC_ROOT": str(root),
                            "CC_SYNTHETIC_MODE": "translation_normal"},
        }
        return {"fixture": fixture, "runtime": {
            "platform": "darwin", "machine": "arm64", "isolated": True,
            "bytecode_disabled": True, "bundle_modules": True,
        }}

    def test_fixture_guard_rejects_real_cli_real_model_credentials_cache_and_host_modules(self):
        prepared = self.prepared_fixture()
        self.assertIs(benchmark.validate_fixture(prepared, self.root, self.app, "short"), prepared["fixture"])
        mutations = [
            lambda value: value["fixture"]["environment"].update(OPENAI_API_KEY="synthetic-test"),
            lambda value: value["fixture"]["environment"].update(PATH="/opt/homebrew/bin:/usr/bin"),
            lambda value: value["fixture"]["environment"].update(CODEX_HOME=str(self.root.parent)),
            lambda value: value["fixture"]["request"].update(use_cache=True),
            lambda value: value["fixture"]["request"].update(record_history=True),
            lambda value: value["fixture"]["config"].update(summary_enabled=True),
            lambda value: value["fixture"]["expected"].update(model="a-real-model"),
            lambda value: value["fixture"]["expected"].update(stream=False),
            lambda value: value["runtime"].update(bundle_modules=False),
            lambda value: value["runtime"].update(platform="win32"),
        ]
        for mutation in mutations:
            invalid = copy.deepcopy(prepared)
            mutation(invalid)
            with self.subTest(mutation=mutation), self.assertRaises(benchmark.BenchmarkError):
                benchmark.validate_fixture(invalid, self.root, self.app, "short")
        Path(prepared["fixture"]["command"]).write_text("#!/bin/sh\nexec codex \"$@\"\n", encoding="utf-8")
        with self.assertRaisesRegex(benchmark.BenchmarkError, "bundle"):
            benchmark.validate_fixture(prepared, self.root, self.app, "short")

    def test_real_runner_plan_prewarms_only_candidate_after_cold_and_records_preparation(self):
        prepared = self.prepared_fixture()
        identity = {"bundle_identifier": self.info["CFBundleIdentifier"]}
        for role, advertised in (("baseline", False), ("candidate", True), ("candidate", False)):
            with self.subTest(role=role, advertised=advertised):
                directory = self.root / f"session-{role}-{advertised}"
                session = Mock(started_ns=1)
                session.expect.return_value = ({
                    "protocol": 1, "capabilities": ["translate", "config_save"] + (["prewarm"] if advertised else []),
                    "max_frame_bytes": benchmark.MAX_FRAME, "fixture": False, "backend": "native_appserver",
                }, 2)
                order = []
                count = 0

                def control(_session, _identifier, payload, _expected):
                    order.append(payload["operation"])
                    return 4

                def translate(_session, _identifier, _fixture):
                    nonlocal count
                    count += 1
                    order.append("translate")
                    return {"first_delta_ms": 10, "completed_ms": 220, "submitted": True, "cached": False,
                            "output_sha256": "output", "provider_timings": None}

                verify = {"cleanup_verified": True, "submitted_turns": 3}
                report = {"sessions": [], "samples": [], "errors": []}
                with patch.object(benchmark, "worker", side_effect=[prepared, verify]), \
                        patch.object(benchmark, "validate_fixture", return_value=prepared["fixture"]), \
                        patch.object(benchmark, "Session", return_value=session) as create, \
                        patch.object(benchmark, "control", side_effect=control), \
                        patch.object(benchmark, "translate", side_effect=translate), \
                        patch.object(benchmark, "submissions", side_effect=lambda _: (count, count)):
                    benchmark.run_session(self.app, identity, directory, "short", role, 0, 0, 2, report)
                expected_order = (["config_save", "translate", "prewarm", "translate", "prewarm", "translate"]
                                  if advertised and role == "candidate"
                                  else ["config_save", "translate", "translate", "translate"])
                self.assertEqual(order, expected_order)
                samples = report["samples"]
                self.assertEqual(samples[0]["preparation_ms"], 0)
                self.assertEqual(samples[0]["phase"], "cold")
                self.assertEqual(samples[1]["preparation_ms"], 4 if advertised else 0)
                self.assertEqual(samples[1]["preparation_plus_completed_ms"], 224 if advertised else 220)
                self.assertTrue(report["sessions"][0]["helper_clean_exit"])
                self.assertTrue(report["sessions"][0]["cleanup_verified"])
                self.assertEqual(report["sessions"][0]["stage"], "passed")
                session.finish.assert_called_once()
                session.dispose.assert_called_once()
                self.assertEqual(set(create.call_args.args[2]),
                                 {"PATH", "HOME", "TMPDIR", "LANG", "CC_TRANSLATE_CODEX_ENV"})

    def test_failed_translation_is_not_retried_and_cleanup_does_not_hide_original_error(self):
        prepared = self.prepared_fixture()
        session = Mock(started_ns=1)
        session.expect.return_value = ({
            "protocol": 1, "capabilities": ["translate", "config_save"],
            "max_frame_bytes": benchmark.MAX_FRAME, "fixture": False, "backend": "native_appserver",
        }, 2)
        report = {"sessions": [], "samples": [], "errors": []}
        with patch.object(benchmark, "worker", side_effect=[
                prepared, benchmark.BenchmarkError("cleanup count mismatch")]) as worker, \
                patch.object(benchmark, "validate_fixture", return_value=prepared["fixture"]), \
                patch.object(benchmark, "Session", return_value=session), \
                patch.object(benchmark, "control", return_value=4), \
                patch.object(benchmark, "submissions", return_value=(0, 0)), \
                patch.object(benchmark, "translate", side_effect=benchmark.BenchmarkError("original failure")) as call:
            with self.assertRaisesRegex(benchmark.BenchmarkError, "original failure"):
                benchmark.run_session(self.app, {"bundle_identifier": self.info["CFBundleIdentifier"]},
                                      self.root / "failed-session", "short", "baseline", 0, 0, 2, report)
        call.assert_called_once()
        self.assertEqual(worker.call_count, 2)
        session.finish.assert_not_called()
        session.dispose.assert_called_once()
        self.assertEqual(report["samples"], [])
        self.assertEqual(report["errors"], ["session cleanup verification: cleanup count mismatch"])
        self.assertEqual(report["sessions"][0]["stage"], "translate-0")


if __name__ == "__main__":
    unittest.main()
