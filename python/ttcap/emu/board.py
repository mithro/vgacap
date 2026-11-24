# SPDX-License-Identifier: Apache-2.0
"""The emulated board: pins, PIO, DMA, heap, USB and the fake modules.

See the package docstring for what is and is not modelled. Everything a
board script touches that this module does not implement raises
`BoardError` (or the AttributeError a missing method would raise on the
board), so a script can never silently rely on behaviour the emulation
made up.
"""

from __future__ import annotations

import bisect
import builtins
import pathlib
import types
import weakref
from typing import Callable, Sequence

#: RP2040 / RP2350 address map (pico-sdk addressmap.h).
PIO_BASE0 = 0x50200000
PIO_STRIDE = 0x100000
PIO_CTRL = 0x000
PIO_FDEBUG = 0x008
PIO_RXF0 = 0x020
#: Atomic register aliases (RP2040 datasheet 2.1.2): XOR +0x1000, SET
#: +0x2000, CLEAR +0x3000.
ALIAS_XOR, ALIAS_SET, ALIAS_CLR = 0x1000, 0x2000, 0x3000
DMA_BASE = 0x50000000
DMA_STRIDE = 0x40
DMA_READ_ADDR, DMA_WRITE_ADDR, DMA_TRANS_COUNT, DMA_CTRL_TRIG = 0x00, 0x04, 0x08, 0x0C
#: Emulated SRAM: buffers get addresses here.
SRAM_BASE = 0x20000000
#: DMA TREQ value meaning "unpaced" (RP2040 0x3f; the same here for both).
TREQ_PERMANENT = 0x3F
MASK32 = 0xFFFFFFFF


class BoardError(Exception):
    """The script did something the emulated board does not support, or
    something the real board would refuse in a way that has no exception of
    its own (an emulated-time limit, an unsupported instruction)."""


# --------------------------------------------------------------------------
# pins


class Waveform:
    """The pins as a function of emulated time (system clock cycles).

    `uo` holds the design's `uo_out` once per project clock and repeats,
    so a whole number of frames makes an endless signal. The project clock
    has period `period` cycles, high for the first `high` of them; `uo_out`
    takes clock k's value `delay` cycles after clock k's rising edge (the
    design's clock-to-output path), and the falling edge is where the
    sampler is meant to read it. Pins that are neither the clock nor a
    `uo_out` bit read `constants.get(gpio, 0)` -- e.g. the RP2040's `ui_in`
    pads inside its 12-bit window.
    """

    def __init__(
        self,
        uo: Sequence[int] | bytes,
        uo_gpios: Sequence[int],
        clk_gpio: int,
        period: int,
        high: int | None = None,
        delay: int = 0,
        constants: dict[int, int] | None = None,
    ) -> None:
        if period < 2:
            raise ValueError("period must be at least 2 cycles")
        self.uo = bytes(uo)
        if not self.uo:
            raise ValueError("uo must not be empty")
        self.n = len(self.uo)
        self.period = period
        self.high = period // 2 if high is None else high
        if not 0 < self.high < period:
            raise ValueError("high must be between 1 and period - 1 cycles")
        self.delay = delay
        self.clk_gpio = clk_gpio
        self.uo_bit = {gpio: bit for bit, gpio in enumerate(uo_gpios)}
        self.constants = dict(constants or {})
        #: Set when a script takes the clock pad (machine.Pin(clk, Pin.IN)):
        #: the PWM loses the pad and the clock stops, as measured on fpga-1.
        self.clock_stopped = False
        self._changes: dict[int, list[int]] = {}
        self._tables: dict[tuple[int, int], tuple[list[int], int]] = {}

    # -- single pins ------------------------------------------------------

    def clock_index(self, cycle: int) -> int:
        """The project clock whose `uo_out` is on the pins at `cycle`."""
        return (cycle - self.delay) // self.period

    def value(self, gpio: int, cycle: int) -> int:
        if gpio == self.clk_gpio:
            if self.clock_stopped:
                return 0
            return 1 if cycle % self.period < self.high else 0
        bit = self.uo_bit.get(gpio)
        if bit is not None:
            return (self.uo[self.clock_index(cycle) % self.n] >> bit) & 1
        return self.constants.get(gpio, 0) & 1

    def _change_list(self, bit: int) -> list[int]:
        """Indices i in [0, n) where bit `bit` differs from index i-1 (cyclic)."""
        if bit not in self._changes:
            uo, n = self.uo, self.n
            self._changes[bit] = [
                i for i in range(n) if ((uo[i] ^ uo[i - 1]) >> bit) & 1
            ]
        return self._changes[bit]

    def next_time(self, gpio: int, want: int, cycle: int) -> int | None:
        """Earliest cycle >= `cycle` at which `gpio` reads `want`, or None."""
        if self.value(gpio, cycle) == want:
            return cycle
        if gpio == self.clk_gpio:
            if self.clock_stopped:
                return None
            phase = cycle % self.period
            if want:  # low now: high again at the next period start
                return cycle - phase + self.period
            return cycle - phase + self.high  # high now: low at `high`
        bit = self.uo_bit.get(gpio)
        if bit is None:
            return None
        changes = self._change_list(bit)
        if not changes:
            return None
        k = self.clock_index(cycle)
        base, r = k - k % self.n, k % self.n
        j = bisect.bisect_right(changes, r)
        k_next = base + changes[j] if j < len(changes) else base + self.n + changes[0]
        # A binary signal that is not `want` now becomes `want` at its next
        # change.
        return k_next * self.period + self.delay

    # -- the IN window ----------------------------------------------------

    def window(self, base: int, count: int, cycle: int) -> int:
        """`count` pins from GPIO `base` up, bit 0 = `base`, at `cycle`."""
        key = (base, count)
        if key not in self._tables:
            table = []
            for byte in range(256):
                word = 0
                for i in range(count):
                    gpio = base + i
                    bit = self.uo_bit.get(gpio)
                    if bit is not None:
                        word |= ((byte >> bit) & 1) << i
                    elif gpio != self.clk_gpio:
                        word |= (self.constants.get(gpio, 0) & 1) << i
                table.append(word)
            clk_pos = self.clk_gpio - base if 0 <= self.clk_gpio - base < count else -1
            self._tables[key] = (table, clk_pos)
        table, clk_pos = self._tables[key]
        word = table[self.uo[self.clock_index(cycle) % self.n]]
        if clk_pos >= 0 and self.value(self.clk_gpio, cycle):
            word |= 1 << clk_pos
        return word


