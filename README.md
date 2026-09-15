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

A frame is emitted at the next frame boundary once the timing is known, so
a capture that starts mid-frame needs one boundary plus one full frame
before the first picture: capture at least three frame periods.

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

## License

Apache-2.0, see [LICENSE](LICENSE).
