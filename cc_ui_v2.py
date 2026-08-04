# -*- coding: utf-8 -*-
"""cc_ui_v2 — rendering foundation for the v2 UI redesign (dark-launch).

This is the SKIN LAYER every v2 page depends on: it bakes the concept
aesthetic — deep-navy base + the tri-colour brand gradient (blue -> violet ->
pink) + soft glow — into PIL images that pages place behind their widgets.

Design constraints (see docs/UI_V2_PLAN.md):
  * Pure, testable core: the rendering functions take/return PIL images and
    take an explicit ``scale`` (HiDPI) argument — NO global scale, NO tk import
    in the hot path — so they can be exercised offscreen in unit tests exactly
    like the standalone POC.
  * Graceful degradation: Pillow is an OPTIONAL app dependency (it can be
    missing). Import it lazily and expose ``PIL_OK``; callers fall back to the
    legacy UI when it is False.
  * Perf first: streaming grows the result popup ~20x/second. Re-baking a
    gradient every frame would lag, so :class:`GradientBackground` bakes ONCE
    at a max size and CROPS per frame (the card grows downward from a fixed
    top, so a top-anchored crop of a fixed-width bake is stable frame to frame).
  * Phase-1 scope: opaque navy gradient + glow only. Real frosted glass / DWM
    acrylic is deliberately deferred (risk #1/#2) — the ``glass_bg`` hook exists
    but is off by default.

This module is NOT wired to any page yet; v2 pages branch on ``ui_v2_enabled``
and call into here as they land.
"""

import math

# Pillow is optional. Keep the import lazy/guarded so importing this module can
# never crash the app; pages check PIL_OK (via ui_v2_available) before using it.
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
    try:
        from PIL import ImageTk as _ImageTk
    except Exception:
        _ImageTk = None
    PIL_OK = True
except Exception:
    Image = ImageDraw = ImageFont = ImageFilter = ImageEnhance = None
    _ImageTk = None
    PIL_OK = False


# ---------------------------------------------------------------------------
# Palette — the source of the "tech vibe".
# ---------------------------------------------------------------------------
# The vivid brand gradient. Dropping the pink stop kills the vibe (learned the
# hard way in the POC), so all three stops are load-bearing.
BRAND = [(0.0, (110, 168, 255)), (0.5, (161, 121, 255)), (1.0, (255, 122, 198))]

# Per-theme skin. ``solid`` is the deep-navy base gradient (NOT the app's greyish
# real popup colour — that greyed out the vibe in an earlier POC). Colours are
# RGB tuples unless a 4th alpha channel is meaningful.
_PALETTES = {
    "dark": dict(
        is_dark=True,
        solid=[(0.0, (34, 37, 70)), (1.0, (22, 24, 48))],
        glow=(70, 50, 160),
        glow_hi=(110, 140, 255),
        glow_lo=(255, 120, 200),
        glass_tint=(16, 18, 40, 168),
        glass_border=(255, 255, 255, 40),
        border=(255, 255, 255, 36),
        fg=(238, 241, 255), sub=(170, 178, 213), hint=(127, 136, 173),
        btn=(255, 255, 255, 18), btn_brd=(255, 255, 255, 30),
        field=(10, 12, 28, 210), field_brd=(150, 130, 255),
        panel=(18, 20, 44),                 # solid inner panel for body text / ttk
        ok=(110, 231, 168), err=(240, 113, 120),
    ),
    "light": dict(
        is_dark=False,
        solid=[(0.0, (255, 255, 255)), (1.0, (240, 243, 252))],
        glow=(150, 150, 210),
        glow_hi=(110, 140, 255),
        glow_lo=(255, 120, 200),
        glass_tint=(255, 255, 255, 170),
        glass_border=(255, 255, 255, 200),
        border=(20, 30, 70, 28),
        fg=(28, 35, 64), sub=(83, 96, 138), hint=(128, 137, 168),
        btn=(255, 255, 255, 180), btn_brd=(20, 30, 70, 26),
        field=(255, 255, 255, 255), field_brd=(150, 130, 255),
        panel=(248, 250, 253),
        ok=(22, 163, 74), err=(220, 38, 38),
    ),
}


def get_palette(name):
    """Return the v2 palette dict for 'dark'/'light' (defaults to dark)."""
    return _PALETTES.get(name, _PALETTES["dark"])


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    """Convert an (r, g, b[, a]) tuple to a '#rrggbb' string for tk widgets
    (alpha is ignored — tk widget colours are opaque)."""
    r, g, b = rgb[0], rgb[1], rgb[2]
    return "#{:02x}{:02x}{:02x}".format(int(r), int(g), int(b))


