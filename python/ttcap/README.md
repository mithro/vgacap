# ttcap

`ttcap` is the command and Python library for capturing from a
[Tiny Tapeout](https://tinytapeout.com) demo board: the board's RP2040 or
RP2350 microcontroller samples the design's `uo_out` with its PIO, and
`ttcap` drives it, turns captures into PNGs and runs a live demo.
[`mp/`](mp/) holds the [MicroPython](https://micropython.org) programs that
run on the board, and [`emu/`](emu/) the RP2040/RP2350 emulator the tests
run them on.

## Capturing with the stock firmware

This capture source needs nothing installed on the board: `ttcap` pushes
the capture program through
[the stock firmware](https://github.com/TinyTapeout/tt-micropython-firmware)'s MicroPython
REPL. The other capture sources have their own READMEs:
[`firmware/`](../../firmware/README.md) (the vgacap firmware),
[`mpy/`](../../mpy/README.md) (MicroPython C module),
[`capture-rp1/`](../../capture-rp1/README.md) (Raspberry Pi 5) and
[`rtl/`](../../rtl/README.md) (FPGA).

These commands assume a demo board on `/dev/ttyACM0` running a 640x480 VGA
design, at 500 kHz on an RP2350 board (an RP2040 board keeps up with 60 kHz):

```sh
uv run ttcap probe serial:/dev/ttyACM0          # sys.version and the GPIO map
uv run ttcap throughput serial:/dev/ttyACM0     # what clock the link can keep up with
uv run ttcap capture serial:/dev/ttyACM0 --clock-hz 500000 --frames 2 --seconds 0 --out capture.vgacap
mkdir -p out && uv run ttcap png capture.vgacap out/frame   # out/frame-0000.png ...
```

`--profile` defaults to `auto`, which reads the board's `GPIOMap` and picks
the RP2040 or RP2350 layout from it.

The capture stops at the first of `--seconds` (default 5; 0 for no limit),
`--max-bytes N` or `--frames N`. In stream mode, `--frames N` captures N + 2
frame periods at 640x480@60 timing
([When frames appear](../../src/frame/explanation-rebuilding-frames.md#when-the-first-frame-appears)); in
frame and window modes it stops after N complete pictures.

`ttcap capture` refuses to start without enough free heap:
`8 * --buf-words + 24000` bytes in stream mode (~56 KB at the default; an
RP2040 demo board has about 80 KB), or 24000 bytes plus one line in frame
and window modes. If it refuses, reset the board.

`ttcap png` needs [Pillow](https://github.com/python-pillow/Pillow) and
[a built `build/vgacap-frames`](../../docs/development.md).

For a board on the Welland bench, see
[Reaching a Welland board](#reaching-a-welland-board-bench-access-needed).

### Run-length capture

`ttcap capture --mode rle` has the PIO run-length encode the samples, so a
simple picture streams at a clock the link could not carry sample by
sample.

- `--rle-oneshot` fills the two `--buf-words` buffers once at full speed,
  then sends them.
- `--rle-mask` picks the sampling program: `1` (RP2350 only), `0` (any
  chip) or `-1`, the default, for the board to choose.
- It stops on `--max-bytes` or `--seconds`; `--frames` is refused.
- A run whose board reported an RX stall exits 1.
- Only the stock firmware runs it; `--firmware picosdk` and
  `--firmware mpy-module` refuse it.

## Running on a Raspberry Pi

The board tools (`ttcap`) need only [pyserial](https://github.com/pyserial/pyserial) and [websockets](https://github.com/python-websockets/websockets). On a Pi, keep
the [dev group](../../pyproject.toml) out of the environment; otherwise
[`uv`](https://docs.astral.sh/uv/) will try to build numpy and Pillow from source, which can hang a Pi 3:

```sh
git clone https://github.com/mithro/vgacap.git && cd vgacap
export UV_NO_DEV=1
uv sync
uv run --no-sync ttcap probe serial:/dev/ttyACM0
```

With `UV_NO_DEV=1`, `ttcap png` needs `uv sync --extra synth`.

## `ttcap demo`: a board in, video out

```sh
export GST_PLUGIN_PATH=$PWD/build
uv run ttcap demo --link serial:/dev/ttyACM0 \
    --clock-hz 60000 --seconds 60 --outdir video          # PNGs + video/capture.mkv
uv run ttcap demo --link serial:/dev/ttyACM0 --clock-hz 500000 --outdir live --window --serve 8080
uv run ttcap demo --link serial:/dev/ttyACM0 --clock-hz 500000 --outdir dry --dry-run   # just the pipeline
```

The demo needs the [GStreamer plugin built](../../docs/development.md). On
the Welland bench, `--board SLUG` replaces `--link`; see
[Reaching a Welland board](#reaching-a-welland-board-bench-access-needed).

One capture feeds every output, through a `tee`:

| output | default | flag | encoder | fallback encoder | muxer | sink |
|---|---|---|---|---|---|---|
| `OUTDIR/frame-%04d.png` | on | `--no-png` | `pngenc` | -- | -- | `multifilesink` |
| `OUTDIR/capture.mkv` | on | `--no-video` | `x264enc` | `vp8enc` | `matroskamux` | `filesink` |
| window | off | `--window` | -- | -- | -- | `autovideosink` |
| `http://localhost:PORT/` | off | `--serve PORT` | `jpegenc` | -- | `multipartmux` | `fdsink` |

`--serve` runs an MJPEG HTTP server that publishes
`multipart/x-mixed-replace` on the loopback address, with an index page
containing an `<img src="/stream.mjpg">`. A viewer that falls behind loses
frames rather than slowing the capture down.

### Frame rate

At a 60 kHz project clock a 640x480 frame is 800x525 clocks, so each frame
is **seven seconds long**: honest timing, but a slideshow rather than a
video. `--fps 30` (or `30/1`, or `29.97`) sets [`vgadecode`](../../gst/reference-elements.md#vgadecode-properties)'s
`repeat-last-frame` and `output-fps`, which re-push the last frame to fill
the gaps so the video plays at wall-clock speed. The setting is on the
decoder, so every output shares the one cadence.

### The pipeline

`--dry-run` prints the `gst-launch-1.0` command and exits without touching
the board, the outdir or the port. It is the same command a real run
prints before it starts, so you can copy, edit and run it by hand.

The pipeline builds `vgacapttsrc` by name, never as a `vgacapbin` URI
([why](../../gst/reference-elements.md#vgacapbin-uris)).

### Checked arguments

Every numeric argument is range-checked before the board is contacted, and
a bad one is refused with the range it takes.

With `--board`, a `--clock-hz` above what the demo board has been measured
to keep up with *at the default buffer size* (60 kHz on an RP2040 demo
board, 750 kHz on an RP2350 one) is a **warning**, not an error; a larger
`--buf-words` moves the ceiling.

### The output directory

`--outdir` belongs to one run alone. A directory that already holds a
`frame-*.png` or a `capture.mkv` is **refused**.
`--force` first deletes every `frame-*.png` and `capture.mkv` in it (the
refusal names them, so nothing goes unseen) and leaves everything else
alone.

A run that finishes without producing any frames exits **3**, the same code
`ttcap capture` uses when no samples arrived and the board reported no
error (a board error exits 1).

### Stopping

Ctrl-C ends the run cleanly. The demo forwards the signal once to
`gst-launch-1.0 -e`, which turns it into an end-of-stream, so the board winds
down through [`vgacapttsrc`'s cooperative stop](../../gst/explanation-live-source.md#why-stopping-is-a-sequence) and the Matroska file is
finalized rather than truncated. Allow one DMA buffer for that: with the
default `--buf-words`, about 22 ms on an RP2350 at 750 kHz and about
137 ms on an RP2040 at 60 kHz. `SIGTERM`, `SIGHUP` and `SIGQUIT` do the
same. Because these are *graceful* stops, the demo exits with the
pipeline's own status: a `kill` on a run that captured successfully exits
**0**, not 143.

Repeated stop signals escalate: the first ends the stream, a second stops
it now, a third terminates the pipeline and a fourth kills it, at the cost
of the video.

However the demo ends (an exception, a `kill`, a closed terminal), it tears
down the pipeline's whole process group on the way out, so a capture is
never left holding a shared bench board.

## Reaching a Welland board (bench access needed)

The Welland bench is a shared set of Tiny Tapeout demo boards, listed on
[tinytapeout.fpgas.online](https://tinytapeout.fpgas.online). Each board
is attached to its own Raspberry Pi, which runs the fpgas.online bridge
(`fpgas-tt`).

Before running against a bench board, check `/board/<slug>/status.json`
on tinytapeout.fpgas.online for other clients, and that its USB serial
matches the one in [`boards.py`](boards.py): boards move between ports.

`ttcap demo --board` takes a slug from the `WELLAND` table in
`boards.py`, such as `fpga-1`, `fpga-3` or `fpga-4`, and
resolves it to a link:

* **on the board's own Pi**, `serial:/dev/ttboard`. Stop the bridge first
  (`sudo systemctl stop fpgas-tt`), since it holds the device open, after
  checking it has no clients, and start it again afterwards
  (`sudo systemctl start fpgas-tt`);
* **anywhere else**, the bridge at `ws://10.21.2.<octet>:8765/serial`, which
  is on the bench network. The octet is the board's bench port number
  (34 for fpga-1, 35 for fpga-4). From a workstation that means an SSH
  tunnel:

```sh
ssh -f -N -L 18034:10.21.2.34:8765 tweed.welland.mithis.com   # fpga-1; 18000 + the octet
uv run ttcap demo --link ws://127.0.0.1:18034/serial --design tt_um_vga_pattern --clock-hz 500000 --outdir tunnel
```

Any free local port works, and one per board keeps two tunnels from
colliding.

`--link` overrides the board entirely. With `--board` from a workstation, a
failed run prints the `ssh -L` command for that board.
