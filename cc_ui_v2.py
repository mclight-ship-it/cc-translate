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
        field=(255, 255, 255, 220), field_brd=(150, 130, 255),
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


def rounded_mask(w, h, r):
    """An L-mode rounded-rectangle alpha mask."""
    w = max(1, int(w))
    h = max(1, int(h))
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius=int(r),
                                        fill=255)
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
    crop is identical frame to frame. :func:`bake_face` cannot provide that —
    its diagonal gradient (``hypot(w, h)``) and h-relative glow offsets shift the
    top region whenever the bake height changes. Here the base is a vertical
    gradient over a FIXED reference height and both glows are anchored to the top
    (offsets independent of ``h``), so baking taller only appends rows and every
    ``crop((0, 0, w, h'))`` for ``h' <= h`` is byte-identical to a shorter bake."""
    w = max(1, int(w))
    h = max(1, int(h))
    # Vertical navy base over a fixed reference height: row y's colour is a pure
    # function of y, so it is the same in a 200px bake and a 1600px bake.
    ref = max(1, scaled(_STREAM_REF_PTS, scale))
    col = Image.new("RGB", (1, h))
    cpx = col.load()
    for y in range(h):
        cpx[0, y] = sample_stops(palette["solid"], min(1.0, y / ref))
    face = col.resize((w, h)).convert("RGBA")
    # Cool glow pinned to the top-left; warm glow pinned a fixed distance below
    # the top. Both offsets are constants (× scale), never × h.
    hi = radial_glow(int(w * 1.05), palette["glow_hi"],
                     0.14 if palette["is_dark"] else 0.07)
    face.alpha_composite(hi, (-int(w * 0.3), -scaled(_STREAM_HI_UP_PTS, scale)))
    lo = radial_glow(int(w * 0.95), palette["glow_lo"],
                     0.12 if palette["is_dark"] else 0.06)
    face.alpha_composite(lo, (int(w * 0.5), scaled(_STREAM_LO_DOWN_PTS, scale)))
    return face


# Fixed geometry for the height-stable streaming bake (design points).
_STREAM_REF_PTS = 520       # vertical gradient reaches the deep navy by here
_STREAM_HI_UP_PTS = 150     # cool glow centre this far ABOVE the top edge
_STREAM_LO_DOWN_PTS = 130   # warm glow centre this far BELOW the top edge


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
        """Face with rounded corners applied (alpha cut)."""
        f = self.face(w, h).copy()
        f.putalpha(rounded_mask(w, h, radius))
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