def over(rgba, base):
    """Flatten a possibly-translucent ``rgba`` colour onto an opaque ``base``
    RGB, returning the resulting opaque RGB. Used to give a tk widget (which
    can't be translucent) the exact colour a baked semi-transparent fill shows
    when composited over the navy panel — so the widget blends seamlessly."""
    a = (rgba[3] if len(rgba) > 3 else 255) / 255.0
    return tuple(int(round(rgba[i] * a + base[i] * (1 - a))) for i in range(3))


# ---------------------------------------------------------------------------
# HiDPI + fonts.
# ---------------------------------------------------------------------------
def scaled(value, scale):
    """Scale a design-point length to device pixels."""
    return int(round(value * scale))


# YaHei lacks many symbol glyphs (tofu boxes for arrows/checks/dots), so we draw
# those as shapes; text uses these families only.
_FONT_FILES = {"reg": "msyh.ttc", "bold": "msyhbd.ttc", "mono": "consola.ttf"}
_font_cache = {}


def load_font(kind, size_pt, scale):
    """Load a scaled font by role ('reg'/'bold'/'mono'), cached. Falls back to
    the regular family, then to PIL's default, so a missing file never crashes."""
    if not PIL_OK:
        return None
    px = scaled(size_pt, scale)
    key = (kind, px)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    for fname in (_FONT_FILES.get(kind), _FONT_FILES["reg"]):
        if not fname:
            continue
        try:
            f = ImageFont.truetype(fname, px)
            _font_cache[key] = f
            return f
        except Exception:
            continue
    try:
        f = ImageFont.load_default()
    except Exception:
        f = None
    _font_cache[key] = f
    return f


# Segoe MDL2 Assets glyphs (the same icon font the app's tk buttons use) drawn
# via PIL so the v2 chip buttons carry crisp modern icons, not tofu boxes.
_MDL2_GLYPHS = {"copy": "\uE8C8", "pin": "\uE718", "close": "\uE711",
                "retry": "\uE72C"}


def icon_font(px):
    """Load Segoe MDL2 Assets at ``px`` pixels for drawing icon glyphs, or
    None if the font is unavailable (caller falls back to a text label)."""
    if not PIL_OK:
        return None
    key = ("mdl2", int(px))
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    for fname in ("segmdl2.ttf", "SegMDL2.ttf"):
        try:
            f = ImageFont.truetype(fname, int(px))
            _font_cache[key] = f
            return f
        except Exception:
            continue
    _font_cache[key] = None
    return None


# ---------------------------------------------------------------------------
# Gradient / glow primitives (parameterized; no global scale).
# ---------------------------------------------------------------------------
def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def sample_stops(stops, t):
    """Colour at position t in [0,1] along a list of (pos, rgb) stops."""
    t = max(0.0, min(1.0, t))
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]
        p1, c1 = stops[i + 1]
        if p0 <= t <= p1:
            lt = 0 if p1 == p0 else (t - p0) / (p1 - p0)
            return _lerp(c0, c1, lt)
    return stops[-1][1]


def linear_gradient(w, h, stops, angle=135):
    """A w×h RGBA image filled with a linear gradient at ``angle`` degrees."""
    w = max(1, int(w))
    h = max(1, int(h))
    d = int(math.hypot(w, h)) + 4
    strip = Image.new("RGB", (d, 1))
    px = strip.load()
    for x in range(d):
        px[x, 0] = sample_stops(stops, x / (d - 1))
    strip = strip.resize((d, d)).rotate(angle - 90, resample=Image.BICUBIC)
    left = (d - w) // 2
    top = (d - h) // 2
    return strip.crop((left, top, left + w, top + h)).convert("RGBA")


def flat_base(palette):
    """The single flat navy the v2 card uses as its base — the AVERAGE of the
    palette's ``solid`` gradient stops. The v2 shell fills a FLAT base (not a
    top-to-bottom gradient) and the content panel uses this exact colour, so the
    two are identical everywhere along the card boundary. That's what stops the
    glow-halo margin from reading as a lighter/darker "solid border": with a
    gradient base the top edge was ~+9 luminance brighter than the flat panel (a
    visible frame); a flat base makes that step ~0 and leaves only the soft
    radial corner glows, which fall off gently and read as glow, not an edge."""
    stops = palette["solid"]
    n = len(stops)
    r = sum(c[1][0] for c in stops) / n
    g = sum(c[1][1] for c in stops) / n
    b = sum(c[1][2] for c in stops) / n
    return (int(round(r)), int(round(g)), int(round(b)))


