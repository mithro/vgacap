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
        held = None  # (data) of a byte offered and not taken on the last edge
        while True:
            # A generator's write lands on the edge after its yield, so the
            # DUT samples, on the coming edge, the out_ready already in place:
            # read that back rather than trusting the value about to be set.
            valid = yield dut.out_valid
            data = yield dut.out_data
            ready_now = yield dut.out_ready
            if held is not None:
                # valid/ready hold: an offered byte stays offered, unchanged,
                # until it is accepted.
                assert valid and data == held, (cycle, valid, data, held)
            if valid and ready_now:
                out.append(data)
                held = None
            else:
                held = data if valid else None
            yield dut.out_ready.eq(int(ready(cycle)))
            yield
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


def check_whole_chunks(samples, raws, drops, chunk=16):
    """Every RAW chunk is a whole input chunk, in order; the last TIME chunk
    accounts for every chunk that did not arrive."""
    assert drops == sorted(drops) and all(d % chunk == 0 for d in drops)
    chunks = [samples[i:i + chunk] for i in range(0, len(samples), chunk)]
    positions = [chunks.index(r) for r in raws]
    assert positions == sorted(positions) and len(set(positions)) == len(positions)
    assert (drops[-1] if drops else 0) == chunk * (len(chunks) - len(raws))


def test_a_slow_sink_drops_whole_chunks_and_says_so():
    rng = random.Random(2)
    samples = [rng.randrange(256) for _ in range(16 * 20)]

    # Ready one cycle in five: four times slower than the bytes are made.
    stream = simulate(samples, ready=lambda c: c % 5 == 0, tail=20000)
    raws, drops = parse(stream)

    assert raws, "nothing got through"
    assert drops, "chunks were lost but no TIME chunk said so"
    check_whole_chunks(samples, raws, drops)


# Ready patterns with runs of consecutive ready cycles, which a sink that
# records a byte on the wrong edge turns into doubled bytes.
@pytest.mark.parametrize("ready", [
    lambda c: c % 5 in (0, 1),
    lambda c: c % 3 != 0,
    lambda c: c % 7 < 4,
], ids=["2-in-5", "2-in-3", "4-in-7"])
def test_runs_of_ready_cycles_move_each_byte_once(ready):
    rng = random.Random(5)
    samples = [rng.randrange(256) for _ in range(16 * 12)]

    raws, drops = parse(simulate(samples, ready=ready, gap=lambda i: 3, tail=20000))

    assert drops == [] and sum(raws, []) == samples


@pytest.mark.parametrize("seed", range(6))
def test_a_random_sink_holds_each_byte_and_loses_only_whole_chunks(seed):
    rng = random.Random(100 + seed)
    samples = [rng.randrange(256) for _ in range(16 * 30)]
    p_ready = rng.choice([0.2, 0.5, 0.8])
    pattern = [rng.random() < p_ready for _ in range(1 << 16)]

    # simulate()'s sink asserts the hold rule on every offered byte.
    stream = simulate(samples, ready=lambda c: pattern[c % len(pattern)],
                      gap=lambda i: rng.randrange(3), tail=40000)
    raws, drops = parse(stream)

    assert raws, "nothing got through"
    check_whole_chunks(samples, raws, drops)


def test_gaps_between_samples_change_nothing():
    rng = random.Random(3)
    samples = [rng.randrange(256) for _ in range(16 * 6)]

    raws, drops = parse(simulate(samples, gap=lambda i: i % 3))

    assert drops == [] and sum(raws, []) == samples


def test_the_committed_verilog_is_what_the_source_generates():
    assert (RTL / "vgacap_raw_framer.v").read_text() == verilog()


@pytest.mark.skipif(shutil.which("iverilog") is None, reason="needs Icarus Verilog")
@pytest.mark.parametrize("n_chunks, gap, ready_pct, expect_drops", [
    (6, 1, 100, False),   # always ready, a sample every other clock
    (8, 3, 60, False),    # backpressure the FIFO absorbs
    (12, 0, 30, True),    # a sample every clock into a slow sink: overruns
    (48, 0, 100, True),   # every clock outruns even an always-ready sink,
                          # once the 16-clock deficit a chunk fills the FIFO
], ids=["ready", "backpressure", "slow-sink", "full-rate"])
def test_the_generated_verilog_frames_the_same_stream_in_icarus(tmp_path, n_chunks, gap, ready_pct,
                                                                 expect_drops):
    rng = random.Random(4)
    samples = bytes(rng.randrange(256) for _ in range(256 * n_chunks))
    (tmp_path / "samples.hex").write_text("\n".join("%02x" % s for s in samples) + "\n")
    tb = RTL / "tb_vgacap_raw_framer.v"
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp), str(tb), str(RTL / "vgacap_raw_framer.v")],
                   check=True, capture_output=True, text=True)
    # The testbench $fatal()s on a valid/ready hold violation: check=True.
    run = subprocess.run(["vvp", "-n", str(vvp), "+samples=%s" % (tmp_path / "samples.hex"),
                          "+count=%d" % len(samples), "+out=%s" % (tmp_path / "out.hex"),
                          "+gap=%d" % gap, "+ready_pct=%d" % ready_pct, "+seed=7"],
                         capture_output=True, text=True, check=True, cwd=tmp_path)
    out = bytes(int(x, 16) for x in (tmp_path / "out.hex").read_text().split())

    raws, drops = parse(out)

    assert "dropped=%d" % (drops[-1] if drops else 0) in run.stdout, run.stdout
    if expect_drops:
        assert raws and drops, run.stdout
        check_whole_chunks(list(samples), raws, drops, chunk=256)
    else:
        assert drops == [], run.stdout
        assert bytes(sum(raws, [])) == samples
