# SPDX-License-Identifier: Apache-2.0
"""Synthetic Tiny VGA sample-stream generator, mirroring tests/synth.c."""
from __future__ import annotations
import numpy as np
from .modes import Mode
from .stream import Header, Writer, TINYVGA_MAP

# RP2040 capture layout: bits 0-3 = uo_out[0..3], bits 4-7 = 0xA (fake
# ui_in), bits 8-11 = uo_out[4..7]. signal_map picks out the eight Tiny VGA
# bits (hs,b0,g0,r0,vs,b1,g1,r1) from that 12-bit word.
RP2040_MAP = (11, 3, 0, 8, 1, 9, 2, 10)


def bars(w: int, h: int) -> np.ndarray:
    x = np.arange(w, dtype=np.uint8)
    return np.broadcast_to(((x // 10) & 0x3F).astype(np.uint8), (h, w)).copy()


def grid(w: int, h: int) -> np.ndarray:
    y, x = np.mgrid[0:h, 0:w]
    return np.where((x % 8 == 0) | (y % 8 == 0), 0x3F, 0x10).astype(np.uint8)


def colour6_to_rgb(image6: np.ndarray) -> np.ndarray:
    r, g, b = (image6 >> 4) & 3, (image6 >> 2) & 3, image6 & 3
    return (np.stack([r, g, b], axis=-1) * 85).astype(np.uint8)


def frame_samples(mode: Mode, image: np.ndarray) -> np.ndarray:
    """One full frame of Tiny VGA samples, starting at the first line of the
    vsync pulse, first clock of the hsync pulse (same convention as
    tests/synth.c)."""
    cpl = mode.h_active + mode.h_front + mode.h_sync + mode.h_back
    lpf = mode.v_active + mode.v_front + mode.v_sync + mode.v_back
    clk = np.arange(cpl)
    line = np.arange(lpf)
    h_pulse = clk < mode.h_sync
    v_pulse = line < mode.v_sync
    hs = (h_pulse if mode.h_sync_positive else ~h_pulse).astype(np.uint8)
    vs = (v_pulse if mode.v_sync_positive else ~v_pulse).astype(np.uint8)
    col = np.zeros((lpf, cpl), dtype=np.uint8)
    x0, y0 = mode.h_sync + mode.h_back, mode.v_sync + mode.v_back
    col[y0:y0 + mode.v_active, x0:x0 + mode.h_active] = image
    r1, r0 = (col >> 5) & 1, (col >> 4) & 1
    g1, g0 = (col >> 3) & 1, (col >> 2) & 1
    b1, b0 = (col >> 1) & 1, col & 1
    s = (hs[None, :] << 7) | (b0 << 6) | (g0 << 5) | (r0 << 4) | (vs[:, None] << 3) | (b1 << 2) | (g1 << 1) | r1
    return s.astype(np.uint8).reshape(-1)


def to_rp2040_layout(s8: np.ndarray) -> np.ndarray:
    s = s8.astype(np.uint32)
    return ((s & 0xF) | (0xA << 4) | ((s >> 4) << 8)).astype(np.uint32)


def write_stream(path, mode: Mode, image6, frames: int = 3, sample_bits: int = 8,
                  samples_per_word: int = 4, flags: int = 0, chunk: str = "raw",
                  window_lines: int = 25) -> None:
    s = frame_samples(mode, image6)
    smap = TINYVGA_MAP
    if sample_bits == 12:
        s = to_rp2040_layout(s)
        smap = RP2040_MAP
    cpl = mode.h_active + mode.h_front + mode.h_sync + mode.h_back
    lpf = mode.v_active + mode.v_front + mode.v_sync + mode.v_back
    with open(path, "wb") as fp:
        w = Writer(fp, Header(sample_bits=sample_bits, samples_per_word=samples_per_word,
                               flags=flags, signal_map=smap, mode=3,
                               desc=f"synth {mode.name} {chunk}"))
        for f in range(frames):
            if chunk == "raw":
                for start in range(0, len(s), 65536):
                    w.raw(s[start:start + 65536].tolist())
            elif chunk == "rle":
                change = np.flatnonzero(np.diff(s)) + 1
                starts = np.concatenate([[0], change])
                ends = np.concatenate([change, [len(s)]])
                w.rle([(int(s[a]), int(b - a)) for a, b in zip(starts, ends)])
            elif chunk == "fram":
                for first in range(0, lpf, window_lines):
                    n = min(window_lines, lpf - first)
                    w.frame(f, first, n, cpl, s[first * cpl:(first + n) * cpl].tolist())
            else:
                raise ValueError(chunk)
