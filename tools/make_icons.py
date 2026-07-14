"""Generate the adaptive tray icons for CC Translate.

Produces two transparent-background "CC" glyph icons that stay crisp against
either taskbar theme:

  cc-dark.ico   white glyph   -> shown on a DARK taskbar (dark mode)
  cc-light.ico  brand-blue    -> shown on a LIGHT taskbar (light mode)

The glyph is drawn as two bold rounded "C" arcs (matching the app's existing
rounded-square identity, minus the tile) with heavy supersampling so the
downsized 16px tray render stays smooth. Re-run this script whenever the mark
changes; the app loads whichever file matches the current taskbar theme and
falls back to cc.ico / a generated glyph if the files are missing.
"""

import os

from PIL import Image, ImageDraw, ImageFont

BRAND_BLUE = (37, 99, 235, 255)      # #2563EB — same blue as the tile icon
WHITE = (245, 246, 248, 255)         # very slightly off-white, softer on dark

ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48),
             (32, 32), (24, 24), (16, 16)]

SS = 4                                # supersampling factor
BASE = 256
CANVAS = BASE * SS

# Heavy, slightly-rounded weights that read well shrunk to 16px. First hit wins.
FONT_CANDIDATES = ["seguibl.ttf", "ariblk.ttf", "segoeuib.ttf", "arialbd.ttf"]
TEXT = "CC"


def _load_font(px):
    win_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in FONT_CANDIDATES:
        path = os.path.join(win_fonts, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, px)
    return ImageFont.load_default()


def render(colour):
    """Draw a bold 'CC' wordmark on a transparent canvas, tightly centred."""
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font = _load_font(int(CANVAS * 0.62))
    # Tighten the default letter spacing so the two C's sit close, like the
    # original wordmark, then centre the whole block by its ink bounds.
    kern = int(CANVAS * -0.03)
    l, r = TEXT[0], TEXT[1]
    wl = draw.textlength(l, font=font)
    wr = draw.textlength(r, font=font)
    total = wl + kern + wr
    bbox = draw.textbbox((0, 0), TEXT, font=font)
    top = bbox[1]
    height = bbox[3] - bbox[1]
    x = (CANVAS - total) / 2
    y = (CANVAS - height) / 2 - top
    draw.text((x, y), l, font=font, fill=colour)
    draw.text((x + wl + kern, y), r, font=font, fill=colour)

    return img.resize((BASE, BASE), Image.LANCZOS)


def save_ico(img, path):
    img.save(path, format="ICO", sizes=ICO_SIZES)
    print("wrote", path)


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dark = render(WHITE)          # white glyph for a dark taskbar
    light = render(BRAND_BLUE)    # blue glyph for a light taskbar
    save_ico(dark, os.path.join(here, "cc-dark.ico"))
    save_ico(light, os.path.join(here, "cc-light.ico"))
    # PNG previews on contrasting backgrounds for visual review.
    for name, glyph, bg in (("_preview-dark", dark, (32, 33, 36, 255)),
                            ("_preview-light", light, (243, 243, 243, 255))):
        canvas = Image.new("RGBA", (BASE, BASE), bg)
        canvas.alpha_composite(glyph)
        canvas.save(os.path.join(here, name + ".png"))
        print("wrote", name + ".png")


if __name__ == "__main__":
    main()