# --------------------------------------------------------------------------
# PIO


#: Decoded instruction kinds.
_WAIT, _IN, _JMP, _PULL, _PUSH, _MOV, _SET = range(7)


class _SM:
    """One state machine: registers, FIFOs and the interpreter."""

    def __init__(self, board: "Board", block: "_PioBlock", index: int) -> None:
        self.board = board
        self.block = block
        self.index = index
        self.program = None
        self.code: list[tuple] = []
        self.wrap_target = 0
        self.wrap_top = 0
        self.in_base = 0
        self.push_thresh = 32
        self.autopush = False
        self.shift_left = True
        self.rx_depth = 4
        self.tx_depth = 4
        self.reset_state()
        self.enabled = False
        self.disable_at: int | None = None
        self.now = 0

    def reset_state(self) -> None:
        self.pc = 0
        self.x = self.y = 0
        self.isr = self.isr_count = 0
        self.osr = 0
        self.osr_count = 32
        self.rx: list[int] = []
        self.tx: list[int] = []

    # -- configuration ----------------------------------------------------

    def load(self, prog: list, in_base: int | None, push_thresh: int | None) -> None:
        data, execctrl, shiftctrl = prog[0], prog[3], prog[4]
        self.wrap_target = (execctrl >> 7) & 0x1F
        self.wrap_top = (execctrl >> 12) & 0x1F
        self.autopush = bool((shiftctrl >> 16) & 1)
        self.shift_left = not ((shiftctrl >> 18) & 1)
        thresh = (shiftctrl >> 20) & 0x1F
        self.push_thresh = thresh or 32
        if push_thresh is not None:
            self.push_thresh = push_thresh
        join = (shiftctrl >> 30) & 3
        self.rx_depth = 8 if join == 2 else (0 if join == 1 else 4)
        self.tx_depth = 8 if join == 1 else (0 if join == 2 else 4)
        if in_base is not None:
            if not self.block.base <= in_base < self.block.base + 32:
                raise BoardError(
                    "in_base GPIO %d is outside PIO%d's window at %d"
                    % (in_base, self.block.index, self.block.base)
                )
            self.in_base = in_base
        if prog[7] is not None:
            raise BoardError("side-set is not emulated")
        self.program = prog
        self.code = [self._decode(word) for word in data]
        # With no side-set, bits 12:8 are all delay: idle cycles after the
        # instruction completes (a stalled instruction's delay waits too).
        self.delays = [(word >> 8) & 0x1F for word in data]
        self.reset_state()

    def _decode(self, word: int) -> tuple:
        op = word >> 13
        if op == 0:
            return (_JMP, (word >> 5) & 7, word & 0x1F)
        if op == 1:
            polarity, src, index = (word >> 7) & 1, (word >> 5) & 3, word & 0x1F
            if src != 0:
                raise BoardError("wait source %d (only gpio is emulated)" % src)
            # RP2350: the hardware adds GPIOBASE to the index, and
            # MicroPython stores (index - base) & 31 -- measured on fpga-1:
            # `wait(1, gpio, 16)` reads GPIO 16 with the window at 16, and
            # `wait(1, gpio, 0)` reads GPIO 32 and stalls.
            base = self.block.base
            return (_WAIT, polarity, ((index - base) & 31) + base)
        if op == 2:
            src, bits = (word >> 5) & 7, word & 0x1F
            if src != 0:
                raise BoardError("in source %d (only pins is emulated)" % src)
            return (_IN, bits or 32)
        if op == 4:
            if word & 0x1F:
                raise BoardError("bad push/pull 0x%04x" % word)
            pull = (word >> 7) & 1
            block_ = (word >> 5) & 1
            if (word >> 6) & 1:
                raise BoardError("push iffull / pull ifempty not emulated")
            return (_PULL if pull else _PUSH, block_)
        if op == 5:
            dest, mov_op, src = (word >> 5) & 7, (word >> 3) & 3, word & 7
            if dest in (0, 3, 4) or src in (4, 5):
                raise BoardError("mov 0x%04x not emulated" % word)
            return (_MOV, dest, mov_op, src)
        if op == 7:
            dest, data = (word >> 5) & 7, word & 0x1F
            if dest not in (1, 2):
                raise BoardError("set destination %d not emulated" % dest)
            return (_SET, dest, data)
        raise BoardError("instruction 0x%04x (opcode %d) not emulated" % (word, op))

    # -- running ----------------------------------------------------------

    def _next_pc(self) -> None:
        self.pc = self.wrap_target if self.pc == self.wrap_top else self.pc + 1

    def run(self, until: int) -> None:
        """Execute until `until`, the disable time, or a stall nothing in
        here can end (an empty TX FIFO, a full RX FIFO with no DMA)."""
        wave = self.board.wave
        code = self.code
        while self.enabled:
            limit = until
            if self.disable_at is not None:
                if self.now >= self.disable_at:
                    self.enabled = False
                    self.disable_at = None
                    return
                limit = min(limit, self.disable_at)
            if self.now >= limit:
                return
            ins = code[self.pc]
            d = self.delays[self.pc]
            kind = ins[0]
            if kind == _WAIT:
                t = wave.next_time(ins[2], ins[1], self.now)
                if t is None or t >= limit:
                    self.now = limit
                    continue
                self.now = t + 1 + d
                self._next_pc()
            elif kind == _IN:
                bits = ins[1]
                value = wave.window(self.in_base, bits, self.now)
                if bits < 32:
                    value &= (1 << bits) - 1
                if self.shift_left:
                    isr = ((self.isr << bits) | value) & MASK32 if bits < 32 else value
                else:
                    raise BoardError("right-shifting ISR not emulated")
                count = min(self.isr_count + bits, 32)
                if self.autopush and count >= self.push_thresh:
                    if len(self.rx) >= self.rx_depth:
                        self._rx_stalled()
                        self.now = limit
                        continue
                    self.isr, self.isr_count = 0, 0
                    self.now += 1 + d
                    self._next_pc()
                    self._push(isr)
                else:
                    self.isr, self.isr_count = isr, count
                    self.now += 1 + d
                    self._next_pc()
            elif kind == _JMP:
                cond, target = ins[1], ins[2]
                if cond == 0:
                    take = True
                elif cond == 1:
                    take = self.x == 0
                elif cond == 2:
                    take = self.x != 0
                    self.x = (self.x - 1) & MASK32
                elif cond == 3:
                    take = self.y == 0
                elif cond == 4:
                    take = self.y != 0
                    self.y = (self.y - 1) & MASK32
                elif cond == 5:
                    take = self.x != self.y
                else:
                    raise BoardError("jmp condition %d not emulated" % cond)
                self.now += 1 + d
                if take:
                    self.pc = target
                else:
                    self._next_pc()
            elif kind == _PULL:
                if not self.tx:
                    if ins[1]:  # block
                        self.now = limit
                        continue
                    self.osr = self.x
                else:
                    self.osr = self.tx.pop(0)
                self.osr_count = 0
                self.now += 1 + d
                self._next_pc()
            elif kind == _PUSH:
                if len(self.rx) >= self.rx_depth:
                    if ins[1]:
                        self._rx_stalled()
                        self.now = limit
                        continue
                else:
                    word = self.isr
                    self.isr, self.isr_count = 0, 0
                    self.now += 1 + d
                    self._next_pc()
                    self._push(word)
                    continue
                self.isr, self.isr_count = 0, 0
                self.now += 1 + d
                self._next_pc()
            elif kind == _MOV:
                _, dest, mov_op, src = ins
                value = {1: self.x, 2: self.y, 3: 0, 6: self.isr, 7: self.osr}.get(src)
                if src == 0:
                    value = wave.window(self.block.base, 32, self.now)
                if mov_op == 1:
                    value = ~value & MASK32
                elif mov_op == 2:
                    value = int("{:032b}".format(value)[::-1], 2)
                if dest == 1:
                    self.x = value
                elif dest == 2:
                    self.y = value
                elif dest == 6:
                    self.isr, self.isr_count = value, 0
                elif dest == 7:
                    self.osr, self.osr_count = value, 0
                self.now += 1 + d
                if dest == 5:
                    self.pc = value & 0x1F
                else:
                    self._next_pc()
            elif kind == _SET:
                if ins[1] == 1:
                    self.x = ins[2]
                else:
                    self.y = ins[2]
                self.now += 1 + d
                self._next_pc()

    def _rx_stalled(self) -> None:
        self.block.fdebug |= 1 << self.index  # RXSTALL, write-1-to-clear

    def _push(self, word: int) -> None:
        self.rx.append(word)
        # DMA and any IRQ it raises happen at this state machine's time,
        # which runs ahead of the board's while it executes.
        self.board.event_now = self.now
        try:
            self.board.dma_service(self)
        finally:
            self.board.event_now = None


