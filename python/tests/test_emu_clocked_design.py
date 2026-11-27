# SPDX-License-Identifier: Apache-2.0
"""`ClockedDesign`: an exact counter clocked only by the edges really driven.

It is the design behind 4e's clocked capture: every sample must equal the
counter's value for that clock, reset must hold it at 0 for exactly the
reset clocks, and its value must reach the pins `settle` cycles after each
edge. The 4e program here is `_clocked` from `test_emu_pio_sideset.py`.
"""

from __future__ import annotations

import pytest
from test_emu_pio_sideset import _clocked

from ttcap.emu import Board, BoardError, ClockedDesign

PIO1 = 0x50300000
IRQ = PIO1 + 0x030
CLK, RST = 16, 14
UO = list(range(33, 41))


def _board(settle: int = 0, **kwargs) -> Board:
    design = ClockedDesign(UO, clk_gpio=CLK, rst_gpio=RST, settle=settle, **kwargs)
    return Board(design, pulls={RST: 1})


def _uo(board: Board) -> int:
    Pin = board.modules()["machine"].Pin
    return sum(Pin(g).value() << i for i, g in enumerate(UO))


# -- the counter, clocked by the CPU -------------------------------------------


def test_it_counts_rising_edges_and_nothing_else():
    board = _board()
    Pin = board.modules()["machine"].Pin
    clk = Pin(CLK, Pin.OUT, value=0)
    board.advance(10_000)
    assert _uo(board) == 0
    for n in range(1, 6):
        clk.value(1)
        assert _uo(board) == n
        clk.value(1)  # no edge
        board.advance(1000)
        clk.value(0)
        assert _uo(board) == n


def test_reset_is_synchronous_to_the_clock():
    board = _board()
    Pin = board.modules()["machine"].Pin
    clk = Pin(CLK, Pin.OUT, value=0)
    for _ in range(5):
        clk.value(1)
        clk.value(0)
    rst = Pin(RST, Pin.OUT, value=0)
    board.advance(1000)
    assert _uo(board) == 5  # held in reset, but no edge yet
    clk.value(1)
    clk.value(0)
    assert _uo(board) == 0
    clk.value(1)
    clk.value(0)
    assert _uo(board) == 0
    # Released by the external pull-up against the internal pull-down.
    rst.init(Pin.IN, pull=Pin.PULL_DOWN)
    assert Pin(RST).value() == 1
    for n in (1, 2):
        clk.value(1)
        clk.value(0)
        assert _uo(board) == n


def test_it_wraps_at_256():
    board = _board()
    Pin = board.modules()["machine"].Pin
    clk = Pin(CLK, Pin.OUT, value=0)
    for _ in range(257):
        clk.value(1)
        clk.value(0)
    assert _uo(board) == 1


@pytest.mark.parametrize("settle", [0, 1, 7])
def test_its_value_reaches_the_pins_settle_cycles_after_the_edge(settle):
    board = _board(settle)
    Pin = board.modules()["machine"].Pin
    clk = Pin(CLK, Pin.OUT, value=0)
    clk.value(1)
    r = board.now
    design = board.wave
    assert board.pio_rises[CLK] == [r]
    assert (design.count_at(r + settle - 1), design.count_at(r + settle)) == (0, 1)
    assert design.next_time(33, 1, r - 5) == r + settle


def test_initial_is_the_value_before_the_first_edge_and_after_a_load():
    board = _board(initial=0x5A)
    Pin = board.modules()["machine"].Pin
    clk = Pin(CLK, Pin.OUT, value=0)
    assert _uo(board) == 0x5A
    clk.value(1)
    clk.value(0)
    assert _uo(board) == 0x5B
    board.wave.configure()
    assert _uo(board) == 0x5A
    clk.value(1)
    assert _uo(board) == 0x5B


def test_a_floating_rst_n_is_refused_at_the_edge():
    design = ClockedDesign(UO, clk_gpio=CLK, rst_gpio=RST)
    board = Board(design)  # no external pull-up
    Pin = board.modules()["machine"].Pin
    Pin(RST, Pin.IN)
    clk = Pin(CLK, Pin.OUT, value=0)
    clk.value(1)
    with pytest.raises(BoardError, match="it floats"):
        _uo(board)


# -- the 4e clocked program ----------------------------------------------------


