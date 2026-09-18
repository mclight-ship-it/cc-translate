"""Real temporary state and deterministic scheduling around the native provider boundary."""

import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from cc_config import CFG, Config, DEFAULT_CONFIG
from cc_direction import DIRECTION_MODES, LANGUAGES, direction_prompt, resolve_target_lang
from cc_prompts import (
    CODE_EXPLAIN_APPEND_PROMPT, CODE_EXPLAIN_PROMPT, DICTIONARY_PROMPT,
    OCR_STRUCTURE_HINT, RESULT_ACTION_PROMPTS, SYSTEM_SUFFIX,
)
from cc_summary import codex_summary_instruction
from cc_providers.base import ProviderResult
from cc_providers.codex_catalog import CatalogProbeError
from cc_providers.codex_cli import build_codex_prompt
from cc_providers.darwin_process import ProcessError
from cc_macos import configuration, translation
from cc_macos.protocol import MAX_FRAME_BYTES, RESERVED_ID, ProtocolError, decode_frame, encode_frame
from cc_macos.server import Server

if __package__:
    from .test_macos_configuration import _ConfigurationDirectory, message
else:
    from test_macos_configuration import _ConfigurationDirectory, message


TEXT = "Synthetic input with a complete sentence."
OUTPUT = "Synthetic translated sentence."
OCR_TEXT = "  Heading \u4e2d\U0001f642\r\n\r\n1. First item\r\n2. Second item\r\n\t- Detail `value`\r\n"


def request(**changes):
    return dict(operation="translate", text=TEXT, app_language="en_US", origin="text",
                use_cache=True, record_history=True) | changes


def action_request(action="concise", **changes):
    return dict(operation="result_action", action=action, text=TEXT, app_language="en_US",
                target_language="ja" if action == "retranslate" else None) | changes


