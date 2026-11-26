#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw rtl/framer.svg and framer-dark.svg: the raw framer's ports, its
FIFO, and where a chunk that does not fit is dropped.

    uv run --no-project python rtl/framer.py

Data flows down the page: samples in at the top, chunk bytes and the
dropped count out at the foot. Ports are drawn at the module's edge. Narrow
enough to read at phone width. Both twins come from one palette.
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

W = 440
M = 2
FONT = "font-family=\"Inter, 'Segoe UI', Helvetica, Arial, sans-serif\""
MONO = ' font-family="ui-monospace, Menlo, Consolas, monospace"'


def draw(p):
    out = []
    add = out.append

    def text(x, y, s, size=14, fill=None, anchor="middle", weight="normal", mono=False):
        add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill or p["ink"]}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{MONO if mono else ""}>{escape(s)}</text>')

    def box(x, y, w, h, edge, fill):
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" '
            f'stroke="{edge}" stroke-width="1.5"/>')

    def arrow(points, colour, width=2.5):
        d = "M" + " L".join(f"{x} {y}" for x, y in points[:-1])
        (x1, y1), (x2, y2) = points[-2], points[-1]
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

    mx, my, mw = M, M + 44, W - 2 * M        # the module outline
    bx, bw = mx + 14, 190                     # the main column
    cx = bx + bw / 2
    dx, dw = bx + bw + 46, 150                # the drop column
    ddx = dx + dw / 2
    y_pack = my + 40
    y_admit = y_pack + 52 + 36
    y_fifo = y_admit + 52 + 36
    y_fsm = y_fifo + 52 + 36
    fw = ddx + 30 - bx                        # the send box, short of the dropped line
    qx = dx + dw - 16                         # the dropped line
    mh = y_fsm + 60 + 24 - my
    foot = my + mh
    height = foot + 58 + M

    add(f'<rect x="0" y="0" width="{W}" height="{height}" fill="{p["paper"]}"/>')
    box(mx, my, mw, mh, p["edge"], "none")
    text(mx + mw - 10, my + 20, "vgacap_raw_framer", 13, p["muted"], "end", "bold", mono=True)

    text(cx, M + 14, "sample[7:0]", 13, p["data"], weight="bold", mono=True)
    text(cx, M + 30, "sample_valid", 13, p["data"], weight="bold", mono=True)
    arrow([(cx, M + 36), (cx, y_pack)], p["data"])

    box(bx, y_pack, bw, 52, p["data"], p["data_fill"])
    text(cx, y_pack + 22, "pack 4 samples", weight="bold")
    text(cx, y_pack + 40, "one 32-bit word", 12, p["muted"])
    arrow([(cx, y_pack + 52), (cx, y_admit)], p["data"])

    box(bx, y_admit, bw, 52, p["data"], p["data_fill"])
    text(cx, y_admit + 22, "room for the chunk?", weight="bold")
    text(cx, y_admit + 40, "at its first sample", 12, p["muted"])

    arrow([(bx + bw, y_admit + 26), (dx, y_admit + 26)], p["drop"])
    text((bx + bw + dx) / 2, y_admit + 18, "no", 12, p["drop"])
    box(dx, y_admit, dw, 52, p["drop"], p["drop_fill"])
    text(ddx, y_admit + 22, "drop the chunk", weight="bold")
    text(ddx, y_admit + 40, "add to the total", 12, p["muted"])

    tx = dx + 30                              # TIME chunk due, into the send box
    arrow([(tx, y_admit + 52), (tx, y_fsm)], p["drop"])
    text(tx - 6, y_fifo + 22, "TIME", 12, p["drop"], "end")
    text(tx - 6, y_fifo + 38, "due", 12, p["drop"], "end")

    arrow([(cx, y_admit + 52), (cx, y_fifo)], p["data"])
    text(cx + 8, y_admit + 52 + 24, "yes", 12, p["data"], "start")

    box(bx, y_fifo, bw - 40, 52, p["data"], p["data_fill"])
    fcx = bx + (bw - 40) / 2
    text(fcx, y_fifo + 22, "FIFO", weight="bold")
    text(fcx, y_fifo + 40, "fifo_words", 12, p["muted"], mono=True)
    arrow([(fcx, y_fifo + 52), (fcx, y_fsm)], p["data"])

    box(bx, y_fsm, fw, 60, p["out"], p["out_fill"])
    text(bx + fw / 2, y_fsm + 25, "one byte a clock", weight="bold")
    text(bx + fw / 2, y_fsm + 44, "RAW chunks, then TIME", 12, p["muted"])

    arrow([(fcx, y_fsm + 60), (fcx, foot + 22)], p["out"])
    text(fcx, foot + 38, "out_data[7:0]", 13, p["out"], weight="bold", mono=True)
    text(fcx, foot + 54, "out_valid", 13, p["out"], weight="bold", mono=True)
    rx = fcx + 120
    arrow([(rx, foot + 22), (rx, y_fsm + 60)], p["out"], 2)
    text(rx, foot + 38, "out_ready", 13, p["out"], weight="bold", mono=True)

    arrow([(qx, y_admit + 52), (qx, foot + 22)], p["drop"], 2)
    text(W - M, foot + 38, "dropped[31:0]", 13, p["drop"], "end", "bold", mono=True)

    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
            f'viewBox="0 0 {W} {height}" {FONT}>\n' + "\n".join(out) + "\n</svg>\n")


def main():
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        (HERE / f"framer{suffix}.svg").write_text(draw(PALETTES[theme]))


if __name__ == "__main__":
    main()
