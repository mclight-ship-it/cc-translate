"""Tests for cc_ui_v2 — the v2 UI rendering foundation.

These exercise the PURE rendering core offscreen (no display needed), mirroring
how the standalone POC self-checked. The most important guarantee under test is
GradientBackground's crop stability: the streaming result popup grows downward
~20x/second, and the anti-flicker/perf strategy is "bake once, crop per frame".
If a downward-growth crop ever differed from the previous frame's overlapping
region, the popup would shimmer — so that invariant is pinned here.

Every rendering test is skipped (not failed) when Pillow is unavailable, since
PIL is an optional app dependency and v2 degrades to the legacy UI without it.
"""
import unittest

import cc_ui_v2 as v2

requires_pil = unittest.skipUnless(v2.PIL_OK, "Pillow not available")


class TestPalette(unittest.TestCase):
    def test_brand_gradient_keeps_all_three_stops(self):
        # Dropping the pink stop killed the vibe in the POC; guard it.
        self.assertEqual(len(v2.BRAND), 3)
        positions = [p for p, _ in v2.BRAND]
        self.assertEqual(positions, [0.0, 0.5, 1.0])
        self.assertEqual(v2.BRAND[-1][1], (255, 122, 198))  # pink endpoint

    def test_dark_and_light_share_required_keys(self):
        required = {"is_dark", "solid", "glow", "glow_hi", "glow_lo",
                    "glass_tint", "glass_border", "border", "fg", "sub",
                    "hint", "btn", "btn_brd", "field", "panel", "ok", "err"}
        for name in ("dark", "light"):
            pal = v2.get_palette(name)
            self.assertTrue(required.issubset(pal),
                            f"{name} palette missing {required - set(pal)}")
        self.assertTrue(v2.get_palette("dark")["is_dark"])
        self.assertFalse(v2.get_palette("light")["is_dark"])

    def test_get_palette_defaults_to_dark(self):
        self.assertEqual(v2.get_palette("nonsense"), v2.get_palette("dark"))

    def test_navy_base_is_not_grey(self):
        # The card base must be deep navy (blue dominant), not the app's greyish
        # real popup colour — that greyed out the vibe in an earlier POC.
        top = v2.get_palette("dark")["solid"][0][1]
        r, g, b = top
        self.assertGreater(b, r, "navy base should be blue-dominant")
        self.assertGreater(b, g, "navy base should be blue-dominant")

    def test_hex_to_rgb(self):
        self.assertEqual(v2.hex_to_rgb("#eef1ff"), (238, 241, 255))
        self.assertEqual(v2.hex_to_rgb("000000"), (0, 0, 0))


class TestScaling(unittest.TestCase):
    def test_scaled_rounds_to_int(self):
        self.assertEqual(v2.scaled(10, 1.0), 10)
        self.assertEqual(v2.scaled(10, 1.5), 15)
        self.assertIsInstance(v2.scaled(9, 1.5), int)


class TestGradientMath(unittest.TestCase):
    def test_sample_stops_endpoints_and_midpoint(self):
        self.assertEqual(v2.sample_stops(v2.BRAND, 0.0), (110, 168, 255))
        self.assertEqual(v2.sample_stops(v2.BRAND, 1.0), (255, 122, 198))
        self.assertEqual(v2.sample_stops(v2.BRAND, 0.5), (161, 121, 255))

    def test_sample_stops_clamps_out_of_range(self):
        self.assertEqual(v2.sample_stops(v2.BRAND, -1.0), (110, 168, 255))
        self.assertEqual(v2.sample_stops(v2.BRAND, 2.0), (255, 122, 198))


