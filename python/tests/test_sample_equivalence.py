# SPDX-License-Identifier: Apache-2.0
"""vgacap-sample against an earlier build of itself: every output file, the
PNG pipe's bytes, the exit status and stderr, byte for byte, on synthtrace
traces covering each path (Task 16a: the speed work must not change an
output).

The reference binary is VGACAP_SAMPLE_REF, a vgacap-sample built from the
commit to compare with (for Task 16a, 12dfaf7) into a separate build
directory; without it these tests are skipped. The binary under test is
build/vgacap-sample, or VGACAP_SAMPLE_NEW.

    git archive 12dfaf7 | tar -x -C tmp/ref-src
    cmake -S tmp/ref-src -B tmp/ref-build -DCMAKE_BUILD_TYPE=Release
    cmake --build tmp/ref-build --target vgacap-sample
    VGACAP_SAMPLE_REF=tmp/ref-build/vgacap-sample uv run pytest python/tests/test_sample_equivalence.py
"""
from __future__ import annotations
import os
import pathlib
import subprocess

import numpy as np
import pytest

from vgacap import synthtrace
from vgacap.modes import MODES

ROOT = pathlib.Path(__file__).resolve().parents[2]
REF = os.environ.get("VGACAP_SAMPLE_REF")
NEW = pathlib.Path(os.environ.get("VGACAP_SAMPLE_NEW", ROOT / "build" / "vgacap-sample"))
RECODE = ROOT / "build" / "trace-recode"
HZ = 25_175_000
R = synthtrace.RESET_CLOCKS
M640 = MODES["640x480@60"]
FRAME = M640.cpl * M640.lpf

pytestmark = pytest.mark.skipif(not REF, reason="VGACAP_SAMPLE_REF (the reference vgacap-sample) not set")


def diag(x, y):
    return (x + 3 * y) & 0x3F


def boxed(colour):
    def f(x, y):
        c = diag(x, y)
        return np.where((x >= 100) & (x < 300) & (y >= 50) & (y < 200), colour, c)
    return f


