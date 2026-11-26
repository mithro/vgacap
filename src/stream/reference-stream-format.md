---
type: reference
owner: vgacap maintainers
reader: someone reading or writing vgacap stream bytes without the vgacap code
review: 2027-05-27
---

# The vgacap stream format

**You want the exact bytes of a vgacap stream: its header, every chunk type, every flag and every rule a reader applies.**

The C writer and reader are [`src/stream/writer.c`](writer.c) and [`src/stream/reader.c`](reader.c), declared in [`include/vgacap/stream.h`](../../include/vgacap/stream.h). [`python/vgacap/stream.py`](../../python/vgacap/stream.py) reads and writes the same bytes. Why the format has this shape is on [The vgacap stream](explanation-stream.md); what a simulation trace adds is on [the simulation trace format](../../docs/trace-format.md).

All integers are unsigned and little-endian. In the byte tables, Offset is the byte offset in the payload, Type the field's width, and Field what it holds.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/diagrams/stream-chunks-dark.svg">
  <img src="../../docs/diagrams/stream-chunks.svg" alt="A chunk is a 4-byte tag, a u32 length and a payload; a capture is a VGCH header chunk, then RAW sample chunks and TIME chunks." width="560">
</picture>

## Chunk framing

| Offset | Type | Field |
|---|---|---|
| 0 | 4 ASCII bytes | tag |
| 4 | u32 | `length`: the payload's size in bytes, at most 16 MiB (16,777,216) |
| 8 | `length` bytes | payload |

