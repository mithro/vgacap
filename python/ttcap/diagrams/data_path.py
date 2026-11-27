#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw data-path.svg and data-path-dark.svg: a stream capture on a demo
board, from the design's pins to the file on the host.

    uv run --no-project python python/ttcap/diagrams/data_path.py

The data flows down: the design's uo_out into the PIO sampler, the RX
FIFO, the two DMA buffers that refill each other, the MicroPython loop
that sends them as chunks, the USB link, and ttcap writing the file. The
project clock goes up from the microcontroller to the design.
"""
from __future__ import annotations

import pathlib

from palette import Svg

HERE = pathlib.Path(__file__).resolve().parent
W, H = 500, 664
CX = 250          # the column the data runs down


def draw(theme):
    s = Svg(W, H, theme)
    c = s.c

    # The design, on its breakout.
    s.rect(110, 8, 280, 56, c["sig_fill"], c["sig_edge"])
    s.label(CX, 33, "Tiny Tapeout design", 16, weight="bold")
    s.label(CX, 53, "ASIC or FPGA breakout", 13, c["muted"])

    # The microcontroller, holding the capture.
    s.rect(30, 128, 440, 362, c["chip"], c["chip"], rx=8)
    s.label(44, 152, "RP2040 or RP2350", 14, c["chip_text"], "start", "bold")
    s.label(44, 170, "stock firmware", 13, c["chip_text"], "start")

    # uo_out down into the PIO; the project clock up to the design.
    s.arrow([(CX, 64), (CX, 172)], c["sig"], 3)
    s.label(CX + 10, 92, "uo_out[7:0]", 13, c["sig"], "start")
    s.rect(414, 184, 44, 26, c["part"], c["part_edge"], rx=4, sw=1.2)
    s.label(436, 202, "PWM", 12)
    s.arrow([(414, 202), (357, 202)], c["sig"], 2)
    s.arrow([(436, 184), (436, 104), (360, 104), (360, 68)], c["sig"], 2)
    s.label(370, 96, "project clock", 13, c["sig"], "start")

    def part(y, h, name, sub, w=280):
        s.rect(CX - w / 2, y, w, h, c["part"], c["part_edge"], rx=5, sw=1.2)
        s.label(CX, y + 22, name, 15, weight="bold")
        s.label(CX, y + 41, sub, 13, c["muted"])

    part(176, 52, "PIO sampler", "one sample a clock edge", 210)
    s.arrow([(CX, 228), (CX, 262)], c["str"], 3)
    s.label(CX + 10, 250, "RX FIFO", 13, c["chip_text"], "start")

    # The DMA ring: two buffers, each channel chained to the other.
    s.rect(110, 266, 280, 112, c["part"], c["part_edge"], rx=5, sw=1.2)
    s.label(CX, 288, "DMA ring", 15, weight="bold")
    for x, name in ((136, "buffer A"), (274, "buffer B")):
        s.rect(x, 302, 90, 40, c["str_fill"], c["str_edge"], rx=4, sw=1.2)
        s.label(x + 45, 327, name, 13)
    s.arrow([(228, 314), (272, 314)], c["str"], 2)
    s.arrow([(272, 332), (228, 332)], c["str"], 2)
    s.label(CX, 365, "fill ⇄ send", 12, c["muted"])

    s.arrow([(CX, 378), (CX, 412)], c["str"], 3)
    part(416, 56, "MicroPython loop", "RAW chunks · TIME reports")

    # USB to the host, and the file.
    s.arrow([(CX, 472), (CX, 552)], c["str"], 3)
    s.label(CX + 10, 520, "USB serial", 13, c["str"], "start")
    s.rect(110, 556, 280, 48, c["ctl_fill"], c["ctl_edge"])
    s.label(CX, 586, "ttcap on the host", 15, c["ctl"], weight="bold")
    s.arrow([(CX, 604), (CX, 626)], c["str"], 3)
    s.label(CX, 652, "capture.vgacap", 15, c["str"], weight="bold")
    return s.text()


if __name__ == "__main__":
    for theme, name in (("light", "data-path.svg"), ("dark", "data-path-dark.svg")):
        (HERE / name).write_text(draw(theme))
