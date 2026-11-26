#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw docs/diagrams/vga-timing.svg and vga-timing-dark.svg: one VGA frame
as libvgaframe lays it out, 640x480@60.

    uv run --no-project python docs/diagrams/vga_timing.py

A line starts at the leading edge of its hsync pulse and a frame at the
leading edge of its vsync pulse, so each axis runs sync, back porch,
active, front porch. The hsync waveform runs along the top, the vsync
waveform down the left, and the active area is the picture. Widths are
to scale except the thin parts, which are widened to stay readable; the
numbers are the mode's, from src/frame/modes.c.
"""
from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent

THEMES = {
    "": dict(BG="#ffffff", INK="#1d2433", MUTED="#5b6577", SIG="#1c64d4",
             BLANK="#eef0f4", BLANK_EDGE="#c9ced8", SYNC="#dbe7fb", ACTIVE="#e7f6ef",
             ACTIVE_EDGE="#14845a", PIC="#14845a"),
    "-dark": dict(BG="#16181d", INK="#e6e9ef", MUTED="#a3acbd", SIG="#6aa5ff",
                  BLANK="#232834", BLANK_EDGE="#454c5c", SYNC="#1e3150", ACTIVE="#10291f",
                  ACTIVE_EDGE="#3fcf91", PIC="#3fcf91"),
}
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

# 640x480@60: sync, back porch, active, front porch.
H = (("sync", 96), ("back porch", 48), ("active", 640), ("front porch", 16))
V = (("sync", 2), ("back porch", 33), ("active", 480), ("front porch", 10))
SCALE = 0.62      # px a clock, and px a line
MIN = 46          # thinnest band drawn


def widths(parts):
    return [max(MIN, n * SCALE) for _, n in parts]


def draw(p):
    out = []
    add = out.append

    def text(x, y, s, size=12.5, weight=400, fill=None, anchor="middle", rotate=False):
        r = f' transform="rotate(-90 {x} {y})"' if rotate else ""
        add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill or p["INK"]}" text-anchor="{anchor}"{r}>'
            f'{escape(s)}</text>')

    def rect(x, y, w, h, fill, edge, sw=1):
        add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" '
            f'stroke="{edge}" stroke-width="{sw}"/>')

    hw, vw = widths(H), widths(V)
    WAVE = 40         # the waveform strip along each axis
    LABEL = 34        # the band names and counts beside the grid
    gx = 2 + WAVE + LABEL           # the grid's left edge
    gy = 2 + WAVE + LABEL           # the grid's top edge
    xs = [gx]
    for w in hw:
        xs.append(xs[-1] + w)
    ys = [gy]
    for h in vw:
        ys.append(ys[-1] + h)

    # The grid: every band blank, the sync rows and columns tinted, the active area green.
    for i in range(4):
        for j in range(4):
            sync = i == 0 or j == 0
            active = i == 2 and j == 2
            fill = p["ACTIVE"] if active else p["SYNC"] if sync else p["BLANK"]
            rect(xs[i], ys[j], xs[i + 1] - xs[i], ys[j + 1] - ys[j], fill, p["BLANK_EDGE"])
    rect(xs[2], ys[2], xs[3] - xs[2], ys[3] - ys[2], p["ACTIVE"], p["ACTIVE_EDGE"], 2)
    cx, cy = (xs[2] + xs[3]) / 2, (ys[2] + ys[3]) / 2
    text(cx, cy - 6, "active area", 18, 700, p["PIC"])
    text(cx, cy + 18, "640 x 480: the picture", 14, 400, p["PIC"])

    # Band names along the top and down the left, with their sizes.
    for i, (name, n) in enumerate(H):
        mx = (xs[i] + xs[i + 1]) / 2
        short = {"back porch": "back", "front porch": "front"}.get(name, name) \
            if xs[i + 1] - xs[i] < 60 else name
        text(mx, gy - LABEL + 13, short, 12, 600)
        text(mx, gy - 6, f"{n} clocks" if xs[i + 1] - xs[i] >= 60 else str(n), 11.5, 400,
             p["MUTED"])
    for j, (name, n) in enumerate(V):
        my = (ys[j] + ys[j + 1]) / 2
        text(gx - LABEL + 13, my, name if ys[j + 1] - ys[j] >= 60 else
             {"back porch": "back", "front porch": "front"}.get(name, name), 12, 600,
             rotate=True)
        text(gx - 8, my, f"{n} lines" if ys[j + 1] - ys[j] >= 60 else str(n), 11.5, 400,
             p["MUTED"], rotate=True)

    # hsync along the top: high, low for the pulse (negative in this mode), high.
    hi, lo = 2 + 8, 2 + WAVE - 8
    # Each waveform starts a little before the grid, at the idle level, so
    # the falling edge that starts the line or the frame is drawn too.
    LEAD = 12
    path = (f"M{xs[0] - LEAD:.1f} {hi} L{xs[0]:.1f} {hi} L{xs[0]:.1f} {lo} "
            f"L{xs[1]:.1f} {lo} L{xs[1]:.1f} {hi} L{xs[4]:.1f} {hi}")
    add(f'<path d="{path}" fill="none" stroke="{p["SIG"]}" stroke-width="2.5"/>')
    text(gx - LEAD - 6, (hi + lo) / 2 + 4, "hsync", 12.5, 700, p["SIG"], "end")
    vl, vh = 2 + 8, 2 + WAVE - 8
    path = (f"M{vl} {ys[0] - LEAD:.1f} L{vl} {ys[0]:.1f} L{vh} {ys[0]:.1f} "
            f"L{vh} {ys[1]:.1f} L{vl} {ys[1]:.1f} L{vl} {ys[4]:.1f}")
    add(f'<path d="{path}" fill="none" stroke="{p["SIG"]}" stroke-width="2.5"/>')
    text((vl + vh) / 2, gy - LEAD - 10, "vsync", 12.5, 700, p["SIG"], "middle")

    # Totals, under the grid and to its right.
    text((xs[0] + xs[4]) / 2, ys[4] + 20, "one line: 800 clocks", 13, 600)
    text(xs[4] + 16, (ys[0] + ys[4]) / 2, "one frame: 525 lines", 13, 600, rotate=True)

    w, h = xs[4] + 16 + 6, ys[4] + 20 + 5
    head = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
            f'viewBox="0 0 {w:.0f} {h:.0f}" role="img" aria-labelledby="t">',
            '<title id="t">One 640x480@60 VGA frame: sync, porches and the active area</title>',
            f'<rect x="0" y="0" width="{w:.0f}" height="{h:.0f}" fill="{p["BG"]}"/>']
    return head + out + ["</svg>"]


for suffix, palette in THEMES.items():
    svg = HERE / f"vga-timing{suffix}.svg"
    svg.write_text("\n".join(draw(palette)) + "\n")
    print("wrote", svg, svg.stat().st_size, "bytes")
