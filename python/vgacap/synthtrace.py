# SPDX-License-Identifier: Apache-2.0
"""Synthetic simulation traces: what tb.cpp would record from a VGA design.

A trace is per clock: the clk-high `uo_out` and `rst_n` (clock 0 is the first
reset clock; rst_n is 0 for `reset_clocks` clocks, then 1; ena is 1). It is
written as a plain VGCH v2 stream with the tt-um-43 layout (uo_out bits 0-7,
uio_out 8-15, uio_oe 16-23, ui_in 24-31, uio_in 32-39, rst_n 40, ena 41,
clk 42): `META(open)`, `BLKH(0)`, `RUNS`, `META(close)`.

Two samples a clock, as the testbench records them: sample 2c (clk low)
holds the outputs the design registered at clock c - 1's rising edge, and
rst_n as driven for clock c; sample 2c + 1 (clk high) holds the outputs of
clock c's own edge. (Clock 0's low sample repeats its high sample's outputs.)

The design is a free-running position counter (hpos, vpos) that reads
(0, 0) in the first clock after reset, one design pixel per `cpp` clocks;
with fractional `cpp`, design pixel p covers clocks
[floor(p * cpp), floor((p + 1) * cpp)) counted from the end of reset. Its
syncs follow one of three phases:

- ``vesa``: the syncs exactly on VESA positions: hsync in its pulse on
  hpos in [H_ACTIVE + H_FRONT, + H_SYNC), vsync on whole lines vpos in
  [V_ACTIVE + V_FRONT, + V_SYNC), so vsync changes at hpos 0.
- ``testpatterns``: tt-vga-testpatterns' common/hvsync_generator.v, which
  registers the syncs from the *next* position: hsync as ``vesa``, vsync
  judged on vline = vpos + 1 from hpos H_ACTIVE + H_FRONT on, so vsync
  changes on the clock hsync starts (as tests/synth.c's synth_frame_hvsync,
  which is checked against that Verilog with iverilog).
- ``playground``: the VGA Playground's hvsync_generator
  (vga-playground src/examples/common/hvsync_generator.v), which registers
  ``hsync <= f(hpos)`` and ``vsync <= g(vpos)`` from the *current*,
  pre-increment position at the same edge that advances hpos/vpos. So in
  the clock where the position registers read (hpos, vpos), the sync
  registers hold f and g of the position one clock earlier:
    hprev = hpos - 1 (H_TOTAL - 1 when hpos == 0),
    vprev = vpos, except at hpos == 0, where the edge that wrapped hpos also
            advanced vpos, so vprev = vpos - 1 (V_TOTAL - 1 when vpos == 0).
  hsync is in its pulse for hprev in [656, 752), i.e. hpos in [657, 753):
  the leading edge is on hpos 657. vsync is g(vprev): it changes one clock
  after vpos does, at hpos 1 of the first line of the new vpos (vpos 490's
  hpos 1 for the start, vpos 492's hpos 1 for the end, at 640x480).
  The module itself is active low only; the polarity is applied here as for
  the other phases.

Colour: `pixel(x, y)` (numpy arrays) returns 6-bit rrggbb colours, used
where hpos < H_ACTIVE and vpos < V_ACTIVE, else black. Bits as tests/synth.c:
hs<<7 | b0<<6 | g0<<5 | r0<<4 | vs<<3 | b1<<2 | g1<<1 | r1.

Glitches: (clock, length) pairs whose clocks have the sync bit inverted
(``h_glitches`` on hsync, ``v_glitches`` on vsync), in trace clocks.
"""
from __future__ import annotations
import io
import math
from typing import Callable, Sequence

import numpy as np

from .modes import Mode
from .stream import Header, Writer

RESET_CLOCKS = 10
UO_MASK = 0xFF
RST_N_BIT = 40
ENA_BIT = 41
CLK_BIT = 42
PHASES = ("vesa", "testpatterns", "playground")
#: Pairs per RUNS chunk: at most 17 bytes each, under MAX_CHUNK_LEN.
RUNS_PER_CHUNK = 500_000