def radial_glow(size, color, strength=1.0):
    """A soft round glow (RGBA). Inset ellipse + blur avoids a hard edge."""
    size = max(2, int(size))
    m = Image.new("L", (size, size), 0)
    ins = int(size * 0.16)
    ImageDraw.Draw(m).ellipse((ins, ins, size - ins, size - ins),
                              fill=int(255 * strength))
    m = m.filter(ImageFilter.GaussianBlur(size / 4.5))
    out = Image.new("RGBA", (size, size), tuple(color) + (0,))
    out.putalpha(m)
    return out


def rounded_mask(w, h, r, ss=4):
    """An L-mode rounded-rectangle alpha mask with anti-aliased corners.

    PIL's ``rounded_rectangle`` is not anti-aliased, so at button sizes the
    corners come out visibly stair-stepped/jagged. Rendering the mask at ``ss``x
    and downsampling with LANCZOS gives smooth, rounded corners (used by every
    gradient pill/tile via ``gradient_round``)."""
    w = max(1, int(w))
    h = max(1, int(h))
    r = max(0, int(r))
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        (0, 0, w * ss - 1, h * ss - 1), radius=r * ss, fill=255)
    if ss != 1:
        m = m.resize((w, h), Image.LANCZOS)
    return m


def gradient_round(w, h, r, stops=BRAND, angle=120):
    """A rounded-rectangle tile filled with a gradient (used for pills/tiles)."""
    g = linear_gradient(w, h, stops, angle)
    g.putalpha(rounded_mask(w, h, r))
    return g


def gradient_text(text, font, stops=BRAND, angle=100, pad=4):
    """Render ``text`` as a gradient-filled RGBA image (for accent titles)."""
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    b = probe.textbbox((0, 0), text, font=font)
    w = (b[2] - b[0]) + pad * 2
    h = (b[3] - b[1]) + pad * 2
    mask = Image.new("L", (max(1, w), max(1, h)), 0)
    ImageDraw.Draw(mask).text((-b[0] + pad, -b[1] + pad), text, font=font,
                              fill=255)
    g = linear_gradient(w, h, stops, angle)
    g.putalpha(mask)
    return g


# ---------------------------------------------------------------------------
# Card face baking (opaque navy gradient + glow). Glass is deferred (off).
# ---------------------------------------------------------------------------
def bake_face(w, h, palette, glass_bg=None, scale=1.0):
    """Bake a w×h card FACE (RGBA, fully opaque interior) for the v2 skin.

    Default path: the deep-navy ``solid`` gradient with two soft brand glows
    (upper-left cool, lower-right warm) — the opaque, readable, performant look
    Phase 1 ships. ``glass_bg`` (a PIL image of what's behind the card) enables
    a baked frosted-glass face instead; it stays OFF by default per the risk
    strategy and is here only so a later phase can experiment without reshaping
    the API."""
    w = max(1, int(w))
    h = max(1, int(h))
    if glass_bg is not None:
        face = glass_bg.filter(ImageFilter.GaussianBlur(scaled(22, scale)))
        face = face.convert("RGBA")
        face = ImageEnhance.Color(face).enhance(1.35)
        face = ImageEnhance.Brightness(face).enhance(
            1.06 if palette["is_dark"] else 1.0)
        face.alpha_composite(Image.new("RGBA", (w, h), palette["glass_tint"]))
        sheen = radial_glow(int(w * 0.9), (150, 130, 255), 0.10)
        face.alpha_composite(sheen, (-int(w * 0.2), -int(h * 0.4)))
        return face
    face = linear_gradient(w, h, palette["solid"], 135)
    hi = radial_glow(int(w * 1.05), palette["glow_hi"],
                     0.14 if palette["is_dark"] else 0.07)
    face.alpha_composite(hi, (-int(w * 0.3), -int(h * 0.5)))
    lo = radial_glow(int(w * 0.95), palette["glow_lo"],
                     0.12 if palette["is_dark"] else 0.06)
    face.alpha_composite(lo, (int(w * 0.5), int(h * 0.55)))
    return face


