"""Mandatory real-process regressions, run only with the bundled macOS Python."""

from contextlib import redirect_stderr, redirect_stdout
import ctypes
import errno
import io
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest


if sys.platform != "darwin":
    raise RuntimeError("This suite requires bundled macOS Python; do not substitute a host run.")

import cc_macos
from cc_macos import catalog_fixture, catalog_process_fixture, probes, server
import cc_providers
from cc_providers import codex_catalog, codex_config, darwin_process
from cc_providers.codex_catalog import CatalogProbeError, CodexModelCatalog
from cc_providers.codex_config import CODEX_CONFIG_OVERRIDES


SAFE_ARGS = [part for override in CODEX_CONFIG_OVERRIDES for part in ("-c", override)]
REPORT = {
    "status": "passed", "fixture": True, "process_verified": True,
    "cache_verified": True, "reopen_verified": True,
}


class TestOwnedCatalogProcess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = Path(sys.executable).resolve()
        contents = next((parent for parent in executable.parents
                         if parent.name == "Contents" and parent.parent.suffix == ".app"), None)
        if contents is None or not executable.is_relative_to(contents / "Helpers" / "python"):
            raise RuntimeError("The catalog process suite must use the app's bundled Python.")
        cls.contents = contents
        cls.core = contents / "Resources" / "Core"
        for module in (cc_macos, catalog_fixture, catalog_process_fixture, probes, server,
                       cc_providers, codex_catalog, codex_config, darwin_process):
            expected = cls.core.joinpath(*module.__name__.split("."))
            expected = expected / "__init__.py" if hasattr(module, "__path__") else expected.with_suffix(".py")
            if Path(module.__file__).resolve() != expected.resolve():
                raise RuntimeError("Catalog process tests imported code outside the app's Core.")
        if not sys.flags.isolated or not sys.dont_write_bytecode:
            raise RuntimeError("Run the bundled Python with -I -B.")

    def setUp(self):
        # Keep all synthetic homes, caches and helper TMPDIRs in the checkout.
        directory = tempfile.TemporaryDirectory(prefix=".cc-catalog-process-", dir=str(Path.cwd()))
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.root = self.directory / "synthetic work \u4e2d # %"
        self.leaders = {}
        self.children = {}
        self.environment_before = dict(os.environ)
        self.sibling = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "import time; time.sleep(45)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=self.directory,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.directory), "TMPDIR": str(self.directory)},
            start_new_session=True,
        )
        self.addCleanup(self._finish_sibling)
        self.addCleanup(self._cleanup_owned)
        self.assertEqual(os.getpgid(self.sibling.pid), self.sibling.pid)
        self.assertEqual(os.getsid(self.sibling.pid), self.sibling.pid)

    def tearDown(self):
        self.assertIsNone(self.sibling.poll(), "Cleanup must not kill an independent sibling session.")
        self.assertTrue(dict(os.environ) == self.environment_before, "The user environment was mutated.")

    def test_bridge_and_all_imports_are_really_bundled(self):
        bridge = darwin_process.load_supervision()
        self.assertIsInstance(bridge, ctypes.CDLL)
        self.assertTrue(bridge._handle)
        self.assertEqual(Path(bridge._name).resolve(),
                         self.contents / "Helpers" / "python" / "lib" / "libCCProcessSupport.dylib")
        self.assertEqual(bridge.cc_process_support_abi(), 1)
        self.assertTrue(issubclass(CatalogProbeError, RuntimeError))
        self.assertFalse(issubclass(CatalogProbeError, ValueError))

    def test_cold_cache_hit_and_reopen_each_use_real_processes(self):
        self._exercise_success("normal")

    def test_early_successful_leaders_clean_term_resistant_pipe_holders(self):
        self._exercise_success("descendant")

    def test_early_nonzero_leader_is_fatal_and_cleans_descendant(self):
        self._exercise_failure("early", "catalog_probe_failed")

    def test_silent_process_and_descendant_obey_the_real_deadline(self):
        self._exercise_failure("timeout", "catalog_probe_timeout", deadline=True)

    def test_stdout_limit_cleans_the_owned_group(self):
        self._exercise_failure("stdout_flood", "catalog_probe_output_limit")

    def test_stderr_limit_cleans_the_owned_group(self):
        self._exercise_failure("stderr_flood", "catalog_probe_output_limit")

    def test_stdout_and_stderr_share_one_eight_mib_budget(self):
        self._exercise_failure("combined_flood", "catalog_probe_output_limit")

    def test_closed_pipes_do_not_remove_the_live_leader_deadline(self):
        self._exercise_failure("closed_pipes", "catalog_probe_timeout", deadline=True)

    def test_explicit_cancellation_waits_for_actual_descendant_start(self):
        self._exercise_failure("timeout", "catalog_probe_cancelled", cancel=True)

    def test_precancel_does_not_spawn_or_create_cache(self):
        manager, warnings = self._create_cli()
        event = threading.Event()
        event.set()
        config = Path(manager.env["CODEX_HOME"]) / "config.toml"
        before = config.read_bytes()
        with self.assertRaises(CatalogProbeError) as caught:
            manager.overrides(cancel_event=event)
        self.assertEqual(str(caught.exception), "catalog_probe_cancelled")
        self.assertEqual(self._calls(self.root), [])
        self.assertFalse((self.root / "children").exists())
        self.assertFalse(manager.cache_dir.exists())
        self._assert_inactive(manager, warnings)
        self.assertEqual(config.read_bytes(), before)
        # A cancelled request must not poison a later independent request.
        result = manager.overrides(cancel_event=threading.Event())
        self._assert_calls(manager, self._cold_args(result), child_indices=())

    def test_nonzero_stderr_remains_private_and_cannot_fall_back(self):
        self._exercise_failure("nonzero", "catalog_probe_failed", child_indices=())

    def test_roundtrip_timeout_never_activates_a_partial_cache(self):
        self._exercise_failure("roundtrip_timeout", "catalog_probe_timeout",
                               deadline=True, child_indices=(2,))

    def test_public_process_probe_proves_cold_hit_and_reopen(self):
        self.assertEqual(catalog_process_fixture.probe_catalog_process(
            self.root, cancel_event=threading.Event()), REPORT)
        calls = self._calls(self.root)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0]["args"], ["--version", *SAFE_ARGS])
        self.assertEqual(calls[1]["args"], ["debug", "models", *SAFE_ARGS])
        self.assertEqual(calls[2]["args"], calls[3]["args"])
        override = calls[2]["args"][3]
        path = Path(json.loads(override.split("=", 1)[1]))
        self.assertTrue(path.resolve().is_relative_to((self.root / "cache").resolve()))
        self.assertEqual(calls[2]["args"], ["debug", "models", "-c", override, *SAFE_ARGS])
        self.assertEqual(json.loads(path.read_bytes()), catalog_fixture.PAYLOAD)
        self.assertEqual(len(list((self.root / "cache").rglob("state.json"))), 1)
        self._assert_processes(self.root, calls, child_indices=())

    def test_helper_eof_cancels_real_catalog_process_before_exit(self):
        self._exercise_helper(protocol_cancel=False)

    def test_helper_protocol_cancel_cleans_process_before_stdin_eof(self):
        self._exercise_helper(protocol_cancel=True)

    def _create_cli(self, mode="normal"):
        manager, warnings = catalog_process_fixture.create_cli(self.root, mode)
        self.assertIs(type(manager), CodexModelCatalog)
        self.assertIs(manager._run.__func__, CodexModelCatalog._run)
        self.assertIsNone(manager._validated)
        self.assertEqual(manager.env["PATH"], "/usr/bin:/bin")
        for key in ("HOME", "CODEX_HOME", "TMPDIR"):
            self.assertTrue(Path(manager.env[key]).resolve().is_relative_to(self.root.resolve()), key)
        for path in (manager.command, manager.cache_dir, manager.work_dir):
            self.assertTrue(Path(path).resolve().is_relative_to(self.root.resolve()))
        self.assertFalse(set(manager.env) - {
            "PATH", "HOME", "CODEX_HOME", "CC_TRANSLATE_CODEX_HOME", "TMPDIR",
            "CC_SYNTHETIC_ROOT", "CC_SYNTHETIC_MODE",
        }, "The synthetic child must not inherit credentials or user CLI settings.")
        return manager, warnings

    @staticmethod
    def _cold_args(result):
        return [["--version"], ["debug", "models"], ["debug", "models", "-c", result[0]]]

    def _exercise_success(self, mode):
        manager, warnings = self._create_cli(mode)
        config = Path(manager.env["CODEX_HOME"]) / "config.toml"
        before = config.read_bytes()
        started = time.monotonic()
        result = manager.overrides(cancel_event=threading.Event())
        self.assertLess(time.monotonic() - started, 6, "Success waited for inherited-pipe EOF.")
        self.assertEqual(len(result), 1)
        key, separator, value = result[0].partition("=")
        self.assertEqual((key, separator), ("model_catalog_json", "="))
        catalog = Path(json.loads(value))
        self.assertTrue(catalog.is_absolute())
        self.assertTrue(catalog.resolve().is_relative_to(manager.cache_dir.resolve()))
        self.assertEqual(json.loads(catalog.read_bytes()), catalog_fixture.PAYLOAD)
        self.assertEqual(manager.status, "ready")
        self.assertIsNotNone(manager._validated)
        children = tuple(range(3)) if mode == "descendant" else ()
        self._assert_calls(manager, self._cold_args(result), children)
        snapshots = {path: path.read_bytes() for path in manager.cache_dir.rglob("*") if path.is_file()}
        self.assertEqual(len(list(manager.cache_dir.rglob("state.json"))), 1)

        self.assertEqual(manager.overrides("synthetic-small"), result)
        self.assertEqual(len(self._calls(self.root)), 3, "An in-manager hit respawned the CLI.")
        reopened = CodexModelCatalog(
            manager.command, env=dict(manager.env), cache_dir=manager.cache_dir,
            work_dir=manager.work_dir,
            log_error=lambda where, error: warnings.append((where, str(error))),
        )
        self.assertIsNone(reopened._validated)
        self.assertEqual(reopened.overrides(cancel_event=threading.Event()), result)
        self.assertEqual(reopened.status, "ready")
        self.assertIsNotNone(reopened._validated)
        self.assertEqual(reopened.overrides(), result)
        children = tuple(range(4)) if mode == "descendant" else ()
        self._assert_calls(manager, [*self._cold_args(result), self._cold_args(result)[2]], children)
        self.assertEqual({path: path.read_bytes() for path in manager.cache_dir.rglob("*")
                          if path.is_file()}, snapshots)
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(warnings, [])
        self.assertFalse(list(self.root.rglob(".catalog-*")))

    def _exercise_failure(self, mode, code, *, deadline=False, cancel=False, child_indices=(0,)):
        manager, warnings = self._create_cli(mode)
        config = Path(manager.env["CODEX_HOME"]) / "config.toml"
        before = config.read_bytes()
        event, stop = threading.Event(), threading.Event()
        ready, waiter_errors = threading.Event(), []

        def cancel_when_started():
            try:
                calls, children = self._wait_for_child(self.root, stop=stop)
                os.kill(calls[-1]["pid"], 0)
                os.kill(children[-1]["pid"], 0)
                ready.set()
            except (AssertionError, OSError, ValueError) as error:
                waiter_errors.append(type(error).__name__)
            finally:
                event.set()

        threads_before = {thread.ident for thread in threading.enumerate()}
        waiter = threading.Thread(target=cancel_when_started, daemon=True) if cancel else None
        if waiter:
            waiter.start()
        output, errors = io.StringIO(), io.StringIO()
        try:
            started = time.monotonic()
            with redirect_stdout(output), redirect_stderr(errors):
                with self.assertRaises(CatalogProbeError) as caught:
                    manager.overrides(cancel_event=event)
            elapsed = time.monotonic() - started
        finally:
            stop.set()
            if waiter:
                waiter.join(timeout=2)
                self.assertFalse(waiter.is_alive(), "The cancellation observer leaked.")
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn("SYNTHETIC_PRIVATE", "".join(traceback.format_exception(caught.exception)))
        self.assertFalse(output.getvalue(), "Catalog stdout escaped the bounded capture.")
        self.assertFalse(errors.getvalue(), "Catalog stderr escaped the bounded capture.")
        if deadline:
            self.assertGreaterEqual(elapsed, 7.5, "The production eight-second deadline was shortened.")
            self.assertLess(elapsed, 12, "The real deadline did not include cleanup.")
        else:
            self.assertLess(elapsed, 6, "Failure or cancellation waited for the timeout.")
        if cancel:
            self.assertEqual(waiter_errors, [])
            self.assertTrue(ready.is_set(), "Cancellation happened before the real processes started.")
        self.assertEqual({thread.ident for thread in threading.enumerate()}, threads_before)
        self._assert_inactive(manager, warnings)
        self.assertEqual(config.read_bytes(), before)
        if mode == "roundtrip_timeout":
            catalogs = list(manager.cache_dir.rglob("models-*.json"))
            self.assertEqual(len(catalogs), 1)
            self.assertEqual(json.loads(catalogs[0].read_bytes()), catalog_fixture.PAYLOAD)
            result = ("model_catalog_json=" + json.dumps(str(catalogs[0].resolve())),)
            expected = self._cold_args(result)
        else:
            expected = [["--version"]]
        self._assert_calls(manager, expected, child_indices)
        before_calls = self._calls(self.root)
        time.sleep(0.25)
        self.assertEqual(self._calls(self.root), before_calls, "A failed probe restarted or fell back.")

    def _assert_inactive(self, manager, warnings):
        self.assertNotEqual(manager.status, "ready")
        self.assertIsNone(manager._validated)
        self.assertEqual(manager._failure_until, 0, "A fatal process error entered fallback/backoff.")
        self.assertEqual(warnings, [], "A fatal process error was converted into a fallback warning.")
        self.assertFalse(list(manager.cache_dir.rglob("state.json")))
        self.assertFalse(list(self.root.rglob(".catalog-*")))

    @staticmethod
    def _calls(root):
        path = root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def _remember(self, root):
        calls = self._calls(root)
        for call in calls:
            self.leaders[call["pid"]] = call
        children = []
        for path in (root / "children").glob("*.json"):
            child = json.loads(path.read_text(encoding="utf-8"))
            self.children[child["pid"]] = child
            self.assertEqual(child["group"], int(path.stem))
            children.append(child)
        return calls, children

    def _assert_calls(self, manager, expected, child_indices):
        calls = self._calls(self.root)
        self.assertEqual([call["args"] for call in calls], [args + SAFE_ARGS for args in expected])
        for call in calls:
            self.assertEqual(Path(call["cwd"]).resolve(), Path(manager.work_dir).resolve())
        self._assert_processes(self.root, calls, child_indices)

    def _assert_processes(self, root, calls, child_indices):
        _, children = self._remember(root)
        self.assertEqual(len({call["pid"] for call in calls}), len(calls), "Calls reused a process.")
        self.assertEqual({child["group"] for child in children},
                         {calls[index]["pid"] for index in child_indices})
        self.assertEqual(len(children), len(child_indices))
        for call in calls:
            self.assertEqual(call["pid"], call["group"])
            self.assertEqual(call["pid"], call["session"])
            self.assertNotIn(call["group"], {os.getpgrp(), self.sibling.pid})
            self.assertTrue(Path(call["cwd"]).resolve().is_relative_to(root.resolve()))
            self.assert_gone(call["pid"])
        for child in children:
            self.assertNotEqual(child["pid"], child["group"])
            self.assert_gone(child["pid"], orphan=True)
        for call in calls:
            self._assert_group_gone(call["group"])
        self.assertIsNone(self.sibling.poll())

    def _wait_for_child(self, root, *, stop=None):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if stop is not None and stop.is_set():
                raise AssertionError("The process observer was stopped before evidence arrived.")
            try:
                calls, children = self._remember(root)
                if calls and children:
                    return calls, children
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(0.01)
        raise AssertionError("The synthetic catalog descendant did not publish start evidence.")

    @staticmethod
    def _process_rows(pid=None):
        args = ["/bin/ps", "-o", "pid=,ppid=,pgid=,stat=", "-p", str(pid)] if pid else [
            "/bin/ps", "-axo", "pid=,ppid=,pgid=,stat="]
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=2, check=False)
        if result.returncode not in (0, 1) or result.stderr:
            raise AssertionError("Cannot inspect Darwin process cleanup.")
        return [line.split() for line in result.stdout.decode("ascii").splitlines() if line.strip()]

    def assert_gone(self, pid, *, orphan=False):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError as error:
                if error.errno == errno.ESRCH:
                    return
                raise
            # Orphans are reaped asynchronously by launchd, not by the catalog
            # owner. Leaders, in contrast, must already have been waited for.
            if orphan:
                rows = self._process_rows(pid)
                if not rows or all(int(row[1]) == 1 and row[3].startswith("Z") for row in rows):
                    return
            time.sleep(0.02)
        self.fail("A synthetic owned process is still live or its leader was not reaped.")

    def _assert_group_gone(self, group):
        try:
            os.killpg(group, 0)
        except OSError as error:
            if error.errno == errno.ESRCH:
                return
            raise
        rows = [row for row in self._process_rows() if int(row[2]) == group]
        self.assertTrue(all(int(row[0]) in self.children and int(row[1]) == 1
                            and row[3].startswith("Z") for row in rows),
                        "A live process remains in an owned group.")

    def _exercise_helper(self, *, protocol_cancel):
        script = """
import sys
sys.path.insert(0, sys.argv[1])
from cc_macos import catalog_process_fixture
original = catalog_process_fixture.create_cli
catalog_process_fixture.create_cli = lambda root, mode="normal": original(root, "timeout")
from cc_macos.server import Server
raise SystemExit(Server(sys.stdin.buffer, sys.stdout.buffer, sys.stderr).run())
"""
        helper = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", script, str(self.core)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.directory,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.directory), "TMPDIR": str(self.directory)},
            start_new_session=True,
        )
        try:
            for identifier, kind, payload in (
                    ("h", "hello", {}), ("r", "request", {"operation": "runtime_probe"})):
                self._send(helper, identifier, kind, payload)
            deadline = time.monotonic() + 5
            root = None
            while time.monotonic() < deadline:
                for path in self.directory.rglob("calls.jsonl"):
                    try:
                        calls, children = self._remember(path.parent)
                        if calls and children:
                            root = path.parent
                            break
                    except (FileNotFoundError, json.JSONDecodeError):
                        pass
                if root is not None:
                    break
                self.assertIsNone(helper.poll(), "The real helper exited before starting the catalog.")
                time.sleep(0.01)
            self.assertIsNotNone(root, "runtime_probe did not start the real catalog fixture.")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["args"], ["--version", *SAFE_ARGS])
            self.assertEqual(len(children), 1)
            self.assertTrue(Path(calls[0]["cwd"]).resolve().is_relative_to(root.resolve()))
            self.assertNotIn(calls[0]["group"], {os.getpgrp(), helper.pid, self.sibling.pid})
            self.assertFalse(list((root / "cache").rglob("state.json")))
            os.kill(calls[0]["pid"], 0)
            os.kill(children[0]["pid"], 0)
            captured = b""
            if protocol_cancel:
                self._send(helper, "c", "cancel", {"request_id": "r"})
                captured = self._read_cancel_ack(helper)
                # Keep stdin open: EOF must not be what actually cancels it.
                self.assert_gone(calls[0]["pid"])
                self.assert_gone(children[0]["pid"], orphan=True)
                self.assertIsNone(helper.poll())
            helper.stdin.close()
            helper.stdin = None
            output, errors = helper.communicate(timeout=5)
            self.assertEqual(helper.returncode, 0)
            self.assertFalse(errors, "The helper leaked stderr or failed to join its worker.")
            events = [json.loads(line) for line in (captured + output).splitlines()]
            self.assertEqual([event["type"] for event in events if event["id"] == "h"], ["ready"])
            terminals = [event for event in events if event["id"] == "r"
                         and event["type"] in {"cancelled", "failed", "completed"}]
            self.assertEqual(len(terminals), 1)
            self.assertEqual((terminals[0]["type"], terminals[0]["payload"]), ("cancelled", {}))
            self.assertNotIn(b"SYNTHETIC_PRIVATE", captured + output + errors)
            self.assert_gone(calls[0]["pid"])
            self.assert_gone(children[0]["pid"], orphan=True)
            self.assertEqual(calls[0]["pid"], calls[0]["group"])
            self.assertEqual(calls[0]["pid"], calls[0]["session"])
            self.assertEqual(children[0]["group"], calls[0]["pid"])
            self._assert_group_gone(calls[0]["group"])
            self.assertFalse(list(self.directory.rglob("state.json")),
                             "The cancelled real catalog must not activate cache state.")
        finally:
            if helper.poll() is None:
                helper.kill()
                helper.wait(timeout=3)
            for stream in (helper.stdin, helper.stdout, helper.stderr):
                if stream is not None:
                    stream.close()

    @staticmethod
    def _send(helper, identifier, kind, payload):
        helper.stdin.write((json.dumps({
            "v": 1, "id": identifier, "type": kind, "payload": payload,
        }) + "\n").encode("utf-8"))
        helper.stdin.flush()

    def _read_cancel_ack(self, helper):
        deadline = time.monotonic() + 5
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(helper.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                for key, _ in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                    data = os.read(key.fileobj.fileno(), 16_384)
                    self.assertTrue(data, "The helper closed stdout without a cancel acknowledgement.")
                    output.extend(data)
                    self.assertLess(len(output), 128 * 1024, "The helper emitted excessive protocol output.")
                    for line in bytes(output).split(b"\n")[:-1]:
                        event = json.loads(line)
                        if event["id"] == "c":
                            self.assertEqual((event["type"], event["payload"]),
                                             ("completed", {"cancel_requested": True}))
                            return bytes(output)
        self.fail("The helper did not acknowledge protocol cancellation.")

    def _cleanup_owned(self):
        # Evidence does not confer ownership after reap: never signal or wait
        # for these PIDs. Failed production cleanup must remain a test failure.
        for path in self.directory.rglob("calls.jsonl"):
            self._remember(path.parent)
        for pid in self.children:
            self.assert_gone(pid, orphan=True)
        for pid in self.leaders:
            self.assert_gone(pid)

    def _finish_sibling(self):
        if self.sibling.poll() is None:
            self.sibling.terminate()
            try:
                self.sibling.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.sibling.kill()
                self.sibling.wait(timeout=2)
