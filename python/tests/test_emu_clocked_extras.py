# SPDX-License-Identifier: Apache-2.0
"""The three emulator pieces `clocked_rp2.py` needs on top of 4c-1 and 4c-2.

* `StateMachine.exec(instr)`, as MicroPython v1.24.0's `rp2_pio.c` has it:
  a string is assembled by `rp2.asm_pio_encode` with the state machine's
  side-set count and runs at once (`pio_sm_exec`). Only `nop` is emulated,
  which is all the teardown runs (`sm.exec("nop() .side(0)")`): its
  side-set drives the pin from the cycle it runs.
* A read of a pad's `IO_BANK0` `GPIOn_CTRL` (RP2350 `0x40028000 + 8n + 4`,
  RP2040 `0x40014000 + 8n + 4`): FUNCSEL is 6 + the PIO block for a pad a
  state machine drives, 5 (SIO) for one a `Pin` mode took, and 31 (NULL,
  the reset value) otherwise.
* `SMn_CLKDIV` (block + 0x0c8 + 0x18 n) takes only 1.0 (`1 << 16`), the value
  every state machine runs at here; anything else is refused, as `freq=` is.
"""

from __future__ import annotations

import pytest

from ttcap.emu import Board, BoardError, ClockedDesign, Waveform

PIO1 = 0x50300000
CLK, RST = 16, 14
UO = list(range(33, 41))
CTRL_RP2350 = 0x40028000 + 8 * CLK + 4
CLKDIV0 = PIO1 + 0x0C8


def _rig(chip: str = "rp2350"):
    board = Board(ClockedDesign(UO, clk_gpio=CLK, rst_gpio=RST), chip=chip, pulls={RST: 1})
    mods = board.modules()
    rp2 = mods["rp2"]
    if chip == "rp2350":
        rp2.PIO(1).gpio_base(16)
    return board, mods


def _high_forever(rp2):
    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        label("top")  # noqa: F821
        jmp("top").side(1)  # noqa: F821

    return p


def test_exec_nop_with_side_set_drives_the_pin_low_at_once():
    board, mods = _rig()
    rp2, machine = mods["rp2"], mods["machine"]
    sm = rp2.StateMachine(4, _high_forever(rp2), sideset_base=machine.Pin(CLK))
    sm.active(1)
    board.advance(10)
    assert board.pad_level(CLK, board.now) == 1
    sm.exec("nop() .side(0)")
    at = board.now
    sm.active(0)
    board.advance(10)
    assert board.pad_level(CLK, at) == 0
    assert board.pad_level(CLK, board.now) == 0
    # The design saw exactly the one rising edge, none from the exec.
    assert len(board.wave.clock_rises) == 1


def test_exec_of_anything_but_nop_is_refused():
    board, mods = _rig()
    rp2, machine = mods["rp2"], mods["machine"]
    sm = rp2.StateMachine(4, _high_forever(rp2), sideset_base=machine.Pin(CLK))
    with pytest.raises(BoardError, match="exec"):
        sm.exec("set(x, 1) .side(0)")
    with pytest.raises(BoardError, match="exec"):
        sm.exec(0xE021)  # set x, 1 as a number


def test_exec_nop_as_a_number_runs_too():
    board, mods = _rig()
    rp2, machine = mods["rp2"], mods["machine"]
    sm = rp2.StateMachine(4, _high_forever(rp2), sideset_base=machine.Pin(CLK))
    sm.active(1)
    board.advance(4)
    sm.exec(0xA042)  # nop, side-set 0 in bit 12
    sm.active(0)
    assert board.pad_level(CLK, board.now) == 0


def test_funcsel_follows_whoever_drives_the_clock_pad():
    board, mods = _rig()
    rp2, machine = mods["rp2"], mods["machine"]
    assert machine.mem32[CTRL_RP2350] & 0x1F == 31  # never configured
    machine.Pin(CLK, machine.Pin.OUT, value=0)
    assert machine.mem32[CTRL_RP2350] & 0x1F == 5  # SIO
    sm = rp2.StateMachine(4, _high_forever(rp2), sideset_base=machine.Pin(CLK))
    assert machine.mem32[CTRL_RP2350] & 0x1F == 7  # PIO1
    sm.active(1)
    board.advance(4)
    machine.Pin(CLK, machine.Pin.OUT)  # taken back, as clock_project_once() does
    assert machine.mem32[CTRL_RP2350] & 0x1F == 5


def test_funcsel_is_at_the_rp2040_address_on_an_rp2040():
    wave = Waveform(b"\0", UO[:0], clk_gpio=CLK, period=4)
    board = Board(wave, chip="rp2040")
    machine = board.modules()["machine"]
    machine.Pin(CLK, machine.Pin.OUT, value=0)
    assert machine.mem32[0x40014000 + 8 * CLK + 4] & 0x1F == 5
    with pytest.raises(BoardError, match="unmodelled"):
        machine.mem32[CTRL_RP2350]


def test_clkdiv_takes_one_and_refuses_anything_else():
    board, mods = _rig()
    rp2, machine = mods["rp2"], mods["machine"]
    rp2.StateMachine(4, _high_forever(rp2), sideset_base=machine.Pin(CLK))
    machine.mem32[CLKDIV0] = 1 << 16
    for value in (9 << 16, (1 << 16) | (1 << 8), 0):
        with pytest.raises(BoardError, match="clock divider"):
            machine.mem32[CLKDIV0] = value
    with pytest.raises(BoardError, match="clock divider"):
        machine.mem32[CLKDIV0 + 0x18] = 2 << 16  # SM1
