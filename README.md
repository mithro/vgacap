# vgacap

vgacap captures the VGA output of a [Tiny Tapeout](https://tinytapeout.com)
design, turns it into pictures and video, and checks it pixel by pixel. A
design that drives the Tiny VGA Pmod puts its VGA signals on its eight
`uo_out` pins; vgacap records those pins and rebuilds the screen from them.

Every capture source writes the same format: a **vgacap stream**, a small
chunked format that records the design's `uo_out` once per project clock,
either continuously or as whole frames or windows. Everything downstream of
the source reads only that stream, so a capture from a Tiny Tapeout demo
board, an FPGA, a Raspberry Pi 5 or a simulator goes through the same
reconstruction, video and comparison tools.

<p align="center">
  <img src="docs/diagrams/overview.svg" width="100%"
       alt="vgacap overview, flowing top to bottom. At the top, a photo of a Tiny Tapeout demo board (an ASIC or an FPGA breakout on top, the design driving VGA on uo_out) and, separately, the design's RTL. One uo_out[7:0] bus from the board feeds three capture sources: the demo board itself (its RP2040 or RP2350 chip: PIO, then DMA, then USB), a Raspberry Pi 5 (its RP1 chip: PIO, then DMA, then DRAM) and an FPGA (the FPGA chip: the project, then capture logic, then its link). The design's RTL feeds a simulator (Verilator or Icarus: the design, then a tracer, then a trace file). ttcap, on a laptop at the side, controls the demo board. All four sources write one vgacap stream, made of VGCH, RAW, RLE, FRAM, EVNT and TIME chunks. libvgaframe reads the stream and rebuilds every frame, which feeds pictures and video, and pixel-exact checks. A legend shows signals in blue, stream bytes in orange, pictures in green and control as violet dashed lines.">
</p>

*Figure 1: the design's `uo_out` (or its RTL, in a simulator) flows down
through a capture source into one vgacap stream, which libvgaframe turns
into pictures, video and pixel-exact checks. Blue is the design's signals,
orange the stream's bytes, green pictures, and violet dashed control.
[`docs/diagrams/overview.py`](docs/diagrams/overview.py) draws the figure.*

The design, research notes, measurements, work log and task list live in
[mithro/tt-vga-capture](https://github.com/mithro/tt-vga-capture). The
calibration designs and their reference renders live in
[mithro/tt-vga-testpatterns](https://github.com/mithro/tt-vga-testpatterns).

> **Caution: AI in use.** This project is being built with Claude Code.
> Check the code and the measurements before relying on them.

## What is in it

**The common core**, used by every capture source and consumer:

| | |
|---|---|
| stream format | `include/vgacap/stream.h`, `src/stream/`: the `VGCH` header and the `RAW`, `RLE`, `FRAM`, `EVNT` and `TIME` chunks; a callback-based C99 writer (no allocation, so it also builds into board firmware) and an incremental reader. `python/vgacap/stream.py` is the Python mirror. |
| reconstruction | `include/vgacap/frame.h`, `src/frame/` (`libvgaframe`): learns the sync timing and polarity from the signal itself, tolerates glitch pulses, matches a mode table and rebuilds frames, both from continuous streams and from the whole frames or windows in `FRAM` chunks. |
| test data | `python/vgacap/synth.py` generates synthetic VGA signals; `vgacap-bin2stream` wraps a one-byte-per-clock simulator dump as a stream. |

**Capture sources.** Each source is a different way of getting `uo_out` into
a vgacap stream; pick one by the hardware you have and how fast the design
must run. In the status column, an *ASIC* board is a Tiny Tapeout demo
board carrying a real Tiny Tapeout chip, and an *FPGA* board is one
carrying an FPGA that stands in for the chip.

| source | where | how it reaches the host | status |
|---|---|---|---|
| Tiny Tapeout demo board, stock firmware | `python/ttcap/mp/`: capture programs that `ttcap` pushes through the board's MicroPython REPL and runs on the PIO of its RP2040 or RP2350 | USB serial, or the fpgas.online websocket bridge | streaming, whole-frame and windowed capture; pixel-exact on RP2350 boards (FPGA and ASIC); on RP2040 boards streaming is proven on silicon, frame and window capture only in the emulator |
| demo board, run-length | `ttcap capture --mode rle` ([vgacap PR #6](https://github.com/mithro/vgacap/pull/6)): the PIO counts runs itself | as above, with fewer bytes for simple pictures | exact up to 25 MHz on an FPGA board, and on an ASIC board |
| demo board (RP2350), vgacap firmware | `firmware/`: a pico-sdk firmware loaded into RAM in place of the stock one (`ttcap --firmware picosdk`) | USB serial, with its own protocol (`firmware/PROTOCOL.md`) | frame, window and stream capture pixel-exact on FPGA and ASIC boards |
| demo board (RP2350), MicroPython C module | `mpy/`: the same capture engines built into a custom MicroPython firmware (`import vgacap`, `ttcap --firmware mpy-module`) | as for the stock firmware | pixel-exact on an FPGA board |
| Raspberry Pi 5 | `capture-rp1/` (`vgacap-rp1`): the PIO in the Pi 5's own RP1 I/O chip, via piolib, with the board's `uo_out` wired to the Pi's GPIO header | runs on the Pi itself | proven by loopback; waiting for a Pi 5 wired to a Tiny Tapeout board |
| FPGA | `rtl/`: capture logic to build into an FPGA design (Migen source and generated Verilog) | whatever link the FPGA design has | exact on an iCE40 at 250 kHz and 25 MHz |
| simulators | `vgacap-bin2stream` today; full simulation traces are being added (tt-vga-capture Milestone 10) | files | in use by the tests |
| BIO (the I/O coprocessor on bunnie's Baochip) | research in tt-vga-capture | -- | simulator only |

**Consumers and tools:** the C tools `vgacap-dump` and `vgacap-frames`; the
`ttcap` CLI (`capture`, `png`, `demo`, `probe`, `throughput`, `reboot`);
the GStreamer plugin in `gst/` (`vgadecode`, `vgacapttsrc`, `vgacapbin`);
`ttcap.emu`, a cycle-level RP2040/RP2350 emulator that runs the exact board
programs for the tests; and `tools/hil.py`, the hardware-in-the-loop check.
Each directory's README, linked under [Layout](#layout), covers its part.

## Building and testing

```sh
cmake -S . -B build && cmake --build build
ctest --test-dir build --output-on-failure
uv run pytest -q
```

CMake builds the GStreamer plugin only when the GStreamer development files
are present. [docs/development.md](docs/development.md) covers the
dependencies and CI.

## A first capture

With a Tiny Tapeout demo board on `/dev/ttyACM0`:

```sh
uv run ttcap capture serial:/dev/ttyACM0 \
    --project tt_um_rejunity_vga --clock-hz 100000 --seconds 1 --out capture.vgacap
build/vgacap-frames capture.vgacap out/frame      # out/frame-0000.ppm ...
uv run ttcap demo --link serial:/dev/ttyACM0 --clock-hz 60000 --outdir out   # PNGs + MKV
```

More: [python/ttcap/README.md](python/ttcap/README.md) (capturing, the
demo, the shared Welland bench boards, running on a Raspberry Pi),
[gst/README.md](gst/README.md) (GStreamer),
[src/tools/README.md](src/tools/README.md) (the C tools) and
[tools/README.md](tools/README.md) (the hardware-in-the-loop check).

## Layout

| | |
|---|---|
| `include/vgacap/stream.h`, `src/stream/` | the vgacap stream format: writer, and an incremental reader that emits `(value, run)` pairs |
| `include/vgacap/frame.h`, `src/frame/` | `libvgaframe`: mode table, sync timing learner, frame reconstruction to RGB24, `FRAM` window reassembly |
| [`src/tools/`](src/tools/README.md) | `vgacap-dump` (chunk list, sample checksum) and `vgacap-frames` (stream to PPM frames) |
| `python/vgacap/` | Python mirror of the stream format, mode table, synthetic signal generators, `vgacap-bin2stream` |
| [`python/ttcap/`](python/ttcap/README.md) | the `ttcap` CLI and library: board links (serial, websocket), the capture session for the demo board's RP2040/RP2350, `ttcap demo`; `mp/` holds the MicroPython board programs, `emu/` the RP2040/RP2350 emulator |
| [`firmware/`](firmware/README.md) | pico-sdk capture firmware for the Tiny Tapeout demo board v3 (RP2350), with its portable core in `firmware/core/` and a simulated board in `firmware/sim/` |
| [`mpy/`](mpy/README.md) | the same capture engines as a MicroPython `USER_C_MODULE` |
| [`capture-rp1/`](capture-rp1/README.md) | `vgacap-rp1`: capture with the PIO in the Raspberry Pi 5's RP1 chip, via piolib |
| [`rtl/`](rtl/README.md) | FPGA capture modules (Migen source and generated Verilog) |
| [`gst/`](gst/README.md) | the GStreamer plugin |
| [`tools/`](tools/README.md) | `hil.py` (hardware-in-the-loop check), `board_exec.py`, `board_restart.py` |
| `tests/`, `python/tests/`, `gst/tests/` | C unit tests (ctest) and Python and end-to-end tests (pytest) |

`firmware/`, `mpy/` and `capture-rp1/` also compile the stream writer
(`src/stream/writer.c`) on its own, so it stays C99 with no allocation and
no dependencies beyond libc.

[tt-vga-capture/docs/research/2026-09-15-stream-format.md](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-09-15-stream-format.md)
describes the stream format in full.

## License

Apache-2.0, see [LICENSE](LICENSE).
