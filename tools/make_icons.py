"""Pack the CC Translate tray icons from source PNGs into multi-size .ico files.

The app ships two theme-specific tray icons that Windows picks between based on
the taskbar theme:

  cc-dark.ico    -> shown on a DARK taskbar  (source: assets/icon-dark.png)
  cc-light.ico   -> shown on a LIGHT taskbar (source: assets/icon-light.png)

Each .ico bundles 7 sizes (16..256) so Windows can serve a crisp render at any
scale — the tray uses the 16px frame, Explorer/large views use bigger ones.

Usage:
  python tools/make_icons.py            # pack from assets/icon-{dark,light}.png
  python tools/make_icons.py A.png B.png  # pack from explicit dark, light PNGs

If a source PNG is missing, that icon is left untouched. Run this whenever the
artwork changes; the app loads whichever .ico matches the current taskbar theme
and falls back to cc.ico / a generated glyph if the files are missing.
"""

import os
import sys

from PIL import Image

ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48),
             (32, 32), (24, 24), (16, 16)]

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(REPO, "assets")

# (source PNG, output ICO). "dark" = the icon for a dark taskbar, etc.
TARGETS = [
    (os.path.join(ASSETS, "icon-dark.png"), os.path.join(REPO, "cc-dark.ico")),
    (os.path.join(ASSETS, "icon-light.png"), os.path.join(REPO, "cc-light.ico")),
]


def pack_ico(src_png, out_ico):
    """Load a square RGBA PNG and write a multi-size .ico.

    The image is normalised to RGBA and, if not square, padded transparently to
    a square before packing so no size in the set gets distorted.
    """
    img = Image.open(src_png).convert("RGBA")
    w, h = img.size
    if w != h:
        side = max(w, h)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.alpha_composite(img, ((side - w) // 2, (side - h) // 2))
        img = square
    # Downscale the master to the largest target with a high-quality filter so
    # PIL's internal per-size resampling starts from a clean source.
    if img.size != ICO_SIZES[0]:
        img = img.resize(ICO_SIZES[0], Image.LANCZOS)
    img.save(out_ico, format="ICO", sizes=ICO_SIZES)
    kb = os.path.getsize(out_ico) / 1024
    print(f"wrote {out_ico}  ({kb:.1f} KB, sizes {[s[0] for s in ICO_SIZES]})")


def main(argv):
    if len(argv) == 2:
        targets = [(argv[0], os.path.join(REPO, "cc-dark.ico")),
                   (argv[1], os.path.join(REPO, "cc-light.ico"))]
    else:
        targets = TARGETS

    any_done = False
    for src, out in targets:
        if os.path.exists(src):
            pack_ico(src, out)
            any_done = True
        else:
            print(f"skip (missing source): {src}")
    if not any_done:
        print("nothing packed — no source PNGs found")


if __name__ == "__main__":
    main(sys.argv[1:])
