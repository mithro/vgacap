#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw docs/developing/test-layers.svg and test-layers-dark.svg: which part
of the capture path each kind of vgacap test runs as shipped code, which it
replaces with a stand-in, and which it leaves out.

    uv run --no-project python docs/developing/test-layers.py

The rows are the capture path, top to bottom in the order the data moves.
The columns are the four kinds of test. Both twins come from one palette.
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
        "none": "#a3aab8", "flow": "#1c64d4", "rule": "#c9ced8",
    },
    "dark": {
        "paper": "#131416", "ink": "#e6e9ef", "muted": "#a0a8b8",
        "real": "#4cc38a", "real_fill": "#173326",
        "stand": "#f0904a", "stand_fill": "#3a2414",
        "none": "#5d6574", "flow": "#6ea2f5", "rule": "#3a3f48",
    },
}

#: The capture path, in the order the data moves.
ROWS = ["design uo_out", "PIO + DMA", "board code", "link to host", "ttcap", "frames"]

#: (title, tool, cells): one cell for each row, as (kind, label), where kind
#: is "real" (the shipped code runs), "stand" (a stand-in) or "none".
COLUMNS = [
    ("C tests", "ctest", [
        ("stand", "synthetic"), ("stand", "simulated"), ("real", "firmware core"),
        ("stand", "in memory"), ("none", ""), ("real", "libvgaframe")]),
    ("emulated RP2", "ttcap.emu", [
        ("stand", "synthetic"), ("stand", "emulated"), ("real", "MicroPython script"),
        ("stand", "modelled USB"), ("real", "script builder"), ("real", "vgacap-frames")]),
    ("simulated board", "vgacap-fw-sim", [
        ("stand", "synthetic"), ("stand", "simulated"), ("real", "firmware"),
        ("stand", "pipe"), ("real", "ttcap"), ("real", "vgacap-frames")]),
    ("HIL check", "tools/hil.py", [
        ("real", "real design"), ("real", "RP2 PIO + DMA"), ("real", "board code"),
        ("real", "USB + bridge"), ("real", "ttcap"), ("real", "vgacap-frames")]),
]

#: The two groups over the columns: (first column, last column, label).
GROUPS = [(0, 2, "CI, every push"), (3, 3, "bench board, by hand")]

M = 2                  # margin: the drawing reaches the edges
LABEL_W = 132          # row labels
COL_W, COL_GAP = 148, 10
CELL_H, ROW_GAP = 34, 8
GROUP_H = 26
HEAD_H = 46
LEGEND_H = 40
FONT = "font-family=\"Inter, 'Segoe UI', Helvetica, Arial, sans-serif\""


def draw(p):
    out = []
    add = out.append
    width = M + LABEL_W + len(COLUMNS) * COL_W + (len(COLUMNS) - 1) * COL_GAP + M
    grid_top = M + GROUP_H + HEAD_H
    grid_h = len(ROWS) * CELL_H + (len(ROWS) - 1) * ROW_GAP
    height = grid_top + grid_h + 14 + LEGEND_H + M

    def text(x, y, s, size=13, fill=None, anchor="middle", weight="normal"):
        add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill or p["ink"]}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{escape(s)}</text>')

    def col_x(i):
        return M + LABEL_W + i * (COL_W + COL_GAP)

    def cell(x, y, w, h, kind, label):
        if kind == "none":
            add(f'<line x1="{x + w / 2 - 8}" y1="{y + h / 2}" x2="{x + w / 2 + 8}" '
                f'y2="{y + h / 2}" stroke="{p["none"]}" stroke-width="2"/>')
            return
        edge, fill = p[kind], p[kind + "_fill"]
        dash = ' stroke-dasharray="5 3"' if kind == "stand" else ""
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" '
            f'stroke="{edge}" stroke-width="1.75"{dash}/>')
        text(x + w / 2, y + h / 2 + 4.5, label, 12.5)

    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" {FONT}>')
    add(f'<rect x="0" y="0" width="{width}" height="{height}" fill="{p["paper"]}"/>')

    # The groups, as brackets over their columns.
    for first, last, label in GROUPS:
        x1, x2 = col_x(first) + 4, col_x(last) + COL_W - 4
        y = M + GROUP_H - 6
        add(f'<path d="M{x1} {y + 5} V{y} H{x2} V{y + 5}" fill="none" '
            f'stroke="{p["muted"]}" stroke-width="1.5"/>')
        text((x1 + x2) / 2, y - 6, label, 12.5, p["muted"])

    # Column heads: the kind of test, then the tool that runs it.
    for i, (title, tool, _cells) in enumerate(COLUMNS):
        cx = col_x(i) + COL_W / 2
        text(cx, M + GROUP_H + 18, title, 14, weight="bold")
        text(cx, M + GROUP_H + 36, tool, 12, p["muted"])

    # Row labels, with the data's direction down their left.
    ax = M + 8
    add(f'<path d="M{ax} {grid_top + 6} V{grid_top + grid_h - 10}" stroke="{p["flow"]}" '
        f'stroke-width="2.5" fill="none"/>')
    add(f'<path d="M{ax - 6} {grid_top + grid_h - 14} L{ax} {grid_top + grid_h - 2} '
        f'L{ax + 6} {grid_top + grid_h - 14} Z" fill="{p["flow"]}"/>')
    text(ax - 2, grid_top - 10, "data", 12.5, p["flow"], anchor="start", weight="bold")
    for r, name in enumerate(ROWS):
        y = grid_top + r * (CELL_H + ROW_GAP)
        text(M + 22, y + CELL_H / 2 + 4.5, name, 13, anchor="start", weight="bold")
        if r:
            add(f'<line x1="{M + 22}" y1="{y - ROW_GAP / 2}" x2="{width - M}" '
                f'y2="{y - ROW_GAP / 2}" stroke="{p["rule"]}" stroke-width="1"/>')
        for i, (_t, _tool, cells) in enumerate(COLUMNS):
            kind, label = cells[r]
            cell(col_x(i), y, COL_W, CELL_H, kind, label)

    # The legend.
    ly = grid_top + grid_h + 14 + 8
    items = [("real", "shipped code"), ("stand", "stand-in"), ("none", "not in the test")]
    x = M + LABEL_W
    for kind, label in items:
        if kind == "none":
            add(f'<line x1="{x + 6}" y1="{ly + 12}" x2="{x + 26}" y2="{ly + 12}" '
                f'stroke="{p["none"]}" stroke-width="2"/>')
        else:
            dash = ' stroke-dasharray="5 3"' if kind == "stand" else ""
            add(f'<rect x="{x}" y="{ly}" width="32" height="24" rx="5" fill="{p[kind + "_fill"]}" '
                f'stroke="{p[kind]}" stroke-width="1.75"{dash}/>')
        text(x + 42, ly + 16.5, label, 13, anchor="start")
        x += 190
    add("</svg>")
    return "\n".join(out) + "\n"


def main():
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        (HERE / f"test-layers{suffix}.svg").write_text(draw(PALETTES[theme]), encoding="utf-8")


if __name__ == "__main__":
    main()
