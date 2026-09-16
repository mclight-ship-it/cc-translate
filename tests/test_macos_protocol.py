import io
import os
from pathlib import Path
import queue
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from cc_macos import probes
from cc_macos.protocol import (
    MAX_FRAME_BYTES, MAX_TEXT_BYTES, MAX_RESULT_ACTION_TEXT_BYTES, ProtocolError, decode_frame, encode_frame,
    read_frame, validate_client,
)
from cc_macos.server import Server
from tools.macos.bundle import copy_core_sources


ROOT = Path(__file__).resolve().parent.parent


def message(id_, type_, **payload):
    return {"v": 1, "id": id_, "type": type_, "payload": payload}


class PipeHelper:
    def __init__(self, case, *, command=None, cwd=ROOT, env=None):
        self.process = subprocess.Popen(
            command or [sys.executable, "-B", "-m", "cc_macos"],
            cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.events = queue.Queue()
        self.received = []
        self.sequences = {}
        case.addCleanup(self.close)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            self.events.put(line)
        self.events.put(None)

    def send(self, value):
        self.process.stdin.write(encode_frame(value))
        self.process.stdin.flush()

    def receive(self, timeout=5):
        raw = self.events.get(timeout=timeout)
        if raw is None:
            raise AssertionError("unexpected helper EOF")
        value = decode_frame(raw)
        previous = self.sequences.get(value["id"], -1)
        if value["seq"] != previous + 1:
            raise AssertionError("non-contiguous event sequence")
        self.sequences[value["id"]] = value["seq"]
        self.received.append(value)
        return value

    def until_terminal(self, id_):
        while True:
            event = self.receive()
            if event["id"] == id_ and event["type"] in {"completed", "failed", "cancelled"}:
                return event

    def hello(self):
        self.send(message("hello", "hello"))
        return self.receive()

    def finish(self):
        self.process.stdin.close()
        self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        return self.process.returncode, self.process.stderr.read()

    def close(self):
        if self.process.poll() is None:
            if not self.process.stdin.closed:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


class TestMacProtocolFrames(unittest.TestCase):
    def test_unicode_and_split_reads(self):
        value = message("r_1", "request", operation="fixture", text="\u4e2d\u6587")
        frame = encode_frame(value)
        self.assertEqual(read_frame(io.BytesIO(frame)), value)
        self.assertIsNone(read_frame(io.BytesIO()))

    def test_length_boundary_includes_newline(self):
        raw = b'{"x":"' + b"a" * (MAX_FRAME_BYTES - 9) + b'"}\n'
        self.assertEqual(len(raw), MAX_FRAME_BYTES)
        self.assertIn("x", decode_frame(raw))
        with self.assertRaisesRegex(ProtocolError, "frame_too_large"):
            read_frame(io.BytesIO(raw[:-1] + b"a\n"))
        with self.assertRaisesRegex(ProtocolError, "frame_too_large"):
            encode_frame({"x": "a" * MAX_FRAME_BYTES})

    def test_malformed_frames(self):
        for raw in (b"\n", b"{}\xff\n", b"\xef\xbb\xbf{}\n", b"[]\n", b"null\n",
                    b'{"a":1,"a":2}\n', b'{"a":NaN}\n', b'{"a":Infinity}\n',
                    b'{"a":1e999}\n', b'{"a":"\\ud800"}\n', b'{"\\ud800":1}\n',
                    b'{"a":' + b"[" * 32 + b"0" + b"]" * 32 + b"}\n"):
            with self.subTest(raw=raw[:40]), self.assertRaises(ProtocolError):
                decode_frame(raw)
        with self.assertRaisesRegex(ProtocolError, "truncated_frame"):
            read_frame(io.BytesIO(b"{}"))

    def test_envelope_is_strict(self):
        base = message("valid", "hello")
        for mutation in (
            {"v": True}, {"v": 2}, {"id": ""}, {"id": "protocol"}, {"id": "x" * 65},
            {"id": "\u4e2d"}, {"type": []}, {"type": "exec"}, {"payload": []},
            {"unexpected": 1},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(ProtocolError):
                validate_client({**base, **mutation})
        validate_client(base)

    def test_bounded_reader_does_not_consume_rest_of_large_frame(self):
        stream = io.BytesIO(b"x" * MAX_FRAME_BYTES * 2)
        with self.assertRaises(ProtocolError):
            read_frame(stream)
        self.assertEqual(stream.tell(), MAX_FRAME_BYTES + 1)


class TestMacHelperProcess(unittest.TestCase):
    def test_handshake_fixture_and_explicit_shutdown(self):
        helper = PipeHelper(self)
        ready = helper.hello()
        self.assertEqual(ready["type"], "ready")
        self.assertTrue(ready["payload"]["fixture"])
        self.assertEqual(ready["payload"]["max_frame_bytes"], MAX_FRAME_BYTES)
        self.assertEqual(ready["payload"]["capabilities"], ["fixture", "runtime_probe"])
        helper.send(message("r", "request", operation="fixture", text="\u4e2d\u6587", delay_ms=0))
        result = helper.until_terminal("r")
        self.assertEqual(result["type"], "completed")
        self.assertTrue(result["payload"]["fixture"])
        self.assertIn("Synthetic fixture", result["payload"]["text"])
        self.assertTrue(result["payload"]["text"].endswith("\u4e2d\u6587"))
        self.assertEqual([e["type"] for e in helper.received if e["id"] == "r"],
                         ["accepted", "delta", "completed"])
        helper.send(message("stop", "shutdown"))
        self.assertEqual(helper.until_terminal("stop")["type"], "completed")
        self.assertEqual(helper.finish(), (0, b""))

    def test_cancel_is_terminal_and_next_request_works(self):
        helper = PipeHelper(self)
        helper.hello()
        helper.send(message("old", "request", operation="fixture", text="old", delay_ms=2000))
        self.assertEqual(helper.receive()["type"], "accepted")
        helper.send(message("cancel", "cancel", request_id="old"))
        acknowledgement = helper.until_terminal("cancel")
        self.assertTrue(acknowledgement["payload"]["cancel_requested"])
        helper.send(message("new", "request", operation="fixture", text="new", delay_ms=0))
        self.assertEqual(helper.until_terminal("new")["type"], "completed")
        self.assertEqual([e["type"] for e in helper.received if e["id"] == "old"],
                         ["accepted", "cancelled"])
        helper.send(message("again", "cancel", request_id="old"))
        self.assertFalse(helper.until_terminal("again")["payload"]["cancel_requested"])
        self.assertEqual(helper.finish(), (0, b""))

    def test_cancel_unknown_is_explicit_not_successful_cancellation(self):
        helper = PipeHelper(self)
        helper.hello()
        helper.send(message("c", "cancel", request_id="missing"))
        self.assertFalse(helper.receive()["payload"]["cancel_requested"])
        self.assertEqual(helper.finish(), (0, b""))

    def test_eof_cancels_running_work_promptly(self):
        helper = PipeHelper(self)
        helper.hello()
        helper.send(message("r", "request", operation="fixture", text="not complete", delay_ms=2000))
        helper.receive()
        self.assertEqual(helper.finish(), (0, b""))
        self.assertEqual(helper.until_terminal("r")["type"], "cancelled")

    def test_shutdown_cancels_before_control_completion(self):
        helper = PipeHelper(self)
        helper.hello()
        helper.send(message("r", "request", operation="fixture", text="old", delay_ms=2000))
        helper.receive()
        helper.send(message("stop", "shutdown"))
        helper.until_terminal("stop")
        self.assertIn(("r", "cancelled"), [(e["id"], e["type"]) for e in helper.received])
        self.assertEqual(helper.finish(), (0, b""))

    def test_duplicate_id_is_fatal_and_redacted(self):
        helper = PipeHelper(self)
        helper.hello()
        helper.send(message("hello", "request", operation="fixture", text="private-test-input"))
        self.assertEqual(helper.receive()["payload"], {"code": "duplicate_id"})
        code, stderr = helper.finish()
        self.assertEqual(code, 2)
        self.assertEqual(stderr.splitlines(), [b"cc_macos:duplicate_id"])
        self.assertNotIn(b"private-test-input", stderr)

    def test_invalid_input_matrix_exits_without_traceback_or_echo(self):
        inputs = [
            b'{"secret":"not logged"}\n',
            encode_frame(message("r", "request", operation="fixture", text="private")),
            encode_frame({**message("h", "hello"), "v": 99}),
            b"x" * (MAX_FRAME_BYTES + 1),
            b'{"v":1',
            b'{"v":1,"v":1,"id":"h","type":"hello","payload":{}}\n',
        ]
        for raw in inputs:
            with self.subTest(raw=raw[:20]):
                process = subprocess.run(
                    [sys.executable, "-B", "-m", "cc_macos"], cwd=ROOT, input=raw,
                    capture_output=True, timeout=5,
                )
                self.assertEqual(process.returncode, 2)
                event = decode_frame(process.stdout)
                self.assertEqual(event["id"], "protocol")
                self.assertEqual(event["type"], "failed")
                self.assertNotIn(b"private", process.stderr)
                self.assertNotIn(b"Traceback", process.stderr)

    def test_operation_errors_do_not_kill_connection(self):
        helper = PipeHelper(self)
        helper.hello()
        invalid = [
            {"operation": "translate", "text": "not a supported provider"},
            {"operation": "fixture", "text": "x" * (MAX_TEXT_BYTES + 1)},
            {"operation": "fixture", "text": "x", "delay_ms": True},
            {"operation": "fixture", "text": "x", "delay_ms": 2001},
            {"operation": "fixture", "text": "x", "model": "not supported"},
            {"operation": "runtime_probe", "https": "yes"},
        ]
        for index, payload in enumerate(invalid):
            helper.send(message(f"r{index}", "request", **payload))
            self.assertEqual(helper.receive()["type"], "failed")
        helper.send(message("good", "request", operation="fixture", text="ok", delay_ms=0))
        self.assertEqual(helper.until_terminal("good")["type"], "completed")
        self.assertEqual(helper.finish(), (0, b""))

    def test_maximum_fixture_remains_bounded_after_json_escaping(self):
        helper = PipeHelper(self)
        helper.hello()
        helper.send(message("r", "request", operation="fixture",
                            text="\x01" * MAX_TEXT_BYTES, delay_ms=0))
        self.assertEqual(helper.until_terminal("r")["type"], "completed")
        self.assertEqual(helper.finish(), (0, b""))

    def test_import_does_not_load_windows_or_touch_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "APPDATA": directory, "LOCALAPPDATA": directory,
                   "PYTHONDONTWRITEBYTECODE": "1"}
            code = (
                "import sys; import cc_macos.server; "
                "assert not any(n in sys.modules for n in "
                "('tkinter','translator','cc_core','win32util','cc_providers')); "
                "print('isolated')"
            )
            result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT,
                                    env=env, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"isolated\r\n" if os.name == "nt" else b"isolated\n")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_bundle_launcher_ignores_pythonpath_and_current_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            core = Path(directory) / "Core"
            copy_core_sources(core)
            env = {**os.environ, "PYTHONPATH": str(Path(directory) / "untrusted")}
            helper = PipeHelper(self, command=[sys.executable, "-I", "-B", str(core / "launch.py")],
                                cwd=directory, env=env)
            self.assertEqual(helper.hello()["type"], "ready")
            self.assertEqual(helper.finish(), (0, b""))
            helper.close()
            self.assertFalse(list(core.rglob("__pycache__")))

    def test_runtime_probe_real_sqlite_and_ssl_without_network(self):
        helper = PipeHelper(self)
        helper.hello()
        helper.send(message("r", "request", operation="runtime_probe"))
        result = helper.until_terminal("r")
        self.assertEqual(result["type"], "completed")
        self.assertTrue(result["payload"]["sqlite"]["read_write"])
        self.assertEqual(result["payload"]["dictionary"], {
            "status": "passed", "read_only": True, "sources_preserved": True, "reopened": True,
        })
        self.assertTrue(result["payload"]["ssl"]["certificate_validation"])
        self.assertEqual(result["payload"]["https"], {"status": "not_run"})
        self.assertEqual(result["payload"]["python"]["platform"], sys.platform)
        self.assertFalse(result["payload"]["python"]["bundle_runtime"])
        self.assertEqual(result["payload"]["codex_config_fixture"], {"status": "not_run"})
        self.assertEqual(result["payload"]["catalog_storage_fixture"], {
            "status": "passed", "cli_simulated": True, "cache_verified": True, "reopen_verified": True})
        self.assertTrue(result["payload"]["python"]["bytecode_disabled"])
        self.assertEqual(helper.finish(), (0, b""))


