# SPDX-License-Identifier: Apache-2.0
"""The emulator's CPU-driven pins: `machine.Pin` modes, values and pulls.

What is copied from MicroPython v1.24.0 (`ports/rp2/machine_pin.c`,
`machine_pin_obj_init_helper`, and `mphalport.h`):

* `Pin(n, Pin.OUT, value=v)` writes the output latch before it makes the
  pad an output; `Pin(n, Pin.OUT)` keeps the latch it had;
* any mode (IN or OUT) sets the pad's FUNCSEL to SIO, so it takes the pad
  from PIO (`mp_hal_pin_input` / `mp_hal_pin_output`), and a bare `Pin(n)`
  touches nothing;
* the pull is set every time a mode is given, None meaning no pull;
* `Pin.value()` is `gpio_get()`, the pad's input level whatever drives it,
  PIO included; `value(v)` is `gpio_put()`, the latch, which reaches the pad
  only while it is an SIO output.

An external pull (`Board(pulls=...)`) beats the internal one, as the demo
board's 10k pull-up on `rst_n` beats the RP2's pull-down (fpga-4,
2026-11-27). A pad nothing configured keeps its reset pull-down.
"""

from __future__ import annotations

import pytest

from ttcap.emu import Board, BoardError, Waveform

CLK = 16
RST = 14


def _board(**kwargs) -> Board:
    wave = Waveform(bytes(range(256)), uo_gpios=list(range(33, 41)), clk_gpio=CLK, period=4)
    return Board(wave, **kwargs)


def _pin(board: Board):
    return board.modules()["machine"].Pin


# -- outputs -------------------------------------------------------------------


def test_an_output_drives_its_value_and_reads_it_back():
    board = _board()
    Pin = _pin(board)
    p = Pin(5, Pin.OUT, value=1)
    assert p.value() == 1
    p.value(0)
    assert p.value() == 0
    p(1)
    assert p() == 1
    p.low()
    assert Pin(5).value() == 0
    p.high()
    assert Pin(5).value() == 1


def test_an_output_without_a_value_keeps_the_latch():
    board = _board()
    Pin = _pin(board)
    p = Pin(5, Pin.IN, Pin.PULL_DOWN)
    p.value(1)  # gpio_put on an input: the latch only
    assert p.value() == 0
    Pin(5, Pin.OUT)
    assert p.value() == 1


def test_pin_init_reconfigures_the_pad():
    board = _board()
    Pin = _pin(board)
    p = Pin(5, Pin.OUT, value=1)
    p.init(Pin.IN, pull=Pin.PULL_DOWN)
    assert p.value() == 0
    p.init(Pin.OUT)
    assert p.value() == 1


def test_driving_a_pin_the_design_drives_is_refused():
    board = _board()
    Pin = _pin(board)
    with pytest.raises(BoardError, match="uo_out"):
        Pin(33, Pin.OUT, value=0)


# -- pulls -----------------------------------------------------------------------


@pytest.mark.parametrize("pull, level", [("PULL_UP", 1), ("PULL_DOWN", 0)])
def test_an_input_reads_its_pull(pull, level):
    board = _board()
    Pin = _pin(board)
    assert Pin(5, Pin.IN, getattr(Pin, pull)).value() == level


def test_an_external_pull_up_beats_the_internal_pull_down():
    board = _board(pulls={RST: 1})
    Pin = _pin(board)
    assert Pin(RST, Pin.IN, Pin.PULL_DOWN).value() == 1
    Pin(RST, Pin.OUT, value=0)
    assert Pin(RST).value() == 0
    Pin(RST, Pin.IN, pull=Pin.PULL_DOWN)
    assert Pin(RST).value() == 1


def test_a_floating_input_is_refused_not_guessed():
    board = _board()
    Pin = _pin(board)
    p = Pin(5, Pin.IN)
    with pytest.raises(BoardError, match="it floats"):
        p.value()


def test_an_unconfigured_pad_keeps_its_reset_pull_down():
    board = _board(pulls={RST: 1})
    Pin = _pin(board)
    assert Pin(5).value() == 0
    assert Pin(RST).value() == 1


# -- the clock pad and PIO ---------------------------------------------------------


def _toggle(rp2):
    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        nop().side(1)[1]  # noqa: F821
        nop().side(0)[1]  # noqa: F821

    return p


def _clock_sm(board: Board):
    mods = board.modules()
    rp2 = mods["rp2"]
    rp2.PIO(1).gpio_base(16)
    sm = rp2.StateMachine(4, _toggle(rp2), sideset_base=CLK)
    return sm, board.blocks[1].sms[0]


def test_driving_the_clock_pad_low_holds_it_low():
    board = _board()
    Pin = _pin(board)
    Pin(CLK, Pin.OUT, value=0)
    board.advance(100)
    assert Pin(CLK).value() == 0
    assert board.wave.clock_stopped


def test_the_cpu_reads_a_pin_pio_drives():
    board = _board()
    Pin = _pin(board)
    sm, _ = _clock_sm(board)
    start = board.now
    sm.active(1)
    levels = []
    for _ in range(8):
        levels.append(Pin(CLK).value())
    # Each read costs pin_read_cycles, then reads the pad at board.now. No
    # state machine has issued an instruction in that cycle yet, so the
    # level is the one driven in the cycle before.
    want = [board.pio_pin(CLK, start + (i + 1) * board.pin_read_cycles - 1) for i in range(8)]
    assert levels == want
    assert set(levels) == {0, 1}


