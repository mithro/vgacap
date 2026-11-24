<!-- SPDX-License-Identifier: Apache-2.0 -->
# frames.txt and frame_hashes.<method>.txt

The text outputs of a sampler run over a simulation trace: which pictures a
design drew, when each frame started, and a hash of every frame. The C writer
(`src/sample/outputs.c`) and the Python reference writer and reader
(`python/vgacap/framesfile.py`) produce and accept exactly this format; the
Python reader refuses anything else, and the two writers produce
byte-identical files.

Both files are UTF-8 text with LF line endings; every line, the last
included, ends in a newline. A header line is `key: value`: the key, a colon,
one space, and the rest of the line (never empty). Keys come in exactly the
order given below. Integers are plain decimal with no leading zeros. Text
values (`id`, `status`, `sampler`, `sampler_status`, `mode`, `store`) are one
non-empty line.

**Doubles** (`clocks_per_pixel`) are written as the first `%.{p}g`, p from 1
to 17, that reads back to the same double and has no exponent; if none
qualifies (very large or tiny values), the first that reads back. So `1`, not
`1.0`, and `0.9523809523809523`.

## Units

- **Clocks** are the design's clock cycles, numbered from 0; clock 0 is the
  first reset clock. All clock values below (`capture_after_clock`, start
  clocks, `frame_clocks`) are in this numbering. `clock_hz` is the design
  clock's frequency in hertz.
- **Pixels** and **lines** are positions in the frame as the sampler decoded
  it: `width` pixels by `height` lines. A pixel is stored as rgb6, one byte,
  `R<<4 | G<<2 | B`, each channel 2 bits, the channel's first Tiny VGA output
  bit the more significant.
- `clocks_per_pixel` is how many design clocks one pixel lasts.

## Values shared by both files

| key | value |
|---|---|
| `id` | the project's name from the trace (e.g. `tt07/tt_um_example`) |
| `status` | how the run itself ended, see below |
| `sampler` | `tb-v1` or `centre-v1`, the pixel-picking method |
| `sampler_status` | whether the sampler found a picture, see below |
| `mode` | the video mode: one of `640x480@60`, `640x480@72`, `640x480@75`, `720x400@70`, `800x600@56`, `800x600@60`, `800x600@72`, `800x600@75`, `1024x768@60`, `1024x768@70`, `1280x720@60`, `1280x1024@60` (the name is resolution@refresh Hz), or `unknown` when the measured timing matches none of them |
| `width`, `height` | the frame size in pixels and lines (0 when there are no frames) |
| `clock_hz` | the design clock in Hz, an integer |
| `clocks_per_pixel` | design clocks per pixel, a double |
| `capture_after_clock` | the clock before which the sampler ignores the design: the sync calibration finishes at it, and frames are counted from it |

### status

How the run (simulation) ended, as the trace records it. `truncated` is used
when the trace has no closing record, because the run was cut off.

| value | meaning |
|---|---|
| `ok` | the simulation finished normally |
| `sim-timeout` | the simulation hit its wall-clock limit before it produced a usable clip |
| `unstable-sync` | the line or frame period varied by more than 1%, so there is no stable raster |
| `no-sync` | no periodic hsync/vsync was seen during calibration |
| `bad-timing` | sync was seen but is not a plausible raster |
| `truncated` | the trace ended without a closing record |

### sampler_status

The sampler's own result, kept apart from `status` (a run can have status
`ok` and still have `sampler_status` `bad-timing`).

| value | meaning |
|---|---|
| `ok` | the sampler found the geometry and decoded frames |
| `no-sync` | the sampler saw no periodic hsync/vsync, so there is no picture |
| `bad-timing` | the sync timing is not a usable picture: no period, a vsync period under twice the line period, a frame size outside 16 to 4096 pixels or lines, or (centre-v1) a window that does not fit inside the line and frame |
| `bad-window` | centre-v1 only: the timing is fine but the requested window offset (`window_offset`) puts the window outside what the line or frame holds, so it cannot be used |

With any value but `ok` there are no frames: `frames: 0`, no distinct frames,
no runs.

## frames.txt

Header, in order:

```
id, status, sampler, sampler_status, pixel_format: rgb6, width, height, mode,
clock_hz, clocks_per_pixel, capture_after_clock, frame_clocks, frames,
distinct_frames, store, store_length, store_sha256
```

- `pixel_format` is always `rgb6`.
- `frames` is the number of frame records, gaps included. Frames are numbered
  0 to `frames` - 1 with no holes; the number comes from counting the end of
  each vsync pulse from `capture_after_clock`.
- `distinct_frames` is the number of distinct frames in the store: frames
  with identical pixels (and the same `missing`) share one.
