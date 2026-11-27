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


@pytest.mark.parametrize("freq", [1_000_000, 133_000_000, 0])
def test_a_state_machine_clock_divider_is_refused(freq):
    # The emulator runs every state machine at the system clock; a `freq`
    # would make its pin timing wrong by the divider, silently.
    board = _board()
    rp2 = board.modules()["rp2"]
    with pytest.raises(BoardError, match="clock divider is not emulated"):
        _sm(board, _toggle(rp2, 0, 0), freq=freq, sideset_base=CLK)
    with pytest.raises(BoardError, match="clock divider is not emulated"):
        rp2.StateMachine(5, _toggle(rp2, 0, 0), freq, sideset_base=CLK)


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
    # MicroPython v1.24.0's asm_pio, the vendored one the boards run, never
    # sets EXECCTRL.SIDE_PINDIR (bit 29); current MicroPython has side_pindir=.
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
    start = board.now
    sm.active(1)
    board.advance(3)
    # set, in, push: one cycle each, whether or not the push pushes, so the
    # jmp is due at exactly start + 3 and has not issued yet.
    assert (pio_sm.pc, pio_sm.now) == (3, start + 3)
    board.advance(1)
    assert (pio_sm.pc, pio_sm.now) == (3, start + 4)
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


# -- irq and the IRQ register -------------------------------------------------


def test_irq_sets_a_flag_the_cpu_clears_by_writing_one():
    board = _board()
    rp2 = board.modules()["rp2"]
    mem32 = board.modules()["machine"].mem32

    @rp2.asm_pio()
    def p():
        irq(5)  # noqa: F821
        label("end")  # noqa: F821
        jmp("end")  # noqa: F821

    sm, _ = _sm(board, p)
    sm.active(1)
    board.advance(2)
    assert mem32[IRQ] == 1 << 5
    mem32[IRQ] = 1 << 4  # write-1-to-clear: other bits untouched
    assert mem32[IRQ] == 1 << 5
    mem32[IRQ] = 1 << 5
    assert mem32[IRQ] == 0


def test_irq_clear_clears_the_flag():
    board = _board()
    rp2 = board.modules()["rp2"]
    mem32 = board.modules()["machine"].mem32

    @rp2.asm_pio()
    def p():
        irq(2)  # noqa: F821
        irq(3)  # noqa: F821
        irq(clear, 2)  # noqa: F821
        label("end")  # noqa: F821
        jmp("end")  # noqa: F821

    sm, _ = _sm(board, p)
    sm.active(1)
    board.advance(4)
    assert mem32[IRQ] == 1 << 3


@pytest.mark.parametrize("chip", ["rp2040", "rp2350"])
@pytest.mark.parametrize("sm_num, index, flag", [(0, 1, 1), (2, 1, 3), (3, 2, 1), (1, 7, 4)])
def test_relative_irq_adds_the_state_machine_number_modulo_4(chip, sm_num, index, flag):
    board = _board(chip)
    rp2 = board.modules()["rp2"]
    mem32 = board.modules()["machine"].mem32

    @rp2.asm_pio()
    def p():
        irq(rel(index))  # noqa: F821
        label("end")  # noqa: F821
        jmp("end")  # noqa: F821

    sm, _ = _sm(board, p, sm_num=sm_num)
    sm.active(1)
    board.advance(2)
    assert mem32[IRQ] == 1 << flag


def test_irq_to_another_pio_block_is_refused():
    # RP2350 IdxMode 01 (PREV) / 11 (NEXT): index 8..15 without `rel`.
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio()
    def p():
        irq(8)  # noqa: F821

    with pytest.raises(BoardError, match="not emulated"):
        _sm(board, p)


@pytest.mark.parametrize("delay", [0, 7])
def test_irq_wait_stalls_with_the_clock_low_until_a_w1c_write(delay):
    board = _board()
    rp2 = board.modules()["rp2"]
    mem32 = board.modules()["machine"].mem32

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_HIGH)
    def p():
        irq(block, 0).side(0)[delay]  # noqa: F821
        label("end")  # noqa: F821
        jmp("end").side(1)  # noqa: F821

    sm, pio_sm = _sm(board, p, sideset_base=CLK)
    start = board.now
    sm.active(1)
    for _ in range(5):
        board.advance(1000)
        assert mem32[IRQ] == 1 and pio_sm.pc == 0
    assert _pins(board, CLK, start, 5000) == [0] * 5000
    cleared = board.now
    mem32[IRQ] = 1
    board.advance(20)
    # Completes in the cycle it sees the flag clear; the delay runs after.
    assert _pins(board, CLK, cleared, delay + 3) == [0] * (delay + 1) + [1, 1]
    assert mem32[IRQ] == 0 and pio_sm.pc == 1


