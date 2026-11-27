#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw docs/diagrams/firmware-datapath.svg and firmware-datapath-dark.svg:
where a sample goes in the vgacap firmware, from the design's uo_out pins to
the host's file (firmware/explanation-capture.md).

    uv run --no-project python docs/diagrams/firmware_datapath.py

Data flows down the page. Stream and frame captures differ only in the
buffer the DMA fills, drawn side by side with an "or" between them. One
palette gives both themes.
"""
from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent
MARGIN = 16

THEMES = {
    "light": dict(bg="#ffffff", ink="#1d2433", muted="#5b6577", sig="#1c64d4",
                  sig_fill="#eaf1fd", sig_edge="#9dbcef", data="#f2620c", data_fill="#fff2e8",
                  data_edge="#f8b48a", chip="#2b3445", part="#ffffff", part_edge="#7d8799",
                  host="#6b46d6"),
    "dark": dict(bg="#131416", ink="#e6e9ef", muted="#a3acbd", sig="#6ea8ff",
                 sig_fill="#15233b", sig_edge="#34507e", data="#ff8a3d", data_fill="#2e1d10",
                 data_edge="#7a4520", chip="#3a4357", part="#1e2230", part_edge="#6b7385",
                 host="#a58bff"),
}

W = 520
CX = W / 2


def draw(theme: str) -> str:
    c = THEMES[theme]
    out: list[str] = []
    add = out.append

    def text(x, y, s, cls, anchor="middle", fill=None):
        f = f' fill="{fill}"' if fill else ""
        add(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}"{f}>{escape(s)}</text>')

    def rect(x, y, w, h, fill, edge, rx=6, sw=1.25):
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{edge}" stroke-width="{sw}"/>')

    def arrow(x, y1, y2, kind, width):
        add(f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2}" stroke="{c[kind]}" '
            f'stroke-width="{width}" marker-end="url(#m-{kind})"/>')

    def part(x, y, w, h, name, sub=None):
        rect(x, y, w, h, c["part"], c["part_edge"], rx=4, sw=1)
        if sub:
            text(x + w / 2, y + h / 2 - 2, name, "part")
            text(x + w / 2, y + h / 2 + 13, sub, "sub")
        else:
            text(x + w / 2, y + h / 2 + 4.5, name, "part")

    def chip(x, y, w, h, name):
        n = max(3, int(w // 18))
        for i in range(n):
            px = x + w * (i + 0.5) / n
            for y1, y2 in ((y - 5, y), (y + h, y + h + 5)):
                add(f'<line x1="{px:.1f}" y1="{y1}" x2="{px:.1f}" y2="{y2}" stroke="{c["chip"]}" '
                    f'stroke-width="2" stroke-linecap="round"/>')
        rect(x, y, w, h, c["chip"], c["chip"], rx=4, sw=1)
        text(x + 8, y + 15, name, "chipname", "start", fill="#ffffff")

    body: list[str] = []
    out = body
    add = body.append

    # the board
    BY = MARGIN
    y = BY + 46
    # breakout with the design
    BK_W, BK_H = 220, 64
    chip(CX - BK_W / 2, y, BK_W, BK_H, "ASIC or FPGA breakout")
    part(CX - 60, y + 26, 120, 28, "your design")
    y += BK_H + 6
    arrow(CX, y, y + 54, "sig", 3)
    text(CX + 10, y + 24, "uo_out[7:0]", "net", "start", fill=c["sig"])
    text(CX + 10, y + 40, "GPIO 33 to 40", "sub", "start")
    y += 62
    # the RP2350B
    MCU_Y = y
    MCU_W = 380
    parts_x = CX - 90
    py = MCU_Y + 28
    part(parts_x, py, 180, 30, "PIO block 1")
    arrow(CX, py + 30, py + 46, "data", 2.5)
    py += 48
    part(parts_x, py, 180, 30, "RX FIFO")
    arrow(CX, py + 30, py + 46, "data", 2.5)
    py += 48
    part(parts_x, py, 180, 30, "DMA, 2 channels")
    # the arena: one SRAM block, used as two buffers (stream) or one window (frame)
    ar_y = py + 30 + 16
    arrow(CX, py + 30, ar_y - 2, "data", 2.5)
    AW = 336
    rect(CX - AW / 2, ar_y, AW, 92, c["data_fill"], c["data_edge"], rx=6)
    text(CX, ar_y + 20, "440 KB arena", "part")
    bw, gap = 136, 36
    lx, rx = CX - gap / 2 - bw, CX + gap / 2
    buf_y = ar_y + 34
    part(lx, buf_y, bw, 44, "2 buffers", "stream")
    part(rx, buf_y, bw, 44, "1 window", "frame")
    add(f'<circle cx="{CX}" cy="{buf_y + 22}" r="13" fill="{c["part"]}" stroke="{c["part_edge"]}"/>')
    text(CX, buf_y + 26.5, "or", "part")
    join = ar_y + 92
    arrow(CX, join, join + 14, "data", 2.5)
    py = join + 16
    part(parts_x, py, 180, 30, "TinyUSB serial port")
    MCU_H = py + 30 + 16 - MCU_Y
    # chip drawn behind its parts: insert it before them
    chip_svg: list[str] = []
    out = chip_svg
    add = chip_svg.append
    chip(CX - MCU_W / 2, MCU_Y, MCU_W, MCU_H, "RP2350B, running the vgacap firmware")
    out = body
    add = body.append
    idx = body.index(next(s for s in body if 'PIO block 1' in s)) - 1
    body[idx:idx] = chip_svg
    board_bottom = MCU_Y + MCU_H + 18
    # board box behind everything
    board_svg: list[str] = []
    out = board_svg
    add = board_svg.append
    rect(MARGIN, BY, W - 2 * MARGIN, board_bottom - BY, c["sig_fill"], c["sig_edge"], rx=8)
    text(MARGIN + 14, BY + 26, "Tiny Tapeout demo board", "title", "start")
    out = body
    add = body.append
    body[0:0] = board_svg

    # USB to the host
    y = board_bottom
    arrow(CX, MCU_Y + MCU_H + 5, y + 50, "data", 4)
    text(CX + 12, y + 30, "USB", "net", "start", fill=c["data"])
    y += 54
    rect(CX - 110, y, 220, 40, c["part"], c["host"], rx=6, sw=1.75)
    text(CX, y + 25, "ttcap on the host", "part")
    arrow(CX, y + 40, y + 76, "data", 4)
    text(CX + 12, y + 62, "VGCH header, then the chunks", "sub", "start")
    y += 80
    rect(CX - 110, y, 220, 40, c["data_fill"], c["data_edge"], rx=6)
    text(CX, y + 25, "capture.vgacap", "part")
    y += 40

    # legend
    ly = y + 34
    x = CX - 150
    for kind, label, width in (("sig", "signals", 3), ("data", "captured samples", 4)):
        add(f'<line x1="{x}" y1="{ly}" x2="{x + 32}" y2="{ly}" stroke="{c[kind]}" '
            f'stroke-width="{width}" marker-end="url(#m-{kind})"/>')
        text(x + 40, ly + 4.5, label, "legend", "start")
        x += 40 + 7.4 * len(label) + 40
    H = ly + 6 + MARGIN

    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif" '
        f'role="img" aria-labelledby="t d">',
        '<title id="t">The vgacap firmware data path</title>',
        '<desc id="d">uo_out from the design on the breakout enters PIO block 1 of the RP2350B, '
        'then its RX FIFO and two DMA channels, into a 440 KB arena used as two buffers for a stream '
        'or one window for a frame, then out of the TinyUSB serial port over USB to ttcap, which writes a '
        '.vgacap file.</desc>',
        f"""<style>
  .title {{ font-size: 15px; font-weight: 600; fill: {c['ink']}; }}
  .part {{ font-size: 13px; font-weight: 700; fill: {c['ink']}; }}
  .sub {{ font-size: 12px; fill: {c['muted']}; }}
  .net {{ font-size: 12.5px; font-weight: 700; }}
  .chipname {{ font-size: 11px; font-weight: 700; letter-spacing: 0.03em; }}
  .legend {{ font-size: 12.5px; fill: {c['ink']}; }}
</style>""",
        "<defs>",
    ]
    for kind in ("sig", "data"):
        head.append(f'<marker id="m-{kind}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="3.2" '
                    f'markerHeight="3.2" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" '
                    f'fill="{c[kind]}"/></marker>')
    head.append("</defs>")
    head.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="{c["bg"]}"/>')
    return "\n".join(head + body + ["</svg>"]) + "\n"


for theme, name in (("light", "firmware-datapath.svg"), ("dark", "firmware-datapath-dark.svg")):
    (HERE / name).write_text(draw(theme))
    print("wrote", HERE / name)
