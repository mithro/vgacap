# SPDX-License-Identifier: Apache-2.0
"""VGCH format v2 in the Python mirror: the 24-byte header, RUNS, BLKH and
META, per tt-vga-capture docs/design/2026-11-25-format-v2-runs.md."""
import io
import pathlib
import struct
import subprocess

import pytest

from vgacap.stream import (Header, Writer, leb128_decode, leb128_encode, parse_header, read_chunks,
                           read_stream, TINYVGA_MAP)

DUMP = pathlib.Path(__file__).resolve().parents[2] / "build" / "vgacap-dump"
CLK = 1 << 42

LEB = [
    (1, "01"), (127, "7f"), (128, "8001"), (300, "ac02"),
    (2 ** 32, "8080808010"), (2 ** 64 - 1, "ffffffffffffffffff01"),
]


def trace_header(**kw) -> Header:
    args = dict(version=2, sample_bits=43, mode=2, clock_hz=25_175_000, samples_per_word=0,
                samples_per_clock=2, clock_bit=42, clock_first=0)
    args.update(kw)
    return Header(**args)


def plain_v2(bits=43, **kw) -> Header:
    return Header(version=2, sample_bits=bits, samples_per_word=0 if bits > 32 else 1, **kw)


def stream(h: Header, fn=None) -> bytes:
    fp = io.BytesIO()
    w = Writer(fp, h)
    if fn:
        fn(w, fp)
    return fp.getvalue()


def chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload


def vgch_v2(bits=43, spw=0, spc=2, cb=42, cf=0, rsv=0, desc=b"", length=None) -> bytes:
    body = struct.pack("<HBBI8sBBH", 2, bits, 2, 25_175_000, bytes(TINYVGA_MAP), spw, 0, len(desc))
    body += bytes([spc, cb, cf, rsv]) + desc
    return b"VGCH" + struct.pack("<I", len(body) if length is None else length) + body


def runs_chunk(pairs, vbytes=6, count=None) -> bytes:
    body = struct.pack("<I", len(pairs) if count is None else count)
    for value, run in pairs:
        body += value.to_bytes(vbytes, "little") + (run if isinstance(run, bytes) else leb128_encode(run))
    return chunk(b"RUNS", body)


OPEN = "meta=open\nproject=tt_um_x\ncalib_seconds=0.5\nseconds=2\nlayout=tt-um-43\n"
CLOSE = "meta=close\ncapture_after_clock=99\nstatus=ok\nframes=3\n"


# --- LEB128 -------------------------------------------------------------------

