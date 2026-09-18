"""Provider binding across bootstrap, snapshots, cache, history, and native helper requests."""

import hashlib
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from cc_config import CFG, Config
from cc_macos import configuration, translation
from cc_macos.image_fixture import PNG_BYTES
from cc_macos.protocol import ProtocolError
from cc_providers.base import CLAUDE_PROVIDER, CODEX_PROVIDER
from cc_prompts import PROVIDER_PROMPT_REVISIONS
from cc_result_rules import provider_cache_signature

if __package__:
    from . import test_macos_translation as shared
else:
    import test_macos_translation as shared


def claude_config(**changes):
    return Config({CFG.MODEL_PROVIDER: CLAUDE_PROVIDER, CFG.CLAUDE_MODEL: "claude-custom",
                   CFG.CODEX_MODEL: "codex-unrelated", CFG.SUMMARY_ENABLED: False,
                   CFG.LABS_DEFAULTS_MIGRATED: True, **changes})


class ClaudeSnapshotContracts(unittest.TestCase):
    def test_explicit_bootstrap_selects_only_its_own_environment_without_constructing_cli(self):
        home = str(Path.cwd())
        command = str(Path(home) / "synthetic-cli")
        flags = {CLAUDE_PROVIDER: "--claude-command", CODEX_PROVIDER: "--codex-command"}
        env = {key: json.dumps({"HOME": home, "PATH": "", "SELECTED": provider})
               for provider, key in translation.CLI_ENVIRONMENT_KEYS.items()}
        with patch.object(translation, "DarwinCodexProvider") as codex, \
                patch.object(translation, "DarwinClaudeProvider") as claude, \
                patch.object(Path, "mkdir", side_effect=AssertionError("Implicit IO")):
            self.assertIsNone(configuration.startup_configuration([], environment=env))
            for provider, flag in flags.items():
                session = configuration.startup_configuration(
                    ["--config-home", home, "--application-id", "synthetic", flag, command], environment=env)
                self.assertEqual(session.provider_id, provider)
                self.assertEqual(session.environment["SELECTED"], provider)
                self.assertEqual(session.command, command)
                session.close()
                wrong_env = {key: value for key, value in env.items()
                             if key != translation.CLI_ENVIRONMENT_KEYS[provider]}
                with self.assertRaisesRegex(ProtocolError, "invalid_startup"):
                    configuration.startup_configuration(
                        ["--config-home", home, "--application-id", "synthetic", flag, command],
                        environment=wrong_env)
            codex.assert_not_called()
            claude.assert_not_called()

    def test_claude_snapshots_preserve_shared_modes_and_freeze_the_selected_model(self):
        for text in (shared.TEXT, "hello", "def greeting():\n    return 42"):
            for origin in ("text", "selection", "ocr"):
                config = claude_config()
                payload = shared.request(text=text, origin=origin)
                snapshot = translation.snapshot_for_translation(config, payload)
                codex = translation.snapshot_for_translation(Config(), payload)
                self.assertEqual(snapshot.selection.provider_id, CLAUDE_PROVIDER)
                self.assertEqual(snapshot.selection.model, "claude-custom")
                self.assertEqual(snapshot.request.user_text, text)
                self.assertEqual(snapshot.request.system_prompt, codex.request.system_prompt)
                self.assertEqual((snapshot.kind, snapshot.dictionary, snapshot.content_class),
                                 (codex.kind, codex.dictionary, codex.content_class))
                config[CFG.CLAUDE_MODEL] = "changed"
                self.assertEqual(snapshot.config[CFG.CLAUDE_MODEL], "claude-custom")

    def test_provider_model_and_prompt_revision_are_part_of_cache_identity(self):
        config = claude_config(**{CFG.CODEX_MODEL: "claude-custom"})
        snapshot = translation.snapshot_for_translation(config, shared.request())
        expected = provider_cache_signature(CLAUDE_PROVIDER, "claude-custom", "auto", False,
                                            "en_US", PROVIDER_PROMPT_REVISIONS[CLAUDE_PROVIDER])
        self.assertEqual(snapshot.sig, expected)
        config[CFG.MODEL_PROVIDER] = CODEX_PROVIDER
        self.assertNotEqual(translation.snapshot_for_translation(config, shared.request()).sig, snapshot.sig)
        config[CFG.MODEL_PROVIDER], config[CFG.CLAUDE_MODEL] = CLAUDE_PROVIDER, "different"
        self.assertNotEqual(translation.snapshot_for_translation(config, shared.request()).sig, snapshot.sig)

    def test_claude_summary_and_ocr_share_rules_without_codex_streaming_preference(self):
        prose = "This is synthetic prose with complete sentences for the summary contract. " * 8
        config = claude_config(**{CFG.SUMMARY_ENABLED: True, CFG.CODEX_STREAMING_EXPERIMENTAL: False})
        snapshot = translation.snapshot_for_translation(config, shared.request(text=prose))
        self.assertTrue(snapshot.summarize)
        self.assertTrue(snapshot.stream_enabled)
        self.assertEqual(snapshot.request.timeout_seconds, 90)
        self.assertEqual(snapshot.request.task, "translation_summary")
        self.assertEqual(snapshot.request.system_prompt, shared.codex_summary_instruction(snapshot.target_lang))
        ocr = translation.snapshot_for_translation(config, shared.request(text=prose, origin="ocr"))
        self.assertFalse(ocr.summarize)
        self.assertIn(shared.OCR_STRUCTURE_HINT, ocr.request.system_prompt)

    def test_all_six_result_actions_use_claude_model_without_cache_or_auto_summary(self):
        for action in translation.RESULT_ACTIONS:
            payload = shared.action_request(action)
            snapshot = translation.snapshot_for_result_action(claude_config(), payload)
            codex = translation.snapshot_for_result_action(Config(), payload)
            self.assertEqual(snapshot.selection.provider_id, CLAUDE_PROVIDER)
            self.assertEqual(snapshot.request.model, "claude-custom")
            self.assertEqual(snapshot.request.system_prompt, codex.request.system_prompt)
            self.assertEqual(snapshot.request.user_text, payload["text"])
            self.assertEqual(snapshot.sig, "")
            self.assertFalse(snapshot.summarize)

    def test_image_snapshot_binds_claude_and_carries_only_the_private_owned_path(self):
        path = str(Path.cwd() / "synthetic.png")
        payload = {"operation": "translate_image", "image_path": path, "image_bytes": len(PNG_BYTES),
                   "image_sha256": hashlib.sha256(PNG_BYTES).hexdigest(),
                   "app_language": "en_US", "record_history": False}
        private = str(Path.cwd() / "owned.png")
        snapshot = translation.snapshot_for_image(claude_config(), payload, private)
        self.assertEqual(snapshot.selection.provider_id, CLAUDE_PROVIDER)
        self.assertEqual(snapshot.request.image_paths, (private,))
        self.assertEqual(snapshot.request.task, "image")
        self.assertIsNone(snapshot.input)
        self.assertEqual(snapshot.kind, "ocr")

    def test_claude_validation_uses_its_model_not_an_unselected_codex_draft(self):
        config = claude_config(**{CFG.CODEX_MODEL: "x" * 1000})
        self.assertEqual(translation.snapshot_for_translation(config, shared.request()).request.model, "claude-custom")
        for model in ("", "x" * 257):
            config[CFG.CLAUDE_MODEL] = model
            with self.assertRaisesRegex(translation.TranslationError, "invalid_translation_settings"):
                translation.snapshot_for_translation(config, shared.request())

    def test_provider_errors_map_to_existing_safe_native_failure_categories(self):
        for raw, expected in (
            ("provider_protocol_error", "provider_protocol_error"),
            ("probe_invalid_utf8", "provider_protocol_error"),
            ("probe_output_limit", "translation_output_limit"),
            ("probe_input_limit", "translation_output_limit"),
            ("probe_timeout", "translation_timeout"),
            ("probe_failed", "provider_failed"),
            ("image_changed", "image_changed"),
            ("image_unavailable", "image_unavailable"),
            ("image_too_large", "image_too_large"),
            ("probe_cleanup_failed", "provider_cleanup_failed"),
        ):
            self.assertEqual(translation.provider_failure(raw), expected)
            self.assertIn(expected, translation.TRANSLATION_FAILURE_CODES)


