#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw docs/diagrams/stream-chunks.svg and stream-chunks-dark.svg: a vgacap
stream as bytes, a run of chunks, each a tag, a length and a payload.

    uv run --no-project python docs/diagrams/stream_chunks.py

Hand-placed. The top row is one chunk's framing, with byte offsets; below
it, the chunks of a stream-mode capture in the order they arrive.
Colours follow docs/diagrams/overview.py: blue for the header that says
how to read the signals, orange for captured data, grey for the rest.
"""
from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent

THEMES = {
    "": dict(BG="#ffffff", INK="#1d2433", MUTED="#5b6577", RULE="#7d8799",
             HDR="#1c64d4", HDR_FILL="#eaf1fd", DATA="#f2620c", DATA_FILL="#fff2e8",
             DIAG="#5b6577", DIAG_FILL="#eef0f4", FRAME="#ffffff", FRAME_EDGE="#c9ced8"),
    "-dark": dict(BG="#16181d", INK="#e6e9ef", MUTED="#a3acbd", RULE="#8a93a6",
                  HDR="#6aa5ff", HDR_FILL="#16233a", DATA="#ff8f45", DATA_FILL="#2f1e12",
                  DIAG="#a3acbd", DIAG_FILL="#232834", FRAME="#1d2129", FRAME_EDGE="#454c5c"),
}

MARGIN = 2            # the stroke's half-width and a hair: no stray whitespace
TAG_W, LEN_W = 92, 92
PAY_W = 330
ROW_H = 34
X0 = MARGIN
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

# The chunks of one capture, as ttcap writes a stream-mode capture from the
# stock firmware: the header, RAW chunks, an overrun marker, and the closing
# TIME chunk with the board's summary.
CHUNKS = (
    ("VGCH", "header: bits, signal map, clock", "HDR"),
    ("RAW ", "samples, packed in 32-bit words", "DATA"),
    ("RAW ", "samples, packed in 32-bit words", "DATA"),
    ("TIME", "overrun: dropped samples", "DIAG"),
    ("RAW ", "samples, packed in 32-bit words", "DATA"),
    ("TIME", "closing: overruns, rxstall", "DIAG"),
)


def draw(p):
    out = []
    add = out.append

    def text(x, y, s, size=13, weight=400, fill=None, anchor="start", family=FONT):
        add(f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill or p["INK"]}" text-anchor="{anchor}">'
            f'{escape(s)}</text>')

    def box(x, y, w, h, fill, edge, sw=1.25):
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{fill}" '
            f'stroke="{edge}" stroke-width="{sw}"/>')

    def chunk(y, tag, payload, kind, tag_label=True):
        colour, fill = p[kind], p[kind + "_FILL"]
        box(X0, y, TAG_W, ROW_H, fill, colour, 1.5)
        text(X0 + TAG_W / 2, y + 22, f'"{tag}"' if tag_label else tag, 14, 700, colour,
             "middle", MONO)
        box(X0 + TAG_W, y, LEN_W, ROW_H, p["FRAME"], p["FRAME_EDGE"])
        text(X0 + TAG_W + LEN_W / 2, y + 22, "length", 13, 400, p["MUTED"], "middle")
        box(X0 + TAG_W + LEN_W, y, PAY_W, ROW_H, fill, colour, 1.5)
        text(X0 + TAG_W + LEN_W + 12, y + 22, payload, 13, 400, p["INK"])

    width = X0 + TAG_W + LEN_W + PAY_W + MARGIN

    # One chunk's framing, with its byte offsets under it.
    y = MARGIN + 16
    text(X0, y - 4, "one chunk", 13, 600, p["MUTED"])
    y += 6
    box(X0, y, TAG_W, ROW_H, p["FRAME"], p["RULE"], 1.5)
    text(X0 + TAG_W / 2, y + 22, "tag", 14, 700, p["INK"], "middle")
    box(X0 + TAG_W, y, LEN_W, ROW_H, p["FRAME"], p["RULE"], 1.5)
    text(X0 + TAG_W + LEN_W / 2, y + 22, "length", 14, 700, p["INK"], "middle")
    box(X0 + TAG_W + LEN_W, y, PAY_W, ROW_H, p["FRAME"], p["RULE"], 1.5)
    text(X0 + TAG_W + LEN_W + PAY_W / 2, y + 22, "payload: length bytes", 14, 700, p["INK"],
         "middle")
    ry = y + ROW_H + 16
    for x, label, anchor in ((X0, "0", "start"), (X0 + TAG_W, "4", "middle"),
                             (X0 + TAG_W + LEN_W, "8", "middle"),
                             (width - MARGIN, "8 + length", "end")):
        add(f'<line x1="{x}" y1="{y + ROW_H}" x2="{x}" y2="{y + ROW_H + 5}" '
            f'stroke="{p["RULE"]}" stroke-width="1.25"/>')
        text(x, ry, label, 12, 400, p["MUTED"], anchor, MONO)
    text(X0 + TAG_W / 2, ry, "4 ASCII", 12, 400, p["MUTED"], "middle")
    text(X0 + TAG_W + LEN_W / 2, ry, "u32 LE", 12, 400, p["MUTED"], "middle")

    # The stream: chunks one after another, down the page.
    y = ry + 34
    text(X0, y - 4, "a capture", 13, 600, p["MUTED"])
    y += 6
    for i, (tag, payload, kind) in enumerate(CHUNKS):
        chunk(y, tag, payload, kind)
        y += ROW_H + 8
    height = y - 8 + MARGIN

    head = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height:.0f}" '
            f'viewBox="0 0 {width} {height:.0f}" role="img" aria-labelledby="t">',
            '<title id="t">A vgacap stream: chunks of tag, length and payload</title>',
            f'<rect x="0" y="0" width="{width}" height="{height:.0f}" fill="{p["BG"]}"/>']
    return head + out + ["</svg>"]


for suffix, palette in THEMES.items():
    svg = HERE / f"stream-chunks{suffix}.svg"
    svg.write_text("\n".join(draw(palette)) + "\n")
    print("wrote", svg, svg.stat().st_size, "bytes")
