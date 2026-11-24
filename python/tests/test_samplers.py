# SPDX-License-Identifier: Apache-2.0
"""The samplers: the C ports (vgacap_sample, run by build/vgacap-tbv1-dump
over a synthtrace trace) against the per-clock Python references
(vgacap.tbv1), and both against the pictures the synthetic designs draw.
tests/test_tbv1.c has more tb-v1 cases on the C side alone. centre-v1 (run by
build/vgacap-centre-dump) is checked against vgacap.centre's window and pick
from the same clock-resolution frames; tests/test_centrev1.c has the rest."""
from __future__ import annotations
import pathlib
import subprocess

import numpy as np
import pytest

from vgacap import calib, centre, synthtrace, tbv1
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


def run_tbv1(tmp_path, uo, rst_n, *, seconds: str = "60", calib_seconds: str = "1", name: str = "t"):
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


def test_centre_glitch_gap(tmp_path):
    """tbv1_glitch_gap's trace: in centre-v1 the glitch's number (3, the
    earlier of the two ends in frame 3's pulse) has no frame; later frames
    carry the same numbers, and pictures, as tb-v1's."""
    clocks = R + 100_000
    uo, rst = synthtrace.build(SMALL, clocks, pixel=diag)
    _, plain, _ = run_centre(tmp_path, uo, rst, name="plain")
    cal = calib.calibrate(bytes(uo), bytes(rst), HZ, 1.0)
    ends = calib.frame_ends(bytes(uo), cal.capture_after_clock, cal.vs.active_low)
    g = ends[3] - 40
    uo2, rst2 = synthtrace.build(SMALL, clocks, pixel=diag, v_glitches=[(g, 1)])
    geo, glitched, _ = run_centre(tmp_path, uo2, rst2, name="glitch")
    assert geo.mode == "unknown"
    assert len(plain) > 5
    assert [f[0] for f in plain] == list(range(len(plain)))
    assert [f[0] for f in glitched] == [k if k < 3 else k + 1 for k in range(len(plain))]
    assert [f[1:] for f in glitched] == [f[1:] for f in plain]
    cal2 = calib.calibrate(bytes(uo2), bytes(rst2), HZ, 1.0)
    _, tb = tbv1.sample(bytes(uo2), cal2, HZ, 60.0)
    tb_by_number = {f.number: f.rgb6 for f in tb}
    assert 3 in tb_by_number and 4 in tb_by_number
    for number, _, _, rgb6 in glitched:
        if number in tb_by_number:
            assert tb_by_number[number] == rgb6


def test_tbv1_no_sync(tmp_path):
    clocks = 2_600_000
    uo = np.full(clocks, 0x88, dtype=np.uint8)
    uo[:R] = 0
    rst = np.ones(clocks, dtype=np.uint8)
    rst[:R] = 0
    geo, frames, g, _ = run_tbv1(tmp_path, uo, rst, calib_seconds="0.1")
    assert not geo.ok and geo.status == "no-sync" and frames == []


def test_cround():
    assert tbv1.cround(2.5) == 3.0 and tbv1.cround(1.5) == 2.0 and tbv1.cround(0.49999999999999994) == 0.0
    assert tbv1.cround(-2.5) == -3.0 and tbv1.cround(1.90625) == 2.0


# ---- centre-v1 -------------------------------------------------------------

CENTRE_DUMP = ROOT / "build" / "vgacap-centre-dump"


def full(x, y):
    """Never black: the whole active area is lit."""
    return 1 + (x + 3 * y) % 63


def parse_centre_dump(text: str) -> dict:
    out: dict = {"frames": []}
    for line in text.splitlines():
        key, _, rest = line.partition("=")
        if key in ("ok", "line_clocks", "hs", "outside"):
            kv = dict(x.split("=", 1) for x in line.split())
        if key == "capture_after_clock":
            out["capture"] = int(rest)
        elif key == "ok":
            out["geo"] = kv
        elif key == "line_clocks":
            out["L"], out["lines"] = int(kv["line_clocks"]), int(kv["lines"])
        elif key == "hs":
            out["hs"], out["vs"] = (calib.SyncInfo(*(bool(int(v)) if i < 2 else int(v)
                                                     for i, v in enumerate(kv[s].split(","))))
                                    for s in ("hs", "vs"))
        elif key == "outside":
            out["diag"] = {k: (v if v == "-" else int(v)) for k, v in kv.items()}
        elif key == "ends":
            out["ends"] = int(rest)
        elif key == "frames":
            out["n"] = int(rest)
        elif key == "frame":
            out["frames"].append(tuple(int(v) for v in rest.split()))
    assert out["n"] == len(out["frames"])
    return out


