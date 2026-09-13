"""Windows persistence consumers keep their shared writer and legacy behavior."""

import ast
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import cc_storage as storage


ROOT = Path(__file__).resolve().parents[1]
# Verified against 5ecc987 once; regression runs need only the source files.
BASELINE_AST_SHA256 = {
    "translator.pyw": {
        "Config": "f6ad2f066492fe8357c0dd5cd2971ae66dbcb3cbe1984a4a9b386bbb25420407",
        "load_config": "c6d2ede2c1f626e45563ed653143005e51329db54289dbc95dd15f21d10f0f4f",
        "save_config": "254abd80c4f57834011c00a0ebf082bfc9e57614f54947ef6ee1f3e0c4a91e18",
        "load_history": "d5764c3a58b675bd352c9244973a2e985eb0999dda2cc6e4c3d4323626cf0aa5",
        "add_history": "eaa1f82a19fe50fa2d03524df37d62942f620971b2d68f8830fff443118d2470",
        "clear_history": "5ed2ad8f6741bf0d9f0edc012173b047c7659957db9b069378e05203ef79790a",
        "_HISTORY_LOCK": "dec1dfaf29fb70e00591cb50aa21b4586e5c212f10ae86cf6bf10023b0d4c1e8",
    },
    "cc_core.py": {
        "_resolve_data_dir": "5555258f65fb93fbb262d7e42dd6c2167b8220c563c2cd42d434d969c7920d7a",
        "_user_data_path": "63cbc9c6557db224760feb1f15d88adb92cd2d162a792e96b1b27d802a65c703",
        "log_error": "eba315ec0b2238d109ce755aeba07b9c35231c4803fb24a6a036fccc25b3a144",
        "CFG": "3cdc857be53d05e271b4d77edde719443cb0f50eb4368016a27d96cecd2ac104",
        "DEFAULT_CONFIG": "40150450e4a7a621640b05df3f581959a75f99f056d4e3498eaa3abe97a0b0ad",
    },
}


def _ast_hash(node):
    def canonical(value):
        if isinstance(value, ast.AST):
            # Python 3.12 added empty type_params; ignore only that empty
            # metadata rather than relying on version-specific ast.dump output.
            return [type(value).__name__, [
                [name, canonical(child)]
                for name, child in ast.iter_fields(value)
                if name != "type_params" or child
            ]]
        if isinstance(value, list):
            return [canonical(child) for child in value]
        return value

    serialized = json.dumps(canonical(node), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("ascii")).hexdigest()


