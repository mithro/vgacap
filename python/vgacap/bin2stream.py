# SPDX-License-Identifier: Apache-2.0
"""Wrap a raw per-clock sample dump (one sample per byte or per 32-bit word)
into a vgacap stream.

    uv run vgacap-bin2stream dump.bin out.vgacap --desc "iverilog tt_um_vga_pattern"

By default the input is one byte per clock with the Tiny VGA signal map and
is written as RAW chunks with sample_bits=8, samples_per_word=4.
"""
from __future__ import annotations

import argparse
import pathlib

from .stream import TINYVGA_MAP, Header, Writer

CHUNK_SAMPLES = 65536


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input", type=pathlib.Path)
    ap.add_argument("output", type=pathlib.Path)
    ap.add_argument("--desc", default="")
    ap.add_argument("--clock-hz", type=int, default=0)
    ap.add_argument("--mode", type=int, default=3, help="0 extclk, 1 selfclk, 2 event, 3 unknown")
    a = ap.parse_args(argv)

    data = a.input.read_bytes()
    header = Header(sample_bits=8, samples_per_word=4, flags=0, signal_map=TINYVGA_MAP,
                    mode=a.mode, clock_hz=a.clock_hz, desc=a.desc)
    with open(a.output, "wb") as fp:
        w = Writer(fp, header)
        for start in range(0, len(data), CHUNK_SAMPLES):
            w.raw(list(data[start:start + CHUNK_SAMPLES]))
    print(f"wrote {a.output}: {len(data)} samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
