#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw docs/developing/test-layers.svg and test-layers-dark.svg: which part
of the board side of the capture path each kind of vgacap test runs as
shipped code, and which it replaces with a stand-in.

    uv run --no-project python docs/developing/test-layers.py

Drawn narrow, for a phone: two bands of two panels. In each panel the rows
are the board side of the capture path, top to bottom in the order the
data moves, and the cells say what the test puts there. The host side
(`ttcap` and `vgacap-frames`) is the shipped code in every kind of test, so
the page says it and the figure leaves it out. Both twins come from one
palette.
"""
from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent

PALETTES = {
    "light": {
        "paper": "#ffffff", "ink": "#1d2433", "muted": "#5b6577",
        "real": "#14845a", "real_fill": "#e7f6ef",
        "stand": "#c05a0c", "stand_fill": "#fff2e8",
        "flow": "#1c64d4", "rule": "#c9ced8",
    },
    "dark": {
        "paper": "#131416", "ink": "#e6e9ef", "muted": "#a0a8b8",
        "real": "#4cc38a", "real_fill": "#173326",
        "stand": "#f0904a", "stand_fill": "#3a2414",
        "flow": "#6ea2f5", "rule": "#3a3f48",
    },
}

#: The board side of the capture path, in the order the data moves.
ROWS = ["design", "PIO + DMA", "board code", "link"]

#: (title, where it runs, cells): one cell for each row, as (kind, label),
#: where kind is "real" (the shipped code or the real hardware) or "stand"
#: (a stand-in).
PANELS = [
    ("C tests", "CI", [
        ("stand", "synthetic"), ("stand", "simulated"), ("real", "firmware core"),
        ("stand", "in memory")]),
    ("emulated RP2", "CI", [
        ("stand", "synthetic"), ("stand", "emulated"), ("real", "MicroPython script"),
        ("stand", "modelled USB")]),
    ("simulated board", "CI", [
        ("stand", "synthetic"), ("stand", "simulated"), ("real", "firmware"),
        ("stand", "pipe")]),
    ("hardware-in-\nthe-loop check", "bench board", [
        ("real", "real design"), ("real", "RP2 PIO + DMA"), ("real", "board code"),
        ("real", "USB + bridge")]),
]

M = 2                   # margin: the drawing reaches the edges
LABEL_W = 92            # row labels, with the arrow
COL_W, COL_GAP = 140, 8
HEAD_H = 40             # a panel's one-line title and where it runs
LINE_H = 16             # each further line of a title
CELL_H, ROW_GAP = 30, 6
BAND_GAP = 18
LEGEND_H = 24
FONT = "font-family=\"Inter, 'Segoe UI', Helvetica, Arial, sans-serif\""


def draw(p):
    out = []
    add = out.append
    width = M + LABEL_W + 2 * COL_W + COL_GAP + M
    grid_h = len(ROWS) * CELL_H + (len(ROWS) - 1) * ROW_GAP
    # A band's head is as tall as its longest title.
    heads = [HEAD_H + LINE_H * max(PANELS[b * 2 + c][0].count("\n") for c in range(2))
             for b in range(2)]
    legend_y = M + sum(heads) + 2 * grid_h + BAND_GAP + 14
    height = legend_y + LEGEND_H + M

    def text(x, y, s, size: float = 12, fill=None, anchor="middle", weight="normal"):
        add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill or p["ink"]}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{escape(s)}</text>')

    def box(x, y, w, h, kind):
        dash = ' stroke-dasharray="5 3"' if kind == "stand" else ""
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{p[kind + "_fill"]}" '
            f'stroke="{p[kind]}" stroke-width="1.75"{dash}/>')

    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" {FONT}>')
    add(f'<rect x="0" y="0" width="{width}" height="{height}" fill="{p["paper"]}"/>')

    for band in range(2):
        top = M + band * (heads[0] + grid_h + BAND_GAP)
        grid_top = top + heads[band]
        if band:
            add(f'<line x1="{M}" y1="{top - BAND_GAP / 2}" x2="{width - M}" '
                f'y2="{top - BAND_GAP / 2}" stroke="{p["rule"]}" stroke-width="1"/>')
        # The data's direction, down the row labels.
        ax = M + 6
        text(M, grid_top - 8, "data", 11.5, p["flow"], anchor="start", weight="bold")
        add(f'<path d="M{ax} {grid_top + 4} V{grid_top + grid_h - 10}" stroke="{p["flow"]}" '
            f'stroke-width="2.5" fill="none"/>')
        add(f'<path d="M{ax - 5} {grid_top + grid_h - 13} L{ax} {grid_top + grid_h - 2} '
            f'L{ax + 5} {grid_top + grid_h - 13} Z" fill="{p["flow"]}"/>')
        for r, name in enumerate(ROWS):
            y = grid_top + r * (CELL_H + ROW_GAP)
            text(M + 18, y + CELL_H / 2 + 4, name, 12, anchor="start", weight="bold")
        for c in range(2):
            title, where, cells = PANELS[band * 2 + c]
            x = M + LABEL_W + c * (COL_W + COL_GAP)
            lines = title.split("\n")
            for i, line in enumerate(lines):
                text(x + COL_W / 2, grid_top - 26 - LINE_H * (len(lines) - 1 - i), line, 13,
                     weight="bold")
            text(x + COL_W / 2, grid_top - 10, where, 11.5, p["muted"])
            for r, (kind, label) in enumerate(cells):
                y = grid_top + r * (CELL_H + ROW_GAP)
                box(x, y, COL_W, CELL_H, kind)
                text(x + COL_W / 2, y + CELL_H / 2 + 4, label, 12)

    # The legend.
    x = M + LABEL_W
    for kind, label in (("real", "the real part"), ("stand", "a stand-in")):
        box(x, legend_y, 28, 20, kind)
        text(x + 36, legend_y + 14.5, label, 12, anchor="start")
        x += COL_W + COL_GAP
    add("</svg>")
    return "\n".join(out) + "\n"


def main():
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        (HERE / f"test-layers{suffix}.svg").write_text(draw(PALETTES[theme]), encoding="utf-8")


if __name__ == "__main__":
    main()