def changing(nframes=12, *, phase="testpatterns", cpp=1):
    """Design frame k shows variant (k // 3) % 3: pictures repeat in runs and recur."""
    fc = int(FRAME * cpp)
    clocks = R + nframes * fc
    variants = [synthtrace.build(M640, clocks, phase=phase, cpp=cpp, pixel=p)
                for p in (diag, boxed(0x15), boxed(0x2A))]
    uo = variants[0][0].copy()
    for k in range(nframes):
        s = R + k * fc
        uo[s:s + fc] = variants[(k // 3) % 3][0][s:s + fc]
    return uo, variants[0][1]


def plain(tmp, uo, rst, name, *, status="ok", calib_seconds="1", seconds="60"):
    path = tmp / f"{name}.vgacap"
    synthtrace.write_trace(path, uo, rst, clock_hz=HZ, project="tt_um_equiv", calib_seconds=calib_seconds,
                           seconds=seconds, status=status)
    return path


def zst(tmp, uo, rst, name, **kw):
    out = tmp / f"{name}.trace.zst"
    subprocess.run([str(RECODE), str(plain(tmp, uo, rst, name, **kw)), str(out)], check=True,
                   capture_output=True, text=True)
    return out


def seek_table_frames(data: bytes) -> list[int]:
    import struct
    n, desc, magic = struct.unpack("<IBI", data[-9:])
    assert magic == 0x8F92EAB1
    esize = 12 if desc & 0x80 else 8
    entries = data[-9 - n * esize:-9]
    return [struct.unpack_from("<I", entries, i * esize)[0] for i in range(n)]


@pytest.fixture(scope="module")
def traces(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("equiv")
    t = {}
    uo, rst = changing()
    t["normal"] = zst(tmp, uo, rst, "normal")
    t["normal_plain"] = plain(tmp, uo, rst, "normal_plain")
    data = t["normal"].read_bytes()
    sizes = seek_table_frames(data)
    t["truncated"] = tmp / "cut.trace.zst"
    t["truncated"].write_bytes(data[:sum(sizes[:-1]) + 100])
    # A byte of the second block damaged: zstd fails it (a read error).
    bad = bytearray(data)
    bad[sizes[0] + sizes[1] // 2] ^= 0x55
    t["damaged_block"] = tmp / "damaged.trace.zst"
    t["damaged_block"].write_bytes(bytes(bad))
    # A plain stream read leniently: damage inside a RUNS chunk is skipped
    # with a resynchronisation, and the runs before it are delivered.
    pdata = t["normal_plain"].read_bytes()
    for frac in (0.4, 0.75):
        bad = bytearray(pdata)
        bad[int(len(pdata) * frac)] ^= 0x80
        t[f"damaged_plain_{frac}"] = tmp / f"damaged_plain_{frac}.vgacap"
        t[f"damaged_plain_{frac}"].write_bytes(bytes(bad))
    uo, rst = changing(phase="playground")
    t["playground"] = zst(tmp, uo, rst, "playground")
    uo, rst = changing(9, cpp=2)
    t["cpp2"] = zst(tmp, uo, rst, "cpp2")
    uo, rst = changing(9, cpp=1.90625)
    t["cpp1.90625"] = zst(tmp, uo, rst, "cpp190625")
    uo, rst = changing(8, cpp=0.95)
    t["cpp0.95"] = zst(tmp, uo, rst, "cpp095")
    clocks = 2_600_000
    uo = np.full(clocks, 0x88, dtype=np.uint8)
    uo[:R] = 0
    rst = np.ones(clocks, dtype=np.uint8)
    rst[:R] = 0
    t["no_sync"] = zst(tmp, uo, rst, "nosync", calib_seconds="0.1")
    uo, rst = synthtrace.build(M640, 50_000, pixel=diag)
    t["ends_in_calibration"] = zst(tmp, uo, rst, "incal", status="sim-timeout")
    g = R + 5 * FRAME + 300 * M640.cpl + 656
    uo, rst = synthtrace.build(M640, R + 8 * FRAME, pixel=None, v_glitches=[(g, 800)])
    t["missing"] = zst(tmp, uo, rst, "missing")
    # Short seconds: tb-v1's frame target stops capture mid-trace.
    uo, rst = changing()
    t["short_seconds"] = zst(tmp, uo, rst, "short", seconds="0.05")
    # Glitches on both syncs inside the picture.
    uo, rst = synthtrace.build(M640, R + 10 * FRAME, pixel=diag,
                               h_glitches=[(R + 4 * FRAME + 100 * 800 + 200, 3)],
                               v_glitches=[(R + 6 * FRAME + 200 * 800 + 10, 1)])
    t["glitches"] = zst(tmp, uo, rst, "glitches")
    return t


#: The reference's default ran both samplers; since Ruling W the default is
#: centre-v1 alone, so a case without --sampler asks for both explicitly.
BOTH = ("--sampler", "tb-v1", "--sampler", "centre-v1")


def run(binary, trace, out, args, png):
    if "--sampler" not in args and "default" not in args:
        args = (*BOTH, *args)
    args = tuple(a for a in args if a != "default")
    fd = os.open(png, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        proc = subprocess.run([str(binary), str(trace), "--out", str(out), "--name", "eq", *args,
                               "--png-fd", str(fd)], capture_output=True, text=True, pass_fds=(fd,))
    finally:
        try:
            os.close(fd)
        except OSError:
            pass   # vgacap-sample closes it on success
    return proc


def tree(d: pathlib.Path) -> dict[str, bytes]:
    if not d.exists():
        return {}
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


CASES = [
    ("normal", ()), ("normal_plain", ()), ("truncated", ()), ("damaged_block", ()),
    ("damaged_plain_0.4", ()), ("damaged_plain_0.75", ()),
    ("playground", ()), ("cpp2", ()), ("cpp1.90625", ()), ("cpp0.95", ()), ("no_sync", ()),
    ("ends_in_calibration", ()), ("missing", ()), ("short_seconds", ()), ("glitches", ()),
    ("normal", ("--sampler", "tb-v1")), ("normal", ("--sampler", "centre-v1")),
    ("normal", ("--centre-window", "2,1")), ("normal", ("--centre-window", "17,0")),
    ("cpp1.90625", ("--centre-window", "-1,2")), ("normal", ("--store-level", "1")),
    ("normal", ("default",)), ("cpp1.90625", ("default",)),
]


@pytest.mark.parametrize("name,args", CASES, ids=[f"{n}{''.join(a)}" for n, a in CASES])
def test_same_as_reference(traces, tmp_path, name, args):
    got = {}
    for who, binary in (("ref", REF), ("new", NEW)):
        out = tmp_path / who
        # "default": the new binary's default (centre-v1) against the reference's --sampler centre-v1.
        a = ("--sampler", "centre-v1") if who == "ref" and args == ("default",) else args
        proc = run(binary, traces[name], out, a, tmp_path / f"{who}.png-pipe")
        got[who] = (proc.returncode, proc.stderr.replace(str(out), "OUT"), tree(out),
                    (tmp_path / f"{who}.png-pipe").read_bytes())
    ref, new = got["ref"], got["new"]
    assert new[0] == ref[0]
    assert new[1] == ref[1]
    assert sorted(new[2]) == sorted(ref[2])
    for f in ref[2]:
        assert new[2][f] == ref[2][f], f
    assert new[3] == ref[3]
    if not name.startswith("damaged"):
        assert ref[2], "the reference wrote nothing"
