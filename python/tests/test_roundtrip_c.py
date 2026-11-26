# SPDX-License-Identifier: Apache-2.0
import io, subprocess, pathlib, random
from vgacap.stream import Header, Writer

DUMP = pathlib.Path(__file__).resolve().parents[2] / "build" / "vgacap-dump"

def fnv1a(samples):
    h = 0x811C9DC5
    for s in samples:
        for b in s.to_bytes(4, "little"):
            h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h

def test_c_dump_agrees_with_python(tmp_path):
    rng = random.Random(1)
    samples = [rng.randrange(4096) for _ in range(1001)]
    fp = io.BytesIO(); w = Writer(fp, Header(sample_bits=12, samples_per_word=2, flags=1, clock_hz=25000000, desc="py"))
    w.raw(samples); w.rle([(0x800, 100)]); w.events([(0, 0x1), (5, 0x2)])
    f = tmp_path / "s.vgacap"; f.write_bytes(fp.getvalue())
    out = subprocess.run([str(DUMP), str(f)], capture_output=True, text=True, check=True).stdout
    expanded = samples + [0x800] * 100 + [0x1] * 5 + [0x2]
    assert f"total_samples={len(expanded)} crc32={fnv1a(expanded):08x}" in out
    assert "VGCH bits=12 spw=2 flags=1 clock=25000000" in out
    # A version 1 stream keeps its 32-bit checksum line; crc64 is v2's.
    assert "crc64=" not in out and "VGCH v2" not in out


# --- simulation traces (vgacap-dump built with the host extras) ---------------

TRACE_MAKE = DUMP.parent / "trace-make"   # tests/trace_make.c
CLK42 = 1 << 42


def trace_make_sample(s: int, every: int) -> int:
    """Sample s of `trace-make <path> <clocks> <every>` (tests/trace_make.c)."""
    uo = ((s + 1) // 2 // every) & 0xFF if every else 0
    return (3 << 40) | uo | (CLK42 if s & 1 else 0)


def test_c_dump_reads_a_trace(tmp_path):
    clocks, every = 3000, 7
    samples = [trace_make_sample(s, every) for s in range(2 * clocks)]
    h64 = 0xCBF29CE484222325
    for v in samples:
        for b in v.to_bytes(6, "little"):
            h64 = ((h64 ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    want = f"total_samples={2 * clocks} crc64={h64:016x}"

    trace = tmp_path / "t.trace.zst"
    subprocess.run([str(TRACE_MAKE), str(trace), str(clocks), str(every)], check=True)
    assert trace.read_bytes()[:4] == b"\x28\xb5\x2f\xfd"
    out = subprocess.run([str(DUMP), str(trace)], capture_output=True, text=True, check=True).stdout
    assert want in out
    assert f"trace blocks=1 samples={2 * clocks} table_rebuilt=0 closed=1" in out
    assert "VGCH v2 bits=43 spc=2 clock_bit=42" in out
    assert "BLKH first_sample=0" in out and "META len=" in out and "RUNS pairs=" in out

    # The same samples as a plain v2 stream from the Python writer dump the same.
    pairs = []
    for v in samples:
        v &= ~CLK42
        if pairs and pairs[-1][0] == v:
            pairs[-1][1] += 1
        else:
            pairs.append([v, 1])
    fp = io.BytesIO()
    w = Writer(fp, Header(version=2, sample_bits=43, mode=2, clock_hz=25_175_000, samples_per_word=0,
                          samples_per_clock=2, clock_bit=42, clock_first=0))
    w.meta("meta=open\nproject=tt_um_test\ncalib_seconds=0.5\nseconds=2\nlayout=tt-um-43\n")
    w.block(0)
    w.runs([tuple(p) for p in pairs])
    w.meta("meta=close\ncapture_after_clock=0\nstatus=ok\nframes=0\n")
    plain = tmp_path / "t.vgacap"
    plain.write_bytes(fp.getvalue())
    out = subprocess.run([str(DUMP), str(plain)], capture_output=True, text=True, check=True).stdout
    assert want in out
    assert "trace blocks=" not in out


def test_plain_v2_fixture_is_current(tmp_path):
    """tests/fixtures/plain-v2.vgacap (read by tests/test_trace_read.c) is what
    its generator writes now, so the two cannot drift apart."""
    import sys
    fixtures = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures"
    out = tmp_path / "plain-v2.vgacap"
    subprocess.run([sys.executable, str(fixtures / "make_plain_v2.py"), str(out)], check=True)
    assert out.read_bytes() == (fixtures / "plain-v2.vgacap").read_bytes()
    # And it holds the samples trace-make writes for 3000 clocks, every 7.
    dump = subprocess.run([str(DUMP), str(out)], capture_output=True, text=True, check=True).stdout
    samples = [trace_make_sample(s, 7) for s in range(6000)]
    h64 = 0xCBF29CE484222325
    for v in samples:
        for b in v.to_bytes(6, "little"):
            h64 = ((h64 ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    assert f"total_samples=6000 crc64={h64:016x}" in dump