def bake_card(w, h, palette, scale=1.0, radius=18, glass_bg=None):
    """Bake a full card: outer glow bleed + rounded gradient face + hairline
    border + top sheen. Returns ``(image, pad)`` where ``pad`` is the
    transparent margin around the card (glow bleed) — mirror of the POC ``card``.
    Callers place widgets at offset ``(pad, pad)``."""
    r = scaled(radius, scale)
    pad = scaled(46, scale)
    W, H = w + pad * 2, h + pad * 2
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (pad, pad + scaled(10, scale), pad + w, pad + h + scaled(10, scale)),
        radius=r, fill=tuple(palette["glow"]) + (150 if palette["is_dark"] else 95,))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(scaled(26, scale))))

    face = bake_face(w, h, palette, glass_bg=glass_bg, scale=scale)
    face.putalpha(rounded_mask(w, h, r))
    canvas.alpha_composite(face, (pad, pad))

    d = ImageDraw.Draw(canvas)
    bcol = palette["glass_border"] if glass_bg is not None else palette["border"]
    bcol = bcol if len(bcol) == 4 else tuple(bcol) + (255,)
    d.rounded_rectangle((pad, pad, pad + w - 1, pad + h - 1), radius=r,
                        outline=bcol, width=1)

    sheen = Image.new("RGBA", (w, 2), (0, 0, 0, 0))
    for x in range(w):
        a = int((150 if palette["is_dark"] else 110) * math.sin(math.pi * x / w))
        sheen.putpixel((x, 0), (255, 255, 255, a))
    canvas.alpha_composite(sheen, (pad + scaled(18, scale), pad + 1))
    return canvas, pad


# ---------------------------------------------------------------------------
# Perf: bake once at max size, crop per frame (streaming hot path).
# ---------------------------------------------------------------------------
def bake_stream_face(w, h, palette, scale=1.0):
    """Bake a card FACE whose appearance is HEIGHT-STABLE: the pixel at row y
    depends only on y (and w), never on the total height ``h``.

    This is the streaming variant of :func:`bake_face`. The result popup grows
    downward ~20x/second, and the anti-flicker guarantee is that a top-anchored
    crop is identical frame to frame. The face is a single FLAT navy
    (:func:`flat_base`) — the same colour as the content panel — so the card is
    one uniform plate with no internal brightness steps. The v2 "frame" is a
    thin brand-gradient hairline drawn separately at the rounded-rect perimeter
    (see :func:`bake_border_ring` / :meth:`GradientBackground.rounded_face`), not
    a wide glow margin: an opaque tk window can't render a real translucent glow
    OUTSIDE itself, so a wide margin only ever read as a lighter "solid border".
    A flat fill is trivially height-stable (every row is identical)."""
    w = max(1, int(w))
    h = max(1, int(h))
    return Image.new("RGBA", (w, h), tuple(flat_base(palette)) + (255,))


# ---------------------------------------------------------------------------
# Thin brand-gradient perimeter hairline (the v2 "border").
# ---------------------------------------------------------------------------
_border_row_cache = {}


def _brand_border_row(w):
    """A cached 1-row horizontal brand gradient (RGBA, opaque) of width ``w``.

    Horizontal means the stroke colour depends only on x, so the top and side
    hairline is HEIGHT-STABLE during streaming (only the bottom edge advances as
    the card grows — expected), keeping the anti-flicker guarantee intact."""
    w = max(1, int(w))
    hit = _border_row_cache.get(w)
    if hit is not None:
        return hit
    row = Image.new("RGBA", (w, 1))
    px = row.load()
    for x in range(w):
        px[x, 0] = tuple(sample_stops(BRAND, x / max(1, w - 1))) + (255,)
    _border_row_cache[w] = row
    return row


def bake_border_ring(w, h, radius, scale=1.0, stroke_pts=1.4, alpha=120):
    """A transparent RGBA (w×h) carrying ONLY a thin brand-gradient stroke along
    the rounded-rectangle perimeter — the v2 popup's slim, subtly-graded frame.
    Composited over the flat navy face by :meth:`GradientBackground.rounded_face`.
    Returns ``None`` when Pillow is unavailable."""
    if not PIL_OK:
        return None
    w = max(1, int(w))
    h = max(1, int(h))
    stroke = max(1, scaled(stroke_pts, scale))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, w - 1, h - 1), radius=int(radius), outline=alpha, width=stroke)
    grad = _brand_border_row(w).resize((w, h))
    ring = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ring.paste(grad, (0, 0), mask)
    return ring


def panel_match_color(palette, scale=1.0):
    """The flat navy the v2 content panel uses — identical to the shell's flat
    base (:func:`flat_base`). With the shell now a single flat colour (no glow
    wash), the panel and the surrounding shell are the same navy everywhere, so
    the ``radius``-wide reveal around the content card is invisible and the only
    thing that shows at the edge is the thin brand hairline."""
    return flat_base(palette)


