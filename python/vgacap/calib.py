# SPDX-License-Identifier: Apache-2.0
"""Reference for the shared calibration and frame clock (include/vgacap/sample.h).

A plain per-clock transcription of tinytapeout-vga-videos'
ttvga/harness/tb.cpp: SyncTracker, median and analyse (tb.cpp:191-230), the
calibration loop (tb.cpp:361-378) and the capture loop's vsync-end counting
(tb.cpp:480, 520). Inputs are per clock: the clk-high uo_out and rst_n of
clock c (spec clock numbering: clock 0 is the first reset clock). tb.cpp's
`clock` reads T = c + 1 after the tick that ran clock c; the trackers, the
0xFFFF check and capture_after_clock are in T, as in tb.cpp.

The C port is fed runs; this is per clock, so the two are independent.
"""
from __future__ import annotations
from dataclasses import dataclass, field

CHECK_EVERY = 1 << 16          # tb.cpp:365
MAX_H_PHASES = 4_000_000       # tb.cpp:376


class SyncTracker:
    """tb.cpp:192-203. `phases` is kept as its count plus the lengths of
    phases 1.. by level (all analyse reads), so 4M phases stay small."""

    def __init__(self) -> None:
        self.last = False
        self.phase_start = 0
        self.phases = 0
        self.lengths: tuple[list[int], list[int]] = ([], [])   # [low, high]

    def feed(self, level: bool, clock: int) -> None:
        if level != self.last:
            if self.phases:                       # analyse skips phase 0 (tb.cpp:221)
                self.lengths[self.last].append(clock - self.phase_start)
            self.phases += 1
            self.phase_start = clock
            self.last = level


def median(v: list[int]) -> int:
    """tb.cpp:205-209: the element at size / 2 after sorting."""
    if not v:
        return 0
    return sorted(v)[len(v) // 2]


@dataclass
class SyncInfo:
    found: bool = False
    active_low: bool = True
    pulse: int = 0
    period: int = 0


def analyse(t: SyncTracker) -> SyncInfo:
    """tb.cpp:218-230."""
    s = SyncInfo()
    low, high = t.lengths
    if len(high) < 3 or len(low) < 3:
        return s
    mh, ml = median(high), median(low)
    s.active_low = ml < mh
    s.pulse = ml if s.active_low else mh
    s.period = ml + mh
    s.found = True
    return s


@dataclass
class Calibration:
    calib_limit: int
    capture_after_clock: int
    hs: SyncInfo
    vs: SyncInfo
    h_phases: int
    v_phases: int


def calib_limit(clock_hz: float, calib_seconds: float) -> int:
    """tb.cpp:364: static_cast<uint64_t>(calib_seconds * clock_hz), in double."""
    return int(float(calib_seconds) * float(clock_hz))


def calibrate(uo: bytes, rst_n: bytes, clock_hz: float, calib_seconds: float) -> Calibration:
    """tb.cpp:361-378 over per-clock uo_out and rst_n (bytes or sequences)."""
    limit = calib_limit(clock_hz, calib_seconds)
    ht, vt = SyncTracker(), SyncTracker()
    started = False
    capture = None
    checked: tuple[int, int] | None = None   # phase counts of the last failed check
    for c in range(len(uo)):
        if not started:
            if not rst_n[c]:
                continue                     # tb.cpp's reset ticks
            started = True
        if c >= limit:                       # while (clock < calib_limit)
            capture = c
            break
        t = c + 1                            # tb.cpp's clock after tick()
        u = uo[c]
        ht.feed(bool(u & 0x80), t)
        vt.feed(bool(u & 0x08), t)
        if t & (CHECK_EVERY - 1) == 0:
            # hs = analyse(ht); vs = analyse(vt); break if both found and
            # vt has 8 phases. analyse is a pure function of the phases, so
            # a check with unchanged phase counts repeats the last result,
            # and vs is tried first; the outcome is tb.cpp's.
            if checked != (ht.phases, vt.phases):
                if vt.phases >= 8 and analyse(vt).found and analyse(ht).found:
                    capture = t
                    break
                checked = (ht.phases, vt.phases)
        if ht.phases > MAX_H_PHASES:
            capture = t
            break
    if capture is None:                      # the trace ended first
        capture = len(uo)
    return Calibration(limit, capture, analyse(ht), analyse(vt), ht.phases, vt.phases)


def frame_ends(uo: bytes, capture_after_clock: int, v_active_low: bool) -> list[int]:
    """tb.cpp:480, 520: clocks e with vsync in its pulse in clock e - 1 and
    not in clock e, e - 1 >= capture_after_clock (prev_v starts false)."""
    ends = []
    prev_v = False
    for c in range(capture_after_clock, len(uo)):
        v = not (uo[c] & 0x08) if v_active_low else bool(uo[c] & 0x08)
        if prev_v and not v:
            ends.append(c)
        prev_v = v
    return ends