class ResultActionContracts(unittest.TestCase):
    def test_exact_payload_accepts_only_the_six_explicit_actions(self):
        for action in translation.RESULT_ACTIONS:
            translation.validate_result_action_request(action_request(action))
        invalid = (
            action_request(extra=True), action_request(operation="translate"),
            action_request(action="rewrite"), action_request(action=""), action_request(action=[]),
            action_request(action={}), action_request(action=True),
            action_request(text=None), action_request(text=1), action_request(text=[]),
            action_request(text=""), action_request(text=" \t\r\n"),
            action_request(app_language="fr"), action_request(app_language=[]),
            action_request(app_language={}), action_request(app_language=True),
            action_request(use_cache=True), action_request(record_history=True),
            action_request(origin="text"),
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaisesRegex(ProtocolError, "^invalid_result_action$"):
                translation.validate_result_action_request(payload)
        for key in action_request():
            payload = action_request()
            del payload[key]
            with self.subTest(missing=key), self.assertRaisesRegex(ProtocolError, "^invalid_result_action$"):
                translation.validate_result_action_request(payload)
        self.assertIn("invalid_result_action", translation.TRANSLATION_FAILURE_CODES)

    def test_targets_are_required_only_for_retranslation_and_allow_every_shared_language(self):
        for language in LANGUAGES:
            translation.validate_result_action_request(action_request("retranslate", target_language=language))
        for target in (None, "", "to_ja", "JA", "it", True, 1, [], {}):
            with self.subTest(target=target), self.assertRaisesRegex(ProtocolError, "^invalid_result_action$"):
                translation.validate_result_action_request(action_request("retranslate", target_language=target))
        for action in translation.RESULT_ACTIONS[:-1]:
            for target in ("ja", "", False, 0, [], {}):
                with self.subTest(action=action, target=target), self.assertRaises(ProtocolError):
                    translation.validate_result_action_request(action_request(action, target_language=target))

    def test_utf8_input_limit_is_separate_from_translation_and_json_output_budgets(self):
        for text in ("a" * 24000, "\u4e2d" * 8000, "\U0001f600" * 6000, "\0" * 24000):
            translation.validate_result_action_request(action_request(text=text))
            snapshot = translation.snapshot_for_result_action(Config(), action_request(text=text))
            self.assertEqual(snapshot.request.user_text, text)
        for text in ("a" * 24001, "\u4e2d" * 8001, "\U0001f600" * 6001, "\ud800"):
            with self.subTest(size=len(text)), self.assertRaisesRegex(ProtocolError, "^invalid_result_action$"):
                translation.validate_result_action_request(action_request(text=text))
        with self.assertRaisesRegex(ProtocolError, "^invalid_translation$"):
            translation.validate_translation_request(request(text="a" * 8193))

    def test_actions_reuse_exact_prompts_without_classifier_dictionary_or_automatic_summary(self):
        config = Config({CFG.SUMMARY_ENABLED: True})
        prose = "This is synthetic prose with complete sentences for the summary contract. " * 8
        for action in translation.RESULT_ACTIONS:
            for text in ("hello", "def greeting():\n    return 42", prose):
                with self.subTest(action=action, text=text[:10]), \
                        patch.object(translation, "classify_selection", side_effect=AssertionError("classifier")), \
                        patch.object(translation, "is_single_word", side_effect=AssertionError("dictionary")), \
                        patch.object(translation, "is_summarizable_prose", side_effect=AssertionError("summary")), \
                        patch.object(translation, "codex_summary_instruction", side_effect=AssertionError("summary")):
                    snapshot = translation.snapshot_for_result_action(config, action_request(action, text=text))
                expected = (RESULT_ACTION_PROMPTS[action][1] if action in RESULT_ACTION_PROMPTS
                            else CODE_EXPLAIN_APPEND_PROMPT if action == "explain_code"
                            else direction_prompt("to_ja" if action == "retranslate" else "auto",
                                                  "en_US") + SYSTEM_SUFFIX)
                self.assertEqual(snapshot.request.system_prompt, expected)
                self.assertEqual(snapshot.request.user_text, text)
                self.assertEqual((snapshot.request.task, snapshot.summarize, snapshot.dictionary),
                                 ("text", False, False))
                self.assertEqual(snapshot.kind, "text")
                if action in RESULT_ACTION_PROMPTS or action == "explain_code":
                    self.assertIsNone(snapshot.target_lang)

    def test_retranslation_overrides_direction_for_all_languages_and_both_ui_languages(self):
        for app_language in ("zh_CN", "en_US"):
            for language in LANGUAGES:
                snapshot = translation.snapshot_for_result_action(
                    Config({CFG.DIRECTION: "to_fr"}),
                    action_request("retranslate", target_language=language, app_language=app_language))
                self.assertEqual(snapshot.direction, "to_" + language)
                self.assertEqual(snapshot.target_lang, language)
                self.assertEqual(snapshot.request.system_prompt,
                                 direction_prompt("to_" + language, app_language) + SYSTEM_SUFFIX)

    def test_as_text_obeys_config_direction_and_ui_language_without_dictionary_routing(self):
        for direction in DIRECTION_MODES:
            for app_language in ("zh_CN", "en_US"):
                for text in ("hello", "\u4f60\u597d", "def hello():\n    return 42"):
                    snapshot = translation.snapshot_for_result_action(
                        Config({CFG.DIRECTION: direction}),
                        action_request("as_text", text=text, app_language=app_language))
                    self.assertEqual(snapshot.request.system_prompt, direction_prompt(direction, app_language) + SYSTEM_SUFFIX)
                    self.assertEqual(snapshot.target_lang, resolve_target_lang(direction, app_language, text))
                    self.assertEqual(snapshot.content_class, "text")
        snapshot = translation.snapshot_for_result_action(
            Config({CFG.LANGUAGE: "zh_CN"}), action_request("as_text", app_language="en_US"))
        self.assertEqual(snapshot.app_language, "zh_CN")
        self.assertEqual(snapshot.request.system_prompt, direction_prompt("auto", "zh_CN") + SYSTEM_SUFFIX)

    def test_action_snapshot_freezes_model_config_input_and_metadata(self):
        for action in translation.RESULT_ACTIONS:
            for streaming in (False, True):
                config = Config({CFG.CODEX_MODEL: "selected-model", CFG.CODEX_STREAMING_EXPERIMENTAL: streaming,
                                 "future": {"x": [1]}})
                payload = action_request(action)
                snapshot = translation.snapshot_for_result_action(config, payload)
                config[CFG.CODEX_MODEL] = "changed"
                config[CFG.CODEX_STREAMING_EXPERIMENTAL] = not streaming
                config["future"]["x"].append(2)
                payload["text"], payload["action"] = "changed", "changed"
                self.assertEqual(snapshot.config["future"]["x"], (1,))
                self.assertEqual(snapshot.config[CFG.CODEX_MODEL], "selected-model")
                self.assertEqual(snapshot.selection.model, "selected-model")
                self.assertEqual(snapshot.request.model, "selected-model")
                self.assertEqual(snapshot.input, TEXT)
                self.assertEqual(snapshot.request.user_text, TEXT)
                self.assertEqual(snapshot.request.timeout_seconds, 90 if streaming else 60)
                self.assertEqual(snapshot.stream_enabled, streaming)
                self.assertEqual(snapshot.action, "rewrite:" + action if action in RESULT_ACTION_PROMPTS else action)
                self.assertEqual(snapshot.content_class, "mixed" if action == "explain_code" else "text")
                with self.assertRaises(TypeError):
                    snapshot.config[CFG.CODEX_MODEL] = "changed"

    def test_actions_keep_provider_and_config_safety_checks(self):
        for values, code in (({CFG.MODEL_PROVIDER: "claude_cli"}, "unsupported_provider"),
                             ({CFG.DIRECTION: "unknown"}, "invalid_translation_settings"),
                             ({CFG.CODEX_MODEL: "x" * 257}, "invalid_translation_settings"),
                             ({CFG.MAX_CHARS: 0}, "invalid_translation_settings"),
                             ({CFG.HISTORY_LIMIT: 0}, "invalid_translation_settings"),
                             ({CFG.LANGUAGE: "unknown"}, "invalid_translation_settings")):
            with self.subTest(values=values), self.assertRaisesRegex(translation.TranslationError, code):
                translation.snapshot_for_result_action(Config(values), action_request())


class TranslationContracts(unittest.TestCase):
    def test_ocr_origin_is_text_only_with_unchanged_utf8_and_configuration_limits(self):
        for origin in ("text", "selection", "ocr"):
            translation.validate_translation_request(request(origin=origin, text="\U0001f642" * 2048))
        for changes in ({"origin": "OCR"}, {"origin": "image"}, {"origin": None},
                        {"text": "\U0001f642" * 2049}, {"text": "\ud800"}, {"text": " \r\n"},
                        {"image_paths": []}, {"image_paths": ["private.png"]}, {"task": "image"},
                        {"image": "base64"}, {"path": "private.png"}, {"ocr": True}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ProtocolError, "^invalid_translation$"):
                translation.validate_translation_request(request(origin="ocr") | changes)
        with self.assertRaisesRegex(translation.TranslationError, "^invalid_translation_settings$"):
            translation.snapshot_for_translation(Config({CFG.MAX_CHARS: 1}), request(origin="ocr"))
        self.assertTrue(translation.DarwinCodexProvider.capabilities.images)

    def test_ocr_snapshot_preserves_raw_layout_and_text_only_provider_encoding(self):
        payload = request(origin="ocr", text=OCR_TEXT)
        config = Config({CFG.CODEX_MODEL: "explicit-model", CFG.DIRECTION: "to_ja",
                         CFG.SUMMARY_ENABLED: True, "future": {"items": ["original"]}})
        snapshot = translation.snapshot_for_translation(config, payload)
        expected_prompt = direction_prompt("to_ja", "en_US") + OCR_STRUCTURE_HINT + SYSTEM_SUFFIX
        self.assertEqual(snapshot.request.system_prompt, expected_prompt)
        self.assertEqual((snapshot.origin, snapshot.kind, snapshot.input), ("ocr", "ocr", OCR_TEXT))
        self.assertEqual((snapshot.request.task, snapshot.request.image_paths, snapshot.request.user_text),
                         ("text", (), OCR_TEXT))
        self.assertEqual((snapshot.target_lang, snapshot.summarize, snapshot.action), ("ja", False, "translation"))
        encoded = build_codex_prompt(snapshot.request)
        self.assertEqual(json.loads(encoded.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0]), OCR_TEXT)
        payload.update(origin="text", text="changed")
        config.update(codex_model="changed", direction="to_ko", summary_enabled=False)
        config["future"]["items"].append("changed")
        self.assertEqual((snapshot.request.model, snapshot.selection.model), ("explicit-model", "explicit-model"))
        self.assertEqual(snapshot.config["future"]["items"], ("original",))
        self.assertEqual(snapshot.request.system_prompt, expected_prompt)
        self.assertEqual(snapshot.history_metadata["origin"], "ocr")
        self.assertEqual(snapshot.history_metadata["input"], OCR_TEXT)
        with self.assertRaises(TypeError):
            snapshot.history_metadata["kind"] = "text"

    def test_ocr_keeps_word_code_and_mixed_classification_but_always_has_ocr_history_kind(self):
        mixed = "This sentence explains the call below.\n```python\nprint(value)\n```"
        for text, content_class, is_dict in (("hello", "text", True),
                                            ("def greeting():\n    return 42", "code", False),
                                            (mixed, "mixed", False)):
            with self.subTest(text=text):
                snapshot = translation.snapshot_for_translation(Config(), request(origin="ocr", text=text))
                self.assertEqual((snapshot.content_class, snapshot.dictionary, snapshot.kind),
                                 (content_class, is_dict, "ocr"))
                expected = (CODE_EXPLAIN_PROMPT if content_class == "code" else DICTIONARY_PROMPT if is_dict
                            else direction_prompt("auto", "en_US") + OCR_STRUCTURE_HINT + SYSTEM_SUFFIX)
                self.assertEqual(snapshot.request.system_prompt, expected)
                self.assertEqual(snapshot.history_metadata["is_code"], content_class == "code")
                self.assertFalse(snapshot.summarize)
                self.assertEqual(snapshot.request.task, "text")
                self.assertEqual(snapshot.request.image_paths, ())
                if content_class == "code" or is_dict:
                    self.assertIsNone(snapshot.target_lang)

    def test_ocr_long_prose_never_evaluates_summary_and_all_directions_keep_layout_hint(self):
        prose = "This is synthetic prose with complete sentences for the summary contract. " * 8
        for direction in DIRECTION_MODES:
            for language in ("en_US", "zh_CN"):
                config = Config({CFG.SUMMARY_ENABLED: True, CFG.DIRECTION: direction})
                with self.subTest(direction=direction, language=language), \
                        patch.object(translation, "is_summarizable_prose", side_effect=AssertionError("summary classifier")), \
                        patch.object(translation, "codex_summary_instruction", side_effect=AssertionError("summary prompt")):
                    snapshot = translation.snapshot_for_translation(config, request(
                        origin="ocr", text=prose, app_language=language))
                self.assertEqual(snapshot.request.system_prompt,
                                 direction_prompt(direction, language) + OCR_STRUCTURE_HINT + SYSTEM_SUFFIX)
                self.assertEqual(snapshot.target_lang, resolve_target_lang(direction, language, prose))
                self.assertEqual((snapshot.summarize, snapshot.request.task), (False, "text"))
        for origin in ("text", "selection"):
            snapshot = translation.snapshot_for_translation(Config(), request(origin=origin, text=prose))
            self.assertTrue(snapshot.summarize)
            self.assertEqual(snapshot.request.system_prompt, codex_summary_instruction(snapshot.target_lang))
            self.assertNotIn(OCR_STRUCTURE_HINT, snapshot.request.system_prompt)

    def test_version_failure_categories_remain_distinct_and_allowlisted(self):
        for suffix in ("unsupported", "unreadable", "prerelease"):
            code = translation.provider_failure("appserver_version_" + suffix)
            self.assertEqual(code, "provider_version_" + suffix)
            self.assertIn(code, translation.TRANSLATION_FAILURE_CODES)
        self.assertEqual(translation.provider_failure("invalid_appserver_message"),
                         "provider_protocol_error")

    def test_exact_payload_types_and_unknown_fields(self):
        translation.validate_translation_request(request())
        for value in (request(extra=True), request(text=" "), request(text=True),
                      request(use_cache=1), request(record_history="true"), request(origin=[]),
                      request(app_language={}), request(operation="fixture")):
            with self.subTest(value=value), self.assertRaisesRegex(ProtocolError, "invalid_translation"):
                translation.validate_translation_request(value)

    def test_request_utf8_limit_and_invalid_unicode(self):
        translation.validate_translation_request(request(text="a" * 8192))
        for text in ("a" * 8193, "\u4e2d" * 2731, "\ud800"):
            with self.assertRaisesRegex(ProtocolError, "invalid_translation"):
                translation.validate_translation_request(request(text=text))

    def test_explicit_cli_environment_has_no_fallback_or_per_request_paths(self):
        home = str(Path.cwd())
        valid = json.dumps({"HOME": home, "PATH": "", "SECRET": "synthetic"})
        environment = {translation.CLI_ENVIRONMENT_KEY: valid}
        self.assertEqual(translation.parse_cli_environment(environment, home)["SECRET"], "synthetic")
        for raw in (None, "[]", '{"HOME":"x","HOME":"y"}', '{"HOME":null}',
                    json.dumps({"HOME": home, "PATH": "", "x": ["bad"]}),
                    json.dumps({"HOME": home, "PATH": "", "x": "\0"}),
                    json.dumps({"HOME": home, "PATH": "", "x": "a" * 32768})):
            with self.assertRaisesRegex(ProtocolError, "invalid_startup"):
                translation.parse_cli_environment({translation.CLI_ENVIRONMENT_KEY: raw}, home)

    def test_startup_is_explicit_and_does_not_construct_provider_or_create_paths(self):
        home, command = str(Path.cwd()), str(Path.cwd() / "synthetic-cli")
        env = {translation.CLI_ENVIRONMENT_KEY: json.dumps({"HOME": home, "PATH": ""})}
        with patch.object(translation, "DarwinCodexProvider") as provider, \
                patch.object(Path, "mkdir", side_effect=AssertionError("implicit mkdir")):
            self.assertIsNone(configuration.startup_configuration([], environment=env))
            session = configuration.startup_configuration(
                ["--config-home", home, "--application-id", "synthetic",
                 "--codex-command", command], environment=env)
            self.assertIsInstance(session, translation.TranslationSession)
            session.close()
            provider.assert_not_called()

    def test_only_explicit_translation_main_installs_and_restores_termination_handler(self):
        from cc_macos import server

        for enabled in (False, True):
            for failed in (False, True):
                with self.subTest(enabled=enabled, failed=failed), \
                        patch.object(server, "startup_configuration", return_value=None), \
                        patch.object(server, "Server") as factory, \
                        patch.object(server.signal, "signal", return_value=signal.SIG_DFL) as install:
                    instance = factory.return_value
                    instance._translation_enabled = enabled
                    instance._dictionary_enabled = False
                    if failed:
                        instance.run.side_effect = RuntimeError("synthetic")
                        with self.assertRaisesRegex(RuntimeError, "synthetic"):
                            server.main([])
                    else:
                        instance.run.return_value = 0
                        self.assertEqual(server.main([]), 0)
                    if enabled:
                        self.assertEqual(install.call_count, 2)
                        self.assertEqual(install.call_args_list[0].args,
                                         (signal.SIGTERM, instance.request_termination))
                        self.assertEqual(install.call_args_list[1].args, (signal.SIGTERM, signal.SIG_DFL))
                    else:
                        install.assert_not_called()

    def test_snapshot_freezes_settings_and_reuses_direction_prompt_and_cache_bytes(self):
        cfg = Config({CFG.CODEX_MODEL: "synthetic", CFG.SUMMARY_ENABLED: False,
                      CFG.LABS_DEFAULTS_MIGRATED: True, "future": {"x": [1]}})
        snap = translation.snapshot_for_translation(cfg, request())
        self.assertEqual(snap.request.system_prompt, direction_prompt("auto", "en_US") + SYSTEM_SUFFIX)
        self.assertEqual(snap.sig, "codex_cli|synthetic|auto|sum0|en_US|codex-format-v5")
        self.assertEqual((snap.kind, snap.target_lang, snap.request.timeout_seconds), ("text", "zh", 90))
        cfg[CFG.CODEX_MODEL] = "changed"
        cfg["future"]["x"].append(2)
        self.assertEqual(snap.selection.model, "synthetic")
        self.assertEqual(snap.config["future"]["x"], (1,))

    def test_unsupported_provider_and_invalid_settings_are_explicit(self):
        for values, code in (({CFG.MODEL_PROVIDER: "claude_cli"}, "unsupported_provider"),
                             ({CFG.DIRECTION: "unknown"}, "invalid_translation_settings"),
                             ({CFG.CODEX_MODEL: "x" * 257}, "invalid_translation_settings"),
                             ({CFG.MAX_CHARS: 1}, "invalid_translation_settings")):
            with self.assertRaisesRegex(translation.TranslationError, code):
                translation.snapshot_for_translation(Config(values), request())

    def test_snapshot_routes_code_dictionary_and_long_prose_with_shared_prompt_bytes(self):
        prose = "This is synthetic prose with complete sentences for the summary contract. " * 8
        for direction in DIRECTION_MODES:
            config = Config(dict(DEFAULT_CONFIG) | {CFG.DIRECTION: direction})
            for text, kind, prompt in (
                    ("hello", "dict", DICTIONARY_PROMPT),
                    ("def greeting():\n    return 42", "code", CODE_EXPLAIN_PROMPT)):
                with self.subTest(direction=direction, kind=kind):
                    snap = translation.snapshot_for_translation(config, request(text=text))
                    self.assertEqual((snap.kind, snap.target_lang, snap.summarize), (kind, None, False))
                    self.assertEqual(snap.request.system_prompt, prompt)
                    self.assertEqual(snap.request.task, "text")
            target = resolve_target_lang(direction, "en_US", prose)
            snap = translation.snapshot_for_translation(config, request(text=prose))
            self.assertEqual((snap.kind, snap.target_lang, snap.summarize), ("text", target, True))
            self.assertEqual(snap.request.system_prompt, codex_summary_instruction(target))
            self.assertEqual(snap.request.task, "translation_summary")
            config[CFG.SUMMARY_ENABLED] = False
            plain = translation.snapshot_for_translation(config, request(text=prose))
            self.assertFalse(plain.summarize)
            self.assertEqual(plain.request.system_prompt, direction_prompt(direction, "en_US") + SYSTEM_SUFFIX)

    def test_json_string_budget_counts_escapes_and_utf8(self):
        self.assertEqual(translation.text_bytes("\0" * 3999 + "aaaa"), 24000)
        self.assertEqual(translation.text_bytes("\u4e2d" * 7999 + "a"), 24000)
        self.assertEqual(translation.text_bytes("\n\\\""), 8)

    def test_default_server_import_has_no_home_environment_or_state_side_effects(self):
        core = str(Path(translation.__file__).resolve().parents[1])
        script = r"""
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
def forbidden(*args, **kwargs): raise AssertionError("implicit user state")
class ForbiddenEnvironment(dict):
    __getitem__ = get = __iter__ = items = keys = values = copy = forbidden
os.environ = ForbiddenEnvironment()
pathlib.Path.home = forbidden
pathlib.Path.mkdir = forbidden
os.makedirs = forbidden
import cc_macos.server
assert cc_macos.server.startup_configuration([]) is None
assert not any(name in sys.modules for name in ("cc_core", "cc_providers", "tkinter", "win32api", "win32gui"))
"""
        with tempfile.TemporaryDirectory(prefix="synthetic-translation-import-") as directory:
            completed = subprocess.run([sys.executable, "-I", "-B", "-c", script, core], cwd=directory,
                                       env=dict(os.environ, HOME=directory, USERPROFILE=directory),
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_all_synthetic_fixture_scenarios_prepare_without_cli_and_match_migration_policy(self):
        from cc_macos import translation_fixture

        with tempfile.TemporaryDirectory(prefix=".translation-fixture-", dir=Path.cwd()) as directory:
            for scenario in translation_fixture.SCENARIOS:
                with self.subTest(scenario=scenario):
                    fixture = translation_fixture.prepare(Path(directory) / scenario, "synthetic", scenario)
                    self.assertEqual(fixture["environment"]["HOME"], fixture["home"])
                    report = translation_fixture.verify(fixture["root"])
                    self.assertEqual((report["submitted_turns"], report["processes"]), (0, 0))
                    self.assertFalse(report["cleanup_verified"])
                    self.assertTrue(fixture["expected"]["stream"])
                    if scenario == "streaming-migration":
                        self.assertIs(fixture["config"]["codex_streaming_experimental"], False)

    def test_custom_model_fixture_preserves_loaded_setting_and_exact_snapshot_without_cli(self):
        from cc_macos import translation_fixture
        from cc_config_store import normalize_config

        models = ("provider/Exact-ID:2026", "gpt-5.4-mini", "model-e\u0301",
                  "model-\u00e9", "future_model.2026", "m" * 256, "\u754c" * 85 + "x")
        with tempfile.TemporaryDirectory(prefix=".model-fixture-", dir=Path.cwd()) as directory, \
                patch.object(subprocess, "Popen", side_effect=AssertionError("prepare must not execute CLI")):
            for index, model in enumerate(models):
                for origin in ("text", "ocr"):
                    with self.subTest(model=model, origin=origin):
                        fixture = translation_fixture.prepare(
                            Path(directory) / (str(index) + origin), "synthetic", model=model, origin=origin)
                        self.assertIs(fixture["config"]["codex_model_default_migrated"], True)
                        for value in (fixture["config"]["codex_model"], fixture["expected"]["model"],
                                      normalize_config(fixture["config"]).codex_model):
                            self.assertEqual(value.encode("utf-8"), model.encode("utf-8"))
                        snapshot = translation.snapshot_for_translation(
                            normalize_config(fixture["config"]), fixture["request"])
                        self.assertEqual(snapshot.request.model.encode("utf-8"), model.encode("utf-8"))
                        report = translation_fixture.verify(fixture["root"])
                        self.assertEqual((report["submitted_turns"], report["processes"]), (0, 0))

    def test_custom_model_fixture_requires_native_routing_when_optional_catalog_has_no_id(self):
        from cc_macos.native_provider_fixture import _validate_provider_catalog

        with tempfile.TemporaryDirectory(prefix=".native-model-args-", dir=Path.cwd()) as directory:
            root = Path(directory).resolve()
            for model in ("provider/Exact-ID:2026", "gpt-5.4-mini", "model-e\u0301", "m" * 256):
                with self.subTest(model=model):
                    _validate_provider_catalog(root, [], model)
                    for extra in (['model_catalog_json="unused"'], ['model="substitute"']):
                        with self.assertRaises(SystemExit) as caught:
                            _validate_provider_catalog(root, extra, model)
                        self.assertEqual(caught.exception.code, 76)
            self.assertEqual(list(root.iterdir()), [])

    def test_known_model_fixture_keeps_exact_catalog_path_payload_and_argument_checks(self):
        from cc_macos.catalog_fixture import PAYLOAD
        from cc_macos.native_provider_fixture import _validate_provider_catalog

        with tempfile.TemporaryDirectory(prefix=".native-model-catalog-", dir=Path.cwd()) as directory:
            root = Path(directory).resolve()
            (root / "cache").mkdir()
            catalog = root / "cache" / "models.json"
            catalog.write_text(json.dumps(PAYLOAD), encoding="utf-8")
            override = "model_catalog_json=" + json.dumps(str(catalog))
            for model in ("synthetic", "synthetic-small"):
                _validate_provider_catalog(root, [override], model)
                for remaining in ([], [override, 'extra=true']):
                    with self.assertRaises(SystemExit) as caught:
                        _validate_provider_catalog(root, remaining, model)
                    self.assertEqual(caught.exception.code, 76)
            outside = root / "outside.json"
            outside.write_text(json.dumps(PAYLOAD), encoding="utf-8")
            with self.assertRaises(SystemExit) as caught:
                _validate_provider_catalog(root, ["model_catalog_json=" + json.dumps(str(outside))], "synthetic")
            self.assertEqual(caught.exception.code, 77)
            catalog.write_text('{"models":[]}', encoding="utf-8")
            with self.assertRaises(SystemExit) as caught:
                _validate_provider_catalog(root, [override], "synthetic")
            self.assertEqual(caught.exception.code, 77)

    def test_result_action_fixtures_keep_exact_expected_prompts_without_running_cli(self):
        from cc_macos import translation_fixture
        from cc_providers.codex_cli import build_codex_prompt

        with tempfile.TemporaryDirectory(prefix=".action-fixture-", dir=Path.cwd()) as directory:
            for action in translation.RESULT_ACTIONS:
                with self.subTest(action=action):
                    fixture = translation_fixture.prepare(
                        Path(directory) / action, "synthetic", result_action=action,
                        target_language="ja" if action == "retranslate" else None)
                    snapshot = translation.snapshot_for_result_action(Config(fixture["config"]), fixture["request"])
                    self.assertEqual(fixture["request"]["operation"], "result_action")
                    self.assertEqual(fixture["expected"]["prompt"], build_codex_prompt(snapshot.request))
                    self.assertEqual(fixture["expected"]["kind"], "text")
                    self.assertIs(fixture["expected"]["summarize"], False)
                    report = translation_fixture.verify(fixture["root"])
                    self.assertEqual((report["submitted_turns"], report["processes"]), (0, 0))

    def test_fixture_origin_preserves_defaults_and_generates_exact_ocr_provider_expectations_without_cli(self):
        from cc_macos import translation_fixture
        from cc_config import plan_config_migration

        with tempfile.TemporaryDirectory(prefix=".origin-fixture-", dir=Path.cwd()) as directory:
            root = Path(directory)
            with patch.object(subprocess, "Popen", side_effect=AssertionError("prepare must not execute CLI")):
                default = translation_fixture.prepare(root / "default", "synthetic")
                explicit = translation_fixture.prepare(root / "explicit", "synthetic", origin="text")
                self.assertEqual(default["request"], explicit["request"])
                self.assertEqual(default["expected"], explicit["expected"])
                for origin in ("text", "selection", "ocr"):
                    for scenario in ("normal", "dictionary", "code", "summary"):
                        with self.subTest(origin=origin, scenario=scenario):
                            fixture = translation_fixture.prepare(
                                root / (origin + "-" + scenario), "synthetic", scenario, origin=origin)
                            self.assertEqual(fixture["request"]["origin"], origin)
                            normalized = Config(fixture["config"])
                            plan_config_migration(fixture["config"], normalized)
                            snapshot = translation.snapshot_for_translation(normalized, fixture["request"])
                            self.assertEqual(fixture["expected"]["prompt"], build_codex_prompt(snapshot.request))
                            self.assertEqual(set(fixture["expected"]), {
                                "prompt", "model", "task", "output", "kind", "target_lang",
                                "summarize", "signature", "stream",
                            })
                            self.assertEqual(json.loads((Path(fixture["root"]) / "expected-request.json").read_bytes()),
                                             fixture["expected"])
                            if origin == "ocr":
                                self.assertEqual((fixture["expected"]["kind"], fixture["expected"]["task"],
                                                  fixture["expected"]["summarize"]), ("ocr", "text", False))
                                self.assertTrue(fixture["request"]["use_cache"])
                                self.assertTrue(fixture["request"]["record_history"])
                            if scenario == "normal":
                                self.assertEqual(fixture["request"]["text"], translation_fixture.INPUT)
                                self.assertIn("\U0001f642", fixture["request"]["text"])
                                self.assertTrue(fixture["request"]["text"].endswith("\r\n"))
                            report = translation_fixture.verify(fixture["root"])
                            self.assertEqual((report["submitted_turns"], report["processes"]), (0, 0))

    def test_fixture_invalid_origin_and_result_action_combination_have_no_side_effects(self):
        from cc_macos import translation_fixture

        with tempfile.TemporaryDirectory(prefix=".origin-fixture-", dir=Path.cwd()) as directory:
            for index, origin in enumerate((None, False, [], "image", "OCR")):
                root = Path(directory) / str(index)
                with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "^synthetic_origin_required$"):
                    translation_fixture.prepare(root, "synthetic", origin=origin)
                self.assertFalse(root.exists())
            root = Path(directory) / "action"
            with self.assertRaisesRegex(ValueError, "^synthetic_origin_not_applicable$"):
                translation_fixture.prepare(root, "synthetic", result_action="summary", origin="ocr")
            self.assertFalse(root.exists())

    def test_fixture_cli_origin_prepares_ocr_expected_data_without_submission_or_checkout_fallback(self):
        from cc_macos import translation_fixture

        script = Path(configuration.__file__).resolve().with_name("translation_fixture.py")
        with tempfile.TemporaryDirectory(prefix=".origin-cli-", dir=Path.cwd()) as directory:
            for scenario in ("normal", "summary"):
                root = Path(directory) / scenario
                result = subprocess.run(
                    [sys.executable, "-I", "-B", "-X", "utf8", str(script), "--prepare", str(root),
                     "--application-id", "synthetic", "--scenario", scenario, "--origin", "ocr"],
                    cwd=directory, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, b"")
                fixture = json.loads(result.stdout)
                self.assertEqual(fixture["request"]["origin"], "ocr")
                self.assertEqual((fixture["expected"]["kind"], fixture["expected"]["task"],
                                  fixture["expected"]["summarize"]), ("ocr", "text", False))
                self.assertIn(OCR_STRUCTURE_HINT, fixture["expected"]["prompt"])
                report = translation_fixture.verify(fixture["root"])
                self.assertEqual((report["submitted_turns"], report["processes"]), (0, 0))