@requires_pil
class TestPrimitives(unittest.TestCase):
    def test_linear_gradient_size_and_mode(self):
        img = v2.linear_gradient(40, 20, v2.BRAND)
        self.assertEqual(img.size, (40, 20))
        self.assertEqual(img.mode, "RGBA")

    def test_linear_gradient_survives_tiny_size(self):
        img = v2.linear_gradient(0, 0, v2.BRAND)  # clamped to >=1
        self.assertEqual(img.size, (1, 1))

    def test_radial_glow_is_transparent_at_corner_opaque_center(self):
        g = v2.radial_glow(60, (110, 140, 255), 1.0)
        self.assertEqual(g.mode, "RGBA")
        corner = g.getpixel((0, 0))[3]
        center = g.getpixel((30, 30))[3]
        self.assertLess(corner, 40)             # corner ~clear (soft blur bleed)
        self.assertGreater(center, corner)      # centre far brighter than corner
        self.assertGreater(center, 0)

    def test_rounded_mask_corner_clear_center_set(self):
        m = v2.rounded_mask(40, 40, 12)
        self.assertEqual(m.getpixel((0, 0)), 0)             # corner cut
        self.assertEqual(m.getpixel((20, 20)), 255)         # centre filled

    def test_gradient_text_produces_nonempty_image(self):
        img = v2.gradient_text("译文", v2.load_font("bold", 16, 1.0))
        self.assertEqual(img.mode, "RGBA")
        self.assertGreater(img.size[0], 0)
        self.assertGreater(img.getchannel("A").getextrema()[1], 0)  # some ink

    def test_gradient_round_applies_rounded_alpha(self):
        img = v2.gradient_round(40, 40, 12)
        self.assertEqual(img.getpixel((0, 0))[3], 0)        # rounded corner clear


@requires_pil
class TestCardBaking(unittest.TestCase):
    def test_bake_face_is_fully_opaque(self):
        pal = v2.get_palette("dark")
        face = v2.bake_face(120, 80, pal, scale=1.0)
        self.assertEqual(face.size, (120, 80))
        # Interior must be opaque (readable body sits on it).
        self.assertEqual(face.getchannel("A").getextrema()[0], 255)

    def test_bake_card_returns_padded_canvas(self):
        pal = v2.get_palette("dark")
        img, pad = v2.bake_card(200, 120, pal, scale=1.0, radius=18)
        self.assertGreater(pad, 0)
        # Canvas includes the glow-bleed pad on all four sides.
        self.assertEqual(img.size, (200 + pad * 2, 120 + pad * 2))
        # Outer corner is transparent (glow bleed / rounded).
        self.assertEqual(img.getpixel((0, 0))[3], 0)

    def test_bake_card_scales_pad_with_dpi(self):
        pal = v2.get_palette("dark")
        _, pad1 = v2.bake_card(200, 120, pal, scale=1.0)
        _, pad2 = v2.bake_card(200, 120, pal, scale=2.0)
        self.assertGreater(pad2, pad1)

    def test_glass_hook_uses_provided_background(self):
        pal = v2.get_palette("dark")
        from PIL import Image
        bg = Image.new("RGBA", (120, 80), (200, 30, 30, 255))
        face = v2.bake_face(120, 80, pal, glass_bg=bg, scale=1.0)
        self.assertEqual(face.size, (120, 80))


@requires_pil
class TestGradientBackgroundCrops(unittest.TestCase):
    """The streaming perf/anti-flicker core: bake once, crop per frame."""

    def test_downward_growth_reuses_overlapping_pixels(self):
        # A shorter frame's pixels must be identical to the taller frame's top
        # region — otherwise the popup shimmers as it grows during streaming.
        gb = v2.GradientBackground(v2.get_palette("dark"), scale=1.0)
        small = gb.face(300, 80)
        big = gb.face(300, 200)
        self.assertEqual(small.size, (300, 80))
        self.assertEqual(big.size, (300, 200))
        self.assertEqual(
            small.tobytes(),
            big.crop((0, 0, 300, 80)).tobytes(),
            "downward growth changed already-drawn pixels (would flicker)")

    def test_stable_even_across_reserve_exceeding_rebake(self):
        # The strong guarantee: even when the card grows PAST the baked reserve
        # and forces a re-bake at a taller size, the top region is byte-identical
        # (bake_stream_face rows are a pure function of y). Use a tiny reserve to
        # force the re-bake deterministically.
        gb = v2.GradientBackground(v2.get_palette("dark"), scale=1.0,
                                   reserve_pts=100)
        small = gb.face(300, 80)                 # baked at reserve 100
        tall = gb.face(300, 260)                 # exceeds 100 -> re-bakes taller
        self.assertEqual(
            small.tobytes(),
            tall.crop((0, 0, 300, 80)).tobytes(),
            "re-bake past reserve shifted the top region (would flicker)")

    def test_shrink_then_grow_is_stable(self):
        gb = v2.GradientBackground(v2.get_palette("dark"), scale=1.0)
        a = gb.face(300, 150)
        gb.face(300, 60)          # a shorter frame in between
        b = gb.face(300, 150)
        self.assertEqual(a.tobytes(), b.tobytes())

    def test_width_change_rebakes(self):
        gb = v2.GradientBackground(v2.get_palette("dark"), scale=1.0)
        f1 = gb.face(300, 80)
        f2 = gb.face(360, 80)
        self.assertEqual(f1.size, (300, 80))
        self.assertEqual(f2.size, (360, 80))

    def test_rounded_face_cuts_corner(self):
        gb = v2.GradientBackground(v2.get_palette("dark"), scale=1.0)
        f = gb.rounded_face(200, 120, 18)
        self.assertEqual(f.getpixel((0, 0))[3], 0)          # corner transparent

    def test_face_is_opaque_interior(self):
        gb = v2.GradientBackground(v2.get_palette("dark"), scale=1.0)
        f = gb.face(200, 120)
        self.assertEqual(f.getpixel((100, 60))[3], 255)

    def test_invalidate_forces_rebake(self):
        gb = v2.GradientBackground(v2.get_palette("dark"), scale=1.0)
        gb.face(300, 80)
        self.assertIsNotNone(gb._cache)
        gb.invalidate()
        self.assertIsNone(gb._cache)