# The entry fixture imports Windows modules, but must not migrate a real
# adjacent config or probe an installed provider while loading these tests.
with tempfile.TemporaryDirectory(prefix=".storage-import-", dir=ROOT) as _root:
    with mock.patch.dict(os.environ, {"APPDATA": _root, "LOCALAPPDATA": _root}), \
            mock.patch.object(subprocess, "Popen",
                              side_effect=AssertionError("provider/process access")):
        import cc_core as core

        _fresh_entry = "tests._tr" not in sys.modules
        with mock.patch.object(core, "APP_DIR", core.DATA_DIR):
            from tests._tr import tr
        if _fresh_entry:
            tr.APP_DIR = core.APP_DIR


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix=".storage-test-", dir=ROOT)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / "config.json"
        self.history = self.root / "history.json"
        self.log = self.root / "error.log"
        self.legacy = self.root / "legacy"
        self.legacy.mkdir()
        for module, name, value in (
                (tr, "CONFIG_PATH", str(self.config)),
                (tr, "HISTORY_PATH", str(self.history)),
                (tr, "DATA_DIR", str(self.root)),
                (tr, "APP_DIR", str(self.legacy)),
                (core, "DATA_DIR", str(self.root)),
                (core, "APP_DIR", str(self.legacy))):
            patcher = mock.patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def read_json(self, path):
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)

    def seed(self, path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def assert_no_temps(self):
        self.assertEqual(list(self.root.glob(".tmp_*.json")), [])


class TestWindowsStorageExports(unittest.TestCase):
    def test_entry_exports_the_actual_shared_function(self):
        self.assertIs(tr._atomic_write_json, storage.atomic_write_json)
        self.assertEqual(tr._atomic_write_json.__module__, "cc_storage")
        self.assertIs(tr.log_error, core.log_error)
        self.assertIs(tr._resolve_data_dir, core._resolve_data_dir)
        self.assertIs(tr._user_data_path, core._user_data_path)

    def test_stdlib_patch_seams_are_shared_objects(self):
        for name, module in (("json", json), ("os", os), ("tempfile", tempfile)):
            with self.subTest(name=name):
                self.assertIs(getattr(tr, name), module)
                self.assertIs(getattr(storage, name), module)

    def test_windows_consumers_paths_schema_and_lock_match_frozen_baseline(self):
        for filename, names in (
                ("translator.pyw", (
                    "Config", "load_config", "save_config", "load_history",
                    "add_history", "clear_history", "_HISTORY_LOCK")),
                ("cc_core.py", (
                    "_resolve_data_dir", "_user_data_path", "log_error",
                    "CFG", "DEFAULT_CONFIG"))):
            current = (ROOT / filename).read_text(encoding="utf-8")

            def definitions(source):
                result = {}
                for node in ast.parse(source).body:
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                        result[node.name] = node
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                result[target.id] = node
                return result

            before, after = BASELINE_AST_SHA256[filename], definitions(current)
            for name in names:
                with self.subTest(filename=filename, name=name):
                    self.assertIn(name, before)
                    self.assertIn(name, after)
                    self.assertEqual(_ast_hash(after[name]), before[name])


class _Stream:
    """Delegate real file I/O while exposing flush/close failure boundaries."""

    def __init__(self, stream, events, failure=None, error=None):
        self.stream = stream
        self.events = events
        self.failure = failure
        self.error = error

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()
        self.events.append("stream-close")
        if self.failure == "stream-close":
            raise self.error

    def flush(self):
        self.events.append("flush")
        if self.failure == "flush":
            raise self.error
        self.stream.flush()


class TestWindowsAtomicWriter(StorageTestCase):
    def test_real_nested_unicode_creation_reopen_and_replacement(self):
        nested = self.root / "\u76ee\u5f55 with spaces"
        nested.mkdir()
        target = nested / "settings.json"
        for value in ({"\u4e2d\u6587": ["\u521d\u59cb", 1]},
                      {"\u4e2d\u6587": "\u66ff\u6362", "unknown": {"kept": True}}):
            tr._atomic_write_json(str(target), value)
            self.assertEqual(self.read_json(target), value)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                json.dumps(value, ensure_ascii=False, indent=2))
            self.assertEqual(list(nested.iterdir()), [target])

    def test_actual_flush_fsync_stream_close_fd_close_then_same_directory_replace(self):
        old = {"old": "\u539f\u6587"}
        new = {"new": "\u8bd1\u6587"}
        self.seed(self.config, old)
        events, handles = [], []
        real_dump, real_fdopen = json.dump, os.fdopen
        real_fsync, real_close, real_replace = os.fsync, os.close, os.replace
        real_mkstemp = tempfile.mkstemp

        def fdopen(fd, *args, **kwargs):
            stream = real_fdopen(fd, *args, **kwargs)
            handles.append((fd, stream))
            return _Stream(stream, events)

        def dump(*args, **kwargs):
            events.append("dump")
            return real_dump(*args, **kwargs)

        def fsync(fd):
            self.assertFalse(handles[0][1].closed)
            events.append("fsync")
            return real_fsync(fd)

        def close(fd):
            self.assertTrue(handles[0][1].closed)
            events.append("fd-close")
            return real_close(fd)

        def replace(source, destination):
            self.assertEqual(Path(source).parent, self.config.parent)
            self.assertRegex(Path(source).name, r"^\.tmp_.+\.json$")
            self.assertEqual(self.read_json(self.config), old)
            self.assertEqual(self.read_json(source), new)
            with self.assertRaises(OSError) as closed:
                os.fstat(handles[0][0])
            self.assertEqual(closed.exception.errno, errno.EBADF)
            events.append("replace")
            return real_replace(source, destination)

        with mock.patch.object(tr.tempfile, "mkstemp", wraps=real_mkstemp) as create, \
                mock.patch.object(tr.os, "fdopen", side_effect=fdopen) as opened, \
                mock.patch.object(tr.json, "dump", side_effect=dump) as dumped, \
                mock.patch.object(tr.os, "fsync", side_effect=fsync), \
                mock.patch.object(tr.os, "close", side_effect=close) as closed, \
                mock.patch.object(tr.os, "replace", side_effect=replace) as replaced:
            tr._atomic_write_json(str(self.config), new)
        create.assert_called_once_with(prefix=".tmp_", suffix=".json", dir=str(self.root))
        opened.assert_called_once_with(handles[0][0], "w", encoding="utf-8", closefd=False)
        self.assertEqual(dumped.call_args.kwargs, {"ensure_ascii": False, "indent": 2})
        closed.assert_called_once_with(handles[0][0])
        replaced.assert_called_once()
        self.assertEqual(events, ["dump", "flush", "fsync", "stream-close", "fd-close", "replace"])
        self.assertEqual(self.read_json(self.config), new)
        self.assert_no_temps()

    def test_relative_destination_uses_current_directory(self):
        original = os.getcwd()
        try:
            os.chdir(self.root)
            with mock.patch.object(tr.tempfile, "mkstemp", wraps=tempfile.mkstemp) as create:
                tr._atomic_write_json("relative.json", ["\u76f8\u5bf9\u8def\u5f84"])
            create.assert_called_once_with(prefix=".tmp_", suffix=".json", dir=".")
        finally:
            os.chdir(original)
        self.assertEqual(self.read_json(self.root / "relative.json"), ["\u76f8\u5bf9\u8def\u5f84"])
        self.assert_no_temps()

    def test_writer_does_not_impose_a_business_schema(self):
        for value in (None, False, 4, "\u6587\u672c", [], [1, {"x": "y"}]):
            with self.subTest(value=value):
                tr._atomic_write_json(str(self.config), value)
                self.assertEqual(self.read_json(self.config), value)
        self.assert_no_temps()

    def test_missing_parent_is_not_created(self):
        target = self.root / "absent" / "config.json"
        with self.assertRaises(FileNotFoundError):
            tr._atomic_write_json(str(target), {})
        self.assertFalse(target.parent.exists())
        self.assert_no_temps()

    def assert_failed_write(self, stage, cleanup_fails=False):
        self.seed(self.config, {"old": "\u4fdd\u7559"})
        original = self.config.read_bytes()
        sibling = self.root / ".tmp_someone_else.json"
        sibling.write_bytes(b"unrelated operation")
        error = OSError(f"injected {stage}")
        allocated = []
        real_mkstemp, real_fdopen, real_close = tempfile.mkstemp, os.fdopen, os.close

        def mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            allocated.append((fd, path))
            return fd, path

        def partial_dump(data, stream, **kwargs):
            stream.write('{"partially written":')
            raise error

        def faulty_stream(fd, *args, **kwargs):
            return _Stream(real_fdopen(fd, *args, **kwargs), [], stage, error)

        def close_then_fail(fd):
            real_close(fd)
            raise error

        target, name, effect = {
            "dump": (tr.json, "dump", partial_dump),
            "fdopen": (tr.os, "fdopen", error),
            "flush": (tr.os, "fdopen", faulty_stream),
            "fsync": (tr.os, "fsync", error),
            "stream-close": (tr.os, "fdopen", faulty_stream),
            "fd-close": (tr.os, "close", close_then_fail),
            "replace": (tr.os, "replace", error),
        }[stage]
        with mock.patch.object(tr.tempfile, "mkstemp", side_effect=mkstemp), \
                mock.patch.object(target, name, side_effect=effect), \
                mock.patch.object(
                    tr.os, "remove", wraps=os.remove,
                    side_effect=PermissionError("cleanup denied") if cleanup_fails else None,
                ) as remove:
            with self.assertRaises(OSError) as raised:
                tr._atomic_write_json(str(self.config), {"new": "\u4e0d\u843d\u76d8"})
        self.assertIs(raised.exception, error)
        self.assertEqual(len(allocated), 1)
        fd, temporary = allocated[0]
        with self.assertRaises(OSError) as closed:
            os.fstat(fd)
        self.assertEqual(closed.exception.errno, errno.EBADF)
        remove.assert_called_once_with(temporary)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(sibling.read_bytes(), b"unrelated operation")
        self.assertEqual(Path(temporary).exists(), cleanup_fails)
        self.assertEqual(
            set(self.root.glob(".tmp_*.json")),
            {sibling, Path(temporary)} if cleanup_fails else {sibling})

    def test_partial_dump_preserves_original_and_only_cleans_own_temp(self):
        self.assert_failed_write("dump")

    def test_fdopen_failure_releases_owned_descriptor_and_temp(self):
        self.assert_failed_write("fdopen")

    def test_flush_failure_releases_descriptor_and_temp(self):
        self.assert_failed_write("flush")

    def test_fsync_failure_preserves_original_and_cleans_temp(self):
        self.assert_failed_write("fsync")

    def test_stream_close_failure_releases_descriptor_and_temp(self):
        self.assert_failed_write("stream-close")

    def test_descriptor_close_failure_preserves_original_exception(self):
        self.assert_failed_write("fd-close")

    def test_replace_failure_preserves_original_and_cleans_temp(self):
        self.assert_failed_write("replace")

    def test_cleanup_failure_does_not_replace_original_exception(self):
        self.assert_failed_write("dump", cleanup_fails=True)

    def test_mkstemp_patch_failure_preserves_original_without_removing_other_files(self):
        self.seed(self.config, {"old": True})
        original = self.config.read_bytes()
        error = PermissionError("cannot allocate temp")
        with mock.patch.object(tr.tempfile, "mkstemp", side_effect=error) as create, \
                mock.patch.object(tr.os, "remove", wraps=os.remove) as remove:
            with self.assertRaises(PermissionError) as raised:
                tr._atomic_write_json(str(self.config), {"new": True})
        self.assertIs(raised.exception, error)
        create.assert_called_once_with(prefix=".tmp_", suffix=".json", dir=str(self.root))
        remove.assert_not_called()
        self.assertEqual(self.config.read_bytes(), original)
        self.assert_no_temps()

    def test_real_serialization_error_preserves_original(self):
        self.seed(self.config, {"old": True})
        original = self.config.read_bytes()
        with self.assertRaises(TypeError):
            tr._atomic_write_json(str(self.config), {"unsupported": object()})
        self.assertEqual(self.config.read_bytes(), original)
        self.assert_no_temps()


