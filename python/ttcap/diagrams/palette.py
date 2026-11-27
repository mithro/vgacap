# SPDX-License-Identifier: Apache-2.0
"""The palette of the ttcap diagrams, light and dark, and the SVG helpers
they share. Each generator here draws its figure once for each theme.

The colours are those of docs/diagrams/overview.py: blue for signals,
orange for captured data, purple for ttcap; the dark theme keeps the
hues and swaps paper and ink.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

THEMES = {
    "light": {
        "paper": "#ffffff", "ink": "#1d2433", "muted": "#5b6577",
        "sig": "#1c64d4", "sig_fill": "#eaf1fd", "sig_edge": "#9dbcef",
        "str": "#e0580a", "str_fill": "#fff2e8", "str_edge": "#f8b48a",
        "ctl": "#6b46d6", "ctl_fill": "#f1ecfd", "ctl_edge": "#c2b1f1",
        "chip": "#2b3445", "chip_text": "#ffffff", "part": "#ffffff", "part_edge": "#7d8799",
    },
    "dark": {
        "paper": "#131722", "ink": "#e6e9ef", "muted": "#9aa3b5",
        "sig": "#6ea2f5", "sig_fill": "#182640", "sig_edge": "#34558f",
        "str": "#ff8c45", "str_fill": "#3a2414", "str_edge": "#8a4a22",
        "ctl": "#a98bff", "ctl_fill": "#251d40", "ctl_edge": "#5a47a0",
        "chip": "#2a3142", "chip_text": "#e6e9ef", "part": "#1c2230", "part_edge": "#5b6577",
    },
}

FONT = "font-family:'DejaVu Sans',Verdana,Arial,sans-serif"


class Svg:
    """An SVG drawn in one theme: shapes are appended, `text()` returns it."""

    def __init__(self, width, height, theme):
        self.w, self.h, self.c = width, height, THEMES[theme]
        self.out = []

    def add(self, s):
        self.out.append(s)

    def rect(self, x, y, w, h, fill, edge, rx=6, sw=1.5):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
                 f'fill="{fill}" stroke="{edge}" stroke-width="{sw}"/>')

    def label(self, x, y, s, size=14, fill=None, anchor="middle", weight="normal"):
        fill = fill or self.c["ink"]
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
                 f'text-anchor="{anchor}" font-weight="{weight}" style="{FONT}">'
                 f'{escape(s)}</text>')

    def arrow(self, points, colour, width=2.5):
        """A polyline from the first point, ending in an arrowhead at the last."""
        (x1, y1), (x2, y2) = points[-2], points[-1]
        d = "M" + " L".join(f"{x} {y}" for x, y in points)
        self.add(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}"/>')
        # the head, pointing along the last segment
        import math
        a = math.atan2(y2 - y1, x2 - x1)
        s = 5 + 2 * width
        p1 = (x2 - s * math.cos(a - 0.45), y2 - s * math.sin(a - 0.45))
        p2 = (x2 - s * math.cos(a + 0.45), y2 - s * math.sin(a + 0.45))
        self.add(f'<path d="M{x2} {y2} L{p1[0]:.1f} {p1[1]:.1f} L{p2[0]:.1f} {p2[1]:.1f} Z" '
                 f'fill="{colour}"/>')

    def text(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}">\n'
                f'<rect width="{self.w}" height="{self.h}" fill="{self.c["paper"]}"/>\n'
                + "\n".join(self.out) + "\n</svg>\n")
