# SPDX-License-Identifier: Apache-2.0
import io
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