class _PioBlock:
    def __init__(self, board: "Board", index: int) -> None:
        self.board = board
        self.index = index
        self.base = 0
        self.fdebug = 0
        self.programs: list[list] = []
        self.sms = [_SM(board, self, i) for i in range(4)]

    @property
    def used(self) -> int:
        return sum(len(p[0]) for p in self.programs)

    def add(self, prog: list) -> None:
        if any(p is prog for p in self.programs):
            return
        if self.used + len(prog[0]) > 32:
            raise OSError(12, "ENOMEM")
        self.programs.append(prog)

    def remove(self, prog: list | None) -> None:
        if prog is None:
            self.programs = []
        else:
            self.programs = [p for p in self.programs if p is not prog]

    def address(self, offset: int) -> int:
        return PIO_BASE0 + PIO_STRIDE * self.index + offset


# --------------------------------------------------------------------------
# DMA


class _Channel:
    def __init__(self, num: int) -> None:
        self.num = num
        self.read_addr = 0
        self.write_addr = 0
        self.reload = 0
        self.remaining = 0
        self.ctrl = 0
        self.busy = False
        self.handler: Callable | None = None
        self.owner = None
        self.claimed = False

    # CTRL as packed by `pack_ctrl()` below (RP2040 bit layout).
    @property
    def inc_read(self) -> bool:
        return bool((self.ctrl >> 4) & 1)

    @property
    def inc_write(self) -> bool:
        return bool((self.ctrl >> 5) & 1)

    @property
    def ring_size(self) -> int:
        return (self.ctrl >> 6) & 0xF

    @property
    def ring_sel(self) -> bool:
        """True: the ring applies to the write address."""
        return bool((self.ctrl >> 10) & 1)

    @property
    def chain_to(self) -> int:
        return (self.ctrl >> 11) & 0xF

    @property
    def treq(self) -> int:
        return (self.ctrl >> 15) & 0x3F

    @property
    def irq_quiet(self) -> bool:
        return bool((self.ctrl >> 21) & 1)

    @property
    def enabled(self) -> bool:
        return bool(self.ctrl & 1)


