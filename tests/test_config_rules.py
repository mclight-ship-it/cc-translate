"""Portable configuration contracts, including the frozen pre-extraction load plan."""

import ast
import hashlib
import inspect
import io
import itertools
import json
import math
import unittest
from unittest import mock

import cc_config as rules


# The moved class/constants are checked against the existing pre-extraction AST
# fingerprints. This old loader remains independent of the new plan function.
LEGACY_LOAD_SOURCE = '''
def load_config() -> "Config":
    cfg = Config()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        cfg = Config(raw)
        migrated = dict(raw)
        config_changed = False
        if CFG.UI_V2_DEFAULT_MIGRATED not in raw:
            migrated[CFG.UI_V2] = cfg[CFG.UI_V2]
            migrated[CFG.UI_V2_DEFAULT_MIGRATED] = cfg[
                CFG.UI_V2_DEFAULT_MIGRATED]
            config_changed = True
        if CFG.LABS_DEFAULTS_MIGRATED not in raw:
            migrated[CFG.SUMMARY_ENABLED] = cfg[CFG.SUMMARY_ENABLED]
            migrated[CFG.CLIPBOARD_PROTECTION_ENABLED] = cfg[
                CFG.CLIPBOARD_PROTECTION_ENABLED]
            migrated[CFG.LABS_DEFAULTS_MIGRATED] = cfg[
                CFG.LABS_DEFAULTS_MIGRATED]
            config_changed = True
        if not cfg[CFG.CODEX_STREAMING_EXPERIMENTAL]:
            cfg[CFG.CODEX_STREAMING_EXPERIMENTAL] = True
            migrated[CFG.CODEX_STREAMING_EXPERIMENTAL] = True
            config_changed = True
        if config_changed:
            save_config(migrated)
    except FileNotFoundError:
        pass
    except Exception as e:
        log_error("load_config", e)
    return cfg
'''

BASELINE_CONFIG_AST = {
    "CFG": "3cdc857be53d05e271b4d77edde719443cb0f50eb4368016a27d96cecd2ac104",
    "DEFAULT_CONFIG": "40150450e4a7a621640b05df3f581959a75f99f056d4e3498eaa3abe97a0b0ad",
    "Config": "f6ad2f066492fe8357c0dd5cd2971ae66dbcb3cbe1984a4a9b386bbb25420407",
    "load_config": "c6d2ede2c1f626e45563ed653143005e51329db54289dbc95dd15f21d10f0f4f",
}


def _ast_hash(node):
    def canonical(value):
        if isinstance(value, ast.AST):
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


def legacy_namespace(**overrides):
    source = inspect.getsource(rules.Config)
    assert _ast_hash(ast.parse(source).body[0]) == BASELINE_CONFIG_AST["Config"]
    values = {"CFG": rules.CFG, "DEFAULT_CONFIG": rules.DEFAULT_CONFIG, "json": json}
    exec(compile(source + "\n" + LEGACY_LOAD_SOURCE, "<frozen-config-reference>", "exec"), values)
    values.update(overrides)
    return values


def config_cases():
    yield {}
    yield {"model": "opus", "unknown": {"keep": ["\u4e2d", 7]}, "font_size": "16"}
    yield {"codex_model": "gpt-5.4-mini", "model_provider": "custom"}
    for ui_marker, labs_marker, streaming in itertools.product(
            ("missing", False, True, None), ("missing", False, True, None),
            (False, True, "false", "true", 0, 1)):
        raw = {"unknown": ["first"], "ui_v2": False, "summary_enabled": False,
               "clipboard_protection_enabled": False, "codex_streaming_experimental": streaming,
               "font_size": "16", "model": "opus"}
        if ui_marker != "missing":
            raw["ui_v2_default_migrated"] = ui_marker
        if labs_marker != "missing":
            raw["labs_defaults_migrated"] = labs_marker
        yield raw


