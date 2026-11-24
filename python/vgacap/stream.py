# SPDX-License-Identifier: Apache-2.0
"""Mirror of the vgacap stream format (see include/vgacap/stream.h).

Version 2 (wide samples, RUNS, BLKH, META) follows tt-vga-capture
docs/design/2026-11-25-format-v2-runs.md. Where the C reader skips a
malformed chunk and reports it, this mirror raises ValueError carrying the
same `what` string.
"""
from __future__ import annotations
import re
import struct
from dataclasses import dataclass
from typing import Iterator, Sequence

TINYVGA_MAP = (7, 3, 0, 4, 1, 5, 2, 6)
FLAG_FIRST_SAMPLE_MSB = 1

#: Largest payload a chunk may declare (mirrors VGACAP_MAX_CHUNK_LEN).
MAX_CHUNK_LEN = 16 * 1024 * 1024

#: Smallest payload each known tag can have: its fixed fields.
MIN_CHUNK_LEN = {"VGCH": 20, "RAW ": 4, "RLE ": 4, "FRAM": 16, "EVNT": 4, "TIME": 18}

#: Version 2 constants (mirror stream.h).
META_MAX = 4096
CLOCK_BIT_NONE = 0xFF
DESC_MAX_V2 = 251
V2_ONLY = ("RUNS", "BLKH", "META")


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
    samples_per_clock: int = 1
    clock_bit: int = CLOCK_BIT_NONE
    clock_first: int = 0

    def mask(self) -> int:
        return (1 << self.sample_bits) - 1

    @property
    def vbytes(self) -> int:
        return (self.sample_bits + 7) // 8

    @property
    def has_clock(self) -> bool:
        return self.clock_bit != CLOCK_BIT_NONE


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


def leb128_encode(v: int) -> bytes:
    if not 0 <= v < 1 << 64:
        raise ValueError(f"LEB128 value {v} out of u64 range")
    out = bytearray()
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def leb128_decode(buf: bytes, pos: int) -> tuple[int, int]:
    """Decode a RUNS run at `pos`: at most 10 bytes, the 10th at most 0x01,
    minimally encoded. Returns (value, position after it)."""
    v = 0
    for k in range(10):
        if pos + k >= len(buf):
            raise ValueError("RUNS pairs do not fill the chunk")
        b = buf[pos + k]
        if k == 9 and b > 1:
            raise ValueError("RUNS run over 64 bits")
        v |= (b & 0x7F) << (7 * k)
        if not b & 0x80:
            if b == 0 and k > 0:
                raise ValueError("RUNS run not minimally encoded")
            return v, pos + k + 1
    raise AssertionError("unreachable: the 10th byte is at most 0x01")  # pragma: no cover


# --- META text -------------------------------------------------------------

_KEY = re.compile(r"[a-z][a-z0-9_]*\Z")
_UINT = re.compile(r"[0-9]+\Z")
#: A decimal number as C strtod reads it in the C locale, the whole value.
_DECIMAL = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?\Z")
STATUS_VALUES = ("ok", "sim-timeout", "unstable-sync", "no-sync", "bad-timing")
LAYOUT_VALUES = ("tt-um-43",)
META_REQUIRED = {"open": ("project", "calib_seconds", "seconds", "layout"),
                 "close": ("capture_after_clock", "status", "frames")}


def _meta_value_ok(key: str, value: str) -> bool:
    if key in ("capture_after_clock", "frames"):
        return bool(_UINT.match(value)) and int(value) < 1 << 64
    if key in ("calib_seconds", "seconds"):
        return bool(_DECIMAL.match(value))
    if key == "status":
        return value in STATUS_VALUES
    if key == "layout":
        return value in LAYOUT_VALUES
    return True  # project, override, simulator, harness: free text; unknown keys ignored


def check_meta_text(raw: bytes) -> str:
    """Return the META text, or raise ValueError("bad META text")."""
    try:
        text = raw.decode("utf-8")  # strict: rejects overlongs and surrogates
    except UnicodeDecodeError:
        raise ValueError("bad META text") from None
    if not text.endswith("\n"):
        raise ValueError("bad META text")
    seen: set[str] = set()
    kind = None
    for i, line in enumerate(text[:-1].split("\n")):
        key, eq, value = line.partition("=")
        if not eq or not _KEY.match(key) or key in seen or not _meta_value_ok(key, value):
            raise ValueError("bad META text")
        if i == 0:
            if key != "meta" or value not in META_REQUIRED:
                raise ValueError("bad META text")
            kind = value
        seen.add(key)
    if not set(META_REQUIRED[kind]) <= seen:
        raise ValueError("bad META text")
    return text


