"""Real bundled helper -> native CLI -> configuration/history translation tests."""

import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

if sys.platform != "darwin":
    raise RuntimeError("Translation IPC process tests require bundled Darwin Python; not a host substitute.")

from cc_macos import native_provider_fixture, translation, translation_fixture
from cc_macos import protocol
from cc_storage import macos_user_paths
from state_ipc_process_support import StateIPCProcessCase


HELPER_SCRIPT = r"""
import json, os, signal, sys, threading
from pathlib import Path
sys.path.insert(0, sys.argv[1])
home, identity, mode, command, environment, barriers = sys.argv[2:]
signal.alarm(40)
os.environ["CC_TRANSLATE_CODEX_ENV"] = environment
def barrier():
    with (Path(barriers) / "entered.fifo").open("wb", buffering=0) as stream:
        stream.write(b"x")
    with (Path(barriers) / "release.fifo").open("rb", buffering=0) as stream:
        if stream.read(1) != b"x":
            raise RuntimeError("synthetic release missing")
if mode == "prestart":
    actual_run = threading.Thread.run
    def controlled_run(thread):
        if thread.name == "cc-macos-translation":
            barrier()
        actual_run(thread)
    threading.Thread.run = controlled_run
if mode == "committing":
    from cc_macos.translation import TranslationSession
    actual_record = TranslationSession._record
    def controlled_record(self, *args):
        barrier()
        return actual_record(self, *args)
    TranslationSession._record = controlled_record
from cc_macos.server import main
raise SystemExit(main(["--config-home", home, "--application-id", identity,
                      "--codex-command", command]))
"""


