# SPDX-License-Identifier: Apache-2.0
"""The emulator's PIO side-set, `push iffull`, `irq` and IRQ register.

Each test assembles a tiny program with MicroPython's own `asm_pio` and
checks the pins, FIFOs and IRQ flags cycle by cycle. The semantics are the
RP2040 datasheet's (3.4 "Instruction set", 3.5.1 "Side-set") and RP2350's
(11.4, 11.5.1), which agree on everything used here:

* side-set takes effect when an instruction issues, even one that then
  stalls, and holds until an instruction with a side-set changes it;
* delay cycles run after the instruction completes, never while it stalls;
* the delay/side-set field (bits 12:8) is SIDESET_COUNT bits of side-set
  at the top (the MSB an enable bit when EXECCTRL.SIDE_EN is set) and the
  rest delay;
* `push iffull` does nothing until the ISR shift count reaches the push
  threshold; `block` stalls while the RX FIFO is full;
* `irq` sets a flag, `irq clear` clears it, `irq wait` sets it and stalls
  until something else clears it; the CPU clears flags by writing 1s to
  the block's IRQ register (offset 0x030).
"""

from __future__ import annotations

import pytest

from ttcap.emu import Board, BoardError, Waveform

PIO1 = 0x50300000
IRQ = PIO1 + 0x030
CLK = 16


def _board(chip: str = "rp2350") -> Board:
    # uo_out on 33..40 counts 0, 1, 2, ... one value per 4 cycles of the
    # (stopped, once the PIO takes GPIO 16) waveform clock.
    wave = Waveform(bytes(range(256)), uo_gpios=list(range(33, 41)), clk_gpio=CLK, period=4)
    return Board(wave, chip=chip)


def _sm(board: Board, prog, sm_num: int = 0, **kwargs):
    """StateMachine `sm_num` of PIO1 (window at 16) running `prog`."""
    mods = board.modules()
    rp2 = mods["rp2"]
    if board.chip == "rp2350" and board.blocks[1].base != 16:
        rp2.PIO(1).gpio_base(16)
    sm = rp2.StateMachine(4 + sm_num, prog, **kwargs)
    return sm, board.blocks[1].sms[sm_num]


def _pins(board: Board, gpio: int, start: int, n: int) -> list[int]:
    return [board.pio_pin(gpio, c) for c in range(start, start + n)]


def _toggle(rp2, high: int, low: int):
    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        nop().side(1)[high]  # noqa: F821
        nop().side(0)[low]  # noqa: F821

    return p


# -- side-set and the delay field -------------------------------------------


@pytest.mark.parametrize("high, low", [(0, 0), (15, 15), (0, 15), (15, 0)])
def test_side_set_high_and_low_phases_at_their_extremes(high, low):
    # One non-optional side-set bit leaves 4 delay bits: 0..15.
    board = _board()
    rp2 = board.modules()["rp2"]
    sm, _ = _sm(board, _toggle(rp2, high, low), sideset_base=CLK)
    start = board.now
    sm.active(1)
    period = high + low + 2
    board.advance(3 * period)

    want = ([1] * (high + 1) + [0] * (low + 1)) * 3
    assert _pins(board, CLK, start, 3 * period) == want


def test_the_side_set_bits_come_out_of_the_delay_field():
    # One bit leaves 4 delay bits (max 15); one bit plus the enable bit
    # leaves 3 (max 7). `asm_pio_encode` assembles one instruction without
    # touching this module's globals, which `asm_pio` leaves swapped out
    # when it raises.
    rp2 = _board().modules()["rp2"]
    assert rp2.asm_pio_encode("nop().side(1)[15]", 1) == 0xA042 | 0x1F00
    with pytest.raises(rp2.PIOASMError, match="delay too large"):
        rp2.asm_pio_encode("nop().side(1)[16]", 1)
    assert rp2.asm_pio_encode("nop()[7]", 1, True) == 0xA042 | 0x0700
    with pytest.raises(rp2.PIOASMError, match="delay too large"):
        rp2.asm_pio_encode("nop()[8]", 1, True)


def test_the_initial_side_set_value_is_driven_at_construction():
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_HIGH)
    def p():
        nop().side(0)  # noqa: F821

    board.advance(10)
    _sm(board, p, sideset_base=CLK)
    assert board.pio_pin(CLK, board.now) == 1
    with pytest.raises(BoardError, match="not driven"):
        board.pio_pin(CLK, board.now - 1)


