# SPDX-License-Identifier: Apache-2.0
"""The PIO run-length program of `rle_rp2.py`, on the emulated PIO.

Each test puts MicroPython's own assembly of `make_rle()` on the emulated
state machine against a known signal, collects every word it pushes and
expands them back into samples; those must be an unbroken stretch of the
signal -- no clock missed, doubled or altered. The signals: whole frames
of the bars and grid patterns, uniformly random bytes (a run is ~1 clock,
worse than tt_um_vgacal_prbs's 6 bits) and a strict alternation (a change
on every clock), at the shortest project clocks each form is rated for.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest
from rle_pio import Run, board_helpers, expand_words, lift, matches_signal, window_values

from ttcap.boards import RP2040_TT06
from ttcap.emu.board import assemble
from vgacap.modes import MODES
from vgacap.stream import read_chunks
from vgacap.synth import bars, frame_samples, grid

MODE = MODES["640x480@60"]
FRAME = MODE.cpl * MODE.lpf

#: The shortest exact project clock in system clocks, per (locked, masked):
#: locked is the longest path (6 masked, 7 through the OSR); edge was found
#: by the sweep in test_edge_style_needs_two_more_cycles.
MIN_PERIOD = {(True, True): 6, (True, False): 7, (False, True): 8, (False, False): 9}


def signal(name: str) -> bytes:
    if name == "bars":
        return frame_samples(MODE, bars(640, 480)).tobytes()
    if name == "grid":
        return frame_samples(MODE, grid(640, 480)).tobytes()
    if name == "random":
        return np.random.default_rng(7).integers(0, 256, 60_000, dtype=np.uint8).tobytes()
    if name == "alternating":
        return bytes([0x55, 0xAA] * 500)
    raise KeyError(name)


def run_and_check(uo, period, clocks, **kw):
    """Run, expand, and check every clock that passed is accounted for."""
    run = Run(uo, period, words=4 * clocks + 64, **kw)
    run.advance(period * clocks)
    words = run.words()
    samples = expand_words(words)
    expect = uo if kw.get("chip", "rp2350") == "rp2350" else window_values(
        uo, RP2040_TT06, kw.get("constants"))
    ok, _ = matches_signal(samples, expect)
    assert ok, "the expanded samples are not an unbroken stretch of the signal"
    # All but the open run (at most one run) and the clocks before the
    # program's first sample are in the words.
    assert len(samples) >= clocks - 3 - max_run(expect)
    assert run.rxstall() == 0
    return run, words, samples


def max_run(signal_values) -> int:
    a = np.asarray(signal_values if not isinstance(signal_values, bytes)
                   else np.frombuffer(signal_values, np.uint8))
    edges = np.flatnonzero(np.diff(a)) if len(a) > 1 else np.array([])
    if len(edges) == 0:
        return len(a)
    return int(max(np.diff(edges).max(initial=0), edges[0] + 1, len(a) - 1 - edges[-1]))


# -- the program ------------------------------------------------------------


def test_the_program_fits_beside_anything_and_has_the_paths_described():
    def build(rp2, locked, masked):
        return lift(["make_rle"], {"rp2": rp2})["make_rle"](16, 1, 0, 6 if masked else 7, masked, 8, locked)

    for locked in (True, False):
        for masked in (1, 0):
            prog = assemble(lambda rp2: build(rp2, locked, masked))
            words = list(prog[0])
            # wait, wait, sample (1 or 2), in, mov, jmp, W, [waits], S..
            assert len(words) == 17 + (0 if masked else 2) + (0 if locked else 2)
            assert len(words) <= 32


# -- exactness ------------------------------------------------------------


@pytest.mark.parametrize("name", ["bars", "grid"])
def test_a_whole_frame_at_the_shortest_locked_clock_is_sample_exact(name):
    # 6 system clocks a pixel: 25 MHz at the RP2350's 150 MHz.
    uo = signal(name)
    run, words, samples = run_and_check(uo, 6, FRAME + 2000, locked=True, masked=True)
    # A whole frame, every pixel of it, and twice the words of the changes.
    assert len(samples) > FRAME
    changes = int(np.count_nonzero(np.diff(np.frombuffer(uo, np.uint8))))
    assert abs(len(words) // 2 - changes) < 2000


@pytest.mark.parametrize("sample_delay", [0, 3])
@pytest.mark.parametrize("locked", [True, False], ids=["locked", "edge"])
@pytest.mark.parametrize("masked", [True, False], ids=["mask", "osr"])
@pytest.mark.parametrize("name", ["bars", "random", "alternating"])
def test_every_form_is_exact_at_its_shortest_clock(name, masked, locked, sample_delay):
    # The edge style's `wait [delay]` is in every loop, so its shortest
    # clock is that much longer; the locked style waits only once.
    period = MIN_PERIOD[(locked, masked)] + (0 if locked else sample_delay)
    for high in sorted({period // 2, (period + 1) // 2}):
        run_and_check(signal(name), period, 30_000, locked=locked, masked=masked, high=high,
                      sample_delay=sample_delay)


@pytest.mark.parametrize("delay", [0, 1, 2, 3])
@pytest.mark.parametrize("sample_delay", [0, 1])
def test_the_sample_point_moves_nothing_but_the_phase(delay, sample_delay):
    # The design's clock-to-output delay and the sample delay may put the
    # read either side of the change; either way it is one per clock.
    run_and_check(signal("random"), 6, 20_000, locked=True, masked=True,
                  delay=delay, sample_delay=sample_delay)


def test_a_longer_locked_clock_is_padded_to_the_same_exactness():
    for period in (7, 12, 35):
        run_and_check(signal("random"), period, 10_000, locked=True, masked=True)


@pytest.mark.parametrize("masked,period", [(True, 6), (False, 7)])
def test_a_clock_one_cycle_short_of_the_program_is_caught(masked, period):
    # The negative control: a program padded for P on a clock of P - 1 is
    # off by one cycle a sample and must not pass the check above.
    uo = signal("random")
    run = Run(uo, period - 1, prog_period=period, locked=True, masked=masked, words=1 << 16)
    run.advance((period - 1) * 20_000)
    ok, _ = matches_signal(expand_words(run.words()), uo)
    assert not ok


@pytest.mark.parametrize("sample_delay", [0, 1, 4])
@pytest.mark.parametrize("masked", [True, False], ids=["mask", "osr"])
def test_edge_style_needs_two_more_cycles(masked, sample_delay):
    # The waits find the edge a cycle after it, and must be back at the
    # first wait before the clock rises again -- after the sample delay,
    # which the edge style spends on every clock.
    shortest = MIN_PERIOD[(False, masked)] + sample_delay
    for period in range(shortest - 3, shortest):
        for name in ("random", "alternating"):
            uo = signal(name)
            run = Run(uo, period, locked=False, masked=masked, sample_delay=sample_delay,
                      words=1 << 16)
            run.advance(period * 10_000)
            try:
                samples = expand_words(run.words())
                ok, _ = matches_signal(samples, uo)
            except ValueError:
                ok, samples = False, []
            # Skipping every other clock of the alternation reads one long
            # run that never closes: right values, far too few of them.
            assert not (ok and len(samples) > 9_000), (period, name)


def test_the_rp2040_window_is_encoded_through_the_osr():
    # 12 pins with the RP2's own ui_in pads in the middle; `out y, 12`
    # takes exactly the window, as the RAW sampler's `in pins, 12` does.
    constants = {9: 1, 10: 0, 11: 1, 12: 0}
    run_and_check(signal("bars"), 7, 30_000, locked=True, masked=False, chip="rp2040",
                  constants=constants)


def test_without_in_count_the_one_instruction_sample_reads_other_pads():
    # Why the masked form needs SHIFTCTRL.IN_COUNT: `mov y, pins` reads all
    # 32 pins of the window from in_base up, which on the RP2350 board
    # wraps round to the project clock pad (GPIO 16, high at a rising-edge
    # sample) and the ui_in/uio pads. Without the mask no value matches.
    uo = signal("bars")
    run = Run(uo, 6, locked=True, masked=True, set_in_count=False, words=1 << 14)
    run.advance(6 * 3000)
    values = set(expand_words(run.words()).tolist())
    assert values and all(v > 0xFF for v in values)


# -- saturation -----------------------------------------------------------


@pytest.mark.parametrize("locked", [True, False], ids=["locked", "edge"])
def test_a_saturated_counter_pushes_and_counts_on(locked):
    # 2^32 equal clocks would take hours to emulate, so the counter is
    # poked forward instead, at the top of the loop where the ISR holds it.
    uo = bytes([5] * 3000 + [9] * 3000)
    period = MIN_PERIOD[(locked, True)]
    run = Run(uo, period, locked=locked, masked=True)
    sm = run.pio_sm
    wrap_target = (run.prog[3] >> 7) & 0x1F
    run.advance(period * 7000)  # into a run of 5s or 9s, a few hundred in
    while sm.pc != wrap_target:
        run.advance(1)
    before = sm.isr
    poke = 0xFFFFFFF0
    sm.isr = poke
    run.advance(period * 12_000)

    words = run.words()
    at = words.index(0xFFFFFFFF)
    assert at % 2 == 1, "the saturated run is a run word"
    value = words[at - 1]
    assert words[at + 1] == value, "the same value starts again"
    # The poke added (poke - before) phantom samples to that run; take them
    # out and every sample must be the signal again.
    merged = 0xFFFFFFFF + 1 + words[at + 2] - (poke - before)
    fixed = words[:at] + [merged] + words[at + 3 :]
    ok, _ = matches_signal(expand_words(fixed), uo)
    assert ok


# -- the board's half: fix_runs() and write_pairs() -------------------------


def chunks_to_pairs(data: bytes):
    pairs = []
    for tag, payload in read_chunks(data):
        assert tag == "RLE "
        n = struct.unpack_from("<I", payload)[0]
        pairs.extend(struct.iter_unpack("<II", payload[4 : 4 + 8 * n]))
    return pairs


def test_fix_runs_adds_the_missing_clock_and_counts_saturations():
    ns = lift(["fix_runs"], board_helpers())
    words = [7, 0, 8, 41, 9, 0xFFFFFFFF, 9, 2]
    buf = bytearray(struct.pack("<8I", *words))

    assert ns["fix_runs"](buf, len(words)) == 1
    assert list(struct.unpack("<8I", buf)) == [7, 1, 8, 42, 9, 0, 9, 3]


def test_a_saturated_pair_goes_out_as_two_halves():
    ns = board_helpers()
    ns["PAIRS_PER_BUF"] = 4
    lift(["write_rle_chunk", "write_pairs"], ns)
    buf = bytearray(struct.pack("<8I", 7, 1, 9, 0, 9, 3, 4, 5))

    class Out:
        data = b""

        def write(self, b):
            Out.data += bytes(b)
            return len(b)

    sent = ns["write_pairs"](Out(), buf)

    assert sent == len(Out.data)
    assert chunks_to_pairs(Out.data) == [(7, 1), (9, 1 << 31), (9, 1 << 31), (9, 3), (4, 5)]
