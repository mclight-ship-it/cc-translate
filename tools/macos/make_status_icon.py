"""Generate menu-bar alpha templates from the existing CC smile, using Pillow.

Run python tools/macos/make_status_icon.py after editing assets/icon-dark.png.
Only asset regeneration needs Pillow; Mac builds copy the checked-in PNGs.
The colored Dock and Windows icons are not modified.
"""

from pathlib import Path

from PIL import Image, ImageChops, ImageOps


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "assets" / "icon-dark.png"
OUTPUT = ROOT / "assets" / "macos"
SIZES = {"CCTranslateStatusTemplate.png": 18, "CCTranslateStatusTemplate-2x.png": 36}


def template_images(source=SOURCE):
    with Image.open(source) as original:
        artwork = original.convert("RGBA")
    # The white glyphs have a high red channel; the blue tile and shadows do not.
    mask = artwork.getchannel("R").point(lambda value: 255 if value >= 128 else 0)
    mask = ImageChops.multiply(mask, artwork.getchannel("A"))
    bounds = mask.getbbox()
    if bounds is None:
        raise ValueError("CC smile artwork contains no foreground")
    mask = mask.crop(bounds)
    images = {}
    for name, pixels in SIZES.items():
        margin = pixels // 18
        glyph = ImageOps.contain(
            mask, (pixels - 2 * margin, pixels - 2 * margin), Image.Resampling.LANCZOS)
        alpha = Image.new("L", (pixels, pixels))
        alpha.paste(glyph, ((pixels - glyph.width) // 2, (pixels - glyph.height) // 2))
        image = Image.new("RGBA", (pixels, pixels))
        image.putalpha(alpha)
        images[name] = image
    return images


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, image in template_images().items():
        destination = OUTPUT / name
        image.save(destination, format="PNG")
        print(f"Wrote {destination.name}: {image.width}x{image.height}, black with alpha")


if __name__ == "__main__":
    main()
