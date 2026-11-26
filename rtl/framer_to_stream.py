# SPDX-License-Identifier: Apache-2.0
"""Put the VGCH header in front of the bytes vgacap_raw_framer sent, so that
every vgacap reader takes them as a stream.

    python3 rtl/framer_to_stream.py framer.bin out.vgacap --clock-hz 25175000

The framer writes only chunks (`RAW ` and overrun `TIME`); the header,
which holds the signal map, the mode and the clock, is the host's to write.
This writes it as the framer's chunks need it: 8-bit samples, four a word,
first sample in the top byte, the Tiny VGA Pmod on uo_out. It checks every
chunk first and prints a summary; it exits 1, writing nothing, if a chunk
is not one the framer sends or is cut short, as a capture stopped
mid-chunk leaves it. Standard library and vgacap.stream only.
"""
from __future__ import annotations

import argparse
import io
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "python"))
from vgacap.stream import FLAG_FIRST_SAMPLE_MSB, TINYVGA_MAP, Header, Writer, read_chunks  # noqa: E402


def summary(chunks: bytes) -> tuple[int, int, int]:
    """(RAW chunks, samples in them, the last TIME chunk's dropped total)."""
    raws = samples = dropped = 0
    for tag, payload in read_chunks(chunks):
        if tag == "RAW ":
            raws += 1
            samples += struct.unpack_from("<I", payload, 0)[0]
        elif tag == "TIME":
            dropped = struct.unpack_from("<I", payload, 12)[0]
        else:
            raise ValueError(f"chunk {tag!r} is not one the raw framer sends")
    return raws, samples, dropped


def header_bytes(clock_hz: int, desc: str) -> bytes:
    fp = io.BytesIO()
    Writer(fp, Header(sample_bits=8, samples_per_word=4, flags=FLAG_FIRST_SAMPLE_MSB,
                      signal_map=TINYVGA_MAP, mode=0, clock_hz=clock_hz, desc=desc))
    return fp.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input", type=pathlib.Path, help="the bytes the framer sent, as received")
    ap.add_argument("output", type=pathlib.Path, help="the vgacap stream to write")
    ap.add_argument("--clock-hz", type=int, default=0,
                    help="the rate samples went into the framer, in hertz (default 0: unknown)")
    ap.add_argument("--desc", default="vgacap_raw_framer", help="the header's description")
    a = ap.parse_args(argv)
    try:
        data = a.input.read_bytes()
        raws, samples, dropped = summary(data)
    except (OSError, ValueError) as e:
        print(f"framer_to_stream: {a.input}: {e}", file=sys.stderr)
        return 1
    a.output.write_bytes(header_bytes(a.clock_hz, a.desc) + data)
    print(f"wrote {a.output}: {raws} RAW chunks, {samples} samples, {dropped} dropped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