_CTRL_FIELDS = {
    # name: (shift, width)
    "enable": (0, 1),
    "high_pri": (1, 1),
    "size": (2, 2),
    "inc_read": (4, 1),
    "inc_write": (5, 1),
    "ring_size": (6, 4),
    "ring_sel": (10, 1),
    "chain_to": (11, 4),
    "treq_sel": (15, 6),
    "irq_quiet": (21, 1),
    "bswap": (22, 1),
    "sniff_en": (23, 1),
}


# --------------------------------------------------------------------------
# the board


class Board:
    """One emulated demo board.

    `chip` is "rp2040" or "rp2350" (RP2350 adds `PIO.gpio_base`, a third
    PIO block and four more DMA channels). `heap_blocks` lists the free
    heap as contiguous blocks, largest first; the default is fpga-1's
    measured state. `cycles_per_byte` prices USB output (the default,
    190, is ~700 KB/s at 133 MHz, the RP2350's measured CDC rate).
    """

    def __init__(
        self,
        wave: Waveform,
        chip: str = "rp2350",
        sysclk_hz: int = 133_000_000,
        heap_blocks: Sequence[int] = (142_591, 96_000, 64_000, 44_800),
        cycles_per_byte: float = 190.0,
        max_cycles: int = 2_000_000_000,
        idle_cycles: int = 2_000,
    ) -> None:
        if chip not in ("rp2040", "rp2350"):
            raise ValueError("chip must be rp2040 or rp2350")
        self.wave = wave
        self.chip = chip
        self.sysclk_hz = sysclk_hz
        self.cycles_per_byte = cycles_per_byte
        self.max_cycles = max_cycles
        self.idle_cycles = idle_cycles
        self.now = 0
        self.blocks = [_PioBlock(self, i) for i in range(3 if chip == "rp2350" else 2)]
        self.channels = [_Channel(i) for i in range(16 if chip == "rp2350" else 12)]
        self.heap = list(heap_blocks)
        self.output = bytearray()
        self.stop_after_bytes: int | None = None
        self.stop_when: Callable[["Board"], bool] | None = None
        self._stop_sent = False
        self._memory: list[tuple[int, int, object]] = []
        self._next_addr = SRAM_BASE
        self.configured_pins: list[tuple[int, object]] = []
        self.kbd_intr = 3
        #: The time of the DMA event being handled, if any (see _SM._push).
        self.event_now: int | None = None
        self._dma_queue: list[tuple[str, _Channel]] = []
        self._dma_running = False
        #: What one `Pin.value()` costs a MicroPython loop, in cycles.
        self.pin_read_cycles = 150

    # -- time ---------------------------------------------------------------

    def advance(self, cycles: float) -> None:
        until = self.now + max(1, int(cycles))
        if until > self.max_cycles:
            raise BoardError(
                "emulated time limit: %d cycles (%.3f s at %d Hz)"
                % (self.max_cycles, self.max_cycles / self.sysclk_hz, self.sysclk_hz)
            )
        for block in self.blocks:
            for sm in block.sms:
                if sm.enabled:
                    if sm.now < self.now:
                        sm.now = self.now
                    sm.run(until)
        self.now = until

    @property
    def stop_requested(self) -> bool:
        if self.stop_after_bytes is not None and len(self.output) >= self.stop_after_bytes:
            return True
        return bool(self.stop_when and self.stop_when(self))

    # -- memory -------------------------------------------------------------

    def address_of(self, obj) -> int:
        for addr, size, held in self._memory:
            if held is obj:
                return addr
        size = len(obj)
        addr = self._next_addr
        self._next_addr += (size + 3) & ~3
        self._memory.append((addr, size, obj))
        return addr

    def _find(self, addr: int):
        for base, size, obj in self._memory:
            if base <= addr < base + size:
                return obj, addr - base
        return None, 0

    def mem32_read(self, addr: int) -> int:
        obj, offset = self._find(addr)
        if obj is not None:
            return int.from_bytes(bytes(obj[offset : offset + 4]), "little")
        for block in self.blocks:
            if addr == block.address(PIO_FDEBUG):
                return block.fdebug
            if addr == block.address(PIO_CTRL):
                return sum(1 << sm.index for sm in block.sms if sm.enabled)
        ch, reg = self._dma_reg(addr)
        if ch is not None:
            if reg == DMA_WRITE_ADDR:
                return ch.write_addr
            if reg == DMA_READ_ADDR:
                return ch.read_addr
            if reg == DMA_TRANS_COUNT:
                return ch.remaining if ch.busy else ch.reload
        raise BoardError("mem32 read of unmodelled address 0x%08x" % addr)

    def mem32_write(self, addr: int, value: int) -> None:
        value &= MASK32
        obj, offset = self._find(addr)
        if obj is not None:
            obj[offset : offset + 4] = value.to_bytes(4, "little")
            return
        for block in self.blocks:
            if addr == block.address(PIO_FDEBUG):
                block.fdebug &= ~value  # write-1-to-clear
                return
            if addr == block.address(PIO_CTRL | ALIAS_CLR):
                for sm in block.sms:
                    if value & (1 << sm.index) and sm.enabled:
                        # The stopper DMA's write lands a few cycles after
                        # the transfer that triggered it.
                        at = self.now if self.event_now is None else self.event_now
                        sm.disable_at = at + 3 if sm.disable_at is None else sm.disable_at
                return
        ch, reg = self._dma_reg(addr)
        if ch is not None:
            if reg == DMA_WRITE_ADDR:
                ch.write_addr = value
                return
            if reg == DMA_READ_ADDR:
                ch.read_addr = value
                return
            if reg == DMA_TRANS_COUNT:
                ch.reload = value
                return
            if reg == DMA_CTRL_TRIG:
                # CTRL through its trigger alias: load it and start.
                ch.ctrl = value
                self.dma_trigger(ch)
                return
        raise BoardError("mem32 write of unmodelled address 0x%08x" % addr)

    def _dma_reg(self, addr: int):
        if DMA_BASE <= addr < DMA_BASE + DMA_STRIDE * len(self.channels):
            num, reg = divmod(addr - DMA_BASE, DMA_STRIDE)
            if reg in (DMA_READ_ADDR, DMA_WRITE_ADDR, DMA_TRANS_COUNT, DMA_CTRL_TRIG):
                return self.channels[num], reg
        return None, 0

    # -- DMA engine ---------------------------------------------------------

    def _rx_source(self, ch: _Channel):
        for block in self.blocks:
            for sm in block.sms:
                if ch.read_addr == block.address(PIO_RXF0 + 4 * sm.index):
                    return sm
        return None

    def _stepped(self, ch: _Channel, addr: int, write: bool) -> int:
        """`addr` after one 4-byte transfer, honouring the address ring."""
        if ch.ring_size and ch.ring_sel == write:
            mask = (1 << ch.ring_size) - 1
            return (addr & ~mask) | ((addr + 4) & mask)
        return addr + 4

    def _transfer(self, ch: _Channel, word: int) -> None:
        dest = ch.write_addr
        obj, offset = self._find(dest)
        if obj is not None:
            if offset + 4 > len(obj):
                raise BoardError("DMA channel %d wrote past the end of its buffer" % ch.num)
            obj[offset : offset + 4] = word.to_bytes(4, "little")
        else:
            self.mem32_write(dest, word)
        if ch.inc_write:
            ch.write_addr = self._stepped(ch, ch.write_addr, True)
        if ch.inc_read:
            ch.read_addr = self._stepped(ch, ch.read_addr, False)
        ch.remaining -= 1
        if ch.remaining == 0:
            self._complete(ch)

    def _complete(self, ch: _Channel) -> None:
        ch.busy = False
        if ch.chain_to != ch.num:
            self._dma_queue.append(("start", self.channels[ch.chain_to]))
        if not ch.irq_quiet and ch.handler is not None:
            self._dma_queue.append(("irq", ch))

    def dma_trigger(self, ch: _Channel) -> None:
        """Start `ch` -- from the script, a chain, or another channel's write
        to its CTRL_TRIG. Starts are queued and run one at a time, so a DMA
        that triggers DMA (control blocks) behaves as the hardware's
        sequential bus does instead of recursing mid-transfer."""
        self._dma_queue.append(("start", ch))
        self._run_dma()

    def _run_dma(self) -> None:
        if self._dma_running:
            return
        self._dma_running = True
        try:
            while self._dma_queue:
                kind, ch = self._dma_queue.pop(0)
                if kind == "irq":
                    ch.handler(ch.owner)
                    continue
                if not ch.enabled:
                    continue
                ch.busy = True
                ch.remaining = ch.reload
                if ch.remaining == 0:
                    self._complete(ch)
                    continue
                self._service(ch)
        finally:
            self._dma_running = False

    def _service(self, ch: _Channel) -> None:
        sm = self._rx_source(ch)
        if sm is not None:
            while ch.busy and sm.rx:
                self._transfer(ch, sm.rx.pop(0))
            return
        if ch.treq != TREQ_PERMANENT:
            raise BoardError("DMA channel %d paced by an unmodelled DREQ %d" % (ch.num, ch.treq))
        while ch.busy:
            self._transfer(ch, self.mem32_read(ch.read_addr))

    def dma_service(self, sm: _SM) -> None:
        for ch in self.channels:
            if ch.busy and self._rx_source(ch) is sm:
                self._service(ch)
        self._run_dma()

    # -- the script ---------------------------------------------------------

    def modules(self) -> dict[str, types.ModuleType]:
        return _make_modules(self)

    def run_script(self, script: str, name: str = "<stdin>") -> bytes:
        """Run a built board script to completion; return what it wrote."""
        modules = self.modules()
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name in modules:
                return modules[name]
            return real_import(name, globals, locals, fromlist, level)

        fake_builtins = dict(vars(builtins))
        fake_builtins["__import__"] = fake_import
        fake_builtins["bytearray"] = modules["_heap"].bytearray
        namespace = {"__builtins__": fake_builtins, "__name__": "__main__"}
        exec(compile(script, name, "exec"), namespace)  # noqa: S102 - the board script
        return bytes(self.output)


