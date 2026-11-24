# SPDX-License-Identifier: Apache-2.0
"""The samplers: the C ports (vgacap_sample, run by build/vgacap-tbv1-dump
over a synthtrace trace) against the per-clock Python references
(vgacap.tbv1), and both against the pictures the synthetic designs draw.
tests/test_tbv1.c has more tb-v1 cases on the C side alone."""
from __future__ import annotations
import pathlib
import subprocess

import numpy as np
import pytest

from vgacap import calib, synthtrace, tbv1
from vgacap.modes import MODES, Mode

ROOT = pathlib.Path(__file__).resolve().parents[2]
TBV1_DUMP = ROOT / "build" / "vgacap-tbv1-dump"
HZ = 25_175_000
R = synthtrace.RESET_CLOCKS
M640 = MODES["640x480@60"]
M800_72 = MODES["800x600@72"]
#: 640x480@75's timing (840 line pixels, 500 lines) on 800-clock lines with a
#: 64-clock hsync: tb-v1 takes it as 640x480@75 at 800 / 840 clocks per pixel.
M75_800 = Mode("75on800", 640, 16, 64, 80, 480, 1, 3, 16, False, False)
#: An 80-clock line, below every tb.cpp mode's 0.9 clocks per pixel: unknown.
SMALL = Mode("small", 64, 4, 8, 4, 30, 2, 3, 5, False, False)


def diag(x, y):
    return (x + 3 * y) & 0x3F


def parse_dump(text: str) -> dict:
    out: dict = {"frames": []}
    for line in text.splitlines():
        key, _, rest = line.partition("=")
        if key == "capture_after_clock":
            out["capture"] = int(rest)
        elif key == "ok":
            kv = dict(x.split("=", 1) for x in line.split())
            out["geo"] = kv
        elif key == "target_frames":
            kv = dict(x.split("=", 1) for x in line.split())
            out["target_frames"] = int(kv["target_frames"])
            out["clock_limit"] = int(kv["clock_limit"])
        elif key == "ends":
            out["ends"] = int(rest)
        elif key == "frames":
            out["n"] = int(rest)
        elif key == "frame":
            num, start = rest.split()
            out["frames"].append((int(num), int(start)))
    assert out["n"] == len(out["frames"])
    return out


