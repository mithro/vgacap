---
type: explanation
owner: vgacap maintainers
reader: someone who wants to know why every vgacap capture source writes one format
review: 2027-05-27
---

# The vgacap stream

This page explains why vgacap has one capture format, the vgacap stream, and what each kind of chunk in it is for. It is for someone choosing a capture source or writing a new one. The exact bytes are on [The vgacap stream format](reference-stream-format.md).

## One format for every source

A demo board's microcontroller, a Raspberry Pi 5, an FPGA and a simulator all record the same thing: a design's `uo_out` pins, once a clock. Each writes it as a vgacap stream, so everything that reads a capture is written once. [`vgacap-frames`](../tools/README.md), the [GStreamer plugin](../../gst/README.md) and the pixel-exact checks never ask where a capture came from. A new capture source only has to write the stream to get all of them.

## A header that describes the wiring

The `VGCH` header says how wide a sample is and which sample bit carries hsync, vsync and each colour bit. So the reader needs no knowledge of the wiring. The Tiny VGA Pmod's byte on `uo_out`, an RP2040's 12-bit read that wraps around other pins, and a simulator's 43-bit sample all decode with the same code. The header also carries the project clock, which turns a sample count into time.

## Chunks a board can write without work

`RAW ` and `FRAM` chunks hold the 32-bit words the board's DMA filled, as they are. The header says how many samples a word holds and which end the first one sits at. So a microcontroller sends its capture buffer without repacking it, which leaves its CPU time for the USB link. `FRAM` holds a whole frame, or a window of lines, captured at the design's real clock and sent between frames.

## Runs for the long stretches of blanking

Much of a VGA signal is blanking: long runs of one sync value. `RLE ` and `RUNS` chunks store a value and how many samples it lasts, which costs a few bytes for a whole porch. The reader turns every chunk type into the same (value, run) pairs. So [libvgaframe](../frame/explanation-rebuilding-frames.md) has one way in, and a run of a thousand samples costs it about as much as one sample.

## Diagnostics beside the data

A `TIME` chunk carries the host's time, the clock rate and the count of samples dropped so far. A board writes one when its buffers overran, with the message `overrun`, so a reader knows the samples on either side do not join. `vgacap-frames` and `vgadecode` reset the picture they are building there, rather than draw a frame across the gap.

## Chunks a reader can skip

Every chunk starts with a tag and a length, so a reader steps over a chunk it does not know. The same framing lets a reader recover from damage: if a serial link drops bytes, the C reader scans for the next tag it knows and reports how many bytes it lost. Version 2 added `RUNS`, `BLKH` and `META` for samples wider than 32 bits and for the [simulation trace](../../docs/trace-format.md). A version 1 reader refuses a version 2 header rather than misread it.
