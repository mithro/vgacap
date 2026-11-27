---
type: explanation
owner: vgacap maintainers
reader: someone who wants to know why every vgacap capture source writes one format
review: 2027-05-27
---

# The vgacap stream

This page explains why vgacap has one capture format, the vgacap stream, and what each kind of chunk in it is for. It is for someone choosing a capture source or writing a new one. The exact bytes are on [The vgacap stream format](reference-stream-format.md).

## One format for every source

Each capture source records a design's `uo_out` pins, at its own rate. A demo board's microcontroller records them once a project clock. The FPGA capture logic records a sample each time its harness marks one valid, which was every other clock in the [fpga-4 test](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-rtl-framer-fpga4). A simulation trace records a 43-bit sample of the design's pins, twice a clock. Each source writes a vgacap stream, so everything that reads a capture is written once.

## A header that describes the wiring

The `VGCH` header says how wide a sample is, and which sample bit carries hsync, vsync and each colour bit. So the reader needs no knowledge of the wiring. The Tiny VGA Pmod's byte on `uo_out`, an RP2040's 12-bit read and a simulator's 43-bit sample all decode with the same code. The header also gives the project clock, which turns a sample count into time.

## Chunks a board can write without work

`RAW ` and `FRAM` chunks hold the 32-bit words the board's DMA filled, as they are. The header says how many samples a word holds, and which end the first one sits at. So a microcontroller sends its capture buffer without repacking it. That leaves its CPU time for the USB link.

## Runs for the long stretches of blanking

Much of a VGA signal is blanking: long runs of one sync value. `RLE ` and `RUNS` chunks store a value and how many samples it lasts, a few bytes for a whole porch. The reader turns every chunk type into the same (value, run) pairs. So [libvgaframe](../frame/explanation-rebuilding-frames.md) has one way in, and a long run costs it about as much as one sample.

## Diagnostics beside the data

A `TIME` chunk carries the host's time, the clock rate and the count of samples dropped so far. A board writes one, with the message `overrun`, when its buffers overran. So a reader knows the samples on either side of it do not join. What the tools do there is on [How libvgaframe rebuilds frames](../frame/explanation-rebuilding-frames.md#resetting-on-an-overrun).

## Chunks a reader can skip

Every chunk starts with a tag and a length, so a reader can step over a chunk it does not know. The same framing lets a reader recover when a link drops bytes. The C reader scans for the next tag it knows, and reports how many bytes it lost. Version 2 added `RUNS`, `BLKH` and `META`, for samples wider than 32 bits and for the [simulation trace](../../docs/trace-format.md).