- Chunks follow each other with no padding and no sync word.
- The first chunk is `VGCH`. A later `VGCH` starts the stream again: the sample index goes back to 0.
- Tags end in a space where the name is three letters: `RAW ` and `RLE `.
- A tag the reader does not know is skipped by its length. Under a header with a clock bit, it is also reported ([what a reader does](#what-a-reader-does-with-a-bad-chunk)).

## Chunk types

| Tag | Version 1 | Version 2 | Holds |
|---|---|---|---|
| `VGCH` | yes | yes | the header |
| `RAW ` | yes | yes | samples packed into 32-bit words |
| `RLE ` | yes | no | (value, run) pairs of 32 bits each |
| `FRAM` | yes | yes | a frame, or a window of lines, packed as `RAW ` |
| `EVNT` | yes | yes | sample values at increasing clocks |
| `TIME` | yes | yes | host time, a clock rate, dropped samples and a message |
| `RUNS` | no | yes | (value, run) pairs, the run in LEB128 |
| `BLKH` | no | yes | the sample index of the next sample |
| `META` | no | yes | `key=value` text |

## VGCH: the header

| Offset | Type | Field | Version 1 | Version 2 |
|---|---|---|---|---|
| 0 | u16 | `version` | 1 | 2 |
| 2 | u8 | `sample_bits` | see field values below | 1 to 64 |
| 3 | u8 | `mode` | see mode below | see mode below |
| 4 | u32 | `clock_hz` | the project clock in Hz; 0 is unknown | the same |
| 8 | u8[8] | `signal_map` | see signal map below | the same |
| 16 | u8 | `samples_per_word` | see field values below | see field values below |
| 17 | u8 | `flags` | see flags below | the same |
| 18 | u16 | `desc_len` | 0 to 255 | 0 to 251 |
| 20 | u8 | `samples_per_clock` | absent: reads as 1 | 1 without a clock bit; 2 with one |
| 21 | u8 | `clock_bit` | absent: reads as none | 0 to `sample_bits` - 1; `0xFF` for none |
| 22 | u8 | `clock_first` | absent: reads as 0 | the clock bit in sample 0, 0 or 1; 0 without a clock bit |
| 23 | u8 | reserved | absent | 0 |
| 20 in version 1; 24 in version 2 | `desc_len` bytes | `desc` | free text, UTF-8 | the same |

| Item | Version 1 | Version 2 |
|---|---|---|
| Fixed part | 20 bytes | 24 bytes |
| Chunk length | exactly 20 + `desc_len` | exactly 24 + `desc_len` |
| Largest chunk | 275 bytes | 275 bytes, so a version 1 reader reaches its version check |

### VGCH field values

Field names the field, When the header it applies to, and Allowed one allowed value.

| Field | When | Allowed |
|---|---|---|
| `sample_bits` | version 1 | 8 |
| `sample_bits` | version 1 | 12 |
| `sample_bits` | version 1 | 16 |
| `sample_bits` | version 1 | 32 |
| `samples_per_word` | `sample_bits` 32 or fewer | 1 |
| `samples_per_word` | `sample_bits` 32 or fewer | 2 |
| `samples_per_word` | `sample_bits` 32 or fewer | 4 |
| `samples_per_word` | `sample_bits` 32 or fewer | 8 |
| `samples_per_word` | version 2, `sample_bits` over 32 | 0 |

`samples_per_word` × `sample_bits` is at most 32.

### mode

| Value | Name | Meaning |
|---|---|---|
| 0 | extclk | sampled on the project's own clock edge |
| 1 | selfclk | the capture hardware made the clock |
| 2 | event | change events from a simulator or analyser |
| 3 | unknown | not stated |

### flags

| Bit | Name | Meaning when set |
|---|---|---|
| 0 | `FIRST_SAMPLE_MSB` | a word's first sample is in its most significant slot |
| 1 to 7 | -- | written as 0 |

### signal map

Byte is the byte of `signal_map`, and Signal the signal whose sample bit it holds; `0xFF` means the signal is absent.

| Byte | Signal |
|---|---|
| 0 | hsync |
| 1 | vsync |
| 2 | r1 |
| 3 | r0 |
| 4 | g1 |
| 5 | g0 |
| 6 | b1 |
| 7 | b0 |

| Map, bytes 0 to 7 | Wiring |
|---|---|
| `7 3 0 4 1 5 2 6` | the [Tiny VGA Pmod](https://github.com/mole99/tiny-vga) on `uo_out[7:0]`, bit 0 = `uo_out[0]` |
| `11 3 0 8 1 9 2 10` | a 12-bit read from GPIO 5 on an RP2040 demo board |

## RAW: packed samples

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | `sample_count` |
| 4 | u32 words | ceil(`sample_count` / `samples_per_word`) words |

- Sample i is in word i / `samples_per_word`, slot i % `samples_per_word`, shifted left by slot × `sample_bits`.
- With `FIRST_SAMPLE_MSB` the slot is `samples_per_word` - 1 - (i % `samples_per_word`).
- The word count matches the chunk's length exactly.

## RLE: 32-bit runs

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | `pair_count` |
| 4 | (u32, u32) pairs | `value`, then `run`: `run` samples of `value` |

The chunk's length is exactly 4 + 8 × `pair_count`.

## FRAM: a frame or a window

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | `frame_counter` |
| 4 | u16 | `first_line`: the frame line the samples start on |
| 6 | u16 | `line_count` |
| 8 | u32 | `clocks_per_line` |
| 12 | u32 | `sample_count` |
| 16 | u32 words | the samples, packed as in `RAW ` |

- The samples start at the leading edge of the hsync pulse of `first_line`.
- Line 0 is the first line of the vsync pulse.
- Chunks with the same `frame_counter` are windows of one frame.

## EVNT: change events

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | `event_count` |
| 4 | (u64, u32) events | `clock`, then `value` |

- Clocks strictly increase.
- Each value holds until the next event's clock; the last holds for one clock.
- The chunk's length is exactly 4 + 12 × `event_count`.

## TIME: time and losses

| Offset | Type | Field |
|---|---|---|
| 0 | u64 | `host_time_ns` |
| 8 | u32 | `clock_hz`: the clock rate as measured or set at this point |
| 12 | u32 | `dropped_samples`: the stream's total so far, never an increment |
| 16 | u16 | `msg_len`: at most 255, and exactly the chunk's length - 18 |
| 18 | `msg_len` bytes | `msg` |

- A rise in `dropped_samples`, or `msg` `overrun`, marks an overrun: the samples before it do not continue into those after.
- A reader takes the last `dropped_samples`, never a sum.

## RUNS: LEB128 runs (version 2)

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | `pair_count`: at least 1 |
| 4 | pairs | `value`, then `run` |

- `value` is ceil(`sample_bits` / 8) bytes, little-endian: 6 bytes for a simulation trace's 43-bit samples.
- In `value`, the clock bit is stored as 0, and no bit at or above `sample_bits` is set.
- `run` is unsigned LEB128: 7 bits a byte, least significant group first, the top bit set when another byte follows.
- `run` is 1 to 10 bytes, at least 1, minimally encoded (no final `0x00`), and below 2^64; a 10th byte is at most `0x01`.
- A pair is `run` consecutive samples, n to n + `run` - 1.
- Two adjacent pairs of one chunk never share a value; a run may go on in the next chunk with the same value.
- The chunk's length is exactly 4 plus its pairs' bytes.

## BLKH: block header (version 2)

| Offset | Type | Field |
|---|---|---|
| 0 | u64 | `first_sample`: the index of the next sample |

- The length is exactly 8.
- A `first_sample` that is not the running index is reported, then adopted.

## META: metadata (version 2)

- 1 to 4096 bytes of strict UTF-8: `key=value` lines, each ending in `\n`, the last too.
- A key matches `[a-z][a-z0-9_]*` and appears once in a chunk.
- The first line is `meta=open` or `meta=close`.
- A reader ignores a key it does not know.
- The keys a trace writes, and their values, are on [the simulation trace format](../../docs/trace-format.md#meta).

## The sample index, the clock bit and time

| Item | Rule |
|---|---|
| Sample index n | 0 after a `VGCH`; grows by every sample delivered; set by every `BLKH` |
| Clock bit of sample n | `clock_first` XOR (n AND 1), never stored |
| Samples a clock | `samples_per_clock`: 2 with a clock bit, one low and one high |
| Time of sample n | round(n × 10^12 / (`samples_per_clock` × `clock_hz`)) ps, halves rounded up, in exact integer arithmetic |
| Time past 2^64 - 1 ps | 2^64 - 1 |
| `clock_hz` 0 | the clock is unknown, so samples have no time |

The time rule is [`vgacap_sample_time_ps`](time.c) in `src/stream/time.c`, mirrored by [`python/vgacap/timebase.py`](../../python/vgacap/timebase.py). With `clock_hz` 0 that function returns 0 for every sample (`time.c` line 57), a placeholder, not a time. [`vgacap-export`](../tools/reference-options.md#vgacap-export) refuses such a header and exits 1 (`vgacap_export.c` line 603).

## Chunks a header allows

Allowed means the chunk is read; skipped means it is skipped by its length and reported.

| Tag | Version 1 | Version 2, no clock bit, 32 bits or fewer | Version 2, no clock bit, over 32 bits | Version 2 with a clock bit |
|---|---|---|---|---|
| `RAW ` | allowed | allowed | skipped | skipped |
| `FRAM` | allowed | allowed | skipped | skipped |
| `EVNT` | allowed | allowed | skipped | skipped |
| `RLE ` | allowed | skipped | skipped | skipped |
| `TIME` | allowed | allowed | allowed | allowed |
| `RUNS` | skipped | allowed | allowed | allowed |
| `BLKH` | skipped | allowed | allowed | allowed |
| `META` | skipped | allowed | allowed | allowed |
| an unknown tag | skipped silently | skipped silently | skipped silently | skipped |

## What a reader does with a bad chunk

The C reader reports a loss as a resync event, with the bytes lost and a reason, and goes on. The Python reader raises `ValueError` and stops. In the table, C reason is the C reader's text and C action what it does. Scan means it searches the input byte by byte for the next known tag with a plausible length; skip means it steps over the chunk by its length. Python message is what the Python reader raises: the same text, the C text followed by detail, or its own text.

| Fault | C reason | C action | Python message |
|---|---|---|---|
| a `version` other than 1 or 2 | `unsupported version` | stop: an error, not a resync | the C text, then detail |
| a tag that is not printable | `non-printable chunk tag` | scan | the C text, then detail |
| a length over 16 MiB | `chunk length too large` | scan | `chunk 'TAG' at offset N declares N bytes (max 16777216)` |
| a `VGCH` length under 20 or over 275 | `bad VGCH length` | scan | the same text |
| `desc_len` that does not match the `VGCH` length | `VGCH desc length mismatch` | scan | the C text, then detail |
| a `sample_bits` not allowed | `bad sample_bits` | scan | the C text, then detail |
| a `samples_per_word` not allowed | `bad samples_per_word` | scan | the C text, then detail |
| bad version 2 clock fields | `bad VGCH v2 clock fields` | scan | the C text, then detail |
| a chunk before the first `VGCH` | `chunk before header` | scan | the same text |
| a length under the tag's fixed part | `bad RAW length` (and so on for each tag) | scan | the C text, then detail |
| a `RAW ` or `FRAM` count that does not fill the length | `RAW sample count does not match chunk length` (or `FRAM`) | scan | `'RAW ' declares N samples but its length holds N words` (or `'FRAM'`) |
| an `RLE ` length that is not whole pairs | `RLE pair count does not match chunk length` | scan | `'RLE ' payload is not a whole number of pairs` |
| an `RLE ` count that does not fill the length | `RLE pair count does not match chunk length` | scan | `'RLE ' declares pair_count=N but its length holds N` |
| an `EVNT` length that is not whole events | `EVNT event count does not match chunk length` | scan | `'EVNT' payload is not a whole number of events` |
| an `EVNT` count that does not fill the length | `EVNT event count does not match chunk length` | scan | `'EVNT' declares event_count=N but its length holds N` |
| an `EVNT` clock that does not increase | `EVNT clock not increasing` | scan | `event clock not increasing` |
| a `TIME` `msg_len` over 255 | `TIME msg_len does not match chunk length` | scan | `'TIME' msg_len N exceeds 255` |
| a `TIME` `msg_len` that does not fill the length | `TIME msg_len does not match chunk length` | scan | `'TIME' declares msg_len=N but its length holds N` |
| a chunk the header does not allow | `chunk needs a version 2 header` (and the like) | skip | the same text |
| a bad `RUNS` pair | `RUNS run is zero` (and the like) | skip | the same text |
| a `RUNS` chunk while n is unknown | `RUNS discarded: sample position unknown until BLKH` | skip | none: the Python reader never loses n |
| a `BLKH` length other than 8 | `bad BLKH length` | skip | the same text |
| a `BLKH` that does not follow | `BLKH first_sample does not follow` | adopt its `first_sample` and go on | the C text, then detail, and stop |
| a `META` length of 0 or over 4096 | `bad META length` | skip | the same text |
| `META` text that breaks its rules | `bad META text` | skip | the same text |
| a chunk cut off by the end of the input | none | wait for more input | `truncated chunk 'TAG'` |
| input that ends while scanning | none: the caller reports it | stop | none |
| a stream with no `VGCH` | none | none | `no header` |

Under a header with a clock bit, n becomes unknown after any loss that could have carried samples. Until the next `BLKH`, every `RUNS` chunk is skipped. A lost `META`, or a scan that lost exactly one `TIME` chunk, leaves n known.

[`vgacap-dump`](../tools/reference-options.md#vgacap-dump) prints each resync as a `resync skipped=N reason=...` line.