@pytest.mark.parametrize("value,hexbytes", LEB)
def test_leb128_vectors(value, hexbytes):
    assert leb128_encode(value) == bytes.fromhex(hexbytes)
    assert leb128_decode(b"\xaa" + bytes.fromhex(hexbytes) + b"\xbb", 1) == (value, 1 + len(hexbytes) // 2)


@pytest.mark.parametrize("hexbytes,what", [
    ("818000", "RUNS run not minimally encoded"),
    ("8000", "RUNS run not minimally encoded"),
    ("ffffffffffffffffff02", "RUNS run over 64 bits"),
    ("ffffffffffffffffff8101", "RUNS run over 64 bits"),
    ("80", "RUNS pairs do not fill the chunk"),
])
def test_leb128_decode_rejects(hexbytes, what):
    with pytest.raises(ValueError, match=what):
        leb128_decode(bytes.fromhex(hexbytes), 0)


# --- a Python-written v2 stream ------------------------------------------------

def test_worked_example_bytes():
    want = bytes.fromhex(
        "5647434818000000" "02002b02d8238001" "0703000401050206" "00000000" "022a0000"
        "52554e5313000000" "02000000" "000000000003ad02" "80000000000301")
    data = stream(trace_header(), lambda w, fp: w.runs([(0x030000000000, 301), (0x030000000080, 1)]))
    assert data == want
    h, items = read_stream(data)
    assert items == [("run", 0x030000000000, 301), ("run", 0x070000000080, 1)]


def test_v2_stream_round_trip():
    def body(w, fp):
        w.meta(OPEN)
        w.block(0)
        w.runs([(0x1_0000_0001, 1), (0x0FF, 2 ** 32 + 5), (0x3FF_FFFF_FFFF & ~CLK, 3)])
        w.block(2 ** 32 + 9)
        w.runs([(7, 2)])
        w.time(5, 0, 0, "t")
        w.meta(CLOSE)
    h, items = read_stream(stream(trace_header(desc="t"), body))
    assert (h.version, h.sample_bits, h.samples_per_word, h.samples_per_clock, h.clock_bit,
            h.clock_first, h.desc) == (2, 43, 0, 2, 42, 0, "t")
    assert items == [
        ("meta", OPEN), ("block", 0),
        ("run", 0x1_0000_0001, 1), ("run", 0x0FF | CLK, 2 ** 32 + 5), ("run", 0x3FF_FFFF_FFFF & ~CLK, 3),
        ("block", 2 ** 32 + 9), ("run", 7 | CLK, 2),
        ("time", 5, 0, 0, "t"), ("meta", CLOSE),
    ]


def test_header_v1_defaults():
    data = stream(Header(sample_bits=8, samples_per_word=4), lambda w, fp: w.raw([1, 2]))
    assert data[4] == 20
    h, items = read_stream(data)
    assert (h.samples_per_clock, h.clock_bit, h.clock_first) == (1, 0xFF, 0)
    assert items == [("run", 1, 1), ("run", 2, 1)]


@pytest.mark.parametrize("bits", [1, 8, 12, 33, 64])
def test_runs_other_widths(bits):
    mask = (1 << bits) - 1
    pairs = [(mask, 1), (0, 300), (mask >> 1, 2 ** 64 - 1)] if bits > 1 else [(1, 1), (0, 300)]
    _, items = read_stream(stream(plain_v2(bits), lambda w, fp: w.runs(pairs)))
    assert items == [("run", v, r) for v, r in pairs]


def test_v2_narrow_stream_allows_raw_and_counts_samples():
    def body(w, fp):
        w.raw([0xABC, 0x123, 0xFFF]); w.events([(0, 1), (10, 2)]); w.runs([(7, 5)]); w.block(19)
    _, items = read_stream(stream(Header(version=2, sample_bits=12, samples_per_word=2), body))
    # 3 RAW samples + 11 EVNT samples + 5 RUNS samples: BLKH(19) follows.
    assert items == [("run", 0xABC, 1), ("run", 0x123, 1), ("run", 0xFFF, 1),
                     ("run", 1, 10), ("run", 2, 1), ("run", 7, 5), ("block", 19)]


def test_clock_first_one():
    _, items = read_stream(stream(trace_header(clock_first=1), lambda w, fp: w.runs([(5, 2), (6, 1)])))
    assert items == [("run", 5 | CLK, 2), ("run", 6 | CLK, 1)]


def test_writer_refuses_bad_input():
    fp = io.BytesIO(); w = Writer(fp, trace_header()); at = len(fp.getvalue())
    for pairs in ([(1, 1), (1, 1)], [(1, 0)], [(1 << 43, 1)], [(CLK, 1)], []):
        with pytest.raises(ValueError):
            w.runs(pairs)
    for n in (0, 4097):
        with pytest.raises(ValueError):
            w.meta("x" * n)
    assert len(fp.getvalue()) == at
    fp = io.BytesIO(); w = Writer(fp, Header())
    for call in (lambda: w.runs([(1, 1)]), lambda: w.block(0), lambda: w.meta(CLOSE)):
        with pytest.raises(ValueError, match="version 2"):
            call()
    with pytest.raises(ValueError):
        Writer(io.BytesIO(), trace_header(desc="d" * 252))


# --- rejection rules (the Python mirror raises where the C reader skips) --------

T = vgch_v2()  # a good trace header


@pytest.mark.parametrize("data,what", [
    (vgch_v2(spc=1), "bad VGCH v2 clock fields"),
    (vgch_v2(spc=1, cb=0xFF, cf=1), "bad VGCH v2 clock fields"),
    (vgch_v2(rsv=7), "bad VGCH v2 clock fields"),
    (vgch_v2(cb=0xFF), "bad VGCH v2 clock fields"),
    (vgch_v2(spc=3), "bad VGCH v2 clock fields"),
    (vgch_v2(spc=0, cb=0xFF), "bad VGCH v2 clock fields"),
    (vgch_v2(cb=43), "bad VGCH v2 clock fields"),
    (vgch_v2(cf=2), "bad VGCH v2 clock fields"),
    (vgch_v2(bits=0, spw=1, spc=1, cb=0xFF), "bad sample_bits"),
    (vgch_v2(bits=65, spc=1, cb=0xFF), "bad sample_bits"),
    (vgch_v2(spw=1), "bad samples_per_word"),
    (vgch_v2(bits=8, spw=0, spc=1, cb=0xFF), "bad samples_per_word"),
    (vgch_v2(bits=8, spw=3, spc=1, cb=0xFF), "bad samples_per_word"),
    (vgch_v2(desc=b"d" * 252), "bad VGCH length"),
    (vgch_v2(desc=b"d" * 255), "bad VGCH length"),
    (vgch_v2(length=22)[:30], "bad VGCH length"),
    (T + runs_chunk([(CLK | 1, 1)]), "RUNS value has the clock bit set"),
    (T + runs_chunk([(1 << 43, 1)]), "RUNS value has bits above sample_bits"),
    (T + runs_chunk([(1, b"\x00")]), "RUNS run is zero"),
    (T + runs_chunk([(1, bytes.fromhex("818000"))]), "RUNS run not minimally encoded"),
    (T + runs_chunk([(1, bytes.fromhex("ffffffffffffffffff02"))]), "RUNS run over 64 bits"),
    (T + runs_chunk([(1, 1)]) + b"", None),
    (T + runs_chunk([(1, 1)], count=2), "RUNS pairs do not fill the chunk"),
    (T + chunk(b"RUNS", struct.pack("<I", 1) + bytes(6) + b"\x01\x00"), "RUNS pairs do not fill the chunk"),
    (T + chunk(b"RUNS", b"\x01\x00"), "RUNS pairs do not fill the chunk"),
    (T + chunk(b"RUNS", struct.pack("<I", 0)), "RUNS has no pairs"),
    (T + runs_chunk([(2, 1), (2, 3)]), "RUNS adjacent pairs have equal values"),
    (T + chunk(b"BLKH", bytes(4)), "bad BLKH length"),
    (T + runs_chunk([(1, 4)]) + chunk(b"BLKH", struct.pack("<Q", 3)), "BLKH first_sample does not follow"),
    (T + chunk(b"META", b""), "bad META length"),
    (T + chunk(b"META", b"x" * 4097), "bad META length"),
    (T + chunk(b"META", b"meta=open\n"), "bad META text"),
    (T + chunk(b"RAW ", struct.pack("<II", 1, 5)), "chunk not allowed with a sample clock"),
    (T + chunk(b"FRAM", bytes(16)), "chunk not allowed with a sample clock"),
    (T + chunk(b"EVNT", bytes(4)), "chunk not allowed with a sample clock"),
    (T + chunk(b"XYZW", b"abc"), "chunk not allowed with a sample clock"),
    (T + chunk(b"RLE ", struct.pack("<III", 1, 3, 2)), "RLE  chunk not allowed in version 2"),
    # Short forbidden chunks: the ban is reported, not the length (C's order).
    (T + chunk(b"RAW ", b"\0\0"), "chunk not allowed with a sample clock"),
    (T + chunk(b"RLE ", b"\0\0"), "RLE  chunk not allowed in version 2"),
    (vgch_v2(bits=8, spw=1, spc=1, cb=0xFF) + chunk(b"RLE ", b"\0\0"), "RLE  chunk not allowed in version 2"),
    (vgch_v2(spc=1, cb=0xFF) + chunk(b"RAW ", b"\0\0"), "chunk not allowed with sample_bits over 32"),
    # Two faults: a count the length cannot hold is judged before pair 1.
    (T + chunk(b"RUNS", struct.pack("<I", 5) + (CLK | 1).to_bytes(6, "little") + b"\x01"),
     "RUNS pairs do not fill the chunk"),
    # Allowed but short: the same "bad ... length" as C.
    (vgch_v2(bits=8, spw=1, spc=1, cb=0xFF) + chunk(b"RAW ", b"\0\0"), "bad RAW length"),
    (vgch_v2(spc=1, cb=0xFF) + chunk(b"RLE ", struct.pack("<III", 1, 3, 2)), "RLE  chunk not allowed in version 2"),
    (vgch_v2(spc=1, cb=0xFF) + chunk(b"RAW ", struct.pack("<II", 1, 5)), "chunk not allowed with sample_bits over 32"),
    (vgch_v2(spc=1, cb=0xFF) + chunk(b"FRAM", bytes(16)), "chunk not allowed with sample_bits over 32"),
    (vgch_v2(spc=1, cb=0xFF) + chunk(b"EVNT", bytes(4)), "chunk not allowed with sample_bits over 32"),
    (stream(Header()) + runs_chunk([(1, 1)], vbytes=1), "chunk needs a version 2 header"),
    (stream(Header()) + chunk(b"BLKH", bytes(8)), "chunk needs a version 2 header"),
    (stream(Header()) + chunk(b"META", CLOSE.encode()), "chunk needs a version 2 header"),
])
def test_rejections(data, what):
    if what is None:
        read_stream(data)
        return
    with pytest.raises(ValueError, match=what):
        read_stream(data)


def test_header_v2_limits_accepted():
    assert read_stream(vgch_v2(desc=b"d" * 251))[0].desc == "d" * 251
    assert read_stream(vgch_v2(bits=5, spw=4, spc=1, cb=0xFF))[0].sample_bits == 5
    assert read_stream(vgch_v2(bits=64, cb=63, cf=1))[0].clock_first == 1
    with pytest.raises(ValueError, match="unsupported version"):
        read_stream(b"VGCH\x18\0\0\0\x03" + vgch_v2()[9:])


def test_runs_equal_values_across_chunks_allowed():
    _, items = read_stream(stream(trace_header(), lambda w, fp: (w.runs([(9, 3)]), w.runs([(9, 4)]))))
    assert items == [("run", 9, 3), ("run", 9 | CLK, 4)]


def test_unknown_tag_skipped_without_clock():
    data = vgch_v2(spc=1, cb=0xFF) + chunk(b"XYZW", b"a") + runs_chunk([(5, 2)])
    assert read_stream(data)[1] == [("run", 5, 2)]


# --- META ---------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    OPEN, CLOSE,
    "meta=open\nproject=x y=z\ncalib_seconds=-1.5e-3\nseconds=.25\nlayout=tt-um-43\n"
    "override=\nsimulator=verilator 5\nharness=h\nfuture_key=anything at all\n",
    "meta=close\ncapture_after_clock=18446744073709551615\nstatus=bad-timing\nframes=0\nproject=é€\U0001F600\n",
] + [f"meta=close\ncapture_after_clock=1\nstatus={s}\nframes=1\n"
     for s in ("ok", "sim-timeout", "unstable-sync", "no-sync", "bad-timing")]
  + [f"meta=open\nproject=x\ncalib_seconds={d}\nseconds=1\nlayout=tt-um-43\n"
     for d in ("0", "+3", "-.5", "2.", "1E+9", "12.5e-03")])
