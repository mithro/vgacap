# SPDX-License-Identifier: Apache-2.0
"""Host-side emulation of a demo board's RP2, for running board scripts.

`ttcap.mp.build()` produces one MicroPython script per capture. On the board
it drives PIO, DMA and hard IRQs; here `Board` provides those as fakes with
real behaviour behind them, so the *same built script* runs under CPython
and writes the same chunk stream it would write over USB:

* **`rp2`** is MicroPython v1.24.0's own `ports/rp2/modules/rp2.py`
  (vendored unchanged as `upstream_rp2_v1_24_0.py`, MIT), so `asm_pio`
  assembles exactly the instruction words the board gets -- including its
  globals swap. Its C half, `_rp2`, is the fake here: `PIO`,
  `StateMachine` and `DMA`.
* **PIO**: a cycle-level interpreter for the instructions these programs
  use (`wait gpio`, `in` from pins or a register, `out` to a register,
  `jmp`, `set`, `mov`, `pull`, `push` and `push iffull`, `irq` set, clear
  and wait, `nop`, wrap, delays), with autopush, RX FIFO depth, `RXSTALL`
  (blocking and dropped nonblocking pushes), pin reads that wrap round the
  32-pin window, the RP2350's SHIFTCTRL.IN_COUNT mask, and the RP2350
  `wait gpio` index relocation measured on fpga-1. `wait` fast-forwards to
  the next edge instead of stepping every cycle. Side-set (plain or
  optional, to pin values; not pindirs) drives its pins from the cycle an
  instruction issues, stalled or not, and `Board.pio_pin()` reads them
  back cycle by cycle. The PIO IRQ register (block + 0x030) is
  write-1-to-clear from `mem32`.
* **DMA**: channels with chaining, completion IRQs that call the script's
  own handlers, and the `mem32` register writes the scripts use (re-arming
  `WRITE_ADDR`/`TRANS_COUNT`, and the PIO `CTRL` atomic-clear alias).
* **Pins** come from a `Waveform`: a project clock of `period` system
  cycles and the design's `uo_out` per project clock, e.g. from
  `vgacap.synth.frame_samples()` or an Icarus dump of real RTL.
* **Heap**: `bytearray` is replaced by one that refuses blocks larger than
  the board's largest free block, which is the limit that matters on
  fpga-1 (414 KB free, 142 KB largest block, measured 2026-11-24).
* **USB**: every byte written to stdout costs `cycles_per_byte` of
  emulated time, so frames elapse while a frame is being sent, as they do
  on the board.

What it does not model: IRQ latency (handlers run at the completion cycle), DMA bus
contention, the input synchroniser (a fixed delay on every pin alike,
so it moves nothing relative to anything else), the PIO clock divider
(state machines run at the system clock; any `freq` is refused), reading
back a pin PIO drives (refused), and `irq` between state machines (state
machines run one after another within each step, so a flag that two
running state machines share is refused).
"""

from ttcap.emu.board import Board, BoardError, Waveform

__all__ = ["Board", "BoardError", "Waveform"]
