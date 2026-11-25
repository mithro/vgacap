#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw docs/diagrams/overview.svg: vgacap's capture sources, the shared
stream, and what reads it.

    uv run --no-project python docs/diagrams/overview.py

Hand-placed, not auto-laid-out. Few words: each capture source is drawn
as the parts that do the work (the chip, and the PIO or logic inside it),
and the colours carry the rest (see the legend). The board photo, the
Tiny Tapeout and the Raspberry Pi logos are embedded from photos/ and
logos/; logos/NOTICE.md says where each is from and under what terms.
"""
from __future__ import annotations

import base64
import pathlib
import re
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent
W, H = 1154, 1000
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


# ---- canvas ------------------------------------------------------------------------
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
    f'font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif" '
    f'role="img" aria-labelledby="t d">')
add('<title id="t">vgacap overview</title>')
add('<desc id="d">A Tiny Tapeout demo board\'s uo_out is captured by the board\'s own '
    'RP2040 or RP2350 (PIO and DMA), a Raspberry Pi 5 (RP1 PIO and DMA), capture logic in an '
    'FPGA, or a simulator; all write one vgacap stream, which libvgaframe turns into pictures, '
    'video and pixel-exact checks.</desc>')
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
SRC_Y, SRC_W, SRC_H, GAP = 318, 232, 180, 22
SRC_XS = [150 + i * (SRC_W + GAP) for i in range(4)]
CX = [x + SRC_W / 2 for x in SRC_XS]          # centre of each source
STREAM_Y, STREAM_H = SRC_Y + SRC_H + 64, 92
LIB_Y = STREAM_Y + STREAM_H + 52
OUT_Y = LIB_Y + 76 + 52
LEFT, RIGHT = SRC_XS[0], SRC_XS[3] + SRC_W

# ---- the inputs: the board, and the design's source for the simulator ----------------------
PH = 206
PH_X, PH_Y = CX[1] - PH / 2 - 40, 22
photo = base64.b64encode((HERE / "photos" / "etr-demoboard.jpg").read_bytes()).decode()
cid = f"clip{next(_ids)}"
add(f'<clipPath id="{cid}"><rect x="{PH_X}" y="{PH_Y}" width="{PH}" height="{PH}" rx="10"/></clipPath>')
add(f'<image x="{PH_X}" y="{PH_Y}" width="{PH}" height="{PH}" clip-path="url(#{cid})" '
    f'preserveAspectRatio="xMidYMid slice" href="data:image/jpeg;base64,{photo}"/>')
rect(PH_X, PH_Y, PH, PH, "none", INK, rx=10, sw=1.5)
text(PH_X + PH + 18, PH_Y + 92, "Tiny Tapeout demo board", "title")
text(PH_X + PH + 18, PH_Y + 112, "an ASIC or an FPGA breakout on top;", "detail")
text(PH_X + PH + 18, PH_Y + 128, "the design drives VGA on uo_out", "detail")

# uo_out leaves the board once, on one bus that drops into three sources.
TAP_X = PH_X + PH - 40               # the OUTPUT header, bottom right of the photo
BUS_Y = SRC_Y - 40
add(f'<path d="M{TAP_X} {PH_Y + PH} L{TAP_X} {BUS_Y}" fill="none" stroke="{SIG}" stroke-width="3"/>')
add(f'<path d="M{CX[0]} {BUS_Y} L{CX[2]} {BUS_Y}" fill="none" stroke="{SIG}" stroke-width="3"/>')
add(f'<circle cx="{TAP_X}" cy="{BUS_Y}" r="4.5" fill="{SIG}"/>')
text(TAP_X + 10, PH_Y + PH + 30, "uo_out[7:0]", "net", fill=SIG)
for cx in CX[:3]:
    line([(cx, BUS_Y), (cx, SRC_Y - 2)], "sig")

doc(CX[3] - 23, PH_Y + 112, 46, 58, "the design's RTL")
line([(CX[3], PH_Y + 112 + 58 + 24), (CX[3], SRC_Y - 2)], "sig")

# ---- the capture sources ---------------------------------------------------------------------
def container(x, title, mark):
    rect(x, SRC_Y, SRC_W, SRC_H, SIG_FILL, SIG_EDGE)
    mark(x + 26, SRC_Y + 24)
    text(x + 48, SRC_Y + 29, title, "title")


def inner(x, kind, name, parts, sink):
    """A chip (or a simulator box) holding two parts side by side, then down to `sink`."""
    bx, by, bw, bh = x + 16, SRC_Y + 52, SRC_W - 32, 66
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
    if sink:
        sx = px + w1 + 22 + w2 / 2
        line([(sx, by + 54), (sx, SRC_Y + SRC_H - 36)], "thin")
        part(sx - 40, SRC_Y + SRC_H - 34, 80, 26, sink)


container(SRC_XS[0], "Demo board", lambda x, y: tt_badge(x, y, 14))
inner(SRC_XS[0], "chip", "RP2040 or RP2350", (("PIO", 54), ("DMA", 54)), "USB")
container(SRC_XS[1], "Raspberry Pi 5", lambda x, y: nested_svg(
    HERE / "logos" / "raspberry-pi.svg", x - 12, y - 15, 24, 30))
inner(SRC_XS[1], "chip", "RP1", (("PIO", 54), ("DMA", 54)), "DRAM")
container(SRC_XS[2], "FPGA", lambda x, y: tt_badge(x, y, 14))
inner(SRC_XS[2], "chip", "FPGA", (("project", 66), ("capture", 70)), "its link")
container(SRC_XS[3], "Simulator", lambda x, y: add(
    f'<path d="M{x - 11} {y + 4} L{x - 11} {y - 4} L{x - 4} {y - 4} L{x - 4} {y + 4} '
    f'L{x + 3} {y + 4} L{x + 3} {y - 4} L{x + 10} {y - 4}" fill="none" stroke="{SIG}" '
    f'stroke-width="2.25" stroke-linejoin="round"/>'))
inner(SRC_XS[3], "sim", "Verilator or Icarus", (("design", 66), ("tracer", 66)), "trace file")

# ttcap drives the demo board from the side, clear of the bus.
laptop(20, SRC_Y + 70, 58, 34)
text(49, SRC_Y + 132, "ttcap", "title", "middle")
line([(84, SRC_Y + 87), (SRC_XS[0] - 2, SRC_Y + 87)], "ctl")

# Every source writes the same stream.
for cx in CX:
    line([(cx, SRC_Y + SRC_H), (cx, STREAM_Y - 2)], "str")

# ---- the stream ----------------------------------------------------------------------------------
rect(LEFT, STREAM_Y, RIGHT - LEFT, STREAM_H, "#f7f8fb", INK2, rx=6, sw=2.5)
text(LEFT + 20, STREAM_Y + 52, "vgacap stream", "title")
chunks = (("VGCH", INK2), ("RAW", STR), ("RLE", STR), ("FRAM", STR), ("EVNT", STR),
          ("TIME", MUTED))
pw, pg = 86, 18
px = RIGHT - 22 - len(chunks) * pw - (len(chunks) - 1) * pg
for name, edge in chunks:
    rect(px, STREAM_Y + 30, pw, 32, "#ffffff", edge, rx=4, sw=1.75)
    text(px + pw / 2, STREAM_Y + 51, name, "pad", "middle")
    px += pw + pg

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
text(OX[1] + 108, OUT_Y + 40, "Pixel-exact", "title")
text(OX[1] + 108, OUT_Y + 60, "checks", "title")

# ---- legend --------------------------------------------------------------------------------------
LY = OUT_Y + 90 + 46
items = (("sig", "signals"), ("str", "stream bytes"), ("pic", "pictures"), ("ctl", "control"))
total = sum(40 + 7.4 * len(label) + 26 for _, label in items) - 26
x = MID - total / 2
for kind, label in items:
    line([(x, LY), (x + 32, LY)], kind)
    text(x + 40, LY + 4.5, label, "legend")
    x += 40 + 7.4 * len(label) + 26

add("</svg>")
(HERE / "overview.svg").write_text("\n".join(out) + "\n")
print("wrote", HERE / "overview.svg", (HERE / "overview.svg").stat().st_size, "bytes")
