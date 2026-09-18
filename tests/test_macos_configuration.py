"""Configuration wire/lifecycle contracts with real temporary JSON and controlled scheduling."""

import io
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cc_config_store
from cc_macos import configuration
from cc_macos import history as history_service
from cc_macos.config_owner import ConfigInUseError
from cc_macos.protocol import (
    MAX_CONFIG_BYTES, MAX_CONFIG_DEPTH, MAX_CONFIG_NUMBER, MAX_FRAME_BYTES,
    PipeFrameReader, ProtocolError, decode_frame, encode_frame, validate_config,
)
from cc_macos.server import Server
from cc_storage import macos_user_paths


def message(id_, type_, **payload):
    return {"v": 1, "id": id_, "type": type_, "payload": payload}


class ConfigurationWireTests(unittest.TestCase):
    def test_objects_unknown_keys_and_exact_scalar_kinds(self):
        validate_config({"future": [None, True, False, 1, -2, 0.5, "\u4e2d", {"key": []}]})
        for invalid in (None, [], "str", {1: "key"}, {"x": ()}, {"x": object()}):
            with self.subTest(value=type(invalid)), self.assertRaisesRegex(ProtocolError, "invalid_config"):
                validate_config(invalid)

    def test_configuration_utf8_byte_boundary(self):
        value = {"x": "a" * (MAX_CONFIG_BYTES - 8)}
        self.assertEqual(len(json.dumps(value, separators=(",", ":")).encode()), MAX_CONFIG_BYTES)
        validate_config(value)
        for value in ({"x": "a" * (MAX_CONFIG_BYTES - 7)}, {"x": "\u4e2d" * 6000},
                      {"x": "\ud800"}, {"\ud800": "value"}):
            with self.assertRaisesRegex(ProtocolError, "invalid_config"):
                validate_config(value)

    def test_configuration_depth_boundary_and_cycles(self):
        value = 1
        for _ in range(MAX_CONFIG_DEPTH - 2):
            value = [value]
        validate_config({"x": value})
        with self.assertRaises(ProtocolError):
            validate_config({"x": [value]})
        circular = {}
        circular["x"] = circular
        with self.assertRaises(ProtocolError):
            validate_config(circular)

    def test_exact_interoperable_number_boundary(self):
        for value in (MAX_CONFIG_NUMBER, -MAX_CONFIG_NUMBER, 1.25, True):
            validate_config({"x": value})
        for value in (MAX_CONFIG_NUMBER + 1, -MAX_CONFIG_NUMBER - 1, 10 ** 400,
                      float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=repr(value)), self.assertRaisesRegex(ProtocolError, "invalid_config"):
                validate_config({"x": value})

    def test_wire_duplicate_keys_and_global_depth_remain_strict(self):
        for wire in (b'{"v":1,"id":"r","type":"request","payload":{"operation":"config_save","config":{"x":1,"x":2}}}\n',
                     b'{"config":{"x":true,"x":false}}\n', b'{"x":NaN}\n',
                     b'{"x":' + b"[" * 17 + b"0" + b"]" * 17 + b"}\n"):
            with self.assertRaises(ProtocolError):
                decode_frame(wire)

    def test_raw_and_normalized_save_must_both_fit_before_writing(self):
        validate_config({"x": "a" * (MAX_CONFIG_BYTES - 8)})
        with self.assertRaises(ProtocolError):
            configuration.validate_save({"x": "a" * (MAX_CONFIG_BYTES - 8)})
        for raw in ({"font_size": "invalid"}, {"history_limit": None},
                    {"double_press_window": "inf"}, {"history_enabled": []}):
            with self.subTest(raw=raw), self.assertRaisesRegex(ProtocolError, "invalid_config"):
                configuration.validate_save(raw)
        configuration.validate_save({"font_size": "16", "future": {"kept": True}})

    def reader(self, chunks):
        stopped = threading.Event()
        reader = PipeFrameReader(SimpleNamespace(fileno=lambda: 81), stopped)
        reader._select = Mock(return_value=([81], [], []))
        reader._read = Mock(side_effect=chunks)
        return reader, stopped

    def test_interruptible_reader_preserves_partial_and_coalesced_frames(self):
        one, two = encode_frame(message("a", "hello")), encode_frame(message("b", "shutdown"))
        reader, _ = self.reader([one[:7], one[7:] + two, b""])
        self.assertEqual(reader.read(), message("a", "hello"))
        self.assertEqual(reader.read(), message("b", "shutdown"))
        self.assertIsNone(reader.read())

    def test_interruptible_reader_stops_without_reading_more_private_input(self):
        reader, stopped = self.reader([b'{"partial'])
        stopped.set()
        self.assertIsNone(reader.read())
        reader._read.assert_not_called()
        reader._select.assert_not_called()

    def test_interruptible_reader_rejects_truncated_and_overlong_frames(self):
        for chunks, code in (([b"{}", b""], "truncated_frame"),
                             ([b"a" * MAX_FRAME_BYTES], "frame_too_large")):
            with self.subTest(code=code):
                reader, _ = self.reader(chunks)
                with self.assertRaisesRegex(ProtocolError, code):
                    reader.read()

    def test_configuration_startup_options_are_explicit_and_paired(self):
        self.assertIsNone(configuration.startup_configuration([]))
        for args in (["--config-home"], ["--config-home", "relative", "--application-id", "synthetic"],
                     ["--home", "private", "--application-id", "synthetic"],
                     ["--config-home", str(Path.cwd()), "--application-id", "../escape"],
                     ["--config-home", str(Path.cwd()), "--application-id", "synthetic", "extra"]):
            with self.subTest(args=args), self.assertRaisesRegex(ProtocolError, "invalid_startup"):
                configuration.startup_configuration(args)
        with patch.object(Path, "mkdir", side_effect=AssertionError("startup mkdir")), \
                patch("builtins.open", side_effect=AssertionError("startup read")):
            session = configuration.startup_configuration([
                "--config-home", str(Path.cwd()), "--application-id", "synthetic.config"])
            session.close()