@requires_pil
class TestWidgets(unittest.TestCase):
    def test_icon_tile(self):
        img = v2.icon_tile(v2.scaled(34, 1.0), "CC", 1.0)
        self.assertEqual(img.mode, "RGBA")
        self.assertGreater(img.size[0], 0)

    def test_gradient_pill_plain_and_gradient(self):
        pal = v2.get_palette("dark")
        f = v2.load_font("bold", 11, 1.0)
        plain = v2.gradient_pill("复制", f, pal, scale=1.0)
        grad = v2.gradient_pill("翻译", f, pal, grad=True, scale=1.0)
        self.assertGreater(plain.size[0], 0)
        self.assertGreater(grad.size[0], 0)

    def test_gradient_pill_caret_widens_it(self):
        pal = v2.get_palette("dark")
        f = v2.load_font("bold", 11, 1.0)
        plain = v2.gradient_pill("操作", f, pal, scale=1.0)
        with_caret = v2.gradient_pill("操作", f, pal, scale=1.0, caret=True)
        self.assertGreater(with_caret.size[0], plain.size[0])

    def test_gradient_pill_dot_widens_it(self):
        pal = v2.get_palette("dark")
        f = v2.load_font("reg", 10, 1.0)
        plain = v2.gradient_pill("生成中", f, pal, scale=1.0)
        with_dot = v2.gradient_pill("生成中", f, pal, scale=1.0, dot=(110, 231, 168))
        self.assertGreater(with_dot.size[0], plain.size[0])

    def test_status_dot(self):
        img = v2.status_dot((110, 231, 168), 8, 1.0)
        self.assertEqual(img.getpixel((4, 4))[3], 255)


