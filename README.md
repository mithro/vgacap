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
       alt="vgacap overview. A Tiny Tapeout demo board box has its parts in a left column and a render of the board on the right, with dotted callouts from the render to the breakout and to the RP2040 or RP2350. Under the label breakout are two chips with an or between them: ASIC or FPGA, holding your design, which sends uo_out[7:0] down; and iCE40UP5K FPGA, holding design then capture, which sends uo_out + uio down. Both go to the RP2040 or RP2350: PIO, then DMA, then USB, to ttcap on a laptop. A wire tapping the uo_out[7:0] arrow goes left to a Raspberry Pi 5, whose RP1 runs PIO, then DMA, into DRAM. On the right, the design's RTL goes into a simulator (Verilator or Icarus: design, then tracer, then trace file). DRAM, ttcap and the trace file all feed one vgacap stream, drawn as a strip of colored pixel samples followed by black sync samples. The stream goes to libvgaframe, which rebuilds every frame, and on to pictures and video, and to pixel-exact checks against references. A legend shows signals in blue, captured data in orange and pictures in green.">
</p>

*Figure 1: from a design's `uo_out` to pictures and checks
([`docs/diagrams/overview.py`](docs/diagrams/overview.py)).*

The design, research notes, measurements, work log and task list live in
[mithro/tt-vga-capture](https://github.com/mithro/tt-vga-capture). The
calibration designs and their reference renders live in
[mithro/tt-vga-testpatterns](https://github.com/mithro/tt-vga-testpatterns).

> **Caution: AI in use.** This project is being built with Claude Code.
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

**Capture sources.** Each source is a different way of getting `uo_out` into
a vgacap stream; pick one by the hardware you have and how fast the design
must run.

| source | runs on | code | `ttcap` option |
|---|---|---|---|
| stock firmware | demo board microcontroller | [`python/ttcap/mp/`](python/ttcap/mp/) | `--firmware micropython` |
| vgacap firmware | demo board RP2350 | [`firmware/`](firmware/README.md) | `--firmware picosdk` |
| MicroPython C module | demo board RP2350 | [`mpy/`](mpy/README.md) | `--firmware mpy-module` |
| Raspberry Pi 5 | RP1 | [`capture-rp1/`](capture-rp1/README.md) | -- |
| FPGA | iCE40UP5K breakout | [`rtl/`](rtl/README.md) | -- |
| simulator | host | [`python/vgacap/bin2stream.py`](python/vgacap/bin2stream.py) | -- |
| BIO | [Baochip](https://github.com/baochip/baochip-1x) | [research note](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-11-24-bio-capture-feasibility.md) | -- |

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
| simulator | -- | -- | tests only | -- | -- |
| BIO | -- | -- | simulator only | -- | -- |

Run-length capture on the stock firmware
([vgacap PR #6](https://github.com/mithro/vgacap/pull/6), not yet merged) was
exact up to 25 MHz with an FPGA breakout and at 100 kHz with an ASIC
breakout.

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

```sh
cmake -S . -B build && cmake --build build
ctest --test-dir build --output-on-failure
uv run pytest -q
```

More: [docs/development.md](docs/development.md).

## A first capture

With a Tiny Tapeout demo board on `/dev/ttyACM0`:

```sh
uv run ttcap capture serial:/dev/ttyACM0 \
    --project tt_um_rejunity_vga --clock-hz 60000 --seconds 1 --out capture.vgacap
mkdir -p out && build/vgacap-frames capture.vgacap out/frame      # out/frame-0000.ppm ...
uv run ttcap demo --link serial:/dev/ttyACM0 --clock-hz 60000 --outdir out   # PNGs + MKV
```

More: [python/ttcap/README.md](python/ttcap/README.md) (capturing, the
demo, the shared Welland bench boards, running on a Raspberry Pi),
[gst/README.md](gst/README.md) (GStreamer),
[src/tools/README.md](src/tools/README.md) (the C tools) and
[tools/README.md](tools/README.md) (the hardware-in-the-loop check).

## Layout

| path | contents |
|---|---|
| [`src/tools/`](src/tools/README.md) | `vgacap-dump` and `vgacap-frames` |
| [`python/vgacap/`](python/vgacap/) | Python stream library |
| [`python/ttcap/`](python/ttcap/README.md) | `ttcap` CLI and library |
| [`python/ttcap/mp/`](python/ttcap/mp/) | MicroPython board programs |
| [`python/ttcap/emu/`](python/ttcap/emu/) | RP2040 and RP2350 emulator |
| [`firmware/`](firmware/README.md) | pico-sdk capture firmware |
| [`mpy/`](mpy/README.md) | MicroPython C module |
| [`capture-rp1/`](capture-rp1/README.md) | Raspberry Pi 5 capture |
| [`rtl/`](rtl/README.md) | FPGA capture modules |
| [`gst/`](gst/README.md) | GStreamer plugin |
| [`tools/`](tools/README.md) | hardware-in-the-loop scripts |
| [`tests/`](tests/) | C unit tests (ctest) |
| [`python/tests/`](python/tests/) | Python tests (pytest) |
| [`gst/tests/`](gst/tests/) | plugin end-to-end tests (pytest) |

## License

Apache-2.0, see [LICENSE](LICENSE).