class Writer:
    """Writes a vgacap capture stream to a binary file object."""

    def __init__(self, fp, header: Header) -> None:
        self.fp, self.h = fp, header
        desc = header.desc.encode()
        if header.version == 2 and len(desc) > DESC_MAX_V2:
            raise ValueError(f"a version 2 desc is at most {DESC_MAX_V2} bytes")
        body = struct.pack(
            "<HBBI8sBBH",
            header.version, header.sample_bits, header.mode, header.clock_hz,
            bytes(header.signal_map), header.samples_per_word, header.flags, len(desc),
        )
        if header.version == 2:
            body += bytes([header.samples_per_clock, header.clock_bit, header.clock_first, 0])
        fp.write(_chunk(b"VGCH", body + desc))

    def _need_v2(self, tag: str) -> None:
        if self.h.version != 2:
            raise ValueError(f"{tag!r} needs a version 2 header")

    def runs(self, pairs: Sequence[tuple[int, int]]) -> None:
        """A RUNS chunk: (value, run) pairs, the clock bit stored as 0."""
        self._need_v2("RUNS")
        h = self.h
        if not pairs:
            raise ValueError("RUNS has no pairs")
        bad = ~h.mask() | ((1 << h.clock_bit) if h.has_clock else 0)
        body = bytearray(struct.pack("<I", len(pairs)))
        prev = None
        for value, run in pairs:
            if run < 1:
                raise ValueError("RUNS run is zero")
            if value < 0 or value & bad:
                raise ValueError(f"RUNS value {value:#x} has the clock bit or bits above sample_bits")
            if value == prev:
                raise ValueError("RUNS adjacent pairs have equal values")
            prev = value
            body += value.to_bytes(h.vbytes, "little") + leb128_encode(run)
        if len(body) > MAX_CHUNK_LEN:
            raise ValueError("RUNS chunk over MAX_CHUNK_LEN")
        self.fp.write(_chunk(b"RUNS", bytes(body)))

    def block(self, first_sample: int) -> None:
        self._need_v2("BLKH")
        self.fp.write(_chunk(b"BLKH", struct.pack("<Q", first_sample)))

    def meta(self, text: str) -> None:
        self._need_v2("META")
        raw = text.encode()
        if not 1 <= len(raw) <= META_MAX:
            raise ValueError("bad META length")
        self.fp.write(_chunk(b"META", raw))

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
        """Write a `TIME` chunk.

        `dropped` is the CUMULATIVE dropped-sample count for the stream so
        far, not an increment since the previous `TIME` chunk: the values a
        stream carries are monotonic, and a reader takes the last one rather
        than summing them. (Increments would stop meaning anything the
        moment a stream was truncated or two were spliced.) The C header
        does not say so -- this is the convention every writer here follows,
        and `ttcap.capture.run_capture()` reads it back that way.
        """
        m = msg.encode()[:255]
        self.fp.write(_chunk(b"TIME", struct.pack("<QIIH", host_time_ns, clock_hz, dropped, len(m)) + m))


def _too_short(tag: str, length: int, pos: int | None = None) -> ValueError:
    # The C reader's string ("bad RAW length", ...), then the detail.
    where = "" if pos is None else f" at offset {pos}"
    return ValueError(f"bad {tag.strip()} length: chunk {tag!r}{where} is too short ({length} bytes)")


def read_chunks(data: bytes, check_min: bool = True) -> Iterator[tuple[str, bytes]]:
    """Walk the chunk framing, rejecting anything implausible.

    The C reader resynchronises on a framing error; this mirror does not need
    to (it reads whole files, not a serial link), so it raises instead. What
    both must agree on is *which* streams are well formed. `check_min=False`
    leaves the per-tag minimum length to the caller (read_stream checks it
    after the version 2 chunk bans, in the C reader's order).
    """
    pos = 0
    while pos + 8 <= len(data):
        raw_tag = data[pos:pos + 4]
        if any(b < 0x20 or b > 0x7E for b in raw_tag):
            raise ValueError(f"non-printable chunk tag {raw_tag!r} at offset {pos}")
        tag = raw_tag.decode("ascii")
        length = struct.unpack_from("<I", data, pos + 4)[0]
        if length > MAX_CHUNK_LEN:
            raise ValueError(f"chunk {tag!r} at offset {pos} declares {length} bytes (max {MAX_CHUNK_LEN})")
        if check_min and length < MIN_CHUNK_LEN.get(tag, 0):
            raise _too_short(tag, length, pos)
        pos += 8
        if pos + length > len(data):
            raise ValueError(f"truncated chunk {tag!r}")
        yield tag, data[pos:pos + length]
        pos += length


