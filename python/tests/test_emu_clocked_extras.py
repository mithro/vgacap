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
* `SMn_CLKDIV` (block + 0x0c8 + 0x18 n): an integer divider, INT in bits
  31:16 (0 is 65536), so a state machine steps once every INT system
  cycles, counted from when it is enabled. A FRAC field (bits 15:8) is
  refused, as is a change while the machine runs; it reads back as
  written, and a `StateMachine` made with a program puts it back to 1.0,
  as `rp2_pio.c`'s init does with the default `freq=-1`.
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


def _toggle(rp2, high: int, low: int):
    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        nop().side(1)[high]  # noqa: F821
        nop().side(0)[low]  # noqa: F821

    return p


def _pins(board, gpio: int, start: int, n: int) -> list[int]:
    return [board.pio_pin(gpio, c) for c in range(start, start + n)]


@pytest.mark.parametrize("div", [1, 2, 9])
def test_an_integer_clkdiv_makes_each_pio_cycle_that_many_system_cycles(div):
    board, mods = _rig()
    rp2, machine = mods["rp2"], mods["machine"]
    sm = rp2.StateMachine(4, _toggle(rp2, 2, 1), sideset_base=machine.Pin(CLK))
    machine.mem32[CLKDIV0] = div << 16
    start = board.now
    sm.active(1)
    board.advance(3 * 5 * div)
    assert _pins(board, CLK, start, 15 * div) == ([1] * (3 * div) + [0] * (2 * div)) * 3


def test_a_stalled_state_machine_resumes_on_its_divided_clock():
    # The divider's enable pulses every `div` cycles from the enable: a
    # word put at an odd time is pulled at the next of them, not at once.
    board, mods = _rig()
    rp2, machine = mods["rp2"], mods["machine"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        pull(block).side(0)  # noqa: F821
        nop().side(1)  # noqa: F821
        label("top")  # noqa: F821
        jmp("top").side(1)  # noqa: F821

    sm = rp2.StateMachine(4, p, sideset_base=machine.Pin(CLK))
    machine.mem32[CLKDIV0] = 4 << 16
    start = board.now
    sm.active(1)
    board.advance(7)
    sm.put(1)
    board.advance(20)
    # The pull runs at start + 8 and takes 4 cycles: the nop raises the pin
    # at start + 12, where at the system clock it would be start + 8.
    assert _pins(board, CLK, start, 14) == [0] * 12 + [1] * 2


@pytest.mark.parametrize("div", [1, 3])
def test_a_wait_completes_on_a_divided_clock_cycle(div):
    # uo_out bit 0 (GPIO 33) toggles every 4 system cycles: with div 3 its
    # edges fall between the state machine's cycles, which see each level
    # at the first of theirs after it.
    wave = Waveform(bytes(range(256)), UO, clk_gpio=CLK, period=4)
    board = Board(wave, chip="rp2350")
    mods = board.modules()
    rp2 = mods["rp2"]
    rp2.PIO(1).gpio_base(16)

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        wait(1, gpio, 1).side(0)  # noqa: F821 - GPIO 33 in the window at 16
        nop().side(1)  # noqa: F821
        wait(0, gpio, 1).side(1)  # noqa: F821
        nop().side(0)  # noqa: F821

    sm = rp2.StateMachine(4, p, sideset_base=17)
    mods["machine"].mem32[CLKDIV0] = div << 16
    start = board.now
    sm.active(1)
    board.advance(60)
    # The same program, one state-machine cycle at a time.
    want, t = [], start
    while t < start + 50:
        for level, side in ((1, 1), (0, 0)):
            while wave.value(33, t) != level:
                t += div
            t += div  # the wait completes; the nop issues
            want.append((t, side))
            t += div
    got = [(c, board.pio_pin(17, c)) for c in range(start + 1, start + 50)
           if board.pio_pin(17, c) != board.pio_pin(17, c - 1)]
    assert len(got) >= 4
    assert got == [w for w in want if w[0] < start + 50]


def test_clkdiv_reads_back_and_a_new_state_machine_puts_it_back_to_one():
    board, mods = _rig()
    rp2, machine = mods["rp2"], mods["machine"]
    rp2.StateMachine(4, _high_forever(rp2), sideset_base=machine.Pin(CLK))
    assert machine.mem32[CLKDIV0] == 1 << 16
    machine.mem32[CLKDIV0] = 9 << 16
    assert machine.mem32[CLKDIV0] == 9 << 16
    machine.mem32[CLKDIV0] = 0  # INT 0 is 65536
    assert machine.mem32[CLKDIV0] == 0
    machine.mem32[CLKDIV0] = 9 << 16
    rp2.StateMachine(4, _high_forever(rp2), sideset_base=machine.Pin(CLK))
    assert machine.mem32[CLKDIV0] == 1 << 16


def test_a_fractional_divider_or_one_changed_while_running_is_refused():
    board, mods = _rig()
    rp2, machine = mods["rp2"], mods["machine"]
    sm = rp2.StateMachine(4, _high_forever(rp2), sideset_base=machine.Pin(CLK))
    with pytest.raises(BoardError, match="FRAC"):
        machine.mem32[CLKDIV0] = (1 << 16) | (1 << 8)
    with pytest.raises(BoardError, match="reserved"):
        machine.mem32[CLKDIV0] = (1 << 16) | 1
    machine.mem32[CLKDIV0] = 2 << 16
    sm.active(1)
    with pytest.raises(BoardError, match="running"):
        machine.mem32[CLKDIV0] = 3 << 16
    machine.mem32[CLKDIV0 + 0x18] = 3 << 16  # SM1, not running: fine
