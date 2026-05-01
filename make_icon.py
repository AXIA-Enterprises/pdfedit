#!/usr/bin/env python3
"""Generate PDFEdit.icns — a clean PDF-editor app icon."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
OUT_ICONSET = HERE / "PDFEdit.iconset"
OUT_ICNS = HERE / "PDFEdit.icns"

# macOS rounded-rect "squircle" corner radius is ~22.5% of side at 1024.
MASTER = 1024


def find_bold_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def draw_master() -> Image.Image:
    s = MASTER
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Background: rounded-square gradient (warm red → deep red), classic PDF feel.
    grad = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    top = (235, 87, 70)        # warm coral
    bot = (188, 35, 35)        # deep red
    for y in range(s):
        t = y / s
        r = int(top[0] * (1 - t) + bot[0] * t)
        g = int(top[1] * (1 - t) + bot[1] * t)
        b = int(top[2] * (1 - t) + bot[2] * t)
        gd.line([(0, y), (s, y)], fill=(r, g, b, 255))

    # Mask: rounded square (squircle-ish)
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, s, s], radius=int(s * 0.225), fill=255)
    img.paste(grad, (0, 0), mask)

    # Glossy highlight along top edge
    hl = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hd.ellipse(
        [-int(s * 0.2), -int(s * 0.7), int(s * 1.2), int(s * 0.55)],
        fill=(255, 255, 255, 50),
    )
    hl_blur = hl.filter(ImageFilter.GaussianBlur(20))
    img = Image.alpha_composite(img, Image.composite(hl_blur, Image.new("RGBA", (s, s), (0, 0, 0, 0)), mask))

    d = ImageDraw.Draw(img)

    # Document silhouette with folded corner
    doc_w = int(s * 0.56)
    doc_h = int(s * 0.66)
    doc_x = (s - doc_w) // 2
    doc_y = int(s * 0.16)
    fold = int(doc_w * 0.24)

    # Soft drop shadow under doc
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        [doc_x + 14, doc_y + 24, doc_x + doc_w + 14, doc_y + doc_h + 24],
        radius=22,
        fill=(0, 0, 0, 110),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    img = Image.alpha_composite(img, shadow)
    d = ImageDraw.Draw(img)

    # Main page polygon (fold cut at top-right)
    page = [
        (doc_x, doc_y),
        (doc_x + doc_w - fold, doc_y),
        (doc_x + doc_w, doc_y + fold),
        (doc_x + doc_w, doc_y + doc_h),
        (doc_x, doc_y + doc_h),
    ]
    d.polygon(page, fill=(255, 255, 255, 255))

    # Folded-corner triangle (slightly darker)
    d.polygon(
        [
            (doc_x + doc_w - fold, doc_y),
            (doc_x + doc_w - fold, doc_y + fold),
            (doc_x + doc_w, doc_y + fold),
        ],
        fill=(228, 228, 228, 255),
    )
    # Fold edge line
    d.line(
        [
            (doc_x + doc_w - fold, doc_y),
            (doc_x + doc_w - fold, doc_y + fold),
            (doc_x + doc_w, doc_y + fold),
        ],
        fill=(190, 190, 190, 255),
        width=4,
    )

    # "PDF" text in red
    font = find_bold_font(int(doc_w * 0.34))
    text = "PDF"
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = doc_x + (doc_w - tw) // 2 - bbox[0]
    ty = doc_y + int(doc_h * 0.40) - bbox[1]
    d.text((tx, ty), text, fill=(190, 35, 35, 255), font=font)

    # --- Pencil (drawn horizontal, rotated, pasted near doc bottom-right) ---
    pen_w, pen_h = 720, 110
    pen = Image.new("RGBA", (pen_w, pen_h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pen)

    # Geometry along the length: eraser | ferrule | body | wood tip | lead
    eraser_l = 90
    ferrule_l = 55
    tip_l = 95          # wood section
    lead_l = 25         # graphite tip
    body_end = pen_w - tip_l - lead_l
    body_start = eraser_l + ferrule_l

    # Eraser (pink, rounded)
    pd.rounded_rectangle(
        [0, 0, eraser_l, pen_h], radius=20, fill=(232, 132, 138, 255)
    )
    # Ferrule (metal band) with stripes
    pd.rectangle([eraser_l, 0, eraser_l + ferrule_l, pen_h], fill=(190, 190, 190, 255))
    pd.line(
        [(eraser_l, pen_h * 0.32), (eraser_l + ferrule_l, pen_h * 0.32)],
        fill=(140, 140, 140, 255),
        width=3,
    )
    pd.line(
        [(eraser_l, pen_h * 0.68), (eraser_l + ferrule_l, pen_h * 0.68)],
        fill=(140, 140, 140, 255),
        width=3,
    )
    # Body (yellow)
    pd.rectangle([body_start, 0, body_end, pen_h], fill=(255, 198, 56, 255))
    # Wood tip (cream triangle)
    pd.polygon(
        [(body_end, 0), (body_end, pen_h), (body_end + tip_l, pen_h // 2)],
        fill=(245, 222, 175, 255),
    )
    # Graphite (black tip)
    g_half = 14
    pd.polygon(
        [
            (body_end + tip_l, pen_h // 2 - g_half),
            (body_end + tip_l, pen_h // 2 + g_half),
            (pen_w, pen_h // 2),
        ],
        fill=(35, 35, 35, 255),
    )
    # Outlines
    pd.rounded_rectangle(
        [0, 0, eraser_l, pen_h], radius=20, outline=(150, 80, 90, 255), width=4
    )
    pd.rectangle(
        [eraser_l, 0, eraser_l + ferrule_l, pen_h],
        outline=(120, 120, 120, 255),
        width=4,
    )
    pd.rectangle(
        [body_start, 0, body_end, pen_h], outline=(170, 110, 0, 255), width=4
    )
    pd.polygon(
        [(body_end, 0), (body_end, pen_h), (body_end + tip_l, pen_h // 2)],
        outline=(170, 110, 0, 255),
        width=4,
    )

    # Drop shadow under the pencil
    pen_shadow = Image.new("RGBA", pen.size, (0, 0, 0, 0))
    ps = ImageDraw.Draw(pen_shadow)
    ps.rectangle([0, 0, pen_w, pen_h], fill=(0, 0, 0, 110))
    pen_shadow = pen_shadow.filter(ImageFilter.GaussianBlur(10))

    angle = -28
    pen_rot = pen.rotate(angle, expand=True, resample=Image.BICUBIC)
    sh_rot = pen_shadow.rotate(angle, expand=True, resample=Image.BICUBIC)

    # Anchor: tip lands near bottom-right of the page, pencil extends down-left across it
    tip_target = (doc_x + int(doc_w * 0.92), doc_y + int(doc_h * 0.92))
    # rotated tip position is roughly (pen_rot.width, pen_rot.height/2 + offset for rotation)
    # easier: just align the right-center of rotated bbox to the target
    place_x = tip_target[0] - pen_rot.width + 30
    place_y = tip_target[1] - pen_rot.height // 2 + 10

    img.alpha_composite(sh_rot, (place_x + 14, place_y + 18))
    img.alpha_composite(pen_rot, (place_x, place_y))

    return img


def build_iconset(master: Image.Image):
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


def make_icns():
    subprocess.run(
        ["iconutil", "-c", "icns", str(OUT_ICONSET), "-o", str(OUT_ICNS)],
        check=True,
    )


def main():
    master = draw_master()
    master.save(HERE / "icon_master.png")
    build_iconset(master)
    make_icns()
    print(f"Wrote {OUT_ICNS}")


if __name__ == "__main__":
    main()
