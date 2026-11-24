"""Shield label + silkscreen textures, redrawn from the Seeed product photo (the STEP's label is a
simplified placeholder and the STEP has no silkscreen).

Run from site/: python3 tools/blender/textures.py  ->  assets/label.png, assets/silk.png
Layout numbers are millimetres in each texture's own frame (origin top-left, text-up = towards USB-C).
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent / "assets"
PX = 200  # pixels per mm
FONTS = Path("/usr/share/fonts/julietaula-montserrat-fonts")
INK = (24, 22, 22, 255)

LABEL_W, LABEL_H = 12.2, 10.2  # shield top, measured from the STEP (see build_scene.add_label)
PCB_W, PCB_H = 20.95, 17.78    # PCB outline: x -11.23..9.72, y -8.89..8.89


def mm(v):
    return round(v * PX)


def font(name, size_mm):
    return ImageFont.truetype(str(FONTS / name), mm(size_mm))


def text_box(d, box, s, f, fill=INK):
    """Draw s so its ink box fills box = (x0, y0, x1, y1) in mm exactly (sizes measured off the photo)."""
    l, t, r, b = d.textbbox((0, 0), s, font=f)
    tmp = Image.new("RGBA", (r - l, b - t), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((-l, -t), s, font=f, fill=fill)
    x0, y0, x1, y1 = box
    tmp = tmp.resize((mm(x1 - x0), mm(y1 - y0)), Image.LANCZOS)
    d._image.alpha_composite(tmp, (mm(x0), mm(y0)))


def arc(d, cx, cy, r_out, stroke, a0, a1, fill=INK):
    """Thick circular arc (degrees, PIL convention: 0 = +x, clockwise) with butt ends."""
    box = [mm(cx - r_out), mm(cy - r_out), mm(cx + r_out), mm(cy + r_out)]
    d.arc(box, a0, a1, fill=fill, width=mm(stroke))


def rect(d, x0, y0, x1, y1):
    d.rectangle([mm(x0), mm(y0), mm(x1), mm(y1)], fill=INK)


def label():
    """Every box below is measured in mm on the product photo's label, rectified flat with the
    camera pose solved from the photo (see match.py)."""
    im = Image.new("RGBA", (mm(LABEL_W), mm(LABEL_H)), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    text_box(d, (0.56, 0.15, 11.64, 1.24), "seeed studio", font("Montserrat-SemiBold.otf", 2.0))
    small = font("Montserrat-Medium.otf", 1.0)
    text_box(d, (0.64, 1.64, 10.4, 2.2), "Model:XIAO-ESP32-C5", small)
    text_box(d, (0.6, 2.56, 12.0, 3.2), "FCC ID:Z4T-XIAOESP32C5", small)

    # FCC logo: F, then a large C with a smaller C nested inside it
    s = 0.34
    rect(d, 0.76, 3.5, 0.76 + s, 7.2)
    rect(d, 0.76, 3.5, 2.6, 3.5 + s)
    rect(d, 0.76, 5.1, 2.3, 5.1 + s)
    arc(d, 3.3, 5.45, 1.8, s, 42, 318)
    arc(d, 3.55, 5.45, 1.0, s, 42, 318)

    # CE: each letter a little more than a half circle of diameter 4.8 mm, stroke 0.44 mm
    r, s = 2.4, 0.44
    arc(d, 7.8, 5.8, r, s, 85, 275)
    arc(d, 10.9, 5.8, r, s, 90, 270)
    rect(d, 10.9, 3.4, 11.5, 3.4 + s)
    rect(d, 10.9, 8.2 - s, 11.5, 8.2)
    rect(d, 8.7, 5.8 - s / 2, 11.2, 5.8 + s / 2)

    # Japanese MIC (telec) mark: circle, lightning stroke over a postal-mark-like T
    cx, cy, r, s = 2.1, 8.85, 1.2, 0.2
    arc(d, cx, cy, r, s, 15, 320)  # open at the upper right, where the lightning stroke exits
    rect(d, cx - 0.75 * r, cy - 0.02 * r, cx + 0.3 * r, cy - 0.02 * r + s)
    rect(d, cx - 0.75 * r, cy + 0.32 * r, cx + 0.3 * r, cy + 0.32 * r + s)
    rect(d, cx - 0.3 * r - s / 2, cy - 0.02 * r, cx - 0.3 * r + s / 2, cy + 0.85 * r)
    d.line([(mm(cx + 0.72 * r), mm(cy - 0.62 * r)), (mm(cx + 0.1 * r), mm(cy - 0.12 * r)),
            (mm(cx + 0.45 * r), mm(cy - 0.12 * r)), (mm(cx - 0.2 * r), mm(cy + 0.35 * r))],
           fill=INK, width=mm(s), joint="curve")

    d.rectangle([mm(3.56), mm(8.3), mm(4.74), mm(9.4)], outline=INK, width=mm(0.1))
    text_box(d, (3.82, 8.5, 4.48, 9.2), "R", small)
    text_box(d, (4.96, 8.36, 11.64, 9.4), "217-252850", small)
    im.save(ASSETS / "label.png")


def silk():
    """White silkscreen on a transparent PCB-sized canvas. Frame: x right = board +x, y down = board -y."""
    im = Image.new("RGBA", (mm(PCB_W), mm(PCB_H)), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    white = (235, 235, 228, 255)

    def at(bx, by):  # board mm -> texture mm
        return bx + 11.23, 8.89 - by

    f = font("Montserrat-Medium.otf", 1.0)
    # letters sit beside the parts they name, rotated like the label (text-up = +x)
    for s, (bx, by) in (("R", (-10.9, 7.85)), ("B", (-10.75, -6.35)), ("L", (6.9, -6.0))):  # glyph top-left corners
        glyph = Image.new("RGBA", (mm(1.2), mm(1.2)), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((mm(0.15), -mm(0.05)), s, font=f, fill=white)
        glyph = glyph.rotate(-90, expand=True)  # CW: text-up -> board +x, reading -> board -y
        x, y = at(bx, by)
        im.alpha_composite(glyph, (mm(x), mm(y)))
    # LED courtyard outline next to the USB-C
    x0, y0 = at(8.1, -5.95)
    x1, y1 = at(9.6, -6.95)
    d.rectangle([mm(x0), mm(y0), mm(x1), mm(y1)], outline=white, width=mm(0.12))
    im.save(ASSETS / "silk.png")


def fpc():
    """White print on the 40 x 20 mm FPC antenna, measured off Seeed's top-down product photo (cable exits right)."""
    im = Image.new("RGBA", (mm(40), mm(20)), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    white = (240, 240, 244, 255)
    text_box(d, (10.1, 2.0, 30.3, 4.4), "seeed studio", font("Montserrat-Bold.otf", 3.0), white)
    text_box(d, (8.6, 16.7, 31.8, 18.2), "seeedstudio.com   2.4G  A-01", font("Montserrat-Medium.otf", 1.6), white)
    im.save(ASSETS / "fpc.png")


if __name__ == "__main__":
    label()
    silk()
    fpc()
    print("wrote", ASSETS / "label.png", ASSETS / "silk.png", ASSETS / "fpc.png")
