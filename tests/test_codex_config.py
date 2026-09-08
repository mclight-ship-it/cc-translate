import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch

from cc_providers.base import ProviderRequest, ProviderResult
from cc_providers.codex_config import (
    CODEX_CONFIG_OVERRIDES, CodexConfigError, child_environment,
    integration_overrides, read_native_config, validate_home,
)
from cc_providers.codex_cli import CodexCliProvider
from cc_providers.codex_appserver import CodexAppServerTransport


class TestNativeConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.request = ProviderRequest("translate", "auto", "Translate.", "hello")

    def test_environment_home_precedence_and_invalid_explicit_home(self):
        for values, expected in (
                ({}, None),
                ({"CODEX_HOME": str(self.root)}, str(self.root)),
                ({"CODEX_HOME": str(self.root), "CC_TRANSLATE_CODEX_HOME": "missing"},
                 os.path.abspath("missing")),
                ({"CODEX_HOME": str(self.root), "CC_TRANSLATE_CODEX_HOME": " "},
                 str(self.root))):
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True):
                before = dict(os.environ)
                actual = child_environment()
                self.assertEqual(actual.get("CODEX_HOME"), expected)
                self.assertEqual(dict(os.environ), before)
        with self.assertRaisesRegex(CodexConfigError, "codex_home_invalid"):
            validate_home({"CODEX_HOME": str(self.root / "missing")})
        with self.assertRaisesRegex(CodexConfigError, "codex_home_invalid"):
            validate_home({"CODEX_HOME": ""})
        with self.assertRaisesRegex(CodexConfigError, "codex_home_config_missing"):
            validate_home({"CODEX_HOME": str(self.root),
                           "CC_TRANSLATE_CODEX_HOME": str(self.root)})

    def test_mcp_overrides_disable_every_name_without_dotted_key_ambiguity(self):
        names = ("node_repl", "a.b", 'quoted"key', "空 格", "slash\\key")
        overrides = integration_overrides({"mcp_servers": dict.fromkeys(names, {})})
        parsed = tomllib.loads(overrides[0])
        self.assertEqual(parsed, {"mcp_servers": {
            name: {"enabled": False} for name in names}})
        with self.assertRaises(CodexConfigError):
            integration_overrides({"mcp_servers": []})

    def test_safety_overrides_are_valid_toml_and_do_not_override_routing_or_auth(self):
        config = tomllib.loads("\n".join(CODEX_CONFIG_OVERRIDES))
        for key in ("model_provider", "model", "model_providers",
                    "model_catalog_json", "cli_auth_credentials_store"):
            self.assertNotIn(key, config)
        self.assertEqual(config["notify"], [])
        self.assertEqual(config["developer_instructions"], "")
        self.assertEqual(config["project_doc_max_bytes"], 0)
        self.assertFalse(config["skills"]["include_instructions"])
        self.assertFalse(config["orchestrator"]["mcp"]["enabled"])
        self.assertTrue(all(not value for value in config["features"].values()))
        self.assertTrue(all(not value for value in config["hooks"].values()))
        self.assertTrue(Path(config["model_instructions_file"]).is_file())

    def _process(self, responses):
        proc = Mock()
        proc.stdout = io.StringIO("\n".join(json.dumps(r) for r in responses) + "\n")
        proc.stdin = io.StringIO()
        proc.poll.return_value = None
        return proc

    def test_native_reader_initializes_and_reads_without_thread_or_auth(self):
        proc = self._process([
            {"id": 1, "result": {}},
            {"id": 2, "result": {"config": {"model_provider": "custom"}, "layers": []}},
        ])
        sent = []
        original_write = proc.stdin.write
        proc.stdin.write = lambda text: (sent.append(json.loads(text)), original_write(text))[1]
        with patch("cc_providers.codex_config.subprocess.Popen", return_value=proc) as popen:
            result = read_native_config("codex.exe", {}, str(self.root))
        self.assertEqual(result["config"]["model_provider"], "custom")
        self.assertEqual([m["method"] for m in sent], ["initialize", "config/read"])
        self.assertEqual(sent[1]["params"]["cwd"], str(self.root))
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.root))
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
        proc.kill.assert_called_once()

    def test_native_reader_fails_closed_and_does_not_leak_config_errors(self):
        for responses in (
                [], [{"id": 1, "error": {"message": "secret_config_value"}}],
                [{"id": 1, "result": {}}, {"id": 2, "result": None}]):
            with self.subTest(responses=responses):
                proc = self._process(responses)
                with patch("cc_providers.codex_config.subprocess.Popen", return_value=proc):
                    with self.assertRaises(CodexConfigError) as caught:
                        read_native_config("codex.exe", {}, str(self.root))
                self.assertNotIn("secret", str(caught.exception))
                proc.kill.assert_called_once()

    def test_config_failure_blocks_exec_stream_and_prewarm_before_submission(self):
        provider = CodexCliProvider("codex.exe", str(self.root))
        transport = CodexAppServerTransport("codex.exe", str(self.root), env={})
        with patch("cc_providers.codex_cli.read_native_config",
                   side_effect=CodexConfigError("config_invalid")), \
                patch("cc_providers.codex_appserver.read_native_config",
                      side_effect=CodexConfigError("config_invalid")), \
                patch("cc_providers.codex_appserver._supported_appserver_version",
                      return_value=True), \
                patch("cc_providers.codex_cli.subprocess.Popen") as popen:
            for result in (provider.complete(self.request),
                           transport.warm_up(self.request),
                           transport.stream(self.request, Mock())):
                self.assertFalse(result.ok)
                self.assertEqual(result.error_code, "config_invalid")
                self.assertFalse(dict(result.metrics).get("turn_submitted", False))
        popen.assert_not_called()
        provider.shutdown()
        transport.shutdown()

    def test_transport_commands_share_native_environment_and_restrictions(self):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root),
                                    "CC_TRANSLATE_CODEX_HOME": ""}):
            provider = CodexCliProvider("codex.exe", str(self.root))
        native = {"config": {"mcp_servers": {"node_repl": {"enabled": True}}}}
        transport = CodexAppServerTransport("codex.exe", str(self.root), env=provider.env)
        with patch("cc_providers.codex_cli.read_native_config", return_value=native), \
                patch("cc_providers.codex_appserver.read_native_config", return_value=native), \
                patch.object(provider._catalog, "overrides", return_value=()):
            for model in ("auto", "auto-fast", "gpt-5.4-mini", "other"):
                request = ProviderRequest("translate", model, "", "")
                for command in (provider.build_command(request), transport.build_command(request)):
                    self.assertNotIn("--ignore-user-config", command)
                    for override in CODEX_CONFIG_OVERRIDES + integration_overrides(native["config"]):
                        self.assertIn(override, command)
        self.assertEqual(provider.env["CODEX_HOME"], str(self.root))