def test_irq_wait_does_not_raise_the_flag_again_while_it_waits():
    board = _board()
    rp2 = board.modules()["rp2"]
    mem32 = board.modules()["machine"].mem32

    @rp2.asm_pio()
    def p():
        irq(block, 0)  # noqa: F821
        irq(block, 1)  # noqa: F821

    sm, pio_sm = _sm(board, p)
    sm.active(1)
    board.advance(10)
    mem32[IRQ] = 1
    board.advance(10)
    assert mem32[IRQ] == 1 << 1 and pio_sm.pc == 1


def _irq_loop(rp2, kind, flag):
    @rp2.asm_pio()
    def p():
        label("top")  # noqa: F821
        if kind == "wait":
            irq(block, flag)  # noqa: F821
        else:
            irq(clear, flag)  # noqa: F821
        jmp("top")  # noqa: F821

    return p


def test_an_irq_flag_shared_by_two_running_state_machines_is_refused():
    # State machines run one after another within each advance, so a flag
    # one of them sets or clears would reach the other at the wrong cycle.
    board = _board()
    rp2 = board.modules()["rp2"]
    sm0, _ = _sm(board, _irq_loop(rp2, "wait", 0))
    sm1, _ = _sm(board, _irq_loop(rp2, "clear", 0), sm_num=1)
    sm0.active(1)
    sm1.active(1)
    with pytest.raises(BoardError, match="IRQ flag 0 .* another running state machine"):
        board.advance(10)


def test_irq_flags_used_by_one_state_machine_each_are_fine():
    board = _board()
    rp2 = board.modules()["rp2"]
    mem32 = board.modules()["machine"].mem32
    sm0, _ = _sm(board, _irq_loop(rp2, "wait", 0))
    sm1, _ = _sm(board, _irq_loop(rp2, "clear", 1), sm_num=1)
    sm0.active(1)
    sm1.active(1)
    board.advance(10)
    assert mem32[IRQ] == 1


def test_a_stopped_state_machine_does_not_count_as_sharing_a_flag():
    board = _board()
    rp2 = board.modules()["rp2"]
    mem32 = board.modules()["machine"].mem32
    sm0, _ = _sm(board, _irq_loop(rp2, "wait", 0))
    _sm(board, _irq_loop(rp2, "clear", 0), sm_num=1)  # loaded, never started
    sm0.active(1)
    board.advance(10)
    assert mem32[IRQ] == 1


# -- the clocked program of design note 4e ------------------------------------


def _clocked(rp2, high, low):
    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW, in_shiftdir=rp2.PIO.SHIFT_LEFT,
                 autopush=False, push_thresh=32)
    def clocked():
        pull(block).side(0)  # noqa: F821
        out(x, 32).side(0)  # noqa: F821
        pull(block).side(0)  # noqa: F821
        out(y, 32).side(0)  # noqa: F821
        label("reset")  # noqa: F821
        nop().side(1)[high]  # noqa: F821
        in_(pins, 8).side(1)  # noqa: F821
        push(iffull, block).side(0)[low]  # noqa: F821
        jmp(x_dec, "reset").side(0)  # noqa: F821
        irq(block, 0).side(0)  # noqa: F821
        label("run")  # noqa: F821
        nop().side(1)[high]  # noqa: F821
        in_(pins, 8).side(1)  # noqa: F821
        push(iffull, block).side(0)[low]  # noqa: F821
        jmp(y_dec, "run").side(0)  # noqa: F821
        label("done")  # noqa: F821
        jmp("done").side(0)  # noqa: F821

    return clocked


