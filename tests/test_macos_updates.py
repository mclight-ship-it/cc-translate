"""Offline checks for the native updater's bundled default behavior."""

from pathlib import Path
import plistlib
import unittest


class MacUpdateDefaultsTests(unittest.TestCase):
    def test_template_keeps_checks_downloads_and_profile_opt_in_with_no_fake_channel(self):
        root = Path(__file__).resolve().parents[1]
        info = plistlib.loads((root / "macos/Resources/Info.plist").read_bytes())
        for key in ("SUEnableAutomaticChecks", "SUAutomaticallyUpdate", "SUSendProfileInfo"):
            self.assertIs(info[key], False, key)
        self.assertIs(info["SUVerifyUpdateBeforeExtraction"], True)
        self.assertNotIn("SUFeedURL", info)
        self.assertNotIn("SUPublicEDKey", info)