class ScriptedProvider:
    def __init__(self, *args, **kwargs):
        self.requests = []
        self.chunks, self.text = [OUTPUT], OUTPUT
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.result_code = ""
        self.closed = 0
        self.catalog_calls = 0
        self.catalog_models = [{"id": "synthetic", "name": "Synthetic", "description": ""}]
        self.catalog_error = None

    def stream(self, captured, on_delta, cancel):
        self.requests.append(captured)
        self.entered.set()
        if not self.release.wait(3):
            raise AssertionError("Synthetic provider gate was not released.")
        if cancel.is_set():
            return ProviderResult(False, error_code="cancelled", metrics=(("turn_submitted", True),))
        try:
            for text in self.chunks:
                on_delta(text)
        except ProcessError as error:
            return ProviderResult(False, error_code=str(error), metrics=(("turn_submitted", True),))
        return ProviderResult(not self.result_code, text=self.text, error_code=self.result_code,
                              metrics=(("turn_submitted", True),))

    def complete(self, captured, cancel):
        return self.stream(captured, lambda _text: None, cancel)

    def model_catalog(self, cancel):
        self.catalog_calls += 1
        self.entered.set()
        if not self.release.wait(3):
            raise AssertionError("Synthetic catalog drain was not released.")
        if self.catalog_error is not None:
            raise CatalogProbeError(self.catalog_error)
        if cancel.is_set():
            raise CatalogProbeError("cancelled")
        return self.catalog_models

    def shutdown(self, *, require_cleanup=False):
        self.closed += 1
        self.release.set()


