---
type: reference
owner: vgacap maintainers
reader: someone looking up an option, an output or an exit code of a vgacap command-line tool
review: 2027-05-27
---

# Options of the vgacap command-line tools

**You want every argument, output and exit code of `vgacap-dump`, `vgacap-frames`, `vgacap-sample`, `vgacap-export` and `vgacap-bin2stream`.**

The four C tools are built into `build/` by CMake, as [Developing vgacap](../../docs/development.md) shows; `vgacap-bin2stream` is a Python command, run with `uv run`. In each argument table, Argument is what you type, Value what follows it, Default what holds without it, and What it sets the effect.

## vgacap-dump

```text
vgacap-dump FILE
```

The source is [`vgacap_dump.c`](vgacap_dump.c).

| Argument | Value | Default | What it sets |
|---|---|---|---|
| `FILE` | a path | required | a vgacap stream, or a simulation trace (zstd) |

| Output line | Meaning |
|---|---|
| `VGCH bits=... spw=... flags=... clock=... map=... desc="..."` | a version 1 header |
| `VGCH v2 bits=... spc=... clock_bit=... clock_first=... ...` | a version 2 header |
| `RAW  samples=N` and one line for each other chunk | a chunk and its counts |
| `resync skipped=N reason=...` | bytes the reader lost, and what first looked wrong |
| `trace blocks=N samples=N table_rebuilt=N closed=N` | a trace's summary, first, for a trace |
| `total_samples=N crc32=X` | version 1: the samples and an FNV-1a 32 over each as 4 bytes |
| `total_samples=N crc64=X` | version 2: the samples and an FNV-1a 64 over each as ceil(`sample_bits` / 8) bytes |

| Exit code | Meaning |
|---|---|
| 0 | the file was read |
| 1 | usage error, a file that cannot be opened, a stream error, or a trace in a build without `VGACAP_HOST_EXTRAS` |

## vgacap-frames

```text
vgacap-frames IN OUT_PREFIX [--max-frames N] [--partial]
```

The source is [`vgacap_frames.c`](vgacap_frames.c).

| Argument | Value | Default | What it sets |
|---|---|---|---|
| `IN` | a path | required | the vgacap stream to read |
| `OUT_PREFIX` | a path prefix | required | frames are written to `OUT_PREFIX-0000.ppm`, `-0001.ppm` and on; the directory must exist |
| `--max-frames` | N | 0, no limit | stop after N frames |
| `--partial` | -- | off | also write frames whose lines were not all covered |

| Output | Where | Meaning |
|---|---|---|
| `OUT_PREFIX-NNNN.ppm` | files | each frame, binary PPM (P6), 8 bits a channel |
| `frame N: WxH mode=M cpl=C lpf=L hsync=pos/neg vsync=pos/neg partial=0/1 glitches=G` | stdout | one line a frame written; `mode=?` when no mode matched |
| `frames=N glitches_total=G` | stdout | the summary, last |
| `vgacap-frames: overrun after sample S (dropped=D): picture reset` | stderr | an overrun reset the picture |
| `vgacap-frames: resync skipped=N reason=...` | stderr | the reader lost bytes |

| Exit code | Meaning |
|---|---|
| 0 | at least one frame was written |
| 1 | the input is a simulation trace: read it with `vgacap-sample` |
| 2 | usage error, an unreadable file, a write error, a stream error, or no frame |

## vgacap-sample

```text
vgacap-sample TRACE --out DIR --name PREFIX [--sampler tb-v1] [--sampler centre-v1]
              [--centre-window DX,DY] [--store-level N] [--png-fd FD]
```

The source is [`vgacap_sample.c`](vgacap_sample.c). It reads a simulation trace only.

| Argument | Value | Default | What it sets |
|---|---|---|---|
| `TRACE` | a path | required | the simulation trace |
| `--out` | DIR | required | the output directory; each sampler writes into `DIR/<sampler>/` |
| `--name` | PREFIX | required | the store file's prefix; no `/`, `\` or whitespace |
| `--sampler` | `tb-v1` or `centre-v1` | `centre-v1` only | a sampler to run; give it twice for both |
| `--centre-window` | DX,DY | `0,0` | move centre-v1's window by DX pixels and DY lines, each -100000 to 100000 |
| `--store-level` | N | 3 | the zstd level of the frame store |
| `--png-fd` | FD | none | also write each new distinct frame to this open file descriptor |

