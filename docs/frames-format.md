---
type: reference
owner: vgacap maintainers
reader: someone reading or writing a sampler's frames.txt or frame hash files
review: 2027-05-27
---

<!-- SPDX-License-Identifier: Apache-2.0 -->
# `frames.txt` and `frame_hashes.<method>.txt`

**You want the exact format of the text files a sampler writes for a simulation trace: the frame list and the hash of every frame.**

The C writer is [`src/sample/outputs.c`](../src/sample/outputs.c); [`python/vgacap/framesfile.py`](../python/vgacap/framesfile.py) is a second writer and a reader that refuses anything else, and the two writers write the same bytes. [`vgacap-sample`](../src/tools/reference-options.md#vgacap-sample) writes the files. In the tables, Key is the key as written, and Value what follows `key: `.

## Text rules

| Item | Rule |
|---|---|
| Encoding | UTF-8, LF line endings, a newline after every line, the last too |
| Header line | `key: value`: the key, a colon, one space, a non-empty value |
| Key order | exactly as listed below |
| Integer | plain decimal, no leading zeros |
| Text value | one non-empty line |
| Double | the first `%.{p}g`, p from 1 to 17, that reads back to the same double with no exponent; else the first that reads back |
| Double examples | `1`, never `1.0`; `0.9523809523809523` |

## Units

| Unit | Meaning |
|---|---|
| clock | one cycle of the design clock, numbered from 0, the first reset clock |
| pixel | a position on a line of the decoded frame, 0 to `width` - 1 |
| line | a line of the decoded frame, 0 to `height` - 1 |
| rgb6 | one byte a pixel: `R<<4 \| G<<2 \| B`, 2 bits a channel, the channel's first Tiny VGA bit the more significant |

## Keys in both files

| Key | Value |
|---|---|
| `id` | the project's name from the trace, as `tt07/tt_um_example` |
| `status` | how the run ended; see status |
| `sampler` | `tb-v1` or `centre-v1`, the pixel-picking method |
| `sampler_status` | whether the sampler found a picture; see sampler_status |
| `mode` | a name from [the VGA mode table](../src/frame/reference-modes.md), or `unknown` |
| `width` | the frame width in pixels; 0 with no frames |
| `height` | the frame height in lines; 0 with no frames |
| `clock_hz` | the design clock in Hz, an integer |
| `clocks_per_pixel` | design clocks a pixel, a double |
| `capture_after_clock` | the clock the sync calibration ends at, where frame counting starts |

### status

| Value | Meaning |
|---|---|
| `ok` | the simulation finished |
| `sim-timeout` | the wall-clock limit stopped the run, or it made under half its target frames |
| `unstable-sync` | frame length varied by over 1%, or over 1% of lines were short or long |
| `no-sync` | calibration saw no periodic hsync and vsync |
| `bad-timing` | sync was seen, but is not a plausible raster |
| `truncated` | the trace has no closing record |

### sampler_status

A run can have `status` `ok` and `sampler_status` `bad-timing`. With any value but `ok`, `frames` is 0 and there are no distinct frames or runs.

A value can have more than one row, one for each cause.

| Value | Meaning |
|---|---|
| `ok` | the sampler found the geometry and decoded frames |
| `no-sync` | the sampler saw no periodic hsync and vsync |
| `bad-timing` | the sync has no period |
| `bad-timing` | the vsync period is under two line periods |
| `bad-timing` | the frame is under 16 or over 4096 pixels wide |
| `bad-timing` | the frame is under 16 or over 4096 lines high |
| `bad-timing` | centre-v1 only: the window the timing gives, with no override, does not fit in the line or frame |
| `bad-window` | centre-v1 only: the timing's own window fits, but `window_offset` moves it outside the line or frame |

## Digests

The store digest, the `distinct` lines and the `xxh3-128-rgb6` hash file all use one hash:

Item is a property of the hash, and Value its setting. The library is [xxHash](https://github.com/Cyan4973/xxHash), and [`vgacap.hashes`](../python/vgacap/hashes.py) is the Python reference.

| Item | Value |
|---|---|
| Hash | XXH3-128 |
| Seed | `0` |
| Secret | default |
| Byte order | big-endian |
| Digest bytes 0 to 7 | the high 64 bits |
| Digest bytes 8 to 15 | the low 64 bits |
| Digits | 32 lowercase hex |
| Digest of the empty input | `99aa06d3014798d86001c324468d497f` |
| Command line | `xxhsum -H2` |
| Python | `xxhash.xxh3_128_hexdigest` |

The digests are for identity and integrity, not security. They tell distinct frames apart and check that a store reads back whole. They do not resist a deliberate collision, so nothing may trust them against an attacker.

Files written before [vgacap #62](https://github.com/mithro/vgacap/pull/62) have the key `store_sha256`, 64-digit SHA-256 `distinct` lines and the method `sha256-rgb6`. [`vgacap.framesfile`](../python/vgacap/framesfile.py) refuses them, and its message says to re-derive the files with `vgacap-sample`.

## frames.txt

### Header keys, in order

```text
id, status, sampler, sampler_status, pixel_format, width, height, mode,
clock_hz, clocks_per_pixel, capture_after_clock, frame_clocks, frames,
distinct_frames, store, store_length, store_xxh3_128
```

The keys not described above:

| Key | Value |
|---|---|
| `pixel_format` | always `rgb6` |
| `frame_clocks` | the median clocks between consecutive non-gap frame starts (element size / 2 after sorting); 0 with under two |
| `frames` | the frame records, gaps included, numbered 0 to `frames` - 1 |
| `distinct_frames` | the distinct frames in the store |
| `store` | the store's file name, in the same directory; no whitespace, `/` or `\` |
| `store_length` | the store's uncompressed length: `distinct_frames` × `width` × `height` |
| `store_xxh3_128` | the XXH3-128 of the uncompressed store, 32 lowercase hex digits |

- The store is the distinct frames' rgb6 bytes, distinct frame n at offset n × `width` × `height`, all zstd-compressed.
- Frame numbers count the end of each vsync pulse from `capture_after_clock`; frame starts never go backwards.

### Frame start clock

| Sampler | A frame starts at |
|---|---|
| `tb-v1` | the clock in whose clock-high sample the vsync pulse ends; frame k is the k-th at or after `capture_after_clock` |
| `centre-v1` | the hsync leading edge that begins the first line of the vsync pulse |

### missing

`missing` is a frame's pixel count that no seen clock supplied, stored as 0 (black). It is always 0 for `tb-v1`. Frames that differ only in `missing` are not one run.

### centre-v1 keys

For `centre-v1` only, after `store_xxh3_128`, zeros when `sampler_status` is not `ok`:

| Key | Value |
|---|---|
| `window_offset` | `<dx> <dy>`: the window's move in pixels and lines; `0 0` is the timing's own window |
| `porches` | `<hback'> <vback'>`: the back porch used, in pixels and lines after the sync pulse, with the move |
| `outside` | the clock positions lit in some frame but outside the window; non-zero means the window cuts off picture |
| `suggested_offset` | `<dx> <dy>`: the move that fits and leaves the fewest lit positions outside; ties to the least \|dx\| + \|dy\|, then dy, then dx |
| `colour_changes` | how often, inside the window, a clock's colour differs from the clock before |
| `colour_changes_off_grid` | how many of those are not on a pixel boundary; `-` when `clocks_per_pixel` is not whole |

### Distinct frames and runs

```text
distinct:
<32 lowercase hex digits>     one line a distinct frame, in store order
runs:
<run line>...
```

| Line | Form | Meaning |
|---|---|---|
| distinct | 32 hex digits | the XXH3-128 of distinct frame n's rgb6 bytes, the n-th line from 0 |
| frame run | `<first> <count> <distinct> <start clock> <missing>` | frames `first` to `first + count - 1` show distinct frame `distinct`, and frame `first + i` starts at `start clock + i × frame_clocks` |
| gap run | `<first> <count> - - -` | frames with no picture; they read back with start clock and `missing` 0 |

- A frame run goes on while the distinct frame and `missing` stay the same and each start is `frame_clocks` after the last.
- A gap run goes on over consecutive gaps.
- The runs are this greedy grouping and cover frames 0 to `frames` - 1 in order.

### The frame at each clock

| Clock | Frame |
|---|---|
| before frame 0's start | none |
| from frame k's start | k, until the next frame starts |
| from frame k's start + `frame_clocks`, when gaps follow it | none, until the next frame with a picture starts |
| after the last frame's start | the last frame, to the end of the trace |

`vgacap-export --frames` writes none as -1.

## `frame_hashes.<method>.txt`

One file a hash method:

| Method | Hash of a frame | Digits |
|---|---|---|
| `xxh3-128-rgb6` | XXH3-128 of the frame as one rgb6 byte a pixel, row-major | 32 |
| `fnv1a64-tb2026-rgb888` | FNV-1a 64, prime 0x100000001b3, offset basis 0x14650fb0739d0383, over RGB888 with each 2-bit channel × 85 | 16 |

| Line | Holds |
|---|---|
| first | `hash_method: <method>` |
| `# ...` | the method's definition in words, the same text in every file of a method; the reader checks it |
| `hash_offset_basis: 0x14650fb0739d0383` | `fnv1a64-tb2026-rgb888` only |
| header | `id`, `status`, `sampler`, `sampler_status`, `mode`, `width`, `height`, `clock_hz`, `clocks_per_pixel`, `capture_after_clock`, `frames`, valued as in `frames.txt` |
| after `frames: N` | N lines, frame 0 first: the hash in lowercase hex, or `-` for a gap |

### The hash files published for earlier shuttles

| Item | Earlier `frame_hashes.txt` | These files |
|---|---|---|
| first line | `# ` | `hash_method: <method>` |
| header | `# ` comment lines, then `id`, `hash_offset_basis`, `mode`, `width` and on, in another order | the keys above |
| keys not there | `hash_method`, `status`, `sampler`, `sampler_status`, `frames`, `capture_after_clock` | none |
| hash lines | lowercase hex, 16 digits, one a frame, frame 0 first | the same, for the FNV method |