class EventOutput(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.events = []
        self.condition = threading.Condition()
        self.break_on_delta = False

    def write(self, raw):
        event = decode_frame(raw)
        if self.break_on_delta and event["type"] == "delta":
            raise BrokenPipeError("SYNTHETIC_PRIVATE")
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()
        return super().write(raw)

    def terminal(self, id_):
        with self.condition:
            return self.condition.wait_for(lambda: any(
                e["id"] == id_ and e["type"] in ("completed", "cancelled", "failed") for e in self.events), 3)

    def result(self, id_):
        return next(e for e in reversed(self.events) if e["id"] == id_ and e["type"] in
                    ("completed", "cancelled", "failed"))


class _TranslationDirectory(_ConfigurationDirectory):
    def setUp(self):
        super().setUp()
        self.provider = ScriptedProvider()
        provider_patch = patch.object(translation, "DarwinCodexProvider", return_value=self.provider)
        provider_patch.start()
        self.addCleanup(provider_patch.stop)
        self.session = translation.TranslationSession(
            str(self.home), self.identity, str(self.home / "synthetic-cli"),
            {"HOME": str(self.home), "PATH": ""})
        self.addCleanup(self.session.close)
        self.stdout, self.stderr = EventOutput(), io.StringIO()
        self.server = Server(io.BytesIO(), self.stdout, self.stderr, configuration=self.session)
        self.server._handle(message("hello", "hello"))
        self.addCleanup(self.server._join_workers)
        self.addCleanup(self.provider.release.set)
        self.addCleanup(self.server._stop)
        self.config = dict(DEFAULT_CONFIG) | {CFG.CODEX_MODEL: "synthetic", CFG.SUMMARY_ENABLED: False}
        self.session.perform({"operation": "config_save", "config": self.config})

    def translate(self, id_="translate", **changes):
        self.server._handle(message(id_, "request", **request(**changes)))

    def history(self):
        return self.session.perform_history(
            {"operation": "history_load", "page_size": 100, "cursor": None}, "read", 2)["entries"]


class HistoryRetentionTranslationTests(_TranslationDirectory):
    def seed_history(self, *, cached=False):
        entries = [{"ts": "2026-09-01 12:00", "input": "older " + str(n), "output": "old output",
                    "kind": "text", "is_dict": False, "is_code": False, "sig": "old signature",
                    "future": [n]} for n in range(5)]
        if cached:
            snapshot = translation.snapshot_for_translation(Config(self.config), request())
            entries[2].update(input=TEXT, output=OUTPUT, sig=snapshot.sig)
        path = self.directory / "history.json"
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        first = self.history_page()
        return path, entries, first

    def history_page(self, cursor=None):
        return self.session.perform_history(
            {"operation": "history_load", "page_size": 1, "cursor": cursor}, "retention", 2)

    def test_running_text_ocr_and_image_commit_latest_limit_atomically_without_changing_provider_request(self):
        import hashlib
        from cc_macos.image_fixture import PNG_BYTES

        source = self.home / "retention.png"
        source.write_bytes(PNG_BYTES)
        image_request = {
            "operation": "translate_image", "image_path": str(source), "image_bytes": len(PNG_BYTES),
            "image_sha256": hashlib.sha256(PNG_BYTES).hexdigest(), "app_language": "en_US", "record_history": True,
        }
        for origin, payload in (("text", request()), ("ocr", request(origin="ocr", text=OCR_TEXT)),
                                ("image", image_request)):
            for limit in (1, 2):
                with self.subTest(origin=origin, limit=limit):
                    self.session.perform({"operation": "config_save", "config": self.config})
                    path, entries, first = self.seed_history()
                    before = path.read_bytes()
                    self.provider.entered.clear()
                    self.provider.release.clear()
                    id_ = origin + str(limit)
                    calls_before = len(self.provider.requests)
                    self.server._handle(message(id_, "request", **payload))
                    attempts = []
                    actual_replace = os.replace
                    def replace(source_path, destination):
                        attempts.append((Path(destination), path.read_bytes(),
                                         json.loads(Path(source_path).read_bytes())))
                        return actual_replace(source_path, destination)
                    try:
                        self.assertTrue(self.provider.entered.wait(1))
                        captured = self.provider.requests[-1]
                        expected = (translation.snapshot_for_image(
                            Config(self.config), payload, captured.image_paths[0]).request if origin == "image"
                            else translation.snapshot_for_translation(Config(self.config), payload).request)
                        self.assertEqual(captured, expected)
                        self.server._handle(message("save-" + id_, "request", operation="config_save",
                                                    config=self.config | {CFG.HISTORY_LIMIT: limit}))
                        self.assertTrue(self.stdout.terminal("save-" + id_))
                        self.assertEqual(self.stdout.result("save-" + id_)["payload"], {"saved": True})
                        self.assertEqual(path.read_bytes(), before)
                        self.assertEqual(self.history_page(), first)
                        with patch("cc_storage.os.replace", side_effect=replace):
                            self.provider.release.set()
                            self.assertTrue(self.stdout.terminal(id_))
                    finally:
                        self.provider.release.set()
                    result = self.stdout.result(id_)
                    self.assertEqual(result["type"], "completed")
                    self.assertEqual((result["payload"]["history"], result["payload"]["history_error"]),
                                     ("recorded", None))
                    self.assertEqual(len(self.provider.requests), calls_before + 1)
                    self.assertEqual(self.provider.requests[-1], expected)
                    stored = json.loads(path.read_bytes())
                    self.assertEqual(len(stored), limit)
                    self.assertEqual(stored[1:], entries[:limit - 1])
                    self.assertEqual(stored[0]["input"], None if origin == "image" else payload["text"])
                    self.assertEqual(stored[0]["output"], OUTPUT)
                    self.assertEqual(stored[0]["kind"], "text" if origin == "text" else "ocr")
                    self.assertEqual(attempts, [(path, before, stored)])
                    self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])

    def test_lowered_limit_never_trims_cache_hit_cancel_or_history_optout(self):
        for mode in ("cache", "cancel", "history_off", "request_optout"):
            with self.subTest(mode=mode):
                self.session.perform({"operation": "config_save", "config": self.config})
                path, entries, first = self.seed_history(cached=True)
                before = path.read_bytes()
                self.session.perform({"operation": "config_save", "config": self.config | {
                    CFG.HISTORY_LIMIT: 1, CFG.HISTORY_ENABLED: mode != "history_off"}})
                self.provider.entered.clear()
                self.provider.release.clear() if mode == "cancel" else self.provider.release.set()
                calls_before = len(self.provider.requests)
                with patch.object(self.session._history._owner, "add",
                                  side_effect=AssertionError("non-recording request trimmed history")):
                    self.translate(mode, use_cache=mode in ("cache", "history_off"),
                                   record_history=mode != "request_optout")
                    try:
                        if mode == "cancel":
                            self.assertTrue(self.provider.entered.wait(1))
                            self.server._handle(message("cancel-active", "cancel", request_id=mode))
                            self.assertEqual(self.stdout.result("cancel-active")["payload"],
                                             {"cancel_requested": True})
                    finally:
                        self.provider.release.set()
                    self.assertTrue(self.stdout.terminal(mode))
                result = self.stdout.result(mode)
                if mode == "cancel":
                    self.assertEqual((result["type"], result["payload"]), ("cancelled", {"submitted": True}))
                else:
                    self.assertEqual(result["type"], "completed")
                    self.assertEqual(result["payload"]["history"], "unchanged" if mode == "cache" else "disabled")
                    self.assertEqual(result["payload"]["cached"], mode == "cache")
                self.assertEqual(len(self.provider.requests), calls_before + (mode != "cache"))
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(self.history_page(), first)
                self.assertEqual(self.history_page(first["next_cursor"])["entries"], entries[1:2])

    def test_retention_replace_failure_preserves_full_history_cursor_and_reports_record_error(self):
        path, entries, first = self.seed_history()
        before = path.read_bytes()
        self.session.perform({"operation": "config_save", "config": self.config | {CFG.HISTORY_LIMIT: 2}})
        attempts = []
        def fail_replace(source, destination):
            attempts.append((Path(destination), path.read_bytes(), json.loads(Path(source).read_bytes())))
            raise PermissionError("SYNTHETIC_PRIVATE_RETENTION")
        with patch("cc_storage.os.replace", side_effect=fail_replace):
            self.translate()
            self.assertTrue(self.stdout.terminal("translate"))
        result = self.stdout.result("translate")
        self.assertEqual(result["type"], "completed")
        self.assertEqual((result["payload"]["text"], result["payload"]["submitted"], result["payload"]["cached"]),
                         (OUTPUT, True, False))
        self.assertEqual((result["payload"]["history"], result["payload"]["history_error"]),
                         ("failed", "history_io_failed"))
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0][:2], (path, before))
        self.assertEqual(len(attempts[0][2]), 2)
        self.assertEqual(attempts[0][2][0]["input"], TEXT)
        self.assertEqual(attempts[0][2][1:], entries[:1])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.history_page(), first)
        self.assertEqual(self.history_page(first["next_cursor"])["entries"], entries[1:2])
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(list(self.directory.glob(".tmp_*.json")), [])
        self.assertNotIn("SYNTHETIC_PRIVATE_RETENTION", self.stdout.getvalue().decode() + self.stderr.getvalue())