class _ConfigurationDirectory(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix=".config-ipc-", dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve()
        self.identity = "synthetic.config-ipc"
        self.directory = macos_user_paths(self.home, self.identity).application_support
        self.path = self.directory / "config.json"
        platform = patch.object(configuration, "sys", SimpleNamespace(platform="darwin"))
        platform.start()
        self.addCleanup(platform.stop)
        factory = patch.object(configuration, "MacConfigOwner", side_effect=lambda home, identity:
                               cc_config_store.ConfigRepository(
                                   macos_user_paths(home, identity).application_support / "config.json"))
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        history_factory = patch.object(history_service, "_BusinessHistoryOwner",
                                       side_effect=history_service._BoundedHistoryRepository)
        history_factory.start()
        self.addCleanup(history_factory.stop)
        self.session = configuration.ConfigurationSession(self.home, self.identity)
        self.addCleanup(self.session.close)


class ConfigurationServiceTests(_ConfigurationDirectory):
    def test_exact_raw_migration_compact_and_disk_limits_remain_readable(self):
        def stored_bytes(value):
            return json.dumps(value, ensure_ascii=False, indent=2).replace("\n", os.linesep).encode()

        self.session.open()
        for budget in ("compact", "disk"):
            with self.subTest(budget=budget):
                raw = {"history_enabled": ""}
                if budget == "disk":
                    nested = [0] * 2800
                    for _ in range(7):
                        nested = [nested]
                    raw["future"] = nested
                view = configuration.normalize_config(raw)
                changed, migrated = configuration.plan_config_migration(raw, view)
                self.assertTrue(changed)
                def measure(value):
                    if budget == "compact":
                        return len(json.dumps(value, separators=(",", ":")).encode())
                    return len(stored_bytes(value))
                limit = MAX_CONFIG_BYTES if budget == "compact" else configuration.MAX_CONFIG_FILE_BYTES
                padding = limit - measure(migrated)
                self.assertGreater(padding, 0)
                raw["history_enabled"] = "x" * padding
                _, migrated = configuration.plan_config_migration(raw, configuration.normalize_config(raw))
                self.assertEqual(measure(migrated), limit)
                self.assertEqual(self.session.perform({"operation": "config_save", "config": raw}), {"saved": True})
                self.assertEqual(self.path.read_bytes(), stored_bytes(raw))
                with patch.object(cc_config_store, "atomic_write_json",
                                  wraps=cc_config_store.atomic_write_json) as write:
                    self.session.perform({"operation": "config_load"})
                    self.session.perform({"operation": "config_load"})
                    write.assert_called_once()
                self.assertEqual(self.path.read_bytes(), stored_bytes(migrated))
                self.session.close()
                self.session = configuration.ConfigurationSession(self.home, self.identity)
                self.addCleanup(self.session.close)
                self.session.open()
                loaded = self.session.perform({"operation": "config_load"})["config"]
                if "future" in raw:
                    self.assertEqual(loaded["future"], raw["future"])
                before = self.path.read_bytes()
                raw["history_enabled"] += "x"
                with self.assertRaisesRegex(configuration.ConfigurationError, "^invalid_config$"):
                    self.session.perform({"operation": "config_save", "config": raw})
                self.assertEqual(self.path.read_bytes(), before)
                external = stored_bytes(raw)
                self.path.write_bytes(external)
                with self.assertRaisesRegex(configuration.ConfigurationError, "^invalid_config$"):
                    self.session.perform({"operation": "config_load"})
                self.assertEqual(self.path.read_bytes(), external)
                self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])

    def test_indented_storage_budget_rejects_expansion_without_changing_old_bytes(self):
        self.session.open()
        self.path.write_bytes(b'{"future":"old"}')
        nested = [0] * 4000
        for _ in range(7):
            nested = [nested]
        raw = {"future": nested}
        validate_config(raw)
        validate_config(configuration.normalize_config(raw))
        self.assertGreater(len(json.dumps(raw, indent=2).encode()), MAX_FRAME_BYTES)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(configuration.ConfigurationError, "^invalid_config$"):
            self.session.perform({"operation": "config_save", "config": raw})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])

    def test_save_predicts_raw_migration_growth_before_changing_old_bytes(self):
        self.session.open()
        self.path.write_bytes(b'{"future":"old"}')
        raw = {"history_enabled": "x" * 16362}
        self.assertEqual(len(json.dumps(raw, separators=(",", ":")).encode()), MAX_CONFIG_BYTES)
        validate_config(configuration.normalize_config(raw))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(configuration.ConfigurationError, "^invalid_config$"):
            self.session.perform({"operation": "config_save", "config": raw})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])

    def test_existing_raw_migration_growth_is_rejected_without_writing(self):
        self.session.open()
        raw = {"history_enabled": "x" * 16362}
        before = json.dumps(raw, ensure_ascii=False, indent=2).encode()
        self.path.write_bytes(before)
        with self.assertRaisesRegex(configuration.ConfigurationError, "^invalid_config$"):
            self.session.perform({"operation": "config_load"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])
        self.assertIsNotNone(self.session._owner)

    def test_explicit_session_load_save_and_reopen_share_real_repository(self):
        self.assertFalse(self.directory.exists())
        self.session.open()
        self.assertEqual(self.factory.call_args.args, (self.home, self.identity))
        self.assertEqual(self.session.perform({"operation": "config_load"})["config"].font_size, 12)
        self.assertFalse(self.path.exists())
        raw = {"font_size": "16", "future": {"keep": ["\u4e2d"]}}
        self.assertEqual(self.session.perform({"operation": "config_save", "config": raw}), {"saved": True})
        self.assertEqual(json.loads(self.path.read_bytes()), raw)
        self.assertEqual(self.session.perform({"operation": "config_load"})["config"].font_size, 16)
        self.session.close()
        with self.assertRaises(configuration.ConfigurationError):
            self.session.perform({"operation": "config_load"})
        reopened = configuration.ConfigurationSession(self.home, self.identity)
        try:
            reopened.open()
            self.assertEqual(reopened.perform({"operation": "config_load"})["config"]["future"], raw["future"])
        finally:
            reopened.close()

    def test_history_limit_save_readback_and_reopen_preserve_history_without_eager_reads(self):
        self.session.open()
        self.assertEqual(self.session.perform({"operation": "config_load"})["config"]["history_limit"], 100)
        history_path = self.directory / "history.json"
        entries = [{"input": str(n), "output": "\u4e2d", "future": [n]} for n in range(5)]
        before = json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        history_path.write_bytes(before)
        load = {"operation": "history_load", "page_size": 1, "cursor": None}
        first = self.session.perform_history(load, "first", 2)
        for limit in (1, "2", 10000):
            with self.subTest(limit=limit):
                with patch.object(history_service._BoundedHistoryRepository, "_read",
                                  side_effect=AssertionError("configuration read history")), \
                        patch.object(history_service._BoundedHistoryRepository, "add",
                                     side_effect=AssertionError("configuration trimmed history")):
                    self.assertEqual(self.session.perform({
                        "operation": "config_save", "config": {"history_limit": limit}}), {"saved": True})
                    value = self.session.perform({"operation": "config_load"})["config"]["history_limit"]
                    self.assertIs(type(value), int)
                    self.assertEqual(value, int(limit))
                self.assertEqual(history_path.read_bytes(), before)
                self.assertEqual(self.session.perform_history(load, "again", 2), first)
                next_page = self.session.perform_history(
                    load | {"cursor": first["next_cursor"]}, "next", 2)
                self.assertEqual(next_page["entries"], entries[1:2])
                self.assertEqual(next_page["revision"], first["revision"])
        self.session.close()
        reopened = configuration.ConfigurationSession(self.home, self.identity)
        self.addCleanup(reopened.close)
        with patch.object(history_service._BoundedHistoryRepository, "_read",
                          side_effect=AssertionError("reopening configuration read history")):
            reopened.open()
            self.assertEqual(reopened.perform({"operation": "config_load"})["config"]["history_limit"], 10000)
        self.assertEqual(history_path.read_bytes(), before)
        refreshed = reopened.perform_history(load | {"page_size": 100}, "refresh", 2)
        self.assertEqual(refreshed["entries"], entries)
        self.assertEqual(refreshed["total"], 5)
        self.assertNotEqual(refreshed["revision"], first["revision"])

    def test_read_wire_validation_happens_before_disk_migration(self):
        self.session.open()
        for before in (b'{"future":1e100}', b'{"future":NaN}',
                       ('{"future":"' + "x" * MAX_CONFIG_BYTES + '"}').encode()):
            with self.subTest(size=len(before)):
                self.path.write_bytes(before)
                with patch.object(cc_config_store, "atomic_write_json") as write:
                    with self.assertRaisesRegex(configuration.ConfigurationError, "^invalid_config$"):
                        self.session.perform({"operation": "config_load"})
                    write.assert_not_called()
                self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_or_oversize_save_never_touches_old_file(self):
        self.session.open()
        self.path.write_bytes(b"{}")
        for raw in ([], {"font_size": "bad"}, {"x": "x" * MAX_CONFIG_BYTES}):
            with patch.object(cc_config_store, "atomic_write_json") as write:
                with self.assertRaisesRegex(configuration.ConfigurationError, "^invalid_config$"):
                    self.session.perform({"operation": "config_save", "config": raw})
                write.assert_not_called()
            self.assertEqual(self.path.read_bytes(), b"{}")

    def test_read_errors_are_fixed_and_old_bytes_survive(self):
        self.session.open()
        for before in (b"{", b"\xff", b"null", b'{"font_size":"bad"}',
                       b'{"future":1,"future":2}', b'{"future":NaN}'):
            self.path.write_bytes(before)
            with self.assertRaisesRegex(configuration.ConfigurationError, "^invalid_config$"):
                self.session.perform({"operation": "config_load"})
            self.assertEqual(self.path.read_bytes(), before)
        with patch("builtins.open", side_effect=PermissionError("private path and contents")):
            with self.assertRaisesRegex(configuration.ConfigurationError, "^config_io_failed$"):
                self.session.perform({"operation": "config_load"})

    def test_initialization_failure_does_not_claim_or_close_another_owner(self):
        self.factory.side_effect = ConfigInUseError("private location")
        with self.assertRaisesRegex(configuration.ConfigurationError, "^config_in_use$"):
            self.session.open()
        self.assertIsNone(self.session._owner)
        self.session.close()
        with self.assertRaises(configuration.ConfigurationError):
            self.session.open()

    def test_config_owner_close_failure_is_not_retried(self):
        self.session.open()
        owner = self.session._owner
        with patch.object(owner, "close", side_effect=OSError("private close")) as close:
            with self.assertRaises(OSError):
                self.session.close()
            self.session.close()
            close.assert_called_once()

    def test_resolution_loop_is_fixed_before_owner_or_directory_creation(self):
        for outcomes in ([RuntimeError("private home")],
                         [self.home, RuntimeError("private support directory")]):
            with self.subTest(resolutions=len(outcomes)):
                self.session = configuration.ConfigurationSession(self.home, self.identity)
                self.addCleanup(self.session.close)
                with patch.object(Path, "resolve", side_effect=outcomes):
                    with self.assertRaisesRegex(configuration.ConfigurationError, "^config_unavailable$"):
                        self.session.open()
                self.factory.assert_not_called()
                self.assertIsNone(self.session._owner)
                self.assertFalse(self.directory.exists())

    def test_default_diagnostic_hello_does_not_initialize_configuration(self):
        wire = encode_frame(message("h", "hello"))
        with patch.object(configuration.ConfigurationSession, "open", side_effect=AssertionError("business I/O")):
            output = io.BytesIO()
            self.assertEqual(Server(io.BytesIO(wire), output, io.StringIO()).run(), 0)
        self.assertTrue(decode_frame(output.getvalue())["payload"]["fixture"])
        self.assertFalse(self.directory.exists())

    def test_bad_handshake_never_initializes_business_storage(self):
        output = io.BytesIO()
        server = Server(io.BytesIO(), output, io.StringIO(), configuration=self.session)
        with self.assertRaisesRegex(ProtocolError, "handshake_required"):
            server._handle(message("h", "hello", config={}))
        self.factory.assert_not_called()
        self.assertFalse(self.directory.exists())