def test_optional_side_set_holds_through_instructions_without_one():
    # SIDE_EN: bit 12 enables, bit 11 is the data, 3 delay bits (max 7).
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        nop().side(1)[7]  # noqa: F821
        nop()[7]  # noqa: F821
        nop().side(0)  # noqa: F821
        nop()  # noqa: F821

    assert [w >> 8 & 0x1F for w in p[0]] == [0x1F, 0x07, 0x10, 0x00]
    sm, _ = _sm(board, p, sideset_base=CLK)
    start = board.now
    sm.active(1)
    board.advance(36)
    assert _pins(board, CLK, start, 36) == ([1] * 16 + [0] * 2) * 2


def test_two_side_set_bits_drive_two_consecutive_pins():
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(sideset_init=(rp2.PIO.OUT_LOW, rp2.PIO.OUT_HIGH))
    def p():
        nop().side(2)[7]  # noqa: F821
        nop().side(1)  # noqa: F821

    sm, _ = _sm(board, p, sideset_base=CLK)
    start = board.now
    assert (board.pio_pin(CLK, start), board.pio_pin(CLK + 1, start)) == (0, 1)
    sm.active(1)
    board.advance(18)
    assert _pins(board, CLK, start, 18) == ([0] * 8 + [1]) * 2
    assert _pins(board, CLK + 1, start, 18) == ([1] * 8 + [0]) * 2


def test_side_set_is_held_while_an_instruction_stalls_and_delay_runs_after():
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        nop().side(0)  # noqa: F821
        pull(block).side(1)[3]  # noqa: F821
        nop().side(0)[15]  # noqa: F821

    sm, pio_sm = _sm(board, p, sideset_base=CLK)
    start = board.now
    sm.active(1)
    board.advance(500)
    # The pull issued at start + 1 and drives the clock high every cycle
    # it is stalled, with the delay not yet started.
    assert _pins(board, CLK, start, 500) == [0] + [1] * 499
    assert pio_sm.pc == 1
    put_at = board.now
    sm.put(7)
    board.advance(30)
    # It completes in the cycle the word arrives, then 3 delay cycles, and
    # only then does the next instruction take the clock low.
    assert _pins(board, CLK, put_at, 6) == [1, 1, 1, 1, 0, 0]
    assert pio_sm.osr == 7


def test_side_set_on_a_pin_outside_the_gpio_window_is_refused_like_micropython():
    board = _board()
    rp2 = board.modules()["rp2"]
    with pytest.raises(ValueError, match="sideset_base not within gpio_base range"):
        _sm(board, _toggle(rp2, 0, 0), sideset_base=3)


def test_side_set_base_takes_a_pin_or_a_number_and_the_clock_pad():
    board = _board()
    rp2 = board.modules()["rp2"]
    machine = board.modules()["machine"]
    sm, _ = _sm(board, _toggle(rp2, 0, 0), sideset_base=machine.Pin(CLK))
    # The pad's FUNCSEL is now PIO: the firmware's PWM clock no longer
    # reaches it.
    assert board.wave.clock_stopped
    assert board.pio_pin(CLK, board.now) == 0


def test_reading_a_side_set_pin_back_is_refused_not_guessed():
    board = _board()
    rp2 = board.modules()["rp2"]
    machine = board.modules()["machine"]
    _sm(board, _toggle(rp2, 0, 0), sideset_base=CLK)
    with pytest.raises(BoardError, match="driven by PIO"):
        machine.Pin(CLK).value()
    with pytest.raises(BoardError, match="driven by PIO"):
        machine.Pin(CLK, machine.Pin.IN)

    @rp2.asm_pio()
    def reader():
        in_(pins, 1)  # noqa: F821

    sm2, _ = _sm(board, reader, sm_num=1, in_base=machine.Pin(CLK))
    sm2.active(1)
    with pytest.raises(BoardError, match="driven by PIO"):
        board.advance(4)


def test_two_state_machines_driving_one_pin_are_refused():
    board = _board()
    rp2 = board.modules()["rp2"]
    _sm(board, _toggle(rp2, 0, 0), sideset_base=CLK)
    with pytest.raises(BoardError, match="another state machine"):
        _sm(board, _toggle(rp2, 1, 1), sm_num=1, sideset_base=CLK)