@pytest.mark.parametrize("hold", [0, 10_000])
@pytest.mark.parametrize("high, low", [(0, 0), (15, 15)])
@pytest.mark.parametrize("clocks, reset_clocks", [(4, 1), (12, 10), (8, 3)])
def test_the_clocked_program_drives_exactly_n_edges(high, low, clocks, reset_clocks, hold):
    board = _board()
    rp2 = board.modules()["rp2"]
    machine = board.modules()["machine"]
    mem32 = board.modules()["machine"].mem32
    sm, pio_sm = _sm(board, _clocked(rp2, high, low), sideset_base=CLK,
                     in_base=machine.Pin(33))
    start = board.now
    sm.put(reset_clocks - 1)
    sm.put(clocks - reset_clocks - 1)
    sm.active(1)
    words = []
    while not mem32[IRQ] & 1:
        board.advance(100)
        while pio_sm.rx:
            words.append(sm.get())
    # Hold the clock at the handshake, as Python releasing rst_n would.
    board.advance(hold)
    handshake = board.now
    mem32[IRQ] = 1
    while len(words) < clocks // 4:
        words.append(sm.get())
    board.advance(1000)
    end = board.now

    trace = _pins(board, CLK, start, end - start)
    rises = [i for i in range(1, len(trace)) if trace[i - 1] == 0 and trace[i] == 1]
    assert len(rises) == clocks
    assert sum(1 for i in rises if start + i < handshake) == reset_clocks
    assert trace[-1] == 0
    # The FIFO never filled, so nothing stalled but the irq handshake.
    assert not board.blocks[1].fdebug & 1
    # Low for the two pulls and two outs, then every phase exactly: high
    # H+2 (nop [H], in), low L+2 (push iffull [L], jmp) -- including the
    # phases whose push is the one in four that pushes. The low phase
    # holding the handshake (push [L], jmp, irq wait) is the only longer
    # one; the last low phase runs to the end of the trace.
    first = rises[0]
    assert trace[:first] == [0] * 4
    phases = []
    for value in trace[first:]:
        if phases and phases[-1][0] == value:
            phases[-1][1] += 1
        else:
            phases.append([value, 1])
    highs = [n for v, n in phases if v == 1]
    lows = [n for v, n in phases if v == 0]
    assert len(highs) == len(lows) == clocks
    assert highs == [high + 2] * clocks
    between = lows[:-1]
    handshake_low = between.pop(reset_clocks - 1)
    assert between == [low + 2] * (clocks - 2)
    assert handshake_low > low + 3
    assert len(words) == clocks // 4 and pio_sm.rx == []
    # The design sees only the side-set clock: uo_out (0, 1, 2, ... one
    # value per rising edge) has taken edge n's value once n rising edges
    # have reached it. Sample k is the `in pins` in the high phase after
    # rising edge k (counting from 0), so after k + 1 edges: uo[k + 1],
    # whatever the phase lengths and however long the handshake holds the
    # clock low. Each word holds four samples, the first in its top byte.
    samples = [(w >> (8 * (3 - i))) & 0xFF for w in words for i in range(4)]
    assert samples == [k + 1 for k in range(clocks)]


def test_wait_on_uo_out_follows_the_side_set_clock():
    # uo[1] = 1 sets bit 0 (GPIO 33) after the first rising edge; bit 1
    # (GPIO 34) waits for uo[2], an edge this program never drives, so that
    # wait holds however long the board runs.
    board = _board()
    rp2 = board.modules()["rp2"]
    mem32 = board.modules()["machine"].mem32

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        nop().side(0)[3]  # noqa: F821
        nop().side(1)[3]  # noqa: F821
        wait(1, gpio, 1).side(0)  # noqa: F821  (GPIO 33 in the window at 16)
        irq(0).side(0)  # noqa: F821
        wait(1, gpio, 2).side(0)  # noqa: F821  (GPIO 34)
        irq(1).side(0)  # noqa: F821
        label("end")  # noqa: F821
        jmp("end").side(0)  # noqa: F821

    sm, pio_sm = _sm(board, p, sideset_base=CLK)
    start = board.now
    sm.active(1)
    board.advance(10_000)
    assert board.pio_rises[CLK] == [start + 4]
    assert mem32[IRQ] == 1 << 0 and pio_sm.pc == 4


def test_reading_uo_out_from_a_state_machine_the_clock_is_not_on_is_refused():
    # The other state machine runs after the clock's within each advance,
    # so it could read uo_out ahead of edges not yet driven.
    board = _board()
    rp2 = board.modules()["rp2"]
    machine = board.modules()["machine"]

    @rp2.asm_pio()
    def reader():
        in_(pins, 8)  # noqa: F821

    clock, _ = _sm(board, _toggle(rp2, 0, 0), sideset_base=CLK)
    sm2, _ = _sm(board, reader, sm_num=1, in_base=machine.Pin(33))
    clock.active(1)
    sm2.active(1)
    with pytest.raises(BoardError, match="clocked by another state machine"):
        board.advance(4)


