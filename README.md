# vgacap

vgacap captures the VGA output of a [Tiny Tapeout](https://tinytapeout.com)
design, turns it into pictures and video, and checks it pixel by pixel. A
design that drives the [Tiny VGA Pmod](https://github.com/mole99/tiny-vga)
puts its VGA signals on its eight `uo_out` pins; vgacap records those pins
and rebuilds the screen from them.

Every capture source writes the same format: a [**vgacap stream**](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-09-15-stream-format.md), a small
chunked format that records the design's `uo_out` once per project clock,
either continuously or as whole frames or windows. Everything downstream of
the source reads only that stream, so a capture from a Tiny Tapeout demo
board, an FPGA, a Raspberry Pi 5 or a simulator goes through the same
reconstruction, video and comparison tools.

<p align="center">
  <img src="docs/diagrams/overview.svg" width="100%"
       alt="vgacap overview. A Tiny Tapeout demo board box has its parts in a left column and a render of the board on the right, with dotted callouts from the render to the breakout and to the RP2040 or RP2350. Under the label breakout are two chips with an or between them: ASIC or FPGA, holding your design, which sends uo_out[7:0] down; and iCE40UP5K FPGA, holding design then capture, which sends uo_out + uio down. The two lines join into one arrow into the RP2040 or RP2350: PIO, then DMA, then USB, to ttcap on a laptop. A wire tapping the uo_out[7:0] arrow goes left to a Raspberry Pi 5, whose RP1 runs PIO, then DMA, into DRAM. On the right, the design's RTL goes into a simulator (Verilator or Icarus: design, then tracer, then trace file). DRAM, ttcap and the trace file all feed one vgacap stream, drawn as a strip of colored pixel samples followed by black sync samples. The stream goes to libvgaframe, which rebuilds every frame, and on to pictures and video, and to pixel-exact checks against references. A legend shows signals in blue, captured data in orange and pictures in green.">
</p>

*Figure 1: from a design's `uo_out` to pictures and checks
([`docs/diagrams/overview.py`](docs/diagrams/overview.py)).*

The design, research notes, measurements, work log and task list live in
[mithro/tt-vga-capture](https://github.com/mithro/tt-vga-capture). The
calibration designs and their reference renders live in
[mithro/tt-vga-testpatterns](https://github.com/mithro/tt-vga-testpatterns).

> **Caution: AI in use.** This project is being built with [Claude Code](https://claude.com/claude-code).
> Check the code and the measurements before relying on them.

## What is in it

**The common core**, used by every capture source and consumer:

| part | C header | C source | Python |
|---|---|---|---|
| stream format | [`include/vgacap/stream.h`](include/vgacap/stream.h) | [`src/stream/`](src/stream/) | [`python/vgacap/stream.py`](python/vgacap/stream.py) |
| reconstruction (`libvgaframe`) | [`include/vgacap/frame.h`](include/vgacap/frame.h) | [`src/frame/`](src/frame/) | -- |
| synthetic VGA signals | -- | -- | [`python/vgacap/synth.py`](python/vgacap/synth.py) |
| simulator dump to stream (`vgacap-bin2stream`) | -- | -- | [`python/vgacap/bin2stream.py`](python/vgacap/bin2stream.py) |

- The stream has a `VGCH` header and `RAW`, `RLE`, `FRAM`, `EVNT` and
  `TIME` chunks. The writer is callback-based C99 with no allocation, so it
  also builds into board firmware; the reader is incremental.
- The reconstruction learns the sync timing and polarity from the signal,
  tolerates glitch pulses, matches a mode table, and rebuilds frames from
  continuous streams and from the whole frames or windows in `FRAM` chunks.
- `vgacap-bin2stream` wraps a one-byte-per-clock simulator dump.

**Capture sources.** Pick one by the hardware you have and how fast the
design must run.

| source | runs on | code | `ttcap` option |
|---|---|---|---|
| stock firmware | demo board microcontroller | [`python/ttcap/mp/`](python/ttcap/mp/) | `--firmware micropython` |
| vgacap firmware | demo board RP2350 | [`firmware/`](firmware/README.md) | `--firmware picosdk` |
| MicroPython C module | demo board RP2350 | [`mpy/`](mpy/README.md) | `--firmware mpy-module` |
| Raspberry Pi 5 | Raspberry Pi 5 RP1 | [`capture-rp1/`](capture-rp1/README.md) | -- |
| FPGA | demo board with an FPGA breakout | [`rtl/`](rtl/README.md) | -- |
| simulator | host computer | [`python/vgacap/bin2stream.py`](python/vgacap/bin2stream.py) | -- |

**Verification:**

| source | chip | breakout | stream | frame | window |
|---|---|---|---|---|---|
| stock firmware | RP2350 | FPGA | pixel-exact | pixel-exact | pixel-exact |
| stock firmware | RP2350 | ASIC | clean | clean | not checked |
| stock firmware | RP2040 | ASIC | clean | emulator only | emulator only |
| vgacap firmware | RP2350 | FPGA | pixel-exact | pixel-exact | pixel-exact |
| vgacap firmware | RP2350 | ASIC | clean | clean | clean |
| MicroPython C module | RP2350 | FPGA | pixel-exact | pixel-exact | pixel-exact |
| FPGA | RP2350 | FPGA | pixel-exact | -- | -- |
| Raspberry Pi 5 | RP1 | -- | loopback only | -- | -- |
| simulator | -- | -- | clean | -- | -- |
| [BIO](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-11-24-bio-capture-feasibility.md) | -- | -- | simulator only | -- | -- |

Run-length capture on the stock firmware
([vgacap PR #6](https://github.com/mithro/vgacap/pull/6), not yet merged) was
exact up to
[25 MHz](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-11-24-pio-run-counting.md)
with an FPGA breakout and
[matched RAW capture at 100 kHz with an ASIC breakout](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-pio-rle-tt09).

**Consumers and tools:**

- [`vgacap-dump` and `vgacap-frames`](src/tools/README.md), the C tools;
- the [`ttcap`](python/ttcap/README.md) CLI: `capture`, `png`, `demo`,
  `probe`, `throughput`, `reboot`;
- the [GStreamer](https://gstreamer.freedesktop.org) plugin in
  [`gst/`](gst/README.md): `vgadecode`, `vgacapttsrc`, `vgacapbin`;
- [`ttcap.emu`](python/ttcap/emu/), a cycle-level RP2040/RP2350 emulator
  that runs the exact board programs for the tests;
- [`tools/hil.py`](tools/README.md#hardware-in-the-loop-check), the
  hardware-in-the-loop check.

## Building and testing

See [docs/development.md](docs/development.md).

## A first capture

With fpga-1 on the Welland bench
([Reaching a Welland board](python/ttcap/README.md#reaching-a-welland-board)):

```sh
ssh -N -L 18034:10.21.2.34:8765 tweed.welland.mithis.com
uv run ttcap capture ws://127.0.0.1:18034/serial --design tt_um_vga_pattern \
    --clock-hz 500000 --frames 2 --out capture.vgacap
mkdir -p out && build/vgacap-frames capture.vgacap out/frame      # out/frame-0000.ppm ...
uv run ttcap demo --link ws://127.0.0.1:18034/serial --design tt_um_vga_pattern \
    --clock-hz 500000 --fps 5 --outdir out                         # PNGs + MKV
```

## Layout

| path | contents |
|---|---|
| [`python/vgacap/`](python/vgacap/) | Python stream library |
| [`tests/`](tests/) | C unit tests (ctest) |
| [`python/tests/`](python/tests/) | Python tests (pytest) |
| [`gst/tests/`](gst/tests/) | plugin end-to-end tests (pytest) |

## License

Apache-2.0, see [LICENSE](LICENSE).
