# SPDX-License-Identifier: Apache-2.0
"""Mirror of the built-in mode table (see src/frame/modes.c)."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    name: str
    h_active: int
    h_front: int
    h_sync: int
    h_back: int
    v_active: int
    v_front: int
    v_sync: int
    v_back: int
    h_sync_positive: bool
    v_sync_positive: bool
    extended: bool = False

    @property
    def cpl(self) -> int:
        """Clocks per line: h_active+h_front+h_sync+h_back."""
        return self.h_active + self.h_front + self.h_sync + self.h_back

    @property
    def lpf(self) -> int:
        """Lines per frame: v_active+v_front+v_sync+v_back."""
        return self.v_active + self.v_front + self.v_sync + self.v_back


_TABLE = [
    Mode("640x480@60",  640, 16,  96,  48, 480, 10, 2, 33, False, False),
    Mode("640x480@72",  640, 24,  40, 128, 480,  9, 3, 28, False, False),
    Mode("640x480@75",  640, 16,  64, 120, 480,  1, 3, 16, False, False),
    Mode("720x400@70",  720, 18, 108,  54, 400, 12, 2, 35, False, True),
    Mode("800x600@56",  800, 24,  72, 128, 600,  1, 2, 22, True,  True),
    Mode("800x600@60",  800, 40, 128,  88, 600,  1, 4, 23, True,  True),
    Mode("1024x768@60", 1024, 24, 136, 160, 768,  3, 6, 29, False, False),
    # Added from the simulation testbench (ttvga/harness/tb.cpp). Clocks per
    # line is not unique with these, so match_cpl skips them.
    Mode("800x600@72",  800,  56, 120,  64, 600, 37, 6, 23, True,  True,  True),
    Mode("800x600@75",  800,  16,  80, 160, 600,  1, 3, 21, True,  True,  True),
    Mode("1024x768@70", 1024, 24, 136, 144, 768,  3, 6, 29, False, False, True),
    Mode("1280x720@60", 1280, 110, 40, 220, 720,  5, 5, 20, True,  True,  True),
    Mode("1280x1024@60", 1280, 48, 112, 248, 1024, 1, 3, 38, True,  True,  True),
]

TABLE: list[Mode] = _TABLE
MODES: dict[str, Mode] = {m.name: m for m in TABLE}


def match(cpl: int, lpf: int) -> Mode | None:
    """Exact match on clocks per line and lines per frame (any entry)."""
    return next((m for m in TABLE if m.cpl == cpl and m.lpf == lpf), None)


def match_cpl(cpl: int) -> Mode | None:
    """Match on clocks per line alone; extended entries are skipped."""
    return next((m for m in TABLE if not m.extended and m.cpl == cpl), None)
