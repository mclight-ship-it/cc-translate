import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

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

    def test_probe_only_runs_version_with_no_ambient_environment(self):
        self.assertIn('[command, "--version"]', smoke.PROBE)
        self.assertNotIn("os.environ", smoke.PROBE)
        self.assertNotIn("login", smoke.PROBE)
        self.assertNotIn("turn/start", smoke.PROBE)
        self.assertIn("darwin_process.capture_output", smoke.PROBE)
