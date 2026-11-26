---
type: reference
owner: vgacap maintainers
reader: someone reading or writing a vgacap simulation trace, with or without vgacap
review: 2027-05-27
---

<!-- SPDX-License-Identifier: Apache-2.0 -->
# The simulation trace format

**What makes a vgacap stream a simulation trace: its header values, its `META` keys, its 43-bit sample layout, its clocks, and its zstd container.**

A trace is a version 2 vgacap stream. Its chunk bytes, header fields, chunk encodings and sample index rules are on [the stream format](../src/stream/reference-stream-format.md). The writer is [`trace_write.c`](../src/trace/trace_write.c), declared in [`trace.h`](../include/vgacap/trace.h), and the reader is [`trace_read.c`](../src/trace/trace_read.c). What a trace is for is on [What a simulation trace records](../src/trace/explanation-traces.md).

## The header

Field is a field of the [`VGCH` header](../src/stream/reference-stream-format.md#vgch-the-header), and Trace value what the trace writer puts in it.

| Field | Trace value |
|---|---|
| `version` | 2 |
| `sample_bits` | 43 |
| `mode` | 2: event |
| `clock_hz` | the design clock in whole hertz |
| `signal_map` | 7 3 0 4 1 5 2 6: the Tiny VGA Pmod on `uo_out` |
| `samples_per_word` | 0 |
| `flags` | 0 |
| `desc_len` | 0 |
| `samples_per_clock` | 2 |
| `clock_bit` | 42 |
| `clock_first` | 0 |

## META

The keys a trace writes in its two `META` chunks. The chunk's syntax is on [META in the stream format](../src/stream/reference-stream-format.md#meta-metadata-version-2), and readers ignore keys they do not know.

Chunk is the `META` chunk, and Need whether a key is required. Value is the key's text.

| Chunk | Key | Need | Value |
|---|---|---|---|
| `META(open)` in block 0 | `project` | required | the design's identifier as free text: `tt07/tt_um_example` for one |
| `META(open)` | `calib_seconds` | required | the calibration length in seconds: an optional sign, digits with an optional point, an optional exponent |
| `META(open)` | `seconds` | required | the run length in seconds: read the same way |
| `META(open)` | `layout` | required | `tt-um-43`: the bit layout below |
| either | `override` | optional | free text |
| either | `simulator` | optional | free text |
| either | `harness` | optional | free text |
| `META(close)` at the end of the last block | `capture_after_clock` | required | an unsigned decimal integer below 2^64 |
| `META(close)` | `frames` | required | an unsigned decimal integer below 2^64 |
| `META(close)` | `status` | required | `ok`, `sim-timeout`, `unstable-sync`, `no-sync` or `bad-timing` |

`truncated` is never written in a `META`. A reader gives a trace that has no `META(close)` the status `truncated`, because it was cut short. The C reader skips a `META` chunk whose value breaks this table, and reports `bad META text`.

## The tt-um-43 bit layout

Bits are the sample bits, and Signal the Tiny Tapeout pin they hold.

| Bits | Signal |
|---|---|
| 0 to 7 | `uo_out[7:0]`: bit 0 is `uo_out[0]` |
| 8 to 15 | `uio_out[7:0]` |
| 16 to 23 | `uio_oe[7:0]` |
| 24 to 31 | `ui_in[7:0]` |
| 32 to 39 | `uio_in[7:0]` |
| 40 | `rst_n` |
| 41 | `ena` |
| 42 | `clk`: the clock bit |

Signal is a Tiny VGA Pmod signal, and `uo_out` bit the bit that carries it, as `signal_map` says.

| Signal | `uo_out` bit |
|---|---|
| hsync | 7 |
| vsync | 3 |
| r1 | 0 |
| r0 | 4 |
| g1 | 1 |
| g0 | 5 |
| b1 | 2 |
| b0 | 6 |

## Clocks and time

Rule is the name of the rule, and Value the rule. Clock values in `META` and in [`frames.txt`](frames-format.md) use this numbering. The time of a sample, and its clock bit, follow [the stream format's sample index rules](../src/stream/reference-stream-format.md#the-sample-index-the-clock-bit-and-time).

| Rule | Value |
|---|---|
| Clock `c` | samples 2`c` (clock low) and 2`c` + 1 (clock high) |
| Clock 0 | the first clock of reset |
| Sample 2`c` | the outputs registered at clock `c` − 1's rising edge; the inputs as driven for clock `c` |
| Sample 2`c` + 1 | the outputs of clock `c`'s own rising edge |
| Clock 0's low sample | repeats its high sample's outputs |
| Time of clock `c` | the time of sample 2`c` |

## The container

A trace file, named `.trace.zst` by convention, is a zstd seekable-format file (`contrib/seekable_format` in zstd 1.5.7). Part is a part of the file, and Value what it is.

| Part | Value |
|---|---|
| A block | one ordinary zstd frame with its content size and XXH64 checksum |
| Block 0 | `VGCH` then `META(open)` then `BLKH(0)` then `RUNS` |
| Every later block | `BLKH(first_sample)` then `RUNS` |
| The last block | ends with `META(close)` |
| All blocks in order | one valid VGCH stream: `zstd -d` on the file gives it whole |
| Largest block in clocks | 2^24 clocks from its first sample |
| Largest block in bytes | 16 MiB uncompressed: it closes before it would pass 16 MiB − 64 KiB |
| A run at a block boundary | split: no run crosses a `BLKH` |
| The seek table | a skippable frame at the very end: u32 magic `0x184D2A5E` then u32 size of what follows |
| A seek table entry for each block | compressed size u32; decompressed size u32; the low 32 bits of the block's XXH64 |
| The seek table footer | u32 number of frames; a descriptor byte with bit 7 set for checksums; u32 magic `0x8F92EAB1` |
| Block `i`'s first sample | its `BLKH`: read by decompressing only the start of its frame |
| Samples `from` to `to` | the last block starting at or below `from` and each later block starting below `to` |
| A plain uncompressed VGCH version 2 stream | also a valid trace: one block read from its start |

## A trace cut short

A killed run leaves no seek table, and perhaps a half-written last frame. Case is what the reader finds, and Reader the rule it follows.

| Case | Reader |
|---|---|
| No seek table | walks the frames from the start by their headers (`ZSTD_findFrameCompressedSize`) |
| Frames kept | every complete frame up to the first incomplete one or the first skippable frame |
| The last kept frame | decompressed whole; if its checksum fails it is dropped and the one before it tried |
| A seek table that does not agree with the frames | rebuilt the same way |
| Disagreement | an entry whose offset or frame header or size does not match; a table that does not end the file |
| Samples of a rebuilt trace | those of the kept blocks |
| Status with no `META(close)` | `truncated` |
