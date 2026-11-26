# SPDX-License-Identifier: Apache-2.0
"""Reference for the tb-v1 sampler (src/sample/tbv1.c).

A plain per-clock transcription of tinytapeout-vga-videos'
ttvga/harness/tb.cpp:379-557: the no-sync and bad-timing decisions, the mode
match against tb.cpp's own 11-mode table (tb.cpp:39-51, in its order), the
clocks-per-pixel rounding, the frame target and clock limit, and the capture
loop. It runs after the shared calibration (vgacap.calib), on per-clock
clk-high uo_out (spec clock numbering: trace clock c is tb.cpp's `clock`
before the tick that runs it).

Frames are rgb6: R << 4 | G << 2 | B, R = uo[0], uo[4]; G = uo[1], uo[5];
B = uo[2], uo[6], the first bit most significant (tb.cpp's RGB888 / 85).
Frame k starts at the k-th vsync end from capture_after_clock on (the frame
clock's frame k) and is emitted at end k + 1.

Python floats are IEEE doubles, so the arithmetic below is tb.cpp's, in its
order. std::round rounds halves away from zero, unlike Python's round.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field

from .calib import Calibration

INT_MIN = -(1 << 31)

# tb.cpp:39-51: name, line_px, lines, hpulse, hback, width, vpulse, vback, height.
TB_MODES = (
    ("640x480@60", 800, 525, 96, 48, 640, 2, 33, 480),
    ("640x480@72", 832, 520, 40, 128, 640, 3, 28, 480),
    ("640x480@75", 840, 500, 64, 120, 640, 3, 16, 480),
    ("800x600@56", 1024, 625, 72, 128, 800, 2, 22, 600),
    ("800x600@60", 1056, 628, 128, 88, 800, 4, 23, 600),
    ("800x600@72", 1040, 666, 120, 64, 800, 6, 23, 600),
    ("800x600@75", 1056, 625, 80, 160, 800, 3, 21, 600),
    ("1024x768@60", 1344, 806, 136, 160, 1024, 6, 29, 768),
    ("1024x768@70", 1328, 806, 136, 144, 1024, 6, 29, 768),
    ("1280x720@60", 1650, 750, 40, 220, 1280, 5, 20, 720),
    ("1280x1024@60", 1688, 1066, 112, 248, 1280, 3, 38, 1024),
)


def cround(x: float) -> float:
    """std::round: halves away from zero (x >= 0 here, but either sign works)."""
    f = math.floor(x)
    if x >= 0:
        return f + 1.0 if x - f >= 0.5 else float(f)
    c = math.ceil(x)
    return c - 1.0 if c - x >= 0.5 else float(c)


@dataclass
class Geometry:
    ok: bool
    status: str                 # "ok", "no-sync" or "bad-timing"
    mode: str = "unknown"
    cpp: float = 1.0
    width: int = 0
    height: int = 0
    hback: int = 0
    vback: int = 0
    target_frames: int = 0
    clock_limit: int = 0


@dataclass
class Frame:
    number: int
    start_clock: int
    rgb6: bytes


def geometry(cal: Calibration, clock_hz: float, seconds: float) -> Geometry:
    """tb.cpp:397-451."""
    hs, vs = cal.hs, cal.vs
    if not hs.found or not vs.found:
        return Geometry(False, "no-sync")
    if hs.period == 0 or vs.period < 2 * hs.period:
        return Geometry(False, "bad-timing")
    line_clocks = hs.period
    lines = (vs.period + line_clocks // 2) // line_clocks        # int, as tb.cpp:403
    mode = None
    cpp = 1.0
    best = 0.03
    for m in TB_MODES:
        k = float(line_clocks) / m[1]
        if k < 0.9 or k > 4.5:
            continue
        ev = abs(float(lines) - m[2]) / m[2]
        ep = abs(float(hs.pulse) / line_clocks - float(m[3]) / m[1])
        e = ev + ep
        if e < best:
            best, mode, cpp = e, m, k
    vpulse_lines = (vs.pulse + line_clocks // 2) // line_clocks
    vertical_matched = False
    if mode is None:
        for m in TB_MODES:
            k = float(line_clocks) / m[1]
            if k < 0.9 or k > 4.5:
                continue
            eh = abs(k - cround(k * m[1]) / m[1])
            ep = abs(float(hs.pulse) / line_clocks - float(m[3]) / m[1])
            if ep < 0.01 and eh < 0.02 and lines > m[8]:
                mode, cpp = m, k
                break
    else:
        vertical_matched = True
    if mode is not None:
        width, hback = mode[5], mode[4]
        height = mode[8] if vertical_matched else lines - vpulse_lines
        vback = mode[7] if vertical_matched else 0
        if abs(cpp - cround(cpp)) < 0.01:
            cpp = cround(cpp)
    else:
        width = line_clocks - hs.pulse
        height = lines - vpulse_lines
        hback = vback = 0
    name = mode[0] if mode is not None else "unknown"
    if width > 4096 or height > 4096 or width < 16 or height < 16:
        return Geometry(False, "bad-timing", name, cpp)
    fps = float(clock_hz) / float(vs.period)
    target_frames = int(float(seconds) * fps + 0.5)
    clock_limit = cal.capture_after_clock + int((float(seconds) + 2.5 / fps) * float(clock_hz))
    return Geometry(True, "ok", name, cpp, width, height, hback, vback, target_frames, clock_limit)


def _rgb6(uo: int) -> int:
    r = ((uo & 1) << 1) | ((uo >> 4) & 1)
    g = (((uo >> 1) & 1) << 1) | ((uo >> 5) & 1)
    b = (((uo >> 2) & 1) << 1) | ((uo >> 6) & 1)
    return (r << 4) | (g << 2) | b


def sample(uo: bytes, cal: Calibration, clock_hz: float, seconds: float) -> tuple[Geometry, list[Frame]]:
    """tb.cpp:470-557 over per-clock uo_out from capture_after_clock to the
    end of `uo`, the frame target or the clock limit, whichever is first."""
    geo = geometry(cal, clock_hz, seconds)
    frames: list[Frame] = []
    if not geo.ok:
        return geo, frames
    h_low, v_low = cal.hs.active_low, cal.vs.active_low
    width, height, hback, vback, cpp = geo.width, geo.height, geo.hback, geo.vback, geo.cpp
    half = cpp / 2.0
    frame = bytearray(width * height)
    nframes = 0
    in_frame = prev_h = prev_v = False
    line_start = 0
    y = last_px = -1
    start = 0
    stop = min(len(uo), geo.clock_limit)
    target = geo.target_frames
    c = cal.capture_after_clock
    while nframes < target and c < stop:     # while (frames < target_frames && clock < clock_limit)
        u = uo[c]
        h_pulse = (not (u & 0x80)) if h_low else bool(u & 0x80)
        v_pulse = (not (u & 0x08)) if v_low else bool(u & 0x08)
        if prev_v and not v_pulse:            # vsync pulse ended: new frame
            if in_frame:
                frames.append(Frame(nframes, start, bytes(frame)))
                frame = bytearray(width * height)
                nframes += 1
            start = c
            in_frame = True
            y = -1
        if prev_h and not h_pulse:            # hsync pulse ended: new line
            line_start = c
            last_px = -1
            if in_frame:
                y += 1
        if in_frame and y >= 0 and not h_pulse:
            x_clock = float(c - line_start)
            q = (x_clock + half) / cpp
            pixel = int(q) if q < 2147483648.0 else INT_MIN   # x86's out-of-range int
            if pixel != last_px:
                last_px = pixel
                px = pixel - hback
                py = y - vback
                if 0 <= px < width and 0 <= py < height:
                    frame[py * width + px] = _rgb6(u)
        prev_h, prev_v = h_pulse, v_pulse
        c += 1
    return geo, frames