def parse_header(payload: bytes) -> Header:
    if len(payload) > 20 + 255:
        raise ValueError("bad VGCH length")
    version, bits, mode, clock, smap, spw, flags, dl = struct.unpack_from("<HBBI8sBBH", payload, 0)
    if version not in (1, 2):
        raise ValueError(f"unsupported version {version}")
    fixed = 24 if version == 2 else 20
    if len(payload) < fixed:
        raise ValueError("bad VGCH length")
    if dl != len(payload) - fixed:
        raise ValueError(f"VGCH desc length mismatch: desc_len {dl}, payload has {len(payload) - fixed} bytes")
    spw_ok = spw in (1, 2, 4, 8) and spw * bits <= 32
    if version == 1:
        if bits not in (8, 12, 16, 32):
            raise ValueError(f"bad sample_bits {bits}")
        if not spw_ok:
            raise ValueError(f"bad samples_per_word {spw} for sample_bits {bits}")
        return Header(version, bits, mode, clock, tuple(smap), spw, flags, payload[20:20 + dl].decode())
    if not 1 <= bits <= 64:
        raise ValueError(f"bad sample_bits {bits}")
    if (spw != 0) if bits > 32 else not spw_ok:
        raise ValueError(f"bad samples_per_word {spw} for sample_bits {bits}")
    spc, cb, cf, rsv = payload[20:24]
    clock = cb != CLOCK_BIT_NONE
    if (spc not in (1, 2) or (clock and cb >= bits) or clock != (spc == 2) or cf > 1
            or (not clock and cf) or rsv):
        raise ValueError(f"bad VGCH v2 clock fields: samples_per_clock {spc}, clock_bit {cb}, "
                         f"clock_first {cf}, reserved {rsv}")
    return Header(version, bits, mode, clock, tuple(smap), spw, flags, payload[24:24 + dl].decode(),
                  spc, cb, cf)


def _check_count(tag: str, name: str, declared: int, expected: int) -> None:
    if declared != expected:
        raise ValueError(f"{tag!r} declares {name}={declared} but its length holds {expected}")


def _forbidden(h: Header, tag: str) -> str | None:
    """The `what` for a chunk type the header forbids, or None."""
    if h.version != 2:
        return "chunk needs a version 2 header" if tag in V2_ONLY else None
    if tag == "RLE ":
        return "RLE  chunk not allowed in version 2"
    if h.has_clock:
        return None if tag in V2_ONLY or tag == "TIME" else "chunk not allowed with a sample clock"
    if h.sample_bits > 32 and tag in ("RAW ", "FRAM", "EVNT"):
        return "chunk not allowed with sample_bits over 32"
    return None


def _runs_pairs(h: Header, p: bytes) -> list[tuple[int, int]]:
    """A RUNS payload's (stored value, run) pairs, checked as the note says."""
    if len(p) < 4:
        raise ValueError("RUNS pairs do not fill the chunk")
    n = struct.unpack_from("<I", p, 0)[0]
    if n == 0:
        raise ValueError("RUNS has no pairs")
    vb, pos, prev, pairs = h.vbytes, 4, None, []
    # As C: a count the length cannot hold is refused before any pair.
    if len(p) - 4 < n * (vb + 1):
        raise ValueError("RUNS pairs do not fill the chunk")
    for _ in range(n):
        if pos + vb > len(p):
            raise ValueError("RUNS pairs do not fill the chunk")
        value = int.from_bytes(p[pos:pos + vb], "little")
        if h.has_clock and value >> h.clock_bit & 1:
            raise ValueError("RUNS value has the clock bit set")
        if value & ~h.mask():
            raise ValueError("RUNS value has bits above sample_bits")
        if value == prev:
            raise ValueError("RUNS adjacent pairs have equal values")
        run, pos = leb128_decode(p, pos + vb)
        if run == 0:
            raise ValueError("RUNS run is zero")
        pairs.append((value, run))
        prev = value
    if pos != len(p):
        raise ValueError("RUNS pairs do not fill the chunk")
    return pairs


