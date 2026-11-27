#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw stop-sequence.svg and stop-sequence-dark.svg: how vgacapttsrc's
stop() ends its `ttcap capture` child, as `stop_child()` in
gst/gstvgacapttsrc.c does it.

    uv run --no-project python gst/diagrams/stop_sequence.py

Time runs down the page. Each step waits for the child to end; a child
that has ended goes straight down the right-hand rail to the reaping,
and one that has not goes on to the next, stronger step. The waits are
`stop-timeout` and ESCALATION_GRACE_S (2 s). Both twins come from one
palette.
"""
from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent

PALETTES = {
    "light": {
        "paper": "#ffffff", "ink": "#1d2433", "muted": "#5b6577",
        "box": "#f3f5f9", "edge": "#9aa3b5", "flow": "#5b6577",
        "ask": "#14845a", "nudge": "#c05a0c", "kill": "#c62828",
        "done": "#14845a", "done_fill": "#e7f6ef",
    },
    "dark": {
        "paper": "#131416", "ink": "#e6e9ef", "muted": "#a3acbd",
        "box": "#1b1e24", "edge": "#5d6578", "flow": "#a3acbd",
        "ask": "#4cc38a", "nudge": "#f0904a", "kill": "#ff6b6b",
        "done": "#4cc38a", "done_fill": "#173326",
    },
}

FONT = "font-family=\"'DejaVu Sans', Verdana, Arial, sans-serif\""
M = 2
BOX_X, BOX_W, BOX_H = M, 250, 52
CX = BOX_X + BOX_W / 2           # the column the steps run down
RAIL = BOX_X + BOX_W + 92        # the rail a child that has ended takes
W = RAIL + 40 + M
DW, DH = 62, 24                  # half the size of an "ended?" diamond


def draw(p):
    out = []
    add = out.append

    def text(x, y, s, size=13, fill=None, anchor="middle", weight="normal"):
        add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill or p["ink"]}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{escape(s)}</text>')

    def arrow(points, colour, width=2):
        d = "M" + " L".join(f"{x} {y}" for x, y in points)
        (x1, y1), (x2, y2) = points[-2], points[-1]
        add(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}"/>')
        if x1 == x2:      # down
            head = f"M{x2 - 6} {y2 - 10} L{x2} {y2} L{x2 + 6} {y2 - 10} Z"
        else:             # right
            head = f"M{x2 - 10} {y2 - 6} L{x2} {y2} L{x2 - 10} {y2 + 6} Z"
        add(f'<path d="{head}" fill="{colour}"/>')

    def step(y, title, sub, colour, w=BOX_W, fill=None, x=BOX_X):
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{BOX_H}" rx="7" '
            f'fill="{fill or p["box"]}" stroke="{colour}" stroke-width="2"/>')
        cx = x + w / 2
        text(cx, y + (22 if sub else 31), title, 14.5, weight="bold")
        if sub:
            text(cx, y + 41, sub, 12.5, p["muted"])

    def ended(cy):
        """An "ended?" test: no goes down, yes goes right to the rail."""
        add(f'<path d="M{CX} {cy - DH} L{CX + DW} {cy} L{CX} {cy + DH} L{CX - DW} {cy} Z" '
            f'fill="{p["paper"]}" stroke="{p["edge"]}" stroke-width="1.75"/>')
        text(CX, cy + 4.5, "ended?", 13, weight="bold")
        add(f'<path d="M{CX + DW} {cy} H{RAIL}" fill="none" stroke="{p["done"]}" '
            f'stroke-width="2"/>')
        text((CX + DW + RAIL) / 2, cy - 7, "yes", 12.5, p["done"], weight="bold")
        text(CX - 10, cy + DH + 18, "no", 12.5, p["muted"], "end", "bold")

    y = M
    step(y, "SIGINT", "to ttcap's process group", p["ask"])
    arrow([(CX, y + BOX_H), (CX, y + BOX_H + 24)], p["flow"])
    y += BOX_H + 26
    step(y, "wait, draining stdout", "up to stop-timeout", p["ask"])
    first = y + BOX_H + 22 + DH
    arrow([(CX, y + BOX_H), (CX, first - DH)], p["flow"])
    ended(first)
    y = first + DH + 30
    arrow([(CX, first + DH), (CX, y - 2)], p["flow"])
    step(y, "close the stdout pipe", "wait up to 2 s", p["nudge"])
    second = y + BOX_H + 22 + DH
    arrow([(CX, y + BOX_H), (CX, second - DH)], p["flow"])
    ended(second)
    y = second + DH + 30
    arrow([(CX, second + DH), (CX, y - 2)], p["flow"])
    step(y, "SIGKILL the group", "wait up to 2 s", p["kill"])
    reap = y + BOX_H + 30
    arrow([(CX, y + BOX_H), (CX, reap - 2)], p["flow"])
    arrow([(RAIL, first), (RAIL, reap - 2)], p["done"])
    step(reap, "reap the child", None, p["done"], w=RAIL + 40 - BOX_X, fill=p["done_fill"])
    height = reap + BOX_H + M

    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
            f'viewBox="0 0 {W} {height}" {FONT}>\n'
            f'<rect x="0" y="0" width="{W}" height="{height}" fill="{p["paper"]}"/>')
    return head + "\n" + "\n".join(out) + "\n</svg>\n"


def main():
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        (HERE / f"stop-sequence{suffix}.svg").write_text(draw(PALETTES[theme]),
                                                         encoding="utf-8")


if __name__ == "__main__":
    main()
