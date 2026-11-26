#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Make docs/diagrams/images/tt-etr-dbv3p2.jpg, the board in the overview figure.

    uv run --no-project --with pillow python docs/diagrams/prep_board.py SRC

SRC is Tiny Tapeout's KiCad render of the ETR demo board v3.2, without a
breakout fitted (so its RP2350 is visible):
https://github.com/TinyTapeout/tt-demo-pcb/blob/3b125feeac2bc38a9818803b32c25ffa5058b97b/doc/img/tt-etr-dbv3p2-render.png
(Apache-2.0; see NOTICE.md). The render has a transparent
background; it is flattened onto the figure's white and scaled down.
"""
import pathlib
import sys

from PIL import Image

src = Image.open(sys.argv[1]).convert("RGBA")
flat = Image.new("RGB", src.size, "#ffffff")
flat.paste(src, mask=src.getchannel("A"))
flat.thumbnail((420, 460), Image.LANCZOS)
dst = pathlib.Path(__file__).resolve().parent / "images" / "tt-etr-dbv3p2.jpg"
dst.parent.mkdir(exist_ok=True)
flat.save(dst, quality=88, optimize=True)
print("wrote", dst, flat.size, dst.stat().st_size, "bytes")
