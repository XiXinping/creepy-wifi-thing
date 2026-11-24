"""PNG frames -> WebP sequences for the site. Run from site/: python3 tools/blender/encode.py [--preview]"""
import json
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "public" / "renders"
suffix = "-preview" if "--preview" in sys.argv else ""
SIZES = {"lg": 1920, "sm": 960}  # sm for phones / narrow viewports

for shot in ("hero", "rise"):
    frames = sorted((HERE / "assets" / "frames" / (shot + suffix)).glob("*.png"))
    if not frames:
        continue
    total = 0
    for size, width in SIZES.items():
        d = OUT / shot / size
        d.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(frames):
            im = Image.open(f)
            if im.width != width:
                im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
            dst = d / f"{i:04d}.webp"
            im.save(dst, "WEBP", quality=80, method=6, alpha_quality=90)
            total += dst.stat().st_size
    (OUT / f"{shot}.frames.json").write_text(json.dumps({"count": len(frames), "sizes": SIZES}))
    print(f"{shot}: {len(frames)} frames, {total / 1e6:.1f} MB")
