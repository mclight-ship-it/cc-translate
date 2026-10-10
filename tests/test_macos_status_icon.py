"""Offline image-generation tests; no AppKit, network, or model requests."""

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from tools.macos import bundle, make_status_icon


class StatusTemplateTests(unittest.TestCase):
    def test_checked_in_templates_are_reproducible_from_the_existing_smile(self):
        self.assertEqual(make_status_icon.SIZES, bundle.STATUS_ICON_SIZES)
        self.assertEqual(make_status_icon.OUTPUT, bundle.STATUS_ICON_SOURCE)
        before = make_status_icon.SOURCE.read_bytes()
        for name, generated in make_status_icon.template_images().items():
            with self.subTest(name=name), Image.open(make_status_icon.OUTPUT / name) as saved:
                self.assertEqual(saved.mode, "RGBA")
                self.assertEqual(saved.size, generated.size)
                self.assertEqual(saved.tobytes(), generated.tobytes())
                self.assertEqual(saved.info, {}, "Do not embed source paths or metadata.")
        self.assertEqual(make_status_icon.SOURCE.read_bytes(), before)

    def test_alpha_contains_three_separate_glyphs_and_no_background_tile(self):
        for name, pixels in make_status_icon.SIZES.items():
            with self.subTest(name=name), Image.open(make_status_icon.OUTPUT / name) as image:
                self.assertEqual(image.size, (pixels, pixels))
                rgba = image.load()
                foreground = set()
                coverage = 0
                for y in range(pixels):
                    for x in range(pixels):
                        red, green, blue, alpha = rgba[x, y]
                        self.assertEqual((red, green, blue), (0, 0, 0))
                        if x in (0, pixels - 1) or y in (0, pixels - 1):
                            self.assertEqual(alpha, 0)
                        coverage += alpha
                        if alpha >= 128:
                            foreground.add((x, y))
                self.assertGreater(coverage / (255 * pixels * pixels), 0.15)
                self.assertLess(coverage / (255 * pixels * pixels), 0.4)
                components = 0
                while foreground:
                    pending = [foreground.pop()]
                    components += 1
                    while pending:
                        x, y = pending.pop()
                        for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                            if neighbor in foreground:
                                foreground.remove(neighbor)
                                pending.append(neighbor)
                self.assertEqual(components, 3, "Retain two Cs and one smile, not a solid tile.")

    def test_empty_artwork_fails_instead_of_creating_an_invisible_menu_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "empty.png"
            Image.new("RGBA", (256, 256)).save(source)
            with self.assertRaisesRegex(ValueError, "no foreground"):
                make_status_icon.template_images(source)


if __name__ == "__main__":
    unittest.main()