def run_centre(tmp_path, uo, rst_n, *, dx: int = 0, dy: int = 0, name: str = "c"):
    """C centre-v1 over a trace of these clocks, and centre.py's window and
    pick from the same clock-resolution frames, given the C calibration's
    numbers (test_calib.py checks those); asserts they agree, frame for frame
    and byte for byte. Returns (Geometry, frames, diag), frames as (number,
    start clock, missing, rgb6 bytes)."""
    path = tmp_path / f"{name}.vgacap"
    synthtrace.write_trace(path, uo, rst_n, clock_hz=HZ, project="tt_um_synth", calib_seconds="1", seconds="60")
    rgb_path, raw_path = tmp_path / f"{name}.rgb6", tmp_path / f"{name}.raw"
    proc = subprocess.run([str(CENTRE_DUMP), str(path), str(rgb_path), str(raw_path), str(dx), str(dy)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    c = parse_centre_dump(proc.stdout)
    geo = centre.geometry(c["hs"], c["vs"], dx, dy)
    g = c["geo"]
    assert int(g["ok"]) == geo.ok and g["status"] == geo.status and g["mode"] == geo.mode
    assert float(g["cpp"]) == geo.cpp
    assert (int(g["width"]), int(g["height"]), int(g["hback"]), int(g["vback"])) == \
        (geo.width, geo.height, geo.hback, geo.vback)
    assert (c["L"], c["lines"]) == (geo.line_clocks, geo.lines)
    rgb, raw = rgb_path.read_bytes(), raw_path.read_bytes()
    size, rsize = geo.width * geo.height, geo.line_clocks * geo.lines
    assert len(rgb) == size * len(c["frames"]) and len(raw) == rsize * len(c["frames"])
    frames = []
    for i, (number, start, missing) in enumerate(c["frames"]):
        r = np.frombuffer(raw, dtype=np.uint8, count=rsize, offset=i * rsize).reshape(geo.lines, geo.line_clocks)
        picked, miss = centre.pick(r, geo)
        assert picked == rgb[i * size:(i + 1) * size] and miss == missing
        frames.append((number, start, missing, picked))
    return geo, frames, c["diag"]


CENTRE_CASES = {
    # name: (cpp, dx, dy)
    "cpp1": (1, 0, 0),
    "cpp2": (2, 0, 0),
    "cpp1.90625": (1.90625, 0, 0),
    "cpp0.95": (0.95, 0, 0),
    "override": (1, 3, -2),
}


@pytest.fixture(scope="module", params=list(CENTRE_CASES))
def centre_case(request, tmp_path_factory):
    cpp, dx, dy = CENTRE_CASES[request.param]
    uo, rst = synthtrace.build(M640, R + int(7 * M640.cpl * M640.lpf * cpp), cpp=cpp, pixel=diag)
    res = run_centre(tmp_path_factory.mktemp("centre"), uo, rst, dx=dx, dy=dy, name=request.param)
    return request.param, uo, res


def test_centre_matches_python(centre_case):
    """centre.py's window and pick agree with centrev1 (run_centre asserts
    it), on whole, fractional and below-one cpp and with an override."""
    name, _, (geo, frames, d) = centre_case
    cpp, dx, dy = CENTRE_CASES[name]
    assert geo.ok and geo.mode == "640x480@60" and geo.cpp == cpp
    assert (geo.width, geo.height, geo.hback, geo.vback) == (640, 480, 48 + dx, 33 + dy)
    assert (d["dx"], d["dy"]) == (dx, dy)
    assert len(frames) >= 1
    assert all(missing == 0 for _, _, missing, _ in frames)


def test_centre_below_one(centre_case):
    """Every pixel holds the colour of the clock the rule names, counted from
    the frame's start clock in whole lines. At cpp 0.95 some design pixels
    have no clock and appear nowhere."""
    name, uo, (geo, frames, _) = centre_case
    if name != "cpp0.95":
        pytest.skip("the cpp 0.95 case")
    assert geo.line_clocks == 760
    x = np.floor((geo.hsync + geo.hback + np.arange(geo.width) + 0.5) * geo.cpp).astype(np.int64)
    rows = geo.vpulse_lines + geo.vback + np.arange(geo.height)
    xx, yy = np.meshgrid(np.arange(640), np.arange(480))
    assert frames
    for _, start, _, rgb6 in frames:
        u = np.asarray(uo)[start + rows[:, None] * geo.line_clocks + x[None, :]]
        want = (((u & 1) << 5) | (((u >> 4) & 1) << 4) | (((u >> 1) & 1) << 3) |
                (((u >> 5) & 1) << 2) | (((u >> 2) & 1) << 1) | ((u >> 6) & 1)).astype(np.uint8)
        got = np.frombuffer(rgb6, dtype=np.uint8).reshape(480, 640)
        assert np.array_equal(got, want)
        assert not np.array_equal(got, diag(xx, yy))


def test_centre_playground_phase(tmp_path):
    """The VGA Playground's hvsync_generator, one pixel and one line off VESA
    timing: the window stays put, so the frames are the design's picture
    shifted by one pixel and one line, the design's first column and first
    row are the lit positions outside it, and the suggestion is (-1, -1).
    With that override the frames are pixel-exact."""
    uo, rst = synthtrace.build(M640, R + 7 * M640.cpl * M640.lpf, phase="playground", pixel=full)
    geo, frames, d = run_centre(tmp_path, uo, rst, name="pg")
    assert geo.ok and (geo.hback, geo.vback) == (48, 33)
    x, y = np.meshgrid(np.arange(640), np.arange(480))
    want = np.zeros((480, 640), dtype=np.uint8)
    want[:479, :639] = full(x[1:, 1:], y[1:, 1:])
    assert frames
    for _, _, missing, rgb6 in frames:
        assert missing == 0
        assert np.array_equal(np.frombuffer(rgb6, dtype=np.uint8).reshape(480, 640), want)
    assert d["outside"] == 640 + 480 - 1
    assert (d["suggest_dx"], d["suggest_dy"]) == (-1, -1)
    _, frames, d = run_centre(tmp_path, uo, rst, dx=-1, dy=-1, name="pg_fixed")
    assert frames
    for _, _, _, rgb6 in frames:
        assert np.array_equal(np.frombuffer(rgb6, dtype=np.uint8).reshape(480, 640), full(x, y))
    assert d["outside"] == 0 and (d["suggest_dx"], d["suggest_dy"]) == (-1, -1)
