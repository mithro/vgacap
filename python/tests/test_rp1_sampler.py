# SPDX-License-Identifier: Apache-2.0
"""vgacap-rp1's sampler is the RP2 sampler, instruction for instruction.

capture-rp1/vgacap_rp1.c builds its program at run time with piolib's
`pio_encode_*`; with VGACAP_RP1_DEBUG it prints the words, and on
pi-sw2-p4 (the loopback layout: data GPIO 4..11, clock GPIO 12, rising
edge) they were `200c 208c 4008`. Here MicroPython's own assembler builds
`capture_rp2.make_sampler()` for the same pins and must produce the same
words, and those words, run on the emulated PIO against that pin layout,
must take every sample in order.
"""

from __future__ import annotations

import ast

from ttcap import mp
from ttcap.emu import Board, Waveform
from ttcap.emu.board import assemble
from vgacap.modes import MODES
from vgacap.synth import bars, frame_samples

#: What vgacap-rp1 printed on pi-sw2-p4, 2026-11-24.
RP1_WORDS = [0x200C, 0x208C, 0x4008]
DATA_BASE, CLK = 4, 12


def make_sampler(rp2):
    source = mp.source("capture_rp2.py")
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == "make_sampler")
    namespace = {"rp2": rp2}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "capture_rp2.py", "exec"), namespace)  # noqa: S102
    return namespace["make_sampler"]


def test_the_rp1_program_is_the_rp2_sampler():
    prog = assemble(lambda rp2: make_sampler(rp2)(CLK, 8, 32, True))

    assert list(prog[0]) == RP1_WORDS


def test_those_words_take_every_sample_in_order_on_the_emulated_pio():
    uo = frame_samples(MODES["640x480@60"], bars(640, 480)).tobytes()
    # The loopback generator: 8 PIO clocks a sample, clock high for 4.
    wave = Waveform(uo, list(range(DATA_BASE, DATA_BASE + 8)), CLK, period=8, high=4, delay=0)
    # Polled reads stand in for the RP1's DMA: emulated time must move in
    # steps shorter than the FIFO's 16 samples, or the sampler stalls and
    # drops samples exactly as an undrained one would on the board.
    board = Board(wave, chip="rp2040", idle_cycles=8)
    rp2 = board.modules()["rp2"]
    machine = board.modules()["machine"]
    sm = rp2.StateMachine(0, make_sampler(rp2)(CLK, 8, 32, True), in_base=machine.Pin(DATA_BASE))
    sm.active(1)

    words = [sm.get() for _ in range(2000)]

    got = [(w >> s) & 0xFF for w in words for s in (24, 16, 8, 0)]
    start = (uo + uo).find(bytes(got[:4000]))  # long enough to be unique in the frame
    assert start >= 0, "the first samples are not a run of the signal"
    assert bytes(got) == (uo + uo)[start:start + len(got)]
