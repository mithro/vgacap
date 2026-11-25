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
| one-byte-per-clock simulator dump to stream (`vgacap-bin2stream`) | -- | -- | [`python/vgacap/bin2stream.py`](python/vgacap/bin2stream.py) |

- The stream has a `VGCH` header and `RAW`, `RLE`, `FRAM`, `EVNT` and
  `TIME` chunks. The writer is callback-based C99 with no allocation, so it
  also builds into board firmware; the reader is incremental.
- The reconstruction learns the sync timing and polarity from the signal,
  tolerates glitch pulses, matches a mode table, and rebuilds frames from
  continuous streams and from the whole frames or windows in `FRAM` chunks.

**Capture sources.** Pick one by the hardware you have and how fast the
design must run.

| source | runs on | code | `ttcap` option | fastest stream clock | fastest frame clock |
|---|---|---|---|---|---|
| stock firmware | RP2350 on the demo board | [`python/ttcap/mp/`](python/ttcap/mp/) | `--firmware micropython` | 750 kHz | 40 MHz |
| stock firmware | RP2040 on the demo board | [`python/ttcap/mp/`](python/ttcap/mp/) | `--firmware micropython` | 60 kHz | -- |
| vgacap firmware | RP2350 on the demo board | [`firmware/`](firmware/README.md) | `--firmware picosdk` | 750 kHz | 25.175 MHz |
| MicroPython C module | RP2350 on the demo board | [`mpy/`](mpy/README.md) | `--firmware mpy-module` | 500 kHz | 25 MHz |
| Raspberry Pi 5 | RP1 on a Raspberry Pi 5 | [`capture-rp1/`](capture-rp1/README.md) | -- | 500 kHz | -- |
| FPGA | iCE40UP5K on an FPGA breakout | [`rtl/`](rtl/README.md) | -- | 250 kHz | -- |
| simulator | host CPU | [`python/vgacap/bin2stream.py`](python/vgacap/bin2stream.py) | -- | -- | -- |

**Verification:**

| source | chip | breakout | stream | frame | window |
|---|---|---|---|---|---|
| stock firmware | RP2350 | FPGA | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-direct-serial-fpga4) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-frame-capture-fpga4) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-frame-capture-fpga4) |
| stock firmware | RP2350 | ASIC | [clean](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-pio-rle-tt09) | [clean](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-frame-capture-fpga4/README.md#real-silicon-tt09-at-its-design-clocks) | not checked |
| stock firmware | RP2040 | ASIC | [clean](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-ttcap-capture-serial-tt07) | emulator only | emulator only |
| vgacap firmware | RP2350 | FPGA | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) |
| vgacap firmware | RP2350 | ASIC | [clean](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-picosdk-firmware-fpga4/README.md#asic-board-tt09-silicon-with-asic_select) | [clean](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-picosdk-firmware-fpga4/README.md#asic-board-tt09-silicon-with-asic_select) | [clean](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-picosdk-firmware-fpga4/README.md#asic-board-tt09-silicon-with-asic_select) |
| MicroPython C module | RP2350 | FPGA | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) |
| FPGA | RP2350 | FPGA | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-rtl-framer-fpga4) | -- | -- |
| Raspberry Pi 5 | RP1 | -- | [loopback only](capture-rp1/README.md#proven-on-hardware-the-loopback-fixture) | -- | -- |
| simulator | -- | -- | [clean](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-sim-tt_um_vga_pattern) | -- | -- |

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

Building needs CMake and [uv](https://docs.astral.sh/uv/); see
[docs/development.md](docs/development.md).

## A first capture

These commands assume a demo board on `/dev/ttyACM0` running a 640x480 VGA
design, such as a [tt-vga-testpatterns](https://github.com/mithro/tt-vga-testpatterns)
bitstream on an FPGA breakout. 500 kHz suits an RP2350 demo board; an
RP2040 demo board keeps up with 60 kHz, where the demo needs `--seconds 21`
or more for three frames.

```sh
uv run ttcap capture serial:/dev/ttyACM0 --clock-hz 500000 --frames 2 --seconds 0 --out capture.vgacap
mkdir -p out && build/vgacap-frames capture.vgacap out/frame      # out/frame-0000.ppm ...
export GST_PLUGIN_PATH=$PWD/build
uv run ttcap demo --link serial:/dev/ttyACM0 --clock-hz 500000 --fps 5 --outdir video   # PNGs + MKV
```

For a board on the Welland bench, see
[Reaching a Welland board](python/ttcap/README.md#reaching-a-welland-board-bench-access-needed).

## Layout

| path | contents |
|---|---|
| [`python/vgacap/`](python/vgacap/) | Python stream library |
| [`tests/`](tests/) | C unit tests (ctest) |
| [`python/tests/`](python/tests/) | Python tests (pytest) |
| [`gst/tests/`](gst/tests/) | plugin end-to-end tests (pytest) |

## License

Apache-2.0, see [LICENSE](LICENSE).
