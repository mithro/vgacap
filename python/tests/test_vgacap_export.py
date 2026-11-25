# SPDX-License-Identifier: Apache-2.0
"""vgacap-export (src/tools/vgacap_export.c): a trace, or a clock range of
it, as VCD or FST, with a .gtkw and a Surfer .sucl beside it.

The VCDs are read back by the small parser below and checked against the
pins vgacap.synthtrace put into the trace; the FSTs are compared with the
VCDs by build/test_export_fst (libfst), and by fst2vcd when it is on PATH.
"""
from __future__ import annotations
import gzip
import os
import pathlib
import re
import shutil
import subprocess

import pytest

from vgacap import synthtrace
from vgacap import framesfile as ff
from vgacap.modes import MODES

from test_vgacap_sample import run_sample, write_trace as write_sample_trace

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
EXPORT = BUILD / "vgacap-export"
RECODE = BUILD / "trace-recode"
DUMP = BUILD / "vgacap-dump"
BENCH = BUILD / "bench-runs"
FSTCHECK = BUILD / "test_export_fst"
VECTOR = ROOT / "tests" / "vectors" / "export_small.vcd"
HZ = 25_175_000
R = synthtrace.RESET_CLOCKS
M640 = MODES["640x480@60"]
FRAME = M640.cpl * M640.lpf
PROJECT = "tt_um_synth_export"
WINDOW = ("--from", "1310720", "--to", "1310740")
ALL_ONES = (1 << 32) - 1

PORTS = [("clk", 1), ("rst_n", 1), ("ena", 1), ("ui_in", 8), ("uio_in", 8), ("uo_out", 8),
         ("uio_out", 8), ("uio_oe", 8)]
DECODED = [("hsync", 1), ("vsync", 1), ("r", 2), ("g", 2), ("b", 2), ("rgb6", 6)]


def time_ps(n: int, spc: int, hz: int) -> int:
    """round(n * 10^12 / (spc * hz)), halves up: include/vgacap/time.h's rule."""
    d = spc * hz
    return (2 * n * 10**12 + d) // (2 * d)


def diag(x, y):
    return (x + 3 * y) & 0x3F


def run_export(trace, out, *args, env=None):
    return subprocess.run([str(EXPORT), str(trace), "--out", str(out), *args],
                          capture_output=True, text=True, env=env)


# ---- a small VCD reader -----------------------------------------------------------

class Vcd:
    """vars: [(full name, width, id)] in declaration order (a range is part of
    the name, "uo_out[7:0]", as GTKWave and FST name it); changes: [(time,
    full name, int value)] in file order; times: the '#' lines in order."""

    def __init__(self, text: str):
        self.text = text
        self.header: list[str] = []
        self.vars: list[tuple[str, int, str]] = []
        self.changes: list[tuple[int, str, int]] = []
        self.times: list[int] = []
        scope: list[str] = []
        ids: dict[str, tuple[str, int]] = {}
        toks = text.split()
        i, defs, t = 0, True, None
        while i < len(toks):
            tok = toks[i]
            if defs:
                if tok == "$scope":
                    scope.append(toks[i + 2]); assert toks[i + 3] == "$end"; i += 4
                elif tok == "$upscope":
                    scope.pop(); assert toks[i + 1] == "$end"; i += 2
                elif tok == "$var":
                    _, width, ident, name = toks[i + 1:i + 5]
                    j = i + 5
                    if toks[j] != "$end":
                        name += toks[j]; j += 1
                    assert toks[j] == "$end"
                    full = ".".join(scope + [name])
                    self.vars.append((full, int(width), ident)); ids[ident] = (full, int(width))
                    i = j + 1
                elif tok == "$enddefinitions":
                    defs = False; i += 2
                else:
                    j = toks.index("$end", i)
                    self.header.append(" ".join(toks[i:j + 1])); i = j + 1
                continue
            if tok in ("$dumpvars", "$end"):
                i += 1
            elif tok.startswith("#"):
                t = int(tok[1:]); self.times.append(t); i += 1
            elif tok.startswith("b"):
                name, width = ids[toks[i + 1]]
                assert len(tok) - 1 == width, (tok, name)
                self.changes.append((t, name, int(tok[1:], 2))); i += 2
            else:
                name, width = ids[tok[1:]]
                assert width == 1
                self.changes.append((t, name, int(tok[0]))); i += 1
        assert not scope

    def names(self) -> list[str]:
        return [v[0] for v in self.vars]

    def history(self, name: str) -> list[tuple[int, int]]:
        return [(t, v) for t, n, v in self.changes if n == name]

    def states(self):
        """{time: {name: value}} after each '#' line's changes."""
        cur: dict[str, int] = {}
        out: dict[int, dict[str, int]] = {}
        for t, n, v in self.changes:
            cur[n] = v
            out[t] = dict(cur)
        for t in self.times:          # a time with no change keeps the last state
            if t not in out:
                out[t] = dict(cur)
        return out


