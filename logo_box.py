#!/usr/bin/env python3
"""Center each logo on a fixed-size square canvas (default 350x350).

Background is chosen automatically per logo:
  * white background normally;
  * black background when the logo artwork is light/white (so it stays visible).

You can force one with --background white|black.

Needs Pillow:  pip install Pillow
(Optional) SVG support:  pip install cairosvg

Examples:
    python logo_box.py                         # process ./logos -> ./logos-boxed
    python logo_box.py --in logos --out boxed
    python logo_box.py mylogo.png              # one file
    python logo_box.py --size 350 --background white logos
"""
import argparse
import os
import sys

try:
    from PIL import Image, ImageChops
except ImportError:
    sys.exit("Pillow is required. Install it with:  pip install Pillow")

RASTER_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".avif"}
SVG_EXTS = {".svg"}

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def luminance(rgb):
    r, g, b = rgb[0], rgb[1], rgb[2]
    return 0.299 * r + 0.587 * g + 0.114 * b


def _mean_rgb(pixels):
    n = len(pixels)
    if not n:
        return (255, 255, 255)
    r = sum(p[0] for p in pixels) / n
    g = sum(p[1] for p in pixels) / n
    b = sum(p[2] for p in pixels) / n
    return (r, g, b)


def load_image(path):
    """Open an image as RGBA. Rasterize SVG via cairosvg if available."""
    ext = os.path.splitext(path)[1].lower()
    if ext in SVG_EXTS:
        try:
            import cairosvg
        except ImportError:
            raise RuntimeError(
                "SVG file needs cairosvg (pip install cairosvg) — skipped"
            )
        import io
        png_bytes = cairosvg.svg2png(url=path, output_width=1024, output_height=1024)
        return Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    return Image.open(path).convert("RGBA")


def _border_pixels(rgb_img, thickness=3):
    w, h = rgb_img.size
    px = rgb_img.load()
    t = max(1, min(thickness, w // 2, h // 2))
    out = []
    for y in range(h):
        for x in range(w):
            if x < t or x >= w - t or y < t or y >= h - t:
                out.append(px[x, y])
    return out


def choose_background(img):
    """Return WHITE or BLACK based on the logo's artwork / own background."""
    alpha = img.getchannel("A")
    w, h = img.size
    total = w * h
    a_px = alpha.load()
    rgb = img.convert("RGB")
    rgb_px = rgb.load()

    transparent = 0
    visible = []
    for y in range(h):
        for x in range(w):
            a = a_px[x, y]
            if a < 250:
                transparent += 1
            if a > 32:
                visible.append(rgb_px[x, y])

    transparent_frac = transparent / total if total else 0

    if transparent_frac > 0.05 and visible:
        # Cutout logo: decide by the artwork's own brightness.
        mean_lum = luminance(_mean_rgb(visible))
        return BLACK if mean_lum > 140 else WHITE

    # Opaque image: match the logo's existing background (its border color).
    border_lum = luminance(_mean_rgb(_border_pixels(rgb)))
    return BLACK if border_lum < 115 else WHITE


def trim(img, bg):
    """Crop away uniform margins so the logo fills the space when centered."""
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] < 250:
        # Has transparency — trim to the visible (non-transparent) region.
        bbox = alpha.getbbox()
    else:
        # Opaque — trim borders matching the background color.
        solid = Image.new("RGB", img.size, bg)
        diff = ImageChops.difference(img.convert("RGB"), solid)
        bbox = diff.getbbox()
    return img.crop(bbox) if bbox else img


def make_box(path, size, margin_frac, forced_bg):
    img = load_image(path)
    bg = forced_bg or choose_background(img)
    logo = trim(img, bg)

    inner = max(1, int(round(size * (1 - 2 * margin_frac))))
    lw, lh = logo.size
    scale = min(inner / lw, inner / lh)
    new_w = max(1, int(round(lw * scale)))
    new_h = max(1, int(round(lh * scale)))
    logo = logo.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (size, size), bg + (255,))
    pos = ((size - new_w) // 2, (size - new_h) // 2)
    canvas.alpha_composite(logo, pos)
    return canvas.convert("RGB"), bg


def iter_inputs(paths, in_dir):
    if paths:
        for p in paths:
            if os.path.isdir(p):
                yield from _dir_images(p)
            else:
                yield p
    else:
        yield from _dir_images(in_dir)


def _dir_images(d):
    if not os.path.isdir(d):
        sys.exit(f"Input folder not found: {d}")
    for name in sorted(os.listdir(d)):
        ext = os.path.splitext(name)[1].lower()
        if ext in RASTER_EXTS or ext in SVG_EXTS:
            yield os.path.join(d, name)


def parse_args(argv):
    p = argparse.ArgumentParser(description="Center logos on a fixed-size square canvas.")
    p.add_argument("paths", nargs="*", help="Image files or folders (default: --in folder).")
    p.add_argument("--in", dest="in_dir", default="logos",
                   help="Input folder of logos (default: logos).")
    p.add_argument("--out", default="logos-boxed",
                   help="Output folder (default: logos-boxed).")
    p.add_argument("--size", type=int, default=350, help="Canvas size in px (default: 350).")
    p.add_argument("--margin", type=float, default=0.12,
                   help="Empty margin as a fraction of size (default: 0.12).")
    p.add_argument("--background", choices=("auto", "white", "black"), default="auto",
                   help="Background: auto-detect (default), or force white/black.")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    forced = {"white": WHITE, "black": BLACK}.get(args.background)

    os.makedirs(args.out, exist_ok=True)
    made = skipped = 0
    for path in iter_inputs(args.paths, args.in_dir):
        name = os.path.splitext(os.path.basename(path))[0]
        out_path = os.path.join(args.out, name + ".png")
        try:
            boxed, bg = make_box(path, args.size, args.margin, forced)
            boxed.save(out_path)
            made += 1
            color = "black" if bg == BLACK else "white"
            print(f"  {os.path.basename(path)} -> {name}.png ({color} bg)")
        except Exception as e:
            skipped += 1
            print(f"  ! skipped {os.path.basename(path)}: {e}", file=sys.stderr)

    print(f"\nDone: {made} image(s) written to {args.out}/"
          + (f", {skipped} skipped" if skipped else ""))


if __name__ == "__main__":
    main()
