#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw docs/diagrams/overview.svg: vgacap's capture sources, the shared
stream, and what reads it.

    uv run --no-project python docs/diagrams/overview.py

Hand-placed, not auto-laid-out. Few words: each capture source is drawn
as the parts that do the work (the chip, and the PIO or logic inside it),
and the colours carry the rest (see the legend). The board photo, the
Tiny Tapeout and the Raspberry Pi logos are embedded from images/ and
logos/; NOTICE.md says where each is from and under what terms.
"""
from __future__ import annotations

import base64
import pathlib
import re
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent
W, H = 1154, 1042
INK, INK2, MUTED = "#1d2433", "#2b3445", "#5b6577"
SIG, SIG_FILL, SIG_EDGE = "#1c64d4", "#eaf1fd", "#9dbcef"
STR, STR_FILL, STR_EDGE = "#f2620c", "#fff2e8", "#f8b48a"
PIC, PIC_FILL, PIC_EDGE = "#14845a", "#e7f6ef", "#93d3b6"
CTL, CTL_FILL, CTL_EDGE = "#6b46d6", "#f2eefd", "#c2b2f0"
PART, PART_EDGE = "#ffffff", "#7d8799"
BARS = ["#ffffff", "#ffff00", "#00ffff", "#00ff00", "#ff00ff", "#ff0000", "#0000ff", "#000000"]

out: list[str] = []
add = out.append
_ids = iter(range(1000))


def text(x, y, s, cls="detail", anchor="start", fill=None):
    f = f' fill="{fill}"' if fill else ""
    add(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}"{f}>{escape(s)}</text>')


def rect(x, y, w, h, fill, edge, rx=8, sw=1.25, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
        f'stroke="{edge}" stroke-width="{sw}"{d}/>')


def line(points, kind, head=True):
    colour, width, marker = {"sig": (SIG, 3, "a-sig"), "str": (STR, 4.5, "a-str"),
                             "pic": (PIC, 3, "a-pic"), "ctl": (CTL, 1.75, "a-ctl"),
                             "thin": (SIG, 1.75, "a-thin")}[kind]
    d = "M" + " L".join(f"{x} {y}" for x, y in points)
    dash = ' stroke-dasharray="5 4"' if kind == "ctl" else ""
    end = f' marker-end="url(#{marker})"' if head else ""
    add(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}"{dash}{end}/>')


def nested_svg(path, x, y, w, h):
    src = path.read_text()
    src = re.sub(r"<\?xml[^>]*\?>|<!DOCTYPE[^>]*>|<!--.*?-->", "", src, flags=re.S)
    src = re.sub(r"<sodipodi:namedview.*?/>", "", src, flags=re.S)
    vb = re.search(r'viewBox="([^"]+)"', src).group(1)
    inner = src[src.index(">", src.index("<svg")) + 1: src.rindex("</svg>")]
    add(f'<svg x="{x}" y="{y}" width="{w}" height="{h}" viewBox="{vb}" '
        f'preserveAspectRatio="xMidYMid meet">{inner}</svg>')


def tt_badge(cx, cy, r):
    cid = f"clip{next(_ids)}"
    add(f'<clipPath id="{cid}"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>')
    add(f'<g clip-path="url(#{cid})">')
    nested_svg(HERE / "logos" / "tinytapeout.svg", cx - r, cy - r, 2 * r, 2 * r)
    add("</g>")


def chip(x, y, w, h, name):
    """A chip package: pins on top and bottom, its name in the corner."""
    n = max(3, int(w // 16))
    for i in range(n):
        px = x + w * (i + 0.5) / n
        for y1, y2 in ((y - 5, y), (y + h, y + h + 5)):
            add(f'<line x1="{px:.1f}" y1="{y1}" x2="{px:.1f}" y2="{y2}" stroke="{INK2}" '
                f'stroke-width="2" stroke-linecap="round"/>')
    rect(x, y, w, h, INK2, INK2, rx=4, sw=1)
    text(x + 8, y + 15, name, "chipname", fill="#ffffff")


def part(x, y, w, h, name):
    """A block inside a chip (PIO, DMA, capture logic)."""
    rect(x, y, w, h, PART, PART_EDGE, rx=4, sw=1)
    text(x + w / 2, y + h / 2 + 4.5, name, "part", "middle")


def monitor(x, y, w, h):
    rect(x, y, w, h, INK2, INK2, rx=4, sw=1)
    bw = (w - 8) / len(BARS)
    for i, c in enumerate(BARS):
        add(f'<rect x="{x + 4 + i * bw:.1f}" y="{y + 4}" width="{bw + 0.2:.1f}" '
            f'height="{h - 8}" fill="{c}"/>')
    add(f'<rect x="{x + w / 2 - 6}" y="{y + h}" width="12" height="6" fill="{INK2}"/>')
    add(f'<rect x="{x + w / 2 - 16}" y="{y + h + 6}" width="32" height="3" rx="1.5" '
        f'fill="{INK2}"/>')


def check(cx, cy, r):
    add(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{PIC}"/>')
    add(f'<path d="M{cx - r * .45} {cy} L{cx - r * .1} {cy + r * .38} L{cx + r * .5} '
        f'{cy - r * .35}" fill="none" stroke="#ffffff" stroke-width="{r * .22:.1f}" '
        f'stroke-linecap="round" stroke-linejoin="round"/>')


def laptop(x, y, w, h):
    rect(x + 6, y, w - 12, h, "#ffffff", CTL, rx=3, sw=1.75)
    add(f'<path d="M{x} {y + h + 6} L{x + w} {y + h + 6} L{x + w - 6} {y + h} '
        f'L{x + 6} {y + h} z" fill="{CTL}"/>')
    text(x + w / 2, y + h / 2 + 4, ">_", "part", "middle", fill=CTL)


def doc(x, y, w, h, label):
    """A source file with a folded corner."""
    f = 10
    add(f'<path d="M{x} {y} L{x + w - f} {y} L{x + w} {y + f} L{x + w} {y + h} L{x} {y + h} z" '
        f'fill="#ffffff" stroke="{SIG}" stroke-width="1.5"/>')
    add(f'<path d="M{x + w - f} {y} L{x + w - f} {y + f} L{x + w} {y + f}" fill="none" '
        f'stroke="{SIG}" stroke-width="1.5"/>')
    for i in range(3):
        add(f'<line x1="{x + 7}" y1="{y + 18 + 7 * i}" x2="{x + w - 9 - 6 * (i % 2)}" '
            f'y2="{y + 18 + 7 * i}" stroke="{SIG_EDGE}" stroke-width="2"/>')
    text(x + w / 2, y + h + 16, label, "net", "middle", fill=SIG)


def flow(x, y, names_widths, gap=22):
    """Parts in a row, joined by thin arrows; returns the right edge."""
    for i, (name, w) in enumerate(names_widths):
        part(x, y, w, 26, name)
        if i < len(names_widths) - 1:
            line([(x + w, y + 13), (x + w + gap - 2, y + 13)], "thin")
        x += w + gap
    return x - gap


# ---- canvas ------------------------------------------------------------------------
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
    f'font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif" '
    f'role="img" aria-labelledby="t d">')
add('<title id="t">vgacap overview</title>')
add('<desc id="d">A Tiny Tapeout demo board\'s uo_out is captured by the board\'s own '
    'RP2040 or RP2350 (PIO, DMA, USB to ttcap), either straight from the design\'s breakout or '
    'as framed bytes from capture logic beside the design on an iCE40UP5K FPGA breakout; or by '
    'a Raspberry Pi 5 wired to the board (RP1 PIO and DMA); or a simulator runs the design\'s '
    'RTL. All write one vgacap stream, which libvgaframe turns into pictures, video and '
    'pixel-exact checks.</desc>')
add(f"""<style>
  .title {{ font-size: 15px; font-weight: 600; fill: {INK}; }}
  .detail {{ font-size: 12.5px; fill: {MUTED}; }}
  .net {{ font-size: 12px; font-weight: 600; }}
  .head {{ font-size: 12px; font-weight: 700; letter-spacing: 0.08em; fill: {MUTED}; }}
  .chipname {{ font-size: 11px; font-weight: 700; letter-spacing: 0.03em; }}
  .part {{ font-size: 12px; font-weight: 700; fill: {INK}; }}
  .pad {{ font-size: 12.5px; font-weight: 700; fill: {INK};
          font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  .legend {{ font-size: 12.5px; fill: {INK}; }}
</style>""")
add("<defs>")
for name, colour, mw in (("a-sig", SIG, 3.4), ("a-str", STR, 3.0), ("a-pic", PIC, 3.4),
                         ("a-ctl", CTL, 4.1), ("a-thin", SIG, 4.6)):
    add(f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="{mw}" '
        f'markerHeight="{mw}" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" '
        f'fill="{colour}"/></marker>')
add("</defs>")
add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')

# ---- geometry: data flows down the page ----------------------------------------------
# Columns: Raspberry Pi 5 | the demo board (two columns wide) | simulator.
COL_W, GAP = 232, 22
PI_X = 150
BOARD_X, BOARD_W = PI_X + COL_W + GAP, 2 * COL_W + GAP
SIM_X = BOARD_X + BOARD_W + GAP
LEFT, RIGHT = PI_X, SIM_X + COL_W
SRC_Y, SRC_H = 236, 240               # the Pi and simulator boxes
BOARD_Y, BOARD_B = 16, 492            # the demo board container, top and bottom
HOST_Y = BOARD_B + 22                 # ttcap, on the host
STREAM_Y, STREAM_H = 600, 96
LIB_Y = STREAM_Y + STREAM_H + 50
OUT_Y = LIB_Y + 76 + 52


def container(x, title, mark):
    rect(x, SRC_Y, COL_W, SRC_H, SIG_FILL, SIG_EDGE)
    mark(x + 26, SRC_Y + 24)
    text(x + 48, SRC_Y + 29, title, "title")


def inner(x, kind, name, parts, sink):
    """A chip (or a simulator box) holding two parts side by side, then down to `sink`."""
    bx, by, bw, bh = x + 16, SRC_Y + 60, COL_W - 32, 66
    if kind == "chip":
        chip(bx, by, bw, bh, name)
    else:
        rect(bx, by, bw, bh, "#ffffff", PART_EDGE, rx=4, sw=1.25)
        text(bx + 8, by + 15, name, "chipname", fill=MUTED)
    (n1, w1), (n2, w2) = parts
    px = bx + (bw - (w1 + 22 + w2)) / 2
    part(px, by + 26, w1, 28, n1)
    line([(px + w1, by + 40), (px + w1 + 20, by + 40)], "thin")
    part(px + w1 + 22, by + 26, w2, 28, n2)
    sx = px + w1 + 22 + w2 / 2
    line([(sx, by + 54), (sx, SRC_Y + SRC_H - 44)], "thin")
    part(sx - 40, SRC_Y + SRC_H - 42, 80, 26, sink)
    return sx


# ---- the demo board: the design on a breakout, captured by the board's own MCU ---------------
rect(BOARD_X, BOARD_Y, BOARD_W, BOARD_B - BOARD_Y, SIG_FILL, SIG_EDGE)
PW, PHH = 150, 150 * 457 / 420
PH_X, PH_Y = BOARD_X + 16, BOARD_Y + 16
board = base64.b64encode((HERE / "images" / "tt-etr-dbv3p2.jpg").read_bytes()).decode()
rect(PH_X - 6, PH_Y - 6, PW + 12, PHH + 12, "#ffffff", SIG_EDGE, rx=8)
add(f'<image x="{PH_X}" y="{PH_Y}" width="{PW}" height="{PHH:.1f}" '
    f'href="data:image/jpeg;base64,{board}"/>')
tx = PH_X + PW + 22
tt_badge(tx + 14, PH_Y + PHH / 2 - 5, 14)
text(tx + 36, PH_Y + PHH / 2, "Tiny Tapeout demo board", "title")

# Two kinds of breakout: the design alone, or an FPGA with capture logic beside it.
BK_Y, BK_H = 236, 70
bw = (BOARD_W - 48) / 2
A_X, B_X = BOARD_X + 16, BOARD_X + 32 + bw
chip(A_X, BK_Y, bw, BK_H, "ASIC or FPGA breakout")
part(A_X + (bw - 110) / 2, BK_Y + 30, 110, 28, "your design")
chip(B_X, BK_Y, bw, BK_H, "FPGA breakout (iCE40UP5K)")
flow(B_X + (bw - 168) / 2, BK_Y + 30, (("design", 72), ("capture", 74)))
OR_X = A_X + bw + 8
add(f'<circle cx="{OR_X}" cy="{BK_Y + BK_H / 2}" r="14" fill="#ffffff" stroke="{SIG_EDGE}" '
    f'stroke-width="1.25"/>')
text(OR_X, BK_Y + BK_H / 2 + 4.5, "or", "part", "middle")

MCU_Y, MCU_H = 380, 96
A_C, B_C = A_X + bw / 2, B_X + bw / 2
line([(A_C, BK_Y + BK_H + 6), (A_C, MCU_Y - 7)], "sig")
text(A_C + 9, BK_Y + BK_H + 42, "uo_out[7:0]", "net", fill=SIG)
line([(B_C, BK_Y + BK_H + 6), (B_C, MCU_Y - 7)], "sig")
text(B_C + 9, BK_Y + BK_H + 42, "uo_out + uio", "net", fill=SIG)

chip(A_X, MCU_Y, BOARD_W - 32, MCU_H, "RP2040 or RP2350")
part(A_X + 24, MCU_Y + 24, BOARD_W - 80, 28, "PIO")
mid = BOARD_X + BOARD_W / 2
line([(mid - 82, MCU_Y + 52), (mid - 82, MCU_Y + 60)], "thin")
flow(mid - 120, MCU_Y + 62, (("DMA", 76), ("USB", 76)))

# ---- the Raspberry Pi 5, wired to the board's OUTPUT header --------------------------------------
container(PI_X, "Raspberry Pi 5", lambda x, y: nested_svg(
    HERE / "logos" / "raspberry-pi.svg", x - 12, y - 15, 24, 30))
PI_SINK = inner(PI_X, "chip", "RP1", (("PIO", 54), ("DMA", 54)), "DRAM")
TAP_X, TAP_Y = PH_X + PW * 0.76, PH_Y + PHH - 6
WIRE_Y = PH_Y + PHH + 10
PI_C = PI_X + COL_W / 2
line([(TAP_X, TAP_Y), (TAP_X, WIRE_Y), (PI_C, WIRE_Y), (PI_C, SRC_Y - 2)], "sig")
add(f'<circle cx="{TAP_X:.1f}" cy="{TAP_Y:.1f}" r="4.5" fill="{SIG}"/>')
text(PI_C + 9, WIRE_Y + 24, "uo_out", "net", fill=SIG)

# ---- the simulator ---------------------------------------------------------------------------------
container(SIM_X, "Simulator", lambda x, y: add(
    f'<path d="M{x - 11} {y + 4} L{x - 11} {y - 4} L{x - 4} {y - 4} L{x - 4} {y + 4} '
    f'L{x + 3} {y + 4} L{x + 3} {y - 4} L{x + 10} {y - 4}" fill="none" stroke="{SIG}" '
    f'stroke-width="2.25" stroke-linejoin="round"/>'))
SIM_SINK = inner(SIM_X, "sim", "Verilator or Icarus", (("design", 66), ("tracer", 66)), "trace file")
SIM_C = SIM_X + COL_W / 2
doc(SIM_C - 23, 110, 46, 58, "the design's RTL")
line([(SIM_C, 110 + 58 + 24), (SIM_C, SRC_Y - 2)], "sig")

# ---- into the stream: ttcap on the host for the board, the tools themselves elsewhere -------------
usb_x = mid - 120 + 76 + 22 + 38
line([(usb_x, MCU_Y + 90), (usb_x, HOST_Y - 2)], "str")
laptop(usb_x - 29, HOST_Y, 58, 32)
text(usb_x + 40, HOST_Y + 22, "ttcap", "title")
line([(usb_x, HOST_Y + 40), (usb_x, STREAM_Y - 2)], "str")
for cx in (PI_SINK, SIM_SINK):
    line([(cx, SRC_Y + SRC_H - 16), (cx, STREAM_Y - 2)], "str")

# ---- the stream: one format for every source --------------------------------------------------------
rect(LEFT, STREAM_Y, RIGHT - LEFT, STREAM_H, "#f7f8fb", INK2, rx=6, sw=2.5)
text(LEFT + 20, STREAM_Y + 30, "vgacap stream", "title")
# A strip of recorded samples: colour bars, then the blanking and sync between lines.
cells = BARS[:-1] * 3 + ["#000000"] * 8
cw = (RIGHT - LEFT - 40) / len(cells)
for i, c in enumerate(cells):
    add(f'<rect x="{LEFT + 20 + i * cw:.1f}" y="{STREAM_Y + 44}" width="{cw - 1.5:.1f}" '
        f'height="22" rx="2" fill="{c}" stroke="#c9ced8" stroke-width="0.75"/>')
sx = LEFT + 20 + 21 * cw
text(sx + 4 * cw, STREAM_Y + 84, "sync", "detail", "middle")
text(LEFT + 20 + 10.5 * cw, STREAM_Y + 84, "pixels", "detail", "middle")

# ---- reading it back ---------------------------------------------------------------------------
MID = (LEFT + RIGHT) / 2
LW = 260
line([(MID, STREAM_Y + STREAM_H), (MID, LIB_Y - 2)], "str")
rect(MID - LW / 2, LIB_Y, LW, 76, STR_FILL, STR_EDGE)
text(MID, LIB_Y + 32, "libvgaframe", "title", "middle")
text(MID, LIB_Y + 54, "rebuilds every frame", "detail", "middle")

OW = 250
OX = (MID - 40 - OW, MID + 40)
split = LIB_Y + 76 + 24
add(f'<path d="M{MID} {LIB_Y + 76} L{MID} {split} M{OX[0] + OW / 2} {split} '
    f'L{OX[1] + OW / 2} {split}" fill="none" stroke="{PIC}" stroke-width="3"/>')
for ox in OX:
    line([(ox + OW / 2, split), (ox + OW / 2, OUT_Y - 2)], "pic")
rect(OX[0], OUT_Y, OW, 90, PIC_FILL, PIC_EDGE)
monitor(OX[0] + 18, OUT_Y + 18, 72, 44)
text(OX[0] + 108, OUT_Y + 40, "Pictures", "title")
text(OX[0] + 108, OUT_Y + 60, "and video", "title")
rect(OX[1], OUT_Y, OW, 90, PIC_FILL, PIC_EDGE)
check(OX[1] + 54, OUT_Y + 45, 25)
text(OX[1] + 108, OUT_Y + 34, "Pixel-exact", "title")
text(OX[1] + 108, OUT_Y + 54, "checks", "title")
text(OX[1] + 108, OUT_Y + 74, "against references", "detail")

# ---- legend --------------------------------------------------------------------------------------
LY = OUT_Y + 90 + 46
items = (("sig", "signals"), ("str", "captured data"), ("pic", "pictures"))
total = sum(40 + 7.4 * len(label) + 26 for _, label in items) - 26
x = MID - total / 2
for kind, label in items:
    line([(x, LY), (x + 32, LY)], kind)
    text(x + 40, LY + 4.5, label, "legend")
    x += 40 + 7.4 * len(label) + 26

add("</svg>")
(HERE / "overview.svg").write_text("\n".join(out) + "\n")
print("wrote", HERE / "overview.svg", (HERE / "overview.svg").stat().st_size, "bytes")