def read_vcd(path) -> Vcd:
    return Vcd(pathlib.Path(path).read_text())


def pins(value: int) -> dict[str, int]:
    """The tt-um-43 ports and the decoded Tiny VGA signals of one sample."""
    uo = value & 0xFF
    col = (((uo >> 0) & 1) << 5 | ((uo >> 4) & 1) << 4 | ((uo >> 1) & 1) << 3 | ((uo >> 5) & 1) << 2
           | ((uo >> 2) & 1) << 1 | ((uo >> 6) & 1))
    return {"clk": (value >> 42) & 1, "rst_n": (value >> 40) & 1, "ena": (value >> 41) & 1,
            "ui_in[7:0]": (value >> 24) & 0xFF, "uio_in[7:0]": (value >> 32) & 0xFF, "uo_out[7:0]": uo,
            "uio_out[7:0]": (value >> 8) & 0xFF, "uio_oe[7:0]": (value >> 16) & 0xFF,
            "decoded.hsync": (uo >> 7) & 1, "decoded.vsync": (uo >> 3) & 1, "decoded.r[1:0]": col >> 4,
            "decoded.g[1:0]": (col >> 2) & 3, "decoded.b[1:0]": col & 3, "decoded.rgb6[5:0]": col}


def check_against_samples(vcd: Vcd, samples, first: int, last: int, scope: str, *, clk=True):
    """Every sample n in [first, last) reads, in the VCD at time_ps(n), as the
    trace's pins; the last '#' line is time_ps(last)."""
    states = vcd.states()
    cur: dict[str, int] = {}
    for n in range(first, last):
        t = time_ps(n, 2, HZ)
        if t in states:
            cur = states[t]
        want = {f"{scope}.{k}": v for k, v in pins(int(samples[n])).items() if clk or k != "clk"}
        got = {k: cur.get(k) for k in want}
        assert got == want, (n, t)
    assert vcd.times[0] == time_ps(first, 2, HZ)
    assert vcd.times[-1] == time_ps(last, 2, HZ)


# ---- the small window -----------------------------------------------------------------

@pytest.fixture(scope="module")
def small(tmp_path_factory):
    """4 frames of 640x480@60, diagonal colours: (plain stream, zstd trace, samples)."""
    tmp = tmp_path_factory.mktemp("export_small")
    uo, rst = synthtrace.build(M640, R + 4 * FRAME, pixel=diag)
    plain = tmp / "small.vgacap"
    synthtrace.write_trace(plain, uo, rst, clock_hz=HZ, project=PROJECT, calib_seconds="1", seconds="1")
    zst = tmp / "small.trace.zst"
    subprocess.run([str(RECODE), str(plain), str(zst)], check=True, capture_output=True, text=True)
    return plain, zst, synthtrace.sample_values(uo, rst)


def test_vector_is_the_trace(small):
    """The hand-written vector, read, holds the trace's pins in the window."""
    _, _, samples = small
    vcd = read_vcd(VECTOR)
    assert vcd.header == ["$version vgacap-export $end", "$timescale 1ps $end"]
    check_against_samples(vcd, samples, 2 * 1310720, 2 * 1310740, PROJECT)


@pytest.mark.parametrize("which", ["plain", "zstd"])
def test_vcd_matches_expected(small, tmp_path, which):
    trace = small[0] if which == "plain" else small[1]
    out = tmp_path / "small.vcd"
    proc = run_export(trace, out, "--format", "vcd", *WINDOW)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert out.read_bytes() == VECTOR.read_bytes()


