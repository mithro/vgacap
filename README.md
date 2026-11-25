# vgacap

Shared libraries and tools for capturing the VGA output of
[Tiny Tapeout](https://tinytapeout.com) designs (the Tiny VGA Pmod's eight
`uo_out` pins), turning it into pictures and video, and checking it.

Every capture source writes the same thing: a **vgacap stream**, a small
chunked format that records the design's `uo_out` once per project clock,
either continuously or as whole frames or windows. Everything after the
source reads only that stream, so a capture from a demo board, an FPGA, a
Raspberry Pi 5 or a simulator goes through the same reconstruction, video
and comparison tools.

<p align="center">
  <img src="docs/diagrams/overview.svg" width="100%"
       alt="vgacap overview. On the left, a Tiny Tapeout design (on an ASIC, an FPGA or in a simulator) drives its VGA signals, uo_out[7:0], into four capture sources: the Tiny Tapeout demo board, whose RP2040 or RP2350 samples uo_out with PIO and reaches the host over USB serial or a websocket at up to about 0.7 MB/s; a Raspberry Pi 5, whose RP1 chip samples uo_out on its GPIO header; FPGA capture logic built into the design; and simulators, which run the design's RTL and write trace files. ttcap on a host computer controls the demo board (clock, reset, start, stop). Every source writes the same vgacap stream, a VGCH header followed by RAW, RLE, FRAM, EVNT and TIME chunks. On the right, libvgaframe learns the sync and mode from the stream and rebuilds each frame, which feed pictures (PNG and PPM), video (GStreamer) and pixel-exact checks against reference renders.">
</p>

*Figure 1: every capture source writes one vgacap stream, and every
consumer reads only that stream. Blue lines are the design's signals,
orange the stream's bytes, green pictures, and violet dashed control.
The figure is drawn by [`docs/diagrams/overview.py`](docs/diagrams/overview.py).*