class ConfigurationSchedulingTests(_ConfigurationDirectory):
    def server(self):
        output, errors = io.BytesIO(), io.StringIO()
        server = Server(io.BytesIO(), output, errors, configuration=self.session)
        server._handle(message("h", "hello"))
        self.addCleanup(server._join_workers)
        self.addCleanup(server._stop)
        return server, output, errors

    def events(self, output):
        return [decode_frame(line + b"\n") for line in output.getvalue().splitlines()]

    def test_history_limit_save_replace_failure_reports_error_and_preserves_both_files(self):
        server, output, errors = self.server()
        self.session.perform({"operation": "config_save", "config": {"history_limit": 100}})
        self.session.perform({"operation": "config_load"})
        before_config = self.path.read_bytes()
        history_path = self.directory / "history.json"
        before_history = b'[{"input":"newest"},{"input":"older"},{"input":"oldest"}]\n'
        history_path.write_bytes(before_history)
        load = {"operation": "history_load", "page_size": 1, "cursor": None}
        first = self.session.perform_history(load, "first", 2)
        attempted, entered = [], threading.Event()
        def fail_replace(source, destination):
            attempted.append((Path(destination), json.loads(Path(source).read_bytes())))
            entered.set()
            raise PermissionError("SYNTHETIC_PRIVATE_REPLACE")
        with patch("cc_storage.os.replace", side_effect=fail_replace):
            server._handle(message("lower", "request", operation="config_save", config={"history_limit": 1}))
            try:
                self.assertTrue(entered.wait(3))
            finally:
                server._stop()
                server._join_workers()
        events = [event for event in self.events(output) if event["id"] == "lower"]
        self.assertEqual([event["type"] for event in events], ["accepted", "started", "failed"])
        self.assertEqual(events[-1]["payload"], {"code": "config_io_failed"})
        self.assertEqual(attempted, [(self.path, {"history_limit": 1})])
        self.assertEqual(self.path.read_bytes(), before_config)
        self.assertEqual(history_path.read_bytes(), before_history)
        self.assertEqual(self.session.perform({"operation": "config_load"})["config"]["history_limit"], 100)
        self.assertEqual(self.session.perform_history(load, "again", 2), first)
        self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])
        self.assertNotIn("SYNTHETIC_PRIVATE_REPLACE", output.getvalue().decode() + errors.getvalue())

    def test_business_ready_and_operations_are_mode_specific(self):
        server, output, _ = self.server()
        ready = self.events(output)[0]
        self.assertEqual(ready["payload"]["capabilities"],
                         ["config_load", "config_save", "history_load", "history_add", "history_clear",
                          "dictionary_status", "dictionary_lookup", "dictionary_prepare_install",
                          "dictionary_install", "dictionary_discard_install", "dictionary_delete"])
        self.assertIs(ready["payload"]["fixture"], False)
        for id_, payload in (("fixture", {"operation": "fixture", "text": "no"}),
                             ("path", {"operation": "config_load", "path": "private"}),
                             ("kind", {"operation": "config_save", "config": []})):
            server._handle(message(id_, "request", **payload))
        self.assertFalse(self.path.exists())
        self.assertEqual([item["type"] for item in self.events(output)], ["ready", "failed", "failed", "failed"])

    def blocked_write(self):
        entered, release = threading.Event(), threading.Event()
        actual = cc_config_store.atomic_write_json
        def write(path, payload):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test writer was not released")
            actual(path, payload)
        return entered, release, write

    def test_started_write_cannot_cancel_and_queued_write_can_cancel(self):
        server, output, errors = self.server()
        entered, release, writer = self.blocked_write()
        with patch.object(cc_config_store, "atomic_write_json", side_effect=writer):
            try:
                server._handle(message("first", "request", operation="config_save", config={"future": "first"}))
                self.assertTrue(entered.wait(3))
                server._handle(message("queued", "request", operation="config_save", config={"future": "never"}))
                server._handle(message("c1", "cancel", request_id="first"))
                server._handle(message("c2", "cancel", request_id="queued"))
                controls = {item["id"]: item for item in self.events(output) if item["id"].startswith("c")}
                self.assertEqual(controls["c1"]["payload"], {"cancel_requested": False})
                self.assertEqual(controls["c2"]["payload"], {"cancel_requested": True})
                server._stop()
                self.assertIsNotNone(self.session._owner)
                self.assertFalse(self.path.exists())
            finally:
                release.set()
                server._join_workers()
        self.assertEqual(json.loads(self.path.read_bytes()), {"future": "first"})
        by_id = {id_: [event["type"] for event in self.events(output) if event["id"] == id_]
                 for id_ in ("first", "queued")}
        self.assertEqual(by_id["first"], ["accepted", "started", "completed"])
        self.assertEqual(by_id["queued"], ["accepted", "cancelled"])
        self.assertEqual(errors.getvalue(), "")

    def test_fifo_load_observes_the_preceding_save(self):
        server, output, _ = self.server()
        entered, release, writer = self.blocked_write()
        finished = threading.Event()
        actual_send = server._send
        def observe(request, event, payload):
            actual_send(request, event, payload)
            if request.id == "load" and event in {"completed", "failed"}:
                finished.set()
        with patch.object(cc_config_store, "atomic_write_json", side_effect=writer), \
                patch.object(server, "_send", side_effect=observe):
            try:
                server._handle(message("save", "request", operation="config_save", config={"font_size": "16"}))
                self.assertTrue(entered.wait(3))
                server._handle(message("load", "request", operation="config_load"))
                release.set()
                self.assertTrue(finished.wait(3))
            finally:
                release.set()
                server._stop()
                server._join_workers()
        loaded = next(item for item in self.events(output) if item["id"] == "load" and item["type"] == "completed")
        self.assertEqual(loaded["payload"]["config"]["font_size"], 16)

    def test_duplicate_request_id_does_not_queue_another_write(self):
        server, output, _ = self.server()
        entered, release, writer = self.blocked_write()
        with patch.object(cc_config_store, "atomic_write_json", side_effect=writer) as writes:
            try:
                request = message("same", "request", operation="config_save", config={"future": "one"})
                server._handle(request)
                self.assertTrue(entered.wait(3))
                with self.assertRaisesRegex(ProtocolError, "duplicate_id"):
                    server._handle(request)
            finally:
                release.set()
                server._stop()
                server._join_workers()
        self.assertEqual(writes.call_count, 1)
        self.assertEqual(json.loads(self.path.read_bytes()), {"future": "one"})