@requires_pil
class TestConceptPolish(unittest.TestCase):
    def _pal(self):
        return v2.get_palette("dark")

    def test_over_blends_alpha(self):
        # Fully opaque -> the colour itself; fully transparent -> the base.
        self.assertEqual(v2.over((10, 20, 30, 255), (0, 0, 0)), (10, 20, 30))
        self.assertEqual(v2.over((10, 20, 30, 0), (5, 6, 7)), (5, 6, 7))
        mid = v2.over((100, 100, 100, 128), (0, 0, 0))
        self.assertTrue(all(40 <= c <= 60 for c in mid))

    def test_brand_badge_is_larger_than_glyph_and_transparent_corner(self):
        tile, pad = v2.brand_badge(24, self._pal(), scale=1.0)
        # The badge tile includes a glow pad, so it's larger than the badge and
        # returns the pad so callers can align it.
        self.assertGreater(pad, 0)
        self.assertGreater(tile.width, 24)
        self.assertEqual(tile.mode, "RGBA")
        # Far corner is (near) transparent — the glow is a soft rounded bleed.
        self.assertLess(tile.getpixel((0, 0))[3], 60)
        # Centre is opaque (the gradient badge itself).
        c = tile.width // 2
        self.assertEqual(tile.getpixel((c, c))[3], 255)

    def test_soft_pill_rounded_and_hover_differs(self):
        pal = self._pal()
        font = v2.load_font("reg", 10, 1.0)
        normal = v2.soft_pill(text="复制", icon="copy", font=font,
                              palette=pal, scale=1.0, hover=False)
        hover = v2.soft_pill(text="复制", icon="copy", font=font,
                             palette=pal, scale=1.0, hover=True)
        self.assertEqual(normal.mode, "RGBA")
        self.assertEqual(normal.size, hover.size)
        # Full-pill radius: the extreme corner pixel is transparent.
        self.assertLess(normal.getpixel((0, 0))[3], 128)
        # Hover brightens the fill/ink, so the two bakes differ.
        self.assertNotEqual(normal.tobytes(), hover.tobytes())

    def test_gradient_soft_pill_hover_keeps_brand_fill(self):
        pal = v2.get_palette("light")
        normal = v2.soft_pill(
            palette=pal, scale=1.0, hover=False, min_w=80, grad=True)
        hover = v2.soft_pill(
            palette=pal, scale=1.0, hover=True, min_w=80, grad=True)
        center = (hover.width // 2, hover.height // 2)
        self.assertNotEqual(normal.tobytes(), hover.tobytes())
        self.assertNotEqual(hover.getpixel(center)[:3], (255, 255, 255))

    def test_danger_soft_pill_is_filled_and_changes_on_hover(self):
        def _luminance(rgb):
            channels = []
            for value in rgb:
                value /= 255
                channels.append(
                    value / 12.92 if value <= 0.04045
                    else ((value + 0.055) / 1.055) ** 2.4)
            return (
                0.2126 * channels[0]
                + 0.7152 * channels[1]
                + 0.0722 * channels[2])

        for theme in ("dark", "light"):
            with self.subTest(theme=theme):
                pal = v2.get_palette(theme)
                normal = v2.soft_pill(
                    palette=pal, scale=1.0, hover=False,
                    min_w=80, danger=True)
                hover = v2.soft_pill(
                    palette=pal, scale=1.0, hover=True,
                    min_w=80, danger=True)
                center = (normal.width // 2, normal.height // 2)
                self.assertEqual(normal.getpixel(center)[3], 255)
                self.assertNotEqual(normal.tobytes(), hover.tobytes())
                for image in (normal, hover):
                    fill = image.getpixel(center)[:3]
                    contrast = 1.05 / (_luminance(fill) + 0.05)
                    self.assertGreaterEqual(contrast, 4.5)

    def test_soft_pill_caret_widens_it(self):
        pal = self._pal()
        font = v2.load_font("reg", 10, 1.0)
        plain = v2.soft_pill(text="操作", font=font, palette=pal, scale=1.0)
        caret = v2.soft_pill(text="操作", font=font, palette=pal, scale=1.0,
                             caret=True)
        self.assertGreater(caret.width, plain.width)

    def test_ghost_icon_transparent_at_rest_fills_on_hover(self):
        pal = self._pal()
        rest = v2.ghost_icon("close", pal, scale=1.0, hover=False, danger=True)
        hover = v2.ghost_icon("close", pal, scale=1.0, hover=True, danger=True)
        self.assertEqual(rest.mode, "RGBA")
        self.assertEqual(rest.size, hover.size)
        # At rest a corner is fully transparent (no fill); hover adds a wash so
        # the two differ.
        self.assertEqual(rest.getpixel((0, 0))[3], 0)
        self.assertNotEqual(rest.tobytes(), hover.tobytes())

    def test_bake_input_field_focus_brightens(self):
        pal = self._pal()
        img_u, inset = v2.bake_input_field(240, 48, 12, pal, 1.0, focused=False)
        img_f, inset_f = v2.bake_input_field(240, 48, 12, pal, 1.0, focused=True)
        self.assertEqual(img_u.size, (240, 48))
        self.assertEqual(inset, inset_f)
        self.assertGreater(inset, 0)
        # Corner is transparent (glow + rounded fill leave the tile edge clear).
        self.assertLess(img_u.getpixel((0, 0))[3], 40)
        # Focused variant has a stronger glow, so the bakes differ.
        self.assertNotEqual(img_u.tobytes(), img_f.tobytes())


class TestTkIntegration(unittest.TestCase):
    def test_ui_v2_available_matches_pil(self):
        # available() implies PIL is importable.
        if v2.ui_v2_available():
            self.assertTrue(v2.PIL_OK)

    def test_to_photo_none_is_safe(self):
        self.assertIsNone(v2.to_photo(None))


if __name__ == "__main__":
    unittest.main()