- `store` is a file in the same directory as `frames.txt` (a bare file name,
  no whitespace, `/` or `\`). It holds the distinct frames' rgb6 bytes
  concatenated, distinct frame n at byte offset n * width * height, and the
  whole file is zstd-compressed. `store_length` is the uncompressed length in
  bytes (`distinct_frames * width * height`) and `store_sha256` is the
  SHA-256 of the uncompressed bytes as exactly 64 lowercase hex digits.
- `frame_clocks` is the typical number of clocks from one frame start to the
  next: the median (element `size / 2` after sorting) of the distances
  between consecutive non-gap frame starts (a gap between two frames is
  skipped, not counted as a frame); 0 with fewer than two non-gap frames.
  Frame starts never go backwards.

### Frame start clock

The clock at which the frame starts, which differs by sampler:

- `tb-v1`: the clock in whose clk-high sample the vsync pulse is seen to end.
  Frame k starts at the k-th such clock at or after `capture_after_clock`.
- `centre-v1`: the clock of the hsync leading edge that begins the first line
  of the vsync pulse (row 0 of the raw frame).

### missing

The number of pixels of the frame that could not be filled in because the
clock that should supply them was not seen (not written by the trace, or past
the end of the line or of the frame as the timing was measured); those pixels
are stored as 0 (black). Always 0 for `tb-v1`, which fills what is not
written with black without counting it. Frames that differ only in `missing`
are not grouped into one run.

### centre-v1 keys

For `centre-v1` only, and always for it (zeros when `sampler_status` is not
`ok`), these follow `store_sha256`:

```
window_offset: <dx> <dy>
porches: <hback'> <vback'>
outside: <n>
suggested_offset: <dx> <dy>
colour_changes: <n>
colour_changes_off_grid: <n|->
```

- `window_offset`: the override applied to the window, in pixels (`dx`) and
  lines (`dy`); `0 0` is the window the timing gives.
- `porches`: the back porch actually used, `hback'` pixels before the first
  active pixel on a line and `vback'` lines before the first active line,
  counted from the end of the hsync and vsync pulses, after the override.
- `outside`: the number of distinct clock positions (clock within the line,
  line within the frame) that were lit (non-black) in some emitted frame but
  lie outside the window, over the whole run. A non-zero value means picture
  content that the window cuts off.
- `suggested_offset`: the `<dx> <dy>` override, within what fits in the line
  and frame, that would leave the fewest lit positions outside the window
  (ties go to the smallest |dx| + |dy|, then dy, then dx).
- `colour_changes`: how many times, within the window of the emitted frames,
  the colour at a clock differs from the colour at the clock before it.
- `colour_changes_off_grid`: how many of those changes are not at a pixel
  boundary of the pixel grid. `-` when `clocks_per_pixel` is not a whole
  number, where that has no meaning.

### Distinct frames and runs

```
distinct:
<64 lowercase hex digits>     SHA-256 of one distinct frame's rgb6 bytes, in store order
runs:
<run line>...
```

`distinct` indexes this list from 0, in store order (the n-th line is the
n-th frame in the store). Run lines:

- a frame run: `<first frame> <count> <distinct> <start clock> <missing>`:
  frames `first` to `first + count - 1` all show distinct frame `distinct`,
  have `missing` missing pixels, and frame `first + i` starts at clock
  `start clock + i * frame_clocks`;
- a gap run: `<first frame> <count> - - -`: frames that have no picture (a
  frame number the sampler counted but could not emit).

A run continues while the distinct number and `missing` stay the same and each
start is exactly `frame_clocks` after the previous one; a gap run continues
over consecutive gaps. The runs are exactly this greedy grouping and cover
frames 0 to `frames` - 1 in order. A gap carries no start clock or `missing`
(they read back as 0).

## frame_hashes.<method>.txt

One file per hash method, `<method>` being `sha256-rgb6` or
`fnv1a64-tb2026-rgb888`. The first line is always `hash_method: <method>`.

```
hash_method: <method>
# <definition line>             one "# " line per line of the method's definition
hash_offset_basis: 0x14650fb0739d0383     fnv1a64-tb2026-rgb888 only
id, status, sampler, sampler_status, mode, width, height, clock_hz,
clocks_per_pixel, capture_after_clock, frames
<frame 0's hash>
...
```

The definition lines state in words how the hash is computed over a frame
(for `sha256-rgb6`: SHA-256 of the frame as one rgb6 byte per pixel,
row-major; for `fnv1a64-tb2026-rgb888`: FNV-1a 64 with prime 0x100000001b3 and
the non-standard offset basis above, over the frame as RGB888 with each
2-bit channel stored as value * 85). They are the same text in every file of
a method and the reader checks it. After `frames: N` there are exactly N
lines, frame 0 first: the frame's hash in lowercase hex, 16 digits for the
FNV method and 64 for SHA-256, or `-` for a gap. Frame k is the k-th line
after the `frames:` line. The header values are the same as in `frames.txt`.

### Difference from the previously published hash files

The `frame_hashes.txt` files published for earlier shuttles start with `# `
comment lines (`# Per-frame hashes from the simulation ...`, the hash
definition, the stimulus) and then `id:`, `hash_offset_basis:`, `mode:`,
`width:`, ... in a different key order, with no `hash_method`, `status`,
`sampler`, `sampler_status`, `frames` or `capture_after_clock` keys. The new
files are told apart by their first line, `hash_method: <id>`; a published
file starts with `# `. The hash lines themselves have the same form in both:
lowercase hex, 16 digits for the FNV method, one frame per line, frame 0
first.