class TestMacResultActionProtocol(unittest.TestCase):
    @staticmethod
    def payload(**changes):
        return {"operation": "result_action", "action": "concise", "text": "Synthetic primary result",
                "app_language": "en_US", "target_language": None} | changes

    def server(self, *, translation_enabled=True):
        from cc_macos.translation import validate_result_action_request, validate_translation_request

        config = Mock(translation_enabled=translation_enabled)
        config.validate_result_action_request = validate_result_action_request
        config.validate_translation_request = validate_translation_request
        output = io.BytesIO()
        server = Server(io.BytesIO(), output, io.StringIO(), configuration=config)
        server._handle(message("hello", "hello"))
        self.addCleanup(server._join_workers)
        self.addCleanup(server._stop)
        return server, config, output

    @staticmethod
    def events(output, id_):
        return [event for raw in output.getvalue().splitlines()
                if (event := decode_frame(raw + b"\n"))["id"] == id_]

    def test_action_utf8_boundary_roundtrips_in_paired_version_one_envelope(self):
        self.assertEqual((MAX_TEXT_BYTES, MAX_RESULT_ACTION_TEXT_BYTES), (8192, 24000))
        for text in ("a" * MAX_RESULT_ACTION_TEXT_BYTES, "\u4e2d" * 8000, "\U0001f600" * 6000):
            value = message("action", "request", **self.payload(text=text))
            self.assertEqual(decode_frame(encode_frame(value)), value)
            self.assertEqual(value["v"], 1)
        with self.assertRaisesRegex(ProtocolError, "frame_too_large"):
            encode_frame(message("action", "request", **self.payload(text="\0" * MAX_RESULT_ACTION_TEXT_BYTES)))

    def test_native_ready_advertises_action_without_invoking_model(self):
        server, config, output = self.server()
        ready = self.events(output, "hello")[0]["payload"]
        self.assertEqual(ready["capabilities"], [
            "config_load", "config_save", "history_load", "history_add", "history_clear", "translate", "result_action",
        ])
        self.assertEqual((ready["backend"], ready["fixture"], ready["protocol"]), ("native_appserver", False, 1))
        config.translate.assert_not_called()
        config.result_action.assert_not_called()
        self.assertFalse(server._tasks)

    def test_diagnostic_and_configuration_only_connections_do_not_accept_actions(self):
        server, config, output = self.server(translation_enabled=False)
        self.assertNotIn("result_action", self.events(output, "hello")[0]["payload"]["capabilities"])
        server._handle(message("action", "request", **self.payload()))
        self.assertEqual(self.events(output, "action")[0]["payload"], {"code": "unsupported_operation"})
        config.result_action.assert_not_called()
        output = io.BytesIO()
        diagnostic = Server(io.BytesIO(), output, io.StringIO())
        diagnostic._handle(message("hello", "hello"))
        diagnostic._handle(message("action", "request", **self.payload()))
        self.assertEqual(self.events(output, "hello")[0]["payload"]["capabilities"], ["fixture", "runtime_probe"])
        self.assertEqual(self.events(output, "action")[0]["payload"], {"code": "unsupported_operation"})

    def test_malformed_action_is_fixed_failure_before_acceptance_or_provider_dispatch(self):
        server, config, output = self.server()
        for index, changes in enumerate((
                {"action": "unknown"}, {"action": {}}, {"text": "a" * 24001},
                {"text": "\u4e2d" * 8001}, {"app_language": []}, {"target_language": "en"},
                {"action": "retranslate"}, {"action": "retranslate", "target_language": []},
                {"use_cache": False}, {"record_history": False})):
            server._handle(message(str(index), "request", **self.payload(**changes)))
            self.assertEqual(self.events(output, str(index)), [{
                "v": 1, "id": str(index), "seq": 0, "type": "failed",
                "payload": {"code": "invalid_result_action"},
            }])
        config.result_action.assert_not_called()
        config.translate.assert_not_called()
        self.assertFalse(server._tasks)

    def test_worker_routes_action_to_action_method_with_contiguous_events_and_cancel_event(self):
        server, config, output = self.server()
        completed = {"text": "Synthetic action output", "submitted": True, "cached": False, "kind": "text",
                     "target_lang": None, "summarize": False, "history": "disabled", "history_error": None}
        def action(payload, cancel, on_delta, begin_finish):
            self.assertEqual(payload, self.payload())
            self.assertIsInstance(cancel, threading.Event)
            self.assertFalse(cancel.is_set())
            on_delta("Synthetic action output")
            self.assertTrue(begin_finish())
            return "completed", completed
        config.result_action.side_effect = action
        server._handle(message("action", "request", **self.payload()))
        server._join_workers()
        events = self.events(output, "action")
        self.assertEqual([event["type"] for event in events], ["accepted", "started", "delta", "completed"])
        self.assertEqual([event["seq"] for event in events], [0, 1, 2, 3])
        self.assertEqual(events[0]["payload"], {"operation": "result_action"})
        self.assertEqual(events[1]["payload"], {"operation": "result_action"})
        self.assertEqual(events[-1]["payload"], completed)
        config.result_action.assert_called_once()
        config.translate.assert_not_called()
        config.perform.assert_not_called()
        config.perform_history.assert_not_called()

    def test_action_worker_start_failure_is_single_known_prestart_terminal(self):
        server, config, output = self.server()
        with patch("cc_macos.server.threading.Thread.start", side_effect=RuntimeError("PRIVATE")):
            self.assertFalse(server._handle(message("action", "request", **self.payload())))
        events = self.events(output, "action")
        self.assertEqual([event["type"] for event in events], ["accepted", "failed"])
        self.assertEqual(events[-1]["payload"], {"code": "worker_start_failed"})
        config.result_action.assert_not_called()
        self.assertFalse(server._tasks)
        self.assertFalse(server._workers)

    def test_cancelled_actions_keep_worker_capacity_until_drain_and_cannot_replay_id(self):
        server, config, output = self.server()
        requests = []
        def start(request, _payload):
            request.started = True
            requests.append(request)
            return True
        with patch.object(server, "_start_translation", side_effect=start):
            for index in range(4):
                server._handle(message(str(index), "request", **self.payload()))
            server._handle(message("cancel", "cancel", request_id="0"))
            self.assertEqual(self.events(output, "cancel")[0]["payload"], {"cancel_requested": True})
            self.assertTrue(requests[0].cancel.is_set())
            self.assertFalse(requests[0].terminal)
            server._handle(message("overflow", "request", **self.payload()))
            self.assertEqual(self.events(output, "overflow")[0]["payload"], {"code": "busy"})
            with self.assertRaisesRegex(ProtocolError, "^duplicate_id$"):
                server._handle(message("0", "request", **self.payload()))
        config.result_action.assert_not_called()


