---
type: explanation
owner: vgacap maintainers
reader: someone who simulates Tiny Tapeout designs and wants to know what a vgacap trace keeps
review: 2027-05-27
---

# What a simulation trace records

This page explains what a vgacap simulation trace records, and why it is shaped as it is. It is for someone who simulates Tiny Tapeout designs and reads or writes traces. The bytes are on [the simulation trace format](../../docs/trace-format.md).

## Every pin, every half clock

A board capture keeps only `uo_out`, and only once a clock. A trace keeps all 43 pins of a Tiny Tapeout design: `uo_out`, `uio_out`, `uio_oe`, `ui_in`, `uio_in`, `rst_n`, `ena` and the clock. It keeps them twice a clock, once with the clock low and once with it high. So a trace answers questions a capture cannot, such as what the inputs were, or when an output changed within its clock.

The clock bit is never stored. It alternates from sample to sample, so a reader works it out from the sample's number. The clock-low sample holds the outputs registered at the edge before, and the clock-high sample those of the clock's own edge.

## Runs, blocks and compression

A VGA design's pins hold still for long stretches: a whole line of one colour is hundreds of clocks of one value. A trace stores each stretch as one run, a value and a count, in `RUNS` chunks of a version 2 vgacap stream. The stream is cut into blocks of at most 2^24 clocks, and each block is compressed as one frame of zstd's seekable format. A seek table at the end lets a reader jump to a block, and decode a range without the blocks before it.

## A trace cut short still reads

A simulation that is killed leaves no seek table and perhaps half a block. A reader then walks the frames from the start, keeps every whole one, and drops the last if its checksum fails. The trace reads up to the last whole block, with the status `truncated`. A plain, uncompressed stream that starts with `VGCH` is a valid trace too.

## Who writes and reads traces

The trace library in this directory writes them from a testbench ([How to write a simulation trace from a C or C++ testbench](how-to-write-trace.md)). The Verilator testbench of [tinytapeout-vga-videos](https://github.com/mithro/tinytapeout-vga-videos) writes its traces with it. [`synthtrace.py`](../../python/vgacap/synthtrace.py) writes what that testbench would record, without a simulator. [The vgacap command-line tools](../tools/README.md) read traces into frames, frame hashes and waveforms.

## The tb-v1 vectors

The tb-v1 sampler is a port of how that testbench decoded frames itself. Six small traces, with the frames and hashes the testbench wrote for them, sit in the repository as [the tb-v1 test vectors](../../tests/vectors/tbv1/reference-tbv1-vectors.md). A test samples each trace with tb-v1, and its output must equal the testbench's byte for byte.