def read_stream(data: bytes) -> tuple[Header, list]:
    """Decode a whole stream into items: ("run", value, run) -- for a
    version 2 stream with a clock bit, value is the run's first sample with
    its clock bit regenerated -- ("frame", ...), ("time", ...),
    ("block", first_sample) and ("meta", text)."""
    header, items = None, []
    n = 0  # the sample index of the next sample
    def runs_out(new):
        nonlocal n
        for v, r in new:
            if header.has_clock:
                v |= (header.clock_first ^ (n & 1)) << header.clock_bit
            items.append(("run", v, r))
            n += r
    for tag, p in read_chunks(data, check_min=False):
        # The C reader's order: VGCH length, chunk before header, the v2
        # bans, then each allowed tag's minimum length.
        if tag == "VGCH":
            if len(p) < MIN_CHUNK_LEN["VGCH"]:
                raise ValueError("bad VGCH length")
            header, n = parse_header(p), 0
            continue
        if header is None:
            raise ValueError("chunk before header")
        what = _forbidden(header, tag)
        if what:
            raise ValueError(what)
        if len(p) < MIN_CHUNK_LEN.get(tag, 0):
            raise _too_short(tag, len(p))
        if tag == "RUNS":
            runs_out(_runs_pairs(header, p))
        elif tag == "BLKH":
            if len(p) != 8:
                raise ValueError("bad BLKH length")
            first = struct.unpack("<Q", p)[0]
            if first != n:
                raise ValueError(f"BLKH first_sample does not follow: {first}, expected {n}")
            items.append(("block", first))
        elif tag == "META":
            if not 1 <= len(p) <= META_MAX:
                raise ValueError("bad META length")
            items.append(("meta", check_meta_text(p)))
        elif tag in ("RAW ", "FRAM"):
            off = 4 if tag == "RAW " else 16
            if tag == "FRAM":
                fc, fl, lc, cpl, count = struct.unpack_from("<IHHII", p, 0)
            else:
                count = struct.unpack_from("<I", p, 0)[0]
            body = len(p) - off
            spw = header.samples_per_word
            if body % 4 or -(-count // spw) != body // 4:
                raise ValueError(f"{tag!r} declares {count} samples but its length holds {body // 4} words")
            if tag == "FRAM":
                items.append(("frame", fc, fl, lc, cpl, count))
            words = struct.unpack_from(f"<{body // 4}I", p, off)
            runs_out((v, 1) for v in unpack_words(header, words, count))
        elif tag == "RLE ":
            count = struct.unpack_from("<I", p, 0)[0]
            if (len(p) - 4) % 8:
                raise ValueError("'RLE ' payload is not a whole number of pairs")
            _check_count(tag, "pair_count", count, (len(p) - 4) // 8)
            runs_out(struct.iter_unpack("<II", p[4:4 + 8 * count]))
        elif tag == "EVNT":
            count = struct.unpack_from("<I", p, 0)[0]
            if (len(p) - 4) % 12:
                raise ValueError("'EVNT' payload is not a whole number of events")
            _check_count(tag, "event_count", count, (len(p) - 4) // 12)
            evs = list(struct.iter_unpack("<QI", p[4:4 + 12 * count]))
            for (c0, v0), (c1, _) in zip(evs, evs[1:]):
                if c1 <= c0:
                    raise ValueError("event clock not increasing")
                runs_out([(v0, c1 - c0)])
            if evs:
                runs_out([(evs[-1][1], 1)])
        elif tag == "TIME":
            t, clk, dropped, ml = struct.unpack_from("<QIIH", p, 0)
            if ml > 255:
                raise ValueError(f"'TIME' msg_len {ml} exceeds 255")
            # Without this, the message would be read from bytes the chunk
            # never carried (the C reader had the same hole).
            _check_count(tag, "msg_len", ml, len(p) - 18)
            items.append(("time", t, clk, dropped, p[18:18 + ml].decode(errors="replace")))
    if header is None:
        raise ValueError("no header")
    return header, items