def test_vcd_whole_trace_reproducible(small, tmp_path):
    """No date: two exports are the same bytes; the whole trace reads back as its samples."""
    _, zst, samples = small
    a, b = tmp_path / "a.vcd", tmp_path / "b.vcd"
    for p in (a, b):
        proc = run_export(zst, p, "--format", "vcd", "--from", "1259000", "--to", "1262000")
        assert proc.returncode == 0, proc.stderr
    assert a.read_bytes() == b.read_bytes()
    check_against_samples(read_vcd(a), samples, 2 * 1259000, 2 * 1262000, PROJECT)


def test_no_clk(small, tmp_path):
    _, zst, samples = small
    out = tmp_path / "noclk.vcd"
    proc = run_export(zst, out, "--format", "vcd", "--no-clk", *WINDOW)
    assert proc.returncode == 0, proc.stderr
    vcd = read_vcd(out)
    assert f"{PROJECT}.clk" not in vcd.names()
    assert vcd.names()[0] == f"{PROJECT}.rst_n"
    check_against_samples(vcd, samples, 2 * 1310720, 2 * 1310740, PROJECT, clk=False)
    # Only the 20 clk-high samples change uo_out here: the dump, those 20, the end.
    assert len(vcd.times) == 22
    gtkw = (tmp_path / "noclk.gtkw").read_text()
    assert ".clk\n" not in gtkw


def test_whole_trace_default_range(tmp_path):
    """No --from/--to: every sample; the scope from a project with a '/'."""
    uo, rst = synthtrace.build(M640, 300, pixel=diag)
    trace = tmp_path / "t.vgacap"
    synthtrace.write_trace(trace, uo, rst, clock_hz=HZ, project="tt07/tt_um_a b", calib_seconds="1", seconds="1")
    out = tmp_path / "t.vcd"
    proc = run_export(trace, out, "--format", "vcd")
    assert proc.returncode == 0, proc.stderr
    vcd = read_vcd(out)
    assert vcd.names()[0] == "tt07_tt_um_a_b.clk"
    check_against_samples(vcd, synthtrace.sample_values(uo, rst), 0, 600, "tt07_tt_um_a_b")
    proc = run_export(trace, out, "--format", "vcd", "--scope", "top")
    assert proc.returncode == 0, proc.stderr
    assert read_vcd(out).names()[-1] == "top.decoded.rgb6[5:0]"


# ---- FST ---------------------------------------------------------------------------------