| Output in `DIR/<sampler>/` | Holds |
|---|---|
| `PREFIX_<sampler>_frames.rgb6.zst` | the distinct frames, one rgb6 byte a pixel, zstd |
| `frames.txt` | the frame list ([format](../../docs/frames-format.md)) |
| `frame_hashes.sha256-rgb6.txt` | each frame's SHA-256 |
| `frame_hashes.fnv1a64-tb2026-rgb888.txt` | tb-v1 only: each frame's FNV-1a 64 |

On `--png-fd`, each new distinct frame is the line `<sampler> <distinct> <width> <height>` then width × height rgb6 bytes. `VGACAP_SAMPLE_DEBUG=1` in the environment prints `trace_passes=1 blocks_decoded=N` to stderr.

| Exit code | Meaning |
|---|---|
| 0 | every file asked for was written, runs with no picture included |
| 1 | an I/O or trace error, or a `--centre-window` outside the line or frame (every file is still written) |
| 2 | usage error, or a board capture instead of a trace |

## vgacap-export

```text
vgacap-export TRACE --format fst|vcd --out FILE [--from CLOCK] [--to CLOCK]
              [--no-clk] [--frames FRAMES_TXT] [--scope NAME]
```

The source is [`vgacap_export.c`](vgacap_export.c). It reads a simulation trace or a board capture.

| Argument | Value | Default | What it sets |
|---|---|---|---|
| `TRACE` | a path | required | a simulation trace, or a vgacap stream of version 1 or 2 |
| `--format` | `fst` or `vcd` | required | the waveform format |
| `--out` | FILE | required | the waveform file |
| `--from` | CLOCK | 0 | the first clock, included |
| `--to` | CLOCK | the end | the clock to stop before |
| `--no-clk` | -- | off | leave out `clk`, which changes every half clock |
| `--frames` | FRAMES_TXT | none | add `frame`, the frame number at each clock, from a sampler's `frames.txt` |
| `--scope` | NAME | the trace's project, else `trace` | the top scope's name; no whitespace, `/`, `.` or `$` |

| Signal | Scope | When |
|---|---|---|
| `clk`, `rst_n`, `ena`, `ui_in[7:0]`, `uio_in[7:0]`, `uo_out[7:0]`, `uio_out[7:0]`, `uio_oe[7:0]` | the top scope | a trace with `layout=tt-um-43` |
| `sample[bits-1:0]` | the top scope | any other input |
| `hsync`, `vsync`, `r[1:0]`, `g[1:0]`, `b[1:0]`, `rgb6[5:0]` | `decoded` | each signal the header's map names |
| `frame[31:0]` | `decoded` | with `--frames`; -1 before frame 0 and in gaps |

| Output | Holds |
|---|---|
| FILE | the waveform; timescale 1 ps, each sample at its exact time; a VCD has no date |
| FILE with `.gtkw` | a GTKWave save file: the signals in groups `ports` and `decoded`, zoomed to fit |
| FILE with `.sucl` | a [Surfer](https://gitlab.com/surfer-project/surfer) command file with the same signals; not written when FILE's path has whitespace |

Both side files name FILE by its absolute path, and replace FILE's extension, so a VCD and an FST of one name share them. `VGACAP_EXPORT_DEBUG=1` prints `blocks_read=N times=T changes=C` to stderr.

| Exit code | Meaning |
|---|---|
| 0 | written |
| 1 | an I/O, trace or `frames.txt` error, or a header with no `clock_hz`; a partly written FILE is removed |
| 2 | usage error, or a range that is empty or past the end; nothing is written |

## vgacap-bin2stream

```text
uv run vgacap-bin2stream INPUT OUTPUT [--desc TEXT] [--clock-hz N] [--mode N]
```

The source is [`python/vgacap/bin2stream.py`](../../python/vgacap/bin2stream.py). It writes a version 1 stream: `sample_bits` 8, four samples a word, the Tiny VGA signal map, `RAW ` chunks of 65,536 samples. The table is built from its argparse parser.

```{vgacap-argparse} vgacap.bin2stream:parser
```
