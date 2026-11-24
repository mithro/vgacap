# SPDX-License-Identifier: Apache-2.0
"""The shared calibration and frame clock: the C port (vgacap_sample, run by
build/vgacap-calib-dump over a synthtrace trace) against the per-clock
Python reference (vgacap.calib), and both against values worked out from the
timing. tests/test_calib.c has the same cases on the C side alone."""
from __future__ import annotations
import pathlib
import subprocess

import numpy as np
import pytest

from vgacap import calib, synthtrace
from vgacap.modes import MODES, Mode

DUMP = pathlib.Path(__file__).resolve().parents[2] / "build" / "vgacap-calib-dump"
HZ = 25_175_000
R = synthtrace.RESET_CLOCKS
K64 = 1 << 16
M640 = MODES["640x480@60"]
F640 = M640.cpl * M640.lpf        # 420000
#: A small mode (80-clock lines, 3200-clock frames) for the cases that need no 640x480.
SMALL = Mode("small", 64, 4, 8, 4, 30, 2, 3, 5, False, False)


def parse_dump(text: str) -> dict:
    out: dict = {"ends": []}
    for line in text.splitlines():
        if line.startswith("capture_after_clock="):
            out["capture"] = int(line.split("=")[1])
        elif line.startswith(("hs ", "vs ")):
            name, *kv = line.split()
            out[name] = {k: int(v) for k, v in (x.split("=") for x in kv)}
        elif line.startswith("ends="):
            out["n"] = int(line.split("=")[1])
        elif line.startswith("end="):
            out["ends"].append(int(line.split("=")[1]))
    assert out["n"] == len(out["ends"])
    return out


def sync_dict(s: calib.SyncInfo, phases: int) -> dict:
    return {"found": int(s.found), "active_low": int(s.active_low), "pulse": s.pulse,
            "period": s.period, "phases": phases}