class ModelCatalogServiceTests(_TranslationDirectory):
    def catalog(self, id_="catalog"):
        self.server._handle(message(id_, "request", operation="model_catalog"))

    def test_explicit_catalog_never_loads_application_config_history_or_submits_model(self):
        self.assertEqual(self.provider.catalog_calls, 0)
        self.assertIn("model_catalog", self.stdout.events[0]["payload"]["capabilities"])
        self.path.write_bytes(b"PRIVATE_BAD_CONFIG")
        history_path = self.directory / "history.json"
        history_path.write_bytes(b"PRIVATE_BAD_HISTORY")
        with patch.object(self.session, "perform", side_effect=AssertionError("config read")), \
                patch.object(self.session, "perform_history", side_effect=AssertionError("history read")), \
                patch.object(self.session, "_capture", side_effect=AssertionError("translation capture")):
            self.catalog()
            self.assertTrue(self.stdout.terminal("catalog"))
        events = [event for event in self.stdout.events if event["id"] == "catalog"]
        self.assertEqual([e["type"] for e in events], ["accepted", "started", "completed"])
        self.assertEqual([e["seq"] for e in events], [0, 1, 2])
        self.assertEqual(events[-1]["payload"], {"models": self.provider.catalog_models})
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(self.path.read_bytes(), b"PRIVATE_BAD_CONFIG")
        self.assertEqual(history_path.read_bytes(), b"PRIVATE_BAD_HISTORY")

    def test_catalog_failure_is_fixed_and_later_translation_is_unaffected(self):
        before = self.path.read_bytes()
        for index, (error, code) in enumerate((
                ("PRIVATE_FAILURE", "model_catalog_failed"), ("timeout", "model_catalog_failed"),
                ("model_catalog_too_large", "model_catalog_too_large"))):
            self.provider.catalog_error = error
            self.catalog(str(index))
            self.assertTrue(self.stdout.terminal(str(index)))
            self.assertEqual(self.stdout.result(str(index))["payload"], {"code": code})
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.history(), [])
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"]["text"], OUTPUT)
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(len(self.provider.requests), 1)

    def test_whole_completed_frame_exact_budget_and_one_byte_over_fail_without_truncation(self):
        id_ = "c" * 64
        model = {"id": "synthetic-\u4e2d", "name": "\U0001f642", "description": ""}
        envelope = {"v": 1, "id": id_, "seq": 2, "type": "completed", "payload": {"models": [model]}}
        available = MAX_FRAME_BYTES - len(encode_frame(envelope))
        # JSON escapes, multibyte scalars and the complete envelope all count.
        model["description"] = "\0" * (available // 6) + "x" * (available % 6)
        self.assertEqual(len(encode_frame(envelope)), MAX_FRAME_BYTES)
        self.provider.catalog_models = [model]
        self.catalog(id_)
        self.assertTrue(self.stdout.terminal(id_))
        self.assertEqual(self.stdout.result(id_)["payload"], {"models": [model]})
        model["description"] += "x"
        too_big = "b" * 64
        self.catalog(too_big)
        self.assertTrue(self.stdout.terminal(too_big))
        self.assertEqual(self.stdout.result(too_big)["payload"], {"code": "model_catalog_too_large"})
        self.assertEqual([e["type"] for e in self.stdout.events if e["id"] == too_big],
                         ["accepted", "started", "failed"])
        self.assertFalse(self.server._stopping)
        self.assertEqual(self.stderr.getvalue(), "")
        self.assertTrue(all(len(raw) + 1 <= MAX_FRAME_BYTES for raw in self.stdout.getvalue().splitlines()))
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["type"], "completed")

    def test_catalog_cancel_waits_for_drain_and_keeps_provider_and_state_owners(self):
        self.provider.release.clear()
        self.catalog()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.server._handle(message("cancel", "cancel", request_id="catalog"))
            self.assertTrue(self.stdout.result("cancel")["payload"]["cancel_requested"])
            self.assertFalse(any(e["id"] == "catalog" and e["type"] == "cancelled" for e in self.stdout.events))
            self.assertIn("catalog", self.server._tasks)
            self.assertEqual(self.provider.closed, 0)
            self.assertIsNotNone(self.session._owner)
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("catalog"))
        self.assertEqual(self.stdout.result("catalog")["type"], "cancelled")
        self.assertEqual(self.stdout.result("catalog")["payload"], {})
        self.assertEqual(self.provider.catalog_calls, 1)
        self.assertEqual(self.provider.requests, [])

    def test_catalog_prestart_cancel_never_calls_provider(self):
        captured = []
        with patch.object(self.server, "_start_translation",
                          side_effect=lambda req, payload: captured.append((req, payload)) or True):
            self.catalog()
        self.server._handle(message("cancel", "cancel", request_id="catalog"))
        self.server._translate(*captured[0])
        self.assertEqual([e["type"] for e in self.stdout.events if e["id"] == "catalog"],
                         ["accepted", "cancelled"])
        self.assertEqual(self.stdout.result("catalog")["payload"], {})
        self.assertEqual(self.provider.catalog_calls, 0)

    def test_catalog_cleanup_failure_wins_over_started_cancel_without_private_detail(self):
        self.provider.release.clear()
        self.provider.catalog_error = "provider_cleanup_failed"
        self.catalog()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.server._handle(message("cancel", "cancel", request_id="catalog"))
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("catalog"))
        self.assertEqual(self.stdout.result("catalog")["type"], "failed")
        self.assertEqual(self.stdout.result("catalog")["payload"], {"code": "provider_cleanup_failed"})

    def test_catalog_finish_rejects_late_cancel_but_honors_cancel_before_publication(self):
        event, result = self.session.model_catalog(threading.Event(), lambda: False)
        self.assertEqual((event, result), ("cancelled", {}))
        original = self.server._send
        def send(req, event, payload):
            if req.id == "catalog" and event == "completed":
                self.server._handle(message("late", "cancel", request_id="catalog"))
            original(req, event, payload)
        with patch.object(self.server, "_send", side_effect=send):
            self.catalog()
            self.assertTrue(self.stdout.terminal("catalog"))
        self.assertFalse(self.stdout.result("late")["payload"]["cancel_requested"])
        self.assertEqual(self.stdout.result("catalog")["type"], "completed")

    def test_catalog_eof_drains_before_provider_shutdown_and_releases_state(self):
        self.provider.release.clear()
        self.catalog()
        self.assertTrue(self.provider.entered.wait(1))
        results = []
        with patch("cc_macos.server.PipeFrameReader") as reader:
            reader.return_value.read.return_value = None
            worker = threading.Thread(target=lambda: results.append(self.server.run()))
            worker.start()
            try:
                self.assertTrue(self.server._stop_event.wait(1))
                self.assertTrue(worker.is_alive())
                self.assertEqual(self.provider.closed, 0)
                self.assertIsNotNone(self.session._owner)
            finally:
                self.provider.release.set()
                worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [0])
        self.assertEqual(self.stdout.result("catalog")["type"], "cancelled")
        self.assertEqual(self.provider.closed, 1)
        self.assertIsNone(self.session._owner)
        self.assertIsNone(self.session._history)

    def test_catalog_shutdown_acknowledges_only_after_read_only_work_drains(self):
        self.provider.release.clear()
        self.catalog()
        self.assertTrue(self.provider.entered.wait(1))
        with patch("cc_macos.server.PipeFrameReader") as reader:
            reader.return_value.read.return_value = message("stop", "shutdown")
            results = []
            worker = threading.Thread(target=lambda: results.append(self.server.run()))
            worker.start()
            try:
                self.assertTrue(self.server._stop_event.wait(1))
                self.assertFalse(any(e["id"] == "stop" for e in self.stdout.events))
                self.assertEqual(self.provider.closed, 0)
            finally:
                self.provider.release.set()
                worker.join(4)
        self.assertEqual(results, [0])
        terminals = [e["id"] for e in self.stdout.events if e["type"] in ("cancelled", "completed")]
        self.assertEqual(terminals, ["catalog", "stop"])
        self.assertEqual(self.stdout.result("stop")["payload"], {})


