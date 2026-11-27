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
  back cycle by cycle. Once side-set drives the design's clock pin,
  `uo_out` advances one value per rising edge it drives, and only
  then. The PIO IRQ register (block + 0x030) is
  write-1-to-clear from `mem32`. `StateMachine.exec()` runs a `nop`, whose
  side-set drives its pins at once, enabled or not; any other instruction
  is refused.
* **DMA**: channels with chaining, completion IRQs that call the script's
  own handlers, and the `mem32` register writes the scripts use (re-arming
  `WRITE_ADDR`/`TRANS_COUNT`, and the PIO `CTRL` atomic-clear alias).
* **IO_BANK0**: a `mem32` read of a pad's `GPIOn_CTRL` gives its FUNCSEL:
  the PIO block driving it, SIO for a pad a `Pin` mode took, else NULL.
* **Pins** come from a `Waveform`: a project clock of `period` system
  cycles and the design's `uo_out` per project clock, e.g. from
  `vgacap.synth.frame_samples()` or an Icarus dump of real RTL -- or from
  a `ClockedDesign`, an exact counter clocked only by the rising edges
  really driven on its clock pad, reset synchronously by `rst_n`.
  `machine.Pin` is MicroPython v1.24.0's: `Pin(n, Pin.OUT, value=v)`
  drives a pad, any mode takes the pad from PIO, `value()` reads the pad
  whatever drives it, and `Board(pulls=...)` sets the external pulls that
  beat the RP2's own.
* **Heap**: `bytearray` is replaced by one that refuses blocks larger than
  the board's largest free block, which is the limit that matters on
  fpga-1 (414 KB free, 142 KB largest block, measured 2026-11-24).
* **USB**: every byte written to stdout, `print()` included, costs
  `cycles_per_byte` of emulated time, so frames elapse while a frame is
  being sent, as they do on the board.
* **Time**: `time` runs on the emulated clock, and `Board.add_timer` runs
  a periodic callback at the end of the advance it falls due in.

What it does not model: IRQ and timer latency (handlers run at the
completion cycle, timers at the end of an advance), DMA bus contention,
the input synchroniser (a fixed delay on every pin alike, so it moves
nothing relative to anything else), the PIO clock divider (state machines
run at the system clock; any `freq`, and any `SMn_CLKDIV` but 1.0, is refused), and `irq` between state
machines (state machines run one after another within each step, so a
flag that two running state machines share is refused). A state machine
reading back a pin PIO drives, a floating input, and side-set onto a pad a
`Pin` init took back from PIO are refused, and so are a `ClockedDesign`'s
`rst_n` and clock driven by two state machines. A pad a `Pin` init took back is
still refused to `in pins` and `wait`, though the board would let a
state machine read it. `ClockedDesign.configure()` forgets the design
before it: `count_at` for a cycle before the load returns `initial`, not
what the old design held then.
"""

from ttcap.emu.board import Board, BoardError, ClockedDesign, Waveform

__all__ = ["Board", "BoardError", "ClockedDesign", "Waveform"]