class ConfigurationExitTests(_ConfigurationDirectory):
    def start(self):
        incoming, outgoing = queue.Queue(), queue.Queue()
        class Reader:
            def __init__(self, _stream, stopping):
                self.stopping = stopping
            def read(self):
                while not self.stopping.is_set():
                    try:
                        return incoming.get(timeout=0.02)
                    except queue.Empty:
                        continue
                return None
        class Output(io.BytesIO):
            broken = False
            def write(self, data):
                if self.broken:
                    raise BrokenPipeError("private exception text")
                count = super().write(data)
                outgoing.put(decode_frame(data))
                return count
        output, errors, result = Output(), io.StringIO(), []
        server = Server(io.BytesIO(), output, errors, configuration=self.session)
        reader_patch = patch("cc_macos.server.PipeFrameReader", Reader)
        reader_patch.start()
        self.addCleanup(reader_patch.stop)
        worker = threading.Thread(target=lambda: result.append(server.run()))
        worker.start()
        def cleanup():
            incoming.put(None)
            worker.join(7)
            self.assertFalse(worker.is_alive())
        self.addCleanup(cleanup)
        incoming.put(message("h", "hello"))
        ready = outgoing.get(timeout=3)
        return server, worker, incoming, outgoing, output, errors, result, ready

    def test_eof_and_shutdown_wait_for_started_write_then_close_owner(self):
        for control in ("eof", "shutdown"):
            with self.subTest(control=control):
                self.session = configuration.ConfigurationSession(self.home, self.identity)
                server, thread, incoming, outgoing, output, errors, result, ready = self.start()
                self.assertEqual(ready["type"], "ready")
                owner = self.session._owner
                entered, release = threading.Event(), threading.Event()
                actual = cc_config_store.atomic_write_json
                def write(path, raw):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("writer not released")
                    actual(path, raw)
                with patch.object(cc_config_store, "atomic_write_json", side_effect=write), \
                        patch.object(owner, "close", wraps=owner.close) as close:
                    try:
                        incoming.put(message("r", "request", operation="config_save", config={"future": control}))
                        self.assertTrue(entered.wait(3))
                        incoming.put(None if control == "eof" else message("s", "shutdown"))
                        self.assertTrue(server._stop_event.wait(3))
                        self.assertTrue(thread.is_alive())
                        close.assert_not_called()
                        events = [decode_frame(raw + b"\n") for raw in output.getvalue().splitlines()]
                        self.assertFalse(any(event["type"] in {"completed", "cancelled"} for event in events))
                    finally:
                        release.set()
                        thread.join(5)
                    self.assertFalse(thread.is_alive())
                    close.assert_called_once()
                self.assertEqual(result, [0])
                self.assertEqual(errors.getvalue(), "")
                self.assertIsNone(self.session._owner)
                self.assertEqual(json.loads(self.path.read_bytes()), {"future": control})
                events = [decode_frame(raw + b"\n") for raw in output.getvalue().splitlines()]
                self.assertEqual([event["type"] for event in events if event["id"] == "r"],
                                 ["accepted", "started", "completed"])
                if control == "shutdown":
                    self.assertEqual(events[-1]["id"], "s")
                    self.assertEqual(events[-1]["type"], "completed")

    def test_stdout_failure_interrupts_input_wait_and_releases_owner(self):
        server, thread, incoming, outgoing, output, errors, result, ready = self.start()
        self.assertEqual(ready["type"], "ready")
        output.broken = True
        incoming.put(message("r", "request", operation="config_save", config={"future": "never"}))
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [2])
        self.assertEqual(errors.getvalue(), "cc_macos:pipe_error\n")
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.session._owner)

    def test_protocol_failure_during_started_write_still_waits_and_does_not_replay(self):
        server, thread, incoming, outgoing, output, errors, result, _ = self.start()
        entered, release = threading.Event(), threading.Event()
        actual = cc_config_store.atomic_write_json
        def write(path, data):
            entered.set()
            if not release.wait(5):
                raise AssertionError("writer not released")
            actual(path, data)
        request = message("same", "request", operation="config_save", config={"future": "one"})
        with patch.object(cc_config_store, "atomic_write_json", side_effect=write) as writes:
            try:
                incoming.put(request)
                self.assertTrue(entered.wait(3))
                incoming.put(request)
                self.assertTrue(server._stop_event.wait(3))
                self.assertTrue(thread.is_alive())
                self.assertIsNotNone(self.session._owner)
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(writes.call_count, 1)
        self.assertEqual(result, [2])
        self.assertEqual(errors.getvalue(), "cc_macos:duplicate_id\n")
        self.assertEqual(json.loads(self.path.read_bytes()), {"future": "one"})
        self.assertIsNone(self.session._owner)

    def test_bootstrap_busy_is_fixed_and_never_announces_ready(self):
        self.factory.side_effect = ConfigInUseError("private path")
        server, thread, _, _, _, errors, result, event = self.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(event["type"], "failed")
        self.assertEqual(event["payload"], {"code": "config_in_use"})
        self.assertEqual(result, [2])
        self.assertEqual(errors.getvalue(), "")
        self.assertIsNone(self.session._owner)

    def test_worker_start_failure_releases_initialized_owner(self):
        operations = [
            {"operation": "config_load"},
            {"operation": "config_save", "config": {"font_size": 21}},
            {"operation": "history_load", "page_size": 1, "cursor": None},
            {"operation": "history_add", "input": "synthetic", "output": "never",
             "is_dict": False, "is_code": False, "kind": "text", "sig": "", "limit": 10},
            {"operation": "history_clear"},
        ]
        history_path = self.directory / "history.json"
        for existing in (False, True):
            for payload in operations:
                with self.subTest(operation=payload["operation"], existing=existing):
                    self.session = configuration.ConfigurationSession(self.home, self.identity)
                    server, thread, incoming, _, output, errors, result, ready = self.start()
                    self.assertEqual(ready["type"], "ready")
                    before = {self.path: b'{"font_size":"16","future":"preserved"}',
                              history_path: b'[{"input":"synthetic","output":"keep","kind":"text"}]'}
                    for path, data in before.items():
                        if existing:
                            path.write_bytes(data)
                        else:
                            path.unlink(missing_ok=True)
                    owner, history_owner = self.session._owner, self.session._history._owner
                    with patch("cc_macos.server.threading.Thread.start", side_effect=RuntimeError("private thread")) as start, \
                            patch.object(self.session, "perform", wraps=self.session.perform) as perform, \
                            patch.object(self.session, "perform_history", wraps=self.session.perform_history) as perform_history, \
                            patch.object(owner, "close", wraps=owner.close) as close, \
                            patch.object(history_owner, "close", wraps=history_owner.close) as close_history:
                        incoming.put(message("r", "request", **payload))
                        thread.join(5)
                        start.assert_called_once()
                        perform.assert_not_called()
                        perform_history.assert_not_called()
                        close.assert_called_once()
                        close_history.assert_called_once()
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(result, [2])
                    self.assertEqual(errors.getvalue(), "cc_macos:worker_start_failed\n")
                    events = [decode_frame(raw + b"\n") for raw in output.getvalue().splitlines()]
                    self.assertEqual(events[1:], [
                        {"v": 1, "id": "r", "seq": 0, "type": "accepted", "payload": {"operation": payload["operation"]}},
                        {"v": 1, "id": "r", "seq": 1, "type": "failed", "payload": {"code": "worker_start_failed"}},
                    ])
                    self.assertEqual(server._tasks, {})
                    self.assertEqual(server._workers, set())
                    self.assertIsNone(self.session._owner)
                    self.assertIsNone(self.session._history)
                    for path, data in before.items():
                        if existing:
                            self.assertEqual(path.read_bytes(), data)
                        else:
                            self.assertFalse(path.exists())
                    self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])

    def test_owner_close_failure_produces_failed_shutdown_not_false_success(self):
        server, thread, incoming, _, output, errors, result, _ = self.start()
        owner, actual_close = self.session._owner, self.session._owner.close
        def close_then_fail():
            actual_close()
            raise OSError("private close detail")
        with patch.object(owner, "close", side_effect=close_then_fail) as close:
            incoming.put(message("s", "shutdown"))
            thread.join(5)
            self.assertFalse(thread.is_alive())
            close.assert_called_once()
        self.assertEqual(result, [2])
        self.assertEqual(errors.getvalue(), "cc_macos:state_io_failed\n")
        events = [decode_frame(raw + b"\n") for raw in output.getvalue().splitlines()]
        self.assertEqual(events[-1]["payload"], {"code": "state_io_failed"})
        self.assertEqual(events[-1]["type"], "failed")
        self.assertIsNone(self.session._owner)
