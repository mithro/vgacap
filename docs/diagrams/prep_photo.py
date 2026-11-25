#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Make docs/diagrams/photos/etr-demoboard.jpg from the Tiny Tapeout photo.

    uv run --no-project --with pillow --with numpy python docs/diagrams/prep_photo.py SRC

SRC is tt-micropython-firmware's images/etr-demoboard-bootbutton.jpg (see
logos/NOTICE.md). That photo carries a green box drawn around the BOOT
button; its pixels are filled from their surroundings so the board reads
as a plain photo, then it is scaled down for embedding in the figure.
"""
import pathlib
import sys

import numpy as np
from PIL import Image, ImageFilter

src = pathlib.Path(sys.argv[1])
img = Image.open(src).convert("RGB")
a = np.asarray(img).astype(np.int16)
r, g, b = a[..., 0], a[..., 1], a[..., 2]
green = (g > 150) & (r < 120) & (b < 120) & (g - r > 80) & (g - b > 80)
# Grow the mask a little to take the anti-aliased edge with it.
mask = Image.fromarray((green * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9))
m = np.asarray(mask) > 0
print("green pixels:", int(green.sum()), "masked:", int(m.sum()))

# Fill masked pixels by repeatedly averaging their unmasked neighbours.
out = a.astype(np.float32)
known = ~m
for _ in range(60):
    if known.all():
        break
    acc = np.zeros_like(out)
    cnt = np.zeros(known.shape, np.float32)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
        k = np.roll(np.roll(known, dy, 0), dx, 1)
        v = np.roll(np.roll(out, dy, 0), dx, 1)
        acc += v * k[..., None]
        cnt += k
    fill = (~known) & (cnt > 0)
    out[fill] = acc[fill] / cnt[fill][..., None]
    known = known | fill

clean = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
clean.thumbnail((360, 360), Image.LANCZOS)
dst = pathlib.Path(__file__).resolve().parent / "photos" / "etr-demoboard.jpg"
dst.parent.mkdir(exist_ok=True)
clean.save(dst, quality=84, optimize=True)
print("wrote", dst, clean.size, dst.stat().st_size, "bytes")
