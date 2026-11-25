<!-- SPDX-License-Identifier: Apache-2.0 -->
# Simulation trace format

A simulation trace records every pin of a Tiny Tapeout design for every half
clock of a simulation. It is a VGCH version 2 capture stream (the chunk
format the capture boards also use), cut into blocks, each block compressed
as one frame of zstd's seekable format. This page is enough to read a trace
without vgacap: a zstd decoder and the rules below.

The writer is `src/trace/trace_write.c` (`include/vgacap/trace.h`), the
reader `src/trace/trace_read.c`, and `python/vgacap/stream.py` reads and
writes the uncompressed stream. `vgacap-dump TRACE` prints a trace's blocks
and chunks; `vgacap-export` turns one into VCD or FST.

All integers are little-endian. "u8/u16/u32/u64" are unsigned integers of
that many bits.

## Chunks

The uncompressed stream is a sequence of chunks:

| offset | size | field |
|---|---|---|
| 0 | 4 | tag, four ASCII bytes |
| 4 | u32 | `length`, the payload's size in bytes (at most 16 MiB) |
| 8 | `length` | payload |

There is no padding and no sync word. A reader skips a chunk whose tag it
does not know by its length. The first chunk is always `VGCH`. The tags a
trace uses are `VGCH`, `META`, `BLKH` and `RUNS` (and `TIME` may appear: a
stream with a clock bit may hold only `RUNS`, `BLKH`, `META` and `TIME`).
The version 1 chunks `RAW `, `RLE `, `FRAM` and `EVNT` never appear in a
trace.

## VGCH, version 2

| offset | type | field |
|---|---|---|
| 0 | u16 | `version` = 2 |
| 2 | u8 | `sample_bits`, 1..64 (43 for a trace) |
| 3 | u8 | `mode`: 0 extclk, 1 selfclk, 2 event, 3 unknown (a trace writes 2) |
| 4 | u32 | `clock_hz`, the design clock in whole hertz; the only place the clock is given |
| 8 | u8[8] | `signal_map`: the sample bit of hsync, vsync, r1, r0, g1, g0, b1, b0; 0xFF = absent |
| 16 | u8 | `samples_per_word`: 0 when `sample_bits` > 32 (a trace: 0), else 1, 2, 4 or 8 |
| 17 | u8 | `flags` (bit 0: version 1 word packing; 0 in a trace) |
| 18 | u16 | `desc_len`, at most 251 |
| 20 | u8 | `samples_per_clock`: 1, or 2 exactly when there is a clock bit |
| 21 | u8 | `clock_bit`: 0..`sample_bits` - 1, or 0xFF for none |
| 22 | u8 | `clock_first`: the clock bit's value in sample 0 (0 when there is no clock bit) |
| 23 | u8 | reserved, 0 |
| 24 | `desc_len` | description, UTF-8 (a trace writes none) |

The chunk's length is exactly `24 + desc_len`. A trace's header is
`sample_bits` 43, `samples_per_clock` 2, `clock_bit` 42, `clock_first` 0,
`signal_map` {7, 3, 0, 4, 1, 5, 2, 6} (the Tiny VGA Pmod on `uo_out`).

(Version 1, the board captures, has the same first 20 bytes with
`sample_bits` 8, 12, 16 or 32, no bytes 20-23, and a chunk length of
`20 + desc_len`; it reads as `samples_per_clock` 1 and no clock bit.)

## Samples and the sample number

Every sample is a `sample_bits`-bit value. The samples are numbered from 0
in stream order: `n`, the number of the next sample, is 0 after the VGCH,
grows by every sample a `RUNS` chunk delivers, and is set by every `BLKH`.

When there is a clock bit, its value is not stored: sample `n`'s clock bit is
`clock_first ^ (n & 1)`, so it alternates, one sample with the clock low and
one with it high, `samples_per_clock` = 2 samples a clock.

### RUNS

| offset | type | field |
|---|---|---|
| 0 | u32 | `pair_count`, at least 1 |
| 4 | | `pair_count` pairs |

A pair is:

- `value`: `vbytes = ceil(sample_bits / 8)` bytes (6 for a trace),
  little-endian; the clock bit is stored as 0, and every bit at or above
  `sample_bits` is 0;
- `run`: an unsigned LEB128 number of 1 to 10 bytes (7 bits a byte, least
  significant group first, the top bit of each byte set when another byte
  follows), at least 1, minimally encoded (no final `0x00` after a byte with
  its top bit set), and below 2^64 (a 10th byte is at most `0x01`).

The pair is `run` consecutive samples, `n` to `n + run - 1`, all with that
value apart from the clock bit, which each sample takes from its own number
as above. Two adjacent pairs of one chunk never have the same value; a run
may still continue in the next chunk or block with the same value (the
writer cuts runs at block boundaries). The chunk's length is exactly 4 plus
the pairs' bytes.

### BLKH

| offset | type | field |
|---|---|---|
| 0 | u64 | `first_sample`, the number of the block's first sample |

It sets `n`. In a trace every block starts with one, and it always equals
the number of samples before it.

### META

1 to 4096 bytes of UTF-8 text: `key=value` lines, each ending in `\n`
(the last too). A key is `[a-z][a-z0-9_]*` and appears once. The first line
is `meta=open` or `meta=close`.