class ConfigRuleTests(unittest.TestCase):
    def test_class_constants_and_loader_oracle_match_frozen_ast(self):
        for node in ast.parse(inspect.getsource(rules)).body + ast.parse(LEGACY_LOAD_SOURCE).body:
            name = getattr(node, "name", None)
            if isinstance(node, ast.Assign):
                name = node.targets[0].id
            if name in BASELINE_CONFIG_AST:
                with self.subTest(name=name):
                    self.assertEqual(_ast_hash(node), BASELINE_CONFIG_AST[name])

    def test_plan_statements_are_the_old_loader_statements(self):
        old_try = ast.parse(LEGACY_LOAD_SOURCE).body[0].body[1]
        plan = ast.parse(inspect.getsource(rules.plan_config_migration)).body[0]
        self.assertEqual(_ast_hash(plan.body[1:-1]), _ast_hash(old_try.body[2:7]))

    def test_defaults_order_and_language_absence(self):
        self.assertIsInstance(rules.Config(), dict)
        self.assertEqual(list(rules.Config().items()), list(rules.DEFAULT_CONFIG.items()))
        self.assertNotIn(rules.CFG.LANGUAGE, rules.DEFAULT_CONFIG)
        self.assertIsNone(rules.Config().language)

    def test_type_matrix_matches_frozen_class(self):
        old = legacy_namespace()["Config"]
        values = (None, False, True, 0, 1, -2, 1.5, "", "16", "bad", " YES ", "false",
                  [], {}, ["x"], float("inf"), float("-inf"), float("nan"))
        for key, value in itertools.product(rules.DEFAULT_CONFIG, values):
            with self.subTest(key=key, value=repr(value)):
                raw = dict(rules.DEFAULT_CONFIG, **{key: value})
                results = []
                for cls in (old, rules.Config):
                    try:
                        cfg = cls(raw)
                    except (TypeError, ValueError, OverflowError) as error:
                        results.append((type(error), str(error)))
                    else:
                        results.append((json.dumps(cfg, ensure_ascii=False),
                                        tuple(type(item) for item in cfg.values())))
                self.assertEqual(*results)

    def test_unknown_values_keep_identity_and_order(self):
        unknown = object()
        nested = ["mutable"]
        raw = {"future": unknown, "nested": nested}
        cfg = rules.Config(raw)
        self.assertIs(cfg["future"], unknown)
        self.assertIs(cfg["nested"], nested)
        self.assertEqual(list(cfg)[-2:], ["future", "nested"])
        self.assertEqual(list(raw), ["future", "nested"])

    def test_bool_conversion_keeps_legacy_string_policy(self):
        for value, expected in ((" YES ", True), ("on", True), ("1", True),
                                ("true", True), ("2", False), (" false ", False), (None, True)):
            with self.subTest(value=value):
                self.assertIs(rules.Config({"history_enabled": value}).history_enabled, expected)

    def test_overflow_and_unexpected_conversion_errors_still_propagate(self):
        with self.assertRaises(OverflowError):
            rules.Config({"font_size": float("inf")})

        class Broken:
            def __int__(self):
                raise RuntimeError("synthetic conversion")
        with self.assertRaisesRegex(RuntimeError, "synthetic conversion"):
            rules.Config({"font_size": Broken()})

    def test_nan_float_and_bad_int_keep_existing_behavior(self):
        self.assertTrue(math.isnan(rules.Config({"double_press_window": "nan"}).double_press_window))
        self.assertEqual(rules.Config({"font_size": "nan"}).font_size, 12)

    def test_falsey_and_invalid_inputs_match_frozen_class(self):
        old = legacy_namespace()["Config"]
        for value in (None, {}, [], (), "", False, 0, 42, True, "abc", ["bad"], [["theme", "dark"]]):
            with self.subTest(value=value):
                try:
                    expected = old(value)
                except (TypeError, ValueError) as error:
                    with self.assertRaises(type(error)):
                        rules.Config(value)
                else:
                    self.assertEqual(list(rules.Config(value).items()), list(expected.items()))

    def test_single_use_iterable_is_not_silently_reinterpreted(self):
        cfg = rules.Config(iter([("theme", "dark"), ("model", "opus")]))
        self.assertEqual(cfg.theme, "system")
        self.assertEqual(cfg.model_provider, "claude_cli")
        self.assertEqual(cfg.claude_model, "opus")

    def test_marker_presence_preserves_explicit_opt_out(self):
        for marker in (False, True, None, "", 0):
            raw = {"ui_v2_default_migrated": marker, "labs_defaults_migrated": marker,
                   "ui_v2": False, "summary_enabled": False, "clipboard_protection_enabled": False}
            with self.subTest(marker=marker):
                cfg = rules.Config(raw)
                self.assertIs(cfg["ui_v2"], False)
                self.assertIs(cfg.summary_enabled, False)
                self.assertIs(cfg["clipboard_protection_enabled"], False)
                changed, payload = rules.plan_config_migration(raw, cfg)
                self.assertFalse(changed)
                self.assertEqual(payload, raw)

    def test_model_migration_and_legacy_sync(self):
        for raw, provider, claude, codex in (
                ({}, "codex_cli", "haiku", "auto-fast"),
                ({"model": "opus"}, "claude_cli", "opus", "auto-fast"),
                ({"model": "opus", "claude_model": "sonnet", "model_provider": "custom",
                  "codex_model": "gpt-5.4-mini"}, "custom", "sonnet", "auto-fast")):
            with self.subTest(raw=raw):
                cfg = rules.Config(raw)
                self.assertEqual((cfg.model_provider, cfg.claude_model, cfg.codex_model),
                                 (provider, claude, codex))
                self.assertEqual(cfg.model, claude)

    def test_public_coerce_and_subclass_override_remain_active(self):
        class Config(rules.Config):
            def _coerce(self):
                self["called"] = True
                super()._coerce()
        cfg = Config()
        self.assertTrue(cfg["called"])
        del cfg["font_size"]
        cfg["max_chars"] = "17"
        cfg._coerce()
        self.assertEqual((cfg.font_size, cfg.max_chars), (12, 17))

    def test_typed_accessors_keep_fallbacks_after_deletion(self):
        cfg = rules.Config()
        for name, accessor in vars(rules.Config).items():
            if isinstance(accessor, property):
                with self.subTest(name=name):
                    cfg.pop(name, None)
                    self.assertEqual(getattr(cfg, name), rules.DEFAULT_CONFIG.get(name))

    def test_plan_matches_frozen_loader_payloads_and_memory(self):
        for raw in config_cases():
            with self.subTest(raw=raw):
                saved = mock.Mock()
                log = mock.Mock()
                old = legacy_namespace(
                    CONFIG_PATH="unused", open=lambda *a, **k: io.StringIO(json.dumps(raw)),
                    save_config=saved, log_error=log)
                expected = old["load_config"]()
                cfg = rules.Config(raw)
                changed, payload = rules.plan_config_migration(raw, cfg)
                self.assertEqual(list(cfg.items()), list(expected.items()))
                self.assertEqual(saved.call_count, int(changed))
                if changed:
                    self.assertEqual(json.dumps(payload, ensure_ascii=False, indent=2),
                                     json.dumps(saved.call_args.args[0], ensure_ascii=False, indent=2))
                log.assert_not_called()

    def test_plan_preserves_raw_values_and_unknown_identity(self):
        nested = {"future": [1]}
        raw = {"future": nested, "font_size": "16", "codex_model": "gpt-5.4-mini"}
        cfg = rules.Config(raw)
        changed, payload = rules.plan_config_migration(raw, cfg)
        self.assertTrue(changed)
        self.assertIs(payload["future"], nested)
        self.assertEqual(payload["font_size"], "16")
        self.assertEqual(payload["codex_model"], "gpt-5.4-mini")
        self.assertEqual(list(payload)[:3], list(raw))
        self.assertEqual(cfg["font_size"], 16)
        self.assertEqual(cfg.codex_model, "auto-fast")
        self.assertEqual(len(raw), 3)

    def test_plan_only_upgrades_streaming_in_explicit_config(self):
        raw = dict(rules.DEFAULT_CONFIG, codex_streaming_experimental=False)
        cfg = rules.Config(raw)
        self.assertIs(cfg["codex_streaming_experimental"], False)
        changed, payload = rules.plan_config_migration(raw, cfg)
        self.assertTrue(changed)
        self.assertIs(cfg["codex_streaming_experimental"], True)
        self.assertIs(payload["codex_streaming_experimental"], True)
        self.assertIs(raw["codex_streaming_experimental"], False)

    def test_plan_bad_raw_and_missing_fields_propagate(self):
        for raw in (None, 42, "abc"):
            with self.subTest(raw=raw), self.assertRaises((TypeError, ValueError)):
                rules.plan_config_migration(raw, rules.Config())
        with self.assertRaises(KeyError):
            rules.plan_config_migration({}, {})

    def test_known_fixed_payload_bytes(self):
        raw = {"font_size": "16", "future": "\u4e2d"}
        changed, payload = rules.plan_config_migration(raw, rules.Config(raw))
        self.assertTrue(changed)
        self.assertEqual(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                         b'{"font_size":"16","future":"\xe4\xb8\xad","ui_v2":true,'
                         b'"ui_v2_default_migrated":true,"summary_enabled":true,'
                         b'"clipboard_protection_enabled":true,"labs_defaults_migrated":true}')
