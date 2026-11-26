# vgacap-dump, vgacap-frames, vgacap-sample and vgacap-export

The C command-line tools for inspecting a
[vgacap stream](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-09-15-stream-format.md)
and turning it into pictures, the tools that read a
[simulation trace](../../docs/trace-format.md), and the Python
[`vgacap-bin2stream`](../../python/vgacap/bin2stream.py) that makes a stream from a
simulator dump. [CMake builds the C tools into `build/`](../../docs/development.md).

## Usage

```sh
build/vgacap-dump capture.vgacap              # chunk list, total samples, checksum
mkdir -p out && build/vgacap-frames capture.vgacap out/frame   # out/frame-0000.ppm ...
uv run vgacap-bin2stream dump.bin out.vgacap  # wrap a one-byte-per-clock simulator dump
build/vgacap-dump run.trace.zst              # a trace's blocks and chunks
build/vgacap-sample run.trace.zst --out out --name run   # a simulation trace to frames
build/vgacap-export run.trace.zst --format fst --out run.fst   # a trace to VCD or FST
```

## When frames appear

A frame is emitted at the frame boundary that closes it, once the timing is
known. A capture starts mid-frame: the tail of that frame is unusable, the
next boundary only tells the reconstruction where frames begin, and the
whole frame after it is the first that can be rendered. So capture at
least three frame periods for one picture.

## Glitches

Spurious sync pulses are tolerated: a capture whose hsync carries the odd
glitch of 2 to 30 clocks ([real silicon does](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-tt08-tiny-logo-glitch-tolerance)) still reconstructs.
`vgacap-frames` reports `glitches=N` per frame, counting the pulses rejected
since the previous frame, and `glitches_total=N` for the whole stream.

## Overruns

An overrun (a `TIME` chunk with the message `overrun`, or a rise in its
dropped count) discards the picture in progress, so no frame is built
across the gap; `vgacap-frames` reports each one on stderr.

## Waveforms: `vgacap-export`

```sh
# A simulation trace, or a clock range of it, as VCD or FST:
build/vgacap-export run.trace.zst --format fst --out run.fst
build/vgacap-export run.trace.zst --format vcd --out win.vcd --from 1310720 --to 1310740
# ... with the frame number of each clock from a sampler's frames.txt:
build/vgacap-export run.trace.zst --format fst --out run.fst --frames out/centre-v1/frames.txt
gtkwave run.gtkw          # or: surfer --command-file run.sucl
```

A `layout=tt-um-43` trace gives the module's pins (`clk`, `rst_n`, `ena`,
`ui_in`, `uio_in`, `uo_out`, `uio_out`, `uio_oe`) in a scope named after the
trace's project (`--scope` to choose), and a `decoded` scope under it with
`hsync`, `vsync`, `r`, `g`, `b`, `rgb6` and, with `--frames`, `frame`. A board
capture gives `sample[bits-1:0]` and the same decoded scope. `--from` and
`--to` are clocks (`--to` exclusive); a range is read from its own block of
the trace. Times are in picoseconds, the sample's exact time. `--no-clk`
leaves out `clk`, which otherwise changes every half clock and dominates the
file. The VCD carries no date, so the same export gives the same bytes.

Beside the output the tool writes `<name>.gtkw`, a GTKWave save file that
opens the output with the signals in two groups, and `<name>.sucl`, a Surfer
command file: `load_file` the output, then `scope_add_as_group` for each
scope. Surfer documents command files (`.sucl`, run with `--command-file`
or `run_command_file`) and these commands in its
[command reference at v0.7.0](https://gitlab.com/surfer-project/surfer/-/blob/v0.7.0/docs/commands/README.md)
(also the example
[`examples/counter.sucl`](https://gitlab.com/surfer-project/surfer/-/blob/v0.7.0/examples/counter.sucl)).
That is the latest release; the Surfer version inside the pinned
oss-cad-suite could not be checked from this host. The file uses only
whole-scope commands, so it does not depend on how Surfer spells a bus's
name. The trace format itself is [docs/trace-format.md](../../docs/trace-format.md).
