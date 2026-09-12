import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from cc_providers import ProviderRequest
from cc_providers.codex_catalog import CodexModelCatalog
from cc_providers.codex_cli import CodexCliProvider
from cc_providers.codex_appserver import CodexAppServerTransport


class TestCodexCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / "codex.exe"
        self.binary.write_bytes(b"test binary")
        self.home = self.root / "home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.config.write_text(
            'model = "sol"\nmodel_provider = "custom"\n', encoding="utf-8")
        self.env = {"CODEX_HOME": str(self.home)}
        self.cache = self.root / "cache"
        self.log = Mock()
        self.manager = CodexModelCatalog(str(self.binary), self.env, self.cache, log_error=self.log)
        self.payload = {"models": [
            {"slug": "sol", "priority": 2, "base_instructions": "keep exactly"},
            {"slug": "mini", "priority": 9, "visibility": "hide"},
        ]}
        self.calls = []
        self.run = patch("cc_providers.codex_catalog.subprocess.run",
                         side_effect=self.fake_run).start()
        self.addCleanup(patch.stopall)
        patch("cc_providers.codex_catalog.Path.cwd", return_value=self.root).start()
        for module in ("codex_cli", "codex_appserver"):
            patch("cc_providers." + module + ".read_native_config",
                  return_value={"config": {}, "layers": []}).start()

    def fake_run(self, args, **kwargs):
        self.calls.append(args)
        self.assertEqual(kwargs["env"], self.env)
        self.assertTrue(kwargs["capture_output"])
        self.assertLessEqual(kwargs["timeout"], 8)
        if "--version" in args:
            output = b"codex-cli 0.146.0"
        else:
            output = json.dumps(self.payload).encode()
        return subprocess.CompletedProcess(args, 0, output, b"")

    def catalog_path(self, overrides):
        return Path(json.loads(overrides[0].split("=", 1)[1]))

    def test_exports_full_metadata_and_reuses_valid_cache(self):
        first = self.manager.overrides("auto-fast")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(json.loads(self.catalog_path(first).read_text()), self.payload)
        self.assertEqual(self.manager.overrides("mini"), first)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.manager.status, "ready")
        self.assertFalse(self.log.called)
        self.assertEqual(self.config.read_text(),
                         'model = "sol"\nmodel_provider = "custom"\n')

    def test_new_manager_validates_cached_catalog_with_local_cli(self):
        first = self.manager.overrides()
        other = CodexModelCatalog(str(self.binary), self.env, self.cache, log_error=self.log)
        self.assertEqual(other.overrides(), first)
        self.assertEqual(len(self.calls), 4)
        self.assertIn("-c", self.calls[-1])

    def test_corrupt_or_missing_file_is_rebuilt(self):
        path = self.catalog_path(self.manager.overrides())
        path.write_bytes(b"{bad")
        self.assertTrue(self.manager.overrides())
        self.assertEqual(json.loads(path.read_text()), self.payload)
        path.unlink()
        self.assertTrue(self.manager.overrides())
        self.assertEqual(len(self.calls), 9)

    def test_expired_catalog_refreshes_not_on_each_translation(self):
        path = self.catalog_path(self.manager.overrides()).parent / "state.json"
        state = json.loads(path.read_text())
        state["created_at"] -= 86401
        path.write_text(json.dumps(state))
        self.assertTrue(self.manager.overrides())
        self.assertEqual(len(self.calls), 6)

    def test_changed_config_or_executable_does_not_reuse_old_snapshot(self):
        old = self.manager.overrides()
        self.config.write_text('model = "mini"\nmodel_provider = "custom"\n')
        new = self.manager.overrides()
        self.assertNotEqual(old, new)
        self.binary.write_bytes(b"new executable")
        self.assertNotEqual(new, self.manager.overrides())

    def test_unsupported_version_uses_native_without_exporting(self):
        self.run.side_effect = None
        self.run.return_value = subprocess.CompletedProcess(
            [], 0, b"codex-cli 99.0.0", b"")
        self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(self.manager.status, "unsupported_catalog_version")
        self.assertEqual(self.run.call_count, 1)
        self.assertTrue(self.log.called)

    def test_unknown_model_is_not_silently_replaced(self):
        self.assertEqual(self.manager.overrides("new-model"), ())
        self.assertEqual(self.manager.status, "selected_model_not_in_catalog")
        self.assertFalse(list(self.cache.rglob("state.json")))

    def test_timeout_is_logged_and_not_retried_immediately(self):
        self.run.side_effect = subprocess.TimeoutExpired("codex", 8)
        self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.manager.status, "TimeoutExpired")
        self.assertTrue(self.log.called)

    def test_write_failure_leaves_off_override(self):
        with patch("cc_providers.codex_catalog._atomic_write",
                   side_effect=PermissionError("private path")):
            self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(self.manager.status, "PermissionError")
        self.assertNotIn("private path", str(self.log.call_args))

    def test_failed_refresh_does_not_reuse_expired_snapshot(self):
        path = self.catalog_path(self.manager.overrides())
        state_path = path.parent / "state.json"
        state = json.loads(state_path.read_text())
        state["created_at"] -= 86401
        state_path.write_text(json.dumps(state))
        self.run.side_effect = subprocess.TimeoutExpired("codex", 8)
        self.assertEqual(self.manager.overrides(), ())
        self.assertTrue(path.is_file())
        self.assertEqual(self.manager.status, "TimeoutExpired")

    def test_bad_cli_output_does_not_leak_error_body(self):
        self.run.side_effect = None
        self.run.return_value = subprocess.CompletedProcess(
            [], 1, b"secret stdout", b"secret stderr")
        self.assertEqual(self.manager.overrides(), ())
        self.assertNotIn("secret", str(self.log.call_args))

    def test_roundtrip_mismatch_never_activates_snapshot(self):
        self.run.side_effect = [
            subprocess.CompletedProcess([], 0, b"codex-cli 0.146.0", b""),
            subprocess.CompletedProcess([], 0, json.dumps(self.payload).encode(), b""),
            subprocess.CompletedProcess([], 0, b'{"models":[]}', b""),
        ]
        self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(self.manager.status, "catalog_roundtrip_mismatch")
        self.assertFalse(list(self.cache.rglob("state.json")))

    def test_user_owned_catalog_is_never_overridden_even_if_missing(self):
        self.config.write_text(
            'model_provider = "custom"\nmodel_catalog_json = "missing.json"\n')
        self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(self.manager.status, "user_owned")
        self.assertFalse(self.run.called)

    def test_official_provider_and_ignored_config_keep_native_behavior(self):
        self.assertEqual(self.manager.overrides(ignore_user_config=True), ())
        self.config.write_text('model = "sol"\n')
        self.assertEqual(self.manager.overrides(), ())
        self.assertFalse(self.run.called)

    def test_layered_and_invalid_config_are_not_guessed(self):
        self.config.write_text('model_provider = "custom"\nprofile = "work"\n')
        self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(self.manager.status, "layered_config_not_managed")
        self.config.write_text('model = [')
        self.assertEqual(self.manager.overrides(), ())
        self.assertTrue(self.log.called)

    def test_ordinary_project_trust_records_preserve_catalog(self):
        self.config.write_text(
            'model = "sol"\nmodel_provider = "custom"\n'
            '[projects."C:\\\\work"]\ntrust_level = "trusted"\n'
            '[projects."C:\\\\other"]\ntrust_level = "untrusted"\n')
        self.assertTrue(self.manager.overrides())
        self.assertEqual(self.manager.status, "ready")

    def test_unknown_project_records_and_profiles_still_use_native(self):
        for extra in (
                '[projects.work]\nmodel="mini"',
                '[projects.work]\ntrust_level="trusted"\nmodel_provider="other"',
                '[projects.work]\ntrust_level="future"',
                '[profiles.work]\nmodel="mini"'):
            with self.subTest(extra=extra):
                self.config.write_text('model_provider="custom"\n' + extra)
                self.assertEqual(self.manager.overrides(), ())
                self.assertEqual(self.manager.status, "layered_config_not_managed")

    def test_native_system_project_and_managed_routing_are_not_guessed(self):
        for layer in ("system", "project", "mdm", "unknown"):
            for key in ("model", "model_provider", "model_providers", "model_catalog_json"):
                with self.subTest(layer=layer, key=key):
                    native = {"layers": [{"name": {"type": layer},
                                          "config": {key: "private"}}]}
                    self.assertEqual(self.manager.overrides(native_config=native), ())
                    self.assertEqual(self.manager.status, "layered_config_not_managed")
        self.assertFalse(self.run.called)

    def test_unrelated_native_system_policy_does_not_change_catalog(self):
        native = {"layers": [{"name": {"type": "system"},
                              "config": {"features": {"hooks": False}}}]}
        self.assertTrue(self.manager.overrides(native_config=native))

    def test_explicit_opt_out(self):
        self.env["CC_TRANSLATE_CODEX_CATALOG"] = "off"
        self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(self.manager.status, "disabled")
        self.assertFalse(self.run.called)

    def test_project_config_and_changed_native_cache(self):
        initial = self.manager.overrides()
        (self.home / "models_cache.json").write_text('{"models":[]}')
        self.assertNotEqual(self.manager.overrides(), initial)
        (self.root / ".codex").mkdir()
        (self.root / ".codex" / "config.toml").write_text('model = "other"')
        self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(self.manager.status, "project_config_not_managed")

    def test_bad_catalog_entries_are_rejected(self):
        for models in ([], [{"slug": "x"}, {"slug": "x"}], [{"slug": 1}], [None]):
            with self.subTest(models=models):
                self.payload = {"models": models}
                self.manager._failure_until = 0
                self.assertEqual(self.manager.overrides(), ())

    def test_exec_and_stream_startup_preserve_route_and_safety(self):
        with patch.dict(os.environ, self.env, clear=True):
            provider = CodexCliProvider(
                str(self.binary), str(self.root),
                catalog_cache_dir=self.cache, catalog_log_error=self.log)
        manager = provider._catalog
        self.assertEqual(manager.cache_dir, self.cache)
        self.assertEqual(manager.work_dir, str(self.root))
        request = ProviderRequest(task="text", model="auto-fast",
                                  system_prompt="Translate.", user_text="hello")
        command = provider.build_command(request)
        self.assertNotIn("-m", command)
        self.assertNotIn("--ignore-user-config", command)
        self.assertIn("--ephemeral", command)
        self.assertIn('model_reasoning_effort="none"', command)
        override = manager.overrides()[0]
        self.assertIn(override, command)
        transport = CodexAppServerTransport(
            str(self.binary), str(self.root), catalog=manager)
        stream_command = transport.build_command(request)
        self.assertIn(override, stream_command)
        self.assertIn("features.shell_tool=false", stream_command)
        self.assertIn('model_reasoning_effort="none"', stream_command)

    def test_provider_warmup_and_stream_share_catalog_manager(self):
        with patch.dict(os.environ, {"CC_TRANSLATE_CODEX_HOME": str(self.home)}):
            provider = CodexCliProvider(str(self.binary), str(self.root))
        request = ProviderRequest(task="text", model="auto-fast",
                                  system_prompt="", user_text="")
        with patch("cc_providers.codex_appserver.CodexAppServerTransport") as cls:
            cls.return_value.ready_for.return_value = False
            provider._warm_appserver("auto-fast")
            provider.stream(request, Mock())
            self.assertEqual(cls.call_count, 1)
            self.assertIs(cls.call_args.kwargs["catalog"], provider._catalog)
            self.assertEqual(cls.return_value.warm_up.call_count, 1)
            self.assertEqual(cls.return_value.stream.call_count, 1)


if __name__ == "__main__":
    unittest.main()