def run_case(tmp_path, uo, rst_n, calib_seconds: str = "0.1", name: str = "t"):
    """Python reference and C on the same clocks; asserts they agree and
    returns (Calibration, ends)."""
    uo_b, rst_b = bytes(np.asarray(uo, dtype=np.uint8)), bytes(np.asarray(rst_n, dtype=np.uint8))
    ref = calib.calibrate(uo_b, rst_b, HZ, float(calib_seconds))
    ends = calib.frame_ends(uo_b, ref.capture_after_clock, ref.vs.active_low)
    path = tmp_path / f"{name}.vgacap"
    synthtrace.write_trace(path, uo, rst_n, clock_hz=HZ, project="tt_um_synth",
                           calib_seconds=calib_seconds, seconds="1",
                           capture_after_clock=ref.capture_after_clock, frames=len(ends))
    proc = subprocess.run([str(DUMP), str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    c = parse_dump(proc.stdout)
    assert c["capture"] == ref.capture_after_clock
    assert c["hs"] == sync_dict(ref.hs, ref.h_phases)
    assert c["vs"] == sync_dict(ref.vs, ref.v_phases)
    assert c["ends"] == ends
    return ref, ends


def level_changes(uo, bit: int) -> np.ndarray:
    """Clocks (from R on) where the sync bit differs from the clock before,
    the clock before R counting as level 0 (SyncTracker's initial false)."""
    lv = (np.asarray(uo[R:]) & bit) != 0
    prev = np.concatenate([[False], lv[:-1]])
    return np.flatnonzero(lv != prev) + R


def roundup64k(t: int) -> int:
    return -(-t // K64) * K64


# testpatterns' hvsync_generator from (0, 0) at clock R: vsync starts (ends)
# on the clock hsync starts, on the line before vline enters (leaves) the pulse.
def v_end_640(k: int) -> int:
    return R + (M640.v_active + M640.v_front + M640.v_sync - 1) * M640.cpl + M640.h_active + M640.h_front + k * F640


@pytest.fixture(scope="module")
def tp640(tmp_path_factory):
    """8 frames of 640x480 testpatterns timing, a bar pattern."""
    uo, rst = synthtrace.build(M640, R + 8 * F640, phase="testpatterns",
                               pixel=lambda x, y: (x // 10) & 0x3F)
    ref, ends = run_case(tmp_path_factory.mktemp("tp640"), uo, rst)
    return uo, ref, ends


def test_calib_640x480_vectors(tp640):
    uo, ref, _ = tp640
    assert ref.calib_limit == 2_517_500
    assert (ref.hs.found, ref.hs.active_low, ref.hs.pulse, ref.hs.period) == (True, True, 96, 800)
    assert (ref.vs.found, ref.vs.active_low, ref.vs.pulse, ref.vs.period) == (True, True, 1600, 420000)
    # The 8th vsync phase completes at the 8th level change; tb.cpp sees it
    # at T = c + 1 and stops at the next multiple of 65536.
    c8 = int(level_changes(uo, 0x08)[7])
    assert ref.capture_after_clock == roundup64k(c8 + 1) == 1_703_936


@pytest.mark.parametrize("phase", ["vesa", "playground"])
def test_calib_640x480_vectors_phases(tmp_path, phase):
    uo, rst = synthtrace.build(M640, R + 5 * F640, phase=phase)
    ref, _ = run_case(tmp_path, uo, rst)
    assert (ref.hs.pulse, ref.hs.period, ref.vs.pulse, ref.vs.period) == (96, 800, 1600, 420000)
    assert ref.capture_after_clock == roundup64k(int(level_changes(uo, 0x08)[7]) + 1)


def test_playground_phase_edges():
    """The playground module's syncs: hsync leading edge on hpos 657, vsync
    changing at hpos 1 of the line after vpos changes."""
    m = M640
    d = synthtrace.design_pixels(m, F640, phase="playground")
    hs = (d >> 7) & 1
    vs = (d >> 3) & 1
    assert hs[656] == 1 and hs[657] == 0 and hs[752] == 0 and hs[753] == 1
    assert vs[490 * 800] == 1 and vs[490 * 800 + 1] == 0          # vpos 490: pulse from hpos 1
    assert vs[492 * 800] == 0 and vs[492 * 800 + 1] == 1          # vpos 492: out from hpos 1
    t = synthtrace.design_pixels(m, F640, phase="testpatterns")
    assert ((t >> 7) & 1)[656] == 0 and ((t >> 3) & 1)[489 * 800 + 656] == 0
    assert ((t >> 3) & 1)[489 * 800 + 655] == 1
    v = synthtrace.design_pixels(m, F640, phase="vesa")
    assert ((v >> 3) & 1)[490 * 800 - 1] == 1 and ((v >> 3) & 1)[490 * 800] == 0


@pytest.mark.parametrize("hpol", [False, True])
@pytest.mark.parametrize("vpol", [False, True])
def test_calib_polarities(tmp_path, hpol, vpol):
    m = Mode("small", 64, 4, 8, 4, 30, 2, 3, 5, hpol, vpol)
    uo, rst = synthtrace.build(m, R + 100_000, phase="testpatterns")
    ref, _ = run_case(tmp_path, uo, rst)
    assert ref.hs.found and ref.vs.found
    assert (ref.hs.active_low, ref.vs.active_low) == (not hpol, not vpol)
    assert (ref.hs.pulse, ref.hs.period, ref.vs.pulse, ref.vs.period) == (8, 80, 240, 3200)
    assert ref.capture_after_clock == K64


def test_calib_no_sync(tmp_path):
    clocks = 2_600_000
    uo = np.full(clocks, 0x88, dtype=np.uint8)
    rst = np.ones(clocks, dtype=np.uint8)
    rst[:R] = 0
    uo[:R] = 0
    ref, ends = run_case(tmp_path, uo, rst)
    assert ref.capture_after_clock == 2_517_500
    assert not ref.hs.found and not ref.vs.found
    assert ref.hs.active_low and ref.vs.active_low      # tb.cpp's defaults
    assert ends == []


def test_calib_glitchy_hsync(tmp_path):
    # hsync changes at clock R + 2k (k >= 1); phase k completes at
    # T = R + 2k + 1, and tb.cpp stops once there are 4,000,001.
    clocks = 8_100_000
    c = np.arange(clocks)
    uo = ((((c - R) >> 1) & 1) << 7 | 0x08).astype(np.uint8)
    uo[:R] = 0
    rst = (c >= R).astype(np.uint8)
    ref, _ = run_case(tmp_path, uo, rst, calib_seconds="0.5")
    assert ref.h_phases == 4_000_001
    assert ref.capture_after_clock == R + 2 * 4_000_001 + 1
    assert not ref.vs.found


@pytest.mark.parametrize("phase,cpp", [("playground", 1.5), ("testpatterns", 2), ("vesa", 1.25)])
def test_calib_run_equals_clock(tmp_path, phase, cpp):
    """C fed as runs (with half-clock runs from the trace) equals the
    per-clock reference, with fractional clocks per pixel and glitches."""
    uo, rst = synthtrace.build(SMALL, R + 150_000, phase=phase, cpp=cpp,
                               pixel=lambda x, y: (x + y) & 0x3F,
                               h_glitches=[(70_000, 1), (90_001, 3)], v_glitches=[(100_000, 2)])
    ref, ends = run_case(tmp_path, uo, rst)
    assert ref.hs.found and ref.vs.found
    assert ref.vs.period == round(3200 * cpp)
    assert len(ends) > 10


def test_synthtrace_fractional_cpp():
    """Design pixel p covers clocks [floor(p * cpp), floor((p + 1) * cpp))."""
    m = SMALL
    uo, _ = synthtrace.build(m, R + 2000, phase="vesa", cpp=1.5, pixel=lambda x, y: x & 0x3F)
    d = synthtrace.design_pixels(m, 2000, phase="vesa", pixel=lambda x, y: x & 0x3F)
    for c in range(2000):
        p = 0
        while int((p + 1) * 1.5) <= c:
            p += 1
        assert uo[R + c] == d[p]


def test_fc_counts(tp640):
    _, ref, ends = tp640
    assert len(ends) >= 3
    k = 0
    while v_end_640(k) < ref.capture_after_clock + 1:
        k += 1
    assert ends[0] == v_end_640(k)
    assert all(b - a == F640 for a, b in zip(ends, ends[1:]))


def test_fc_end_on_capture_clock_not_counted(tmp_path):
    # 64Ki-clock frames whose vsync pulse (the last 1024 clocks) ends on
    # multiples of 65536: calibration stops at T = 262144, the very clock
    # that pulse ends, and tb.cpp (prev_v false) does not count it.
    clocks = 7 * K64
    c = np.arange(clocks)
    hs = (c % 256) >= 32
    vs = (c % K64) < K64 - 1024
    uo = ((hs.astype(np.uint8) << 7) | (vs.astype(np.uint8) << 3)).astype(np.uint8)
    uo[:R] = 0
    rst = (c >= R).astype(np.uint8)
    ref, ends = run_case(tmp_path, uo, rst)
    assert ref.capture_after_clock == 4 * K64
    assert not uo[4 * K64 - 1] & 0x08 and uo[4 * K64] & 0x08
    assert ends == [5 * K64, 6 * K64]


def test_fc_glitch_adds_end(tmp_path):
    g = v_end_640(5) - 800              # mid-pulse, after capture (1703936)
    uo, rst = synthtrace.build(M640, R + 8 * F640, phase="testpatterns", v_glitches=[(g, 1)])
    ref, ends = run_case(tmp_path, uo, rst)
    assert ref.capture_after_clock == 1_703_936
    assert ends == [v_end_640(4), g, v_end_640(5), v_end_640(6), v_end_640(7)]


def test_calib_limit_double():
    """tb.cpp:364 truncates the double product: 0.29 * 25175000 is
    7300749.999999999, so the limit is 7300749 (tests/test_calib.c too)."""
    assert calib.calib_limit(HZ, 0.29) == 7_300_749
    assert calib.calib_limit(HZ, 0.1) == 2_517_500


def sync_uo(hs: np.ndarray, vs: np.ndarray) -> np.ndarray:
    uo = ((hs.astype(np.uint8) << 7) | (vs.astype(np.uint8) << 3)).astype(np.uint8)
    uo[:R] = 0
    return uo


def test_calib_first_phase_skipped(tmp_path):
    # vsync (active low): high from R, pulses [1000, 1100) and [3000, 3100),
    # low again from 5000 on: six phases. analyse skips phase 0 (tb.cpp:221),
    # leaving three high and two low, so vsync is not found; counting phase 0
    # would give three of each.
    clocks = 110_000
    c = np.arange(clocks)
    hs = (c % 256) >= 32
    vs = ~(((c >= 1000) & (c < 1100)) | ((c >= 3000) & (c < 3100)) | (c >= 5000))
    rst = (c >= R).astype(np.uint8)
    ref, _ = run_case(tmp_path, sync_uo(hs, vs), rst, calib_seconds="0.004")
    assert ref.v_phases == 6 and not ref.vs.found
    assert ref.hs.found and ref.capture_after_clock == 100_700


def test_calib_median_upper(tmp_path):
    # hsync low 8 clocks, then high 30 and 50 in turn: with an even number of
    # high phases, tb.cpp's median (v[size / 2] after sorting) is 50, not 30.
    clocks = 110_000
    c = np.arange(clocks)
    m = (c - R) % 96
    hs = ((m >= 8) & (m < 38)) | (m >= 46)
    rst = (c >= R).astype(np.uint8)
    ref, _ = run_case(tmp_path, sync_uo(hs, np.ones(clocks, dtype=bool)), rst, calib_seconds="0.003997")
    # High phases completed before the limit, from the levels (phase 0, the
    # first pulse, is not high).
    lv = hs[R:ref.calib_limit]
    edges = np.flatnonzero(lv[1:] != lv[:-1]) + 1
    highs = [int(b - a) for a, b in zip(edges[:-1], edges[1:]) if lv[a]]
    assert len(highs) % 2 == 0
    assert ref.hs.found and ref.hs.period == 8 + sorted(highs)[len(highs) // 2] == 58
