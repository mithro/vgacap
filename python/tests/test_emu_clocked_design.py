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
