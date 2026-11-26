#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw docs/diagrams/overview.svg and overview-dark.svg: vgacap's capture
sources, the shared stream, and what reads it, for the light and the dark
theme.

    uv run --no-project python docs/diagrams/overview.py

Hand-placed, not auto-laid-out. Few words: each capture source is drawn
as the parts that do the work (the chip, and the PIO or logic inside it),
and the colours carry the rest (see the legend). The board photo, the
Tiny Tapeout and the Raspberry Pi logos are embedded from images/ and
logos/; NOTICE.md says where each is from and under what terms.

Every colour comes from one palette, THEMES, which has a light and a dark
entry; the drawing is made once for each. Only the colour bars (a
picture's own colours) and the board photo are the same in both.
"""
from __future__ import annotations

import base64
import pathlib
import re
import struct
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent
MARGIN = 16
# The palette. Each name is set from THEMES before a drawing is made:
#   BG the page; PAPER a white-ish surface (a file, a screen); INK, MUTED text;
#   CHIP a chip body and CHIP_TEXT its name; PART, PART_EDGE a block in a chip;
#   SIG, STR, PIC the three kinds of flow, each with a FILL and an EDGE;
#   CTL ttcap; LEADER the callouts; STREAM_BG, STREAM_EDGE, CELL_EDGE the stream.
THEMES = {
    "": dict(
        BG="#ffffff", PAPER="#ffffff", INK="#1d2433", MUTED="#5b6577",
        CHIP="#2b3445", CHIP_TEXT="#ffffff", PART="#ffffff", PART_EDGE="#7d8799",
        SIG="#1c64d4", SIG_FILL="#eaf1fd", SIG_EDGE="#9dbcef",
        STR="#f2620c", STR_FILL="#fff2e8", STR_EDGE="#f8b48a",
        PIC="#14845a", PIC_FILL="#e7f6ef", PIC_EDGE="#93d3b6",
        CTL="#6b46d6", LEADER="#8a93a6",
        STREAM_BG="#f7f8fb", STREAM_EDGE="#2b3445", CELL_EDGE="#c9ced8"),
    "-dark": dict(
        BG="#16181d", PAPER="#232834", INK="#e6e9ef", MUTED="#a3acbd",
        CHIP="#4a5468", CHIP_TEXT="#ffffff", PART="#232834", PART_EDGE="#8a93a6",
        SIG="#6aa5ff", SIG_FILL="#16233a", SIG_EDGE="#355a91",
        STR="#ff8f45", STR_FILL="#2f1e12", STR_EDGE="#8a4e24",
        PIC="#3fcf91", PIC_FILL="#10291f", PIC_EDGE="#2a7353",
        CTL="#b29cff", LEADER="#7d869a",
        STREAM_BG="#1d2129", STREAM_EDGE="#8a93a6", CELL_EDGE="#454c5c"),
}
PHOTO_BG = "#ffffff"   # the board render is flattened onto white (prep_board.py)
BARS = ["#ffffff", "#ffff00", "#00ffff", "#00ff00", "#ff00ff", "#ff0000", "#0000ff", "#000000"]

out: list[str] = []
add = out.append
_ids = iter(range(1000))


def text(x, y, s, cls="detail", anchor="start", fill=None):
    f = f' fill="{fill}"' if fill else ""
    add(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}"{f}>{escape(s)}</text>')


def rect(x, y, w, h, fill, edge, rx=8, sw=1.25):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
        f'stroke="{edge}" stroke-width="{sw}"/>')


# kind: the palette name of its colour, stroke width, arrowhead size
LINES = {"sig": ("SIG", 3, 3.4), "str": ("STR", 4.5, 3.0), "pic": ("PIC", 3, 3.4),
         "thin": ("SIG", 1.75, 4.6)}


def line(points, kind):
    """A polyline ending in an arrowhead."""
    name, width, _ = LINES[kind]
    colour = globals()[name]
    d = "M" + " L".join(f"{x} {y}" for x, y in points)
    add(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}" '
        f'marker-end="url(#a-{kind})"/>')


def jpeg_size(path):
    """(width, height) from a baseline or progressive JPEG's SOF segment."""
    data = path.read_bytes()
    i = 2
    while i < len(data):
        marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + length
    raise ValueError(f"no SOF segment in {path}")


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
            add(f'<line x1="{px:.1f}" y1="{y1}" x2="{px:.1f}" y2="{y2}" stroke="{CHIP}" '
                f'stroke-width="2" stroke-linecap="round"/>')
    rect(x, y, w, h, CHIP, CHIP, rx=4, sw=1)
    text(x + 8, y + 15, name, "chipname", fill=CHIP_TEXT)


def part(x, y, w, h, name):
    """A block inside a chip (PIO, DMA, capture logic)."""
    rect(x, y, w, h, PART, PART_EDGE, rx=4, sw=1)
    text(x + w / 2, y + h / 2 + 4.5, name, "part", "middle")


def monitor(x, y, w, h):
    rect(x, y, w, h, CHIP, CHIP, rx=4, sw=1)
    bw = (w - 8) / len(BARS)
    for i, c in enumerate(BARS):
        add(f'<rect x="{x + 4 + i * bw:.1f}" y="{y + 4}" width="{bw + 0.2:.1f}" '
            f'height="{h - 8}" fill="{c}"/>')
    add(f'<rect x="{x + w / 2 - 6}" y="{y + h}" width="12" height="6" fill="{CHIP}"/>')
    add(f'<rect x="{x + w / 2 - 16}" y="{y + h + 6}" width="32" height="3" rx="1.5" '
        f'fill="{CHIP}"/>')


def check(cx, cy, r):
    add(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{PIC}"/>')
    add(f'<path d="M{cx - r * .45} {cy} L{cx - r * .1} {cy + r * .38} L{cx + r * .5} '
        f'{cy - r * .35}" fill="none" stroke="#ffffff" stroke-width="{r * .22:.1f}" '
        f'stroke-linecap="round" stroke-linejoin="round"/>')


def laptop(x, y, w, h):
    rect(x + 6, y, w - 12, h, PAPER, CTL, rx=3, sw=1.75)
    add(f'<path d="M{x} {y + h + 6} L{x + w} {y + h + 6} L{x + w - 6} {y + h} '
        f'L{x + 6} {y + h} z" fill="{CTL}"/>')
    text(x + w / 2, y + h / 2 + 4, ">_", "part", "middle", fill=CTL)


def doc(x, y, w, h, label):
    """A source file with a folded corner."""
    f = 10
    add(f'<path d="M{x} {y} L{x + w - f} {y} L{x + w} {y + f} L{x + w} {y + h} L{x} {y + h} z" '
        f'fill="{PAPER}" stroke="{SIG}" stroke-width="1.5"/>')
    add(f'<path d="M{x + w - f} {y} L{x + w - f} {y + f} L{x + w} {y + f}" fill="none" '
        f'stroke="{SIG}" stroke-width="1.5"/>')
    for i in range(3):
        add(f'<line x1="{x + 7}" y1="{y + 18 + 7 * i}" x2="{x + w - 9 - 6 * (i % 2)}" '
            f'y2="{y + 18 + 7 * i}" stroke="{SIG_EDGE}" stroke-width="2"/>')
    text(x + w / 2, y + h + 16, label, "net", "middle", fill=SIG)


# ---- canvas (W and H come from the geometry; see the bottom) -------------------------
def canvas(w, h):
    head = []
    add = head.append
    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
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
  .chipname {{ font-size: 11px; font-weight: 700; letter-spacing: 0.03em; }}
  .part {{ font-size: 12px; font-weight: 700; fill: {INK}; }}
  .legend {{ font-size: 12.5px; fill: {INK}; }}
</style>""")
    add("<defs>")
    for kind, (name, _, mw) in LINES.items():
        colour = globals()[name]
        add(f'<marker id="a-{kind}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="{mw}" '
            f'markerHeight="{mw}" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" '
            f'fill="{colour}"/></marker>')
    add("</defs>")
    add(f'<rect x="0" y="0" width="{w}" height="{h}" fill="{BG}"/>')
    return head


# ---- geometry: data flows down the page ----------------------------------------------
# Columns: Raspberry Pi 5 | the demo board | simulator.
COL_W, BOARD_W, GAP = 210, 560, 22
PI_X = MARGIN
BOARD_X = PI_X + COL_W + GAP
SIM_X = BOARD_X + BOARD_W + GAP
LEFT, RIGHT = PI_X, SIM_X + COL_W
BOARD_Y = MARGIN
SRC_Y, SRC_H = 240, 236               # the Pi and simulator boxes
BOARD_B = SRC_Y + SRC_H               # the board box ends level with them
HOST_Y = BOARD_B + 22                 # ttcap, on the host
STREAM_Y, STREAM_H = HOST_Y + 84, 96
LIB_Y = STREAM_Y + STREAM_H + 50
OUT_Y = LIB_Y + 76 + 52


def container(x, title, mark):
    rect(x, SRC_Y, COL_W, SRC_H, SIG_FILL, SIG_EDGE)
    mark(x + 26, SRC_Y + 24)
    text(x + 48, SRC_Y + 29, title, "title")


def stack(cx, y, names, w=110, h=28, gap=18):
    """Parts one above the other, joined by thin downward arrows; returns the bottom edge."""
    for i, name in enumerate(names):
        part(cx - w / 2, y, w, h, name)
        if i < len(names) - 1:
            line([(cx, y + h), (cx, y + h + gap - 2)], "thin")
        y += h + gap
    return y - gap


def inner(x, kind, name, parts, sink):
    """A chip (or a simulator box) holding parts stacked top to bottom, then down to `sink`."""
    bx, by, bw, bh = x + 16, SRC_Y + 52, COL_W - 32, 116
    cx = x + COL_W / 2
    if kind == "chip":
        chip(bx, by, bw, bh, name)
    else:
        rect(bx, by, bw, bh, PAPER, PART_EDGE, rx=4, sw=1.25)
        text(bx + 8, by + 15, name, "chipname", fill=MUTED)
    bottom = stack(cx, by + 26, parts)
    line([(cx, bottom), (cx, SRC_Y + SRC_H - 44)], "thin")
    part(cx - 48, SRC_Y + SRC_H - 42, 96, 26, sink)


def leader(x1, y1, x2, y2):
    """A callout from a spot on the board render to the part it is."""
    add(f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="5" fill="none" stroke="{LEADER}" stroke-width="1.5"/>')
    add(f'<path d="M{x1 - 5:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}" fill="none" stroke="{LEADER}" '
        f'stroke-width="1.25" stroke-dasharray="3 3"/>')
    add(f'<circle cx="{x2:.1f}" cy="{y2:.1f}" r="2.5" fill="{LEADER}"/>')


def draw():
    """The figure, in the current theme's colours; returns its width and height."""
    global out, add
    out = []
    add = out.append
    # ---- the demo board: what is on it (left), the board itself (right) ----------------------------
    rect(BOARD_X, BOARD_Y, BOARD_W, BOARD_B - BOARD_Y, SIG_FILL, SIG_EDGE)
    tt_badge(BOARD_X + 26, BOARD_Y + 24, 14)
    text(BOARD_X + 48, BOARD_Y + 29, "Tiny Tapeout demo board", "title")

    BOARD_IMG = HERE / "images" / "tt-etr-dbv3p2.jpg"
    PW = 180
    PHH = PW * jpeg_size(BOARD_IMG)[1] / jpeg_size(BOARD_IMG)[0]
    INNER_W = BOARD_W - 16 - PW - 28 - 16          # the column of parts
    A_X = BOARD_X + 16
    BK_Y, BK_H = BOARD_Y + 72, 116
    bw = (INNER_W - 36) / 2
    B_X = A_X + bw + 36
    MCU_H = 146
    MCU_Y = BOARD_B - 16 - MCU_H

    text(A_X + INNER_W / 2, BK_Y - 10, "breakout", "detail", "middle")
    chip(A_X, BK_Y, bw, BK_H, "ASIC or FPGA")
    part(A_X + (bw - 110) / 2, BK_Y + 49, 110, 28, "your design")
    chip(B_X, BK_Y, bw, BK_H, "iCE40UP5K FPGA")
    stack(B_X + bw / 2, BK_Y + 26, ("design", "capture"))
    OR_X = A_X + bw + 18
    add(f'<circle cx="{OR_X}" cy="{BK_Y + BK_H / 2}" r="14" fill="{PAPER}" stroke="{SIG_EDGE}" '
        f'stroke-width="1.25"/>')
    text(OR_X, BK_Y + BK_H / 2 + 4.5, "or", "part", "middle")

    A_C, B_C = A_X + bw / 2, B_X + bw / 2
    mid = A_X + INNER_W / 2
    JOIN_Y = MCU_Y - 24
    add(f'<path d="M{A_C} {BK_Y + BK_H + 6} L{A_C} {JOIN_Y} L{B_C} {JOIN_Y} L{B_C} {BK_Y + BK_H + 6}" '
        f'fill="none" stroke="{SIG}" stroke-width="3" stroke-linejoin="round"/>')
    text(A_C + 9, (BK_Y + BK_H + JOIN_Y) / 2 + 18, "uo_out[7:0]", "net", fill=SIG)
    text(B_C + 9, (BK_Y + BK_H + JOIN_Y) / 2 + 18, "uo_out + uio", "net", fill=SIG)

    MCU_W = 220
    chip(mid - MCU_W / 2, MCU_Y, MCU_W, MCU_H, "RP2040 or RP2350")
    line([(mid, JOIN_Y), (mid, MCU_Y - 7)], "sig")
    part(mid - 55, MCU_Y + 22, 110, 28, "PIO")
    line([(mid, MCU_Y + 50), (mid, MCU_Y + 64)], "thin")
    MCU_BOTTOM = stack(mid, MCU_Y + 66, ("DMA", "USB"), w=110, h=26)

    # The board render, with callouts to the breakout socket and to the board's RP2350.
    PH_X = BOARD_X + BOARD_W - 16 - PW
    PH_Y = (BK_Y + BOARD_B - 16) / 2 - PHH / 2
    board = base64.b64encode(BOARD_IMG.read_bytes()).decode()
    rect(PH_X - 6, PH_Y - 6, PW + 12, PHH + 12, PHOTO_BG, SIG_EDGE, rx=8)
    add(f'<image x="{PH_X}" y="{PH_Y:.1f}" width="{PW}" height="{PHH:.1f}" '
        f'href="data:image/jpeg;base64,{board}"/>')
    # Spots on the render (fractions of its width and height): the breakout's left
    # connector, and the RP2350B under the breakout.
    leader(PH_X + 0.31 * PW, PH_Y + 0.34 * PHH, B_X + bw, BK_Y + BK_H / 2)
    leader(PH_X + 0.51 * PW, PH_Y + 0.50 * PHH, mid + MCU_W / 2, MCU_Y + MCU_H / 2)

    # ---- the Raspberry Pi 5, wired to the same uo_out -------------------------------------------------
    container(PI_X, "Raspberry Pi 5", lambda x, y: nested_svg(
        HERE / "logos" / "raspberry-pi.svg", x - 12, y - 15, 24, 30))
    inner(PI_X, "chip", "RP1", ("PIO", "DMA"), "DRAM")
    PI_C = PI_X + COL_W / 2
    TAP_Y = BK_Y + BK_H + 12
    assert TAP_Y < SRC_Y - 16, "the Pi's tap must drop into its box"
    line([(A_C, TAP_Y), (PI_C, TAP_Y), (PI_C, SRC_Y - 2)], "sig")
    add(f'<circle cx="{A_C:.1f}" cy="{TAP_Y}" r="4.5" fill="{SIG}"/>')

    # ---- the simulator ---------------------------------------------------------------------------------
    container(SIM_X, "Simulator", lambda x, y: add(
        f'<path d="M{x - 11} {y + 4} L{x - 11} {y - 4} L{x - 4} {y - 4} L{x - 4} {y + 4} '
        f'L{x + 3} {y + 4} L{x + 3} {y - 4} L{x + 10} {y - 4}" fill="none" stroke="{SIG}" '
        f'stroke-width="2.25" stroke-linejoin="round"/>'))
    inner(SIM_X, "sim", "Verilator or Icarus", ("design", "tracer"), "trace file")
    SIM_C = SIM_X + COL_W / 2
    doc(SIM_C - 23, 60, 46, 58, "the design's RTL")
    line([(SIM_C, 60 + 58 + 24), (SIM_C, SRC_Y - 2)], "sig")

    # ---- into the stream: ttcap on the host for the board, the tools themselves elsewhere -------------
    usb_x = mid
    line([(usb_x, MCU_BOTTOM), (usb_x, HOST_Y - 2)], "str")
    laptop(usb_x - 29, HOST_Y, 58, 32)
    text(usb_x + 40, HOST_Y + 22, "ttcap", "title")
    line([(usb_x, HOST_Y + 40), (usb_x, STREAM_Y - 2)], "str")
    for cx in (PI_C, SIM_C):
        line([(cx, SRC_Y + SRC_H - 16), (cx, STREAM_Y - 2)], "str")
    # The three sources are alternatives: an "or" between each pair of the
    # arrows into the stream, as between the two breakouts above.
    OR_Y = STREAM_Y - 26
    for ox in ((PI_C + usb_x) / 2, (usb_x + SIM_C) / 2):
        add(f'<circle cx="{ox:.1f}" cy="{OR_Y}" r="14" fill="{PAPER}" stroke="{STR_EDGE}" '
            f'stroke-width="1.25"/>')
        text(ox, OR_Y + 4.5, "or", "part", "middle")

    # ---- the stream: one format for every source --------------------------------------------------------
    rect(LEFT, STREAM_Y, RIGHT - LEFT, STREAM_H, STREAM_BG, STREAM_EDGE, rx=6, sw=2.5)
    text(LEFT + 20, STREAM_Y + 30, "vgacap stream", "title")
    # A strip of recorded samples: colour bars, then the blanking and sync between lines.
    N_PIXELS, N_SYNC = 3 * (len(BARS) - 1), 8
    cells = BARS[:-1] * 3 + ["#000000"] * N_SYNC
    cw = (RIGHT - LEFT - 40) / len(cells)
    for i, c in enumerate(cells):
        add(f'<rect x="{LEFT + 20 + i * cw:.1f}" y="{STREAM_Y + 44}" width="{cw - 1.5:.1f}" '
            f'height="22" rx="2" fill="{c}" stroke="{CELL_EDGE}" stroke-width="0.75"/>')
    text(LEFT + 20 + (N_PIXELS + N_SYNC / 2) * cw, STREAM_Y + 84, "sync", "detail", "middle")
    text(LEFT + 20 + N_PIXELS / 2 * cw, STREAM_Y + 84, "pixels", "detail", "middle")

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
    items = (("sig", "from the design"), ("str", "captured data"), ("pic", "pictures"))
    total = sum(40 + 7.4 * len(label) + 26 for _, label in items) - 26
    x = MID - total / 2
    for kind, label in items:
        line([(x, LY), (x + 32, LY)], kind)
        text(x + 40, LY + 4.5, label, "legend")
        x += 40 + 7.4 * len(label) + 26

    add("</svg>")
    w = RIGHT + MARGIN
    h = LY + 6 + MARGIN                   # the legend's text sits about 6 px below LY
    out[:0] = canvas(w, h)
    return out


for suffix, palette in THEMES.items():
    globals().update(palette)
    svg = HERE / f"overview{suffix}.svg"
    svg.write_text("\n".join(draw()) + "\n")
    print("wrote", svg, svg.stat().st_size, "bytes")
