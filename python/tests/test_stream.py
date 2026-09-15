# SPDX-License-Identifier: Apache-2.0
import io
import struct

import pytest

from vgacap.stream import Header, Writer, pack_words, unpack_words, read_chunks, read_stream, TINYVGA_MAP

def test_pack_lsb_first():
    h = Header(sample_bits=8, samples_per_word=4)
    assert pack_words(h, [0x11, 0x22, 0x33, 0x44, 0x55]) == [0x44332211, 0x00000055]

def test_pack_msb_first_12bit():
    h = Header(sample_bits=12, samples_per_word=2, flags=1)
    assert pack_words(h, [0xABC, 0x123]) == [0xABC00000 >> 8 | 0x123]  # 0xABC123
    assert unpack_words(h, [0xABC123], 2) == [0xABC, 0x123]

def test_header_bytes():
    fp = io.BytesIO(); Writer(fp, Header(sample_bits=12, samples_per_word=2, clock_hz=25175000, desc="x"))
    data = fp.getvalue()
    assert data[:4] == b"VGCH" and data[4] == 21 and data[8:10] == b"\x01\x00" and data[10] == 12
    assert data[16:24] == bytes(TINYVGA_MAP) and data[24] == 2 and data[26:28] == b"\x01\x00" and data[28:29] == b"x"

def test_roundtrip_all_chunks():
    fp = io.BytesIO(); w = Writer(fp, Header(sample_bits=8, samples_per_word=4))
    w.raw([1, 2, 3, 4, 5]); w.rle([(0x88, 96), (0x3F, 640)]); w.frame(7, 10, 1, 5, [9, 8, 7, 6, 5])
    w.events([(0, 0x80), (10, 0x00), (12, 0x3F)]); w.time(123, 500000, 0, "ok")
    header, items = read_stream(fp.getvalue())
    assert header.sample_bits == 8
    runs = [i for i in items if i[0] == "run"]
    assert runs[:5] == [("run", v, 1) for v in (1, 2, 3, 4, 5)]
    assert ("run", 0x88, 96) in runs and ("run", 0x3F, 640) in runs
    assert ("frame", 7, 10, 1, 5, 5) in items
    assert runs[-3:] == [("run", 0x80, 10), ("run", 0x00, 2), ("run", 0x3F, 1)]
    assert [i for i in items if i[0] == "time"] == [("time", 123, 500000, 0, "ok")]
    assert [t for t, _ in read_chunks(fp.getvalue())] == ["VGCH", "RAW ", "RLE ", "FRAM", "EVNT", "TIME"]


# --- framing/length validation (mirrors the C reader's checks) --------------

def _header_bytes(**kw) -> bytes:
    fp = io.BytesIO(); Writer(fp, Header(**kw)); return fp.getvalue()


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload


def test_time_msg_len_beyond_payload_rejected():
    # 20 message bytes declared, none carried: the C reader used to hand back
    # 20 bytes of stale scratch, and this mirror used to accept it silently.
    data = _header_bytes() + _chunk(b"TIME", struct.pack("<QIIH", 1, 2, 3, 20))
    with pytest.raises(ValueError, match="msg_len"):
        read_stream(data)


def test_rle_pair_count_mismatch_rejected():
    data = _header_bytes() + _chunk(b"RLE ", struct.pack("<I", 5) + b"\0" * 16)
    with pytest.raises(ValueError, match="pair_count"):
        read_stream(data)


def test_raw_sample_count_mismatch_rejected():
    data = _header_bytes() + _chunk(b"RAW ", struct.pack("<I", 99) + b"\0" * 8)
    with pytest.raises(ValueError, match="samples"):
        read_stream(data)


def test_fram_sample_count_mismatch_rejected():
    head = struct.pack("<IHHII", 1, 0, 1, 800, 99)
    data = _header_bytes() + _chunk(b"FRAM", head + b"\0" * 8)
    with pytest.raises(ValueError, match="samples"):
        read_stream(data)


def test_evnt_event_count_mismatch_rejected():
    data = _header_bytes() + _chunk(b"EVNT", struct.pack("<I", 4) + b"\0" * 12)
    with pytest.raises(ValueError, match="event_count"):
        read_stream(data)


def test_non_printable_tag_rejected():
    data = _header_bytes() + _chunk(b"\x01\x02\x03\x04", b"")
    with pytest.raises(ValueError, match="non-printable"):
        read_stream(data)


def test_oversized_length_rejected():
    data = _header_bytes() + b"RLE " + struct.pack("<I", 1 << 30)
    with pytest.raises(ValueError, match="max"):
        read_stream(data)


def test_short_known_chunk_rejected():
    data = _header_bytes() + _chunk(b"TIME", b"\0" * 4)
    with pytest.raises(ValueError, match="too short"):
        read_stream(data)


def test_unknown_printable_chunk_is_skipped():
    data = _header_bytes() + _chunk(b"XYZW", b"\1\2\3\4")
    data += _chunk(b"RLE ", struct.pack("<I", 1) + struct.pack("<II", 5, 2))
    _, items = read_stream(data)
    assert items == [("run", 5, 2)]


def test_header_desc_len_checked_against_payload():
    bad = struct.pack("<HBBI8sBBH", 1, 8, 3, 0, bytes(TINYVGA_MAP), 4, 0, 7)  # says 7 desc bytes
    with pytest.raises(ValueError, match="desc_len"):
        read_stream(_chunk(b"VGCH", bad))


def test_header_sample_bits_validated():
    bad = struct.pack("<HBBI8sBBH", 1, 7, 3, 0, bytes(TINYVGA_MAP), 4, 0, 0)
    with pytest.raises(ValueError, match="sample_bits"):
        read_stream(_chunk(b"VGCH", bad))


def test_header_samples_per_word_validated():
    bad = struct.pack("<HBBI8sBBH", 1, 32, 3, 0, bytes(TINYVGA_MAP), 4, 0, 0)  # 4*32 > 32
    with pytest.raises(ValueError, match="samples_per_word"):
        read_stream(_chunk(b"VGCH", bad))
