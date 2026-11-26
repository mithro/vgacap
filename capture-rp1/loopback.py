#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw capture-rp1/loopback.svg and loopback-dark.svg: the Raspberry Pi 5
loopback fixture, in which one RP1 PIO state machine drives the GPIOs that
another samples, with nothing on the 40-pin header.

    uv run --no-project python capture-rp1/loopback.py

Data flows down the page: the generator, the GPIOs, the sampler, the DMA,
the file. Both twins come from one palette.
"""
from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent

PALETTES = {
    "light": {"paper": "#ffffff", "ink": "#1d2433", "muted": "#5b6577",
              "pio": "#6b46d6", "pio_fill": "#f1ecfd", "pin": "#1c64d4", "pin_fill": "#eaf1fd",
              "host": "#14845a", "host_fill": "#e7f6ef", "clk": "#c05a0c", "empty": "#a3aab8"},
    "dark": {"paper": "#131416", "ink": "#e6e9ef", "muted": "#a0a8b8",
             "pio": "#a78bfa", "pio_fill": "#251d3d", "pin": "#6ea2f5", "pin_fill": "#16243b",
             "host": "#4cc38a", "host_fill": "#173326", "clk": "#f0904a", "empty": "#5d6574"},
}

W = 560
M = 2
FONT = "font-family=\"Inter, 'Segoe UI', Helvetica, Arial, sans-serif\""


def draw(p):
    out = []
    add = out.append

    def text(x, y, s, size=13, fill=None, anchor="middle", weight="normal", mono=False):
        family = ' font-family="ui-monospace, Menlo, Consolas, monospace"' if mono else ""
        add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill or p["ink"]}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{family}>{escape(s)}</text>')

    def box(x, y, w, h, edge, fill, dash=False):
        d = ' stroke-dasharray="6 4"' if dash else ""
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" '
            f'stroke="{edge}" stroke-width="1.5"{d}/>')

    def arrow(x1, y1, x2, y2, colour, width=2.5):
        add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2 - 7}" stroke="{colour}" stroke-width="{width}"/>')
        add(f'<path d="M{x2 - 6} {y2 - 9} L{x2} {y2} L{x2 + 6} {y2 - 9} Z" fill="{colour}"/>')

    body = []
    # The RP1 outline holds the two state machines and the GPIOs.
    rp1_x, rp1_y, rp1_w, rp1_h = M, M, W - 2 * M - 150, 318
    # Generator state machine.
    gx, gy, gw, gh = rp1_x + 20, rp1_y + 36, rp1_w - 40, 58
    # GPIOs.
    px, py, ph = gx, gy + gh + 44, 52
    # Sampler state machine.
    sx, sy, sh = gx, py + ph + 44, 58
    height = rp1_y + rp1_h + 40 + 52 + 40 + 52 + M

    add(f'<rect x="0" y="0" width="{W}" height="{height}" fill="{p["paper"]}"/>')
    box(rp1_x, rp1_y, rp1_w, rp1_h, p["muted"], "none")
    text(rp1_x + 14, rp1_y + 22, "RP1", 14, p["muted"], "start", "bold")

    box(gx, gy, gw, gh, p["pio"], p["pio_fill"])
    text(gx + gw / 2, gy + 24, "state machine: generator", 14, weight="bold")
    text(gx + gw / 2, gy + 44, "--gen-counter", 12, p["muted"], mono=True)

    # Two arrows down: data and clock.
    dx, cx = gx + gw * 0.38, gx + gw * 0.82
    arrow(dx, gy + gh, dx, py, p["pin"])
    arrow(cx, gy + gh, cx, py, p["clk"])
    text(dx - 8, gy + gh + 27, "out 8 bits", 12, p["pin"], "end")
    text(cx - 8, gy + gh + 27, "clock", 12, p["clk"], "end")

    # The GPIOs, data and clock.
    dw = gw * 0.62
    box(px, py, dw, ph, p["pin"], p["pin_fill"])
    text(px + dw / 2, py + 22, "GPIO 4 to GPIO 11", 14, weight="bold")
    text(px + dw / 2, py + 41, "data", 12, p["muted"])
    kx, kw = px + dw + 12, gw - dw - 12
    box(kx, py, kw, ph, p["clk"], p["pin_fill"])
    text(kx + kw / 2, py + 22, "GPIO 12", 14, weight="bold")
    text(kx + kw / 2, py + 41, "clock", 12, p["muted"])

    # The empty header, beside the GPIOs.
    hx, hw = rp1_x + rp1_w + 24, 126
    add(f'<line x1="{px + gw}" y1="{py + ph / 2}" x2="{hx}" y2="{py + ph / 2}" '
        f'stroke="{p["empty"]}" stroke-width="1.5" stroke-dasharray="4 4"/>')
    box(hx, py - 6, hw, ph + 12, p["empty"], "none", dash=True)
    text(hx + hw / 2, py + 20, "40-pin header", 13, p["muted"], weight="bold")
    text(hx + hw / 2, py + 40, "no wires", 12, p["muted"])

    arrow(dx, py + ph, dx, sy, p["pin"])
    arrow(cx, py + ph, cx, sy, p["clk"])
    text(dx - 8, py + ph + 27, "in pins, 8", 12, p["pin"], "end")
    text(cx - 8, py + ph + 27, "wait for edge", 12, p["clk"], "end")

    box(sx, sy, gw, sh, p["pio"], p["pio_fill"])
    text(sx + gw / 2, sy + 24, "state machine: sampler", 14, weight="bold")
    text(sx + gw / 2, sy + 44, "RX FIFO, 8 words", 12, p["muted"])

    # Below the RP1: DMA into DRAM, then the file.
    mid = rp1_x + rp1_w / 2
    ay = rp1_y + rp1_h
    arrow(dx, sy + sh, dx, ay + 40, p["host"])
    text(dx + 10, ay + 26, "DMA", 12, p["host"], "start")
    bw = gw
    box(gx, ay + 40, bw, 52, p["host"], p["host_fill"])
    text(mid, ay + 63, "DRAM: one capture buffer", 14, weight="bold")
    text(mid, ay + 81, "32 KB a transfer, 4 queued", 12, p["muted"])
    fy = ay + 40 + 52 + 40
    arrow(dx, ay + 92, dx, fy, p["host"])
    box(gx, fy, bw, 52, p["host"], p["host_fill"])
    text(mid, fy + 23, "counter.vgacap", 14, weight="bold", mono=True)
    text(mid, fy + 41, "VGCH, RAW chunks, TIME", 12, p["muted"])

    body.extend(out)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
           f'viewBox="0 0 {W} {height}" {FONT}>\n' + "\n".join(body) + "\n</svg>\n")
    return svg


def main():
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        (HERE / f"loopback{suffix}.svg").write_text(draw(PALETTES[theme]))


if __name__ == "__main__":
    main()
