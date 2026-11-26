---
type: explanation
owner: vgacap maintainers
reader: someone who wants to know how the vgacap module shares a demo board with MicroPython and the Tiny Tapeout SDK
review: 2027-05-27
---

# The vgacap module inside MicroPython

This page explains what the vgacap MicroPython module is. It also explains how a capture borrows the chip's PIO,
DMA and interrupts from MicroPython, and gives them back. It is for someone choosing between the module and the
vgacap firmware, or changing the module. The calls themselves are on
[The vgacap module's Python API](reference-api.md).

## The module firmware

The module is a MicroPython C module, a `USER_C_MODULE`. These pages call the demo board's MicroPython firmware
built with it and with the Tiny Tapeout SDK frozen in **the module firmware**. The module is a thin binding over
the vgacap firmware's capture engines, [`firmware/core/fw_capture.c`](../firmware/core/fw_capture.c), and their RP2350 half,
[`firmware/pico/hal_rp2.c`](../firmware/pico/hal_rp2.c). It reimplements neither. Everything else on the board
stays the stock firmware's: the Tiny Tapeout SDK's `tt.shuttle`, the FPGA loader and the REPL.

So the module sits between the two other ways of capturing on the demo board. Like the stock firmware's path,
it keeps the board as it is and lets ttcap select designs and run the clock through the SDK. Like the vgacap
firmware, it runs the capture in C, as one call instead of a program sent over the REPL for each capture.

The keyword arguments of a call are turned back into the firmware protocol's `key=value` text and parsed by the
firmware's own tables. So the names, the ranges and the refusal messages are the firmware's, raised as a `ValueError` before anything
is written. The module adds two refusals of its own, for a key or a word value that is not lower-case letters and
underscores.

## What a capture borrows

MicroPython owns the chip, so a capture borrows what it needs and gives all of it back when it ends:

- **A PIO state machine.** State machine `sm` of block `pio` is taken as the capture programs in
  [`python/ttcap/mp/`](../python/ttcap/mp/) take it. For a block other than 0, `rp2.PIO(pio).remove_program()`
  clears MicroPython's programs, so the block's GPIO window can move to GPIO 16. Then
  `rp2.StateMachine(4 * pio + sm)` claims the state machine, and refuses one that something else has claimed.
- **Two DMA channels**, claimed with `dma_claim_unused_channel`, which `rp2.DMA` shares.
- **A handler on `DMA_IRQ_1`.** `rp2.DMA` owns `DMA_IRQ_0`, so the capture adds a shared handler on the other
  interrupt that acknowledges only its own two channels.
- **Heap for the buffers**, unless the caller passes `buf`: freed when the call ends.

Block 0 holds the stock firmware's FPGA bitstream loader, and its window cannot move while that is loaded. So
ttcap refuses `--pio 0`, and uses block 1 by default. Only one capture runs at a time; a second call raises
`RuntimeError`.

## Frames on a fragmented heap

The vgacap firmware owns [a static buffer](../firmware/reference-board.md#memory), and the module has
MicroPython's heap instead, which is rarely one free block. `frame()` therefore asks for the whole window in one
block, and failing that for blocks of 64, 16, 4 and 1 lines. The DMA walks the blocks in order through control
blocks, as [`frame_rp2.py`](../python/ttcap/mp/frame_rp2.py) does. So a window is still one unbroken run of
samples and one `FRAM` chunk.

On fpga-1 the largest free block held 188 lines, and all 481 lines of 640x480 fitted in 46 blocks. Each picture
was one captured frame, where one block alone had needed three. The run is in the
[dated result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1).

## Errors while the hardware is held

Nothing MicroPython raises may unwind through an engine while it holds the PIO and DMA, because only the
engine's own ending gives them back. So every call from the engines into MicroPython is protected, and an
exception is kept until the end. A failed write ends the capture with nothing more written. A pending exception
from MicroPython's event handling ends it at the next chunk, after the summary. Either way the exception is
raised once the hardware is free.

While the capture waits for a buffer, it runs MicroPython's pending events: its scheduler, soft timers such as
the SDK's, and USB. Ctrl-C is an ordinary byte for the length of the call, so the host's stop byte ends the
capture between chunks rather than interrupting it. Once the closing summary is written, Ctrl-C is a
`KeyboardInterrupt` again, the rule the capture programs follow.

## What stays with the SDK

The project clock, the project reset, `ui_in` and design selection stay the SDK's. Its calls for them include
`tt.clock_project_PWM`, `tt.reset_project` and `tt.shuttle[...].enable()`. ttcap drives them as it does on the
stock firmware's path. The vgacap firmware's `asic_select` is not in the module, which does not build the
firmware's command layer. The SDK selects ASIC projects itself.