class GradientBackground:
    """Caches a height-stable card FACE and serves per-frame crops cheaply.

    The result popup streams: its width is locked and it only grows DOWNWARD
    from a fixed top. We bake once (via :func:`bake_stream_face`, whose rows are
    a pure function of y) at the locked width and a generous reserve height, then
    return a top-anchored crop for the current height — no per-frame gradient
    math, and because the bake is height-stable the crop is byte-identical frame
    to frame, INCLUDING across a re-bake when the card finally grows past the
    reserve. The cache re-bakes only when the width, scale or palette change, or
    a height beyond the current reserve is requested."""

    def __init__(self, palette, scale=1.0, reserve_pts=1600):
        self.palette = palette
        self.scale = scale
        self.reserve_pts = reserve_pts
        self._cache = None          # baked PIL face
        self._cw = 0                # cached width
        self._ch = 0                # cached (reserve) height

    def _ensure(self, w, h):
        w = max(1, int(w))
        h = max(1, int(h))
        if self._cache is not None and w == self._cw and h <= self._ch:
            return
        # Bake to a generous fixed reserve so the common case never re-bakes;
        # if an unusually tall card exceeds it, grow the reserve. Either way the
        # bake is height-stable, so crops stay identical across the re-bake.
        bake_h = max(h, self._ch, scaled(self.reserve_pts, self.scale))
        self._cache = bake_stream_face(w, bake_h, self.palette, scale=self.scale)
        self._cw, self._ch = w, bake_h

    def face(self, w, h):
        """Return an opaque w×h face, cropped from the cached bake."""
        w = max(1, int(w))
        h = max(1, int(h))
        self._ensure(w, h)
        return self._cache.crop((0, 0, w, h))

    def rounded_face(self, w, h, radius):
        """Face with rounded corners applied (alpha cut) plus the thin
        brand-gradient perimeter hairline composited on top."""
        f = self.face(w, h).copy()
        f.putalpha(rounded_mask(w, h, radius))
        ring = bake_border_ring(w, h, radius, scale=self.scale)
        if ring is not None:
            f.alpha_composite(ring)
        return f

    def invalidate(self):
        self._cache = None
        self._cw = self._ch = 0