def run_tbv1(tmp_path, uo, rst_n, *, seconds: str = "60", calib_seconds: str = "0.1", name: str = "t"):
    """Python reference and C on the same clocks; asserts they agree, frame
    for frame and byte for byte, and returns (Geometry, frames, C geometry
    text fields, Calibration)."""
    uo_b, rst_b = bytes(np.asarray(uo, dtype=np.uint8)), bytes(np.asarray(rst_n, dtype=np.uint8))
    cal = calib.calibrate(uo_b, rst_b, HZ, float(calib_seconds))
    geo, frames = tbv1.sample(uo_b, cal, HZ, float(seconds))
    path = tmp_path / f"{name}.vgacap"
    synthtrace.write_trace(path, uo, rst_n, clock_hz=HZ, project="tt_um_synth",
                           calib_seconds=calib_seconds, seconds=seconds,
                           capture_after_clock=cal.capture_after_clock, frames=len(frames))
    out = tmp_path / f"{name}.rgb6"
    proc = subprocess.run([str(TBV1_DUMP), str(path), str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    c = parse_dump(proc.stdout)
    assert c["capture"] == cal.capture_after_clock
    g = c["geo"]
    assert int(g["ok"]) == geo.ok and g["status"] == geo.status and g["mode"] == geo.mode
    assert float(g["cpp"]) == geo.cpp
    assert (int(g["width"]), int(g["height"]), int(g["hback"]), int(g["vback"])) == \
        ((geo.width, geo.height, geo.hback, geo.vback) if geo.ok else (0, 0, 0, 0))
    assert (c["target_frames"], c["clock_limit"]) == (geo.target_frames, geo.clock_limit)
    assert c["frames"] == [(f.number, f.start_clock) for f in frames]
    assert out.read_bytes() == b"".join(f.rgb6 for f in frames)
    return geo, frames, g, cal


CASES = {
    # name: (mode, cpp, expected tb mode, expected cpp text)
    "640x480@60_cpp1": (M640, 1, "640x480@60", "1"),
    "800x600@72_cpp1": (M800_72, 1, "800x600@72", "1"),
    "640x480@60_cpp2": (M640, 2, "640x480@60", "2"),
    "640x480@60_cpp1.90625": (M640, 1.90625, "640x480@60", "1.90625"),
    "75on800": (M75_800, 1, "640x480@75", "0.9523809523809523"),
}


@pytest.fixture(scope="module", params=list(CASES))
def tbv1_case(request, tmp_path_factory):
    mode, cpp, _, _ = CASES[request.param]
    clocks = R + int(7 * mode.cpl * mode.lpf * cpp)
    uo, rst = synthtrace.build(mode, clocks, cpp=cpp, pixel=diag)
    res = run_tbv1(tmp_path_factory.mktemp("tbv1"), uo, rst, name=request.param)
    return request.param, uo, res


def test_tbv1_matches_python(tbv1_case):
    name, _, (geo, frames, g, cal) = tbv1_case
    _, _, want_mode, want_cpp = CASES[name]
    assert geo.ok and geo.mode == want_mode
    assert g["mode"] == want_mode and g["cpp"] == want_cpp
    assert len(frames) >= 1
    for k, f in enumerate(frames):
        assert f.number == k
        assert len(f.rgb6) == geo.width * geo.height
        assert any(f.rgb6)


def test_tbv1_frame_numbers_are_frame_clock(tbv1_case):
    """tb-v1 frame k starts at the frame clock's end k."""
    _, uo, (geo, frames, _, cal) = tbv1_case
    ends = calib.frame_ends(bytes(uo), cal.capture_after_clock, cal.vs.active_low)
    assert len(frames) == len(ends) - 1
    assert [f.start_clock for f in frames] == ends[:-1]


def test_tbv1_reproduces_half_pixel_early(tmp_path):
    """cpp 2: tb-v1's pixel p is the design's p - 1 (p >= 1), its column 0
    the design's last blanking pixel. The published behaviour, recorded."""
    clocks = R + 7 * M640.cpl * M640.lpf * 2
    uo, rst = synthtrace.build(M640, clocks, cpp=2, pixel=diag)
    geo, frames, _, _ = run_tbv1(tmp_path, uo, rst)
    assert (geo.width, geo.height, geo.hback, geo.vback) == (640, 480, 48, 33)
    x, y = np.meshgrid(np.arange(640), np.arange(480))
    want = np.zeros((480, 640), dtype=np.uint8)
    want[:, 1:] = diag(x[:, :-1], y[:, :-1])
    assert frames
    for f in frames:
        assert np.array_equal(np.frombuffer(f.rgb6, dtype=np.uint8).reshape(480, 640), want)


def test_tbv1_tennis_lines(tmp_path):
    """640x480 timing with 506 lines: no line-count match; the fallback keeps
    every line after the vsync pulse."""
    m = Mode("tennis", 640, 16, 96, 48, 480, 10, 2, 14, False, False)
    uo, rst = synthtrace.build(m, R + 7 * m.cpl * m.lpf, pixel=diag)
    geo, frames, g, _ = run_tbv1(tmp_path, uo, rst)
    assert geo.mode == "640x480@60" and g["cpp"] == "1"
    assert (geo.width, geo.height, geo.hback, geo.vback) == (640, 506 - 2, 48, 0)
    x, y = np.meshgrid(np.arange(640), np.arange(480))
    want = np.zeros((504, 640), dtype=np.uint8)
    want[14:494] = diag(x, y)
    assert frames
    for f in frames:
        assert np.array_equal(np.frombuffer(f.rgb6, dtype=np.uint8).reshape(504, 640), want)


def test_tbv1_frame_target(tmp_path):
    """tb.cpp's frame target, (uint64_t)(seconds * fps + 0.5) frames, then
    stop (SMALL's 7867 fps keeps it short; test_tbv1.c has 0.1 s at 60 fps)."""
    uo, rst = synthtrace.build(SMALL, R + 160_000, pixel=diag)
    geo, frames, _, cal = run_tbv1(tmp_path, uo, rst, seconds="0.003")
    fps = HZ / cal.vs.period
    assert geo.target_frames == int(0.003 * fps + 0.5) == 24
    assert len(frames) == 24
    ends = calib.frame_ends(bytes(uo), cal.capture_after_clock, cal.vs.active_low)
    assert len(ends) > 25


def test_tbv1_glitch_gap(tmp_path):
    """A 1-clock vsync glitch inside frame 3's pulse: tb-v1 numbers frames as
    tb.cpp does, one extra number. (Task 13b adds centre-v1's half.)"""
    clocks = R + 100_000
    uo, rst = synthtrace.build(SMALL, clocks, pixel=diag)
    geo, plain, _, cal = run_tbv1(tmp_path, uo, rst, name="plain")
    assert geo.mode == "unknown"
    ends = calib.frame_ends(bytes(uo), cal.capture_after_clock, cal.vs.active_low)
    g = ends[3] - 40                     # mid-pulse (3 lines of 80 clocks)
    assert not uo[g] & 0x08 and not uo[g - 1] & 0x08 and not uo[g + 1] & 0x08
    uo2, rst2 = synthtrace.build(SMALL, clocks, pixel=diag, v_glitches=[(g, 1)])
    _, glitched, _, _ = run_tbv1(tmp_path, uo2, rst2, name="glitch")
    assert len(glitched) == len(plain) + 1
    assert [f.number for f in glitched] == list(range(len(glitched)))
    assert glitched[3].start_clock == g and glitched[4].start_clock == ends[3]
    assert [f.rgb6 for f in glitched[:3]] == [f.rgb6 for f in plain[:3]]
    assert [(f.start_clock, f.rgb6) for f in glitched[4:]] == [(f.start_clock, f.rgb6) for f in plain[3:]]


def test_tbv1_no_sync(tmp_path):
    clocks = 2_600_000
    uo = np.full(clocks, 0x88, dtype=np.uint8)
    uo[:R] = 0
    rst = np.ones(clocks, dtype=np.uint8)
    rst[:R] = 0
    geo, frames, g, _ = run_tbv1(tmp_path, uo, rst)
    assert not geo.ok and geo.status == "no-sync" and frames == []


def test_cround():
    assert tbv1.cround(2.5) == 3.0 and tbv1.cround(1.5) == 2.0 and tbv1.cround(0.49999999999999994) == 0.0
    assert tbv1.cround(-2.5) == -3.0 and tbv1.cround(1.90625) == 2.0