def test_meta_accepted(text):
    assert read_stream(stream(trace_header(), lambda w, fp: w.meta(text)))[1] == [("meta", text)]


def test_meta_4096_bytes():
    head = "meta=close\nstatus=ok\nframes=1\ncapture_after_clock=1\nproject="
    text = head + "a" * (4096 - len(head) - 1) + "\n"
    assert read_stream(stream(trace_header(), lambda w, fp: w.meta(text)))[1] == [("meta", text)]


OPEN_KEYS = "project=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n"


@pytest.mark.parametrize("raw", [
    b"meta=open\n" + OPEN_KEYS.encode()[:-1],
    b"project=x\nmeta=open\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n",
    b"meta=opened\n" + OPEN_KEYS.encode(),
    b"meta=open\n" + OPEN_KEYS.encode() + b"noequals\n",
    b"meta=open\n" + OPEN_KEYS.encode() + b"project=y\n",
    b"meta=open\n" + OPEN_KEYS.encode() + b"meta=close\n",
    b"meta=open\nProject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n",
    b"meta=open\n" + OPEN_KEYS.encode() + b"1x=2\n",
    b"meta=open\n" + OPEN_KEYS.encode() + b"=2\n",
    b"meta=open\n" + OPEN_KEYS.encode() + b"k-y=2\n",
    b"meta=open\nproject=\xff\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n",
    b"meta=open\nproject=\xc0\xaf\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n",
    b"meta=open\nproject=\xed\xa0\x80\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n",
    b"meta=open\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n",
    b"meta=open\nproject=x\nseconds=2\nlayout=tt-um-43\n",
    b"meta=open\nproject=x\ncalib_seconds=1\nlayout=tt-um-43\n",
    b"meta=open\nproject=x\ncalib_seconds=1\nseconds=2\n",
    b"meta=close\nstatus=ok\nframes=1\n",
    b"meta=close\ncapture_after_clock=1\nframes=1\n",
    b"meta=close\ncapture_after_clock=1\nstatus=ok\n",
    b"meta=open\nproject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-44\n",
    b"meta=open\nproject=x\ncalib_seconds=1\nseconds=\nlayout=tt-um-43\n",
    b"meta=open\nproject=x\ncalib_seconds=1\nseconds=1.2.3\nlayout=tt-um-43\n",
    b"meta=open\nproject=x\ncalib_seconds=1\nseconds=0x10\nlayout=tt-um-43\n",
    b"meta=open\nproject=x\ncalib_seconds=1\nseconds=inf\nlayout=tt-um-43\n",
    b"meta=open\nproject=x\ncalib_seconds=1\nseconds= 2\nlayout=tt-um-43\n",
    b"meta=open\nproject=x\ncalib_seconds=1e\nseconds=2\nlayout=tt-um-43\n",
    b"meta=open\nproject=x\ncalib_seconds=.\nseconds=2\nlayout=tt-um-43\n",
    b"meta=close\ncapture_after_clock=-1\nstatus=ok\nframes=1\n",
    b"meta=close\ncapture_after_clock=1\nstatus=ok\nframes=\n",
    b"meta=close\ncapture_after_clock=18446744073709551616\nstatus=ok\nframes=1\n",
    b"meta=close\ncapture_after_clock=1\nstatus=ok\nframes=+1\n",
    b"meta=close\ncapture_after_clock=1\nstatus=truncated\nframes=1\n",
    b"meta=close\ncapture_after_clock=1\nstatus=OK\nframes=1\n",
])
def test_meta_bad_text(raw):
    with pytest.raises(ValueError, match="bad META text"):
        read_stream(T + chunk(b"META", raw))


