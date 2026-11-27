#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw window-sweep.svg and window-sweep-dark.svg: window capture
(`ttcap capture --mode window`) sweeping a window of lines down the
picture over successive captured frames, then joining the windows.

    uv run --no-project python python/ttcap/diagrams/window_sweep.py

The numbers are those of how-to-capture-window.md's note for a 640x480
design: 48 of its 481 lines a frame, so 11 captured frames a picture,
the last window one line. Each thumbnail shows only the lines that
frame gave, the rest greyed, and each shown frame's arrow runs down
beside the picture into its own band, outlined and numbered, so no
arrow crosses the picture or another arrow. frame_rp2.py's FRAM chunks of one sweep
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
LEVEL = 12                           # between two arrows' turning levels
LANE = 14                            # between two arrows' lanes
PX = W - M - 4 * LANE - 4 - PW      # the joined picture, its lanes to its right
H = TOP + TH + 24 + 12 + LEVEL * 4 + 6 + PH + 22 + M


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

    # Each shown frame's arrow drops from its thumbnail, turns right at its
    # own level, runs down its own lane right of the picture, and turns left
    # into its own band. Frame 1 turns lowest and takes the inner lane, so no
    # two arrows cross.
    drop = TOP + TH + 24
    shown = [f for f in SHOWN if f]
    level = {f: drop + 12 + LEVEL * (len(shown) - 1 - i) for i, f in enumerate(shown)}
    px, py = PX, drop + 12 + LEVEL * len(shown) + 6
    lane = {f: px + PW + LANE * (i + 1) for i, f in enumerate(shown)}

    def band_box(frame, x, y, w, h):
        """A band of a w x h frame as (top, height), at least 3 high."""
        y0, y1 = band(frame)
        top, bottom = y + y0 * h, y + y1 * h
        if bottom - top < 3:
            top = bottom - 3
        return top, bottom - top

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
        top, h = band_box(frame, x + 1, TOP + 1, TW - 2, TH - 2)
        s.add(f'<rect x="{x + 0.5}" y="{top:.2f}" width="{TW - 1}" height="{h:.2f}" '
              f'fill="none" stroke="{c["str"]}" stroke-width="1.5"/>')
        s.label(x + TW / 2, TOP + TH + 17, f"frame {frame}", 12.5, c["ink"])
        x += TW + GAP

    # The joined picture: every window, its edges dashed.
    s.rect(px, py, PW, PH, c["part"], c["part_edge"], rx=2, sw=1.5)
    picture(s, px + 1, py + 1, PW - 2, PH - 2, 0, 1, "all")
    for k in range(1, -(-LINES // WINDOW)):
        ly = py + 1 + (PH - 2) * k * WINDOW / LINES
        s.add(f'<line x1="{px + 1}" y1="{ly:.2f}" x2="{px + PW - 1}" y2="{ly:.2f}" '
              f'stroke="#ffffff" stroke-width="1" stroke-dasharray="4 3" opacity="0.85"/>')

    # Each shown frame's band outlined, numbered, and reached by its arrow.
    x = M
    for frame in SHOWN:
        if frame is None:
            x += GAP_W + GAP
            continue
        top, h = band_box(frame, px + 1, py + 1, PW - 2, PH - 2)
        s.add(f'<rect x="{px + 1}" y="{top:.2f}" width="{PW - 2}" height="{h:.2f}" '
              f'fill="none" stroke="{c["str"]}" stroke-width="2"/>')
        mid = top + h / 2
        s.label(px - 8, mid + 4.5, str(frame), 12, c["muted"], "end", "bold")
        cx = x + TW / 2
        s.arrow([(cx, drop), (cx, level[frame]), (lane[frame], level[frame]),
                 (lane[frame], mid), (px + PW + 3, mid)], c["str"], 2)
        x += TW + GAP
    s.label(px + PW / 2, py + PH + 20, "one picture", 14, c["ink"], weight="bold")
    return s.text()


def main():
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        (HERE / f"window-sweep{suffix}.svg").write_text(draw(theme), encoding="utf-8")


if __name__ == "__main__":
    main()
