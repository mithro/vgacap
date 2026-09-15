# SPDX-License-Identifier: Apache-2.0
"""Mirror of the vgacap stream format (see include/vgacap/stream.h)."""
from __future__ import annotations
import struct
from dataclasses import dataclass
from typing import Iterator, Sequence

TINYVGA_MAP = (7, 3, 0, 4, 1, 5, 2, 6)
FLAG_FIRST_SAMPLE_MSB = 1


@dataclass
class Header:
    version: int = 1
    sample_bits: int = 8
    mode: int = 3
    clock_hz: int = 0
    signal_map: tuple = TINYVGA_MAP
    samples_per_word: int = 4
    flags: int = 0
    desc: str = ""

    def mask(self) -> int:
        return (1 << self.sample_bits) - 1


def _slot(h: Header, i: int) -> int:
    s = i % h.samples_per_word
    return h.samples_per_word - 1 - s if h.flags & FLAG_FIRST_SAMPLE_MSB else s


def pack_words(h: Header, samples: Sequence[int]) -> list[int]:
    spw = h.samples_per_word
    words = [0] * ((len(samples) + spw - 1) // spw)
    for i, s in enumerate(samples):
        words[i // spw] |= (s & h.mask()) << (_slot(h, i) * h.sample_bits)
    return words


def unpack_words(h: Header, words: Sequence[int], count: int) -> list[int]:
    return [(words[i // h.samples_per_word] >> (_slot(h, i) * h.sample_bits)) & h.mask() for i in range(count)]


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload


class Writer:
    """Writes a vgacap capture stream to a binary file object."""

    def __init__(self, fp, header: Header) -> None:
        self.fp, self.h = fp, header
        desc = header.desc.encode()
        body = struct.pack(
            "<HBBI8sBBH",
            header.version, header.sample_bits, header.mode, header.clock_hz,
            bytes(header.signal_map), header.samples_per_word, header.flags, len(desc),
        ) + desc
        fp.write(_chunk(b"VGCH", body))

    def _words(self, samples) -> bytes:
        return b"".join(struct.pack("<I", w) for w in pack_words(self.h, samples))

    def raw(self, samples: Sequence[int]) -> None:
        self.fp.write(_chunk(b"RAW ", struct.pack("<I", len(samples)) + self._words(samples)))

    def rle(self, pairs: Sequence[tuple[int, int]]) -> None:
        self.fp.write(_chunk(b"RLE ", struct.pack("<I", len(pairs)) + b"".join(struct.pack("<II", v, r) for v, r in pairs)))

    def frame(self, frame_counter: int, first_line: int, line_count: int, clocks_per_line: int, samples: Sequence[int]) -> None:
        head = struct.pack("<IHHII", frame_counter, first_line, line_count, clocks_per_line, len(samples))
        self.fp.write(_chunk(b"FRAM", head + self._words(samples)))

    def events(self, events: Sequence[tuple[int, int]]) -> None:
        self.fp.write(_chunk(b"EVNT", struct.pack("<I", len(events)) + b"".join(struct.pack("<QI", c, v) for c, v in events)))

    def time(self, host_time_ns: int, clock_hz: int, dropped: int, msg: str) -> None:
        m = msg.encode()[:255]
        self.fp.write(_chunk(b"TIME", struct.pack("<QIIH", host_time_ns, clock_hz, dropped, len(m)) + m))


def read_chunks(data: bytes) -> Iterator[tuple[str, bytes]]:
    pos = 0
    while pos + 8 <= len(data):
        tag, length = data[pos:pos + 4].decode("ascii"), struct.unpack_from("<I", data, pos + 4)[0]
        pos += 8
        if pos + length > len(data):
            raise ValueError(f"truncated chunk {tag!r}")
        yield tag, data[pos:pos + length]
        pos += length


def parse_header(payload: bytes) -> Header:
    version, bits, mode, clock, smap, spw, flags, dl = struct.unpack_from("<HBBI8sBBH", payload, 0)
    if version != 1:
        raise ValueError(f"unsupported version {version}")
    return Header(version, bits, mode, clock, tuple(smap), spw, flags, payload[20:20 + dl].decode())


def read_stream(data: bytes) -> tuple[Header, list]:
    header, items = None, []
    for tag, p in read_chunks(data):
        if tag == "VGCH":
            header = parse_header(p)
            continue
        if header is None:
            raise ValueError("chunk before header")
        if tag in ("RAW ", "FRAM"):
            off = 4 if tag == "RAW " else 16
            if tag == "FRAM":
                fc, fl, lc, cpl, n = struct.unpack_from("<IHHII", p, 0)
                items.append(("frame", fc, fl, lc, cpl, n))
            else:
                n = struct.unpack_from("<I", p, 0)[0]
            words = struct.unpack_from(f"<{(len(p) - off) // 4}I", p, off)
            items.extend(("run", v, 1) for v in unpack_words(header, words, n))
        elif tag == "RLE ":
            n = struct.unpack_from("<I", p, 0)[0]
            items.extend(("run", v, r) for v, r in struct.iter_unpack("<II", p[4:4 + 8 * n]))
        elif tag == "EVNT":
            n = struct.unpack_from("<I", p, 0)[0]
            evs = list(struct.iter_unpack("<QI", p[4:4 + 12 * n]))
            for (c0, v0), (c1, _) in zip(evs, evs[1:]):
                if c1 <= c0:
                    raise ValueError("event clock not increasing")
                items.append(("run", v0, c1 - c0))
            if evs:
                items.append(("run", evs[-1][1], 1))
        elif tag == "TIME":
            t, clk, dropped, ml = struct.unpack_from("<QIIH", p, 0)
            items.append(("time", t, clk, dropped, p[18:18 + ml].decode(errors="replace")))
    if header is None:
        raise ValueError("no header")
    return header, items
