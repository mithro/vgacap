<!-- SPDX-License-Identifier: Apache-2.0 -->
# frames.txt and frame_hashes.<method>.txt

The text outputs of a sampler run. Writers: `src/sample/outputs.c`
(`vgaout_frames_txt`, `vgaout_hash_file`, declared in `include/vgacap/outputs.h`)
and `python/vgacap/framesfile.py`, which also reads both files and refuses
anything that is not exactly this format. The two writers produce
byte-identical files.

Both files are UTF-8 text, LF line endings, every line (the last included)
ending in a newline. A header line is `key: value`: the key, a colon, one
space, the rest of the line. Keys come in exactly the order below. Integers
are plain decimal with no leading zeros.

**Doubles** (`clocks_per_pixel`) are written with `vgacap_fmt_double` (C) and
`vgacap.timebase.fmt_double` (Python): the first `%.{p}g`, p from 1 to 17,
that reads back to the same double and has no exponent, otherwise the first
that reads back. So `1`, not `1.0`, and `0.9523809523809523`.

## Values shared by both files

| key | value |
|---|---|
| `id` | the trace's META(open) `project` |
| `status` | the run's status from the trace: META(close) `status`, or `truncated` |
| `sampler` | `tb-v1` or `centre-v1` |
| `sampler_status` | the sampler's own result: `ok`, `no-sync`, `bad-timing`, or (centre-v1 only) `bad-window` |
| `mode` | mode table name, or `unknown` |
| `width`, `height` | the frame size in pixels |
| `clock_hz` | the VGCH header's integer u32 |
| `clocks_per_pixel` | the sampler's cpp, a double |
| `capture_after_clock` | the calibration's value, clocks numbered from 0 |

`status` and `sampler_status` are separate on purpose: `status` is how the
trace ended, `sampler_status` is whether the sampler found a picture. When
`sampler_status` is not `ok` there are no frames (`frames: 0`).

## frames.txt

Header, in order:

```
id, status, sampler, sampler_status, pixel_format: rgb6, width, height, mode,
clock_hz, clocks_per_pixel, capture_after_clock, frame_clocks, frames,
distinct_frames, store, store_length, store_sha256
```

- `frames` is the number of frame records, gaps included; frame numbers run
  0 .. frames - 1 with no holes.
- `distinct_frames` is the number of distinct frames in the store.
- `store`, `store_length`, `store_sha256`: the frame store's file name, its
  length in bytes and its lowercase hex SHA-256.
- `frame_clocks` is the median (element `size / 2` after sorting) of the
  distances between consecutive non-gap frame starts (a gap between two
  frames is skipped, not counted as a frame); 0 with fewer than two non-gap
  frames.

For `centre-v1` only (and always for it, even when `sampler_status` is not
`ok`, with zeros) these keys follow `store_sha256`:

```
window_offset: <dx> <dy>
porches: <hback'> <vback'>
outside: <n>
suggested_offset: <dx> <dy>
colour_changes: <n>
colour_changes_off_grid: <n|->
```

The values come from `centrev1_diag_t` and the geometry (`include/vgacap/sample.h`):
`window_offset` is the override used, `porches` the geometry's `hback` and
`vback`, `outside` the count of distinct lit positions outside the window,
`suggested_offset` the diagnostics' `suggest_dx suggest_dy`, and
`colour_changes_off_grid` is `-` when cpp is not whole.

Then:

```
distinct:
<64 lowercase hex digits>     one SHA-256 (sha256-rgb6) per distinct frame, store order
runs:
<run line>...
```

Run lines:

- a frame run: `<first frame> <count> <distinct> <start clock> <missing>`; the
  frames `first .. first + count - 1` all show distinct frame `distinct`, have
  `missing` missing pixels (centre-v1; 0 for tb-v1), and frame `first + i`
  starts at clock `start clock + i * frame_clocks`;
- a gap run: `<first frame> <count> - - -`: frames that have no picture.

A run continues while the distinct number and `missing` stay the same and each
start is exactly `frame_clocks` after the previous one; a gap run continues
over consecutive gaps. The runs are exactly this greedy grouping and cover
frames 0 .. frames - 1 in order. A gap record carries start clock 0 and
missing 0 (the files do not store them).

## frame_hashes.<method>.txt

One file per hash method, `<method>` being `sha256-rgb6` or
`fnv1a64-tb2026-rgb888`.

```
hash_method: <method>
# <definition line>             one "# " line per line of the method's definition
hash_offset_basis: 0x14650fb0739d0383     fnv1a64-tb2026-rgb888 only
id, status, sampler, sampler_status, mode, width, height, clock_hz,
clocks_per_pixel, capture_after_clock, frames
<frame 0's hash>
...
```

The definition lines are `vgahash_definition(method)` (C) /
`vgacap.hashes.DEFINITIONS[method]` (Python), verbatim. After `frames: N`
there are exactly N lines, frame 0 first: the frame's hash in lowercase hex,
16 digits for the FNV method, 64 for SHA-256, or `-` for a gap. (The FNV hash
lines are byte-identical in form to the published tt07 `frame_hashes.txt`.)
Frame `k` is line `k` after the `frames:` line.
