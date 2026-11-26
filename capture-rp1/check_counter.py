# SPDX-License-Identifier: Apache-2.0
"""Check a `vgacap-rp1 --gen-counter` capture: every sample is one less than
the sample before it, modulo 256.

    python3 capture-rp1/check_counter.py run.vgacap

The counter generator drives a descending 8-bit count onto the data pins, one
value a clock, so a capture that lost or repeated samples breaks the count,
unless the loss is a whole multiple of 256 samples: that this cannot see.
Prints one line and exits 0 when the count is unbroken; otherwise it names
the first break and exits 1. A stream that cannot be read exits 2.
Standard library and vgacap.stream only, so it runs on the Pi as it is.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "python"))
from vgacap.stream import read_stream  # noqa: E402


def samples(data: bytes) -> list[int]:
    _, items = read_stream(data)
    out: list[int] = []
    for item in items:
        if item[0] == "run":
            out.extend([item[1] & 0xFF] * item[2])
    return out


def first_break(values: list[int]) -> int | None:
    """The index of the first sample that is not its predecessor minus 1."""
    for i in range(1, len(values)):
        if values[i] != (values[i - 1] - 1) & 0xFF:
            return i
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stream", type=pathlib.Path, help="the vgacap-rp1 --gen-counter capture")
    a = ap.parse_args(argv)
    try:
        values = samples(a.stream.read_bytes())
    except (OSError, ValueError) as e:
        print(f"check_counter: {a.stream}: {e}", file=sys.stderr)
        return 2
    if len(values) < 2:
        print(f"check_counter: {a.stream}: {len(values)} samples, too few to check", file=sys.stderr)
        return 2
    i = first_break(values)
    if i is not None:
        print(f"broken at sample {i}: {values[i - 1]:#04x} then {values[i]:#04x} ({len(values)} samples)")
        return 1
    print(f"ok: {len(values)} samples, every step -1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