class TestBackendDiagnostics(unittest.TestCase):
    def test_backend_auth_matrix_never_treats_custom_config_as_authenticated(self):
        cases = [
            ("openai", {}, True, "Logged in with ChatGPT", True, "chatgpt"),
            ("openai", {}, True, "Logged in using an API key", True, "api"),
            ("openai", {}, False, "", False, ""),
            ("enterprise", {"auth": {"command": "never-execute"}}, True, "", None, "command"),
            ("custom", {"env_key": "PROVIDER_KEY"}, True, "", None, "environment"),
            ("proxy", {"base_url": "http://localhost:23333"}, True, "", None, "provider"),
            ("custom", {"experimental_bearer_token": "secret"}, True, "", None, "provider"),
            ("custom", {"requires_openai_auth": True}, True, "ChatGPT", True, "chatgpt"),
            ("custom", {"requires_openai_auth": True, "env_key": "KEY"}, True, "", None, "environment"),
            ("openai", {"auth": {"command": "never-execute"}}, True, "", None, "command"),
        ]
        for backend, definition, login_ok, text, authenticated, method in cases:
            with self.subTest(backend=backend, definition=list(definition)):
                provider = CodexCliProvider("codex.exe", r"C:\unused")
                config = {"model_provider": backend, "model_providers": {backend: definition}}
                with patch("cc_providers.codex_cli.read_native_config",
                           return_value={"config": config}), \
                        patch.object(provider, "_probe", side_effect=[
                            ProviderResult(True, "codex-cli 0.146.0"),
                            ProviderResult(login_ok, text, error_code="" if login_ok else "login_required")
                        ]) as probe:
                    status = provider.diagnose()
                self.assertIs(status.authenticated, authenticated)
                self.assertEqual(status.auth_method, method)
                self.assertEqual(status.backend, backend)
                self.assertEqual(probe.call_count, 1 if authenticated is None else 2)
                self.assertNotIn("secret", repr(status))

    def test_invalid_config_diagnosis_does_not_query_an_unrelated_login(self):
        provider = CodexCliProvider("codex.exe", r"C:\unused")
        with patch("cc_providers.codex_cli.read_native_config",
                   side_effect=CodexConfigError("config_invalid")), \
                patch.object(provider, "_probe", return_value=ProviderResult(True, "0.146.0")) as probe:
            status = provider.diagnose()
        self.assertTrue(status.installed)
        self.assertFalse(status.authenticated)
        self.assertEqual(status.error_code, "config_invalid")
        self.assertEqual(probe.call_count, 1)


if __name__ == "__main__":
    unittest.main()
