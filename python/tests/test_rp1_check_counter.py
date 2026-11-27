# SPDX-License-Identifier: Apache-2.0
"""capture-rp1/check_counter.py on streams written as vgacap-rp1 writes them:
8-bit samples, four a word, first sample in the top byte."""
from __future__ import annotations

import importlib.util
import io
import pathlib

from vgacap.stream import FLAG_FIRST_SAMPLE_MSB, TINYVGA_MAP, Header, Writer

PATH = pathlib.Path(__file__).resolve().parents[2] / "capture-rp1" / "check_counter.py"
spec = importlib.util.spec_from_file_location("check_counter", PATH)
check_counter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_counter)


def stream(values) -> bytes:
    fp = io.BytesIO()
    w = Writer(fp, Header(sample_bits=8, samples_per_word=4, flags=FLAG_FIRST_SAMPLE_MSB,
                          signal_map=TINYVGA_MAP, mode=0, clock_hz=500000, desc="vgacap-rp1"))
    w.raw(list(values))
    w.time(0, 500000, 0, "rp1")
    return fp.getvalue()


def test_an_unbroken_count_passes(tmp_path, capsys):
    path = tmp_path / "run.vgacap"
    path.write_bytes(stream([(7 - i) & 0xFF for i in range(1000)]))

    assert check_counter.main([str(path)]) == 0
    assert capsys.readouterr().out == "ok: 1000 samples, every step -1\n"


def test_a_lost_sample_is_named(tmp_path, capsys):
    values = [(7 - i) & 0xFF for i in range(1000)]
    del values[500]
    path = tmp_path / "run.vgacap"
    path.write_bytes(stream(values))

    assert check_counter.main([str(path)]) == 1
    assert capsys.readouterr().out.startswith("broken at sample 500: ")


def test_a_file_that_is_not_a_stream_is_refused(tmp_path):
    path = tmp_path / "junk.vgacap"
    path.write_bytes(b"not a stream")

    assert check_counter.main([str(path)]) == 2
