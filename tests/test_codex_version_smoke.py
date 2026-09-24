import hashlib
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

from cc_providers import codex_darwin
from cc_providers.base import ProviderResult

from tools.macos import codex_version_smoke as smoke


class TestOfficialVersionSmoke(unittest.TestCase):
    def test_assets_are_exact_pinned_stable_releases(self):
        self.assertEqual([version for version, _ in smoke.ASSETS], ["0.146.0", "0.154.0"])
        for _, digest in smoke.ASSETS:
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_download_requires_exact_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "archive"
            with patch.object(smoke.urllib.request, "urlopen", return_value=io.BytesIO(b"synthetic")):
                smoke.download("0.154.0", hashlib.sha256(b"synthetic").hexdigest(), target)
            self.assertEqual(target.read_bytes(), b"synthetic")
            with patch.object(smoke.urllib.request, "urlopen", return_value=io.BytesIO(b"changed")):
                with self.assertRaisesRegex(ValueError, "hash_mismatch"):
                    smoke.download("0.154.0", "0" * 64, Path(directory) / "bad")

    def test_archive_only_writes_the_selected_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, target = Path(directory) / "archive", Path(directory) / "binary"
            with tarfile.open(archive, "w:gz") as bundle:
                member = tarfile.TarInfo(smoke.BINARY_NAME)
                member.size = 9
                bundle.addfile(member, io.BytesIO(b"synthetic"))
                unrelated = tarfile.TarInfo("../must-not-extract")
                unrelated.size = 7
                bundle.addfile(unrelated, io.BytesIO(b"ignored"))
            smoke.unpack_binary(archive, target)
            self.assertEqual(target.read_bytes(), b"synthetic")
            self.assertEqual({path.name for path in Path(directory).iterdir()}, {"archive", "binary"})

    def test_symbolic_link_cannot_be_used_as_the_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive"
            with tarfile.open(archive, "w:gz") as bundle:
                member = tarfile.TarInfo(smoke.BINARY_NAME)
                member.type = tarfile.SYMTYPE
                member.linkname = "/private/never"
                bundle.addfile(member)
            with self.assertRaisesRegex(ValueError, "invalid_official_cli_archive"):
                smoke.unpack_binary(archive, Path(directory) / "binary")

    def test_probe_runs_version_and_guarded_prewarm_with_no_ambient_environment(self):
        self.assertIn('[command, "--version"]', smoke.PROBE)
        self.assertNotIn("os.environ", smoke.PROBE)
        self.assertNotIn("login", smoke.PROBE)
        self.assertNotIn("turn/start", smoke.PROBE)
        self.assertIn("darwin_process.capture_output", smoke.PROBE)
        self.assertIn("provider.warm_up(None)", smoke.PROBE)

    def run_probe(self, provider):
        with tempfile.TemporaryDirectory() as directory:
            core = Path(codex_darwin.__file__).resolve().parent.parent
            command = str(Path(directory) / "synthetic-cli")
            args = ["synthetic-probe", str(core), command, directory, "0.154.0"]
            output = io.StringIO()
            with patch.object(sys, "argv", args), patch.object(sys, "path", list(sys.path)), \
                    patch.object(codex_darwin, "DarwinCodexProvider", return_value=provider) as factory, \
                    patch("cc_providers.darwin_process.capture_output", return_value=b"codex-cli 0.154.0"), \
                    redirect_stdout(output):
                exec(smoke.PROBE, {"__name__": "synthetic_probe"})
            self.assertEqual(factory.call_args.kwargs["environment"], {
                "HOME": directory, "CODEX_HOME": str(Path(directory) / ".codex"),
                "TMPDIR": directory, "PATH": "/usr/bin:/bin",
            })
            return json.loads(output.getvalue())

    def test_prewarm_report_requires_exact_threadless_requests_and_shutdown(self):
        provider = Mock()
        calls = []
        def warm(_model):
            if not calls:
                for method in ("initialize", "initialized", "hooks/list"):
                    provider._transport._send(None, method)
            hit = int(bool(calls))
            calls.append(True)
            return ProviderResult(True, metrics=(("turn_submitted", False), ("version_cache_hit", hit),
                                                 ("version_check_ms", 0 if hit else 200)))
        provider.warm_up.side_effect = warm
        result = self.run_probe(provider)
        timing = result.pop("timing")
        self.assertEqual(timing["reused_version_cache_hit"], 1)
        self.assertEqual(timing["cold_version_check_ms"], 200)
        self.assertEqual(timing["reused_version_check_ms"], 0)
        self.assertEqual(provider.warm_up.call_count, 2)
        self.assertEqual(result, {
            "version": "0.154.0", "meets_minimum": True, "native_prewarm": "passed",
            "turn_submitted": False, "requests": ["initialize", "initialized", "hooks/list"],
        })
        provider.shutdown.assert_called_once_with()

    def test_probe_blocks_thread_turn_or_any_unexpected_request_before_write(self):
        for method in ("thread/start", "turn/start", "account/read", "synthetic/unknown"):
            with self.subTest(method=method):
                provider = Mock()
                original_send = provider._transport._send
                provider.warm_up.side_effect = lambda _model: provider._transport._send(None, method)
                with self.assertRaisesRegex(RuntimeError, "non_preflight_request"):
                    self.run_probe(provider)
                original_send.assert_not_called()
                provider.shutdown.assert_called_once_with()

    def test_prewarm_failure_never_becomes_a_successful_report(self):
        provider = Mock()
        provider.warm_up.return_value = ProviderResult(False, error_code="invalid_appserver_message")
        with self.assertRaisesRegex(RuntimeError, "^official_preflight_failed:invalid_appserver_message$"):
            self.run_probe(provider)
        provider.shutdown.assert_called_once_with()
