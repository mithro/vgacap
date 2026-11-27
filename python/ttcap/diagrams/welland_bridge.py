#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw welland-bridge.svg and welland-bridge-dark.svg: a capture from a
Welland bench board through its fpgas.online bridge (`fpgas-tt`) to a
workstation, through an SSH tunnel to the bench gateway.

    uv run --no-project python python/ttcap/diagrams/welland_bridge.py

The captured data flows down: the demo board, its Pi's `/dev/ttboard`
and the bridge on port 8765, the gateway `tweed`, the SSH tunnel, the
tunnel's near end on the workstation, and `ttcap`. The names
and ports are fpga-1's, as in how-to-capture-welland-bridge.md and
`WELLAND` in boards.py.
"""
from __future__ import annotations

import pathlib

from palette import Svg

HERE = pathlib.Path(__file__).resolve().parent
W, H = 400, 600
CX = W / 2        # the column the data runs down
BOX_W = 290       # a machine
PART_W = 210      # a part inside a machine
LEFT = CX - BOX_W / 2


def part(s, y, name):
    c = s.c
    s.rect(CX - PART_W / 2, y, PART_W, 32, c["part"], c["part_edge"])
    s.label(CX, y + 21, name, 14, c["ink"], weight="bold")


def link(s, y1, y2, name):
    """The captured data going down from one machine to the next."""
    s.arrow([(CX, y1), (CX, y2)], s.c["str"], 3)
    s.label(CX + 12, (y1 + y2) / 2 + 5, name, 13, s.c["str"], "start")


def draw(theme):
    s = Svg(W, H, theme)
    c = s.c

    # The Welland bench: what the workstation reaches only through tweed.
    s.add(f'<rect x="2" y="2" width="{W - 4}" height="396" rx="10" fill="none" '
          f'stroke="{c["part_edge"]}" stroke-width="1.5" stroke-dasharray="6 4"/>')
    s.label(16, 24, "Welland bench", 13, c["muted"], "start", "bold")

    # The demo board.
    s.rect(LEFT, 36, BOX_W, 56, c["sig_fill"], c["sig_edge"], rx=8)
    s.label(CX, 60, "Tiny Tapeout demo board", 15, weight="bold")
    s.label(CX, 80, "fpga-1", 13, c["muted"])
    link(s, 92, 134, "USB")

    # Its Raspberry Pi: the serial port, then the bridge holding it.
    s.rect(LEFT, 136, BOX_W, 148, c["chip"], c["chip"], rx=8)
    s.label(LEFT + 14, 158, "Raspberry Pi", 14, c["chip_text"], "start", "bold")
    s.label(LEFT + BOX_W - 14, 158, "10.21.2.34", 13, c["chip_text"], "end")
    part(s, 172, "/dev/ttboard")
    s.arrow([(CX, 204), (CX, 236)], c["str"], 2.5)
    part(s, 238, "fpgas-tt  :8765")
    link(s, 284, 326, "WebSocket")

    # The bench gateway.
    s.rect(LEFT, 328, BOX_W, 56, c["part"], c["part_edge"], rx=8)
    s.label(CX, 352, "tweed.welland.mithis.com", 15, weight="bold")
    s.label(CX, 372, "bench gateway", 13, c["muted"])
    link(s, 384, 448, "SSH tunnel")

    # The workstation: the tunnel's near end, then ttcap.
    s.rect(LEFT, 450, BOX_W, 148, c["ctl_fill"], c["ctl_edge"], rx=8)
    s.label(LEFT + 14, 472, "your workstation", 14, c["ctl"], "start", "bold")
    part(s, 486, "127.0.0.1:18034")
    s.arrow([(CX, 518), (CX, 550)], c["str"], 2.5)
    part(s, 552, "ttcap")
    return s.text()


def main():
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        (HERE / f"welland-bridge{suffix}.svg").write_text(draw(theme), encoding="utf-8")


if __name__ == "__main__":
    main()