class TestTranslationIPCProcess(StateIPCProcessCase):
    bundle_modules = StateIPCProcessCase.bundle_modules + (
        translation, translation_fixture, native_provider_fixture)
    owner_script = HELPER_SCRIPT

    def test_newer_cli_versions_complete_with_validated_catalog_and_history(self):
        for index, version in enumerate(("0.147.0", "0.154.0", "1.0.0")):
            with self.subTest(version=version):
                if index:
                    self.prepare()
                (self.root / "version-output.bin").write_bytes(
                    ("Warning runtime 99.0.0 SYNTHETIC_PRIVATE\ncodex-cli " + version + "+build\n").encode())
                process = self.start()
                self.request(process)
                self.assert_completed(self.terminal(process, "translation"))
                self.assertEqual(len(self.turns()), 1)
                self.assertEqual(len(self.history(process)), 1)
                states = list(self.home.rglob("state.json"))
                self.assertEqual(len(states), 1)
                self.assertEqual(json.loads(states[0].read_bytes())["version"], version)
                self.stop(process)
                self.assert_native_gone()

    def test_version_failures_are_distinct_before_submission_without_retry(self):
        cases = ((b"codex-cli 0.145.0", "provider_version_unsupported"),
                 (b"codex-cli 0.147.0-rc.1", "provider_version_prerelease"),
                 (b"runtime 0.146.0 SYNTHETIC_PRIVATE", "provider_version_unreadable"),
                 (b"\xff", "provider_version_unreadable"),
                 (b"codex-cli 0.146.0\ncodex-cli 0.147.0", "provider_version_unreadable"))
        for index, (output, code) in enumerate(cases):
            with self.subTest(code=code, index=index):
                if index:
                    self.prepare()
                (self.root / "version-output.bin").write_bytes(output)
                process = self.start()
                self.request(process)
                result = self.terminal(process, "translation")
                self.assertEqual(result["type"], "failed")
                self.assertEqual(result["payload"], {"code": code, "submitted": False})
                frames = [event for pid, event in self.events
                          if pid == process.pid and event["id"] == "translation"]
                self.assertEqual([(event["type"], event["seq"]) for event in frames],
                                 [("accepted", 0), ("started", 1), ("failed", 2)])
                self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(result))
                self.assertEqual(self.turns(), [])
                self.assertFalse((self.root / "native-rpc.jsonl").exists())
                self.assertFalse(self.history_path.exists())
                self.assertEqual(len((self.root / "version.jsonl").read_text().splitlines()), 1)
                self.stop(process)
                self.assert_native_gone()

    def test_corrected_version_only_runs_after_a_new_explicit_request(self):
        output = self.root / "version-output.bin"
        output.write_bytes(b"not a version SYNTHETIC_PRIVATE")
        process = self.start()
        self.request(process, id_="unreadable")
        failure = self.terminal(process, "unreadable")
        self.assertEqual(failure["payload"],
                         {"code": "provider_version_unreadable", "submitted": False})
        output.write_bytes(b"codex-cli 0.154.0")
        time.sleep(0.1)
        self.assertEqual(self.turns(), [])
        self.assertEqual(len((self.root / "version.jsonl").read_text().splitlines()), 1)
        self.request(process, id_="corrected")
        self.assert_completed(self.terminal(process, "corrected"))
        self.assertEqual(len(self.turns()), 1)
        self.assertEqual(len((self.root / "version.jsonl").read_text().splitlines()), 2)
        self.stop(process)
        self.assert_native_gone()

    def setUp(self):
        super().setUp()
        self.barriers = self.home
        self.fixture_index = 0
        self.prepare()

    def prepare(self, scenario="normal", *, result_action=None):
        self.fixture_index += 1
        self.fixture = translation_fixture.prepare(
            self.barriers / ("translation-" + str(self.fixture_index)), self.identity, scenario,
            result_action=result_action)
        self.home = Path(self.fixture["home"])
        self.root = Path(self.fixture["root"])
        self.directory = macos_user_paths(self.home, self.identity).application_support
        self.path, self.history_path = self.directory / "config.json", self.directory / "history.json"
        self.mode = "translation"

    def process_arguments(self):
        return (self.core, self.home, self.identity, self.mode, self.fixture["command"],
                json.dumps(self.fixture["environment"]), self.barriers)

    def terminal(self, process, id_):
        for _ in range(14_000):
            event = self.receive(process)
            if event["id"] == id_ and event["type"] in {"completed", "failed", "cancelled", "ready"}:
                return event
        self.fail("Translation response exceeded the bounded fixture event count.")

    def rpc(self):
        path = self.root / "native-rpc.jsonl"
        return [] if not path.exists() else [json.loads(line) for line in path.read_text().splitlines()]

    def turns(self):
        return [row for row in self.rpc()
                if row["kind"] == "provider" and row["request"]["method"] == "turn/start"]

    def no_cli(self):
        for name in ("calls.jsonl", "version.jsonl", "native-processes.jsonl", "native-rpc.jsonl"):
            self.assertFalse((self.root / name).exists(), name)

    def configure(self, process, **changes):
        self.send_message(process, "save", "request", operation="config_save",
                          config={**self.fixture["config"], **changes})
        self.assertEqual(self.terminal(process, "save")["payload"], {"saved": True})

    def start(self, *, configure=True):
        process = self.spawn()
        ready = self.hello(process)
        self.assertEqual(ready["type"], "ready")
        self.assertIs(ready["payload"]["fixture"], False)
        self.assertEqual(ready["payload"]["backend"], "native_appserver")
        self.assertIn("translate", ready["payload"]["capabilities"])
        self.assertIn("result_action", ready["payload"]["capabilities"])
        if configure:
            self.configure(process)
        return process

    def request(self, process, id_="translation", **changes):
        self.send_message(process, id_, "request", **{**self.fixture["request"], **changes})

    def until_delta(self, process, id_="translation"):
        self.assertEqual(self.receive(process)["type"], "accepted")
        self.assertEqual(self.receive(process)["type"], "started")
        event = self.receive(process)
        self.assertEqual((event["id"], event["type"]), (id_, "delta"))
        self.assertEqual(event["payload"], {"text": self.fixture["expected"]["output"], "submitted": True})
        self.assertEqual(len(self.turns()), 1)
        return event

    def history(self, process, id_="history"):
        self.send_message(process, id_, "request", operation="history_load", page_size=100, cursor=None)
        result = self.terminal(process, id_)
        self.assertEqual(result["type"], "completed")
        return result["payload"]["entries"]

    def assert_completed(self, event, *, cached=False, history="recorded"):
        expected = self.fixture["expected"]
        self.assertEqual(event["type"], "completed")
        self.assertEqual(event["payload"], {
            "text": expected["output"], "submitted": not cached, "cached": cached,
            "kind": expected["kind"], "target_lang": expected["target_lang"],
            "summarize": expected["summarize"], "history": history, "history_error": None,
        })

    def assert_native_gone(self, *, descendant=False):
        rows = []
        for name in ("native-processes.jsonl", "calls.jsonl", "version.jsonl"):
            path = self.root / name
            if path.exists():
                rows.extend(json.loads(line) for line in path.read_text().splitlines())
        self.assertTrue(rows, "Native process receipts are required, not a fake service result.")
        groups = {row["group"] for row in rows}
        for row in rows:
            self.assertEqual(row["pid"], row["group"])
            self.assertEqual(row["pid"], row["session"])
        children = [json.loads(path.read_bytes()) for path in (self.root / "children").glob("*.json")]
        if descendant:
            self.assertTrue(children)
        for child in children:
            self.assertIn(child["group"], groups)
        known_children = {child["pid"] for child in children}
        deadline = time.monotonic() + 5
        while True:
            result = subprocess.run(
                ["/bin/ps", "-axo", "pid=,ppid=,pgid=,stat="],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=True, timeout=2)
            self.assertEqual(result.stderr, b"")
            live = [line.split() for line in result.stdout.decode("ascii").splitlines() if line.strip()]
            owned = [row for row in live if int(row[2]) in groups]
            if all(int(row[0]) in known_children and int(row[1]) == 1 and row[3].startswith("Z")
                   for row in owned):
                return
            if time.monotonic() >= deadline:
                self.fail("A receipt-owned native group remains live after helper drain.")
            time.sleep(0.02)

    def stop(self, process):
        self.send_message(process, "shutdown", "shutdown")
        self.assertEqual(self.terminal(process, "shutdown")["payload"], {})
        self.finish_helper(process)

    def test_hello_has_no_cli_then_configuration_snapshot_stream_history_cache_and_reopen(self):
        process = self.start(configure=False)
        self.no_cli()
        self.assertFalse(self.path.exists())
        self.configure(process)
        self.no_cli()
        self.request(process)
        self.assert_completed(self.terminal(process, "translation"))
        events = [event for pid, event in self.events if pid == process.pid and event["id"] == "translation"]
        self.assertEqual([event["type"] for event in events], ["accepted", "started", "delta", "completed"])
        self.assertEqual([event["seq"] for event in events], [0, 1, 2, 3])
        self.assertEqual(self.turns()[0]["request"]["params"]["input"],
                         [{"type": "text", "text": self.fixture["expected"]["prompt"]}])
        entries = self.history(process)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["input"], self.fixture["request"]["text"])
        self.assertEqual(entries[0]["output"], self.fixture["expected"]["output"])
        self.assertEqual(entries[0]["sig"], self.fixture["expected"]["signature"])
        stored = self.history_path.read_bytes()
        self.request(process, "cached")
        self.assert_completed(self.terminal(process, "cached"), cached=True, history="unchanged")
        self.assertEqual(self.history_path.read_bytes(), stored)
        self.assertEqual(len(self.turns()), 1)
        self.stop(process)
        self.assert_native_gone()
        reopened = self.start(configure=False)
        self.request(reopened)
        self.assert_completed(self.terminal(reopened, "translation"), cached=True, history="unchanged")
        self.assertEqual(self.history(reopened), entries)
        self.assertEqual(len(self.turns()), 1)
        self.stop(reopened)
        self.assert_native_gone()

    def test_real_snapshot_direction_dictionary_code_summary_and_streaming_migration_prompts(self):
        for scenario in ("direction", "dictionary", "code", "summary", "streaming-migration"):
            with self.subTest(scenario=scenario):
                self.prepare(scenario)
                process = self.start()
                self.request(process)
                self.assert_completed(self.terminal(process, "translation"))
                self.assertEqual(len(self.turns()), 1)
                self.assertEqual(self.turns()[0]["request"]["params"]["input"],
                                 [{"type": "text", "text": self.fixture["expected"]["prompt"]}])
                if scenario == "streaming-migration":
                    self.assertTrue(self.fixture["expected"]["stream"])
                    self.assertIs(json.loads(self.path.read_bytes())["codex_streaming_experimental"], True)
                    self.assertTrue(any(event["type"] == "delta" for pid, event in self.events
                                        if pid == process.pid))
                if scenario == "summary":
                    self.assertTrue(self.fixture["expected"]["summarize"])
                self.stop(process)
                self.assert_native_gone()

    def test_optout_save_during_delta_uses_current_configuration_and_original_snapshot(self):
        self.prepare("gated")
        process = self.start()
        self.request(process)
        self.until_delta(process)
        self.send_message(process, "optout", "request", operation="config_save",
                          config={**self.fixture["config"], "history_enabled": False, "direction": "to_ja"})
        self.assertEqual(self.terminal(process, "optout")["payload"], {"saved": True})
        Path(self.fixture["gate"]).touch()
        self.assert_completed(self.terminal(process, "translation"), history="disabled")
        self.assertEqual(self.history(process), [])
        self.assertFalse(self.history_path.exists())
        self.stop(process)
        self.assert_native_gone(descendant=True)

    def test_corrupt_current_configuration_or_history_is_fixed_failure_without_overwrite(self):
        for which, code in (("config", "invalid_config"), ("history", "invalid_history")):
            with self.subTest(which=which):
                self.prepare()
                process = self.start()
                path = self.path if which == "config" else self.history_path
                malformed = b'{"SYNTHETIC_PRIVATE_CORRUPTION":'
                path.write_bytes(malformed)
                self.request(process)
                result = self.terminal(process, "translation")
                self.assertEqual(result["type"], "failed")
                self.assertEqual(result["payload"], {"code": code, "submitted": False})
                self.assertEqual(path.read_bytes(), malformed)
                self.no_cli()
                self.stop(process)
                self.assertEqual(path.read_bytes(), malformed)

    def test_malformed_and_utf8_oversize_requests_are_rejected_before_submission(self):
        process = self.start()
        invalid = [
            {"text": ""}, {"text": " \r\n"}, {"text": "\u4e2d" * 2731},
            {"text": "x" * 8193}, {"app_language": "fr_FR"}, {"origin": "ocr"},
            {"use_cache": 1}, {"record_history": None}, {"timeout": 1},
        ]
        for index, changes in enumerate(invalid):
            id_ = "invalid-" + str(index)
            self.request(process, id_, **changes)
            event = self.terminal(process, id_)
            self.assertEqual((event["type"], event["seq"], event["payload"]),
                             ("failed", 0, {"code": "invalid_translation"}))
        self.no_cli()
        self.assertFalse(self.history_path.exists())
        self.stop(process)

    def test_unicode_controls_json_output_and_total_envelope_budgets_never_truncate(self):
        for scenario in ("controls", "output-limit", "envelope-limit"):
            with self.subTest(scenario=scenario):
                self.prepare(scenario)
                process = self.start()
                self.request(process)
                event = self.terminal(process, "translation")
                events = [value for pid, value in self.events
                          if pid == process.pid and value["id"] == "translation"]
                self.assertLessEqual(sum(len(protocol.encode_frame(value)) for value in events),
                                     protocol.MAX_STREAM_BYTES)
                if scenario == "controls":
                    self.assert_completed(event)
                    self.assertEqual("".join(value["payload"]["text"] for value in events
                                             if value["type"] == "delta"),
                                     self.fixture["expected"]["output"])
                    self.assertEqual(self.history(process)[0]["output"], self.fixture["expected"]["output"])
                else:
                    self.assertEqual((event["type"], event["payload"]),
                                     ("failed", {"code": "translation_output_limit", "submitted": True}))
                    self.assertFalse(self.history_path.exists())
                    if scenario == "envelope-limit":
                        self.assertGreater(len(events), 1000)
                self.stop(process)
                self.assert_native_gone()

    def test_two_helpers_compete_then_owner_exit_releases_state_and_reopens_cached(self):
        owner = self.start()
        competitor = self.spawn()
        result = self.hello(competitor)
        self.assertEqual(result["payload"], {"code": "config_in_use"})
        self.finish_helper(competitor, code=2)
        self.no_cli()
        self.request(owner)
        self.assert_completed(self.terminal(owner, "translation"))
        self.stop(owner)
        self.assert_native_gone()
        successor = self.start(configure=False)
        self.request(successor)
        self.assert_completed(self.terminal(successor, "translation"), cached=True, history="unchanged")
        self.assertEqual(len(self.turns()), 1)
        self.stop(successor)

    def test_active_cancel_waits_for_group_cleanup_and_waiting_request_is_not_submitted(self):
        self.prepare("gated")
        process = self.start()
        self.request(process)
        self.until_delta(process)
        self.request(process, "waiting", use_cache=False)
        self.assertEqual(self.receive(process)["type"], "accepted")
        self.assertEqual(self.receive(process)["type"], "started")
        self.send_message(process, "cancel-waiting", "cancel", request_id="waiting")
        self.send_message(process, "cancel-active", "cancel", request_id="translation")
        terminals = {}
        while len(terminals) < 4:
            event = self.receive(process)
            if event["type"] in ("completed", "cancelled"):
                terminals[event["id"]] = event
        self.assertEqual(terminals["waiting"]["payload"], {"submitted": False})
        self.assertEqual(terminals["translation"]["payload"], {"submitted": True})
        for id_ in ("cancel-waiting", "cancel-active"):
            self.assertEqual(terminals[id_]["payload"], {"cancel_requested": True})
        self.assertEqual(len(self.turns()), 1)
        self.assert_native_gone(descendant=True)
        self.assertFalse(self.history_path.exists())
        self.stop(process)

    def test_queued_cancel_has_no_started_or_submitted_and_never_launches_cli(self):
        self.mode = "prestart"
        process = self.start()
        self.request(process)
        self.assertEqual(self.receive(process)["type"], "accepted")
        self.barrier()
        self.send_message(process, "cancel", "cancel", request_id="translation")
        self.assertEqual(self.receive(process)["payload"], {})
        self.assertEqual(self.receive(process)["payload"], {"cancel_requested": True})
        self.release()
        self.stop(process)
        self.no_cli()
        self.assertEqual([event["type"] for pid, event in self.events
                          if pid == process.pid and event["id"] == "translation"],
                         ["accepted", "cancelled"])

    def test_result_action_queued_cancel_never_starts_provider_or_reads_history(self):
        self.prepare(result_action="summary")
        self.mode = "prestart"
        process = self.start()
        self.request(process)
        self.assertEqual(self.receive(process)["type"], "accepted")
        self.barrier()
        self.send_message(process, "cancel", "cancel", request_id="translation")
        self.assertEqual(self.receive(process)["payload"], {})
        self.assertEqual(self.receive(process)["payload"], {"cancel_requested": True})
        self.release()
        self.stop(process)
        self.no_cli()
        self.assertFalse(self.history_path.exists())
        self.assertEqual([event["type"] for pid, event in self.events
                          if pid == process.pid and event["id"] == "translation"],
                         ["accepted", "cancelled"])

    def test_result_action_eof_drains_native_group_and_reopen_does_not_replay(self):
        self.prepare("gated", result_action="summary")
        process = self.start()
        self.request(process)
        self.until_delta(process)
        process.stdin.close()
        process.stdin = None
        self.assertEqual(self.terminal(process, "translation")["payload"], {"submitted": True})
        self.finish_helper(process)
        self.assert_native_gone(descendant=True)
        self.assertFalse(self.history_path.exists())
        reopened = self.start(configure=False)
        self.assertEqual(self.history(reopened), [])
        self.assertEqual(len(self.turns()), 1)
        self.stop(reopened)

    def test_invalid_result_actions_are_determinate_before_provider_submission(self):
        self.prepare(result_action="summary")
        process = self.start()
        for index, changes in enumerate((
                {"action": "unsupported"}, {"target_language": "ja"}, {"target_language": False},
                {"text": "x" * 24_001}, {"record_history": True})):
            id_ = "invalid_" + str(index)
            self.request(process, id_, **changes)
            result = self.terminal(process, id_)
            self.assertEqual((result["type"], result["seq"]), ("failed", 0))
            self.assertEqual(result["payload"], {"code": "invalid_result_action"})
        self.no_cli()
        self.assertFalse(self.history_path.exists())
        self.stop(process)

    def test_cancel_after_begin_finish_is_rejected_and_shutdown_waits_for_real_history_write(self):
        self.mode = "committing"
        process = self.start()
        self.request(process)
        self.until_delta(process)
        self.barrier()
        self.send_message(process, "cancel", "cancel", request_id="translation")
        self.assertEqual(self.terminal(process, "cancel")["payload"], {"cancel_requested": False})
        self.send_message(process, "shutdown", "shutdown")
        self.assertFalse(self.history_path.exists())
        self.release()
        self.assert_completed(self.terminal(process, "translation"))
        self.assertEqual(self.terminal(process, "shutdown")["payload"], {})
        self.finish_helper(process)
        self.assertEqual(json.loads(self.history_path.read_bytes())[0]["output"],
                         self.fixture["expected"]["output"])
        self.assert_native_gone()

    def test_eof_and_shutdown_drain_active_translation_before_releasing_owners(self):
        for shutdown in (False, True):
            with self.subTest(shutdown=shutdown):
                self.prepare("gated")
                process = self.start()
                self.request(process)
                self.until_delta(process)
                if shutdown:
                    self.send_message(process, "shutdown", "shutdown")
                else:
                    process.stdin.close()
                    process.stdin = None
                self.assertEqual(self.terminal(process, "translation")["payload"], {"submitted": True})
                if shutdown:
                    self.assertEqual(self.terminal(process, "shutdown")["payload"], {})
                self.finish_helper(process)
                self.assert_native_gone(descendant=True)
                reopened = self.start(configure=False)
                self.assertEqual(self.history(reopened), [])
                self.assertEqual(len(self.turns()), 1)
                self.stop(reopened)

    def test_lost_stdout_after_submission_does_not_replay_and_reopen_reads_committed_cache(self):
        self.prepare("gated")
        process = self.start()
        self.request(process)
        self.until_delta(process)
        process.stdout.close()
        process.stdout = None
        Path(self.fixture["gate"]).touch()
        # Keep stdin open: the broken writer must stop the real reader and drain.
        process.wait(timeout=10)
        errors = process.stderr.read()
        self.assertEqual(process.returncode, 2)
        self.assertIn(b"cc_macos:pipe_error\n", errors)
        self.assertNotIn(b"SYNTHETIC_PRIVATE", errors)
        self.assert_native_gone(descendant=True)
        self.assertEqual(len(self.turns()), 1)
        self.assertTrue(self.history_path.exists(), "Completed history is not replayed after lost acknowledgement.")
        reopened = self.start(configure=False)
        self.request(reopened)
        self.assert_completed(self.terminal(reopened, "translation"), cached=True, history="unchanged")
        self.assertEqual(len(self.turns()), 1)
        self.stop(reopened)

    def test_owned_helper_sigterm_drains_native_group_without_automatic_replay(self):
        self.prepare("gated")
        process = self.start()
        self.request(process)
        self.until_delta(process)
        process.terminate()
        process.wait(timeout=8)
        self.assertNotEqual(process.returncode, 0)
        self.assert_native_gone(descendant=True)
        self.assertEqual(len(self.turns()), 1)
        reopened = self.start(configure=False)
        self.assertEqual(self.history(reopened), [])
        self.assertEqual(len(self.turns()), 1)
        self.stop(reopened)


if __name__ == "__main__":
    unittest.main()