class OCRTranslationServiceTests(_TranslationDirectory):
    def test_explicit_ocr_streams_once_per_click_without_cache_or_local_dictionary_and_records_flags(self):
        self.session.perform({"operation": "config_save", "config": self.config | {
            CFG.SUMMARY_ENABLED: True, CFG.LOCAL_DICTIONARY_ENABLED: True}})
        self.assertEqual(self.provider.requests, [])
        self.assertFalse((self.directory / "dictionary").exists())
        inputs = [(OCR_TEXT, False, False), ("hello", True, False),
                  ("def greeting():\n    return 42", False, True)]
        with patch.object(self.session._history, "find_cached", side_effect=AssertionError("OCR cache")), \
                patch.object(self.session._dictionary, "perform", side_effect=AssertionError("OCR local dictionary")):
            for index, (text, is_dict, is_code) in enumerate(inputs):
                for click in (0, 1):
                    id_ = "ocr" + str(index) + str(click)
                    self.translate(id_, origin="ocr", text=text)
                    self.assertTrue(self.stdout.terminal(id_))
                    events = [e for e in self.stdout.events if e["id"] == id_]
                    self.assertEqual([e["type"] for e in events], ["accepted", "started", "delta", "completed"])
                    self.assertEqual([e["seq"] for e in events], [0, 1, 2, 3])
                    payload = events[-1]["payload"]
                    self.assertEqual(payload, {
                        "text": OUTPUT, "submitted": True, "cached": False, "kind": "ocr",
                        "target_lang": None if is_dict or is_code else "zh", "summarize": False,
                        "history": "recorded", "history_error": None,
                    })
                    entry = self.history()[0]
                    self.assertEqual((entry["input"], entry["output"], entry["kind"], entry["is_dict"], entry["is_code"]),
                                     (text, OUTPUT, "ocr", is_dict, is_code))
                    self.assertEqual(self.provider.requests[-1].user_text, text)
                    self.assertEqual(self.provider.requests[-1].image_paths, ())
        self.assertEqual(len(self.provider.requests), 6)
        self.assertEqual(len(self.history()), 6)
        self.assertFalse((self.directory / "dictionary").exists())

    def test_ocr_complete_path_uses_same_contract_and_explicit_history_optout(self):
        config = self.config | {CFG.CODEX_STREAMING_EXPERIMENTAL: False}
        with patch.object(self.session, "perform", return_value={"config": config}), \
                patch.object(self.session._history, "find_cached", side_effect=AssertionError("OCR cache")):
            self.translate(origin="ocr", text=OCR_TEXT, record_history=False)
            self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual([e["type"] for e in self.stdout.events if e["id"] == "translate"],
                         ["accepted", "started", "completed"])
        self.assertEqual(self.stdout.result("translate")["payload"]["history"], "disabled")
        self.assertEqual(self.provider.requests[0].timeout_seconds, 60)
        self.assertEqual(self.history(), [])

    def test_ocr_corrupt_history_never_blocks_cache_bypass_or_repairs_history(self):
        path = self.directory / "history.json"
        path.write_bytes(b"broken")
        for index, (enabled, record, status, error) in enumerate((
                (False, True, "disabled", None), (True, False, "disabled", None),
                (True, True, "failed", "invalid_history"))):
            self.session.perform({"operation": "config_save", "config": self.config | {CFG.HISTORY_ENABLED: enabled}})
            with patch.object(self.session._history, "find_cached", side_effect=AssertionError("OCR cache")):
                self.translate(str(index), origin="ocr", record_history=record)
                self.assertTrue(self.stdout.terminal(str(index)))
            result = self.stdout.result(str(index))
            self.assertEqual(result["type"], "completed")
            self.assertEqual((result["payload"]["text"], result["payload"]["kind"],
                              result["payload"]["history"], result["payload"]["history_error"]),
                             (OUTPUT, "ocr", status, error))
            self.assertEqual(path.read_bytes(), b"broken")
        self.assertEqual(len(self.provider.requests), 3)

    def test_ocr_running_snapshot_ignores_later_config_and_honors_latest_history_optout(self):
        self.provider.release.clear()
        self.translate(origin="ocr", text=OCR_TEXT)
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.session.perform({"operation": "config_save", "config": self.config | {
                CFG.CODEX_MODEL: "next-model", CFG.DIRECTION: "to_ja", CFG.HISTORY_ENABLED: False}})
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("translate"))
        captured = self.provider.requests[0]
        self.assertEqual((captured.model, captured.user_text, captured.image_paths), ("synthetic", OCR_TEXT, ()))
        self.assertEqual(captured.system_prompt, direction_prompt("auto", "en_US") + OCR_STRUCTURE_HINT + SYSTEM_SUFFIX)
        result = self.stdout.result("translate")["payload"]
        self.assertEqual((result["target_lang"], result["history"]), ("zh", "disabled"))
        self.assertEqual(self.history(), [])

    def test_ocr_prestart_cancel_and_finish_rejection_never_record_or_replay(self):
        queued = []
        with patch.object(self.server, "_start_translation",
                          side_effect=lambda req, payload: queued.append((req, payload)) or True):
            self.translate(origin="ocr")
        self.server._handle(message("cancel", "cancel", request_id="translate"))
        self.server._translate(*queued[0])
        self.assertEqual([(e["type"], e["payload"]) for e in self.stdout.events if e["id"] == "translate"],
                         [("accepted", {"operation": "translate"}), ("cancelled", {})])
        self.assertEqual(self.provider.requests, [])
        cancel = threading.Event()
        cancel.set()
        result = self.session.translate(request(origin="ocr"), cancel, lambda _: None, lambda: True)
        self.assertEqual(result, ("cancelled", {"submitted": False}))
        self.assertEqual(self.provider.requests, [])
        result = self.session.translate(request(origin="ocr"), threading.Event(), lambda _: None, lambda: False)
        self.assertEqual(result, ("cancelled", {"submitted": True}))
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.history(), [])

    def test_ocr_partial_cancel_waits_for_drain_and_cleanup_failure_remains_a_failure(self):
        for cleanup_failure in (False, True):
            emitted, release = threading.Event(), threading.Event()
            def partial(captured, on_delta, cancel):
                self.provider.requests.append(captured)
                on_delta("Partial OCR text")
                emitted.set()
                if not release.wait(3):
                    raise AssertionError("Synthetic drain not released")
                return ProviderResult(False, error_code="group_cleanup_failed" if cleanup_failure else "cancelled",
                                      metrics=(("turn_submitted", True),))
            id_ = "ocr" + str(cleanup_failure)
            with patch.object(self.provider, "stream", side_effect=partial):
                self.translate(id_, origin="ocr")
                try:
                    self.assertTrue(emitted.wait(1))
                    self.server._handle(message("cancel" + id_, "cancel", request_id=id_))
                    self.assertTrue(self.stdout.result("cancel" + id_)["payload"]["cancel_requested"])
                    self.assertFalse(any(e["id"] == id_ and e["type"] in ("cancelled", "failed")
                                         for e in self.stdout.events))
                finally:
                    release.set()
                self.assertTrue(self.stdout.terminal(id_))
            expected = ({"code": "provider_cleanup_failed", "submitted": True}
                        if cleanup_failure else {"submitted": True})
            self.assertEqual(self.stdout.result(id_)["payload"], expected)
            self.assertEqual(self.stdout.result(id_)["type"], "failed" if cleanup_failure else "cancelled")
        self.assertEqual(len(self.provider.requests), 2)
        self.assertEqual(self.history(), [])

    def test_ocr_provider_errors_and_output_limits_never_record_partial_success(self):
        for code, expected in (("rpc_timeout", "translation_timeout"), ("PRIVATE", "provider_failed"),
                                ("invalid_appserver_message", "provider_protocol_error")):
            self.provider.result_code = code
            self.translate(code, origin="ocr")
            self.assertTrue(self.stdout.terminal(code))
            self.assertEqual(self.stdout.result(code)["payload"], {"code": expected, "submitted": True})
        self.provider.result_code = ""
        self.provider.chunks = ["\0" * 4000]
        self.translate("overflow", origin="ocr")
        self.assertTrue(self.stdout.terminal("overflow"))
        self.assertEqual(self.stdout.result("overflow")["payload"],
                         {"code": "translation_output_limit", "submitted": True})
        self.assertEqual(self.history(), [])
        self.assertEqual(len(self.provider.requests), 4)

    def test_ocr_unknown_transport_has_no_fabricated_terminal_or_retry(self):
        def unknown(captured, on_delta, _cancel):
            self.provider.requests.append(captured)
            on_delta("Partial OCR")
            raise ProcessError("PRIVATE transport")
        with patch.object(self.provider, "stream", side_effect=unknown):
            self.translate(origin="ocr")
            self.server._join_workers()
        self.assertEqual([e["type"] for e in self.stdout.events if e["id"] == "translate"],
                         ["accepted", "started", "delta"])
        self.assertEqual(self.stdout.result(RESERVED_ID)["payload"], {"code": "internal_error"})
        self.assertTrue(self.server._stopping)
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.history(), [])

    def test_ocr_shutdown_drains_before_releasing_provider_and_state_owners(self):
        self.provider.release.clear()
        self.translate(origin="ocr")
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.assertFalse(self.server._handle(message("shutdown", "shutdown")))
            self.assertEqual(self.provider.closed, 0)
            self.assertIsNotNone(self.session._owner)
        finally:
            self.provider.release.set()
        self.server._join_workers()
        self.session.close()
        self.assertEqual(self.stdout.result("translate")["type"], "cancelled")
        self.assertEqual(self.provider.closed, 1)
        self.assertIsNone(self.session._owner)
        self.assertIsNone(self.session._history)
        self.assertFalse((self.directory / "history.json").exists())

    def test_ocr_config_failure_remains_pre_submission_and_does_not_call_provider(self):
        self.path.write_bytes(b"{broken")
        self.translate(origin="ocr")
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"], {"code": "invalid_config", "submitted": False})
        self.assertEqual(self.path.read_bytes(), b"{broken")
        self.assertEqual(self.provider.requests, [])