def test_a_stopped_state_machine_keeps_driving_its_last_value():
    board = _board()
    Pin = _pin(board)
    sm, _ = _clock_sm(board)
    sm.active(1)
    board.advance(2)  # nop().side(1)[1]: high for two cycles
    sm.active(0)
    board.advance(1000)
    assert Pin(CLK).value() == 1


@pytest.mark.parametrize("mode", ["OUT", "IN"])
def test_a_pin_re_init_takes_the_pad_back_from_pio(mode):
    board = _board()
    Pin = _pin(board)
    sm, _ = _clock_sm(board)
    sm.active(1)
    board.advance(40)
    rises = list(board.pio_rises[CLK])
    released = board.now
    if mode == "OUT":
        Pin(CLK, Pin.OUT, value=0)
    else:
        Pin(CLK, Pin.IN, Pin.PULL_DOWN)
    board.advance(1000)
    # PIO still runs, but its side-set no longer reaches the pad, so the
    # design sees no more edges and the pad reads what SIO makes it.
    assert board.pio_rises[CLK] == rises
    assert Pin(CLK).value() == 0
    assert board.pad_level(CLK, released - 1) == board.pio_pin(CLK, released - 1)
    assert board.pad_level(CLK, released) == 0


def test_cpu_edges_on_a_clock_pio_released_clock_the_design():
    board = _board()
    Pin = _pin(board)
    sm, _ = _clock_sm(board)
    sm.active(1)
    board.advance(3)  # rises at the start only: high 2, low 2
    n = len(board.pio_rises[CLK])
    sm.active(0)
    clk = Pin(CLK, Pin.OUT, value=0)
    clk.value(1)
    at = board.now
    clk.value(0)
    assert board.pio_rises[CLK][n:] == [at]
    assert board.wave.clock_rises is board.pio_rises[CLK]


def test_side_set_onto_a_pad_taken_back_from_pio_is_refused():
    board = _board()
    Pin = _pin(board)
    sm, _ = _clock_sm(board)
    Pin(CLK, Pin.OUT, value=0)
    rp2 = board.modules()["rp2"]
    with pytest.raises(BoardError, match="taken back"):
        rp2.StateMachine(4, _toggle(rp2), sideset_base=CLK)



def test_writing_the_latch_of_a_pad_pio_drives_changes_nothing():
    # gpio_put reaches the pad only through SIO; PIO keeps it, and no edge.
    board = _board()
    Pin = _pin(board)
    Pin(CLK, Pin.OUT, value=0)  # 4d holds it low before PIO takes it (4e)
    sm, _ = _clock_sm(board)
    sm.active(1)
    board.advance(3)  # high, high, low: PIO holds it low from here
    sm.active(0)
    rises = list(board.pio_rises[CLK])
    Pin(CLK).value(1)
    assert board.pio_rises[CLK] == rises
    assert Pin(CLK).value() == 0
    assert board.latch[CLK] == 1


def test_init_with_only_a_pull_keeps_the_mode():
    # machine_pin.c: with no mode, FUNCSEL and direction are left alone and
    # only the pull is set -- what the SDK's StandardPin.pull setter does
    # (standard.py:76-79).
    board = _board()
    Pin = _pin(board)
    p = Pin(5, Pin.OUT, value=0)
    p.init(pull=Pin.PULL_UP)
    assert board.pads[5] == (Pin.OUT, Pin.PULL_UP)
    assert p.value() == 0


def test_a_release_and_a_rise_in_one_pin_init_is_an_edge():
    # The release takes effect in the cycle of the init, so the level SIO
    # drives in that same init is compared with PIO's last one: a rise.
    board = _board()
    Pin = _pin(board)
    sm, _ = _clock_sm(board)
    sm.active(1)
    board.advance(3)  # PIO holds the clock low
    sm.active(0)
    n = len(board.pio_rises[CLK])
    Pin(CLK, Pin.OUT, value=1)
    assert board.pio_rises[CLK][n:] == [board.now]
    assert board.pad_level(CLK, board.now) == 1


def test_once_the_cpu_has_the_clock_another_state_machine_may_read_uo_out():
    # No state machine drives the clock any more, so none can run ahead of
    # its edges: the CPU's land between advances.
    board = _board()
    Pin = _pin(board)
    rp2 = board.modules()["rp2"]
    sm, _ = _clock_sm(board)
    sm.active(1)
    board.advance(3)
    sm.active(0)
    Pin(CLK, Pin.OUT, value=0)

    @rp2.asm_pio()
    def reader():
        in_(pins, 8)  # noqa: F821
        push(noblock)  # noqa: F821

    sm2 = rp2.StateMachine(5, reader, in_base=Pin(33))
    sm2.active(1)
    board.advance(20)
    assert sm2.rx_fifo() > 0


def test_the_clock_pad_as_an_input_with_no_pull_reads_0_as_on_fpga_1():
    board = _board()
    Pin = _pin(board)
    assert Pin(CLK, Pin.IN).value() == 0