class ClaudeTranslationServiceTests(shared._TranslationDirectory):
    bound_provider = CLAUDE_PROVIDER

    def test_ready_then_first_translation_records_claude_cache_and_reuses_it(self):
        self.assertEqual(self.stdout.events[0]["payload"]["backend"], "native_print")
        self.assertEqual(self.session.provider_id, CLAUDE_PROVIDER)
        self.translate("first")
        self.assertTrue(self.stdout.terminal("first"))
        first = self.stdout.result("first")
        self.assertEqual(first["type"], "completed")
        self.assertTrue(first["payload"]["submitted"])
        self.assertEqual(first["payload"]["history"], "recorded")
        self.assertEqual(self.provider.requests[0].model, "synthetic")
        self.assertEqual(len(self.history()), 1)
        self.assertTrue(self.history()[0]["sig"].startswith("claude_cli|synthetic|"))
        self.translate("cached")
        self.assertTrue(self.stdout.terminal("cached"))
        self.assertFalse(self.stdout.result("cached")["payload"]["submitted"])
        self.assertTrue(self.stdout.result("cached")["payload"]["cached"])
        self.assertEqual(len(self.provider.requests), 1)

    def test_provider_switch_requires_rebinding_before_cache_lookup_or_submission(self):
        self.config[CFG.MODEL_PROVIDER] = CODEX_PROVIDER
        self.session.perform({"operation": "config_save", "config": self.config})
        with patch.object(self.session._history, "find_cached", side_effect=AssertionError("Wrong provider cache")):
            self.translate()
            self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["payload"],
                         {"code": "unsupported_provider", "submitted": False})
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(self.history(), [])

    def test_inflight_model_and_provider_are_frozen_while_live_history_optout_is_respected(self):
        self.provider.release.clear()
        self.translate()
        try:
            self.assertTrue(self.provider.entered.wait(1))
            self.config.update({CFG.CLAUDE_MODEL: "edited", CFG.MODEL_PROVIDER: CODEX_PROVIDER,
                                CFG.HISTORY_ENABLED: False})
            self.session.perform({"operation": "config_save", "config": self.config})
        finally:
            self.provider.release.set()
        self.assertTrue(self.stdout.terminal("translate"))
        self.assertEqual(self.stdout.result("translate")["type"], "completed")
        self.assertEqual(self.stdout.result("translate")["payload"]["history"], "disabled")
        self.assertEqual(self.provider.requests[0].model, "synthetic")
        self.assertEqual(self.history(), [])

    def test_ocr_and_result_actions_use_claude_without_result_action_history(self):
        self.translate("ocr", origin="ocr", text=shared.OCR_TEXT)
        self.assertTrue(self.stdout.terminal("ocr"))
        self.assertEqual(self.provider.requests[0].user_text, shared.OCR_TEXT)
        before = self.history()
        for action in translation.RESULT_ACTIONS:
            self.server._handle(shared.message(action, "request", **shared.action_request(action)))
            self.assertTrue(self.stdout.terminal(action))
            self.assertEqual(self.stdout.result(action)["type"], "completed")
            self.assertEqual(self.provider.requests[-1].model, "synthetic")
        self.assertEqual(self.history(), before)

    def test_image_request_cleans_private_copy_before_committing_terminal(self):
        source = self.home / "capture.png"
        source.write_bytes(PNG_BYTES)
        self.server._handle(shared.message("image", "request", operation="translate_image",
            image_path=str(source), image_bytes=len(PNG_BYTES),
            image_sha256=hashlib.sha256(PNG_BYTES).hexdigest(), app_language="en_US", record_history=True))
        self.assertTrue(self.stdout.terminal("image"))
        self.assertEqual(self.stdout.result("image")["type"], "completed")
        captured = self.provider.requests[0]
        self.assertEqual(captured.task, "image")
        self.assertEqual(captured.model, "synthetic")
        self.assertNotEqual(captured.image_paths, (str(source),))
        self.assertFalse(Path(captured.image_paths[0]).exists())
        self.assertEqual(source.read_bytes(), PNG_BYTES)
        self.assertEqual(self.history()[0]["input"], None)
        self.assertTrue(self.history()[0]["sig"].startswith("claude_cli|synthetic|"))

    def test_catalog_unavailable_is_explicit_and_later_translation_remains_usable(self):
        self.provider.catalog_error = "model_catalog_unavailable"
        self.server._handle(shared.message("catalog", "request", operation="model_catalog"))
        self.assertTrue(self.stdout.terminal("catalog"))
        self.assertEqual(self.stdout.result("catalog")["payload"], {"code": "model_catalog_unavailable"})
        self.translate("later")
        self.assertTrue(self.stdout.terminal("later"))
        self.assertEqual(self.stdout.result("later")["type"], "completed")
        self.assertEqual(self.provider.catalog_calls, 1)

    def test_claude_failure_preserves_submission_and_does_not_record_partial_text(self):
        for index, code in enumerate(("provider_protocol_error", "probe_failed", "probe_output_limit")):
            self.provider.result_code = code
            identifier = "failure" + str(index)
            self.translate(identifier)
            self.assertTrue(self.stdout.terminal(identifier))
            self.assertEqual(self.stdout.result(identifier)["payload"],
                             {"code": translation.provider_failure(code), "submitted": True})
        self.assertEqual(self.history(), [])
        self.assertEqual(len(self.provider.requests), 3)

    def test_direct_mismatched_snapshot_is_not_accepted_even_with_cached_output(self):
        snapshot = translation.snapshot_for_translation(Config(), shared.request())
        with self.assertRaisesRegex(translation.TranslationError, "unsupported_provider"):
            self.session._execute(snapshot, "stale cache", False, threading.Event(), lambda _: None, lambda: True)
        self.assertEqual(self.provider.requests, [])
