# SPDX-License-Identifier: Apache-2.0
"""vgacap-sample (src/tools/vgacap_sample.c): one pass over a simulation
trace, its samplers (centre-v1 by default, both when asked), their stores, frames.txt and hash files, against the
Python references (vgacap.tbv1 for tb-v1; vgacap.centre's pick over
build/vgacap-centre-dump's clock-resolution frames for centre-v1, as
test_samplers.run_centre; vgacap.framesfile for the files). Traces come from
vgacap.synthtrace, rewritten by build/trace-recode as zstd traces with the
writer's block boundaries."""
from __future__ import annotations
import gzip
import os
import pathlib
import struct
import subprocess
import threading

import numpy as np
import pytest
import xxhash
import zstandard

from vgacap import calib, hashes, synthtrace, tbv1
from vgacap import framesfile as ff
from vgacap.modes import MODES

from test_samplers import run_centre

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
SAMPLE = BUILD / "vgacap-sample"
RECODE = BUILD / "trace-recode"
DUMP = BUILD / "vgacap-dump"
HZ = 25_175_000
R = synthtrace.RESET_CLOCKS
M640 = MODES["640x480@60"]
FRAME = M640.cpl * M640.lpf
NFRAMES = 12
PROJECT = "tt_um_synth_sample"
NAME = "run"
SAMPLERS = ("tb-v1", "centre-v1")
NOT_SIM = ("is not a simulation trace (needs samples_per_clock 2 and layout=tt-um-43); "
           "board captures are decoded with vgacap-frames")


def diag(x, y):
    return (x + 3 * y) & 0x3F


def boxed(colour):
    def f(x, y):
        c = diag(x, y)
        return np.where((x >= 100) & (x < 300) & (y >= 50) & (y < 200), colour, c)
    return f


