# SPDX-License-Identifier: Apache-2.0
"""rtl/framer_to_stream.py: the raw framer's chunks behind a VGCH header
read back as the samples that went in."""
from __future__ import annotations

import importlib.util
import io
import pathlib

import pytest

from vgacap.stream import FLAG_FIRST_SAMPLE_MSB, Header, Writer, read_stream

PATH = pathlib.Path(__file__).resolve().parents[2] / "rtl" / "framer_to_stream.py"
spec = importlib.util.spec_from_file_location("framer_to_stream", PATH)
framer_to_stream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(framer_to_stream)


def framer_bytes(chunks, dropped=None) -> bytes:
    """Chunks as the framer sends them: RAW chunks of 4-a-word samples, first
    in the top byte, then an overrun TIME chunk; no VGCH."""
    fp = io.BytesIO()
    w = Writer(fp, Header(sample_bits=8, samples_per_word=4, flags=FLAG_FIRST_SAMPLE_MSB))
    start = len(fp.getvalue())
    for chunk in chunks:
        w.raw(chunk)
    if dropped is not None:
        w.time(0, 0, dropped, "overrun")
    return fp.getvalue()[start:]


def test_the_stream_holds_the_samples_in_order(tmp_path, capsys):
    chunks = [[(i * 7 + j) & 0xFF for j in range(256)] for i in range(3)]
    (tmp_path / "framer.bin").write_bytes(framer_bytes(chunks, dropped=512))

    rc = framer_to_stream.main([str(tmp_path / "framer.bin"), str(tmp_path / "out.vgacap"),
                                "--clock-hz", "25175000"])

    assert rc == 0
    assert capsys.readouterr().out.endswith(": 3 RAW chunks, 768 samples, 512 dropped\n")
    header, items = read_stream((tmp_path / "out.vgacap").read_bytes())
    assert header.clock_hz == 25175000 and header.flags == FLAG_FIRST_SAMPLE_MSB
    assert [v for kind, v, *_ in items if kind == "run"] == sum(chunks, [])


def test_a_chunk_cut_short_writes_nothing(tmp_path):
    data = framer_bytes([list(range(256))])[:-3]
    (tmp_path / "framer.bin").write_bytes(data)

    assert framer_to_stream.main([str(tmp_path / "framer.bin"), str(tmp_path / "out.vgacap")]) == 1
    assert not (tmp_path / "out.vgacap").exists()


def test_a_chunk_the_framer_never_sends_is_refused():
    with pytest.raises(ValueError, match="not one the raw framer sends"):
        framer_to_stream.summary(b"META" + (4).to_bytes(4, "little") + b"x=1\n")
