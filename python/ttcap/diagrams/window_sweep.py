#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw window-sweep.svg and window-sweep-dark.svg: window capture
(`ttcap capture --mode window`) sweeping a window of lines down the
picture over successive captured frames, then joining the windows.

    uv run --no-project python python/ttcap/diagrams/window_sweep.py

The numbers are those of how-to-capture-window.md's note for a 640x480
design: 48 of its 481 lines a frame, so 11 captured frames a picture,
the last window one line. Each thumbnail shows only the lines that
frame gave, the rest greyed; the data flows down into the one picture
they make, where dashed lines mark the windows and each shown frame's
number sits beside its band. frame_rp2.py's FRAM chunks of one sweep
share a frame_counter, and libvgaframe joins them once every line is
covered. The picture is a plain disc on a background, so a band reads
as a slice of it.
"""
from __future__ import annotations

import pathlib

from palette import Svg

HERE = pathlib.Path(__file__).resolve().parent

LINES, WINDOW = 481, 48
SHOWN = [1, 2, 3, None, 11]          # captured frames drawn; None is the gap
SKY, DISC = "#2f6fd6", "#ffd23f"     # the design's picture, the same in both themes

TW, TH = 88, 66                      # a thumbnail
GAP = 12
GAP_W = 26                           # the "..." slot
M = 2
W = M + 4 * TW + GAP_W + 4 * GAP + M
PW, PH = 208, 156                    # the joined picture
TOP = M + 19                         # the thumbnails, under the row's label
H = TOP + TH + 24 + 70 + PH + 22 + M


def band(frame):
    """The fractions of the height that a captured frame's window covers."""
    return (frame - 1) * WINDOW / LINES, min(frame * WINDOW, LINES) / LINES


def picture(s, x, y, w, h, y0, y1, clip_id):
    """The design's picture in a w x h frame, only lines y0..y1 (fractions)."""
    top, bottom = y + y0 * h, y + y1 * h
    if bottom - top < 3:                 # a one-line window still shows
        top = bottom - 3
    s.add(f'<clipPath id="{clip_id}"><rect x="{x}" y="{top:.2f}" width="{w}" '
          f'height="{bottom - top:.2f}"/></clipPath>')
    s.add(f'<g clip-path="url(#{clip_id})">'
          f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{SKY}"/>'
          f'<circle cx="{x + w / 2:.2f}" cy="{y + h / 2:.2f}" r="{h * 0.36:.2f}" fill="{DISC}"/>'
          f'</g>')


def draw(theme):
    s = Svg(W, H, theme)
    c = s.c
    s.label(M, M + 10, "captured frames", 13, c["muted"], "start", "bold")

    px, py = (W - PW) / 2, TOP + TH + 24 + 70
    x = M
    for frame in SHOWN:
        if frame is None:
            s.label(x + GAP_W / 2, TOP + TH / 2 + 5, "...", 16, c["muted"], weight="bold")
            x += GAP_W + GAP
            continue
        # Lines this frame did not give: grey.
        s.rect(x, TOP, TW, TH, c["muted"], c["part_edge"], rx=2, sw=1.25)
        s.add(f'<rect x="{x}" y="{TOP}" width="{TW}" height="{TH}" rx="2" '
              f'fill="{c["paper"]}" opacity="0.6"/>')
        picture(s, x + 1, TOP + 1, TW - 2, TH - 2, *band(frame), f"f{frame}")
        s.label(x + TW / 2, TOP + TH + 17, f"frame {frame}", 12.5, c["ink"])
        # Down from the frame into the picture.
        tx = px + PW * (x + TW / 2 - M) / (W - 2 * M)
        s.arrow([(x + TW / 2, TOP + TH + 24), (tx, py - 2)], c["str"], 2)
        x += TW + GAP

    # The joined picture: every window, its edges dashed.
    s.rect(px, py, PW, PH, c["part"], c["part_edge"], rx=2, sw=1.5)
    picture(s, px + 1, py + 1, PW - 2, PH - 2, 0, 1, "all")
    for k in range(1, -(-LINES // WINDOW)):
        ly = py + 1 + (PH - 2) * k * WINDOW / LINES
        s.add(f'<line x1="{px + 1}" y1="{ly:.2f}" x2="{px + PW - 1}" y2="{ly:.2f}" '
              f'stroke="#ffffff" stroke-width="1" stroke-dasharray="4 3" opacity="0.85"/>')
    # Each shown frame's number beside its band.
    for frame in (f for f in SHOWN if f):
        y0, y1 = band(frame)
        mid = py + 1 + (PH - 2) * (y0 + y1) / 2
        s.label(px + PW + 8, min(mid + 4.5, py + PH + 2), str(frame), 12, c["muted"], "start",
                "bold")
    s.label(W / 2, py + PH + 20, "one picture", 14, c["ink"], weight="bold")
    return s.text()


def main():
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        (HERE / f"window-sweep{suffix}.svg").write_text(draw(theme), encoding="utf-8")


if __name__ == "__main__":
    main()
