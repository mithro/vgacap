#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw rtl/framer.svg and framer-dark.svg: the raw framer's ports, its
FIFO, and where a chunk that does not fit is dropped.

    uv run --no-project python rtl/framer.py

Data flows down the page: samples in at the top, chunk bytes out at the
foot. Ports are drawn at the module's edge. Both twins come from one palette.
"""
from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent

PALETTES = {
    "light": {"paper": "#ffffff", "ink": "#1d2433", "muted": "#5b6577",
              "data": "#1c64d4", "data_fill": "#eaf1fd", "out": "#14845a", "out_fill": "#e7f6ef",
              "drop": "#c0392b", "drop_fill": "#fdecea", "edge": "#7d8799"},
    "dark": {"paper": "#131416", "ink": "#e6e9ef", "muted": "#a0a8b8",
             "data": "#6ea2f5", "data_fill": "#16243b", "out": "#4cc38a", "out_fill": "#173326",
             "drop": "#f07a6a", "drop_fill": "#3a1a17", "edge": "#6b7385"},
}

W = 700
M = 2
FONT = "font-family=\"Inter, 'Segoe UI', Helvetica, Arial, sans-serif\""
MONO = ' font-family="ui-monospace, Menlo, Consolas, monospace"'


def draw(p):
    out = []
    add = out.append

    def text(x, y, s, size=13, fill=None, anchor="middle", weight="normal", mono=False):
        add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill or p["ink"]}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{MONO if mono else ""}>{escape(s)}</text>')

    def box(x, y, w, h, edge, fill, dash=False):
        d = ' stroke-dasharray="6 4"' if dash else ""
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" '
            f'stroke="{edge}" stroke-width="1.5"{d}/>')

    def arrow(points, colour, width=2.5):
        d = "M" + " L".join(f"{x} {y}" for x, y in points[:-1])
        (x1, y1), (x2, y2) = points[-2], points[-1]
        # shorten the last segment by the head, and point the head along it
        if x1 == x2:
            s = 1 if y2 > y1 else -1
            d += f" L{x2} {y2 - 8 * s}"
            head = f"M{x2 - 6} {y2 - 10 * s} L{x2} {y2} L{x2 + 6} {y2 - 10 * s} Z"
        else:
            s = 1 if x2 > x1 else -1
            d += f" L{x2 - 8 * s} {y2}"
            head = f"M{x2 - 10 * s} {y2 - 6} L{x2} {y2} L{x2 - 10 * s} {y2 + 6} Z"
        add(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}"/>')
        add(f'<path d="{head}" fill="{colour}"/>')

    # The module outline, between the input ports (top) and the output ports (foot).
    mx, my, mw = M + 10, M + 56, W - 2 * M - 20
    cx = mx + 200                              # the main column's centre
    bw = 260                                   # the main column's boxes
    bx = cx - bw / 2
    y_pack = my + 44
    y_admit = y_pack + 54 + 40
    y_fifo = y_admit + 54 + 40
    y_fsm = y_fifo + 64 + 40
    mh = y_fsm + 74 + 30 - my
    height = my + mh + 56 + M

    add(f'<rect x="0" y="0" width="{W}" height="{height}" fill="{p["paper"]}"/>')
    box(mx, my, mw, mh, p["edge"], "none")
    text(mx + mw - 14, my + 22, "vgacap_raw_framer", 14, p["muted"], "end", "bold", mono=True)

    # Input ports, above the module.
    text(cx - 40, M + 16, "sample[7:0]", 13, p["data"], weight="bold", mono=True)
    text(cx + 70, M + 16, "sample_valid", 13, p["data"], weight="bold", mono=True)
    arrow([(cx, M + 24), (cx, y_pack)], p["data"])

    box(bx, y_pack, bw, 54, p["data"], p["data_fill"])
    text(cx, y_pack + 23, "pack 4 samples", 14, weight="bold")
    text(cx, y_pack + 42, "one 32-bit word", 12, p["muted"])
    arrow([(cx, y_pack + 54), (cx, y_admit)], p["data"])

    box(bx, y_admit, bw, 54, p["data"], p["data_fill"])
    text(cx, y_admit + 23, "room for the whole chunk?", 14, weight="bold")
    text(cx, y_admit + 42, "checked at its first sample", 12, p["muted"])

    # The drop branch, to the right.
    dx, dw = bx + bw + 60, 190
    arrow([(bx + bw, y_admit + 27), (dx, y_admit + 27)], p["drop"])
    text((bx + bw + dx) / 2, y_admit + 19, "no", 12, p["drop"])
    box(dx, y_admit, dw, 54, p["drop"], p["drop_fill"])
    text(dx + dw / 2, y_admit + 23, "drop the chunk", 14, weight="bold")
    text(dx + dw / 2, y_admit + 42, "add its samples to the total", 12, p["muted"])
    # dropped[31:0] leaves at the module's right edge... drawn down to the foot.
    ddx = dx + dw / 2
    arrow([(ddx, y_admit + 54), (ddx, y_fsm)], p["drop"])
    text(ddx + 8, y_fifo + 34, "TIME chunk due", 12, p["drop"], "start")

    arrow([(cx, y_admit + 54), (cx, y_fifo)], p["data"])
    text(cx + 8, y_admit + 54 + 26, "yes", 12, p["data"], "start")

    box(bx, y_fifo, bw, 64, p["data"], p["data_fill"])
    text(cx, y_fifo + 25, "FIFO", 14, weight="bold")
    text(cx, y_fifo + 45, "fifo_words × 32 bits", 12, p["muted"], mono=False)
    arrow([(cx, y_fifo + 64), (cx, y_fsm)], p["data"])

    fw = dx + dw - bx
    box(bx, y_fsm, fw, 74, p["out"], p["out_fill"])
    text(bx + fw / 2, y_fsm + 26, "send one byte a clock", 14, weight="bold")
    text(bx + fw / 2, y_fsm + 47, "RAW header, words, then any TIME chunk", 12, p["muted"])

    # Output ports, below the module.
    foot = my + mh
    ox = cx
    arrow([(ox, y_fsm + 74), (ox, foot + 24)], p["out"])
    text(ox - 12, foot + 44, "out_data[7:0]", 13, p["out"], "end", "bold", mono=True)
    text(ox + 12, foot + 44, "out_valid", 13, p["out"], "start", "bold", mono=True)
    rx = ox + 150
    arrow([(rx, foot + 24), (rx, y_fsm + 74)], p["out"], 2)
    text(rx + 12, foot + 44, "out_ready", 13, p["out"], "start", "bold", mono=True)
    # dropped[31:0] leaves from the drop counter, down the right side to the foot.
    qx = dx + dw + 40
    arrow([(dx + dw, y_admit + 27), (qx, y_admit + 27), (qx, foot + 24)], p["drop"], 2)
    text(qx, foot + 44, "dropped[31:0]", 13, p["drop"], "middle", "bold", mono=True)

    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
            f'viewBox="0 0 {W} {height}" {FONT}>\n' + "\n".join(out) + "\n</svg>\n")


def main():
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        (HERE / f"framer{suffix}.svg").write_text(draw(PALETTES[theme]))


if __name__ == "__main__":
    main()
