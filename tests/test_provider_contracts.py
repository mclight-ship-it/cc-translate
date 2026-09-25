"""Portable provider contracts and registry; no real backends are loaded."""

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cc_providers
from cc_providers import base
from cc_providers.registry import ProviderRegistry


class TestPortableProviderContracts(unittest.TestCase):
    def test_package_exports_the_existing_contract_objects(self):
        for name in (
                "CLAUDE_PROVIDER", "CODEX_PROVIDER", "PROVIDER_IDS", "ModelProvider",
                "ProviderCapabilities", "ProviderRequest", "ProviderResult",
                "ProviderSelection", "ProviderStatus"):
            self.assertIs(getattr(cc_providers, name), getattr(base, name))
        self.assertIs(cc_providers.ProviderRegistry, ProviderRegistry)

    def test_request_is_frozen_and_does_not_normalize_user_content(self):
        request = base.ProviderRequest("translate", None, "fixed prompt", "  synthetic\n")
        self.assertEqual(request.user_text, "  synthetic\n")
        self.assertEqual(request.image_paths, ())
        self.assertEqual(request.timeout_seconds, 60.0)
        with self.assertRaises(FrozenInstanceError):
            request.user_text = "changed"

    def test_replacing_request_does_not_change_original_snapshot(self):
        request = base.ProviderRequest("translate", "model-a", "prompt", "synthetic")
        updated = replace(request, model="model-b", timeout_seconds=20)
        self.assertEqual(request.model, "model-a")
        self.assertEqual(request.timeout_seconds, 60.0)
        self.assertEqual(updated.user_text, request.user_text)
        self.assertEqual(updated.model, "model-b")
        self.assertEqual(updated.timeout_seconds, 20)

    def test_unknown_authentication_is_not_false_or_true(self):
        unknown = base.ProviderStatus(installed=True, authenticated=None)
        self.assertIsNone(unknown.authenticated)
        self.assertIsNone(unknown.command)
        self.assertNotEqual(unknown, replace(unknown, authenticated=False))
        self.assertNotEqual(unknown, replace(unknown, authenticated=True))

    def test_result_failure_and_metrics_remain_explicit(self):
        result = base.ProviderResult(
            False, error_code="synthetic_failure", metrics=(("wall_ms", 1),))
        self.assertFalse(result.ok)
        self.assertEqual(result.text, "")
        self.assertEqual(result.error_code, "synthetic_failure")
        self.assertEqual(result.metrics, (("wall_ms", 1),))
        self.assertIsNone(result.model_info)

    def test_optional_model_info_preserves_positional_result_and_frozen_contract(self):
        legacy = base.ProviderResult(True, "text", "", "", (("total_ms", 7),))
        info = base.ProviderModelInfo("auto-fast", "gpt-5.4-mini", "low")
        updated = replace(legacy, model_info=info)
        self.assertEqual(updated.metrics, legacy.metrics)
        self.assertIsNone(legacy.model_info)
        self.assertEqual(updated.model_info, info)
        with self.assertRaises(FrozenInstanceError):
            info.resolved_model = "changed"

    def test_model_info_omits_untrusted_values_without_truncating_or_normalizing(self):
        for value in (None, True, 12, [], {}, "", "x" * 129, " synthetic ", "\ud800",
                      "model\nSYNTHETIC_PRIVATE", "model\0secret", "你好", "../private",
                      "/Users/private", r"C:\Users\private", "https://host/secret",
                      "Bearer SYNTHETIC_PRIVATE", "sk-SYNTHETIC_PRIVATE",
                      "ghp_SYNTHETIC_PRIVATE", "eyJhbGci.SYNTHETIC_PRIVATE.signature",
                      '{"model":"synthetic","token":"SYNTHETIC_PRIVATE"}'):
            with self.subTest(value=value):
                info = base.ProviderModelInfo(value, value, value)
                self.assertEqual(info, base.ProviderModelInfo())
                self.assertNotIn("SYNTHETIC_PRIVATE", repr(info))
        for value in ("auto-fast", "gpt-5.4-mini", "provider/Exact-ID:2026", "x" * 128):
            self.assertEqual(base.ProviderModelInfo(value).requested_model, value)
        for value in ("auto", "auto-fast"):
            self.assertIsNone(base.ProviderModelInfo("auto", value).resolved_model)

    def test_reasoning_effort_accepts_only_fixed_known_enum_values(self):
        for effort in base.MODEL_REASONING_EFFORTS:
            self.assertEqual(base.ProviderModelInfo(reasoning_effort=effort).reasoning_effort, effort)
        for effort in ("LOW", "disabled", "future", "low private", True, [], {}):
            self.assertIsNone(base.ProviderModelInfo(reasoning_effort=effort).reasoning_effort)

    def test_registry_keeps_existing_identity_and_errors(self):
        registry = ProviderRegistry()
        first = SimpleNamespace(provider_id="synthetic", shutdown=Mock())
        second = SimpleNamespace(provider_id="synthetic", shutdown=Mock())
        self.assertIs(registry.register(first), first)
        self.assertIs(registry.get("synthetic"), first)
        registry.register(second)
        self.assertEqual(registry.ids(), ("synthetic",))
        self.assertIs(registry.get("synthetic"), second)
        with self.assertRaisesRegex(KeyError, "Unknown model provider"):
            registry.get("missing")
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            registry.register(SimpleNamespace(provider_id=""))

    def test_registry_shutdown_reports_failures_after_cleaning_all_providers(self):
        registry = ProviderRegistry()
        failed = SimpleNamespace(provider_id="failed", shutdown=Mock(side_effect=OSError("synthetic")))
        healthy = SimpleNamespace(provider_id="healthy", shutdown=Mock())
        registry.register(failed)
        registry.register(healthy)
        with self.assertRaisesRegex(RuntimeError, "1 model provider") as error:
            registry.shutdown()
        self.assertIsInstance(error.exception.__cause__, OSError)
        failed.shutdown.assert_called_once_with()
        healthy.shutdown.assert_called_once_with()