class TestWindowsStorageConsumers(StorageTestCase):
    def test_save_and_load_roundtrip_uses_real_shared_writer(self):
        config = dict(tr.DEFAULT_CONFIG)
        config.update({tr.CFG.FONT_SIZE: 16, tr.CFG.THEME: "dark", "unknown": "\u4fdd\u7559"})
        with mock.patch.object(tr.os, "replace", wraps=os.replace) as replace:
            tr.save_config(config)
            self.assertEqual(self.read_json(self.config), config)
            loaded = tr.load_config()
        self.assertEqual(loaded[tr.CFG.FONT_SIZE], 16)
        self.assertEqual(loaded[tr.CFG.THEME], "dark")
        self.assertEqual(loaded["unknown"], "\u4fdd\u7559")
        replace.assert_called_once()
        self.assertEqual(replace.call_args.args[1], str(self.config))
        self.assertFalse(self.log.exists())
        self.assert_no_temps()

    def test_load_config_persists_only_existing_migrations_and_runs_once(self):
        cfg = tr.CFG
        original = {
            cfg.UI_V2: False, cfg.SUMMARY_ENABLED: False,
            cfg.CLIPBOARD_PROTECTION_ENABLED: False,
            cfg.CODEX_STREAMING_EXPERIMENTAL: False, cfg.FONT_SIZE: 16,
            "unknown": {"unchanged": "\u4e2d\u6587"},
        }
        self.seed(self.config, original)
        expected = dict(original)
        expected.update({
            cfg.UI_V2: True, cfg.UI_V2_DEFAULT_MIGRATED: True,
            cfg.SUMMARY_ENABLED: True, cfg.CLIPBOARD_PROTECTION_ENABLED: True,
            cfg.LABS_DEFAULTS_MIGRATED: True, cfg.CODEX_STREAMING_EXPERIMENTAL: True,
        })
        with mock.patch.object(tr.tempfile, "mkstemp", wraps=tempfile.mkstemp) as create:
            loaded = tr.load_config()
            self.assertEqual(self.read_json(self.config), expected)
            self.assertEqual(loaded[cfg.FONT_SIZE], 16)
            for key in (cfg.UI_V2, cfg.SUMMARY_ENABLED, cfg.CLIPBOARD_PROTECTION_ENABLED,
                        cfg.CODEX_STREAMING_EXPERIMENTAL):
                self.assertIs(loaded[key], True)
            tr.load_config()
        create.assert_called_once()
        for key in (cfg.UI_V2, cfg.SUMMARY_ENABLED, cfg.CLIPBOARD_PROTECTION_ENABLED):
            expected[key] = False
        tr.save_config(expected)
        with mock.patch.object(tr.os, "replace", wraps=os.replace) as replace:
            loaded = tr.load_config()
        replace.assert_not_called()
        self.assertEqual(self.read_json(self.config), expected)
        for key in (cfg.UI_V2, cfg.SUMMARY_ENABLED, cfg.CLIPBOARD_PROTECTION_ENABLED):
            self.assertIs(loaded[key], False)
        self.assert_no_temps()

    def test_legacy_model_migration_and_unknown_keys_are_preserved(self):
        self.seed(self.config, {tr.CFG.MODEL: "opus", "unknown": "\u4fdd\u7559"})
        loaded = tr.load_config()
        self.assertEqual(loaded[tr.CFG.MODEL_PROVIDER], "claude_cli")
        self.assertEqual(loaded[tr.CFG.CLAUDE_MODEL], "opus")
        self.assertEqual(loaded["unknown"], "\u4fdd\u7559")
        self.assertEqual(self.read_json(self.config)["unknown"], "\u4fdd\u7559")
        self.assert_no_temps()

    def test_missing_and_corrupt_config_fall_back_without_overwriting_input(self):
        with mock.patch.object(tr.tempfile, "mkstemp", wraps=tempfile.mkstemp) as create:
            self.assertEqual(tr.load_config(), tr.Config())
            self.assertFalse(self.config.exists())
            self.config.write_bytes(b"{broken")
            self.assertEqual(tr.load_config(), tr.Config())
        create.assert_not_called()
        self.assertEqual(self.config.read_bytes(), b"{broken")
        self.assertIn("[load_config] JSONDecodeError:", self.log.read_text(encoding="utf-8"))

    def test_failed_migration_keeps_old_disk_and_returns_migrated_config(self):
        self.seed(self.config, {tr.CFG.UI_V2: False})
        original = self.config.read_bytes()
        with mock.patch.object(tr.os, "replace", side_effect=PermissionError("locked")):
            loaded = tr.load_config()
        self.assertTrue(loaded[tr.CFG.UI_V2])
        self.assertEqual(self.config.read_bytes(), original)
        self.assertIn("[save_config] PermissionError: locked", self.log.read_text(encoding="utf-8"))
        self.assert_no_temps()

    def test_replacing_entry_alias_still_controls_all_consumers_and_forwards_to_real_writer(self):
        calls = []

        def replacement(path, data):
            calls.append((path, data))
            return storage.atomic_write_json(path, data)

        with mock.patch.object(tr, "_atomic_write_json", replacement):
            tr.save_config({"unknown": "explicit save"})
            tr.load_config()
            tr.add_history("hello", "\u4f60\u597d", False, 5)
        self.assertEqual([path for path, _ in calls], [
            str(self.config), str(self.config), str(self.history)])
        self.assertEqual(self.read_json(self.config), calls[1][1])
        self.assertEqual(self.read_json(self.history), calls[2][1])
        self.assertIs(tr._atomic_write_json, storage.atomic_write_json)
        self.assert_no_temps()

    def test_entry_alias_failure_injection_keeps_consumer_error_boundaries(self):
        self.seed(self.config, {"old": True})
        self.seed(self.history, [{"input": "old", "output": "\u4fdd\u7559"}])
        before = (self.config.read_bytes(), self.history.read_bytes())
        error = RuntimeError("replacement seam")
        with mock.patch.object(tr, "_atomic_write_json", side_effect=error) as writer:
            tr.save_config({"new": True})
            tr.add_history("new", "\u4e0d\u843d\u76d8", False, 5)
        self.assertEqual(writer.call_count, 2)
        self.assertEqual(before, (self.config.read_bytes(), self.history.read_bytes()))
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("[save_config] RuntimeError: replacement seam", log)
        self.assertIn("[add_history] RuntimeError: replacement seam", log)

    def test_json_replace_and_mkstemp_seams_reach_both_real_consumers(self):
        for module, name in ((tr.json, "dump"), (tr.os, "replace"), (tr.tempfile, "mkstemp")):
            for consumer in ("save_config", "add_history"):
                with self.subTest(seam=name, consumer=consumer):
                    target = self.config if consumer == "save_config" else self.history
                    self.seed(target, {"old": True} if consumer == "save_config" else [])
                    original = target.read_bytes()
                    error = OSError(f"{consumer}:{name}")
                    with mock.patch.object(module, name, side_effect=error) as seam:
                        if consumer == "save_config":
                            tr.save_config({"new": True})
                        else:
                            tr.add_history("new", "\u65b0", False, 5)
                    seam.assert_called_once()
                    self.assertEqual(target.read_bytes(), original)
                    self.assertIn(
                        f"[{consumer}] OSError: {consumer}:{name}",
                        self.log.read_text(encoding="utf-8"))
                    self.assert_no_temps()

    def test_history_real_writes_preserve_order_fields_kinds_and_limit(self):
        cases = (
            ("text", False, False, None),
            ("dict", True, False, None),
            ("code", False, True, "invalid"),
            ("ocr", False, False, "ocr"),
        )
        for expected, is_dict, is_code, kind in cases:
            tr.add_history(expected, "\u8bd1\u6587", is_dict, 3, is_code=is_code, kind=kind, sig="sig")
            entry = tr.load_history()[0]
            self.assertEqual(set(entry), {"ts", "input", "output", "is_dict", "is_code", "kind", "sig"})
            self.assertRegex(entry["ts"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
            self.assertEqual(entry["kind"], expected)
            self.assertEqual(entry["sig"], "sig")
            self.assertEqual(entry["is_dict"], is_dict)
            self.assertEqual(entry["is_code"], is_code)
        self.assertEqual([entry["input"] for entry in tr.load_history()], ["ocr", "code", "dict"])
        for limit in (0, -1, "1"):
            tr.add_history(None, None, False, limit)
            entries = self.read_json(self.history)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["input"], "")
            self.assertEqual(entries[0]["output"], "")
            self.assertEqual(entries[0]["sig"], "")
        self.assert_no_temps()

    def test_history_missing_malformed_and_non_list_behavior(self):
        self.assertEqual(tr.load_history(), [])
        self.history.write_bytes(b"invalid history")
        self.assertEqual(tr.load_history(), [])
        self.assertIn("[load_history] JSONDecodeError:", self.log.read_text(encoding="utf-8"))
        self.seed(self.history, {"not": "a list"})
        self.assertEqual(tr.load_history(), [])

    def test_concurrent_real_history_consumers_keep_existing_lock_and_all_entries(self):
        lock = tr._HISTORY_LOCK
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda n: tr.add_history(str(n), "\u8bd1\u6587", False, 20), range(12)))
        entries = self.read_json(self.history)
        self.assertEqual(len(entries), 12)
        self.assertEqual({entry["input"] for entry in entries}, {str(n) for n in range(12)})
        self.assertIs(tr._HISTORY_LOCK, lock)
        self.assertFalse(self.log.exists())
        self.assert_no_temps()

    def test_log_error_really_appends_unicode_without_atomic_json(self):
        with mock.patch.object(tr.tempfile, "mkstemp", wraps=tempfile.mkstemp) as create:
            tr.log_error("first", ValueError("\u9519\u8bef"))
            tr.log_error("second", RuntimeError("second failure"))
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("[first] ValueError: \u9519\u8bef"))
        self.assertTrue(lines[1].endswith("[second] RuntimeError: second failure"))
        create.assert_not_called()

    def test_log_error_still_swallows_its_own_open_failure(self):
        with mock.patch.object(core, "open", create=True, side_effect=PermissionError("denied")) as opened:
            tr.log_error("cannot_log", RuntimeError("original"))
        opened.assert_called_once_with(str(self.log), "a", encoding="utf-8")
        self.assertFalse(self.log.exists())


