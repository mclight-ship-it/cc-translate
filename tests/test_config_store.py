"""Portable explicit-path configuration persistence and operation-lock contracts."""

import builtins
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from cc_config import CFG, Config, DEFAULT_CONFIG
import cc_config_store as store
import cc_storage

if __package__:
    from .test_history import ObservedLock
else:
    from test_history import ObservedLock


class TestConfigRepository(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix=".cc-config-store-", dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "config \u4e2d # %.json"
        self.repo = store.ConfigRepository(self.path, lock=ObservedLock())
        self.addCleanup(self.repo.close)

    def seed(self, raw):
        self.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path.read_bytes()

    def assert_unchanged(self, before):
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_import_does_not_access_paths_environment_or_files(self):
        source = compile(Path(store.__file__).read_text(encoding="utf-8"), store.__file__, "exec")
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {"cc_core", "tkinter", "cc_providers", "fcntl"}:
                raise AssertionError("unexpected platform dependency")
            return original(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded), \
                mock.patch("builtins.open", side_effect=AssertionError("import opened file")), \
                mock.patch.object(Path, "home", side_effect=AssertionError("implicit home")), \
                mock.patch.object(Path, "resolve", side_effect=AssertionError("path resolution")), \
                mock.patch.object(os, "getenv", side_effect=AssertionError("environment")), \
                mock.patch.object(os, "open", side_effect=AssertionError("descriptor")), \
                mock.patch.object(os, "mkdir", side_effect=AssertionError("directory")):
            namespace = {"__name__": "synthetic_config_store_import"}
            exec(source, namespace)
        self.assertIn("ConfigRepository", namespace)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_explicit_path_and_constructor_close_do_not_touch_disk(self):
        with self.assertRaises(TypeError):
            store.ConfigRepository()
        missing = self.root / "not-created" / "config.json"
        with mock.patch("builtins.open", side_effect=AssertionError("constructor read")), \
                mock.patch.object(os, "mkdir", side_effect=AssertionError("directory")), \
                mock.patch.object(Path, "home", side_effect=AssertionError("implicit home")):
            with store.ConfigRepository(missing) as repo:
                self.assertEqual(repo.path, missing)
                self.assertFalse(repo._closed)
                with repo._lock:
                    with repo._lock:
                        repo._ensure_open()
            repo.close()
            self.assertTrue(repo._closed)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_returns_independent_defaults_without_writing_or_planning(self):
        with mock.patch.object(store, "atomic_write_json") as writer, \
                mock.patch.object(store, "plan_config_migration") as planner:
            first = self.repo.load()
            self.assertIsInstance(first, Config)
            self.assertEqual(first, Config())
            self.assertIs(first[CFG.CODEX_MODEL_DEFAULT_MIGRATED], True)
            first["future"] = {"nested": []}
            first["font_size"] = 90
            self.assertEqual(self.repo.load(), Config())
            writer.assert_not_called()
            planner.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_parent_is_not_created_by_load_or_save(self):
        with store.ConfigRepository(self.root / "absent" / "config.json") as repo:
            self.assertEqual(repo.load(), Config())
            with self.assertRaises(FileNotFoundError):
                repo.save({})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_old_configuration_migrates_once_with_exact_raw_bytes_and_order(self):
        raw = {
            "font_size": "16", "future": {"nested": ["\u4e2d", 7]},
            "model": "opus", "codex_model": "gpt-5.4-mini",
            "ui_v2": False, "summary_enabled": False,
            "codex_streaming_experimental": False,
        }
        self.seed(raw)
        expected = dict(raw)
        expected.update({
            "ui_v2": True, "ui_v2_default_migrated": True,
            "summary_enabled": True, "clipboard_protection_enabled": True,
            "labs_defaults_migrated": True, "codex_streaming_experimental": True,
            "codex_model_default_migrated": True, "codex_model": "auto-fast",
        })
        with mock.patch.object(store, "atomic_write_json", wraps=cc_storage.atomic_write_json) as writer:
            cfg = self.repo.load()
            writer.assert_called_once_with(self.path, expected)
            self.assertEqual(cfg.font_size, 16)
            self.assertEqual(cfg.model_provider, "claude_cli")
            self.assertEqual(cfg.codex_model, "auto-fast")
            self.assertIs(cfg[CFG.CODEX_MODEL_DEFAULT_MIGRATED], True)
            self.assertIs(cfg["codex_streaming_experimental"], True)
            expected_bytes = json.dumps(expected, ensure_ascii=False, indent=2).replace(
                "\n", os.linesep).encode("utf-8")
            self.assertEqual(self.path.read_bytes(), expected_bytes)
            self.assertEqual(list(json.loads(self.path.read_bytes())), list(expected))
            cfg["future"]["nested"].append("returned mutation")
            self.assertEqual(self.repo.load()["future"], raw["future"])
            with store.ConfigRepository(self.path) as reopened:
                self.assertEqual(reopened.load()["future"], raw["future"])
            self.assertEqual(writer.call_count, 1)
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_raw_migration_validation_failure_preserves_file_and_error(self):
        validator = mock.Mock(side_effect=ValueError("synthetic migration rejected"))
        self.repo.load(validate_migration=validator)
        validator.assert_not_called()
        before = self.seed({"font_size": "16", "future": {"kept": 7}})
        with mock.patch.object(store, "atomic_write_json") as writer:
            with self.assertRaises(ValueError) as caught:
                self.repo.load(validate_migration=validator)
            self.assertIs(caught.exception, validator.side_effect)
            writer.assert_not_called()
        payload = validator.call_args.args[0]
        self.assertEqual(payload["font_size"], "16")
        self.assertEqual(payload["future"], {"kept": 7})
        self.assertTrue(payload["ui_v2_default_migrated"])
        self.assertNotIn("model_provider", payload)
        self.assert_unchanged(before)
        self.assertEqual(self.repo.load().font_size, 16)

    def test_markers_preserve_opt_out_and_no_migration_preserves_original_bytes(self):
        for marker in (False, True, 0, 1, "false", "true"):
            with self.subTest(marker=marker):
                raw = {
                    "future": {"first": [1]}, "ui_v2_default_migrated": marker,
                    "ui_v2": False, "labs_defaults_migrated": marker,
                    "summary_enabled": False, "clipboard_protection_enabled": False,
                    "font_size": "16", "codex_model_default_migrated": marker,
                }
                before = json.dumps(raw, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                self.path.write_bytes(before)
                with mock.patch.object(store, "atomic_write_json") as writer:
                    cfg = self.repo.load()
                    writer.assert_not_called()
                self.assertFalse(cfg["ui_v2"])
                self.assertFalse(cfg.summary_enabled)
                self.assertFalse(cfg["clipboard_protection_enabled"])
                self.assertEqual(cfg.font_size, 16)
                self.assert_unchanged(before)

    def test_null_migration_markers_are_invalid_booleans_not_absent_markers(self):
        for key in ("ui_v2_default_migrated", "labs_defaults_migrated", "codex_model_default_migrated"):
            with self.subTest(key=key):
                raw = dict(DEFAULT_CONFIG, ui_v2=False, summary_enabled=False, **{key: None})
                before = self.seed(raw)
                with mock.patch.object(store, "plan_config_migration") as planner, \
                        mock.patch.object(store, "atomic_write_json") as writer:
                    with self.assertRaisesRegex(TypeError, "config_boolean_value_required: " + key):
                        self.repo.load()
                    planner.assert_not_called()
                    writer.assert_not_called()
                self.assert_unchanged(before)

    def test_explicit_mini_survives_strict_normalize_save_load_and_reopen(self):
        for initial in (None, {"codex_model": "gpt-5.4-mini"}):
            with self.subTest(initial=initial):
                cfg = store.normalize_config(initial)
                self.assertEqual(cfg.codex_model, "auto-fast")
                self.assertIs(cfg[CFG.CODEX_MODEL_DEFAULT_MIGRATED], True)
                cfg[CFG.CODEX_MODEL] = "gpt-5.4-mini"
                cfg["future"] = {"nested": ["\u4e2d", 7]}
                normalized = store.normalize_config(cfg)
                self.assertEqual(normalized.codex_model, "gpt-5.4-mini")
                self.assertIs(normalized["future"], cfg["future"])
                self.repo.save(normalized)
                before = self.path.read_bytes()
                with mock.patch.object(store, "atomic_write_json") as writer:
                    for loaded in (self.repo.load(), store.normalize_config(self.repo.load())):
                        self.assertEqual(loaded.codex_model, "gpt-5.4-mini")
                        self.assertIs(loaded[CFG.CODEX_MODEL_DEFAULT_MIGRATED], True)
                        self.assertEqual(loaded["future"], {"nested": ["\u4e2d", 7]})
                    with store.ConfigRepository(self.path) as reopened:
                        self.assertEqual(reopened.load().codex_model, "gpt-5.4-mini")
                    writer.assert_not_called()
                self.assert_unchanged(before)

    def test_legacy_mini_migration_then_explicit_selection_is_persistent(self):
        self.seed({"codex_model": "gpt-5.4-mini", "future": {"first": [1]}})
        migrated = self.repo.load()
        self.assertEqual(migrated.codex_model, "auto-fast")
        saved = json.loads(self.path.read_bytes())
        self.assertEqual(saved["codex_model"], "auto-fast")
        self.assertIs(saved["codex_model_default_migrated"], True)
        migrated["codex_model"] = "gpt-5.4-mini"
        self.repo.save(migrated)
        before = self.path.read_bytes()
        with mock.patch.object(store, "atomic_write_json") as writer:
            self.assertEqual(self.repo.load().codex_model, "gpt-5.4-mini")
            with store.ConfigRepository(self.path) as reopened:
                self.assertEqual(store.normalize_config(reopened.load()).codex_model, "gpt-5.4-mini")
            writer.assert_not_called()
        self.assert_unchanged(before)

    def test_other_model_ids_only_add_marker_once_then_preserve_exact_bytes(self):
        for model in ("auto-fast", "auto", "gpt-5.4", "gpt-5.4-mini ",
                      "GPT-5.4-mini", "vendor/custom-\u4e2d"):
            with self.subTest(model=model):
                raw = {"codex_model": model, "font_size": "16", "future": {"nested": ["\u4e2d", 7]},
                       "ui_v2_default_migrated": True, "labs_defaults_migrated": True}
                self.seed(raw)
                expected = dict(raw, codex_model_default_migrated=True)
                expected_bytes = json.dumps(expected, ensure_ascii=False, indent=2).replace(
                    "\n", os.linesep).encode("utf-8")
                with mock.patch.object(store, "atomic_write_json", wraps=cc_storage.atomic_write_json) as writer:
                    self.assertEqual(self.repo.load().codex_model, model)
                    writer.assert_called_once_with(self.path, expected)
                    self.assert_unchanged(expected_bytes)
                    self.assertEqual(self.repo.load().codex_model, model)
                    with store.ConfigRepository(self.path) as reopened:
                        self.assertEqual(reopened.load().codex_model, model)
                    self.assertEqual(writer.call_count, 1)
                self.assert_unchanged(expected_bytes)

    def test_present_model_marker_uses_strict_boolean_coercion_without_rewriting_mini(self):
        for marker, expected in ((True, True), (False, False), (0, False), (2, True),
                                 (" YES ", True), ("false", False), ("", False)):
            with self.subTest(marker=marker):
                raw = dict(DEFAULT_CONFIG, codex_model="gpt-5.4-mini", codex_model_default_migrated=marker)
                before = self.seed(raw)
                with mock.patch.object(store, "atomic_write_json") as writer:
                    cfg = self.repo.load()
                    self.assertEqual(cfg.codex_model, "gpt-5.4-mini")
                    self.assertIs(cfg[CFG.CODEX_MODEL_DEFAULT_MIGRATED], expected)
                    self.assertEqual(store.normalize_config(cfg).codex_model, "gpt-5.4-mini")
                    writer.assert_not_called()
                self.assert_unchanged(before)
        for marker in (None, [], {}):
            with self.subTest(invalid_marker=marker):
                before = self.seed(dict(DEFAULT_CONFIG, codex_model="gpt-5.4-mini",
                                        codex_model_default_migrated=marker))
                with mock.patch.object(store, "plan_config_migration") as planner, \
                        mock.patch.object(store, "atomic_write_json") as writer:
                    with self.assertRaisesRegex(TypeError, "config_boolean_value_required: codex_model_default_migrated"):
                        self.repo.load()
                    planner.assert_not_called()
                    writer.assert_not_called()
                self.assert_unchanged(before)

    def test_model_migration_respects_normalized_and_raw_payload_byte_validators(self):
        raw = {"codex_model": "gpt-5.4-mini", "future": "\u4e2d" * 10,
               "ui_v2_default_migrated": True, "labs_defaults_migrated": True}
        normalized = dict(store.normalize_config(raw))
        payload = dict(raw, codex_model="auto-fast", codex_model_default_migrated=True)

        def wire_size(value):
            return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

        for callback, expected in (("validate", normalized), ("validate_migration", payload)):
            for limit in (wire_size(expected) - 1, wire_size(expected)):
                with self.subTest(callback=callback, limit=limit):
                    before = self.seed(raw)
                    error = ValueError("synthetic config byte budget")

                    def validate(value):
                        self.assertEqual(list(value.items()), list(expected.items()))
                        if wire_size(value) > limit:
                            raise error

                    validator = mock.Mock(side_effect=validate)
                    with mock.patch.object(store, "atomic_write_json", wraps=cc_storage.atomic_write_json) as writer:
                        if limit < wire_size(expected):
                            with self.assertRaises(ValueError) as caught:
                                self.repo.load(**{callback: validator})
                            self.assertIs(caught.exception, error)
                            writer.assert_not_called()
                            self.assert_unchanged(before)
                        else:
                            self.assertEqual(self.repo.load(**{callback: validator}).codex_model, "auto-fast")
                            writer.assert_called_once_with(self.path, payload)
                    validator.assert_called_once()

    def test_model_migration_failed_replace_preserves_file_and_can_retry(self):
        before = self.seed({"codex_model": "gpt-5.4-mini", "future": {"nested": [1]}})
        error = PermissionError("synthetic model marker replace")
        with mock.patch.object(cc_storage.os, "replace", side_effect=error):
            with self.assertRaises(PermissionError) as caught:
                self.repo.load()
            self.assertIs(caught.exception, error)
        self.assert_unchanged(before)
        self.assertEqual(self.repo.load().codex_model, "auto-fast")
        saved = json.loads(self.path.read_bytes())
        self.assertIs(saved[CFG.CODEX_MODEL_DEFAULT_MIGRATED], True)
        self.assertEqual(saved["codex_model"], "auto-fast")
        self.assertEqual(saved["future"], {"nested": [1]})
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_explicit_save_writes_raw_snapshot_returns_none_and_load_normalizes(self):
        raw = {"font_size": "16", "future": {"values": ["\u4e2d", {"enabled": True}]}}
        expected = json.dumps(raw, ensure_ascii=False, indent=2)
        with mock.patch.object(store, "_StrictConfig", side_effect=AssertionError("save normalized")), \
                mock.patch.object(store, "plan_config_migration", side_effect=AssertionError("save planned")):
            self.assertIsNone(self.repo.save(raw))
        self.assertEqual(self.path.read_bytes(), expected.replace("\n", os.linesep).encode("utf-8"))
        raw["future"]["values"][1]["enabled"] = False
        raw["future"]["values"].append("caller mutation")
        loaded = self.repo.load()
        self.assertEqual(loaded.font_size, 16)
        self.assertEqual(loaded["future"]["values"], ["\u4e2d", {"enabled": True}])
        loaded["future"]["values"][1]["enabled"] = False
        with store.ConfigRepository(self.path) as reopened:
            self.assertEqual(reopened.load()["future"]["values"], ["\u4e2d", {"enabled": True}])

    def test_snapshot_detaches_every_nested_container_before_writer(self):
        shared = {"values": [1, {"child": []}]}
        raw = {"first": shared, "second": shared}
        snapshots = []

        def writer(path, snapshot):
            self.assertEqual(path, self.path)
            self.assertIsNot(snapshot, raw)
            self.assertIsNot(snapshot["first"], shared)
            self.assertIsNot(snapshot["first"]["values"], shared["values"])
            self.assertIsNot(snapshot["first"]["values"][1], shared["values"][1])
            self.assertIsNot(snapshot["first"]["values"][1]["child"], shared["values"][1]["child"])
            self.assertIsNot(snapshot["first"], snapshot["second"])
            shared["values"][1]["child"].append("input changed after snapshot")
            self.assertEqual(snapshot["first"]["values"][1]["child"], [])
            cc_storage.atomic_write_json(path, snapshot)
            snapshots.append(snapshot)

        with mock.patch.object(store, "atomic_write_json", side_effect=writer):
            self.assertIsNone(self.repo.save(raw))
        snapshots[0]["first"]["values"].append("writer-held mutation")
        self.assertEqual(json.loads(self.path.read_bytes()),
                         {"first": {"values": [1, {"child": []}]},
                          "second": {"values": [1, {"child": []}]}})
        self.assertEqual(set(vars(self.repo)), {"_path", "_lock", "_closed"})

    def test_json_serializable_tuples_and_keys_use_json_snapshot_rules(self):
        self.repo.save({2: ("first", {"nested": (True, None)})})
        self.assertEqual(json.loads(self.path.read_bytes()),
                         {"2": ["first", {"nested": [True, None]}]})

    def test_nonfinite_numbers_follow_existing_json_primitive_contract(self):
        raw = {"double_press_window": float("inf"),
               "future": [float("inf"), float("-inf"), float("nan")]}
        self.repo.save(raw)
        self.assertEqual(self.path.read_text(encoding="utf-8"),
                         json.dumps(raw, ensure_ascii=False, indent=2))
        cfg = self.repo.load()
        self.assertEqual(cfg.double_press_window, float("inf"))
        self.assertEqual(cfg["future"][:2], [float("inf"), float("-inf")])
        self.assertTrue(math.isnan(cfg["future"][2]))

    def test_save_uses_real_atomic_replace_and_no_temporary_remains(self):
        before = self.seed({"future": "old"})
        expected = {"future": {"new": ["\u4e2d"]}, "font_size": "18"}
        real_replace = os.replace
        replacements = []

        def replace(source, destination):
            source = Path(source)
            self.assertEqual(Path(destination), self.path)
            self.assertEqual(source.parent, self.root)
            self.assertNotEqual(source, self.path)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(json.loads(source.read_bytes()), expected)
            real_replace(source, destination)
            replacements.append(source)

        with mock.patch.object(cc_storage.os, "replace", side_effect=replace):
            self.repo.save(expected)
        self.assertEqual(len(replacements), 1)
        self.assertFalse(replacements[0].exists())
        self.assertEqual(json.loads(self.path.read_bytes()), expected)
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_corrupt_json_and_encoding_errors_propagate_without_overwrite(self):
        for before, error in ((b"{broken", json.JSONDecodeError),
                              (b'{"font_size":', json.JSONDecodeError),
                              (b"\xff", UnicodeDecodeError)):
            with self.subTest(before=before):
                self.path.write_bytes(before)
                with mock.patch.object(store, "atomic_write_json") as writer:
                    with self.assertRaises(error):
                        self.repo.load()
                    writer.assert_not_called()
                self.assert_unchanged(before)

    def test_non_object_json_is_not_treated_as_missing_or_overwritten(self):
        for raw in (None, False, 5, "text", [], [["font_size", 12]]):
            with self.subTest(raw=raw):
                before = self.seed(raw)
                with mock.patch.object(store, "atomic_write_json") as writer:
                    with self.assertRaisesRegex(store.ConfigFormatError, "config_object_required"):
                        self.repo.load()
                    writer.assert_not_called()
                self.assert_unchanged(before)

    def test_read_permission_failure_propagates_unchanged(self):
        before = self.seed({"font_size": "16"})
        error = PermissionError("synthetic read permission")
        with mock.patch.object(store, "open", create=True, side_effect=error), \
                mock.patch.object(store, "atomic_write_json") as writer:
            with self.assertRaises(PermissionError) as raised:
                self.repo.load()
            self.assertIs(raised.exception, error)
            writer.assert_not_called()
        self.assert_unchanged(before)

    def test_file_not_found_during_read_is_not_missing_open(self):
        before = self.seed({"future": [1]})
        error = FileNotFoundError("synthetic stream read")
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.read.side_effect = error
        with mock.patch.object(store, "open", create=True, return_value=stream), \
                mock.patch.object(store, "atomic_write_json") as writer:
            with self.assertRaises(FileNotFoundError) as raised:
                self.repo.load()
            self.assertIs(raised.exception, error)
            writer.assert_not_called()
        self.assert_unchanged(before)

    def test_real_integer_overflow_propagates_before_migration_or_write(self):
        for key in ("font_size", "history_limit"):
            for number in ("Infinity", "-Infinity", "1e309"):
                with self.subTest(key=key, number=number):
                    before = ('{"' + key + '":' + number + ',"future":"keep"}').encode("utf-8")
                    self.path.write_bytes(before)
                    with mock.patch.object(store, "plan_config_migration") as planner, \
                            mock.patch.object(store, "atomic_write_json") as writer:
                        with self.assertRaises(OverflowError):
                            self.repo.load()
                        planner.assert_not_called()
                        writer.assert_not_called()
                    self.assert_unchanged(before)

    def test_real_float_conversion_overflow_does_not_plan_or_write(self):
        before = self.seed({"double_press_window": 10 ** 400, "future": ["keep"]})
        with mock.patch.object(store, "plan_config_migration") as planner, \
                mock.patch.object(store, "atomic_write_json") as writer:
            with self.assertRaises(OverflowError):
                self.repo.load()
            planner.assert_not_called()
            writer.assert_not_called()
        self.assert_unchanged(before)

    def test_invalid_numeric_fields_raise_without_shared_default_fallback(self):
        for key in ("font_size", "history_limit", "double_press_window"):
            for value, error in (("not-a-number", ValueError), (None, TypeError),
                                 ([], TypeError), ({}, TypeError)):
                with self.subTest(key=key, value=value):
                    raw = {key: value, "future": {"kept": [1]}}
                    before = self.seed(raw)
                    self.assertEqual(Config(raw)[key], DEFAULT_CONFIG[key])
                    with mock.patch.object(store, "plan_config_migration") as planner, \
                            mock.patch.object(store, "atomic_write_json") as writer:
                        with self.assertRaises(error):
                            self.repo.load()
                        planner.assert_not_called()
                        writer.assert_not_called()
                    self.assert_unchanged(before)

    def test_nan_integer_fields_raise_before_migration_or_write(self):
        for key in ("font_size", "history_limit"):
            with self.subTest(key=key):
                before = self.seed({key: float("nan"), "future": ["keep"]})
                with mock.patch.object(store, "plan_config_migration") as planner, \
                        mock.patch.object(store, "atomic_write_json") as writer:
                    with self.assertRaises(ValueError):
                        self.repo.load()
                    planner.assert_not_called()
                    writer.assert_not_called()
                self.assert_unchanged(before)

    def test_unsupported_boolean_types_raise_before_migration_or_write(self):
        for value in (None, [], {}, ["true"]):
            with self.subTest(value=value):
                raw = {"history_enabled": value, "future": ["keep"]}
                before = self.seed(raw)
                self.assertEqual(Config(raw).history_enabled, DEFAULT_CONFIG["history_enabled"])
                with mock.patch.object(store, "plan_config_migration") as planner, \
                        mock.patch.object(store, "atomic_write_json") as writer:
                    with self.assertRaisesRegex(TypeError, "config_boolean_value_required: history_enabled"):
                        self.repo.load()
                    planner.assert_not_called()
                    writer.assert_not_called()
                self.assert_unchanged(before)

    def test_valid_conversions_use_shared_rules_and_leave_raw_values_on_disk(self):
        for boolean in (False, True, 0, 1, 2.5, " YES ", "false", "other"):
            with self.subTest(boolean=boolean):
                raw = dict(DEFAULT_CONFIG, font_size="16", history_limit="17",
                           double_press_window="0.75", history_enabled=boolean)
                before = self.seed(raw)
                cfg = self.repo.load()
                self.assertIsInstance(cfg, Config)
                self.assertEqual(cfg, Config(raw))
                self.assertEqual((cfg.font_size, cfg.history_limit, cfg.double_press_window),
                                 (16, 17, 0.75))
                self.assert_unchanged(before)

    def test_save_remains_raw_and_invalid_field_is_rejected_only_on_load(self):
        raw = {"font_size": "invalid", "future": {"kept": [1]}}
        self.assertIsNone(self.repo.save(raw))
        before = self.path.read_bytes()
        self.assertEqual(json.loads(before), raw)
        with mock.patch.object(store, "plan_config_migration") as planner, \
                mock.patch.object(store, "atomic_write_json") as writer:
            with self.assertRaises(ValueError):
                self.repo.load()
            planner.assert_not_called()
            writer.assert_not_called()
        self.assert_unchanged(before)

    def test_normalization_and_plan_exceptions_are_not_caught(self):
        before = self.seed({"font_size": "16", "future": [1]})
        for target in ("_StrictConfig", "plan_config_migration"):
            for kind in (TypeError, ValueError, FileNotFoundError):
                with self.subTest(target=target, error=kind):
                    error = kind("synthetic " + target)
                    with mock.patch.object(store, target, side_effect=error), \
                            mock.patch.object(store, "atomic_write_json") as writer:
                        with self.assertRaises(kind) as raised:
                            self.repo.load()
                        self.assertIs(raised.exception, error)
                        writer.assert_not_called()
                    self.assert_unchanged(before)

    def test_migration_and_save_write_failures_propagate_without_default_overwrite(self):
        before = self.seed({"font_size": "16", "future": [1]})
        for operation in (self.repo.load, lambda: self.repo.save({"future": "new"})):
            for kind in (PermissionError, FileNotFoundError, UnicodeEncodeError):
                with self.subTest(operation=operation, error=kind):
                    error = (kind("utf-8", "\ud800", 0, 1, "synthetic encoding")
                             if kind is UnicodeEncodeError else kind("synthetic write"))
                    with mock.patch.object(store, "atomic_write_json", side_effect=error) as writer:
                        with self.assertRaises(kind) as raised:
                            operation()
                        self.assertIs(raised.exception, error)
                        self.assertEqual(writer.call_count, 1)
                    self.assert_unchanged(before)

    def test_real_atomic_encoding_failure_preserves_old_file(self):
        before = self.seed({"future": "old"})
        with self.assertRaises(UnicodeEncodeError):
            self.repo.save({"future": "\ud800"})
        self.assert_unchanged(before)

    def test_failed_replace_cleans_only_own_temporary_for_save_and_migration(self):
        before = self.seed({"font_size": "16", "future": [1]})
        neighbour = self.root / ".tmp_neighbour.json"
        neighbour.write_bytes(b"another operation")
        for operation in (self.repo.load, lambda: self.repo.save({"future": "new"})):
            with self.subTest(operation=operation):
                error = PermissionError("synthetic replace")
                with mock.patch.object(cc_storage.os, "replace", side_effect=error) as replace:
                    with self.assertRaises(PermissionError) as raised:
                        operation()
                    self.assertIs(raised.exception, error)
                    self.assertEqual(replace.call_count, 1)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(neighbour.read_bytes(), b"another operation")
                self.assertEqual(set(self.root.iterdir()), {self.path, neighbour})

    def test_save_rejects_non_dict_before_writing(self):
        before = self.seed({"future": "old"})
        for value in (None, [], [("font_size", 16)], "text", 1, True):
            with self.subTest(value=value):
                with mock.patch.object(store, "atomic_write_json") as writer:
                    with self.assertRaisesRegex(store.ConfigFormatError, "config_object_required"):
                        self.repo.save(value)
                    writer.assert_not_called()
                self.assert_unchanged(before)

    def test_save_rejects_unserializable_and_circular_nested_values(self):
        circular = {"future": []}
        circular["future"].append(circular)
        for raw, error in (({"future": object()}, TypeError),
                           ({"future": {1, 2}}, TypeError),
                           ({"future": b"bytes"}, TypeError),
                           ({("tuple",): 1}, TypeError),
                           (circular, ValueError)):
            with self.subTest(raw=raw):
                before = self.seed({"future": "old"})
                with mock.patch.object(store, "atomic_write_json") as writer:
                    with self.assertRaises(error):
                        self.repo.save(raw)
                    writer.assert_not_called()
                self.assert_unchanged(before)

    def test_closed_repository_rejects_all_operations_even_invalid_save(self):
        self.repo.close()
        self.repo.close()
        with mock.patch.object(store, "atomic_write_json") as writer, \
                mock.patch.object(store, "open", create=True) as reader:
            for operation in (self.repo.load, self.repo.__enter__,
                              lambda: self.repo.save(None), lambda: self.repo.save({}),
                              lambda: self.repo.save({"future": object()})):
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(RuntimeError, "config_repository_closed"):
                        operation()
            reader.assert_not_called()
            writer.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_context_exit_closes_after_success_or_failure(self):
        with self.repo as entered:
            self.assertIs(entered, self.repo)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.repo.load()
        with store.ConfigRepository(self.path) as repo:
            with self.assertRaisesRegex(ValueError, "synthetic body"):
                with repo:
                    raise ValueError("synthetic body")
            with self.assertRaisesRegex(RuntimeError, "closed"):
                repo.save({})

    def run_blocked(self, first, second, target, attribute, real):
        entered, release = threading.Event(), threading.Event()

        def blocking(*args, **kwargs):
            self.repo._lock.observe = True
            entered.set()
            if not release.wait(5):
                raise AssertionError("controlled operation was not released")
            return real(*args, **kwargs)

        self.repo._lock.observe = False
        self.repo._lock.waiter.clear()
        with mock.patch.object(target, attribute, side_effect=blocking, create=True):
            with ThreadPoolExecutor(max_workers=2) as workers:
                first_result = workers.submit(first)
                try:
                    self.assertTrue(entered.wait(3), "first operation did not reach controlled stage")
                    second_result = workers.submit(second)
                    self.assertTrue(self.repo._lock.waiter.wait(3),
                                    "competing operation did not try the shared lock")
                    self.assertFalse(second_result.done())
                finally:
                    release.set()
                return first_result.result(timeout=3), second_result.result(timeout=3)

    def test_read_normalize_plan_and_migration_write_share_one_operation_lock(self):
        stages = (("open", builtins.open), ("_StrictConfig", store._StrictConfig),
                  ("plan_config_migration", store.plan_config_migration),
                  ("atomic_write_json", cc_storage.atomic_write_json))
        replacement = dict(DEFAULT_CONFIG, future={"replacement": [1]})
        for attribute, real in stages:
            with self.subTest(stage=attribute):
                self.seed({"font_size": "16", "future": {"original": [1]}})
                loaded, saved = self.run_blocked(
                    self.repo.load, lambda: self.repo.save(replacement), store, attribute, real)
                self.assertEqual(loaded.font_size, 16)
                self.assertEqual(loaded["future"], {"original": [1]})
                self.assertIsNone(saved)
                self.assertEqual(json.loads(self.path.read_bytes()), replacement)

    def test_close_waits_for_save_snapshot_and_atomic_write(self):
        for target, attribute, real in ((store.json, "dumps", json.dumps),
                                        (store, "atomic_write_json", cc_storage.atomic_write_json)):
            with self.subTest(stage=attribute):
                with store.ConfigRepository(self.path, lock=ObservedLock()) as repo:
                    self.repo = repo
                    self.run_blocked(lambda: repo.save({"future": [1]}), repo.close,
                                     target, attribute, real)
                    self.assertEqual(json.loads(self.path.read_bytes()), {"future": [1]})
                    with self.assertRaisesRegex(RuntimeError, "closed"):
                        repo.save(None)
                    with self.assertRaisesRegex(RuntimeError, "closed"):
                        repo.load()

    def test_close_waits_for_read_and_migration(self):
        self.seed({"font_size": "16"})
        loaded, _ = self.run_blocked(self.repo.load, self.repo.close,
                                     store, "atomic_write_json", cc_storage.atomic_write_json)
        self.assertEqual(loaded.font_size, 16)
        self.assertTrue(json.loads(self.path.read_bytes())["ui_v2_default_migrated"])
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.repo.load()

    def test_close_waits_for_raw_migration_validation(self):
        self.seed({"font_size": "16"})
        validator = mock.Mock()
        validator.check = lambda payload: None
        loaded, _ = self.run_blocked(
            lambda: self.repo.load(validate_migration=lambda payload: validator.check(payload)),
            self.repo.close, validator, "check", validator.check)
        self.assertEqual(loaded.font_size, 16)
        self.assertTrue(json.loads(self.path.read_bytes())["ui_v2_default_migrated"])
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.repo.load()

    def test_load_waits_for_save_and_observes_complete_snapshot(self):
        raw = dict(DEFAULT_CONFIG, future={"nested": [1, 2]})
        _, loaded = self.run_blocked(lambda: self.repo.save(raw), self.repo.load,
                                    store, "atomic_write_json", cc_storage.atomic_write_json)
        self.assertEqual(loaded, raw)
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_shared_lock_serializes_separate_repository_instances(self):
        raw = dict(DEFAULT_CONFIG, future={"from": ["first"]})
        replacement = dict(DEFAULT_CONFIG, future={"from": ["second"]})
        with store.ConfigRepository(self.path, lock=self.repo._lock) as other:
            self.run_blocked(lambda: self.repo.save(raw), lambda: other.save(replacement),
                             store, "atomic_write_json", cc_storage.atomic_write_json)
            self.assertEqual(other.load(), replacement)
        self.assertEqual(self.repo.load(), replacement)


if __name__ == "__main__":
    unittest.main()
