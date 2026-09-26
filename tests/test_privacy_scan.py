import importlib.util
import io
from pathlib import Path
import unittest
from unittest import mock


_PATH = Path(__file__).resolve().parents[1] / ".githooks" / "privacy_scan.py"
_SPEC = importlib.util.spec_from_file_location("privacy_scan", _PATH)
privacy_scan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(privacy_scan)


class TestPrivacyScan(unittest.TestCase):
    def test_new_branch_uses_advertised_remote_common_ancestor(self):
        published = "1" * 40
        ancestor = "2" * 40
        with mock.patch.object(privacy_scan, "_git",
                               side_effect=[published + "\tHEAD\n", ancestor + "\n"]) as git:
            self.assertEqual(privacy_scan.published_base("origin", "local-head"), ancestor)
        self.assertEqual(git.call_args_list, [
            mock.call("ls-remote", "--", "origin", "HEAD"),
            mock.call("merge-base", "local-head", published),
        ])

    def test_empty_remote_still_scans_whole_tree(self):
        with mock.patch.object(privacy_scan, "_git", side_effect=["", "empty-tree\n"]) as git:
            self.assertEqual(privacy_scan.published_base("origin", "local-head"), "empty-tree")
        git.assert_called_with("hash-object", "-t", "tree", "--stdin")

    def test_invalid_remote_advertisement_is_not_a_scan_exemption(self):
        for advertisement in ("invalid HEAD\n", "1" * 40 + " other\n",
                              ("1" * 40 + " HEAD\n") * 2):
            with mock.patch.object(privacy_scan, "_git", return_value=advertisement):
                with self.assertRaises(RuntimeError):
                    privacy_scan.published_base("origin", "local-head")

    def test_missing_remote_ancestor_blocks_push(self):
        with mock.patch.object(privacy_scan, "_git", side_effect=[
            "1" * 40 + "\tHEAD\n", RuntimeError("Fetch the published commit first")
        ]), mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(privacy_scan.main(
                ["privacy_scan.py", "--new-branch-base", "origin", "local-head"]), 2)

    def test_remote_lookup_failure_does_not_fall_back_to_local_refs(self):
        with mock.patch.object(privacy_scan, "_git", side_effect=RuntimeError("lookup failed")) as git:
            with self.assertRaises(RuntimeError):
                privacy_scan.published_base("origin", "local-head")
        self.assertEqual(git.call_count, 1)

    def test_blocks_current_home_path(self):
        home = Path("C:" + "\\Users\\" + "LocalOwner")
        line = "output=" + str(home / "project" / "result.txt")
        self.assertIn(
            "current user's home path",
            privacy_scan.scan_line("notes.txt", line, home=home),
        )

    def test_blocks_non_placeholder_user_path(self):
        line = "Set-Location 'C:" + "\\Users\\" + "Alice\\project'"
        self.assertIn(
            "machine-specific user path",
            privacy_scan.scan_line("notes.txt", line, home=Path("Z:/none")),
        )

    def test_allows_synthetic_user_path(self):
        line = "open C:" + "\\Users\\" + "person\\fixture.txt"
        self.assertEqual(
            privacy_scan.scan_line("test.py", line, home=Path("Z:/none")),
            [],
        )

    def test_blocks_common_token_format(self):
        line = "TOKEN = '" + "ghp_" + ("A" * 36) + "'"
        self.assertIn(
            "GitHub token",
            privacy_scan.scan_line("config.py", line, home=Path("Z:/none")),
        )

    def test_blocks_non_example_email(self):
        line = "owner = 'alice" + "@contoso.com'"
        self.assertIn(
            "email address or account identifier",
            privacy_scan.scan_line("notes.md", line, home=Path("Z:/none")),
        )

    def test_allows_documentation_placeholders(self):
        lines = [
            "email=user" + "@example.com",
            "api_key=YOUR_API_KEY_PLACEHOLDER",
            "auth_token=secret_config_value",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(
                    privacy_scan.scan_line(
                        "example.md", line, home=Path("Z:/none")),
                    [],
                )

    def test_sensitive_local_files_are_blocked(self):
        for path in (
            ".env",
            "config.json",
            "history.json",
            "auth.json",
            "keys/private.pem",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(
                    privacy_scan._sensitive_path_reason(path))

    def test_example_env_file_is_allowed(self):
        self.assertIsNone(
            privacy_scan._sensitive_path_reason(".env.example"))

    def test_scan_range_combines_path_and_content_findings(self):
        with (
            mock.patch.object(
                privacy_scan, "_changed_paths",
                return_value=["history.json", "README.md"],
            ),
            mock.patch.object(
                privacy_scan, "_added_lines",
                return_value=[
                    ("README.md", "contact=bob" + "@contoso.com"),
                ],
            ),
        ):
            findings = privacy_scan.scan_range("base", "head")
        self.assertEqual(len(findings), 2)
        self.assertTrue(any("history.json" in item for item in findings))
        self.assertTrue(any("email address" in item for item in findings))


if __name__ == "__main__":
    unittest.main()
