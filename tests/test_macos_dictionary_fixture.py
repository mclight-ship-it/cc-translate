"""Offline acquisition-tool contracts, never download or install a dictionary."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cc_dictionary_artifact_core import ARTIFACT_SHA256, ARTIFACT_SIZE, ARTIFACT_URL
from tools.macos import bundle, dictionary_fixture as fixture


class DictionaryFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".dictionary-fixture-test-", dir=bundle.ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.asset = self.root / "fixture.sqlite3"

    def test_validation_uses_the_single_product_pin(self):
        with patch.object(bundle, "verified_asset") as verify:
            result = fixture.validate_asset(self.asset)
        verify.assert_called_once_with(self.asset, {"size": ARTIFACT_SIZE, "sha256": ARTIFACT_SHA256})
        self.assertEqual(result["url"], ARTIFACT_URL)
        self.assertEqual(result["sha256"], ARTIFACT_SHA256)
        self.assertIn("not product URLSession", result["scope"])

    def test_missing_and_invalid_local_assets_fail_without_network(self):
        with patch.object(fixture.urllib.request, "build_opener") as network:
            with self.assertRaises(bundle.BundleError):
                fixture.validate_asset(self.asset)
            self.asset.write_bytes(b"not the pinned database")
            with self.assertRaises(bundle.BundleError):
                fixture.validate_asset(self.asset)
        network.assert_not_called()

    def test_download_requires_explicit_consent(self):
        with redirect_stderr(io.StringIO()), patch.object(fixture, "download_asset") as download:
            for arguments in (["--download-to", str(self.asset)], ["--asset", str(self.asset), "--allow-download"]):
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                    fixture.main(arguments)
        download.assert_not_called()

    def test_existing_invalid_destination_is_preserved_and_never_redownloaded(self):
        self.asset.write_bytes(b"existing invalid fixture")
        with patch.object(fixture.urllib.request, "build_opener") as network:
            with self.assertRaises(bundle.BundleError):
                fixture.download_asset(self.asset)
        network.assert_not_called()
        self.assertEqual(self.asset.read_bytes(), b"existing invalid fixture")

    def test_truncated_network_fixture_cleans_only_owned_partial(self):
        neighbor = self.root / "unrelated"
        neighbor.write_bytes(b"keep")
        response = Mock(status=200, url=ARTIFACT_URL, headers={})
        response.read.side_effect = [b"truncated", b""]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(fixture.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(bundle.BundleError):
                fixture.download_asset(self.asset)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, ARTIFACT_URL)
        self.assertEqual(list(self.root.iterdir()), [neighbor])
        self.assertEqual(neighbor.read_bytes(), b"keep")

    def test_http_redirect_is_rejected_before_following(self):
        with self.assertRaises(bundle.BundleError):
            fixture.HTTPSRedirectHandler().redirect_request(
                None, None, 302, "redirect", {}, "http://example.invalid/fixture")

    def test_validation_failure_is_nonzero_and_not_success_shaped(self):
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            self.assertEqual(fixture.main(["--asset", str(self.asset)]), 1)
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
