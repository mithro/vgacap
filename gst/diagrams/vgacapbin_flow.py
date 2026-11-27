#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw vgacapbin-flow.svg and vgacapbin-flow-dark.svg: the data's way
through `vgacapbin` in a live pipeline, as gst/gstvgacapbin.c,
gst/gstvgacapttsrc.c and gst/gstvgadecode.c build it.

    uv run --no-project python gst/diagrams/vgacapbin_flow.py

The data flows down: the demo board, the `ttcap capture --out -` child
that `vgacapttsrc` runs, its stdout pipe, the 64 KiB buffers the source
pushes as application/x-vgacap, `vgadecode`, and RGB video out of the
bin to the rest of the pipeline. Nesting shows what owns what. The
colours are those of the other figures: blue for the design's signals,
orange for captured data, purple for ttcap, green for pictures.
"""
from __future__ import annotations

import math
import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent

THEMES = {
    "light": {
        "paper": "#ffffff", "ink": "#1d2433", "muted": "#5b6577",
        "sig_fill": "#eaf1fd", "sig_edge": "#9dbcef",
        "str": "#e0580a",
        "ctl": "#6b46d6", "ctl_fill": "#f1ecfd", "ctl_edge": "#c2b1f1",
        "pic": "#14845a",
        "bin_fill": "#f3f5f9", "bin_edge": "#9aa3b5",
        "part": "#ffffff", "part_edge": "#7d8799",
    },
    "dark": {
        "paper": "#131722", "ink": "#e6e9ef", "muted": "#9aa3b5",
        "sig_fill": "#182640", "sig_edge": "#34558f",
        "str": "#ff8c45",
        "ctl": "#a98bff", "ctl_fill": "#251d40", "ctl_edge": "#5a47a0",
        "pic": "#4cc38a",
        "bin_fill": "#1b1e24", "bin_edge": "#5d6578",
        "part": "#1c2230", "part_edge": "#5b6577",
    },
}

FONT = "font-family=\"'DejaVu Sans', Verdana, Arial, sans-serif\""
W, H = 400, 612
CX = W / 2


def draw(theme):
    c = THEMES[theme]
    out = []
    add = out.append

    def rect(x, y, w, h, fill, edge, rx=7, dash=False):
        d = ' stroke-dasharray="6 4"' if dash else ""
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{edge}" stroke-width="1.5"{d}/>')

    def text(x, y, s, size: float = 14, fill=None, anchor="middle", weight="normal",
             mono=False):
        family = ' font-family="ui-monospace, Menlo, Consolas, monospace"' if mono else ""
        add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill or c["ink"]}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{family}>{escape(s)}</text>')

    def down(y1, y2, colour, label=None, width: float = 3):
        add(f'<path d="M{CX} {y1} V{y2}" stroke="{colour}" stroke-width="{width}" fill="none"/>')
        s = 5 + 2 * width
        a = 0.45
        add(f'<path d="M{CX} {y2} L{CX - s * math.sin(a):.1f} {y2 - s * math.cos(a):.1f} '
            f'L{CX + s * math.sin(a):.1f} {y2 - s * math.cos(a):.1f} Z" fill="{colour}"/>')
        if label:
            text(CX + 12, (y1 + y2) / 2 + 5, label, 13, colour, "start")

    # The demo board.
    rect(CX - 140, 2, 280, 44, c["sig_fill"], c["sig_edge"], rx=8)
    text(CX, 30, "Tiny Tapeout demo board", 15, weight="bold")

    # vgacapbin, holding the source and the decoder.
    rect(2, 100, W - 4, 396, c["bin_fill"], c["bin_edge"], rx=10, dash=True)
    text(16, 122, "vgacapbin", 15, c["muted"], "start", "bold", mono=True)
    text(CX + 12, 78, "USB or bridge", 13, c["str"], "start")

    # vgacapttsrc, running ttcap as its child.
    rect(32, 136, W - 64, 230, c["part"], c["part_edge"], rx=8)
    text(46, 158, "vgacapttsrc", 14, anchor="start", weight="bold", mono=True)
    down(46, 170, c["str"])   # drawn over the two boxes it enters
    rect(CX - 120, 172, 240, 48, c["ctl_fill"], c["ctl_edge"])
    text(CX, 193, "ttcap capture --out -", 13.5, c["ctl"], weight="bold", mono=True)
    text(CX, 211, "child process", 12, c["muted"])
    down(220, 290, c["str"], "stdout pipe", 2.5)
    rect(CX - 120, 292, 240, 48, c["part"], c["part_edge"])
    text(CX, 321, "64 KiB buffers", 14, weight="bold")

    down(366, 418, c["str"], "application/x-vgacap")
    rect(CX - 120, 420, 240, 48, c["part"], c["part_edge"], rx=8)
    text(CX, 449, "vgadecode", 14, weight="bold", mono=True)

    down(468, 564, c["pic"], "video/x-raw, RGB")
    rect(CX - 140, 566, 280, 44, c["part"], c["part_edge"], rx=8)
    text(CX, 593, "videoconvert ! autovideosink", 13.5, weight="bold", mono=True)

    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}" {FONT}>\n'
            f'<rect x="0" y="0" width="{W}" height="{H}" fill="{c["paper"]}"/>')
    return head + "\n" + "\n".join(out) + "\n</svg>\n"


def main():
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        (HERE / f"vgacapbin-flow{suffix}.svg").write_text(draw(theme), encoding="utf-8")


if __name__ == "__main__":
    main()
