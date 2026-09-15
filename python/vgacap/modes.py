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


_TABLE = [
    Mode("640x480@60",  640, 16,  96,  48, 480, 10, 2, 33, False, False),
    Mode("640x480@72",  640, 24,  40, 128, 480,  9, 3, 28, False, False),
    Mode("640x480@75",  640, 16,  64, 120, 480,  1, 3, 16, False, False),
    Mode("720x400@70",  720, 18, 108,  54, 400, 12, 2, 35, False, True),
    Mode("800x600@56",  800, 24,  72, 128, 600,  1, 2, 22, True,  True),
    Mode("800x600@60",  800, 40, 128,  88, 600,  1, 4, 23, True,  True),
    Mode("1024x768@60", 1024, 24, 136, 160, 768,  3, 6, 29, False, False),
]

MODES: dict[str, Mode] = {m.name: m for m in _TABLE}