The design, research notes, measurements, work log and task list live in
[mithro/tt-vga-capture](https://github.com/mithro/tt-vga-capture); the
calibration designs and their reference renders in
[mithro/tt-vga-testpatterns](https://github.com/mithro/tt-vga-testpatterns).

> **Caution: AI in use.** This project is being built with Claude Code.
> Check the code and the measurements before relying on them.

## What is in it

**The common core**, used by every source and consumer:

| | |
|---|---|
| stream format | `include/vgacap/stream.h`, `src/stream/`: the `VGCH` header and `RAW`, `RLE`, `FRAM`, `EVNT`, `TIME` chunks; a callback-based C99 writer (no allocation, so it also builds into board firmware) and an incremental reader. Python mirror in `python/vgacap/stream.py`. |
| reconstruction | `include/vgacap/frame.h`, `src/frame/` (`libvgaframe`): learns the sync timing and polarity from the signal itself, tolerates glitch pulses, matches a mode table, rebuilds frames (continuous streams, and whole frames or windows from `FRAM` chunks). |
| test data | `python/vgacap/synth.py`: synthetic VGA signals; `vgacap-bin2stream`: wraps a one-byte-per-clock simulator dump as a stream. |

**Capture sources.** Each one is a different way of getting `uo_out` into a
stream; pick by what hardware you have and how fast the design must run.

| source | where | how it reaches the host | status |
|---|---|---|---|
| Tiny Tapeout demo board, stock firmware | `python/ttcap/mp/`: capture programs pushed through the board's MicroPython REPL, run on the PIO of its RP2040 or RP2350; driven by `ttcap` | USB serial, or the fpgas.online websocket bridge | streaming, whole-frame and windowed capture; pixel-exact on RP2350 boards (FPGA and ASIC); on RP2040 boards streaming is proven on silicon, frame/window only in the emulator |
| demo board, run-length | `ttcap capture --mode rle` (vgacap PR #6): the PIO counts runs itself | as above, fewer bytes for simple pictures | exact up to 25 MHz on an FPGA demo board, and on an ASIC |
| demo board (RP2350), vgacap firmware | `firmware/`: a pico-sdk firmware loaded into RAM in place of the stock one (`ttcap --firmware picosdk`) | USB serial, its own protocol (`firmware/PROTOCOL.md`) | frame, window, stream pixel-exact on FPGA and ASIC demo boards |
| demo board (RP2350), MicroPython C module | `mpy/`: the same engines built into a custom MicroPython firmware (`import vgacap`, `ttcap --firmware mpy-module`) | as the stock firmware | pixel-exact on an FPGA demo board |
| Raspberry Pi 5 | `capture-rp1/` (`vgacap-rp1`): the Pi 5's own PIO (in its RP1 I/O chip), via piolib, with the board's `uo_out` wired to the Pi's GPIO | runs on the Pi itself | proven by loopback; waiting for a Pi 5 wired to a Tiny Tapeout board |
| FPGA | `rtl/`: capture logic to build into an FPGA design (Migen + generated Verilog) | whatever link the FPGA design has | exact on an iCE40 at 250 kHz and 25 MHz |
| simulators | `vgacap-bin2stream` today; full simulation traces are being added (tt-vga-capture Milestone 10) | files | in use by the tests |
| bunnie's BIO | research in tt-vga-capture | -- | simulator only |

**Consumers and tools:** `vgacap-dump` and `vgacap-frames` (C), the
`ttcap` CLI (`capture`, `png`, `demo`, `probe`, `throughput`, `reboot`),
the GStreamer plugin in `gst/` (`vgadecode`, `vgacapttsrc`, `vgacapbin`),
`ttcap.emu` (a cycle-level RP2040/RP2350 emulator that runs the exact
board programs, used by the tests), and `tools/hil.py` (the
hardware-in-the-loop check). Each directory's README, linked under
Layout, covers its part.

## Building and testing

```sh
cmake -S . -B build && cmake --build build
ctest --test-dir build --output-on-failure
uv run pytest -q
```

The core libraries are C99 with no dependencies beyond libc; the GStreamer
plugin is built only when its development files are present. CI and the
details: [docs/development.md](docs/development.md).

## A first capture

```sh
uv run ttcap capture serial:/dev/ttyACM0 \
    --project tt_um_rejunity_vga --clock-hz 100000 --seconds 1 --out capture.vgacap
build/vgacap-frames capture.vgacap out/frame      # out/frame-0000.ppm ...
uv run ttcap demo --link serial:/dev/ttyACM0 --clock-hz 60000 --outdir out   # PNGs + MKV
```

More: [python/ttcap/README.md](python/ttcap/README.md) (capturing, the demo,
Welland boards, Pis), [gst/README.md](gst/README.md) (GStreamer),
[src/tools/README.md](src/tools/README.md) (the C tools),
[tools/README.md](tools/README.md) (hardware-in-the-loop check).

## Layout

| | |
|---|---|
| `include/vgacap/stream.h`, `src/stream/` | capture stream format: `VGCH` header, `RAW`, `RLE`, `FRAM`, `EVNT`, `TIME` chunks; writer and incremental reader that emits `(value, run)` pairs |
| `include/vgacap/frame.h`, `src/frame/` | `libvgaframe`: mode table, sync timing learner, frame reconstruction to RGB24, `FRAM` window reassembly |
| [`src/tools/`](src/tools/README.md) | `vgacap-dump` (chunk list, sample checksum), `vgacap-frames` (stream to PPM frames) |
| `python/vgacap/` | Python mirror of the stream format, mode table, synthetic generators, `vgacap-bin2stream` |
| [`python/ttcap/`](python/ttcap/README.md) | the `ttcap` CLI and library: board links (serial, websocket), the capture session for the demo board's RP2040/RP2350, `ttcap demo`; `mp/` the MicroPython board programs, `emu/` the RP2040/RP2350 emulator |
| [`firmware/`](firmware/README.md) | pico-sdk capture firmware for the Tiny Tapeout demo board v3 (RP2350) (portable core in `firmware/core/`, simulated board in `firmware/sim/`) |
| [`mpy/`](mpy/README.md) | the same capture engines as a MicroPython `USER_C_MODULE` |
| [`capture-rp1/`](capture-rp1/README.md) | `vgacap-rp1`, capture with the Raspberry Pi 5's RP1 PIO via piolib |
| [`rtl/`](rtl/README.md) | FPGA capture modules (Migen source and the generated Verilog) |
| [`gst/`](gst/README.md) | the GStreamer plugin |
| [`tools/`](tools/README.md) | `hil.py` (hardware-in-the-loop check), `board_exec.py`, `board_restart.py` |
| `tests/`, `python/tests/`, `gst/tests/` | C unit tests (ctest) and Python + end-to-end tests (pytest) |

The stream writer (`src/stream/writer.c`) is also compiled on its own into
`firmware/`, `mpy/` and `capture-rp1/`, so it stays C99 with no
allocation and no dependencies beyond libc.

The stream format is described in
[tt-vga-capture/docs/research/2026-09-15-stream-format.md](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-09-15-stream-format.md).

## License

Apache-2.0, see [LICENSE](LICENSE).