- `META(open)`, in block 0, required keys `project` (the design's
  identifier, e.g. `tt07/tt_um_example`), `calib_seconds` and `seconds`
  (decimal numbers as C `strtod` reads them: the calibration and the run
  length in seconds) and `layout` (`tt-um-43`, the bit layout below);
  optional `override`, `simulator`, `harness`.
- `META(close)`, at the end of the last block, required keys
  `capture_after_clock` and `frames` (plain decimal integers) and `status`
  (`ok`, `sim-timeout`, `unstable-sync`, `no-sync` or `bad-timing`; see
  `docs/frames-format.md`). A trace without it was cut short (status
  `truncated`).

Readers ignore keys they do not know.

## The tt-um-43 bit layout

`layout=tt-um-43` names this 43-bit sample:

| bits | signal |
|---|---|
| 0-7 | `uo_out[7:0]` (bit 0 = `uo_out[0]`) |
| 8-15 | `uio_out[7:0]` |
| 16-23 | `uio_oe[7:0]` |
| 24-31 | `ui_in[7:0]` |
| 32-39 | `uio_in[7:0]` |
| 40 | `rst_n` |
| 41 | `ena` |
| 42 | `clk` (the clock bit) |

The Tiny VGA Pmod's signals are on `uo_out`, as `signal_map` says: hsync
bit 7, vsync 3, r1 0, r0 4, g1 1, g0 5, b1 2, b0 6.

## Clocks and time

- **Clock numbering.** Clock `c` is samples `2c` (clk low) and `2c + 1`
  (clk high). Clock 0 is the first clock of reset. Sample `2c` holds the
  outputs the design registered at clock `c - 1`'s rising edge and the
  inputs (`rst_n`, `ui_in`, ...) as driven for clock `c`; sample `2c + 1`
  holds the outputs of clock `c`'s own rising edge. (Clock 0's low sample
  repeats its high sample's outputs.) All clock values in META and in
  `frames.txt` use this numbering.
- **Time.** Sample `n` is at `round(n * 10^12 / (samples_per_clock *
  clock_hz))` picoseconds, halves rounded up, computed exactly (in 128-bit
  integers in `src/stream/time.c`, `vgacap_sample_time_ps`) from the
  absolute sample number, never by adding up per-sample steps in floating
  point. So clock `c` begins at the time of sample `2c`. `clock_hz` 0 means
  the time is unknown.

## The container

A trace file (conventionally `.trace.zst`) is a zstd seekable-format file
(`contrib/seekable_format` in zstd 1.5.7):

1. **Blocks.** Each block is one ordinary zstd frame, written with its
   content size and its XXH64 checksum. Block 0 decompresses to `VGCH`,
   `META(open)`, `BLKH(0)`, then `RUNS`; every later block to
   `BLKH(first_sample)` then `RUNS`; the last block ends with
   `META(close)`. Concatenated, the blocks are one valid VGCH stream, so
   `zstd -d` on the file gives the whole stream.
2. **Size of a block.** A block closes when it has `2^24` clocks (`2^24 *
   samples_per_clock` samples) counted from its first sample, or before its
   uncompressed size would pass 16 MiB - 64 KiB. A run that would cross the
   boundary is split, so no run crosses a `BLKH`. A block is at most 16 MiB
   uncompressed.
3. **The seek table** is a skippable frame (u32 magic `0x184D2A5E`, u32
   size of what follows) at the very end of the file, in zstd's seekable
   format with checksums: one entry a
   block (compressed size u32, decompressed size u32, the low 32 bits of the
   block's XXH64), then the footer (u32 number of frames, a descriptor byte
   with bit 7 set for "checksums present", u32 magic `0x8F92EAB1`). Block
   `i`'s first sample is its `BLKH`, the first chunk of its frame, read by
   decompressing only the start of the frame. To read samples `[from, to)`,
   find the last block whose first sample is at or below `from` and decode
   it and the blocks after it that start before `to`.

### Rebuilding a trace cut short

A trace that was killed has no seek table and possibly a half-written last
frame. A reader then walks the frames from the start of the file, frame
after frame by their headers (`ZSTD_findFrameCompressedSize`), and keeps
every complete frame up to the first incomplete one (or a skippable frame).
The last kept frame is decompressed whole; if its checksum fails, it is
dropped and the one before it is tried. The table is also rebuilt this way
when it is present but does not agree with the frames (an entry whose
offset, frame header or decompressed size does not match, or a table that
does not run exactly to the end of the file). The samples are those of the
kept blocks; with no `META(close)` the status is `truncated`.

A plain (uncompressed) VGCH v2 stream, starting with the bytes `VGCH`, is
also a valid trace: one pseudo-block, read from its start.

## Reading a trace without vgacap

1. Decompress the whole file with `zstd -d` (or each frame as needed, using
   the seek table).
2. Parse chunks. From `VGCH` take `sample_bits`, `samples_per_clock`,
   `clock_bit`, `clock_first` and `clock_hz`; from the first `META`, the
   `layout`.
3. Keep `n`; for each `RUNS` pair, samples `n .. n + run - 1` have the value
   with the clock bit set to `clock_first ^ (sample & 1)`; set `n` at each
   `BLKH`.
4. Time of sample `n` as above; clock `n >> 1`.