def _run_clocked(board: Board, high: int, low: int, clocks: int, reset_clocks: int,
                 hold: int = 0) -> list[int]:
    """The 4e board loop, by hand: hold rst_n low and the clock low, run the
    program, release rst_n at the IRQ handshake, collect the samples."""
    mods = board.modules()
    rp2, machine = mods["rp2"], mods["machine"]
    Pin, mem32 = machine.Pin, machine.mem32
    rst = Pin(RST, Pin.OUT, value=0)
    Pin(CLK, Pin.OUT, value=0)
    rp2.PIO(1).gpio_base(16)
    sm = rp2.StateMachine(4, _clocked(rp2, high, low), sideset_base=Pin(CLK), in_base=Pin(33))
    sm.put(reset_clocks - 1)
    sm.put(clocks - reset_clocks - 1)
    sm.active(1)
    words = []
    pio_sm = board.blocks[1].sms[0]
    while not mem32[IRQ] & 1:
        board.advance(100)
        while pio_sm.rx:
            words.append(sm.get())
    board.advance(hold)
    rst.init(Pin.IN, pull=Pin.PULL_DOWN)  # tt.reset_project(False)
    assert Pin(RST).value() == 1
    mem32[IRQ] = 1
    while len(words) < clocks // 4:
        words.append(sm.get())
    board.advance(1000)
    assert len(board.pio_rises[CLK]) == clocks
    return [(w >> (8 * (3 - i))) & 0xFF for w in words for i in range(4)]


@pytest.mark.parametrize("hold", [0, 10_000])
@pytest.mark.parametrize("clocks, reset_clocks", [(4, 1), (12, 10), (16, 3)])
@pytest.mark.parametrize("high, settle", [(0, 0), (0, 1), (3, 0), (3, 4), (15, 16)])
def test_every_sample_is_the_counter_after_its_clock(high, settle, clocks, reset_clocks, hold):
    # Sample k is the `in pins` H + 1 cycles after rising edge k, so with
    # settle <= H + 1 the counter after k + 1 edges is on the pins: 0 for
    # the reset edges 0 .. reset_clocks - 1, then 1, 2, ...
    board = _board(settle)
    samples = _run_clocked(board, high, high, clocks, reset_clocks, hold)
    want = [max(0, k + 1 - reset_clocks) for k in range(clocks)]
    assert samples == want
    assert samples[:reset_clocks] == [0] * reset_clocks
    assert samples[reset_clocks] == 1


@pytest.mark.parametrize("high, settle", [(0, 2), (3, 5), (15, 17)])
def test_a_settle_longer_than_the_high_phase_shows_the_clock_before(high, settle):
    # One cycle too slow for the sample point: each sample shows the value
    # after k edges, not k + 1.
    board = _board(settle)
    clocks, reset_clocks = 12, 3
    samples = _run_clocked(board, high, high, clocks, reset_clocks)
    assert samples == [max(0, k - reset_clocks) for k in range(clocks)]


def test_rst_n_written_in_the_cycle_of_an_edge_counts_for_that_edge():
    # A Pin init costs no time, so it can land in the cycle of the edge
    # just driven; the design reads rst_n as the pad reads in that cycle,
    # even when that edge's value was already looked at.
    board = _board()
    Pin = board.modules()["machine"].Pin
    clk = Pin(CLK, Pin.OUT, value=0)
    clk.value(1)
    clk.value(0)
    clk.value(1)
    edge = board.now
    assert board.wave.count_at(edge) == 2
    Pin(RST, Pin.OUT, value=0)
    assert board.now == edge
    assert board.wave.count_at(edge) == 0


def test_the_in_window_shows_the_clock_pad_the_cpu_drives():
    board = _board()
    Pin = board.modules()["machine"].Pin
    clk = Pin(CLK, Pin.OUT, value=1)
    assert board.wave.window(CLK, 1, board.now) & 1 == 1
    clk.value(0)
    assert board.wave.window(CLK, 1, board.now) & 1 == 0


def _free_running(rp2):
    # Samples uo_out every clock and never stalls (push noblock).
    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW, in_shiftdir=rp2.PIO.SHIFT_LEFT,
                 autopush=False, push_thresh=32)
    def p():
        nop().side(1)  # noqa: F821
        in_(pins, 8).side(1)  # noqa: F821
        push(noblock).side(0)  # noqa: F821

    return p