def _syncs(mode: Mode, phase: str, hpos: np.ndarray, vpos: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(h_pulse, v_pulse) booleans at each position."""
    cpl, lpf = mode.cpl, mode.lpf
    hss = mode.h_active + mode.h_front
    vss = mode.v_active + mode.v_front
    if phase == "vesa":
        hj, vline = hpos, vpos
    elif phase == "testpatterns":
        hj = hpos
        vline = np.where(hpos >= hss, (vpos + 1) % lpf, vpos)
    elif phase == "playground":
        hj = (hpos - 1) % cpl
        vline = np.where(hpos == 0, (vpos - 1) % lpf, vpos)
    else:
        raise ValueError(f"unknown sync phase {phase!r}; one of {PHASES}")
    h_pulse = (hj >= hss) & (hj < hss + mode.h_sync)
    v_pulse = (vline >= vss) & (vline < vss + mode.v_sync)
    return h_pulse, v_pulse


def design_pixels(mode: Mode, count: int, *, phase: str = "vesa",
                  pixel: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None,
                  start: int = 0) -> np.ndarray:
    """uo_out for design pixels start .. start + count - 1 (pixel 0 is (0, 0))."""
    p = np.arange(start, start + count, dtype=np.int64)
    hpos = p % mode.cpl
    vpos = (p // mode.cpl) % mode.lpf
    h_pulse, v_pulse = _syncs(mode, phase, hpos, vpos)
    hs = (h_pulse if mode.h_sync_positive else ~h_pulse).astype(np.uint8)
    vs = (v_pulse if mode.v_sync_positive else ~v_pulse).astype(np.uint8)
    col = np.zeros(count, dtype=np.uint8)
    if pixel is not None:
        active = (hpos < mode.h_active) & (vpos < mode.v_active)
        col[active] = np.asarray(pixel(hpos[active], vpos[active])).astype(np.uint8) & 0x3F
    r1, r0 = (col >> 5) & 1, (col >> 4) & 1
    g1, g0 = (col >> 3) & 1, (col >> 2) & 1
    b1, b0 = (col >> 1) & 1, col & 1
    return ((hs << 7) | (b0 << 6) | (g0 << 5) | (r0 << 4) | (vs << 3) | (b1 << 2) | (g1 << 1) | r1).astype(np.uint8)


def build(mode: Mode, clocks: int, *, phase: str = "vesa", cpp: float = 1,
          pixel: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None,
          reset_clocks: int = RESET_CLOCKS, reset_uo: int = 0,
          h_glitches: Sequence[tuple[int, int]] = (), v_glitches: Sequence[tuple[int, int]] = ()
          ) -> tuple[np.ndarray, np.ndarray]:
    """Per-clock (uo_out, rst_n) for `clocks` clocks, the first `reset_clocks`
    in reset (uo_out = reset_uo)."""
    if cpp <= 0:
        raise ValueError("cpp must be positive")
    run = clocks - reset_clocks
    if run < 0:
        raise ValueError("fewer clocks than reset clocks")
    # Design pixels needed to cover `run` clocks, and each one's clock count.
    npix = math.ceil(run / cpp) + 1
    bounds = np.floor(np.arange(npix + 1, dtype=np.float64) * cpp).astype(np.int64)
    widths = np.diff(bounds)
    design = design_pixels(mode, npix, phase=phase, pixel=pixel)
    body = np.repeat(design, widths)[:run]
    if len(body) != run:
        raise AssertionError("pixel expansion short")  # pragma: no cover
    uo = np.concatenate([np.full(reset_clocks, reset_uo & UO_MASK, dtype=np.uint8), body])
    for bit, glitches in ((0x80, h_glitches), (0x08, v_glitches)):
        for clock, length in glitches:
            uo[clock:clock + length] ^= bit
    rst_n = np.ones(clocks, dtype=np.uint8)
    rst_n[:reset_clocks] = 0
    return uo, rst_n


def sample_values(uo: np.ndarray, rst_n: np.ndarray) -> np.ndarray:
    """The 43-bit samples, two a clock, clock bit included."""
    uo = np.asarray(uo, dtype=np.uint64)
    rst = np.asarray(rst_n, dtype=np.uint64)
    base = (rst << np.uint64(RST_N_BIT)) | np.uint64(1 << ENA_BIT)
    prev = np.concatenate([uo[:1], uo[:-1]])
    s = np.empty(2 * len(uo), dtype=np.uint64)
    s[0::2] = base | prev
    s[1::2] = base | uo | np.uint64(1 << CLK_BIT)
    return s


def _runs(samples: np.ndarray) -> list[tuple[int, int]]:
    v = samples & np.uint64(~(1 << CLK_BIT) & ((1 << 64) - 1))
    if len(v) == 0:
        return []
    change = np.flatnonzero(v[1:] != v[:-1]) + 1
    starts = np.concatenate([[0], change])
    lens = np.diff(np.concatenate([starts, [len(v)]]))
    return list(zip(v[starts].tolist(), lens.tolist()))


def trace_bytes(uo: np.ndarray, rst_n: np.ndarray, *, clock_hz: int, project: str,
                calib_seconds: str, seconds: str, capture_after_clock: int = 0,
                frames: int = 0, status: str = "ok") -> bytes:
    """The plain VGCH v2 stream. `calib_seconds` and `seconds` are the META
    text as written (strings, read back with strtod). The closing META's
    values are only a cross-check for readers (the spec derives
    capture_after_clock from the trace); pass the reference's."""
    fp = io.BytesIO()
    w = Writer(fp, Header(version=2, sample_bits=43, mode=2, clock_hz=clock_hz, samples_per_word=0,
                          samples_per_clock=2, clock_bit=CLK_BIT, clock_first=0))
    w.meta(f"meta=open\nproject={project}\ncalib_seconds={calib_seconds}\nseconds={seconds}\nlayout=tt-um-43\n")
    w.block(0)
    pairs = _runs(sample_values(uo, rst_n))
    for i in range(0, len(pairs), RUNS_PER_CHUNK):
        w.runs(pairs[i:i + RUNS_PER_CHUNK])
    w.meta(f"meta=close\ncapture_after_clock={capture_after_clock}\nstatus={status}\nframes={frames}\n")
    return fp.getvalue()


def write_trace(path, uo: np.ndarray, rst_n: np.ndarray, **kw) -> None:
    """Writes trace_bytes(uo, rst_n, **kw) to `path`."""
    with open(path, "wb") as f:
        f.write(trace_bytes(uo, rst_n, **kw))
