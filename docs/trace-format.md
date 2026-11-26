---
type: reference
owner: vgacap maintainers
reader: someone reading or writing a vgacap simulation trace, with or without vgacap
review: 2027-05-27
---

<!-- SPDX-License-Identifier: Apache-2.0 -->
# The simulation trace format

**The bytes of a vgacap simulation trace: its chunks, its 43-bit sample layout, its clock and time rules, and its zstd container.**

The writer is [`trace_write.c`](../src/trace/trace_write.c), declared in [`trace.h`](../include/vgacap/trace.h), and the reader is [`trace_read.c`](../src/trace/trace_read.c). [`stream.py`](../python/vgacap/stream.py) reads and writes the uncompressed stream. What a trace is for is on [What a simulation trace records](../src/trace/explanation-traces.md), and reading one with no vgacap code on [How to read a simulation trace without vgacap](how-to-read-trace.md).

In every table, Offset is in bytes from the start of the payload, or of the chunk where it says so. Type is `u8`, `u16`, `u32` or `u64`: an unsigned little-endian integer of that many bits. Field is the name the code uses, and Value what it holds.

## Chunks

The uncompressed trace is a sequence of chunks, with no padding and no sync word. Offset is from the start of the chunk.

| Offset | Type | Field | Value |
|---|---|---|---|
| 0 | 4 ASCII bytes | tag | the chunk's kind |
| 4 | u32 | `length` | the payload's size in bytes: 16 MiB at most |
| 8 | `length` bytes | payload | as the tag says |

Tag is the chunk's tag, and In a trace whether a trace may hold it. A reader skips a chunk whose tag it does not know, by its length.

| Tag | In a trace |
|---|---|
| `VGCH` | always the first chunk |
| `META` | yes |
| `BLKH` | yes |
| `RUNS` | yes |
| `TIME` | allowed |
| `RAW ` | never |
| `RLE ` | never |
| `FRAM` | never |
| `EVNT` | never |

## VGCH, version 2

The chunk's length is exactly 24 plus `desc_len`. Trace value is what a trace from the trace writer holds.

| Offset | Type | Field | Value | Trace value |
|---|---|---|---|---|
| 0 | u16 | `version` | 2 | 2 |
| 2 | u8 | `sample_bits` | 1 to 64 | 43 |
| 3 | u8 | `mode` | 0 extclk; 1 selfclk; 2 event; 3 unknown | 2 |
| 4 | u32 | `clock_hz` | the design clock in whole hertz: the only place it is given | the design's |
| 8 | 8 × u8 | `signal_map` | the sample bit of hsync, vsync, r1, r0, g1, g0, b1 and b0; `0xFF` for absent | 7 3 0 4 1 5 2 6 |
| 16 | u8 | `samples_per_word` | 0 when `sample_bits` is over 32; otherwise 1, 2, 4 or 8 | 0 |
| 17 | u8 | `flags` | bit 0: version 1 word packing | 0 |
| 18 | u16 | `desc_len` | 251 at most | 0 |
| 20 | u8 | `samples_per_clock` | 1; or 2 exactly when there is a clock bit | 2 |
| 21 | u8 | `clock_bit` | 0 to `sample_bits` − 1; or `0xFF` for none | 42 |
| 22 | u8 | `clock_first` | the clock bit's value in sample 0; 0 with no clock bit | 0 |
| 23 | u8 | reserved | 0 | 0 |
| 24 | `desc_len` bytes | description | UTF-8 | none |

## VGCH, version 1

A board capture's header. Item is what differs from version 2, and Version 1 its value there.

| Item | Version 1 |
|---|---|
| `sample_bits` | 8, 12, 16 or 32 |
| Bytes 20 to 23 | absent |
| Chunk length | 20 plus `desc_len` |
| `samples_per_clock` as read | 1 |
| `clock_bit` as read | none |

## Sample numbers

Rule is the name of the rule, and Value the rule.

| Rule | Value |
|---|---|
| A sample | a `sample_bits`-bit value |
| Sample number `n` | 0 after the `VGCH`; grows by each sample a `RUNS` chunk delivers; set by each `BLKH` |
| Clock bit of sample `n` | `clock_first` XOR (`n` AND 1): it is never stored |
| Samples a clock | `samples_per_clock`: one with the clock low and one with it high |

## RUNS

| Offset | Type | Field | Value |
|---|---|---|---|
| 0 | u32 | `pair_count` | at least 1 |
| 4 | pairs | pairs | `pair_count` of them |

The chunk's length is exactly 4 plus the pairs' bytes. Part is a part of one pair, and Encoding how it is stored.

| Part | Encoding | Value |
|---|---|---|
| `value` | `vbytes` = ceil(`sample_bits` ÷ 8) bytes, little-endian: 6 in a trace | the run's value; the clock bit and every bit at or above `sample_bits` stored as 0 |
| `run` | unsigned LEB128: 1 to 10 bytes; 7 bits a byte; low group first; top bit set when a byte follows | at least 1, below 2^64, minimally encoded: no final `0x00`, and a 10th byte at most `0x01` |