def test_optional_side_set_without_a_sideset_base_is_refused():
    # MicroPython then leaves SIDESET_COUNT 0 with SIDE_EN set; what the
    # hardware makes of that is not documented.
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        nop().side(1)  # noqa: F821
        nop()  # noqa: F821

    with pytest.raises(BoardError, match="sideset_base"):
        _sm(board, p)


def test_without_a_sideset_base_the_side_set_bits_are_delay():
    # MicroPython sets SIDESET_COUNT only when sideset_base is given, so
    # all five bits are delay: `.side(1)` with one bit is 16 more cycles.
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        set(x, 1).side(1)  # noqa: F821
        in_(x, 32).side(0)  # noqa: F821
        push(noblock).side(0)  # noqa: F821

    sm, pio_sm = _sm(board, p)
    sm.active(1)
    board.advance(17)
    assert pio_sm.isr_count == 0  # `in` has not issued yet
    board.advance(1)
    assert pio_sm.isr_count == 32
    assert CLK not in board.pio_pins and not board.wave.clock_stopped


def test_side_set_to_pindirs_is_refused():
    # MicroPython's asm_pio never sets EXECCTRL.SIDE_PINDIR (bit 29).
    board = _board()
    rp2 = board.modules()["rp2"]
    prog = _toggle(rp2, 0, 0)
    prog[3] |= 1 << 29
    with pytest.raises(BoardError, match="SIDE_PINDIR"):
        _sm(board, prog, sideset_base=CLK)


# -- push iffull -------------------------------------------------------------


@pytest.mark.parametrize("bits, pushed", [(7, False), (8, True)])
def test_push_iffull_pushes_only_at_the_threshold(bits, pushed):
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(in_shiftdir=rp2.PIO.SHIFT_LEFT, autopush=False, push_thresh=8)
    def p():
        set(x, 31)  # noqa: F821
        in_(x, bits)  # noqa: F821
        push(iffull, block)  # noqa: F821
        label("end")  # noqa: F821
        jmp("end")  # noqa: F821

    sm, pio_sm = _sm(board, p)
    sm.active(1)
    board.advance(3)
    assert pio_sm.pc == 3  # iffull with nothing to do still takes a cycle
    if pushed:
        assert pio_sm.rx == [0x1F] and pio_sm.isr_count == 0
    else:
        assert pio_sm.rx == [] and (pio_sm.isr, pio_sm.isr_count) == (0x1F, 7)


def test_push_iffull_counts_shifts_across_instructions_to_32():
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(in_shiftdir=rp2.PIO.SHIFT_LEFT, autopush=False, push_thresh=32)
    def p():
        set(x, 5)  # noqa: F821
        label("loop")  # noqa: F821
        in_(x, 8)  # noqa: F821
        push(iffull, block)  # noqa: F821
        jmp("loop")  # noqa: F821

    sm, pio_sm = _sm(board, p)
    sm.active(1)
    board.advance(1 + 3 * 3)
    assert pio_sm.rx == [] and pio_sm.isr_count == 24
    board.advance(3)
    assert pio_sm.rx == [0x05050505]


def test_push_iffull_block_stalls_on_a_full_fifo_with_the_clock_held_low():
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW, in_shiftdir=rp2.PIO.SHIFT_LEFT,
                 autopush=False, push_thresh=32)
    def p():
        label("loop")  # noqa: F821
        in_(x, 32).side(1)  # noqa: F821
        push(iffull, block).side(0)[2]  # noqa: F821
        jmp("loop").side(0)  # noqa: F821

    sm, pio_sm = _sm(board, p, sideset_base=CLK)
    start = board.now
    sm.active(1)
    board.advance(200)
    # Four words fill the RX FIFO (5 cycles each); the fifth push stalls
    # low, delay not started.
    assert len(pio_sm.rx) == 4 and pio_sm.pc == 1
    assert board.blocks[1].fdebug & (1 << 0)  # RXSTALL
    assert _pins(board, CLK, start, 22) == ([1] + [0] * 4) * 4 + [1, 0]
    assert _pins(board, CLK, start + 21, 179) == [0] * 179
    freed = board.now
    assert sm.get() == 0
    board.advance(5)
    # It pushes in the cycle the FIFO has room, waits its 2 delay cycles,
    # jumps, and the clock rises again.
    assert _pins(board, CLK, freed, 5) == [0, 0, 0, 0, 1]
    assert len(pio_sm.rx) == 4