@pytest.mark.parametrize("step", [24_000, 100])
def test_each_edge_is_counted_once_however_long_the_advance(step, monkeypatch):
    # 8,000 edges in one advance cost what they cost in advances of 100
    # cycles: the design looks rst_n up once per edge, not once per edge
    # per sample (which was 32 million lookups in one advance).
    board = _board()
    mods = board.modules()
    rp2, Pin = mods["rp2"], mods["machine"].Pin
    rp2.PIO(1).gpio_base(16)
    sm = rp2.StateMachine(4, _free_running(rp2), sideset_base=Pin(CLK), in_base=Pin(33))
    lookups = []
    real = board.pad_level

    def counting(gpio, cycle):
        if gpio == RST:
            lookups.append(cycle)
        return real(gpio, cycle)

    monkeypatch.setattr(board, "pad_level", counting)
    sm.active(1)
    for _ in range(24_000 // step):
        board.advance(step)
    edges = len(board.pio_rises[CLK])
    assert edges == 8_000
    assert len(lookups) <= edges + 2
    assert board.wave.count_at(board.now) == edges % 256


def test_rst_n_driven_by_a_later_state_machine_resets_edges_already_counted():
    # Within one advance the clock's state machine runs, and samples, before
    # the one that drives rst_n low; the edges it counted after that drive
    # must be counted again.
    design = ClockedDesign(UO, clk_gpio=CLK, rst_gpio=17)
    board = Board(design)
    mods = board.modules()
    rp2, Pin = mods["rp2"], mods["machine"].Pin
    rp2.PIO(1).gpio_base(16)
    Pin(17, Pin.OUT, value=1)
    clock = rp2.StateMachine(4, _free_running(rp2), sideset_base=Pin(CLK), in_base=Pin(33))

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_HIGH)
    def reset_at_100():  # low from 100 cycles after the start
        nop().side(1)[15]  # noqa: F821
        nop().side(1)[15]  # noqa: F821
        nop().side(1)[15]  # noqa: F821
        nop().side(1)[15]  # noqa: F821
        nop().side(1)[15]  # noqa: F821
        nop().side(1)[15]  # noqa: F821
        nop().side(1)[3]  # noqa: F821
        label("low")  # noqa: F821
        jmp("low").side(0)  # noqa: F821

    reset = rp2.StateMachine(5, reset_at_100, sideset_base=Pin(17))
    clock.active(1)
    reset.active(1)
    board.advance(300)
    rises = board.pio_rises[CLK]
    low_from = board.pio_rises[CLK][0] + 100
    # An edge every 3 cycles: one lands in the cycle before rst_n falls, and
    # counts, since rst_n is read in the edge's own cycle.
    assert low_from - 1 in rises
    assert board.pio_pin(17, low_from) == 0 and board.pio_pin(17, low_from - 1) == 1
    assert design.count_at(board.now) == 0
    assert [design.count(i + 1) for i in range(len(rises))] == [
        i + 1 if r < low_from else 0 for i, r in enumerate(rises)]


def test_a_design_with_no_board_is_refused_not_a_none_error():
    design = ClockedDesign(UO, clk_gpio=CLK, rst_gpio=RST)
    with pytest.raises(BoardError, match="no Board"):
        design.value(CLK, 0)
    with pytest.raises(BoardError, match="no Board"):
        design.window(CLK, 1, 0)
    design.clock_rises.append(5)
    with pytest.raises(BoardError, match="no Board"):
        design.count_at(10)


def test_one_design_cannot_drive_two_boards():
    design = ClockedDesign(UO, clk_gpio=CLK, rst_gpio=RST)
    Board(design)
    with pytest.raises(BoardError, match="already drives"):
        Board(design)


def test_rst_n_falling_the_cycle_after_an_edge_does_not_reset_that_edge():
    # rst_n is read in the edge's own cycle, not the next one.
    board = _board()
    Pin = board.modules()["machine"].Pin
    clk = Pin(CLK, Pin.OUT, value=0)
    clk.value(1)
    edge = board.now
    board.advance(1)
    Pin(RST, Pin.OUT, value=0)  # a Pin init costs no time: lands at edge + 1
    assert board.pad_level(RST, edge) == 1 and board.pad_level(RST, edge + 1) == 0
    assert board.wave.count_at(board.now) == 1