# --- C cross-check through vgacap-dump -----------------------------------------

def fnv1a64_samples(header: Header, runs) -> int:
    """vgacap-dump's v2 checksum: FNV-1a 64 over each sample as vbytes
    little-endian bytes, the clock bit toggling sample by sample."""
    vb = (header.sample_bits + 7) // 8
    h = 0xCBF29CE484222325
    for value, run in runs:
        for k in range(run):
            v = value ^ (CLK if header.clock_bit != 0xFF and (k & 1) else 0)
            for b in v.to_bytes(vb, "little"):
                h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def test_c_dump_agrees_with_python_v2(tmp_path):
    h = trace_header(desc="py")
    pairs = [(0x030000000000, 301), (0x030000000080, 1), (0x0000_0000_00FF, 1000), (0x1_0000_0001, 7)]

    def body(w, fp):
        w.meta(OPEN); w.block(0); w.runs(pairs[:2]); w.block(302); w.runs(pairs[2:]); w.meta(CLOSE)
    data = stream(h, body)
    f = tmp_path / "s.vgacap"; f.write_bytes(data)
    out = subprocess.run([str(DUMP), str(f)], capture_output=True, text=True, check=True).stdout
    _, items = read_stream(data)
    runs = [(i[1], i[2]) for i in items if i[0] == "run"]
    total = sum(r for _, r in pairs)
    assert sum(r for _, r in runs) == total
    assert "VGCH v2 bits=43 spc=2 clock_bit=42" in out
    assert f"total_samples={total} crc64={fnv1a64_samples(h, runs):016x}" in out
    assert "crc32=" not in out
    assert "RUNS pairs=2" in out and "BLKH first_sample=302" in out and "META len=" in out