def test_a_rise_driven_in_the_construction_cycle_is_an_edge():
    # The initial low is driven at construction and the first `.side(1)` in
    # the same emulated cycle; on a board the low lasts until active(1),
    # microseconds later, so the design sees that edge.
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        nop().side(1)[1]  # noqa: F821
        nop().side(0)[1]  # noqa: F821

    sm, _ = _sm(board, p, sideset_base=CLK)
    start = board.now
    sm.active(1)
    board.advance(40)
    assert board.pio_rises[CLK] == [start + 4 * i for i in range(10)]


def _delay_board(delay: int) -> Board:
    wave = Waveform(bytes(range(256)), uo_gpios=list(range(33, 41)), clk_gpio=CLK,
                    period=4, delay=delay)
    return Board(wave)


def test_uo_out_under_a_side_set_clock_follows_the_design_delay():
    # One rising edge at r; uo_out takes uo[1] `delay` = 3 cycles later.
    board = _delay_board(3)
    rp2 = board.modules()["rp2"]
    machine = board.modules()["machine"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        nop().side(0)[3]  # noqa: F821
        nop().side(1)  # noqa: F821
        wait(1, gpio, 1).side(1)  # noqa: F821  (GPIO 33, bit 0 of uo[1])
        label("end")  # noqa: F821
        jmp("end").side(1)  # noqa: F821

    sm, pio_sm = _sm(board, p, sideset_base=CLK, in_base=machine.Pin(33))
    r = board.now + 4
    sm.active(1)
    pcs = {}
    for _ in range(20):
        board.advance(1)
        pcs[board.now] = pio_sm.pc
    assert board.pio_rises[CLK] == [r]
    # The wait issues at r + 1 and completes in cycle r + 3, so the jmp is
    # the instruction due from r + 4.
    assert [pcs[c] for c in range(r + 1, r + 6)] == [2, 2, 2, 3, 3]
    assert pio_sm.read_pins(r + 2, 8) == 0  # uo[0]
    assert pio_sm.read_pins(r + 3, 8) == 1  # uo[1]
    assert board.wave.next_time(33, 1, r) == r + 3


def test_with_no_design_delay_an_edge_shows_in_its_own_cycle():
    # bisect_right: the edge at r already counts at r.
    board = _delay_board(0)
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW)
    def p():
        nop().side(0)[3]  # noqa: F821
        label("end")  # noqa: F821
        jmp("end").side(1)  # noqa: F821

    sm, _ = _sm(board, p, sideset_base=CLK)
    r = board.now + 4
    sm.active(1)
    board.advance(10)
    assert board.pio_rises[CLK] == [r]
    assert (board.wave.clock_index(r - 1), board.wave.clock_index(r)) == (0, 1)


@pytest.mark.parametrize("at, rises", [(0, []), (2, [2])])
def test_a_high_initial_value_on_the_clock_is_an_edge_only_from_low(at, rises):
    # The waveform clock (period 4) is high at cycles 0-1 and low at 2-3.
    # Taking the pad with sideset_init high raises it if it was low.
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_HIGH)
    def p():
        label("end")  # noqa: F821
        jmp("end").side(1)  # noqa: F821

    if at:
        board.advance(at)
    sm, _ = _sm(board, p, sideset_base=CLK)
    sm.active(1)
    board.advance(10)
    assert board.pio_rises[CLK] == rises


def test_a_high_initial_value_driven_low_in_the_same_cycle_is_still_an_edge():
    # The high lasts until active(1) on a board, so the design sees it.
    board = _board()
    rp2 = board.modules()["rp2"]

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_HIGH)
    def p():
        nop().side(0)[3]  # noqa: F821
        nop().side(1)[3]  # noqa: F821

    board.advance(2)
    sm, _ = _sm(board, p, sideset_base=CLK)
    sm.active(1)
    board.advance(13)
    assert board.pio_rises[CLK] == [2, 6, 14]


def test_a_rise_out_of_a_re_constructed_state_machines_initial_value_is_an_edge():
    # Constructing the state machine again drives sideset_init again; on a
    # board that low lasts until active(1), so the first `.side(1)`, issued
    # in the same emulated cycle, is a rising edge, as at first construction.
    board = _board()
    rp2 = board.modules()["rp2"]
    prog = _toggle(rp2, 0, 0)
    sm, _ = _sm(board, prog, sideset_base=CLK)
    t0 = board.now
    sm.active(1)
    board.advance(3)  # high at t0 and t0 + 2
    sm, _ = _sm(board, prog, sideset_base=CLK)
    sm.active(1)
    board.advance(3)
    assert board.pio_rises[CLK] == [t0, t0 + 2, t0 + 3, t0 + 5]
