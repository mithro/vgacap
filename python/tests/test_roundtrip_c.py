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