def test_c_dump_reports_v2_skips(tmp_path):
    data = vgch_v2() + runs_chunk([(1, b"\x00")]) + chunk(b"BLKH", struct.pack("<Q", 0)) + runs_chunk([(1, 1)])
    f = tmp_path / "s.vgacap"; f.write_bytes(data)
    out = subprocess.run([str(DUMP), str(f)], capture_output=True, text=True, check=True).stdout
    assert "resync skipped=19 reason=RUNS run is zero" in out
    assert "total_samples=1 crc64=" in out


def test_read_chunks_sees_new_tags():
    data = stream(trace_header(), lambda w, fp: (w.meta(OPEN), w.block(0), w.runs([(1, 1)])))
    assert [t for t, _ in read_chunks(data)] == ["VGCH", "META", "BLKH", "RUNS"]


def test_c_dump_survives_bad_sample_bits(tmp_path):
    # A VGCH whose sample_bits (200) the first pass never validated used to
    # make vgacap-dump read 25 value bytes into an 8-byte buffer.
    data = vgch_v2(bits=200, spc=1, cb=0xFF) + runs_chunk([(1, 1)], vbytes=25)
    f = tmp_path / "s.vgacap"; f.write_bytes(data)
    proc = subprocess.run([str(DUMP), str(f)], capture_output=True, text=True)
    assert proc.returncode == 0, proc
    assert "RUNS pairs=1 runs=0 (malformed)" in proc.stdout
    assert "error: bad sample_bits 200" in proc.stderr
    assert "reason=bad sample_bits" in proc.stdout


def test_c_dump_bad_meta_keeps_n_known(tmp_path):
    # Ruling K: a bad META in a clock stream loses no samples, so every later
    # RUNS is still delivered.
    data = (vgch_v2() + runs_chunk([(1, 5)]) + chunk(b"META", b"meta=open\n")
            + runs_chunk([(2, 7)]) + runs_chunk([(3, 9)]))
    f = tmp_path / "s.vgacap"; f.write_bytes(data)
    out = subprocess.run([str(DUMP), str(f)], capture_output=True, text=True, check=True).stdout
    assert "reason=bad META text" in out
    assert "total_samples=21 crc64=" in out


def test_parse_header_v2_keeps_clock_hz_and_clock_fields():
    h = parse_header(stream(trace_header())[8:8 + 24])
    assert h.clock_hz == 25_175_000
    assert type(h.clock_hz) is int
    assert (h.samples_per_clock, h.clock_bit, h.clock_first) == (2, 42, 0)
    assert h.has_clock
