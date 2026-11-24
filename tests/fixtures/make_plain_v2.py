# SPDX-License-Identifier: Apache-2.0
"""Writes tests/fixtures/plain-v2.vgacap: a plain VGCH version 2 stream from
the Python writer (vgacap.stream.Writer), for tests/test_trace_read.c.

    uv run python tests/fixtures/make_plain_v2.py tests/fixtures/plain-v2.vgacap

The samples are those of `trace-make <path> 3000 7` (tests/trace_make.c) and
of make_trace(path, 3000, 7) in tests/test_trace_read.c: 6000 samples of
the tt-um-43 layout, rst_n and ena high, uo_out counting up every 7 clocks,
changing in the clock-high sample. The test reads this file and a trace of
the same samples from the C trace writer and compares every run.
python/tests/test_roundtrip_c.py regenerates it and checks the committed
copy is identical.
"""
import io
import sys

from vgacap.stream import Header, Writer

CLOCKS, EVERY = 3000, 7
CLK42 = 1 << 42
OPEN = "meta=open\nproject=tt_um_test\ncalib_seconds=0.5\nseconds=2\nlayout=tt-um-43\n"
CLOSE = "meta=close\ncapture_after_clock=0\nstatus=ok\nframes=0\n"
PAIRS_PER_CHUNK = 100   # several RUNS chunks, split between pairs


def sample(s: int) -> int:
    uo = ((s + 1) // 2 // EVERY) & 0xFF
    return (3 << 40) | uo | (CLK42 if s & 1 else 0)


def build() -> bytes:
    pairs: list[list[int]] = []
    for s in range(2 * CLOCKS):
        v = sample(s) & ~CLK42
        if pairs and pairs[-1][0] == v:
            pairs[-1][1] += 1
        else:
            pairs.append([v, 1])
    fp = io.BytesIO()
    w = Writer(fp, Header(version=2, sample_bits=43, mode=2, clock_hz=25_175_000, samples_per_word=0,
                          samples_per_clock=2, clock_bit=42, clock_first=0, desc="plain v2 fixture"))
    w.meta(OPEN)
    w.block(0)
    for i in range(0, len(pairs), PAIRS_PER_CHUNK):
        w.runs([(v, r) for v, r in pairs[i:i + PAIRS_PER_CHUNK]])
    w.meta(CLOSE)
    return fp.getvalue()


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <output>")
    with open(sys.argv[1], "wb") as f:
        f.write(build())


if __name__ == "__main__":
    main()