class TestMacServerConcurrency(unittest.TestCase):
    def test_eof_waits_for_owned_worker_cleanup(self):
        cleaned = threading.Event()
        def probe(**kwargs):
            kwargs["cancel"].wait(2)
            cleaned.set()
            return {}
        wire = (encode_frame(message("h", "hello")) +
                encode_frame(message("r", "request", operation="runtime_probe")))
        server = Server(io.BytesIO(wire), io.BytesIO(), io.StringIO())
        with patch("cc_macos.server.runtime_probe", side_effect=probe):
            self.assertEqual(server.run(), 0)
        self.assertTrue(cleaned.is_set())
        self.assertFalse(server._workers)

    def test_worker_start_failure_is_visible_and_nonzero(self):
        wire = (encode_frame(message("h", "hello")) +
                encode_frame(message("r", "request", operation="fixture", text="synthetic")))
        output, errors = io.BytesIO(), io.StringIO()
        server = Server(io.BytesIO(wire), output, errors)
        with patch("cc_macos.server.threading.Thread.start", side_effect=RuntimeError("private detail")):
            self.assertEqual(server.run(), 2)
        self.assertIn(b"worker_start_failed", output.getvalue())
        self.assertEqual(errors.getvalue(), "cc_macos:worker_start_failed\n")
        self.assertFalse(server._workers)

    def test_cancel_completion_race_has_one_terminal(self):
        for iteration in range(40):
            helper = PipeHelper(self)
            helper.hello()
            helper.send(message("r", "request", operation="fixture", text="race", delay_ms=0))
            helper.send(message("c", "cancel", request_id="r"))
            helper.until_terminal("c")
            helper.finish()
            events = helper.received[:]
            while not helper.events.empty():
                raw = helper.events.get_nowait()
                if raw is not None:
                    events.append(decode_frame(raw))
            terminal = [e for e in events if e["id"] == "r"
                        and e["type"] in {"completed", "cancelled", "failed"}]
            self.assertEqual(len(terminal), 1, iteration)
            helper.close()

    def test_capacity_includes_cancelled_but_still_running_workers(self):
        release = threading.Event()
        def blocked_probe(**_kwargs):
            release.wait(5)
            return {}
        out = io.BytesIO()
        server = Server(io.BytesIO(), out, io.StringIO())
        try:
            with patch("cc_macos.server.runtime_probe", side_effect=blocked_probe):
                server._handle(message("h", "hello"))
                for i in range(4):
                    server._handle(message(f"r{i}", "request", operation="runtime_probe"))
                server._handle(message("c", "cancel", request_id="r0"))
                server._handle(message("overflow", "request", operation="fixture", text="no"))
                events = [decode_frame(line + b"\n") for line in out.getvalue().splitlines()]
                result = next(e for e in events if e["id"] == "overflow")
                self.assertEqual(result["payload"], {"code": "busy"})
                server._stop()
        finally:
            release.set()

    def test_session_limit_is_bounded(self):
        wire = encode_frame(message("h", "hello")) + encode_frame(message("r", "cancel", request_id="x"))
        out = io.BytesIO()
        server = Server(io.BytesIO(wire), out, io.StringIO())
        with patch("cc_macos.server.MAX_IDS", 1):
            self.assertEqual(server.run(), 2)
        self.assertIn(b"session_limit", out.getvalue())

    def test_broken_output_pipe_is_failure(self):
        output = Mock()
        output.write.side_effect = BrokenPipeError()
        server = Server(io.BytesIO(encode_frame(message("h", "hello"))), output, io.StringIO())
        self.assertEqual(server.run(), 2)


