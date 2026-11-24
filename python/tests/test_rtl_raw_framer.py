# SPDX-License-Identifier: Apache-2.0
"""rtl/vgacap_raw_framer.py: its byte stream parses with vgacap's own reader.

Simulated two ways: Migen's simulator on the Migen source, and Icarus
Verilog on the committed generated `.v` -- the file a yosys-only flow
actually uses -- which must also be what the source generates today.
"""

from __future__ import annotations

import pathlib
import random
import shutil
import struct
import subprocess
import sys

import pytest

migen = pytest.importorskip("migen")
from migen.sim import passive, run_simulation  # noqa: E402

RTL = pathlib.Path(__file__).resolve().parents[2] / "rtl"
sys.path.insert(0, str(RTL))
from vgacap_raw_framer import VgacapRawFramer, verilog  # noqa: E402

from vgacap.stream import read_chunks  # noqa: E402


def parse(stream: bytes):
    """[(samples of each RAW chunk)], [dropped totals of each TIME chunk]."""
    raws, drops = [], []
    for tag, payload in read_chunks(stream):
        if tag == "RAW ":
            count = struct.unpack_from("<I", payload, 0)[0]
            samples = []
            for i in range(4, len(payload), 4):
                w = struct.unpack_from("<I", payload, i)[0]
                samples += [(w >> 24) & 255, (w >> 16) & 255, (w >> 8) & 255, w & 255]
            assert len(samples) == count
            raws.append(samples)
        elif tag == "TIME":
            dropped = struct.unpack_from("<I", payload, 12)[0]
            n = struct.unpack_from("<H", payload, 16)[0]
            assert payload[18:18 + n] == b"overrun"
            drops.append(dropped)
        else:
            raise AssertionError(tag)
    return raws, drops


def simulate(samples, *, chunk=16, fifo=8, ready=lambda cycle: True, gap=lambda i: 0, tail=4000):
    dut = VgacapRawFramer(chunk, fifo)
    out = []

    def drive():
        for i, s in enumerate(samples):
            for _ in range(gap(i)):
                yield dut.sample_valid.eq(0)
                yield
            yield dut.sample.eq(s)
            yield dut.sample_valid.eq(1)
            yield
        yield dut.sample_valid.eq(0)
        for _ in range(tail):
            yield

    @passive  # runs as long as drive() does, then the simulation ends
    def sink():
        cycle = 0
        while True:
            r = ready(cycle)
            yield dut.out_ready.eq(int(r))
            # A byte moves on the edge where valid and ready are both high:
            # read them before that edge.
            valid = yield dut.out_valid
            data = yield dut.out_data
            yield
            if valid and r:
                out.append(data)
            cycle += 1

    run_simulation(dut, [drive(), sink()])
    return bytes(out)


def test_an_always_ready_sink_gets_every_chunk_in_order():
    rng = random.Random(1)
    samples = [rng.randrange(256) for _ in range(16 * 10)]

    # A sample every other clock: the header overhead needs headroom.
    raws, drops = parse(simulate(samples, gap=lambda i: 1))

    assert drops == []
    assert sum(raws, []) == samples


def test_a_slow_sink_drops_whole_chunks_and_says_so():
    rng = random.Random(2)
    samples = [rng.randrange(256) for _ in range(16 * 20)]

    # Ready one cycle in five: four times slower than the bytes are made.
    stream = simulate(samples, ready=lambda c: c % 5 == 0, tail=20000)
    raws, drops = parse(stream)

    assert raws, "nothing got through"
    assert drops, "chunks were lost but no TIME chunk said so"
    assert drops == sorted(drops) and all(d % 16 == 0 for d in drops)
    # Every chunk that arrived is a whole, in-order chunk of the input.
    chunks = [samples[i:i + 16] for i in range(0, len(samples), 16)]
    positions = [chunks.index(r) for r in raws]
    assert positions == sorted(positions)
    # The last report accounts for every chunk that did not arrive.
    assert drops[-1] == 16 * (len(chunks) - len(raws))


def test_gaps_between_samples_change_nothing():
    rng = random.Random(3)
    samples = [rng.randrange(256) for _ in range(16 * 6)]

    raws, drops = parse(simulate(samples, gap=lambda i: i % 3))

    assert drops == [] and sum(raws, []) == samples


def test_the_committed_verilog_is_what_the_source_generates():
    assert (RTL / "vgacap_raw_framer.v").read_text() == verilog()


@pytest.mark.skipif(shutil.which("iverilog") is None, reason="needs Icarus Verilog")
def test_the_generated_verilog_frames_the_same_stream_in_icarus(tmp_path):
    rng = random.Random(4)
    samples = bytes(rng.randrange(256) for _ in range(256 * 6))
    (tmp_path / "samples.hex").write_text("\n".join("%02x" % s for s in samples) + "\n")
    tb = RTL / "tb_vgacap_raw_framer.v"
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp), str(tb), str(RTL / "vgacap_raw_framer.v")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(["vvp", "-n", str(vvp), "+samples=%s" % (tmp_path / "samples.hex"),
                          "+count=%d" % len(samples), "+out=%s" % (tmp_path / "out.hex")],
                         capture_output=True, text=True, check=True, cwd=tmp_path)
    out = bytes(int(x, 16) for x in (tmp_path / "out.hex").read_text().split())

    raws, drops = parse(out)

    assert drops == [], run.stdout
    assert bytes(sum(raws, [])) == samples