class TranslationServiceTests(_TranslationDirectory):
    def test_termination_signal_only_marks_then_run_drains_execution_and_releases_owners(self):
        class Reader:
            def __init__(self, _stream, stopping):
                self.stopping = stopping

            def read(self):
                while not self.stopping.is_set():
                    self.stopping.event.wait(0.01)
                return None

        self.provider.release.clear()
        self.translate()
        self.assertTrue(self.provider.entered.wait(1))
        results = []
        with patch("cc_macos.server.PipeFrameReader", Reader):
            worker = threading.Thread(target=lambda: results.append(self.server.run()))
            worker.start()
            try:
                with self.server._lock:
                    self.server.request_termination(signal.SIGTERM, None)
                    self.assertFalse(self.server._stopping)
                    self.assertFalse(self.server._stop_event.is_set())
                    self.assertTrue(self.server._read_stop.is_set())
                self.assertTrue(self.server._stop_event.wait(1))
                self.assertTrue(worker.is_alive())
                self.assertEqual(self.provider.closed, 0)
                self.assertIsNotNone(self.session._owner)
                self.assertFalse(any(e["id"] == "translate" and e["type"] == "cancelled"
                                     for e in self.stdout.events))
            finally:
                self.provider.release.set()
                worker.join(4)
            self.assertFalse(worker.is_alive())
        self.assertEqual(results, [128 + signal.SIGTERM])
        self.assertEqual(self.stdout.result("translate")["payload"], {"submitted": True})
        self.assertEqual(self.provider.closed, 1)
        self.assertIsNone(self.session._owner)
        self.assertIsNone(self.session._history)
        self.assertEqual(self.stderr.getvalue(), "")

    def test_history_commit_cannot_be_cancelled_or_released_early(self):
        entered, release = threading.Event(), threading.Event()
        original = self.session._record
        def record(*args):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic history gate was not released.")
            return original(*args)
        with patch.object(self.session, "_record", side_effect=record):
            self.translate()
            try:
                self.assertTrue(entered.wait(1))
                self.server._handle(message("cancel", "cancel", request_id="translate"))
                self.assertIs(self.stdout.result("cancel")["payload"]["cancel_requested"], False)
                self.server._stop()
                self.assertIsNotNone(self.session._owner)
                self.assertFalse(any(e["id"] == "translate" and e["type"] in ("cancelled", "completed")
                                     for e in self.stdout.events))
            finally:
                release.set()
            self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["type"], "completed")
        self.assertEqual(len(self.history()), 1)

    def test_cleanup_failure_has_priority_over_started_cancellation(self):
        def fail(_request, _delta, cancel):
            cancel.set()
            return ProviderResult(False, error_code="group_cleanup_failed",
                                  metrics=(("turn_submitted", True),))
        with patch.object(self.provider, "stream", side_effect=fail):
            self.translate()
            self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"],
                         {"code": "provider_cleanup_failed", "submitted": True})
        self.assertEqual(self.history(), [])

    def test_shutdown_cleanup_failure_is_fixed_and_releases_both_state_owners(self):
        reader = iter([message("shutdown", "shutdown")])
        with patch("cc_macos.server.PipeFrameReader") as factory, \
                patch.object(self.provider, "shutdown", side_effect=ProcessError("PRIVATE_cleanup_failed")):
            factory.return_value.read.side_effect = lambda: next(reader)
            self.assertEqual(self.server.run(), 2)
        self.assertEqual(self.stdout.result("shutdown")["payload"], {"code": "provider_cleanup_failed"})
        self.assertIsNone(self.session._owner)
        self.assertIsNone(self.session._history)
        self.assertNotIn("PRIVATE", self.stderr.getvalue())

    def test_unexpected_transport_error_closes_without_fabricating_submission_state(self):
        with patch.object(self.provider, "stream", side_effect=ProcessError("PRIVATE_transport")):
            self.translate()
            self.server._join_workers()
        self.assertEqual([e["type"] for e in self.stdout.events if e["id"] == "translate"],
                         ["accepted", "started"])
        self.assertEqual(self.stdout.result(RESERVED_ID)["payload"], {"code": "internal_error"})
        self.assertTrue(self.server._stopping)
        self.assertNotIn("PRIVATE", self.stderr.getvalue())

    def test_invalid_unicode_delta_and_final_text_have_fixed_errors_without_history(self):
        for id_, chunks, text in (("delta", ["\ud800"], OUTPUT), ("final", [], "\ud800")):
            with self.subTest(id_=id_):
                self.provider.chunks, self.provider.text = chunks, text
                self.translate(id_)
                self.assertTrue(self.stdout.terminal(id_))
                self.assertEqual(self.stdout.result(id_)["payload"],
                                 {"code": "provider_protocol_error", "submitted": True})
        self.assertEqual(self.history(), [])

    def test_ready_is_native_and_first_request_streams_records_then_hits_cache(self):
        ready = self.stdout.events[0]["payload"]
        self.assertEqual((ready["backend"], ready["fixture"]), ("native_appserver", False))
        self.assertEqual(len(ready["capabilities"]), 15)
        self.assertIn("result_action", ready["capabilities"])
        self.assertEqual(ready["capabilities"][-6:], [
            "dictionary_status", "dictionary_lookup", "dictionary_prepare_install",
            "dictionary_install", "dictionary_discard_install", "dictionary_delete"])
        self.assertEqual(self.provider.requests, [])
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        events = [e for e in self.stdout.events if e["id"] == "translate"]
        self.assertEqual([e["type"] for e in events], ["accepted", "started", "delta", "completed"])
        self.assertEqual([e["seq"] for e in events], [0, 1, 2, 3])
        self.assertEqual(events[-1]["payload"]["history"], "recorded")
        self.assertEqual(self.history()[0]["input"], TEXT)
        self.translate("cached")
        self.assertTrue(self.stdout.terminal("cached"))
        result = self.stdout.result("cached")["payload"]
        self.assertEqual((result["cached"], result["submitted"], result["history"]), (True, False, "unchanged"))
        self.assertEqual(len(self.provider.requests), 1)

    def test_current_history_optout_can_commit_while_native_request_is_running(self):
        self.provider.release.clear()
        self.translate()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.server._handle(message("optout", "request", operation="config_save",
                                        config=self.config | {CFG.HISTORY_ENABLED: False}))
            self.assertTrue(self.stdout.terminal("optout"))
            self.assertFalse(self.session.perform({"operation": "config_load"})["config"][CFG.HISTORY_ENABLED])
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"]["history"], "disabled")
        self.assertEqual(self.history(), [])

    def test_started_cancel_waits_for_execution_cleanup_before_terminal(self):
        self.provider.release.clear()
        self.translate()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.server._handle(message("cancel", "cancel", request_id="translate"))
            self.assertTrue(self.stdout.result("cancel")["payload"]["cancel_requested"])
            self.assertFalse(any(e["id"] == "translate" and e["type"] == "cancelled" for e in self.stdout.events))
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"], {"submitted": True})
        self.assertEqual(self.history(), [])

    def test_queued_cancel_is_determinate_and_does_not_execute(self):
        captured = []
        with patch.object(self.server, "_start_translation",
                          side_effect=lambda req, payload: captured.append((req, payload)) or True):
            self.translate()
        self.server._handle(message("cancel", "cancel", request_id="translate"))
        self.server._translate(*captured[0])
        events = [e for e in self.stdout.events if e["id"] == "translate"]
        self.assertEqual([(e["seq"], e["type"], e["payload"]) for e in events],
                         [(0, "accepted", {"operation": "translate"}), (1, "cancelled", {})])
        self.assertEqual(self.provider.requests, [])

    def test_thread_start_failure_keeps_original_exact_prestart_terminal(self):
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("synthetic")):
            self.translate()
        events = [e for e in self.stdout.events if e["id"] == "translate"]
        self.assertEqual([(e["seq"], e["type"]) for e in events], [(0, "accepted"), (1, "failed")])
        self.assertEqual(events[-1]["payload"], {"code": "worker_start_failed"})
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(self.server._workers, set())

    def test_malformed_and_oversize_requests_fail_before_acceptance(self):
        self.translate(text="a" * 8193)
        result = self.stdout.result("translate")
        self.assertEqual((result["seq"], result["type"], result["payload"]),
                         (0, "failed", {"code": "invalid_translation"}))
        self.assertEqual(self.provider.requests, [])

    def test_corrupt_configuration_is_not_overwritten_and_does_not_call_provider(self):
        self.path.write_bytes(b"{broken")
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"], {"code": "invalid_config", "submitted": False})
        self.assertEqual(self.path.read_bytes(), b"{broken")
        self.assertEqual(self.provider.requests, [])

    def test_corrupt_history_is_not_an_empty_cache(self):
        path = self.directory / "history.json"
        path.write_bytes(b"not-json")
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"], {"code": "invalid_history", "submitted": False})
        self.assertEqual(path.read_bytes(), b"not-json")
        self.assertEqual(self.provider.requests, [])

    def test_output_escape_budget_rejects_without_truncation_or_history(self):
        self.provider.chunks = ["\0" * 4000]
        self.provider.text = self.provider.chunks[0]
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"],
                         {"code": "translation_output_limit", "submitted": True})
        self.assertEqual(self.history(), [])

    def test_exact_output_budget_splits_utf8_and_preserves_result(self):
        text = "\u4e2d" * 7999 + "a"
        self.provider.chunks, self.provider.text = [text], text
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        deltas = [e["payload"]["text"] for e in self.stdout.events if e["type"] == "delta"]
        self.assertEqual("".join(deltas), text)
        self.assertTrue(all(translation.text_bytes(value) <= 4096 for value in deltas))
        self.assertEqual(self.stdout.result("translate")["payload"]["text"], text)

    def test_stream_budget_counts_actual_envelopes_and_retains_space_for_failure(self):
        self.provider.chunks = ["x"] * 23000
        self.translate(id_="r" * 64, use_cache=False)
        self.assertTrue(self.stdout.terminal("r" * 64))
        result = self.stdout.result("r" * 64)
        self.assertEqual(result["payload"], {"code": "translation_output_limit", "submitted": True})
        self.assertLessEqual(len(self.stdout.getvalue()), translation.MAX_STREAM_BYTES + 512)
        self.assertEqual(self.history(), [])

    def test_explicit_record_optout_and_disabled_cache_do_not_change_history(self):
        self.translate(record_history=False, use_cache=False)
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"]["history"], "disabled")
        self.assertEqual(self.history(), [])

    def test_record_failure_surfaces_separately_without_losing_successful_translation(self):
        with patch.object(self.session, "perform_history", side_effect=configuration.ConfigurationError("history_io_failed")):
            self.translate()
            self.assertTrue(self.stdout.terminal("translate"))
        result = self.stdout.result("translate")
        self.assertEqual(result["type"], "completed")
        self.assertEqual((result["payload"]["text"], result["payload"]["history_error"]),
                         (OUTPUT, "history_io_failed"))
        self.assertEqual(result["payload"]["history"], "failed")

    def test_shutdown_cancels_active_translation_then_releases_workers(self):
        self.provider.release.clear()
        self.translate()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.assertFalse(self.server._handle(message("shutdown", "shutdown")))
            self.assertTrue(self.server._tasks["translate"].cancel.is_set())
        finally:
            self.provider.release.set()
        self.server._join_workers()
        self.assertEqual(self.stdout.result("translate")["type"], "cancelled")
        self.assertEqual(self.provider.requests[0].user_text, TEXT)

    def test_lost_stdout_cancels_without_replaying_or_recording(self):
        self.stdout.break_on_delta = True
        self.translate()
        self.server._join_workers()
        self.assertTrue(self.server._pipe_closed)
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.history(), [])
        self.assertNotIn("SYNTHETIC_PRIVATE", self.stderr.getvalue())

    def test_failure_mapping_does_not_echo_provider_detail(self):
        self.provider.result_code = "SYNTHETIC_PRIVATE_PROVIDER"
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"], {"code": "provider_failed", "submitted": True})
        self.assertNotIn("SYNTHETIC_PRIVATE", self.stdout.getvalue().decode())


