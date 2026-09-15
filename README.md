# vgacap

Capture the Tiny VGA Pmod output of a [Tiny Tapeout](https://tinytapeout.com)
project with the PIO block of the demo board's RP2040 / RP2350 (later the
Raspberry Pi 5 RP1), reconstruct the picture on a host, and present it as a
virtual video source through GStreamer.

This repository holds the code: the capture stream formats, the
reconstruction library, the RP2 and RP1 capture backends, the GStreamer
plugin and the Python tools. The design, research notes, work log and task
list live in [mithro/tt-vga-capture](https://github.com/mithro/tt-vga-capture).

> **Caution: AI in use.** This project is being built with Claude Code.
> Check the code and the measurements before relying on them.

## Building and testing

The core libraries are C99 with no dependencies beyond libc.

```sh
cmake -S . -B build && cmake --build build
ctest --test-dir build --output-on-failure
uv run pytest -q
```

## Layout

| | |
|---|---|
| `include/vgacap/stream.h`, `src/stream/` | capture stream format: `VGCH` header, `RAW`, `RLE`, `FRAM`, `EVNT`, `TIME` chunks; writer and incremental reader that emits `(value, run)` pairs |
| `include/vgacap/frame.h`, `src/frame/` | `libvgaframe`: mode table, sync timing learner, frame reconstruction to RGB24, `FRAM` window reassembly |
| `src/tools/` | `vgacap-dump` (chunk list, sample checksum), `vgacap-frames` (stream to PPM frames) |
| `python/vgacap/` | Python mirror of the stream format, synthetic generators, `vgacap-bin2stream` |
| `tests/`, `python/tests/` | C unit tests (ctest) and Python + end-to-end tests (pytest) |

The stream format is described in
[tt-vga-capture/docs/research/2026-09-15-stream-format.md](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-09-15-stream-format.md).

## Tools

```sh
build/vgacap-dump capture.vgacap              # chunk list, total samples, checksum
mkdir -p out && build/vgacap-frames capture.vgacap out/frame   # out/frame-0000.ppm ...
uv run vgacap-bin2stream dump.bin out.vgacap  # wrap a one-byte-per-clock simulator dump
```

A frame is emitted at the frame boundary that closes it, once the timing is
known. A capture that starts mid-frame -- the normal case -- claims the frame
that its first vsync pulse begins, so two frame periods are enough for the
first picture and each further period adds one. A capture that happens to
start exactly on a vsync pulse cannot measure that pulse and waits for the
next one, so capture at least three frame periods to be sure.

Spurious sync pulses are tolerated: a capture whose hsync carries the odd 2
to 30 clock glitch (real silicon does) still reconstructs. `vgacap-frames`
reports `glitches=N` per frame, counting the pulses rejected since the
previous frame, and `glitches_total=N` for the whole stream.

## GStreamer plugin

`gst/` builds `libgstvgacap.so`, whose `vgadecode` element turns a capture
stream (`application/x-vgacap`) into `video/x-raw` RGB frames. The plugin is
optional: CMake skips it when the GStreamer development files are missing,
and the plugin tests skip with it.

```sh
export GST_PLUGIN_PATH=$PWD/build
gst-inspect-1.0 vgadecode
gst-launch-1.0 filesrc location=capture.vgacap ! vgadecode ! \
    pngenc ! multifilesink location=frame-%04d.png
```

| property | default | |
|---|---|---|
| `repeat-last-frame` | false | re-push the last frame to hold a steady `output-fps` cadence |
| `output-fps` | 30/1 | rate used when repeating, and when the stream declares no project clock |
| `max-width`, `max-height` | 1400, 900 | size of the reconstruction buffers, allocated once when the element starts |
| `force-mode` | `""` | force a mode table entry instead of detecting one |
| `partial` | false | also push frames whose lines were not all covered |

Frames are timestamped in project time -- clocks since the first emitted
frame divided by the stream's `clock_hz` -- when the header declares a clock,
and at `output-fps` otherwise. A partial frame carries
`GST_BUFFER_FLAG_CORRUPTED`, and every frame's counter rides in
`GST_BUFFER_OFFSET`. The detected timing is posted on the bus as an element
message named `vgacap-timing`, once when it is first known and again whenever
it changes.

## Running on a Raspberry Pi

The board tools (`ttcap`) need only pyserial and websockets. On a Pi, keep
the dev group (pytest, numpy, Pillow) out of the environment, otherwise `uv`
will try to build numpy and Pillow from source and can hang a Pi 3:

```sh
git clone https://github.com/mithro/vgacap.git && cd vgacap
export UV_NO_DEV=1
uv sync
uv run --no-sync ttcap probe ws://127.0.0.1:8765/serial
```

## Capturing from a board

```sh
uv run ttcap probe serial:/dev/ttyACM0          # sys.version and the GPIO map
uv run ttcap throughput serial:/dev/ttyACM0     # what clock the link can keep up with
uv run ttcap capture serial:/dev/ttyACM0 \
    --project tt_um_rejunity_vga --clock-hz 100000 --seconds 1 --out capture.vgacap
uv run ttcap png capture.vgacap out/frame       # out/frame-0000.png ...
```

`--profile` defaults to `auto`, which reads the board's `GPIOMap` and picks
the RP2040 or RP2350 layout from it. Stop the capture by time (`--seconds`)
or by size (`--max-bytes N`, or `--frames N` which works the bytes out from
640x480@60 timing and the board's packing, adding two frame periods of
margin for the convergence described above); `--seconds 0` runs until the
byte limit.

The board has very little heap -- about 80 KB on the RP2040 demo board --
so `ttcap capture` clears the previous run's names and collects before it
sends anything, and refuses to start unless the free heap covers the two
DMA buffers plus room to compile the script (`8 * --buf-words + 24000`
bytes, so ~56 KB at the default and ~90 KB at `--buf-words 8192`, which no
demo board has). If it refuses, reset the board: a script that fails to
compile up there does not always say so, and can halt the firmware outright. `ttcap png` needs Pillow, so it wants
the `synth` extra (`uv sync --extra synth`) and a built `build/vgacap-frames`.

Performance captures should own the serial device: stop the fpgas.online
bridge first (`sudo systemctl stop fpgas-tt`) and start it again afterwards.

## License

Apache-2.0, see [LICENSE](LICENSE).
