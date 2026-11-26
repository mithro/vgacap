# SPDX-License-Identifier: Apache-2.0
"""Reference for the centre-v1 sampler's window and pick (src/sample/centrev1.c).

centre-v1 takes its lines and frames from libvgaframe's timing learner, at
clock resolution; that learner is not re-implemented here. This covers what
the sampler adds, from the shared calibration's numbers (vgacap.calib) and a
clock-resolution frame (libvgaframe's raw frame: one byte a clock, the 6-bit
colour | 0x80 when written, row 0 the first line of the vsync pulse, column
0 the hsync leading edge):

- geometry: the mode, by tb.cpp's summed-error rule (tb.cpp:397-446) against
  the shared 12-mode table (vgacap.modes, in table order); the clocks per
  pixel (1. cpp_l = L / the mode's line pixels, within 0.01 of a whole
  number; 2. the smallest k >= 1 with the hsync pulse within 2 clocks of k
  times the mode's; 3. cpp_l); and the window (hback' = floor(B * hback /
  (hback + hfront) + 0.5) with B = L / cpp - hsync - width, the same
  vertically), plus the override (dx, dy), with the bounds: the window
  ([floor(a), ceil(b)) clocks, a = (hsync + hback') * cpp, b = a + width *
  cpp) inside the line and frame and after the sync pulses, else
  "bad-timing" (without the override) or "bad-window" (with it);
- pick: pixel (p, y) is row vpulse_lines + vback' + y at clock
  floor((hsync + hback' + p + 0.5) * cpp), evaluated in that order; an
  unwritten clock, or one outside the line or the frame, is missing (0).

Python floats are IEEE doubles and numpy's float64 arithmetic is
elementwise IEEE too, so the numbers are the C code's, in its order.
"""
from __future__ import annotations
import math
from dataclasses import dataclass

import numpy as np

from .calib import SyncInfo
from .modes import TABLE
from .tbv1 import cround

#: libvgaframe's raw buffer: 4.5 clocks a pixel times the widest line (1,688).
MAX_LINE_CLOCKS = 7596
#: Frames with more lines are bad timing.
MAX_LINES = 1200


@dataclass
class Geometry:
    ok: bool
    status: str                 # "ok", "no-sync", "bad-timing" or "bad-window"
    mode: str = "unknown"
    cpp: float = 1.0
    width: int = 0
    height: int = 0
    hback: int = 0              # hback' + dx
    vback: int = 0              # vback' + dy
    hsync: float = 0.0          # the hsync pulse in pixels
    vpulse_lines: int = 0
    line_clocks: int = 0        # L
    lines: int = 0


