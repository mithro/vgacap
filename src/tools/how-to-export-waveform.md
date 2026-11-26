---
type: how-to
owner: vgacap maintainers
reader: someone who wants to look at a capture or a simulation trace in a waveform viewer
review: 2027-05-27
---

# How to export a waveform as VCD or FST

**You have a capture or a simulation trace and want to see its signals, clock by clock, in GTKWave or Surfer.**

This page runs `vgacap-export`, which writes a VCD or an FST and a file for each viewer that opens it with the signals grouped. Every option is on [the tool reference](reference-options.md#vgacap-export). The trace itself is described on [the simulation trace format](../../docs/trace-format.md).

## What you need

- A vgacap checkout built with CMake with its host extras, the default, so that `build/vgacap-export` exists.
- A capture or trace whose header gives the clock: `vgacap-bin2stream` writes one only with `--clock-hz`.
- [GTKWave](https://gtkwave.github.io/gtkwave/) or [Surfer](https://gitlab.com/surfer-project/surfer).
- For step 3 only: a [simulation trace](../../docs/trace-format.md), and the `frames.txt` that [`vgacap-sample`](reference-options.md#vgacap-sample) wrote for it into `out/centre-v1/`.

## Steps

1. In the vgacap checkout, run `build/vgacap-export capture.vgacap --format fst --out capture.fst`, which writes `capture.fst`, `capture.gtkw` and `capture.sucl`.
2. In the same directory, for part of a long capture, run step 1's command with `--from 800 --to 1600` added, which exports clocks 800 to 1599 only.
3. In the same directory, for a trace, run step 1's command on the trace with `--frames out/centre-v1/frames.txt` added, which adds a `frame` signal.
4. In the same directory, run `gtkwave capture.gtkw` or `surfer --command-file capture.sucl`, and the viewer opens with the signals in two groups.

## Check

The command prints nothing and exits 0. The viewer shows a `ports` group, with `sample[7:0]` for a board capture or the design's pins for a trace. Below it is a `decoded` group with `hsync`, `vsync`, `r`, `g`, `b` and `rgb6`.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `the header has no clock_hz, so samples have no time` | the capture was written without a clock | write it again with the clock: `vgacap-bin2stream --clock-hz` |
| 2 | `--to N is past the trace's end` | the range runs past the last clock | give a smaller `--to` |
| 2 | `the range is empty` | `--from` is at or after `--to` | give a `--from` below `--to` |
| 1 | no `.sucl` file, and a note on stderr | the output path holds whitespace | give an output path without spaces |
| 4 | the viewer opens the wrong file | an export to the other format, of the same name, overwrote the side files | give the two exports different names |

## Next

- [Options of the vgacap command-line tools](reference-options.md)
- [The simulation trace format](../../docs/trace-format.md)
- [How to turn a capture into frames](how-to-capture-to-frames.md)