Rule is a rule of the pairs, and Value the rule.

| Rule | Value |
|---|---|
| Samples a pair stands for | `n` to `n` + `run` − 1, all with `value` apart from each one's own clock bit |
| Two adjacent pairs of one chunk | never the same value |
| A run across chunks or blocks | may continue with the same value: the writer cuts runs at block boundaries |

## BLKH

| Offset | Type | Field | Value |
|---|---|---|---|
| 0 | u64 | `first_sample` | the number of the block's first sample: it sets `n` |

In a trace every block starts with one, and it equals the number of samples before it.

## META

1 to 4096 bytes of UTF-8 text: `key=value` lines, each ending in `\n`, the last one too. A key matches `[a-z][a-z0-9_]*` and appears once, and the first line is `meta=open` or `meta=close`. Readers ignore keys they do not know.

Chunk is the `META` chunk, and Need whether a key is required. Value is the key's text.

| Chunk | Key | Need | Value |
|---|---|---|---|
| `META(open)`, in block 0 | `project` | required | the design's identifier, such as `tt07/tt_um_example` |
| `META(open)` | `calib_seconds` | required | the calibration length in seconds, a decimal number as C `strtod` reads it |
| `META(open)` | `seconds` | required | the run length in seconds, read the same way |
| `META(open)` | `layout` | required | `tt-um-43`: the bit layout below |
| `META(open)` | `override` | optional | |
| `META(open)` | `simulator` | optional | |
| `META(open)` | `harness` | optional | |
| `META(close)`, at the end of the last block | `capture_after_clock` | required | a plain decimal integer |
| `META(close)` | `frames` | required | a plain decimal integer |
| `META(close)` | `status` | required | `ok`, `sim-timeout`, `unstable-sync`, `no-sync` or `bad-timing`, as in [`frames.txt`](frames-format.md) |

A trace without `META(close)` was cut short: its status is `truncated`.

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

Rule is the name of the rule, and Value the rule. Clock values in `META` and in `frames.txt` use this numbering.

| Rule | Value |
|---|---|
| Clock `c` | samples 2`c` (clock low) and 2`c` + 1 (clock high) |
| Clock 0 | the first clock of reset |
| Sample 2`c` | the outputs registered at clock `c` − 1's rising edge; the inputs as driven for clock `c` |
| Sample 2`c` + 1 | the outputs of clock `c`'s own rising edge |
| Clock 0's low sample | repeats its high sample's outputs |
| Time of sample `n` | round(`n` × 10^12 ÷ (`samples_per_clock` × `clock_hz`)) picoseconds, halves rounded up |
| How the time is computed | exactly, in 128-bit integers from the sample number: [`time.c`](../src/stream/time.c), `vgacap_sample_time_ps` |
| `clock_hz` of 0 | the time is unknown |

## The container

A trace file, named `.trace.zst` by convention, is a zstd seekable-format file (`contrib/seekable_format` in zstd 1.5.7). Part is a part of the file, and Value what it is.

| Part | Value |
|---|---|
| A block | one ordinary zstd frame, with its content size and its XXH64 checksum |
| Block 0 | `VGCH`, `META(open)`, `BLKH(0)`, then `RUNS` |
| Every later block | `BLKH(first_sample)`, then `RUNS` |
| The last block | ends with `META(close)` |
| All blocks in order | one valid VGCH stream: `zstd -d` on the file gives it whole |
| Largest block, in clocks | 2^24 clocks from its first sample |
| Largest block, in bytes | 16 MiB uncompressed; a block closes before it would pass 16 MiB − 64 KiB |
| A run at a block boundary | split, so no run crosses a `BLKH` |
| The seek table | a skippable frame at the very end: u32 magic `0x184D2A5E`, then u32 size of what follows |
| A seek table entry, one a block | compressed size u32; decompressed size u32; the low 32 bits of the block's XXH64 |
| The seek table footer | u32 number of frames; a descriptor byte with bit 7 set for checksums; u32 magic `0x8F92EAB1` |
| Block `i`'s first sample | its `BLKH`, read by decompressing only the start of its frame |
| Samples `from` to `to` | the last block starting at or below `from`, and each later block starting below `to` |
| A plain uncompressed VGCH version 2 stream | also a valid trace: one block, read from its start |

## A trace cut short

A killed run leaves no seek table, and perhaps a half-written last frame. Case is what the reader finds, and Reader the rule it follows.

| Case | Reader |
|---|---|
| No seek table | walks the frames from the start by their headers (`ZSTD_findFrameCompressedSize`) |
| Frames kept | every complete frame up to the first incomplete one or the first skippable frame |
| The last kept frame | decompressed whole; dropped if its checksum fails, and the one before it tried |
| A seek table that does not agree with the frames | rebuilt the same way |
| Disagreement | an entry whose offset, frame header or size does not match; a table that does not end the file |
| Samples of a rebuilt trace | those of the kept blocks |
| Status with no `META(close)` | `truncated` |
