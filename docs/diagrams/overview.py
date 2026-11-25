#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw docs/diagrams/overview.svg: vgacap's capture sources, the shared
stream, and its consumers.

    uv run --no-project python docs/diagrams/overview.py

Hand-placed, not auto-laid-out: every box, line and label has a fixed
position, so the figure is reviewed as drawn. Style follows one palette:
blue for the design's signals, orange for vgacap stream bytes, green for
pictures, violet dashed for control.
"""
from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

W, H = 1146, 650
INK, INK2, MUTED = "#1d2433", "#2b3445", "#5b6577"
SIG, SIG_FILL, SIG_EDGE = "#1c64d4", "#eaf1fd", "#9dbcef"
STR, STR_FILL, STR_EDGE = "#f2620c", "#fff2e8", "#f8b48a"
PIC, PIC_FILL, PIC_EDGE = "#14845a", "#e7f6ef", "#93d3b6"
CTL, CTL_FILL, CTL_EDGE = "#6b46d6", "#f2eefd", "#c2b2f0"
PAD = "#cfd5df"

out: list[str] = []


def add(s: str) -> None:
    out.append(s)


def text(x, y, s, cls="detail", anchor="start", fill=None):
    f = f' fill="{fill}"' if fill else ""
    add(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}"{f}>{escape(s)}</text>')


def box(x, y, w, h, fill, edge, title, lines=(), rx=8, sw=1.25, title_fill=INK):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
        f'stroke="{edge}" stroke-width="{sw}"/>')
    text(x + 12, y + 22, title, "title", fill=title_fill)
    for i, line in enumerate(lines):
        text(x + 12, y + 41 + 17 * i, line)


def line(points, kind, both=False, dash=None):
    colour, width, marker = {
        "sig": (SIG, 3, "arrow-sig"),
        "str": (STR, 4.5, "arrow-str"),
        "pic": (PIC, 3, "arrow-pic"),
        "ctl": (CTL, 1.75, "arrow-ctl"),
    }[kind]
    d = "M" + " L".join(f"{x} {y}" for x, y in points)
    extra = ' stroke-dasharray="5 4"' if kind == "ctl" else ""
    start = f' marker-start="url(#{marker})"' if both else ""
    add(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}"{extra}'
        f'{start} marker-end="url(#{marker})"/>')


def tag(x, y, s, dark=True):
    w = 7.2 * len(s) + 18
    if dark:
        add(f'<rect x="{x}" y="{y}" width="{w:.0f}" height="22" rx="11" fill="{INK2}"/>')
        text(x + w / 2, y + 15.5, s, "tag", "middle", fill="#ffffff")
    else:
        add(f'<rect x="{x}" y="{y}" width="{w:.0f}" height="22" rx="11" fill="#ffffff" '
            f'stroke="{INK2}" stroke-width="1.25"/>')
        text(x + w / 2, y + 15.5, s, "tag", "middle", fill=INK2)


# ---- canvas, style, markers ------------------------------------------------
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
    f'font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif" '
    f'role="img" aria-labelledby="t d">')
add('<title id="t">vgacap overview</title>')
add('<desc id="d">Capture sources on the left write one vgacap stream in the middle; '
    'reconstruction and the tools on the right turn it into pictures, video and checks.</desc>')
add(f"""<style>
  .title {{ font-size: 14px; font-weight: 600; fill: {INK}; }}
  .detail {{ font-size: 12.5px; fill: {MUTED}; }}
  .net {{ font-size: 11.5px; font-weight: 600; }}
  .tag {{ font-size: 12px; font-weight: 600; }}
  .pad {{ font-size: 12px; font-weight: 600; fill: {INK}; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  .legend {{ font-size: 12.5px; fill: {INK}; }}
</style>""")
add("<defs>")
for name, colour, mw in (("arrow-sig", SIG, 3.4), ("arrow-str", STR, 3.0),
                         ("arrow-pic", PIC, 3.4), ("arrow-ctl", CTL, 4.1)):
    add(f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="{mw}" '
        f'markerHeight="{mw}" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" '
        f'fill="{colour}"/></marker>')
add("</defs>")
add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')

# ---- geometry ------------------------------------------------------------------
ROWS = [176, 282, 388, 494]          # centre line of each source row
SRC_X, SRC_W, SRC_H = 296, 250, 92   # source boxes
DES_X, DES_W = 20, 180               # the design under capture
STREAM_X, STREAM_W = 716, 196        # the stream block
CON_X, CON_W = 956, 176              # consumers

# ---- column headings ------------------------------------------------------------
for x, w, s in ((DES_X, DES_W, "Under capture"), (SRC_X, SRC_W, "Capture sources"),
                (STREAM_X, STREAM_W, "One format"), (CON_X, CON_W, "Consumers")):
    text(x + w / 2, 24, s.upper(), "tag", "middle", fill=MUTED)

# ---- the design ----------------------------------------------------------------------
dy0, dy1 = ROWS[0] - SRC_H / 2, ROWS[3] + SRC_H / 2
add(f'<rect x="{DES_X}" y="{dy0}" width="{DES_W}" height="{dy1 - dy0}" rx="4" fill="#ffffff" '
    f'stroke="{INK}" stroke-width="1.5"/>')
text(DES_X + 14, dy0 + 26, "Tiny Tapeout design", "title")
for i, s in enumerate(("on an ASIC, an FPGA", "or in a simulator", "",
                       "VGA on the Tiny VGA", "Pmod: uo_out[7:0]", "",
                       "project clock", "25.175 MHz typical")):
    text(DES_X + 14, dy0 + 48 + 17 * i, s)

# ---- the host (control) ------------------------------------------------------------
HOST_Y = 44
box(SRC_X, HOST_Y, SRC_W, 36, CTL_FILL, CTL_EDGE, "ttcap on a host computer")
line([(SRC_X + 40, HOST_Y + 36), (SRC_X + 40, ROWS[0] - SRC_H / 2 - 2)], "ctl")
text(SRC_X + 50, HOST_Y + 61, "clock, reset, start, stop", "net", fill=CTL)

# ---- the sources --------------------------------------------------------------------
sources = [
    ("Tiny Tapeout demo board", ["its RP2040 or RP2350 samples", "uo_out with PIO, once a clock;",
                                 "stock firmware or vgacap's own"],
     "USB serial or websocket", "up to ~0.7 MB/s"),
    ("Raspberry Pi 5", ["its RP1 I/O chip samples", "uo_out on the GPIO header"],
     "on the Pi itself", "DMA to DRAM"),
    ("FPGA capture logic", ["rtl/: built into the FPGA", "design next to the project"],
     "the FPGA's own link", "one byte a clock"),
    ("Simulators", ["the design's RTL, run in", "Verilator or Icarus"],
     "trace files", "every clock"),
]
for (title, lines_, link, rate), cy in zip(sources, ROWS):
    y = cy - SRC_H / 2
    box(SRC_X, y, SRC_W, SRC_H, SIG_FILL, SIG_EDGE, title, lines_)
    # the design's signal into the source
    if title == "Simulators":
        line([(DES_X + DES_W, cy), (SRC_X - 2, cy)], "sig")
        text((DES_X + DES_W + SRC_X) / 2, cy - 8, "RTL", "net", "middle", fill=SIG)
    else:
        line([(DES_X + DES_W, cy), (SRC_X - 2, cy)], "sig")
        text((DES_X + DES_W + SRC_X) / 2, cy - 8, "uo_out[7:0]", "net", "middle", fill=SIG)
    # the source's stream into the format
    line([(SRC_X + SRC_W, cy), (STREAM_X - 2, cy)], "str")
    text((SRC_X + SRC_W + STREAM_X) / 2, cy - 9, link, "net", "middle", fill=STR)
    text((SRC_X + SRC_W + STREAM_X) / 2, cy + 19, rate, "detail", "middle")


# ---- the stream ----------------------------------------------------------------------
sy0, sy1 = ROWS[0] - SRC_H / 2, ROWS[3] + SRC_H / 2
add(f'<rect x="{STREAM_X}" y="{sy0}" width="{STREAM_W}" height="{sy1 - sy0}" rx="6" '
    f'fill="#f7f8fb" stroke="{INK2}" stroke-width="2.5"/>')
text(STREAM_X + STREAM_W / 2, sy0 + 26, "vgacap stream", "title", "middle")
text(STREAM_X + STREAM_W / 2, sy0 + 44, "include/vgacap/stream.h", "detail", "middle")
chunks = [("VGCH", "header", INK2),
          ("RAW", "every sample", STR), ("RLE", "value + run", STR),
          ("FRAM", "frame or window", STR), ("EVNT", "timed changes", STR),
          ("TIME", "notes, overruns", MUTED)]
for i, (name, what, colour) in enumerate(chunks):
    py = sy0 + 64 + 50 * i
    add(f'<rect x="{STREAM_X + 14}" y="{py}" width="54" height="24" rx="3" fill="#ffffff" '
        f'stroke="{colour}" stroke-width="1.5"/>')
    text(STREAM_X + 41, py + 16.5, name, "pad", "middle")
    text(STREAM_X + 78, py + 16.5, what, "detail")

# ---- the consumers ---------------------------------------------------------------------
LIB_Y = ROWS[0] - SRC_H / 2
box(CON_X, LIB_Y, CON_W, 100, STR_FILL, STR_EDGE, "libvgaframe",
    ["learns sync and mode", "from the signal, then", "rebuilds each frame"])
line([(STREAM_X + STREAM_W, ROWS[0]), (CON_X - 2, ROWS[0])], "str")

outputs = [
    ("Pictures", ["PNG and PPM", "(vgacap-frames, ttcap png)"]),
    ("Video", ["GStreamer: MKV, a window,", "MJPEG (ttcap demo)"]),
    ("Checks", ["pixel-exact against", "reference renders"]),
]
oy = LIB_Y + 130
trunk_x = CON_X - 20
add(f'<path d="M{CON_X} {LIB_Y + 76} L{trunk_x} {LIB_Y + 76} L{trunk_x} {oy + 2 * 96 + 38}" '
    f'fill="none" stroke="{PIC}" stroke-width="3"/>')
for i, (title, lines_) in enumerate(outputs):
    y = oy + 96 * i
    box(CON_X, y, CON_W, 76, PIC_FILL, PIC_EDGE, title, lines_)
    line([(trunk_x, y + 38), (CON_X - 2, y + 38)], "pic")

# ---- legend ------------------------------------------------------------------------------
LY = 604
add(f'<rect x="{SRC_X}" y="{LY - 22}" width="{W - SRC_X - 16}" height="54" rx="6" fill="#ffffff" '
    f'stroke="{PAD}" stroke-width="1.25"/>')
x = SRC_X + 18
for kind, label in (("sig", "the design's signals"), ("str", "vgacap stream bytes"),
                    ("pic", "pictures"), ("ctl", "control")):
    line([(x, LY + 5), (x + 44, LY + 5)], kind)
    text(x + 54, LY + 9.5, label, "legend")
    x += 54 + 7.0 * len(label) + 34

add("</svg>")
here = pathlib.Path(__file__).resolve().parent
(here / "overview.svg").write_text("\n".join(out) + "\n")
print("wrote", here / "overview.svg")