class TestWindowsDataPaths(StorageTestCase):
    def test_default_windows_data_directory_precedence_and_fallbacks(self):
        roaming = self.root / "Roaming"
        local = self.root / "Local"
        for values, expected in (
                ({"APPDATA": str(roaming), "LOCALAPPDATA": str(local)}, roaming / core.APP_NAME),
                ({"APPDATA": "", "LOCALAPPDATA": str(local)}, local / core.APP_NAME),
                ({"LOCALAPPDATA": str(local)}, local / core.APP_NAME),
                ({}, self.legacy)):
            with self.subTest(values=values), mock.patch.dict(os.environ, values, clear=True):
                self.assertEqual(tr._resolve_data_dir(), str(expected))
                self.assertTrue(expected.is_dir())
        self.assertEqual(tuple(inspect.signature(tr._resolve_data_dir).parameters), ())

    def test_failed_windows_directory_creation_falls_back_to_app_directory(self):
        with mock.patch.dict(os.environ, {"APPDATA": str(self.root / "blocked")}, clear=True), \
                mock.patch.object(core.os, "makedirs", side_effect=PermissionError("denied")):
            self.assertEqual(tr._resolve_data_dir(), str(self.legacy))

    def test_legacy_config_and_history_move_then_reopen_without_content_changes(self):
        for name, data in (("config.json", {"legacy": "\u914d\u7f6e"}),
                           ("history.json", [{"input": "\u65e7"}])):
            old, new = self.legacy / name, self.root / name
            self.seed(old, data)
            original = old.read_bytes()
            self.assertEqual(tr._user_data_path(name), str(new))
            self.assertFalse(old.exists())
            self.assertEqual(new.read_bytes(), original)
            self.assertEqual(self.read_json(new), data)

    def test_existing_destination_is_not_replaced_by_legacy_file(self):
        old = self.legacy / "config.json"
        self.seed(old, {"old": True})
        self.seed(self.config, {"new": True})
        with mock.patch.object(core.shutil, "move", wraps=core.shutil.move) as move, \
                mock.patch.object(core.shutil, "copy2", wraps=core.shutil.copy2) as copy:
            self.assertEqual(tr._user_data_path("config.json"), str(self.config))
        move.assert_not_called()
        copy.assert_not_called()
        self.assertEqual(self.read_json(self.config), {"new": True})
        self.assertEqual(self.read_json(old), {"old": True})

    def test_failed_legacy_move_falls_back_to_real_copy(self):
        old = self.legacy / "config.json"
        self.seed(old, {"legacy": "\u4fdd\u7559"})
        with mock.patch.object(core.shutil, "move", side_effect=PermissionError("locked")) as move, \
                mock.patch.object(core.shutil, "copy2", wraps=core.shutil.copy2) as copy:
            self.assertEqual(tr._user_data_path("config.json"), str(self.config))
        move.assert_called_once_with(str(old), str(self.config))
        copy.assert_called_once_with(str(old), str(self.config))
        self.assertEqual(self.config.read_bytes(), old.read_bytes())

    def test_failed_move_and_copy_remain_best_effort(self):
        old = self.legacy / "config.json"
        self.seed(old, {"legacy": True})
        with mock.patch.object(core.shutil, "move", side_effect=PermissionError("move denied")), \
                mock.patch.object(core.shutil, "copy2", side_effect=PermissionError("copy denied")):
            self.assertEqual(tr._user_data_path("config.json"), str(self.config))
        self.assertEqual(self.read_json(old), {"legacy": True})
        self.assertFalse(self.config.exists())
        self.assertFalse(self.log.exists())

    def test_no_migration_when_paths_match_or_legacy_file_is_missing(self):
        with mock.patch.object(core.shutil, "move", wraps=core.shutil.move) as move, \
                mock.patch.object(core.shutil, "copy2", wraps=core.shutil.copy2) as copy:
            self.assertEqual(tr._user_data_path("absent.json"), str(self.root / "absent.json"))
            with mock.patch.object(core, "DATA_DIR", str(self.legacy)):
                self.assertEqual(tr._user_data_path("config.json"), str(self.legacy / "config.json"))
        move.assert_not_called()
        copy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
