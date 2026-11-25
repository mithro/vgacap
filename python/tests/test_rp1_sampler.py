# SPDX-License-Identifier: Apache-2.0
"""vgacap-rp1's sampler is the RP2 sampler, instruction for instruction.

capture-rp1/vgacap_rp1.c builds its programs at run time from
capture-rp1/programs.h with piolib's `pio_encode_*`. Those need an opened
RP1, but the RP1 chip's encoders are piolib's header-only
`hardware/pio_instructions.h` ones unchanged, so capture-rp1/print_programs.c
builds the same table with those on any host and prints the words. Here it
is compiled against piolib (`VGACAP_PIOLIB`, the directory CI checks out at
the commit the tool was proven with) and its sampler must be what
MicroPython's own assembler makes of `capture_rp2.make_sampler()`, for
every clock pin and edge.

On pi-sw2-p4 (the loopback layout: data GPIO 4..11, clock GPIO 12, rising
edge) vgacap-rp1 printed `200c 208c 4008` with VGACAP_RP1_DEBUG; those
words, run on the emulated PIO against that pin layout, must take every
sample in order.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ttcap import mp
from ttcap.emu import Board, Waveform
from ttcap.emu.board import assemble
from vgacap.modes import MODES
from vgacap.synth import bars, frame_samples

#: What vgacap-rp1 printed on pi-sw2-p4, 2026-11-24.
RP1_WORDS = [0x200C, 0x208C, 0x4008]
DATA_BASE, CLK = 4, 12
CAPTURE_RP1 = Path(__file__).resolve().parents[2] / "capture-rp1"


def make_sampler(rp2):
    source = mp.source("capture_rp2.py")
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == "make_sampler")
    namespace = {"rp2": rp2}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "capture_rp2.py", "exec"), namespace)  # noqa: S102
    return namespace["make_sampler"]


@pytest.fixture(scope="module")
def print_programs(tmp_path_factory):
    """capture-rp1/print_programs.c, built against piolib's headers."""
    piolib = os.environ.get("VGACAP_PIOLIB")
    if not piolib:
        # CI checks piolib out for this; there, a skip would hide the check.
        if os.environ.get("CI"):
            pytest.fail("VGACAP_PIOLIB is not set: the C sampler cannot be checked")
        pytest.skip("VGACAP_PIOLIB (raspberrypi/utils' piolib) is not set")
    cc = shutil.which("cc")
    assert cc, "no C compiler"
    exe = tmp_path_factory.mktemp("rp1") / "vgacap-rp1-programs"
    subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-Werror", f"-I{piolib}/include",
         "-o", str(exe), str(CAPTURE_RP1 / "print_programs.c")],
        check=True)

    def run(clk: int, rising: bool, delay: int = 3) -> dict[str, list[int]]:
        out = subprocess.run([str(exe), str(clk), "rising" if rising else "falling", str(delay)],
                             check=True, capture_output=True, text=True).stdout
        return {name: [int(w, 16) for w in words]
                for name, *words in (line.split() for line in out.splitlines())}

    return run


def test_the_rp1_program_is_the_rp2_sampler():
    prog = assemble(lambda rp2: make_sampler(rp2)(CLK, 8, 32, True))

    assert list(prog[0]) == RP1_WORDS


@pytest.mark.parametrize("rising", [True, False], ids=["rising", "falling"])
@pytest.mark.parametrize("clk", [0, CLK, 27])
def test_the_c_sampler_is_the_rp2_sampler(print_programs, clk, rising):
    prog = assemble(lambda rp2: make_sampler(rp2)(clk, 8, 32, rising))

    assert print_programs(clk, rising)["sampler"] == list(prog[0])


def test_the_c_sampler_is_what_the_board_printed(print_programs):
    assert print_programs(CLK, True)["sampler"] == RP1_WORDS


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
