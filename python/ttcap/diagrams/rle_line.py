#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw rle-line.svg and rle-line-dark.svg: part of one VGA line as
samples, then as the runs a run-length capture sends for them.

    uv run --no-project python python/ttcap/diagrams/rle_line.py

Top: sixteen samples, one a project clock, coloured as the picture is.
Middle: the same clocks grouped into runs of one value. Bottom: the
words the board sends, a value and a length for each run.
"""
from __future__ import annotations

import pathlib

from palette import Svg

HERE = pathlib.Path(__file__).resolve().parent
RUNS = [("#e02020", 5, "red"), ("#f4f4f4", 3, "white"), ("#e02020", 1, "red"),
        ("#101010", 7, "black")]
CELL, X0 = 32, 128
W = X0 + CELL * sum(n for _, n, _ in RUNS) + 12
H = 254


def draw(theme):
    s = Svg(W, H, theme)
    c = s.c
    edge = c["muted"]

    # Samples: one square a clock.
    s.label(X0 - 12, 38, "samples", 14, anchor="end", weight="bold")
    s.label(X0 - 12, 55, "one a clock", 12, c["muted"], "end")
    x = X0
    for colour, n, _ in RUNS:
        for _ in range(n):
            s.rect(x + 2, 20, CELL - 4, CELL - 4, colour, edge, rx=3, sw=1)
            x += CELL

    s.arrow([(W / 2 + 60, 58), (W / 2 + 60, 92)], c["str"], 2.5)

    # Runs: one bar for each stretch of one value, with its length.
    s.label(X0 - 12, 118, "runs", 14, anchor="end", weight="bold")
    x = X0
    for colour, n, _ in RUNS:
        s.rect(x + 2, 98, CELL * n - 4, CELL - 4, colour, edge, rx=3, sw=1)
        s.label(x + CELL * n / 2, 145, f"× {n}", 13)
        x += CELL * n

    s.arrow([(W / 2 + 60, 156), (W / 2 + 60, 186)], c["str"], 2.5)

    # Words: value, length, for each run.
    s.label(X0 - 12, 210, "words sent", 14, anchor="end", weight="bold")
    s.label(X0 - 12, 226, "value, length", 12, c["muted"], "end")
    x = X0
    for _, n, name in RUNS:
        s.rect(x + 2, 194, CELL * 2 - 6, 32, c["str_fill"], c["str_edge"], rx=3, sw=1)
        s.label(x + CELL - 1, 215, name, 12)
        s.rect(x + CELL * 2 - 2, 194, CELL * 2 - 6, 32, c["str_fill"], c["str_edge"], rx=3, sw=1)
        s.label(x + CELL * 3 - 5, 215, str(n), 12)
        x += CELL * 4
    s.label(X0 + 2, 246, "8 words for 16 samples", 12, c["muted"], "start")
    return s.text()


if __name__ == "__main__":
    for theme, name in (("light", "rle-line.svg"), ("dark", "rle-line-dark.svg")):
        (HERE / name).write_text(draw(theme))