class ResultActionServiceTests(_TranslationDirectory):
    def action(self, id_="action", action="concise", **changes):
        self.server._handle(message(id_, "request", **action_request(action, **changes)))

    def test_all_actions_stream_or_complete_with_exact_translation_terminal_and_no_history_access(self):
        for streaming in (False, True):
            # Isolate both execution paths; persisted settings currently migrate streaming to true.
            config = self.config | {CFG.CODEX_STREAMING_EXPERIMENTAL: streaming, CFG.HISTORY_ENABLED: True}
            for action in translation.RESULT_ACTIONS:
                id_ = action + str(streaming)
                with self.subTest(action=action, streaming=streaming), \
                        patch.object(self.session, "perform", return_value={"config": config}), \
                        patch.object(self.session._history, "find_cached", side_effect=AssertionError("cache read")), \
                        patch.object(self.session, "_record", side_effect=AssertionError("history write")):
                    self.action(id_, action)
                    self.assertTrue(self.stdout.terminal(id_))
                events = [e for e in self.stdout.events if e["id"] == id_]
                self.assertEqual([e["type"] for e in events],
                                 ["accepted", "started"] + (["delta"] if streaming else []) + ["completed"])
                self.assertEqual([e["seq"] for e in events], list(range(len(events))))
                self.assertEqual(events[0]["payload"], {"operation": "result_action"})
                self.assertEqual(events[1]["payload"], {"operation": "result_action"})
                if streaming:
                    self.assertEqual(events[2]["payload"], {"text": OUTPUT, "submitted": True})
                self.assertEqual(events[-1]["payload"], {
                    "text": OUTPUT, "submitted": True, "cached": False, "kind": "text",
                    "target_lang": "ja" if action == "retranslate" else "zh" if action == "as_text" else None,
                    "summarize": False, "history": "disabled", "history_error": None,
                })
        self.assertEqual(len(self.provider.requests), 12)
        self.assertEqual(self.history(), [])

    def test_actions_neither_use_existing_translation_cache_nor_pollute_later_cache_or_history(self):
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        existing = self.history()
        self.provider.text, self.provider.chunks = "Action output", ["Action output"]
        for action in translation.RESULT_ACTIONS:
            with patch.object(self.session._history, "find_cached", side_effect=AssertionError("cache read")):
                self.action(action, action)
                self.assertTrue(self.stdout.terminal(action))
            self.assertEqual(self.stdout.result(action)["payload"]["text"], "Action output")
        self.assertEqual(self.history(), existing)
        self.translate("cached")
        self.assertTrue(self.stdout.terminal("cached"))
        result = self.stdout.result("cached")["payload"]
        self.assertEqual((result["text"], result["cached"], result["history"]), (OUTPUT, True, "unchanged"))
        self.assertEqual(len(self.provider.requests), 7)
        self.assertEqual(self.history(), existing)

    def test_action_captures_selected_model_direction_streaming_and_text_until_provider_finishes(self):
        self.provider.release.clear()
        self.action(action="as_text")
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.server._handle(message("settings", "request", operation="config_save",
                                        config=self.config | {CFG.CODEX_MODEL: "new-model", CFG.DIRECTION: "to_ko",
                                                              CFG.CODEX_STREAMING_EXPERIMENTAL: False}))
            self.assertTrue(self.stdout.terminal("settings"))
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("action"))
        first = self.provider.requests[0]
        self.assertEqual((first.model, first.user_text, first.timeout_seconds), ("synthetic", TEXT, 90))
        self.assertEqual(first.system_prompt, direction_prompt("auto", "en_US") + SYSTEM_SUFFIX)
        self.assertEqual(self.stdout.result("action")["payload"]["target_lang"], "zh")
        self.action("next", "as_text")
        self.assertTrue(self.stdout.terminal("next"))
        second = self.provider.requests[1]
        self.assertEqual((second.model, second.timeout_seconds), ("new-model", 90))
        self.assertEqual(second.system_prompt, direction_prompt("to_ko", "en_US") + SYSTEM_SUFFIX)
        self.assertEqual(self.stdout.result("next")["payload"]["target_lang"], "ko")
        self.assertTrue(any(e["id"] == "next" and e["type"] == "delta" for e in self.stdout.events))

    def test_partial_cancellation_waits_for_provider_drain_then_emits_one_cancelled_terminal(self):
        emitted, release = threading.Event(), threading.Event()
        def partial(captured, on_delta, cancel):
            self.provider.requests.append(captured)
            on_delta("Partial action")
            emitted.set()
            if not release.wait(3):
                raise AssertionError("Synthetic drain gate was not released")
            return ProviderResult(False, error_code="cancelled", metrics=(("turn_submitted", True),))
        with patch.object(self.provider, "stream", side_effect=partial):
            self.action()
            try:
                self.assertTrue(emitted.wait(1))
                self.server._handle(message("cancel", "cancel", request_id="action"))
                self.assertTrue(self.stdout.result("cancel")["payload"]["cancel_requested"])
                self.assertIn("action", self.server._tasks)
                self.assertFalse(any(e["id"] == "action" and e["type"] == "cancelled" for e in self.stdout.events))
            finally:
                release.set()
            self.assertTrue(self.stdout.terminal("action"))
        events = [e for e in self.stdout.events if e["id"] == "action"]
        self.assertEqual([e["type"] for e in events], ["accepted", "started", "delta", "cancelled"])
        self.assertEqual(events[-1]["payload"], {"submitted": True})
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.history(), [])
        self.action("next", "formal")
        self.assertTrue(self.stdout.terminal("next"))
        self.assertEqual(self.stdout.result("next")["type"], "completed")

    def test_cancellation_before_execution_is_determinate_and_never_calls_provider(self):
        queued = []
        with patch.object(self.server, "_start_translation",
                          side_effect=lambda req, payload: queued.append((req, payload)) or True):
            self.action()
        self.server._handle(message("cancel", "cancel", request_id="action"))
        self.server._translate(*queued[0])
        self.assertEqual([(e["type"], e["payload"]) for e in self.stdout.events if e["id"] == "action"],
                         [("accepted", {"operation": "result_action"}), ("cancelled", {})])
        self.assertEqual(self.provider.requests, [])
        cancel = threading.Event()
        cancel.set()
        with patch.object(self.provider, "stream", side_effect=AssertionError("provider call")):
            result = self.session.result_action(action_request(), cancel, lambda _text: None, lambda: True)
        self.assertEqual(result, ("cancelled", {"submitted": False}))

    def test_cancel_at_finish_does_not_publish_success_or_record_action(self):
        with patch.object(self.session, "_record", side_effect=AssertionError("history write")):
            result = self.session.result_action(action_request(), threading.Event(), lambda _text: None, lambda: False)
        self.assertEqual(result, ("cancelled", {"submitted": True}))
        self.assertEqual(self.history(), [])

    def test_committing_action_rejects_late_cancel_and_preserves_completed_result(self):
        entered, release = threading.Event(), threading.Event()
        send = self.server._send
        def gated_send(request_, event, payload):
            if request_.id == "action" and event == "completed":
                entered.set()
                if not release.wait(3):
                    raise AssertionError("Synthetic completion gate was not released")
            return send(request_, event, payload)
        with patch.object(self.server, "_send", side_effect=gated_send):
            self.action()
            try:
                self.assertTrue(entered.wait(1))
                self.server._handle(message("cancel", "cancel", request_id="action"))
                self.assertFalse(self.stdout.result("cancel")["payload"]["cancel_requested"])
                self.assertFalse(self.server._tasks["action"].cancel.is_set())
            finally:
                release.set()
            self.assertTrue(self.stdout.terminal("action"))
        self.assertEqual(self.stdout.result("action")["type"], "completed")
        self.assertEqual(self.history(), [])

    def test_cleanup_failure_has_priority_over_partial_action_cancellation(self):
        def fail(_request, on_delta, cancel):
            on_delta("Partial")
            cancel.set()
            return ProviderResult(False, error_code="group_cleanup_failed",
                                  metrics=(("turn_submitted", True),))
        with patch.object(self.provider, "stream", side_effect=fail):
            self.action()
            self.assertTrue(self.stdout.terminal("action"))
        self.assertEqual(self.stdout.result("action")["payload"], {"code": "provider_cleanup_failed", "submitted": True})
        self.assertEqual([e["type"] for e in self.stdout.events if e["id"] == "action"],
                         ["accepted", "started", "delta", "failed"])
        self.assertEqual(self.history(), [])

    def test_unknown_transport_after_partial_does_not_fabricate_action_terminal_or_replay(self):
        def unknown(captured, on_delta, _cancel):
            self.provider.requests.append(captured)
            on_delta("Partial")
            raise ProcessError("PRIVATE_transport")
        with patch.object(self.provider, "stream", side_effect=unknown):
            self.action()
            self.server._join_workers()
        self.assertEqual([e["type"] for e in self.stdout.events if e["id"] == "action"],
                         ["accepted", "started", "delta"])
        self.assertEqual(self.stdout.result(RESERVED_ID)["payload"], {"code": "internal_error"})
        self.assertTrue(self.server._stopping)
        self.assertEqual(len(self.provider.requests), 1)
        self.assertNotIn("PRIVATE", self.stderr.getvalue())
        self.assertEqual(self.history(), [])

    def test_known_failures_preserve_submission_and_fixed_error_categories_without_replay(self):
        cases = (
            ("appserver_version_unsupported", "provider_version_unsupported", False),
            ("appserver_version_unreadable", "provider_version_unreadable", False),
            ("appserver_version_prerelease", "provider_version_prerelease", False),
            ("timeout", "translation_timeout", True),
            ("rpc_timeout", "translation_timeout", True),
            ("invalid_appserver_message", "provider_protocol_error", True),
            ("PRIVATE_provider_failure", "provider_failed", True),
        )
        for index, (provider_code, code, submitted) in enumerate(cases):
            result = ProviderResult(False, error_code=provider_code, metrics=(("turn_submitted", submitted),))
            with self.subTest(code=provider_code), patch.object(self.provider, "stream", return_value=result) as stream:
                self.action(str(index))
                self.assertTrue(self.stdout.terminal(str(index)))
            stream.assert_called_once()
            self.assertEqual(self.stdout.result(str(index))["payload"], {"code": code, "submitted": submitted})
        self.assertNotIn("PRIVATE", self.stdout.getvalue().decode())
        self.assertEqual(self.history(), [])

    def test_empty_oversize_invalid_unicode_and_nontext_outputs_fail_without_history(self):
        cases = (
            (["\ud800"], OUTPUT, "provider_protocol_error"),
            ([], "\ud800", "provider_protocol_error"),
            ([123], OUTPUT, "provider_protocol_error"),
            ([], "", "translation_output_limit"),
            ([], " ", "translation_output_limit"),
            ([], None, "translation_output_limit"),
            (["\0" * 4000], OUTPUT, "translation_output_limit"),
            ([], "a" * 23999, "translation_output_limit"),
        )
        for index, (chunks, text, code) in enumerate(cases):
            with self.subTest(index=index):
                self.provider.chunks, self.provider.text = chunks, text
                self.action(str(index))
                self.assertTrue(self.stdout.terminal(str(index)))
                self.assertEqual(self.stdout.result(str(index))["payload"], {"code": code, "submitted": True})
        self.assertEqual(self.history(), [])

    def test_exact_output_budget_splits_utf8_deltas_and_returns_full_action(self):
        text = "\u4e2d" * 7999 + "a"
        self.provider.chunks, self.provider.text = [text], text
        self.action()
        self.assertTrue(self.stdout.terminal("action"))
        deltas = [e["payload"]["text"] for e in self.stdout.events if e["id"] == "action" and e["type"] == "delta"]
        self.assertEqual("".join(deltas), text)
        self.assertTrue(all(translation.text_bytes(value) <= 4096 for value in deltas))
        self.assertEqual(self.stdout.result("action")["payload"]["text"], text)
        self.assertEqual(self.history(), [])

    def test_action_stream_budget_counts_envelopes_and_retains_room_for_terminal(self):
        self.provider.chunks = ["x"] * 23000
        id_ = "r" * 64
        self.action(id_)
        self.assertTrue(self.stdout.terminal(id_))
        self.assertEqual(self.stdout.result(id_)["payload"], {"code": "translation_output_limit", "submitted": True})
        self.assertLessEqual(len(self.stdout.getvalue()), translation.MAX_STREAM_BYTES + 512)
        self.assertEqual(self.history(), [])

    def test_corrupt_history_is_not_read_or_repaired_by_action(self):
        path = self.directory / "history.json"
        path.write_bytes(b"not-json")
        self.action()
        self.assertTrue(self.stdout.terminal("action"))
        self.assertEqual(self.stdout.result("action")["type"], "completed")
        self.assertEqual(self.stdout.result("action")["payload"]["history"], "disabled")
        self.assertEqual(path.read_bytes(), b"not-json")
        self.translate()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"], {"code": "invalid_history", "submitted": False})
        self.assertEqual(len(self.provider.requests), 1)

    def test_corrupt_config_fails_before_action_provider_and_is_not_overwritten(self):
        self.path.write_bytes(b"{broken")
        self.action()
        self.assertTrue(self.stdout.terminal("action"))
        self.assertEqual(self.stdout.result("action")["payload"], {"code": "invalid_config", "submitted": False})
        self.assertEqual(self.path.read_bytes(), b"{broken")
        self.assertEqual(self.provider.requests, [])

    def test_shutdown_cancels_action_and_drains_before_removing_active_task(self):
        self.provider.release.clear()
        self.action()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.assertFalse(self.server._handle(message("shutdown", "shutdown")))
            self.assertTrue(self.server._tasks["action"].cancel.is_set())
            self.assertIn("action", self.server._tasks)
        finally:
            self.provider.release.set()
        self.server._join_workers()
        self.assertEqual(self.stdout.result("action")["type"], "cancelled")
        self.assertFalse(self.server._workers)
        self.assertEqual(self.history(), [])

    def test_lost_stdout_cancels_action_without_replay_or_history(self):
        self.stdout.break_on_delta = True
        self.action()
        self.server._join_workers()
        self.assertTrue(self.server._pipe_closed)
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.history(), [])
        self.assertNotIn("SYNTHETIC_PRIVATE", self.stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