def test_fst_equals_vcd(small, tmp_path):
    _, zst, _ = small
    for args in (WINDOW, ("--from", "1259000", "--to", "1262000", "--no-clk")):
        fst, vcd = tmp_path / "w.fst", tmp_path / "w.vcd"
        for fmt, p in (("fst", fst), ("vcd", vcd)):
            proc = run_export(zst, p, "--format", fmt, *args)
            assert proc.returncode == 0, proc.stderr
        assert fst.read_bytes()[:1] == b"\x00"          # an FST header block, not text
        proc = subprocess.run([str(FSTCHECK), str(fst), str(vcd)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


def test_fst_equals_vcd_fst2vcd(small, tmp_path):
    """fst2vcd's value changes are the VCD's (only when fst2vcd is installed)."""
    tool = shutil.which("fst2vcd")
    if tool is None:
        pytest.skip("fst2vcd is not on PATH (it comes with GTKWave or oss-cad-suite); "
                    "test_fst_equals_vcd compares the FST through libfst instead")
    _, zst, _ = small
    fst, vcd = tmp_path / "w.fst", tmp_path / "w.vcd"
    for fmt, p in (("fst", fst), ("vcd", vcd)):
        assert run_export(zst, p, "--format", fmt, *WINDOW).returncode == 0
    proc = subprocess.run([tool, str(fst)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    got, want = Vcd(proc.stdout), read_vcd(vcd)
    assert sorted(got.changes) == sorted(want.changes)


# ---- range seeks -------------------------------------------------------------------------

@pytest.fixture(scope="module")
def busy(tmp_path_factory):
    """bench-runs' busy trace of 20e6 clocks (7 blocks): (path, block first samples)."""
    tmp = tmp_path_factory.mktemp("export_busy")
    path = tmp / "busy.trace.zst"
    subprocess.run([str(BENCH), "--clocks", "20000000", "--profile", "busy", "--write-trace", str(path)],
                   check=True, capture_output=True, text=True)
    proc = subprocess.run([str(DUMP), str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    starts = [int(m) for m in re.findall(r"^BLKH first_sample=(\d+)$", proc.stdout, re.M)]
    assert len(starts) >= 6
    return path, starts


def blocks_read(proc) -> int:
    m = re.fullmatch(r"blocks_read=(\d+)\n", proc.stderr)
    assert m, proc.stderr
    return int(m.group(1))


def test_range_seeks(busy, tmp_path):
    path, starts = busy
    env = dict(os.environ, VGACAP_EXPORT_DEBUG="1")
    b3, b4 = (starts[3] + 1) // 2, starts[4] // 2     # first whole clocks of blocks 3 and 4
    out = tmp_path / "r.vcd"
    proc = run_export(path, out, "--format", "vcd", "--from", str(b3 + 1000), "--to", str(b3 + 3000), env=env)
    assert proc.returncode == 0, proc.stderr
    assert blocks_read(proc) == 1
    vcd = read_vcd(out)
    assert vcd.times[0] == time_ps(2 * (b3 + 1000), 2, HZ) and vcd.times[-1] == time_ps(2 * (b3 + 3000), 2, HZ)
    proc = run_export(path, out, "--format", "vcd", "--from", str(b4 - 1000), "--to", str(b4 + 1000), env=env)
    assert proc.returncode == 0, proc.stderr
    assert blocks_read(proc) == 2
    proc = run_export(path, tmp_path / "r.fst", "--format", "fst", "--from", str(b4 - 1000), "--to", str(b4 + 1000),
                      env=env)
    assert proc.returncode == 0, proc.stderr
    assert blocks_read(proc) == 2
    proc = subprocess.run([str(FSTCHECK), str(tmp_path / "r.fst"), str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ---- the frame signal ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def framed(tmp_path_factory):
    """12 black 640x480@60 frames (calibration takes 5), through vgacap-sample (centre-v1, the default):
    (trace, frames.txt, FramesFile)."""
    tmp = tmp_path_factory.mktemp("export_frames")
    uo, rst = synthtrace.build(M640, R + 12 * FRAME)
    trace = write_sample_trace(tmp, uo, rst, name="black")
    proc = run_sample(trace, tmp / "out", samplers=())
    assert proc.returncode == 0, proc.stderr
    frames_txt = tmp / "out" / "centre-v1" / "frames.txt"
    got = ff.read_frames_txt(frames_txt)
    assert got.sampler == "centre-v1" and len(got.frames) >= 6
    return trace, frames_txt, got


def expected_frame(f: ff.FramesFile, first_clock: int) -> list[tuple[int, int]]:
    """(clock, value) changes of `frame` from first_clock on: each frame's number
    from its start clock; -1 before frame 0 and in a gap, which starts
    frame_clocks after the frame before it."""
    fc = f.frame_clocks
    ev: list[tuple[int, int]] = []
    prev_start = None
    for k, fr in enumerate(f.frames):
        if fr.distinct >= 0:
            ev.append((fr.start_clock, k)); prev_start = fr.start_clock
        elif prev_start is not None and fc:
            ev.append((prev_start + fc, ALL_ONES)); prev_start = None
    value = ALL_ONES
    out = []
    for c, v in ev:
        if c <= first_clock:
            value = v
        else:
            out.append((c, v))
    return [(first_clock, value)] + out


def frame_changes(vcd: Vcd, scope: str) -> list[tuple[int, int]]:
    return vcd.history(f"{scope}.decoded.frame[31:0]")


def test_frame_signal(framed, tmp_path):
    trace, frames_txt, f = framed
    out = tmp_path / "f.vcd"
    proc = run_export(trace, out, "--format", "vcd", "--no-clk", "--frames", str(frames_txt))
    assert proc.returncode == 0, proc.stderr
    vcd = read_vcd(out)
    scope = "tt_um_synth_sample"
    assert vcd.header[-1] == "$comment frame: the frame starts of centre-v1's frames.txt $end"
    assert vcd.names()[-1] == f"{scope}.decoded.frame[31:0]"
    want = [(time_ps(2 * c, 2, HZ), v) for c, v in expected_frame(f, 0)]
    assert frame_changes(vcd, scope) == want
    assert [v for _, v in want] == [ALL_ONES] + list(range(len(f.frames)))
    # A range that starts inside frame 1 starts with frame 1.
    c1 = f.frames[1].start_clock
    proc = run_export(trace, out, "--format", "vcd", "--no-clk", "--frames", str(frames_txt),
                      "--from", str(c1 + 5), "--to", str(c1 + 3 * FRAME))
    assert proc.returncode == 0, proc.stderr
    want = [(time_ps(2 * c, 2, HZ), v) for c, v in expected_frame(f, c1 + 5) if c < c1 + 3 * FRAME]
    assert frame_changes(read_vcd(out), scope) == want
    assert want[0][1] == 1


def test_frame_signal_gaps(framed, tmp_path):
    """Frames 0 and 2-3 made gaps: -1 until frame 1, and from frame 1's start +
    frame_clocks until frame 4."""
    trace, _, f = framed
    gapped = ff.FramesFile(**{**f.__dict__, "frames": [ff.Frame(-1) if k in (0, 2, 3) else fr
                                                         for k, fr in enumerate(f.frames)]})
    path = tmp_path / "frames.txt"
    ff.write_frames_txt(path, gapped)
    out = tmp_path / "g.vcd"
    proc = run_export(trace, out, "--format", "vcd", "--no-clk", "--frames", str(path))
    assert proc.returncode == 0, proc.stderr
    got = frame_changes(read_vcd(out), "tt_um_synth_sample")
    fc = gapped.frame_clocks
    assert fc > 0
    s = [fr.start_clock for fr in f.frames]
    head = [(0, ALL_ONES), (s[1], 1), (s[1] + fc, ALL_ONES), (s[4], 4)]
    assert got[:4] == [(time_ps(2 * c, 2, HZ), v) for c, v in head]
    assert got == [(time_ps(2 * c, 2, HZ), v) for c, v in expected_frame(gapped, 0)]


def test_frame_signal_fst(framed, tmp_path):
    trace, frames_txt, f = framed
    c1 = f.frames[1].start_clock
    fst, vcd = tmp_path / "f.fst", tmp_path / "f.vcd"
    for fmt, p in (("fst", fst), ("vcd", vcd)):
        proc = run_export(trace, p, "--format", fmt, "--frames", str(frames_txt), "--from", str(c1 - 1000),
                          "--to", str(c1 + 100000))
        assert proc.returncode == 0, proc.stderr
    proc = subprocess.run([str(FSTCHECK), str(fst), str(vcd)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ---- the viewer files ----------------------------------------------------------------------

def test_gtkw_written(framed, tmp_path):
    trace, frames_txt, _ = framed
    out = tmp_path / "view.vcd"
    proc = run_export(trace, out, "--format", "vcd", "--frames", str(frames_txt), "--from", "1000",
                      "--to", "1100")
    assert proc.returncode == 0, proc.stderr
    s = "tt_um_synth_sample"
    want = (f'[dumpfile] "{out.resolve()}"\n'
            f"[timestart] {time_ps(2000, 2, HZ)}\n"
            "@800200\n-ports\n"
            f"@28\n{s}.clk\n{s}.rst_n\n{s}.ena\n"
            f"@22\n{s}.ui_in[7:0]\n{s}.uio_in[7:0]\n{s}.uo_out[7:0]\n{s}.uio_out[7:0]\n{s}.uio_oe[7:0]\n"
            "@1000200\n-ports\n"
            "@800200\n-decoded\n"
            f"@28\n{s}.decoded.hsync\n{s}.decoded.vsync\n"
            f"@22\n{s}.decoded.r[1:0]\n{s}.decoded.g[1:0]\n{s}.decoded.b[1:0]\n{s}.decoded.rgb6[5:0]\n"
            f"@24\n{s}.decoded.frame[31:0]\n"
            "@1000200\n-decoded\n")
    gtkw = (tmp_path / "view.gtkw").read_text()
    assert gtkw == want
    # Every exported signal once.
    names = read_vcd(out).names()
    assert sorted(line for line in gtkw.splitlines() if line.startswith(s)) == sorted(names)
    sucl = (tmp_path / "view.sucl").read_text()
    assert sucl == (f"load_file {out.resolve()}\n"
                    f"scope_add_as_group {s}\n"
                    f"scope_add_as_group {s}.decoded\n")
    # An FST names its own file; no extension gets one added.
    proc = run_export(trace, tmp_path / "plain", "--format", "fst", "--from", "1000", "--to", "1100")
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "plain.gtkw").read_text().startswith(f'[dumpfile] "{(tmp_path / "plain").resolve()}"\n')
    assert (tmp_path / "plain.sucl").exists()


# ---- board captures --------------------------------------------------------------------------

def test_board_capture_exports(tmp_path):
    """The VGCH v1 fixture (12-bit samples, Tiny VGA on its signal map, one
    sample a clock at 60 kHz): sample[11:0] and the decoded scope, no clk."""
    fixture = ROOT / "tests" / "fixtures" / "tiny-logo-capture.vgacap.gz"
    path = tmp_path / "tiny-logo-capture.vgacap"
    path.write_bytes(gzip.decompress(fixture.read_bytes()))
    out = tmp_path / "board.vcd"
    proc = run_export(path, out, "--format", "vcd", "--to", "20000")
    assert proc.returncode == 0, proc.stderr
    vcd = read_vcd(out)
    assert vcd.names() == ["trace.sample[11:0]", "trace.decoded.hsync", "trace.decoded.vsync",
                           "trace.decoded.r[1:0]", "trace.decoded.g[1:0]", "trace.decoded.b[1:0]",
                           "trace.decoded.rgb6[5:0]"]
    hmap = (11, 3, 0, 8, 1, 9, 2, 10)    # the fixture's signal map: hs, vs, r1, r0, g1, g0, b1, b0
    states = vcd.states()
    for t in sorted(states):
        st = states[t]
        smp = st["trace.sample[11:0]"]
        bit = [(smp >> b) & 1 for b in hmap]
        assert st["trace.decoded.hsync"] == bit[0] and st["trace.decoded.vsync"] == bit[1]
        assert st["trace.decoded.rgb6[5:0]"] == (bit[2] << 5 | bit[3] << 4 | bit[4] << 3 | bit[5] << 2
                                                 | bit[6] << 1 | bit[7])
    assert len({v for _, v in vcd.history("trace.decoded.hsync")}) == 2
    assert vcd.times[0] == 0 and vcd.times[-1] == time_ps(20000, 1, 60000)
    sample_times = {time_ps(n, 1, 60000) for n in range(20000 + 1)}
    assert set(vcd.times) <= sample_times
    proc = run_export(path, tmp_path / "board.fst", "--format", "fst", "--to", "20000")
    assert proc.returncode == 0, proc.stderr
    proc = subprocess.run([str(FSTCHECK), str(tmp_path / "board.fst"), str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ---- usage -----------------------------------------------------------------------------------

def test_usage(small, tmp_path):
    _, zst, _ = small
    out = tmp_path / "u.vcd"
    for args in ([], ["--format", "ghw"], ["--format", "vcd", "--from", "x"],
                 ["--format", "vcd", "--from", "10", "--to", "10"],
                 ["--format", "vcd", "--to", str(R + 4 * FRAME + 1)],
                 ["--format", "vcd", "--scope", "a b"], ["--format", "vcd", "--scope", ""],
                 ["--format", "vcd", "--bogus"]):
        proc = run_export(zst, out, *args)
        assert proc.returncode == 2, (args, proc.stderr)
        assert proc.stderr.startswith("vgacap-export: "), args
        assert not out.exists(), args
    proc = subprocess.run([str(EXPORT), str(zst), "--format", "vcd"], capture_output=True, text=True)
    assert proc.returncode == 2
    proc = run_export(tmp_path / "missing.trace.zst", out, "--format", "vcd")
    assert proc.returncode == 1 and proc.stderr.startswith("vgacap-export: ")
    bad = tmp_path / "bad_frames.txt"
    bad.write_text("sampler: centre-v1\nframe_clocks: 1\nframes: 2\nruns:\n0 1 0 5 0\n")
    proc = run_export(zst, out, "--format", "vcd", "--frames", str(bad), *WINDOW)
    assert proc.returncode == 1
    assert proc.stderr == f"vgacap-export: {bad}: frames.txt: the runs do not cover frames 0 to frames - 1\n"
    # The whole range of a trace is accepted at its end exactly.
    proc = run_export(zst, out, "--format", "vcd", "--no-clk", "--from", str(R + 4 * FRAME - 5),
                      "--to", str(R + 4 * FRAME))
    assert proc.returncode == 0, proc.stderr