class TestMacRuntimeProbe(unittest.TestCase):
    def test_cancel_prevents_any_work(self):
        cancel = threading.Event()
        cancel.set()
        with patch("cc_macos.probes.sqlite3.connect") as connect:
            with self.assertRaises(probes.ProbeCancelled):
                probes.runtime_probe(https=False, cancel=cancel)
            connect.assert_not_called()

    def test_offline_probe_never_opens_connection_and_removes_database(self):
        with tempfile.TemporaryDirectory() as root:
            original = tempfile.TemporaryDirectory
            with patch("cc_macos.probes.tempfile.TemporaryDirectory",
                       side_effect=lambda **kw: original(dir=root, **kw)), \
                    patch("cc_macos.probes.http.client.HTTPSConnection") as connection:
                result = probes.runtime_probe(https=False, cancel=threading.Event())
            connection.assert_not_called()
            self.assertEqual(result["sqlite"]["status"], "passed")
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_https_requires_bundle_ca(self):
        with patch("cc_macos.probes.BUNDLE_CA") as ca:
            ca.is_file.return_value = False
            with self.assertRaisesRegex(probes.ProbeError, "bundle_ca_missing"):
                probes.runtime_probe(https=True, cancel=threading.Event())

    def _https_mocks(self):
        context = Mock(verify_mode=ssl.CERT_REQUIRED, check_hostname=True)
        connection = Mock()
        connection.getresponse.return_value.status = 200
        return context, connection

    def test_https_uses_verifying_context_fixed_host_and_no_redirect(self):
        context, connection = self._https_mocks()
        with patch("cc_macos.probes.BUNDLE_CA") as ca, \
                patch("cc_macos.probes.ssl.create_default_context", return_value=context) as create, \
                patch("cc_macos.probes.http.client.HTTPSConnection", return_value=connection) as connect:
            ca.is_file.return_value = True
            result = probes.runtime_probe(https=True, cancel=threading.Event())
            create.assert_called_once_with(cafile=str(ca))
            connect.assert_called_once_with("www.python.org", timeout=8, context=context)
        self.assertTrue(result["https"]["certificate_verified"])
        connection.close.assert_called_once()

    def test_certificate_failure_is_not_reported_as_success(self):
        context, connection = self._https_mocks()
        connection.request.side_effect = ssl.SSLCertVerificationError("private host detail")
        with patch("cc_macos.probes.BUNDLE_CA") as ca, \
                patch("cc_macos.probes.ssl.create_default_context", return_value=context), \
                patch("cc_macos.probes.http.client.HTTPSConnection", return_value=connection):
            ca.is_file.return_value = True
            with self.assertRaisesRegex(probes.ProbeError, "^https_certificate_failed$"):
                probes.runtime_probe(https=True, cancel=threading.Event())
        connection.close.assert_called_once()

    def test_unverified_context_rejected(self):
        with patch("cc_macos.probes.ssl.create_default_context",
                   return_value=Mock(verify_mode=ssl.CERT_NONE, check_hostname=False)):
            with self.assertRaisesRegex(probes.ProbeError, "ssl_validation_disabled"):
                probes.runtime_probe(https=False, cancel=threading.Event())


if __name__ == "__main__":
    unittest.main()