# ---------------------------------------------------------------------------
# Small shape widgets (drawn, not glyphs — dodges YaHei tofu boxes).
# ---------------------------------------------------------------------------
def draw_caret(draw, cx, cy, size, color):
    """A downward triangle (dropdown caret) centred at (cx, cy)."""
    draw.polygon([(cx - size, cy - size // 2), (cx + size, cy - size // 2),
                  (cx, cy + size // 2)], fill=color)


def draw_check(draw, x, y, size, color, width=2):
    """A check-mark starting near (x, y) spanning ~size."""
    draw.line((x, y + size * 0.55, x + size * 0.42, y + size), fill=color,
              width=width)
    draw.line((x + size * 0.42, y + size, x + size, y), fill=color, width=width)


def icon_tile(size, text="CC", scale=1.0):
    """The rounded gradient app-mark tile with centred text."""
    t = gradient_round(size, size, scaled(11, scale), BRAND, 135)
    d = ImageDraw.Draw(t)
    f = load_font("bold", int(size / (2.2 * scale)), scale)
    b = d.textbbox((0, 0), text, font=f)
    d.text(((size - (b[2] - b[0])) / 2 - b[0], (size - (b[3] - b[1])) / 2 - b[1]),
           text, font=f, fill=(255, 255, 255, 245))
    return t


def gradient_pill(text, font, palette, fg=None, grad=False, px=14, py=8,
                  scale=1.0, caret=False, dot=None):
    """A pill button image: gradient-filled (``grad``) or translucent chip.

    ``caret`` appends a dropdown triangle; ``dot`` (an RGB tuple) prepends a
    status dot. Both are drawn as shapes to avoid tofu glyphs."""
    fg = fg or palette["fg"]
    b = ImageDraw.Draw(Image.new("L", (4, 4))).textbbox((0, 0), text, font=font)
    tw, th = b[2] - b[0], b[3] - b[1]
    cg = scaled(15, scale) if caret else 0
    dg = scaled(15, scale) if dot else 0
    pxs, pys = scaled(px, scale), scaled(py, scale)
    w = tw + pxs * 2 + cg + dg
    h = th + pys * 2
    r = h // 2
    if grad:
        img = gradient_round(w, h, r, BRAND, 120)
        fg = (255, 255, 255, 255)
    else:
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        brd = palette["btn_brd"]
        brd = brd if len(brd) == 4 else tuple(brd) + (255,)
        ImageDraw.Draw(img).rounded_rectangle((0, 0, w - 1, h - 1), radius=r,
                                              fill=palette["btn"], outline=brd,
                                              width=1)
    dr = ImageDraw.Draw(img)
    col = fg if len(fg) == 4 else tuple(fg) + (255,)
    ox = pxs
    if dot:
        dr.ellipse((ox, h // 2 - scaled(4, scale), ox + scaled(8, scale),
                    h // 2 + scaled(4, scale)), fill=tuple(dot) + (255,))
        ox += dg
    dr.text((ox - b[0], (h - th) / 2 - b[1]), text, font=font, fill=col)
    ox += tw
    if caret:
        draw_caret(dr, ox + scaled(7, scale), h // 2, scaled(3, scale), col)
    return img


def status_dot(color, size=8, scale=1.0):
    """A standalone filled status dot image."""
    s = scaled(size, scale)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((0, 0, s - 1, s - 1), fill=tuple(color) + (255,))
    return img


# ---------------------------------------------------------------------------
# Concept-polish pieces: brand badge, soft pill / ghost-icon buttons, field.
# ---------------------------------------------------------------------------
# Every top-right control (soft pill + ghost icon) bakes to this device height
# so Copy / pin / close line up perfectly (same centre, same top) — no more one
# button sitting taller or a pushpin floating high.
SOFT_BTN_H_PTS = 30


def brand_badge(size_pt, palette, scale=1.0):
    """The concept's brand mark: a tri-colour brand-gradient rounded square with
    a white "CC" floating on a soft violet bloom. Returns ``(tile, pad)`` where
    ``pad`` is the transparent glow margin (device px) AND the LEFT/TOP offset of
    the badge inside the tile, so a caller can shift the label left by ``pad`` to
    align the badge's visual edge with a text column. The bloom is deliberately
    stronger than a flat icon so it reads as a *designed*, lit mark — not the
    plain .ico app icon."""
    if not PIL_OK:
        return None, 0
    s = scaled(size_pt, scale)
    r = int(s * 0.32)
    pad = int(s * 0.34)
    W = s + pad * 2
    tile = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    # Two stacked blurred violet blooms (wide+faint under a tighter+brighter one)
    # give a soft, obvious halo with no boxy edge.
    for spread, alpha, blur, dy in (
            (0.12, 70, 0.55, 0.16), (0.0, 150, 0.30, 0.12)):
        glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        gx = pad - int(s * spread)
        gy = pad + int(s * dy)
        ImageDraw.Draw(glow).rounded_rectangle(
            (gx, gy, gx + s + int(s * spread * 2), gy + s),
            radius=r, fill=(150, 120, 255, alpha))
        tile.alpha_composite(glow.filter(ImageFilter.GaussianBlur(int(s * blur))))
    badge = gradient_round(s, s, r, stops=BRAND, angle=135)
    hl = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(hl).rounded_rectangle((0, 0, s - 1, int(s * 0.5)), radius=r,
                                         fill=(255, 255, 255, 40))
    badge.alpha_composite(hl.filter(ImageFilter.GaussianBlur(int(s * 0.10))))
    f = load_font("bold", max(6, int(size_pt * 0.46)), scale)
    d = ImageDraw.Draw(badge)
    b = d.textbbox((0, 0), "CC", font=f)
    tw, th = b[2] - b[0], b[3] - b[1]
    d.text(((s - tw) / 2 - b[0], (s - th) / 2 - b[1]), "CC", font=f,
           fill=(255, 255, 255, 255))
    tile.alpha_composite(badge, (pad, pad))
    return tile, pad


def soft_pill(text=None, icon=None, font=None, palette=None, scale=1.0,
              hover=False, caret=False, min_w=0):
    """A soft translucent rounded pill button (RGBA) — the concept's primary
    top-right action style (e.g. 复制 / 操作). Optional MDL2 ``icon`` and a
    ``caret`` down-triangle (drawn, never a tofu box). No hard border; a full
    pill radius and a gentle white fill that brightens on ``hover``. Every pill
    bakes to the SAME height (``SOFT_BTN_H_PTS``) regardless of whether it has an
    icon, so 复制 and 操作 are the same size and sit on the same line. ``min_w``
    (device px) floors the width so sibling pills match; the content is centred
    in the extra room."""
    if not PIL_OK:
        return None
    pw = scaled(12, scale)
    gap = scaled(6, scale)
    icon_px = scaled(15, scale)
    ifont = icon_font(icon_px) if icon else None
    glyph = _MDL2_GLYPHS.get(icon) if icon else None
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    tw = th = iw = ih = 0
    if text and font:
        b = probe.textbbox((0, 0), text, font=font)
        tw, th = b[2] - b[0], b[3] - b[1]
    if glyph and ifont:
        b = probe.textbbox((0, 0), glyph, font=ifont)
        iw, ih = b[2] - b[0], b[3] - b[1]
    cgap = gap if (iw and tw) else 0
    cw = scaled(8, scale) if caret else 0
    ccgap = scaled(5, scale) if caret else 0
    content_w = iw + cgap + tw + ccgap + cw
    W = max(1, content_w + pw * 2, int(min_w))
    H = scaled(SOFT_BTN_H_PTS, scale)
    is_dark = palette["is_dark"]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if is_dark:
        fill = (255, 255, 255, 30 if hover else 16)
        ink = (238, 241, 255, 255) if hover else (206, 212, 235, 255)
    else:
        # Light mode needs a clearly visible surface: a ~5% navy wash read as
        # "disabled". Give the pill a soft, visible lavender-grey fill and darker
        # ink so it looks like a real button on white.
        fill = (36, 48, 92, 46 if hover else 28)
        ink = (28, 35, 64, 255) if hover else (66, 76, 112, 255)
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2, fill=fill)
    # Centre the icon+text+caret block horizontally (so a min_w-floored pill
    # keeps its content centred, not left-hugging).
    ox = max(pw, (W - content_w) // 2)
    if glyph and ifont:
        b = probe.textbbox((0, 0), glyph, font=ifont)
        d.text((ox - b[0], (H - ih) / 2 - b[1]), glyph, font=ifont, fill=ink)
        ox += iw + cgap
    if text and font:
        b = probe.textbbox((0, 0), text, font=font)
        d.text((ox - b[0], (H - th) / 2 - b[1]), text, font=font, fill=ink)
        ox += tw
    if caret:
        cx = ox + ccgap
        cy = H // 2
        ch = scaled(3, scale)
        d.polygon([(cx, cy - ch), (cx + cw, cy - ch), (cx + cw / 2, cy + ch)],
                  fill=ink)
    return img


def ghost_icon(icon, palette, scale=1.0, hover=False, danger=False):
    """An icon-only window control (RGBA): fully transparent at rest so it reads
    as light-weight, gaining a soft round fill on ``hover`` (a subtle red wash
    when ``danger``, i.e. the close button). Bakes to the SAME height as
    ``soft_pill`` (and a matching square-ish width) so pin / close line up with
    Copy — same centre, no floating-high pushpin."""
    if not PIL_OK:
        return None
    icon_px = scaled(15, scale)
    ifont = icon_font(icon_px)
    glyph = _MDL2_GLYPHS.get(icon, icon)
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    b = probe.textbbox((0, 0), glyph, font=ifont)
    iw, ih = b[2] - b[0], b[3] - b[1]
    H = scaled(SOFT_BTN_H_PTS, scale)
    W = max(H, iw + scaled(13, scale))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink = (206, 212, 235, 255) if palette["is_dark"] else (74, 84, 120, 255)
    if danger and hover:
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2,
                            fill=tuple(palette["err"]) + (40,))
        ink = tuple(palette["err"]) + (255,)
    elif hover:
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2,
                            fill=(255, 255, 255, 30) if palette["is_dark"]
                            else (36, 48, 92, 34))
        ink = (238, 241, 255, 255) if palette["is_dark"] else (28, 35, 64, 255)
    d.text(((W - iw) / 2 - b[0], (H - ih) / 2 - b[1]), glyph, font=ifont,
           fill=ink)
    return img


def bake_input_field(w, h, radius, palette, scale=1.0, focused=False,
                     inset=None):
    """Bake a rounded input field (RGBA, transparent outside the field).

    Dark mode: a soft violet bloom bleeds from the field edge (reads as emitted
    light against the deep-navy card), a dark rounded fill on top, and a subtle
    violet hairline — brighter when ``focused``.

    Light mode: a wide coloured bloom over WHITE only reads as a dirty grey
    smudge (you can't emit light brighter than white), so instead the field gets
    a clean, tight neutral drop-shadow to lift it off the surface plus a crisp
    violet ring that strengthens on focus — a modern Fluent-style input.

    Returns ``(image, inset)`` where ``inset`` is the transparent margin (device
    px) reserved around the field for the halo/shadow; place the text widget at
    that offset. ``inset`` may be passed by the caller (generous so the blur
    fades fully inside the image — otherwise it clips to a hard line); defaults
    to a compact 11px."""
    if not PIL_OK:
        return None, 0
    w = max(1, int(w))
    h = max(1, int(h))
    inset = scaled(11, scale) if inset is None else int(inset)
    x0, y0, x1, y1 = inset, inset, w - 1 - inset, h - 1 - inset
    if x1 <= x0 or y1 <= y0:
        return Image.new("RGBA", (w, h), (0, 0, 0, 0)), inset
    gcol = tuple((palette.get("field_brd") or (150, 130, 255)))[:3]
    is_dark = palette["is_dark"]
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    def _safe_sigma(desired_logical, grow_dev):
        # Clamp a layer's blur radius so its tail (~3*sigma beyond ``grow``) still
        # fades to ~0 *inside* the transparent ``inset`` margin. Otherwise the
        # Gaussian gets clipped flat at the image edge and shows a hard
        # horizontal line (the seam the field used to have above/below it).
        sig = scaled(desired_logical, scale)
        return min(float(sig), max(1.0, (inset - grow_dev - 1) / 3.0))

    if is_dark:
        # Three blurred violet layers (wide+faint under mid under tight+strong)
        # = a smooth, clearly-visible bloom that falls off gently. Alphas are
        # (unfocused, focused). Kept small enough that, with the caller's
        # generous inset, the bloom fades to ~0 before the image edge (no clip).
        for grow, alpha_f, blur in (
                (scaled(3, scale), (0.26, 0.40), 6),
                (scaled(1, scale), (0.42, 0.62), 4),
                (0, (0.62, 0.90), 2)):
            glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            a = int(255 * (alpha_f[1] if focused else alpha_f[0]))
            ImageDraw.Draw(glow).rounded_rectangle(
                (x0 - grow, y0 - grow, x1 + grow, y1 + grow),
                radius=int(radius) + grow, fill=gcol + (a,))
            canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(
                _safe_sigma(blur, grow))))
    else:
        # Light mode: a soft violet/pink halo hugging the field (a gentle brand
        # tint, NOT neutral grey and NOT a wide muddy bloom). Two tight, faint,
        # blurred layers of a pink-leaning violet — kept close to the edge with
        # low alpha so over white it stays a clean coloured haze, not a smudge.
        # Slightly stronger on focus. No downward offset, so it reads as a glow
        # (evenly around) rather than a drop-shadow.
        halo = (196, 132, 224)          # pink-violet, echoes the brand gradient
        base = 1.35 if focused else 1.0
        for grow, alpha, blur in (
                (scaled(3, scale), 0.16 * base, 8),
                (scaled(1, scale), 0.20 * base, 5)):
            gl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(gl).rounded_rectangle(
                (x0 - grow, y0 - grow, x1 + grow, y1 + grow),
                radius=int(radius) + grow,
                fill=halo + (int(255 * min(alpha, 1.0)),))
            canvas.alpha_composite(gl.filter(ImageFilter.GaussianBlur(
                _safe_sigma(blur, grow))))
        if focused:
            ring = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(ring).rounded_rectangle(
                (x0 - scaled(1, scale), y0 - scaled(1, scale),
                 x1 + scaled(1, scale), y1 + scaled(1, scale)),
                radius=int(radius) + scaled(1, scale),
                outline=gcol + (150,), width=max(1, scaled(2, scale)))
            canvas.alpha_composite(ring.filter(ImageFilter.GaussianBlur(
                _safe_sigma(2, scaled(1, scale)))))
    # Draw the crisp field fill + hairline outline on a supersampled layer, then
    # downscale (LANCZOS) so the rounded corners are anti-aliased (the non-AA
    # rounded_rectangle showed corner stair-stepping, worst on white).
    if is_dark:
        brd_a = 190 if focused else 110
    else:
        # A calm violet ring at rest, clearly stronger on focus.
        brd_a = 220 if focused else 120
    ss = 4
    top = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    td = ImageDraw.Draw(top)
    sx0, sy0, sx1, sy1 = x0 * ss, y0 * ss, (x1 + 1) * ss - 1, (y1 + 1) * ss - 1
    td.rounded_rectangle((sx0, sy0, sx1, sy1), radius=int(radius) * ss,
                         fill=tuple(palette["field"]))
    td.rounded_rectangle((sx0, sy0, sx1, sy1), radius=int(radius) * ss,
                         outline=gcol + (brd_a,),
                         width=max(1, scaled(1, scale)) * ss)
    canvas.alpha_composite(
        top.resize((w, h), Image.LANCZOS))
    return canvas, inset


# ---------------------------------------------------------------------------
# Thin tk integration.
# ---------------------------------------------------------------------------
def ui_v2_available():
    """True only when Pillow (with ImageTk) is present, so a page can safely
    render the v2 skin. Pair this with cc_core.ui_v2_enabled(cfg): a page uses
    v2 only when BOTH the flag is on AND the renderer is available."""
    return PIL_OK and _ImageTk is not None


def to_photo(img, master=None):
    """Convert a PIL image to an ImageTk.PhotoImage (or None if unavailable).
    Keep a reference on a long-lived object — Tk drops unreferenced images."""
    if not ui_v2_available() or img is None:
        return None
    try:
        if master is not None:
            return _ImageTk.PhotoImage(img, master=master)
        return _ImageTk.PhotoImage(img)
    except Exception:
        return None