def geometry(hs: SyncInfo, vs: SyncInfo, dx: int = 0, dy: int = 0) -> Geometry:
    """The mode, cpp and window from the calibration's syncs. On failure, as
    tb-v1: no-sync and bad timing by period give "unknown" and cpp 1; bad
    timing by size gives the matched mode and its cpp."""
    if not hs.found or not vs.found:
        return Geometry(False, "no-sync")
    if hs.period == 0 or vs.period < 2 * hs.period:
        return Geometry(False, "bad-timing")
    L = hs.period
    lines = (vs.period + L // 2) // L
    mode = None
    cpp = 1.0
    best = 0.03
    for m in TABLE:
        lp, fl = float(m.cpl), float(m.lpf)
        k = float(L) / lp
        if k < 0.9 or k > 4.5:
            continue
        e = abs(float(lines) - fl) / fl + abs(float(hs.pulse) / float(L) - float(m.h_sync) / lp)
        if e < best:
            best, mode, cpp = e, m, k
    vpl = (vs.pulse + L // 2) // L
    vertical_matched = mode is not None
    if mode is None:
        for m in TABLE:
            lp = float(m.cpl)
            k = float(L) / lp
            if k < 0.9 or k > 4.5:
                continue
            eh = abs(k - cround(k * lp) / lp)
            ep = abs(float(hs.pulse) / float(L) - float(m.h_sync) / lp)
            if ep < 0.01 and eh < 0.02 and lines > m.v_active:
                mode, cpp = m, k
                break
    if mode is not None:
        cpp_l = cpp
        if abs(cpp_l - cround(cpp_l)) < 0.01:
            cpp = cround(cpp_l)
        else:
            k = 1
            while k * mode.h_sync <= hs.pulse + 2 and abs(hs.pulse - k * mode.h_sync) > 2:
                k += 1
            cpp = float(k) if k * mode.h_sync <= hs.pulse + 2 else cpp_l
        width = mode.h_active
        height = mode.v_active if vertical_matched else lines - vpl
        hsync = float(hs.pulse) / cpp
        B = float(L) / cpp - hsync - float(width)
        hb = math.floor(B * mode.h_back / (mode.h_back + mode.h_front) + 0.5)
        if vertical_matched:
            Bv = float(lines) - float(vpl) - float(height)
            vb = math.floor(Bv * mode.v_back / (mode.v_back + mode.v_front) + 0.5)
        else:
            vb = 0
    else:                                       # tb.cpp:441-444
        cpp = 1.0
        hsync = float(hs.pulse)
        width = L - hs.pulse
        height = lines - vpl
        hb = vb = 0
    name = mode.name if mode is not None else "unknown"
    if not (16 <= width <= 4096 and 16 <= height <= 4096) or lines > MAX_LINES or L > MAX_LINE_CLOCKS:
        return Geometry(False, "bad-timing", name, cpp)
    # The bounds: for the window the timing gives, then with the override.
    if not window_fits(hsync, cpp, hb, vb, width, height, L, lines, vpl):
        return Geometry(False, "bad-timing", name, cpp)
    if not window_fits(hsync, cpp, hb + dx, vb + dy, width, height, L, lines, vpl):
        return Geometry(False, "bad-window", name, cpp)
    return Geometry(True, "ok", name, cpp, width, height, hb + dx, vb + dy, hsync, vpl, L, lines)


def window_span(hsync: float, cpp: float, hback: int, width: int) -> tuple[int, int]:
    """The window's clocks, [floor(a), ceil(b)): those overlapping its span
    [a, b) on the pick's clock axis, a = (hsync + hback) * cpp,
    b = a + width * cpp."""
    a = (hsync + float(hback)) * cpp
    b = a + float(width) * cpp
    return math.floor(a), math.ceil(b)


def window_fits(hsync: float, cpp: float, hback: int, vback: int, width: int, height: int,
                L: int, lines: int, vpl: int) -> bool:
    """The spec's bounds: not starting inside a sync pulse, not past the line
    or the frame."""
    if hback < 0 or vback < 0 or hback > L or vback > lines:
        return False
    return window_span(hsync, cpp, hback, width)[1] <= L and vpl + vback + height <= lines


def pick(raw: np.ndarray, geo: Geometry) -> tuple[bytes, int]:
    """The rgb6 picture (width * height bytes) and its missing pixels, from a
    clock-resolution frame: a 2-D uint8 array, a row per line received (from
    row 0) and at least geo.line_clocks columns."""
    p = np.arange(geo.width, dtype=np.float64)
    x = np.floor((geo.hsync + geo.hback + p + 0.5) * geo.cpp)
    x_in = (x >= 0) & (x < geo.line_clocks)
    xi = np.where(x_in, x, 0).astype(np.int64)
    rows = geo.vpulse_lines + geo.vback + np.arange(geo.height, dtype=np.int64)
    r_in = (rows >= 0) & (rows < min(raw.shape[0], geo.lines))
    ri = np.where(r_in, rows, 0)
    b = raw[ri[:, None], xi[None, :]]
    ok = r_in[:, None] & x_in[None, :] & ((b & 0x80) != 0)
    out = np.where(ok, b & 0x3F, 0).astype(np.uint8)
    return out.tobytes(), int(np.count_nonzero(~ok))