# --------------------------------------------------------------------------
# fake modules


class _Pin:
    """machine.Pin: knows its number; configuring the clock pad stops it."""

    IN, OUT = 0, 1
    PULL_UP, PULL_DOWN = 1, 2
    _board: Board

    def __init__(self, gpio: int, mode=None, pull=None, *args, **kwargs) -> None:
        self.gpio = gpio
        if mode is not None:
            self._board.configured_pins.append((gpio, mode))
            if gpio == self._board.wave.clk_gpio:
                self._board.wave.clock_stopped = True

    def value(self, v=None):
        if v is not None:
            raise BoardError("driving pins is not emulated")
        board = self._board
        board.advance(board.pin_read_cycles)
        return board.wave.value(self.gpio, board.now)

    def __repr__(self) -> str:
        return "Pin(GPIO%d, mode=IN)" % self.gpio


def _make_modules(board: Board) -> dict[str, types.ModuleType]:
    pin_cls = type("Pin", (_Pin,), {"_board": board})

    # -- heap -------------------------------------------------------------

    heap_mod = types.ModuleType("_heap")
    blocks = board.heap

    class HeapBytearray(bytearray):
        """bytearray that has to fit one of the board's free heap blocks."""

        def __new__(cls, *args, **kwargs):
            obj = super().__new__(cls, *args, **kwargs)
            return obj

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            size = len(self)
            # Small objects (a 4-byte stop word, a 16-byte control block)
            # are noise next to the blocks that matter; a frame line (800
            # bytes and up) is not.
            if size >= 256:
                for i, free in enumerate(blocks):
                    if free >= size:
                        blocks[i] -= size
                        weakref.finalize(self, _give_back, blocks, i, size)
                        break
                else:
                    raise MemoryError("memory allocation failed, allocating %d bytes" % size)

    heap_mod.bytearray = HeapBytearray

    # -- machine ------------------------------------------------------------

    machine = types.ModuleType("machine")
    machine.Pin = pin_cls

    class Mem32:
        def __getitem__(self, addr):
            return board.mem32_read(addr)

        def __setitem__(self, addr, value):
            board.mem32_write(addr, value)

    machine.mem32 = Mem32()
    machine.freq = lambda hz=None: board.sysclk_hz
    machine.idle = lambda: board.advance(board.idle_cycles)

    # -- micropython --------------------------------------------------------

    micropython = types.ModuleType("micropython")
    micropython.const = lambda value: value

    def kbd_intr(char):
        board.kbd_intr = char

    micropython.kbd_intr = kbd_intr
    micropython.heap_lock = lambda: None
    micropython.heap_unlock = lambda: 0
    micropython.mem_info = lambda *a: None

    # -- gc -------------------------------------------------------------------

    gc = types.ModuleType("gc")
    gc.collect = lambda: None
    gc.mem_free = lambda: sum(blocks)

    # -- uctypes ------------------------------------------------------------

    uctypes = types.ModuleType("uctypes")
    uctypes.addressof = board.address_of

    # -- sys / select ---------------------------------------------------------

    class Stdout:
        def write(self, data):
            data = bytes(data)
            board.output += data
            board.advance(len(data) * board.cycles_per_byte)
            return len(data)

        def flush(self):
            pass

    class Stdin:
        def read(self, n=1):
            if board.stop_requested and not board._stop_sent:
                board._stop_sent = True
                return b"\x03"
            return b""

    stdout = types.SimpleNamespace(buffer=Stdout(), write=lambda s: Stdout().write(s.encode()))
    stdin_buffer = Stdin()
    stdin = types.SimpleNamespace(buffer=stdin_buffer)
    sys_mod = types.ModuleType("sys")
    sys_mod.stdout = stdout
    sys_mod.stdin = stdin
    sys_mod.implementation = types.SimpleNamespace(name="micropython")

    select = types.ModuleType("select")
    select.POLLIN = 1

    class Poll:
        def __init__(self):
            self.streams = []

        def register(self, stream, mask=1):
            self.streams.append(stream)

        def poll(self, timeout=-1):
            if board.stop_requested and not board._stop_sent:
                return [(s, 1) for s in self.streams]
            return []

    select.poll = Poll

    # -- _rp2 ---------------------------------------------------------------

    _rp2 = types.ModuleType("_rp2")

    class PIO:
        SHIFT_LEFT, SHIFT_RIGHT = 0, 1
        JOIN_NONE, JOIN_TX, JOIN_RX = 0, 1, 2
        IN_LOW, IN_HIGH, OUT_LOW, OUT_HIGH = 0, 1, 2, 3
        IRQ_SM0, IRQ_SM1, IRQ_SM2, IRQ_SM3 = 0x100, 0x200, 0x400, 0x800

        def __init__(self, index: int) -> None:
            if not 0 <= index < len(board.blocks):
                raise ValueError("invalid PIO %d" % index)
            self._block = board.blocks[index]

        def remove_program(self, prog=None):
            self._block.remove(prog)

        def __repr__(self) -> str:
            return "PIO(%d)" % self._block.index

    if board.chip == "rp2350":

        def gpio_base(self, base=None):
            block = self._block
            if base is None:
                return pin_cls(block.base)
            gpio = base.gpio if isinstance(base, _Pin) else base
            if gpio not in (0, 16):
                raise ValueError("gpio_base must be 0 or 16")
            if block.programs:
                raise OSError(22, "EINVAL")
            block.base = gpio

        PIO.gpio_base = gpio_base

    class StateMachine:
        def __init__(self, id, prog=None, freq=-1, *, in_base=None, push_thresh=None, **kwargs):
            unsupported = [k for k, v in kwargs.items() if v is not None]
            if unsupported:
                raise BoardError("StateMachine options not emulated: %s" % unsupported)
            block = board.blocks[id // 4]
            self._sm = block.sms[id % 4]
            if prog is not None:
                block.add(prog)
                base = in_base.gpio if isinstance(in_base, _Pin) else in_base
                self._sm.load(prog, base, push_thresh)
                self._sm.enabled = False

        def active(self, value=None):
            sm = self._sm
            if value is None:
                return sm.enabled
            if value and not sm.enabled:
                sm.enabled = True
                sm.disable_at = None
                sm.now = board.now
            elif not value:
                sm.enabled = False
            return sm.enabled

        def restart(self):
            sm = self._sm
            sm.pc = 0
            sm.isr = sm.isr_count = 0
            sm.osr_count = 32
            sm.disable_at = None

        def put(self, value, shift=0):
            sm = self._sm
            values = value if isinstance(value, (list, tuple)) else [value]
            for v in values:
                while len(sm.tx) >= sm.tx_depth:
                    if not sm.enabled:
                        raise BoardError("put() would block forever: TX FIFO full, SM stopped")
                    board.advance(board.idle_cycles)
                sm.tx.append((v << shift) & MASK32)

        def get(self, buf=None, shift=0):
            sm = self._sm
            while not sm.rx:
                if not sm.enabled:
                    raise BoardError("get() would block forever: RX FIFO empty, SM stopped")
                board.advance(board.idle_cycles)
            return sm.rx.pop(0) >> shift

        def rx_fifo(self):
            return len(self._sm.rx)

        def tx_fifo(self):
            return len(self._sm.tx)

    class DMA:
        def __init__(self):
            for ch in board.channels:
                if not ch.claimed:
                    ch.claimed = True
                    ch.owner = self
                    ch.ctrl = 0
                    ch.busy = False
                    ch.handler = None
                    self._ch = ch
                    return
            raise OSError(19, "ENODEV")

        @property
        def channel(self):
            return self._ch.num

        def pack_ctrl(self, default=None, **kwargs):
            fields = {
                "enable": 1,
                "high_pri": 0,
                "size": 2,
                "inc_read": 1,
                "inc_write": 1,
                "ring_size": 0,
                "ring_sel": 0,
                "chain_to": self._ch.num,
                "treq_sel": TREQ_PERMANENT,
                "irq_quiet": 1,
                "bswap": 0,
                "sniff_en": 0,
            }
            for key, value in kwargs.items():
                if key not in fields:
                    raise TypeError("unexpected keyword argument '%s'" % key)
                fields[key] = int(value)
            word = 0
            for key, value in fields.items():
                shift, width = _CTRL_FIELDS[key]
                word |= (value & ((1 << width) - 1)) << shift
            return word

        def _address(self, value):
            if isinstance(value, int):
                return value
            return board.address_of(value)

        def config(self, read=None, write=None, count=None, ctrl=None, trigger=False):
            ch = self._ch
            if read is not None:
                ch.read_addr = self._address(read)
            if write is not None:
                ch.write_addr = self._address(write)
            if count is not None:
                ch.reload = count
            if ctrl is not None:
                ch.ctrl = ctrl
            if trigger:
                board.dma_trigger(ch)

        def active(self, value=None):
            ch = self._ch
            if value is None:
                return ch.busy
            if value:
                board.dma_trigger(ch)
            else:
                ch.busy = False
            return ch.busy

        def irq(self, handler=None, hard=False):
            self._ch.handler = handler

        def close(self):
            ch = self._ch
            ch.busy = False
            ch.claimed = False
            ch.handler = None
            ch.owner = None

        @property
        def count(self):
            return self._ch.remaining if self._ch.busy else self._ch.reload

    _rp2.PIO = PIO
    _rp2.StateMachine = StateMachine
    _rp2.DMA = DMA
    _rp2.Flash = None

    rp2 = _load_upstream_rp2(_rp2, micropython)

    return {
        "machine": machine,
        "micropython": micropython,
        "gc": gc,
        "uctypes": uctypes,
        "sys": sys_mod,
        "select": select,
        "rp2": rp2,
        "_rp2": _rp2,
        "_heap": heap_mod,
    }


def _give_back(blocks: list[int], index: int, size: int) -> None:
    blocks[index] += size


_UPSTREAM = pathlib.Path(__file__).with_name("upstream_rp2_v1_24_0.py")


def _load_upstream_rp2(fake_rp2: types.ModuleType, micropython: types.ModuleType) -> types.ModuleType:
    """MicroPython's own rp2.py, with `_rp2` and `micropython` resolved to
    the fakes. Its `asm_pio` is what assembles every program here."""
    module = types.ModuleType("rp2")
    module.__file__ = str(_UPSTREAM)
    real_import = builtins.__import__

    def upstream_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "_rp2":
            return fake_rp2
        if name == "micropython":
            return micropython
        return real_import(name, globals, locals, fromlist, level)

    module_builtins = dict(vars(builtins))
    module_builtins["__import__"] = upstream_import
    module.__dict__["__builtins__"] = module_builtins
    exec(compile(_UPSTREAM.read_text(encoding="utf-8"), str(_UPSTREAM), "exec"), module.__dict__)  # noqa: S102
    return module


def assemble(build: Callable[[types.ModuleType], object]) -> list:
    """Run `build(rp2)` with the upstream assembler; for tests of programs."""
    wave = Waveform(b"\0", [], clk_gpio=0, period=2)
    return build(Board(wave).modules()["rp2"])


__all__ = ["Board", "BoardError", "Waveform", "assemble"]
