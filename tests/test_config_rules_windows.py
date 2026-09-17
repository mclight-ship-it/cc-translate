"""Real Windows load/save boundaries retain the frozen configuration behavior."""

import json
import os
from unittest import mock

import cc_config as rules
from tests.test_config_rules import (
    MODEL_MARKER, config_cases, legacy_namespace, model_migration_payload, without_model_marker,
)
from tests.test_storage_windows import StorageTestCase, core, tr


class WindowsConfigRuleTests(StorageTestCase):
    def reference(self, **overrides):
        values = dict(CONFIG_PATH=str(self.config), save_config=mock.Mock(), log_error=mock.Mock())
        values.update(overrides)
        return legacy_namespace(**values)

    def test_public_exports_keep_single_class_and_constant_objects(self):
        self.assertIs(tr.Config, rules.Config)
        self.assertIs(tr.CFG, rules.CFG)
        self.assertIs(core.CFG, rules.CFG)
        self.assertIs(tr.DEFAULT_CONFIG, rules.DEFAULT_CONFIG)
        self.assertIs(core.DEFAULT_CONFIG, rules.DEFAULT_CONFIG)
        self.assertIs(tr.plan_config_migration, rules.plan_config_migration)

    def test_mutating_shared_defaults_remains_visible_to_class_and_accessors(self):
        with mock.patch.dict(tr.DEFAULT_CONFIG, {tr.CFG.FONT_SIZE: 37, "future_default": ["shared"]}):
            cfg = tr.Config()
            self.assertEqual(cfg.font_size, 37)
            self.assertIs(cfg["future_default"], tr.DEFAULT_CONFIG["future_default"])
            del cfg["font_size"]
            self.assertEqual(cfg.font_size, 37)
        self.assertNotIn("future_default", tr.DEFAULT_CONFIG)

    def test_real_load_memory_disk_bytes_and_save_count_match_frozen_loader(self):
        for raw in config_cases():
            with self.subTest(raw=raw):
                self.seed(self.config, raw)
                original = self.config.read_bytes()
                old = self.reference()
                expected = old["load_config"]()
                with mock.patch.object(tr, "save_config", wraps=tr.save_config) as save, \
                        mock.patch.object(tr, "plan_config_migration", wraps=rules.plan_config_migration) as plan:
                    actual = tr.load_config()
                self.assertEqual(list(without_model_marker(actual).items()), list(expected.items()))
                self.assertIs(actual[MODEL_MARKER], True)
                self.assertEqual(save.call_count,
                                 int(old["save_config"].called or MODEL_MARKER not in raw))
                plan.assert_called_once()
                if save.called:
                    previous = old["save_config"]
                    payload = model_migration_payload(raw, previous.call_args.args[0] if previous.called else raw)
                    save.assert_called_once_with(payload)
                    expected_bytes = json.dumps(payload, ensure_ascii=False, indent=2).replace(
                        "\n", os.linesep).encode("utf-8")
                else:
                    expected_bytes = original
                self.assertEqual(self.config.read_bytes(), expected_bytes)
                self.assertFalse(self.log.exists(),
                                 self.log.read_text(encoding="utf-8") if self.log.exists() else "")
                self.assert_no_temps()

    def test_missing_corrupt_and_non_mapping_match_old_partial_return_and_log(self):
        for raw_bytes in (None, b"{broken", b"null", b"42", b"true", b'""', b'"abc"',
                          b"[]", b'[["theme","dark"]]', b'["bad"]'):
            with self.subTest(raw_bytes=raw_bytes):
                self.config.unlink(missing_ok=True)
                if raw_bytes is not None:
                    self.config.write_bytes(raw_bytes)
                old = self.reference()
                expected = old["load_config"]()
                with mock.patch.object(tr, "save_config") as save, mock.patch.object(tr, "log_error") as log:
                    actual = tr.load_config()
                self.assertEqual(list(without_model_marker(actual).items()), list(expected.items()))
                self.assertIs(actual[MODEL_MARKER], True)
                expected_calls = old["save_config"].call_args_list
                if expected_calls:
                    self.assertEqual(len(expected_calls), 1)
                    expected_calls = [mock.call(model_migration_payload(
                        json.loads(raw_bytes), expected_calls[0].args[0]))]
                self.assertEqual(save.call_args_list, expected_calls)
                self.assertEqual(
                    [(call.args[0], type(call.args[1]), str(call.args[1])) for call in log.call_args_list],
                    [(call.args[0], type(call.args[1]), str(call.args[1]))
                     for call in old["log_error"].call_args_list])
                self.assertEqual(self.config.read_bytes() if self.config.exists() else None, raw_bytes)
                self.assert_no_temps()

    def test_read_error_keeps_old_logging_and_no_save(self):
        error = PermissionError("synthetic config read")
        old = self.reference(open=mock.Mock(side_effect=error))
        expected = old["load_config"]()
        with mock.patch.object(tr, "open", side_effect=error, create=True), \
                mock.patch.object(tr, "log_error") as log, mock.patch.object(tr, "save_config") as save:
            actual = tr.load_config()
            self.assertEqual(list(without_model_marker(actual).items()), list(expected.items()))
            self.assertIs(actual[MODEL_MARKER], True)
        log.assert_called_once_with("load_config", error)
        save.assert_not_called()

    def test_failed_migration_keeps_old_disk_and_returns_upgraded_memory(self):
        raw = {"ui_v2": False, "codex_streaming_experimental": False, "future": ["keep"]}
        self.seed(self.config, raw)
        original = self.config.read_bytes()
        error = PermissionError("synthetic config replace")
        with mock.patch.object(tr, "_atomic_write_json", side_effect=error) as writer, \
                mock.patch.object(tr, "log_error") as log:
            actual = tr.load_config()
        self.assertIs(actual["ui_v2"], True)
        self.assertIs(actual["codex_streaming_experimental"], True)
        writer.assert_called_once()
        log.assert_called_once_with("save_config", error)
        self.assertEqual(self.config.read_bytes(), original)
        self.assert_no_temps()

    def test_config_factory_and_save_entry_patch_seams_remain_live(self):
        self.seed(self.config, {"font_size": "16"})
        with mock.patch.object(tr, "Config", wraps=rules.Config) as factory, \
                mock.patch.object(tr, "save_config", wraps=tr.save_config) as save:
            cfg = tr.load_config()
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(factory.call_args_list, [mock.call(), mock.call({"font_size": "16"})])
        save.assert_called_once()
        self.assertIsInstance(cfg, tr.Config)

    def test_factory_failure_returns_original_default_config_and_logs_same_error(self):
        self.seed(self.config, {"font_size": "16"})
        default = rules.Config()
        error = RuntimeError("synthetic normalization")
        with mock.patch.object(tr, "Config", side_effect=[default, error]), \
                mock.patch.object(tr, "log_error") as log, mock.patch.object(tr, "save_config") as save:
            actual = tr.load_config()
        self.assertIs(actual, default)
        log.assert_called_once_with("load_config", error)
        save.assert_not_called()

    def test_plan_failure_returns_already_assigned_config_without_saving(self):
        self.seed(self.config, {"font_size": "16"})
        error = RuntimeError("synthetic plan")
        with mock.patch.object(tr, "plan_config_migration", side_effect=error), \
                mock.patch.object(tr, "log_error") as log, mock.patch.object(tr, "save_config") as save:
            cfg = tr.load_config()
        self.assertEqual(cfg.font_size, 16)
        log.assert_called_once_with("load_config", error)
        save.assert_not_called()

    def test_existing_instance_save_wrapper_still_uses_public_save_entry(self):
        cfg = tr.Config({"future": "keep"})
        with mock.patch.object(tr, "save_config", wraps=tr.save_config) as save:
            tr.TranslatorApp._save_config(object(), cfg)
        save.assert_called_once_with(cfg)
        self.assertEqual(self.read_json(self.config), cfg)