def changing(nframes: int = NFRAMES):
    """640x480@60 at cpp 1: design frame k shows variant (k // 3) % 3, so
    pictures repeat in runs and recur."""
    clocks = R + nframes * FRAME
    variants = [synthtrace.build(M640, clocks, pixel=p) for p in (diag, boxed(0x15), boxed(0x2A))]
    uo = variants[0][0].copy()
    rst = variants[0][1]
    for k in range(nframes):
        s = R + k * FRAME
        uo[s:s + FRAME] = variants[(k // 3) % 3][0][s:s + FRAME]
    return uo, rst


def write_trace(tmp_path, uo, rst, name="t", *, status="ok", calib_seconds="1", seconds="60"):
    """A zstd trace of these clocks (the writer's blocks); returns its path."""
    plain = tmp_path / f"{name}.vgacap"
    synthtrace.write_trace(plain, uo, rst, clock_hz=HZ, project=PROJECT, calib_seconds=calib_seconds,
                           seconds=seconds, status=status)
    out = tmp_path / f"{name}.trace.zst"
    subprocess.run([str(RECODE), str(plain), str(out)], check=True, capture_output=True, text=True)
    return out


def trace_blocks(path) -> tuple[int, int]:
    """(blocks, samples) as vgacap-dump reports them."""
    proc = subprocess.run([str(DUMP), str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    first = proc.stdout.splitlines()[0].split()
    assert first[0] == "trace"
    kv = dict(x.split("=") for x in first[1:])
    return int(kv["blocks"]), int(kv["samples"])


#: Both samplers, asked for: without --sampler only centre-v1 runs (Ruling W:
#: tb-v1 is legacy and runs only on request).
BOTH = ("--sampler", "tb-v1", "--sampler", "centre-v1")


def run_sample(trace, out, *args, samplers=BOTH, env=None, pass_fds=()):
    return subprocess.run([str(SAMPLE), str(trace), "--out", str(out), "--name", NAME, *samplers, *args],
                          capture_output=True, text=True, env=env, pass_fds=pass_fds)


def store_name(sampler):
    return f"{NAME}_{sampler}_frames.rgb6.zst"


def read_store(path) -> bytes:
    d = zstandard.ZstdDecompressor(max_window_size=2 ** 27)
    return d.decompressobj().decompress(path.read_bytes())


def build_ff(sampler, sampler_status, mode, width, height, cpp, capture, frames, *, status, centre=None):
    """The reference FramesFile and the distinct frames' bytes, from frames
    (number, start clock, missing, rgb6) in number order."""
    distinct: list[bytes] = []
    index: dict[bytes, int] = {}
    recs: list[ff.Frame] = []
    for number, start, missing, rgb6 in frames:
        assert number >= len(recs)
        while len(recs) < number:
            recs.append(ff.Frame(-1))
        if rgb6 not in index:
            index[rgb6] = len(distinct)
            distinct.append(rgb6)
        recs.append(ff.Frame(index[rgb6], start, missing))
    store = b"".join(distinct)
    f = ff.FramesFile(PROJECT, status, sampler, sampler_status, width, height, mode, HZ, cpp, capture,
                      store_name(sampler), len(store), xxhash.xxh3_128_hexdigest(store),
                      [hashes.xxh3_128_rgb6(b) for b in distinct], recs, centre)
    return f, distinct


def reference(tmp_path, uo, rst, *, status="ok", dx=0, dy=0):
    """{sampler: (FramesFile, distinct bytes)} from the Python references."""
    uo_b, rst_b = bytes(uo), bytes(rst)
    cal = calib.calibrate(uo_b, rst_b, HZ, 1.0)
    geo, frames = tbv1.sample(uo_b, cal, HZ, 60.0)
    w, h = (geo.width, geo.height) if geo.ok else (0, 0)
    tb = build_ff("tb-v1", geo.status, geo.mode, w, h, geo.cpp, cal.capture_after_clock,
                  [(f.number, f.start_clock, 0, f.rgb6) for f in frames], status=status)
    cgeo, cframes, d = run_centre(tmp_path, uo, rst, dx=dx, dy=dy, name="ref_centre")
    centre = ff.Centre((d["dx"], d["dy"]), (cgeo.hback, cgeo.vback), d["outside"],
                       (d["suggest_dx"], d["suggest_dy"]), d["colour_changes"],
                       None if d["off_grid"] == "-" else d["off_grid"])
    ce = build_ff("centre-v1", cgeo.status, cgeo.mode, cgeo.width, cgeo.height, cgeo.cpp,
                  cal.capture_after_clock, cframes, status=status, centre=centre)
    return {"tb-v1": tb, "centre-v1": ce}


def check_outputs(out, ref, samplers=SAMPLERS):
    """Every file of each sampler equals the reference's, byte for byte, and
    the store holds the distinct frames."""
    for s in samplers:
        want, distinct = ref[s]
        d = out / s
        assert (d / "frames.txt").read_text() == ff.format_frames_txt(want)
        got = ff.read_frames_txt(d / "frames.txt")
        store = read_store(d / store_name(s))
        assert store == b"".join(distinct)
        assert xxhash.xxh3_128_hexdigest(store) == got.store_xxh3_128 and len(store) == got.store_length
        size = got.width * got.height
        assert [hashes.xxh3_128_rgb6(store[i * size:(i + 1) * size]) for i in range(len(distinct))] == got.distinct
        xxh = [hashes.xxh3_128_rgb6(b) for b in distinct]
        assert (d / "frame_hashes.xxh3-128-rgb6.txt").read_text() == \
            ff.format_hash_file(ff.HashFile.from_frames(want, "xxh3-128-rgb6", xxh))
        assert not (d / "frame_hashes.sha256-rgb6.txt").exists()
        fnv_path = d / "frame_hashes.fnv1a64-tb2026-rgb888.txt"
        if s == "tb-v1":
            fnv = [f"{hashes.fnv1a64_tb2026_rgb888(b):016x}" for b in distinct]
            assert fnv_path.read_text() == \
                ff.format_hash_file(ff.HashFile.from_frames(want, "fnv1a64-tb2026-rgb888", fnv))
        else:
            assert not fnv_path.exists()


@pytest.fixture(scope="module")
def changing_trace(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sample")
    uo, rst = changing()
    path = write_trace(tmp, uo, rst)
    return tmp, path, uo, rst, reference(tmp, uo, rst)


def test_sample_both(changing_trace, tmp_path):
    _, trace, _, _, ref = changing_trace
    assert trace_blocks(trace)[0] >= 2
    proc = run_sample(trace, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr
    check_outputs(tmp_path / "out", ref)
    tb, _ = ref["tb-v1"]
    assert tb.sampler_status == "ok" and tb.mode == "640x480@60"
    assert 1 < len(tb.distinct) < len(tb.frames)
    ce, _ = ref["centre-v1"]
    assert ce.sampler_status == "ok" and len(ce.distinct) > 1


def test_sample_one_sampler(changing_trace, tmp_path):
    _, trace, _, _, ref = changing_trace
    for s in SAMPLERS:
        out = tmp_path / s
        proc = run_sample(trace, out, "--sampler", s, samplers=())
        assert proc.returncode == 0, proc.stderr
        check_outputs(out, ref, (s,))
        assert sorted(p.name for p in out.iterdir()) == [s]


def test_sample_default_is_centre_only(changing_trace, tmp_path):
    """Without --sampler only centre-v1 runs, writing what it writes beside
    tb-v1."""
    _, trace, _, _, ref = changing_trace
    out = tmp_path / "out"
    proc = run_sample(trace, out, samplers=())
    assert proc.returncode == 0, proc.stderr
    assert sorted(p.name for p in out.iterdir()) == ["centre-v1"]
    check_outputs(out, ref, ("centre-v1",))
    both = tmp_path / "both"
    assert run_sample(trace, both).returncode == 0
    for f in (out / "centre-v1").iterdir():
        assert f.read_bytes() == (both / "centre-v1" / f.name).read_bytes(), f.name


def test_sample_rejects_board_capture(tmp_path):
    fixture = ROOT / "tests" / "fixtures" / "tiny-logo-capture.vgacap.gz"
    path = tmp_path / "tiny-logo-capture.vgacap"
    path.write_bytes(gzip.decompress(fixture.read_bytes()))
    proc = run_sample(path, tmp_path / "out")
    assert proc.returncode == 2
    assert proc.stderr == f"vgacap-sample: {path} {NOT_SIM}\n"
    assert not (tmp_path / "out").exists()


def test_sample_usage(changing_trace, tmp_path):
    _, trace, _, _, _ = changing_trace
    for args in (["--sampler", "tb-v2"], ["--centre-window", "2"], ["--store-level", "x"],
                 ["--png-fd", "-1"], ["--bogus"]):
        proc = run_sample(trace, tmp_path / "out", *args)
        assert proc.returncode == 2, args
        assert proc.stderr.startswith("vgacap-sample: "), args
    proc = subprocess.run([str(SAMPLE), str(trace), "--out", str(tmp_path / "o")], capture_output=True, text=True)
    assert proc.returncode == 2
    proc = run_sample(tmp_path / "missing.trace.zst", tmp_path / "out")
    assert proc.returncode == 1 and proc.stderr.startswith("vgacap-sample: ")


def read_pipe_records(data: bytes) -> list[tuple[str, int, int, int, bytes]]:
    out, i = [], 0
    while i < len(data):
        nl = data.index(b"\n", i)
        sampler, distinct, w, h = data[i:nl].decode().split(" ")
        w, h = int(w), int(h)
        out.append((sampler, int(distinct), w, h, data[nl + 1:nl + 1 + w * h]))
        assert len(out[-1][4]) == w * h
        i = nl + 1 + w * h
    return out


def test_sample_png_pipe(changing_trace, tmp_path):
    _, trace, _, _, ref = changing_trace
    r, w = os.pipe()
    chunks: list[bytes] = []

    def reader():
        with os.fdopen(r, "rb") as f:
            while True:
                b = f.read(1 << 16)
                if not b:
                    break
                chunks.append(b)

    t = threading.Thread(target=reader)
    t.start()
    try:
        proc = subprocess.Popen([str(SAMPLE), str(trace), "--out", str(tmp_path / "out"), "--name", NAME,
                                 *BOTH, "--png-fd", str(w)], pass_fds=(w,), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    finally:
        os.close(w)
    _, err = proc.communicate()
    t.join()
    assert proc.returncode == 0, err
    recs = read_pipe_records(b"".join(chunks))
    for s in SAMPLERS:
        want, distinct = ref[s]
        mine = [x for x in recs if x[0] == s]
        assert [x[1] for x in mine] == list(range(len(distinct)))
        assert all((x[2], x[3]) == (want.width, want.height) for x in mine)
        assert [x[4] for x in mine] == distinct
    check_outputs(tmp_path / "out", ref)


def seek_table_frames(data: bytes) -> list[int]:
    """The compressed size of each zstd frame, from the seek table."""
    n, desc, magic = struct.unpack("<IBI", data[-9:])
    assert magic == 0x8F92EAB1
    esize = 12 if desc & 0x80 else 8
    entries = data[-9 - n * esize:-9]
    return [struct.unpack_from("<I", entries, i * esize)[0] for i in range(n)]


def test_sample_truncated(changing_trace, tmp_path):
    _, trace, uo, rst, _ = changing_trace
    data = trace.read_bytes()
    sizes = seek_table_frames(data)
    assert len(sizes) >= 2 and sizes[-1] > 200
    cut = tmp_path / "cut.trace.zst"
    cut.write_bytes(data[:sum(sizes[:-1]) + 100])
    blocks, samples = trace_blocks(cut)
    assert blocks == len(sizes) - 1
    clocks = samples // 2           # an odd count ends on a clk-low sample: that clock is incomplete
    ref = reference(tmp_path, uo[:clocks], rst[:clocks], status="truncated")
    assert len(ref["tb-v1"][0].frames) >= 1
    proc = run_sample(cut, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr
    check_outputs(tmp_path / "out", ref)
    for s in SAMPLERS:
        assert ff.read_frames_txt(tmp_path / "out" / s / "frames.txt").status == "truncated"


def test_sample_ends_in_calibration(tmp_path):
    uo, rst = synthtrace.build(M640, 50_000, pixel=diag)
    trace = write_trace(tmp_path, uo, rst, status="sim-timeout")
    proc = run_sample(trace, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr
    ref = reference(tmp_path, uo, rst, status="sim-timeout")
    check_outputs(tmp_path / "out", ref)
    for s in SAMPLERS:
        text = (tmp_path / "out" / s / "frames.txt").read_text()
        assert "\nframes: 0\n" in text and "\ndistinct_frames: 0\n" in text
        assert "\nstatus: sim-timeout\n" in text
        assert "\ncapture_after_clock: 50000\n" in text


def test_sample_no_sync(tmp_path):
    clocks = 2_600_000
    uo = np.full(clocks, 0x88, dtype=np.uint8)
    uo[:R] = 0
    rst = np.ones(clocks, dtype=np.uint8)
    rst[:R] = 0
    trace = write_trace(tmp_path, uo, rst, calib_seconds="0.1")
    proc = run_sample(trace, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr
    for s in SAMPLERS:
        f = ff.read_frames_txt(tmp_path / "out" / s / "frames.txt")
        assert (f.status, f.mode, f.frames, f.distinct) == ("ok", "unknown", [], [])
        assert f.sampler_status == "no-sync"
        assert read_store(tmp_path / "out" / s / store_name(s)) == b""
        h = ff.read_hash_file(tmp_path / "out" / s / "frame_hashes.xxh3-128-rgb6.txt")
        assert h.hashes == [] and h.sampler_status == "no-sync"


def test_sample_centre_window(changing_trace, tmp_path):
    tmp, trace, uo, rst, _ = changing_trace
    proc = run_sample(trace, tmp_path / "out", "--centre-window", "2,1")
    assert proc.returncode == 0, proc.stderr
    text = (tmp_path / "out" / "centre-v1" / "frames.txt").read_text()
    assert "\nwindow_offset: 2 1\n" in text
    ref = reference(tmp_path, uo, rst, dx=2, dy=1)
    check_outputs(tmp_path / "out", ref)
    assert ref["centre-v1"][0].centre.porches == (50, 34)


def test_sample_bad_window(changing_trace, tmp_path):
    """Ruling S: an override that breaks the window's bounds is an error
    (exit 1, naming the override and the bound), after every file is
    written; centre-v1's say bad-window with no frames."""
    _, trace, uo, rst, ref = changing_trace
    proc = run_sample(trace, tmp_path / "out", "--centre-window", "17,0")
    assert proc.returncode == 1
    assert "--centre-window 17,0" in proc.stderr and "past the end of the line" in proc.stderr
    check_outputs(tmp_path / "out", ref, ("tb-v1",))
    bad = reference(tmp_path, uo, rst, dx=17, dy=0)
    assert bad["centre-v1"][0].sampler_status == "bad-window"
    check_outputs(tmp_path / "out", bad, ("centre-v1",))
    proc = run_sample(trace, tmp_path / "out2", "--centre-window", "0,-34")
    assert proc.returncode == 1
    assert "--centre-window 0,-34" in proc.stderr and "inside the vsync pulse" in proc.stderr


def test_sample_one_pass(changing_trace, tmp_path):
    _, trace, _, _, _ = changing_trace
    blocks, _ = trace_blocks(trace)
    assert blocks >= 2
    env = dict(os.environ, VGACAP_SAMPLE_DEBUG="1")
    for i, args in enumerate(([], list(BOTH), ["--sampler", "tb-v1"], ["--sampler", "centre-v1"])):
        proc = run_sample(trace, tmp_path / f"o{i}", *args, samplers=(), env=env)
        assert proc.returncode == 0, proc.stderr
        assert f"trace_passes=1 blocks_decoded={blocks}\n" in proc.stderr
    proc = run_sample(trace, tmp_path / "quiet")
    assert "trace_passes" not in proc.stderr



def test_sample_same_pixels_different_missing(tmp_path):
    """A black design with an 800-clock vsync glitch at line 300 of design
    frame 5: centre-v1 emits two short frames with rows never received
    (missing > 0) and then whole ones, all black. They share one distinct
    frame (pixels are the identity); missing is kept per frame, so the runs
    split on it."""
    g = R + 5 * FRAME + 300 * M640.cpl + 656
    uo, rst = synthtrace.build(M640, R + 8 * FRAME, pixel=None, v_glitches=[(g, 800)])
    trace = write_trace(tmp_path, uo, rst)
    proc = run_sample(trace, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr
    check_outputs(tmp_path / "out", reference(tmp_path, uo, rst))
    f = ff.read_frames_txt(tmp_path / "out" / "centre-v1" / "frames.txt")
    assert len(f.distinct) == 1
    assert [x.distinct for x in f.frames] == [0] * len(f.frames)
    missing = [x.missing for x in f.frames]
    assert missing[0] > 0 and missing[-1] == 0 and len(set(missing)) >= 2
    rs = ff.runs(f.frames)
    assert len(rs) >= 2 and all(r[2] == 0 for r in rs)


@pytest.mark.parametrize("key,value", [("seconds", "1e400"), ("seconds", "-1"),
                                       ("calib_seconds", "-0.5"), ("calib_seconds", "-1e999")])
def test_sample_rejects_bad_seconds(tmp_path, key, value):
    """Negative, or too large for a double (strtod gives inf; the stream
    format already refuses the words inf and nan): exit 1 before sampling."""
    uo, rst = synthtrace.build(M640, 50_000, pixel=diag)
    kw = {"calib_seconds": "1", "seconds": "60", key: value}
    trace = write_trace(tmp_path, uo, rst, **kw)
    proc = run_sample(trace, tmp_path / "out")
    assert proc.returncode == 1
    assert proc.stderr == (f"vgacap-sample: {trace}: META(open) {key} '{value}' "
                           "is not a finite number >= 0\n")
    assert not (tmp_path / "out").exists()


def test_bench_distinct_profile_makes_every_frame_distinct(tmp_path):
    """bench-runs --profile distinct is the benchmark of vgacap-sample's frame
    hash and store: every frame must be a new distinct frame, or the
    repeated-frame short cut skips them and the benchmark times nothing."""
    trace = tmp_path / "distinct.trace.zst"
    clocks = 10 + 24 * FRAME
    subprocess.run([str(BUILD / "bench-runs"), "--clocks", str(clocks), "--profile", "distinct",
                    "--write-trace", str(trace)], check=True, capture_output=True, text=True)
    proc = run_sample(trace, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr
    for s in SAMPLERS:
        f = ff.read_frames_txt(tmp_path / "out" / s / "frames.txt")
        shown = [fr for fr in f.frames if fr.distinct >= 0]
        assert len(shown) >= 12, (s, len(shown))   # calibration takes the first few
        assert len(f.distinct) == len(shown) == len(set(f.distinct)), s
