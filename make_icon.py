#!/usr/bin/env python3
"""Generate PDFEdit.icns (macOS) and PDFEdit.ico (Windows) from a single
1024×1024 master rendered with PIL.

Design: flat squircle in the app's accent-blue palette, white document with
PDF wordmark, subtle pencil glyph crossing the bottom-right corner. No
glossy 2010-era highlights — matches the app's modern dark/blue chrome.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
OUT_ICONSET = HERE / "PDFEdit.iconset"
OUT_ICNS = HERE / "PDFEdit.icns"
OUT_ICO = HERE / "PDFEdit.ico"
OUT_MASTER = HERE / "icon_master.png"

MASTER = 1024
RADIUS_RATIO = 0.225  # macOS squircle radius


def find_bold_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _vertical_gradient(size: int, top: tuple, bot: tuple) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / size
        r = int(top[0] * (1 - t) + bot[0] * t)
        g = int(top[1] * (1 - t) + bot[1] * t)
        b = int(top[2] * (1 - t) + bot[2] * t)
        d.line([(0, y), (size, y)], fill=(r, g, b, 255))
    return img


def draw_master() -> Image.Image:
    s = MASTER
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Background squircle gradient — matches the app's accent palette.
    # Deep navy at the top → bright accent blue at the bottom.
    grad = _vertical_gradient(s, top=(30, 58, 138), bot=(59, 130, 246))
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, s, s], radius=int(s * RADIUS_RATIO), fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)

    # Document silhouette with folded corner.
    doc_w = int(s * 0.58)
    doc_h = int(s * 0.66)
    doc_x = (s - doc_w) // 2
    doc_y = int(s * 0.16)
    fold = int(doc_w * 0.22)

    # Soft shadow under the doc.
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        [doc_x + 12, doc_y + 22, doc_x + doc_w + 12, doc_y + doc_h + 22],
        radius=18,
        fill=(0, 0, 0, 110),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(20))
    img = Image.alpha_composite(img, shadow)
    d = ImageDraw.Draw(img)

    # Page polygon with a clipped top-right corner (folded look).
    page = [
        (doc_x, doc_y),
        (doc_x + doc_w - fold, doc_y),
        (doc_x + doc_w, doc_y + fold),
        (doc_x + doc_w, doc_y + doc_h),
        (doc_x, doc_y + doc_h),
    ]
    d.polygon(page, fill=(255, 255, 255, 255))

    # Folded-corner triangle a touch darker than the page.
    d.polygon(
        [
            (doc_x + doc_w - fold, doc_y),
            (doc_x + doc_w - fold, doc_y + fold),
            (doc_x + doc_w, doc_y + fold),
        ],
        fill=(225, 229, 235, 255),
    )
    d.line(
        [
            (doc_x + doc_w - fold, doc_y),
            (doc_x + doc_w - fold, doc_y + fold),
            (doc_x + doc_w, doc_y + fold),
        ],
        fill=(180, 190, 205, 255),
        width=4,
    )

    # PDF wordmark in the accent blue, centered on the page.
    font = find_bold_font(int(doc_w * 0.34))
    text = "PDF"
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = doc_x + (doc_w - tw) // 2 - bbox[0]
    ty = doc_y + int(doc_h * 0.34) - bbox[1]
    d.text((tx, ty), text, fill=(37, 99, 235, 255), font=font)

    # Three faint content lines below the wordmark — a subtle nod to a doc.
    line_y0 = doc_y + int(doc_h * 0.62)
    line_h = int(doc_h * 0.05)
    line_l = doc_x + int(doc_w * 0.18)
    line_r = doc_x + int(doc_w * 0.82)
    line_color = (200, 210, 225, 255)
    for i in range(3):
        y = line_y0 + i * (line_h + 14)
        # The third line is shorter — variety helps the icon read at small sizes.
        right = line_r - (int(doc_w * 0.18) if i == 2 else 0)
        d.rounded_rectangle(
            [line_l, y, right, y + line_h],
            radius=line_h // 2,
            fill=line_color,
        )

    # --- Pencil — flat, modern, drawn at -28° crossing the bottom-right ---
    pen_w, pen_h = 700, 100
    pen = Image.new("RGBA", (pen_w, pen_h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pen)

    eraser_l = 90
    ferrule_l = 50
    tip_l = 90
    lead_l = 25
    body_end = pen_w - tip_l - lead_l
    body_start = eraser_l + ferrule_l

    # Eraser — soft coral, restrained.
    pd.rounded_rectangle([0, 0, eraser_l, pen_h], radius=18, fill=(244, 113, 116, 255))
    # Metal ferrule.
    pd.rectangle([eraser_l, 0, eraser_l + ferrule_l, pen_h], fill=(186, 196, 209, 255))
    # Pencil body — accent blue, not yellow.
    pd.rectangle([body_start, 0, body_end, pen_h], fill=(37, 99, 235, 255))
    # Wood tip — warm cream.
    pd.polygon(
        [(body_end, 0), (body_end, pen_h), (body_end + tip_l, pen_h // 2)],
        fill=(245, 222, 175, 255),
    )
    # Graphite tip.
    g_half = 12
    pd.polygon(
        [
            (body_end + tip_l, pen_h // 2 - g_half),
            (body_end + tip_l, pen_h // 2 + g_half),
            (pen_w, pen_h // 2),
        ],
        fill=(31, 41, 55, 255),
    )

    # Soft shadow underneath the pencil.
    pen_shadow = Image.new("RGBA", pen.size, (0, 0, 0, 0))
    ps = ImageDraw.Draw(pen_shadow)
    ps.rectangle([0, 0, pen_w, pen_h], fill=(0, 0, 0, 90))
    pen_shadow = pen_shadow.filter(ImageFilter.GaussianBlur(10))

    angle = -28
    pen_rot = pen.rotate(angle, expand=True, resample=Image.BICUBIC)
    sh_rot = pen_shadow.rotate(angle, expand=True, resample=Image.BICUBIC)

    tip_target = (doc_x + int(doc_w * 0.93), doc_y + int(doc_h * 0.93))
    place_x = tip_target[0] - pen_rot.width + 28
    place_y = tip_target[1] - pen_rot.height // 2 + 8

    img.alpha_composite(sh_rot, (place_x + 12, place_y + 16))
    img.alpha_composite(pen_rot, (place_x, place_y))

    return img


def build_iconset(master: Image.Image) -> None:
    if OUT_ICONSET.exists():
        shutil.rmtree(OUT_ICONSET)
    OUT_ICONSET.mkdir()
    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    for size, name in sizes:
        master.resize((size, size), Image.LANCZOS).save(OUT_ICONSET / name)


def make_icns() -> bool:
    if shutil.which("iconutil") is None:
        # Non-mac host — skip silently.
        return False
    subprocess.run(
        ["iconutil", "-c", "icns", str(OUT_ICONSET), "-o", str(OUT_ICNS)],
        check=True,
    )
    return True


def make_ico(master: Image.Image) -> None:
    # PIL's .ico writer accepts a sizes= list and produces a multi-resolution
    # Windows icon. 256 is the max for ICO; bigger sizes get downscaled.
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.resize((256, 256), Image.LANCZOS).save(
        OUT_ICO, format="ICO", sizes=sizes
    )


def main() -> None:
    master = draw_master()
    master.save(OUT_MASTER)
    build_iconset(master)
    icns_ok = make_icns()
    make_ico(master)
    if icns_ok:
        print(f"Wrote {OUT_ICNS} and {OUT_ICO}")
    else:
        print(f"Wrote {OUT_ICO} (skipped .icns — iconutil not available)")


if __name__ == "__main__":
    main()
