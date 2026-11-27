#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draw docs/diagrams/firmware-protocol.svg and firmware-protocol-dark.svg:
the lines and chunks of one frame capture through the vgacap firmware, as
ttcap sends them (firmware/PROTOCOL.md).

    uv run --no-project python docs/diagrams/firmware_protocol.py

Time runs down the page. The host is on the left, the firmware on the
right; commands go right, replies and chunks come back left. One palette
gives both themes.
"""
from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent
MARGIN = 16

THEMES = {
    "light": dict(bg="#ffffff", ink="#1d2433", muted="#5b6577", cmd="#1c64d4",
                  reply="#14845a", data="#f2620c", data_fill="#fff2e8", data_edge="#f8b48a",
                  lane="#9aa3b5", band="#f3f5f9", end="#5b6577", head_fill="#eaf1fd", head_edge="#9dbcef"),
    "dark": dict(bg="#131416", ink="#e6e9ef", muted="#a3acbd", cmd="#6ea8ff",
                 reply="#4cc790", data="#ff8a3d", data_fill="#2e1d10", data_edge="#7a4520",
                 lane="#5d6578", band="#1b1e24", end="#a3acbd", head_fill="#15233b", head_edge="#34507e"),
}

HOST_X, FW_X = 112, 432          # the two lifelines
ROW = 30                          # one message
DOTTED = ' stroke-dasharray="2 3"'  # the end marker's line
W = FW_X + 70 + MARGIN

# (phase, kind, label, dashed): kind is cmd (host to firmware), reply
# (firmware to host) or data (a chunk, firmware to host).
SEQUENCE = [
    ("sync", "cmd", "0x03  hello", False),
    ("sync", "reply", "ok vgacap-fw proto=1 ...", False),
    ("setup", "cmd", "clock hz=0", False),
    ("setup", "reply", "ok clock_hz=0 ...", False),
    ("setup", "cmd", "reset hold=1", False),
    ("setup", "reply", "ok reset=1", False),
    ("setup", "cmd", "asic_select index=N   (ASIC only)", True),
    ("setup", "reply", "ok asic_select index=N", True),
    ("setup", "cmd", "clock hz=25000000", False),
    ("setup", "reply", "ok clock_hz=25000000 ... period=4", False),
    ("setup", "cmd", "reset hold=0", False),
    ("setup", "reply", "ok reset=0", False),
    ("capture", "cmd", "frame cpl=800 ... max_frames=2 ...", False),
    ("capture", "reply", "ok frame", False),
    ("capture", "data", "TIME  frame window=481 ...", False),
    ("capture", "data", "FRAM  picture 0", False),
    ("capture", "data", "FRAM  picture 1", False),
    ("capture", "data", "TIME  frames=2 stalls=0 ...", False),
    ("capture", "end", "DONE", False),
    ("teardown", "cmd", "clock hz=0", False),
    ("teardown", "reply", "ok clock_hz=0 ...", False),
]


def draw(theme: str) -> str:
    c = THEMES[theme]
    out: list[str] = []
    add = out.append

    def text(x, y, s, cls, anchor="start", fill=None):
        f = f' fill="{fill}"' if fill else ""
        add(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}"{f}>{escape(s)}</text>')

    head_y, head_h = MARGIN, 44
    top = head_y + head_h + 18
    rows_y = [top + 14 + i * ROW for i in range(len(SEQUENCE))]
    bottom = rows_y[-1] + 18
    H = bottom + 34 + MARGIN

    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif" '
        f'role="img" aria-labelledby="t d">')
    add('<title id="t">A frame capture through the vgacap firmware</title>')
    add('<desc id="d">ttcap on the host and the vgacap firmware exchange lines: a sync with hello, '
        'setup of the clock and reset, the frame command and its ok, then TIME, FRAM and DONE '
        'chunks, then the clock stopped.</desc>')
    add(f"""<style>
  .head {{ font-size: 15px; font-weight: 600; fill: {c['ink']}; }}
  .phase {{ font-size: 10.5px; font-weight: 700; fill: {c['muted']}; letter-spacing: 0.04em; }}
  .msg {{ font-size: 13.5px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  .legend {{ font-size: 12.5px; fill: {c['ink']}; }}
</style>""")
    add("<defs>")
    for kind in ("cmd", "reply", "data", "end"):
        mw = 2.4 if kind == "data" else 4
        add(f'<marker id="m-{kind}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="{mw}" '
            f'markerHeight="{mw}" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" '
            f'fill="{c[kind]}"/></marker>')
    add("</defs>")
    add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="{c["bg"]}"/>')

    # phase bands, behind everything
    phases: dict[str, list[int]] = {}
    for (phase, *_), y in zip(SEQUENCE, rows_y):
        phases.setdefault(phase, []).append(y)
    for phase, ys in phases.items():
        y0, y1 = ys[0] - 19, ys[-1] + 9
        add(f'<rect x="{MARGIN}" y="{y0}" width="{W - 2 * MARGIN}" height="{y1 - y0}" '
            f'rx="6" fill="{c["band"]}"/>')
        ty = (y0 + y1) / 2
        add(f'<text x="{MARGIN + 14}" y="{ty}" class="phase" text-anchor="middle" '
            f'transform="rotate(-90 {MARGIN + 14} {ty})">{phase}</text>')

    # lifeline heads and lines
    for x, name in ((HOST_X, "ttcap (host)"), (FW_X, "vgacap firmware")):
        add(f'<rect x="{x - 70}" y="{head_y}" width="140" height="{head_h}" rx="8" '
            f'fill="{c["head_fill"]}" stroke="{c["head_edge"]}" stroke-width="1.25"/>')
        text(x, head_y + 27, name, "head", "middle")
        add(f'<line x1="{x}" y1="{head_y + head_h}" x2="{x}" y2="{bottom}" stroke="{c["lane"]}" '
            f'stroke-width="1.5" stroke-dasharray="4 4"/>')

    mid = (HOST_X + FW_X) / 2
    for (phase, kind, label, dashed), y in zip(SEQUENCE, rows_y):
        x1, x2 = (HOST_X, FW_X - 4) if kind == "cmd" else (FW_X, HOST_X + 4)
        width = 4 if kind == "data" else 1.75
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        if kind == "end":
            dash = DOTTED
        add(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{c[kind]}" '
            f'stroke-width="{width}"{dash} marker-end="url(#m-{kind})"/>')
        if kind == "data":
            tw = 8.2 * len(label) + 18
            add(f'<rect x="{mid - tw / 2}" y="{y - 11}" width="{tw}" height="22" rx="4" '
                f'fill="{c["data_fill"]}" stroke="{c["data_edge"]}" stroke-width="1"/>')
            text(mid, y + 4.5, label, "msg", "middle", fill=c["ink"])
        else:
            text(mid, y - 5, label, "msg", "middle", fill=c[kind])

    # legend
    ly = bottom + 24
    items = (("cmd", "command"), ("reply", "reply"), ("data", "chunk"), ("end", "end marker"))
    x = MARGIN + 20
    for kind, label in items:
        add(f'<line x1="{x}" y1="{ly}" x2="{x + 32}" y2="{ly}" stroke="{c[kind]}" '
            f'stroke-width="{4 if kind == "data" else 1.75}"{DOTTED if kind == "end" else ""} '
            f'marker-end="url(#m-{kind})"/>')
        text(x + 40, ly + 4.5, label, "legend")
        x += 40 + 7.4 * len(label) + 22
    add("</svg>")
    return "\n".join(out) + "\n"


for theme, name in (("light", "firmware-protocol.svg"), ("dark", "firmware-protocol-dark.svg")):
    (HERE / name).write_text(draw(theme))
    print("wrote", HERE / name)
