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
the capture program through the stock Tiny Tapeout firmware's MicroPython
REPL. The other capture sources have their own READMEs:
[`firmware/`](../../firmware/README.md) (pico-sdk firmware),
[`mpy/`](../../mpy/README.md) (MicroPython C module),
[`capture-rp1/`](../../capture-rp1/README.md) (Raspberry Pi 5) and
[`rtl/`](../../rtl/README.md) (FPGA).

```sh
ssh -N -L 18034:10.21.2.34:8765 tweed.welland.mithis.com   # fpga-1
uv run ttcap probe ws://127.0.0.1:18034/serial          # sys.version and the GPIO map
uv run ttcap throughput ws://127.0.0.1:18034/serial     # what clock the link can keep up with
uv run ttcap capture ws://127.0.0.1:18034/serial --design tt_um_vga_pattern \
    --clock-hz 500000 --frames 2 --out capture.vgacap
uv run ttcap png capture.vgacap out/frame       # out/frame-0000.png ...
```

`--profile` defaults to `auto`, which reads the board's `GPIOMap` and picks
the RP2040 or RP2350 layout from it.

Stop the capture by time (`--seconds`) or by size (`--max-bytes N`, or
`--frames N`). `--frames N` captures N + 2 frame periods at 640x480@60
timing ([When frames appear](../../src/tools/README.md#when-frames-appear)).
`--seconds 0` runs until the byte limit.

The board has very little heap (about 80 KB on the RP2040 demo board), so
`ttcap capture` clears the previous run's names and collects garbage before
it sends anything. It refuses to start unless the free heap covers the two
DMA buffers plus room to compile the script: `8 * --buf-words + 24000`
bytes, so ~56 KB at the default and ~90 KB at `--buf-words 8192`, which an
RP2040 demo board does not have. If it refuses, reset the board: a script
that fails to compile on the board does not always say so, and can halt the
firmware outright.

`ttcap png` needs [Pillow](https://github.com/python-pillow/Pillow), so it wants the `synth` extra
(`uv sync --extra synth`) and a built `build/vgacap-frames`.

Performance captures should own the serial device: on a board's own Pi,
stop the [fpgas.online](https://tinytapeout.fpgas.online) bridge first (`sudo systemctl stop fpgas-tt`) and
start it again afterwards.

## Running on a Raspberry Pi

The board tools (`ttcap`) need only [pyserial](https://github.com/pyserial/pyserial) and [websockets](https://github.com/python-websockets/websockets). On a Pi, keep
the dev group (pytest, numpy, Pillow) out of the environment; otherwise
[`uv`](https://docs.astral.sh/uv/) will try to build numpy and Pillow from source, which can hang a Pi 3:

```sh
git clone https://github.com/mithro/vgacap.git && cd vgacap
export UV_NO_DEV=1
uv sync
uv run --no-sync ttcap probe ws://127.0.0.1:8765/serial
```

## `ttcap demo`: a board in, video out

```sh
uv run ttcap demo --board fpga-1 --design tt_um_vga_pattern \
    --clock-hz 60000 --seconds 60 --outdir out            # PNGs + out/capture.mkv
uv run ttcap demo --board fpga-1 --clock-hz 500000 --outdir out --window --serve 8080
uv run ttcap demo --board fpga-1 --clock-hz 500000 --outdir out --dry-run   # just the pipeline
```

`--board fpga-1` names a board on the Welland bench; see
[Reaching a Welland board](#reaching-a-welland-board), or use `--link` for
a board of your own.

One capture feeds every output, through a `tee`:

| output | default | flag | encoder | fallback encoder | muxer | sink |
|---|---|---|---|---|---|---|
| `out/frame-%04d.png` | on | `--no-png` | `pngenc` | -- | -- | `multifilesink` |
| `out/capture.mkv` | on | `--no-video` | `x264enc` | `vp8enc` | `matroskamux` | `filesink` |
| window | off | `--window` | -- | -- | -- | `autovideosink` |
| `http://localhost:PORT/` | off | `--serve PORT` | `jpegenc` | -- | `multipartmux` | `fdsink` |

`--serve` runs an MJPEG HTTP server that publishes
`multipart/x-mixed-replace` on the loopback address,
with an index page containing an `<img src="/stream.mjpg">`, which is all a
browser needs. The server uses only the standard library, and a viewer
that falls behind loses frames rather than slowing the capture down.

### Frame rate

At a 60 kHz project clock a 640x480 frame is 800x525 clocks, so each frame
is **seven seconds long**: honest timing, but a slideshow rather than a
video. `--fps 30` (or `30/1`, or `29.97`) sets [`vgadecode`](../../gst/README.md#vgadecode-a-stream-in-video-out)'s
`repeat-last-frame` and `output-fps`, which re-push the last frame to fill
the gaps so the video plays at wall-clock speed. Nothing is invented: a
repeated frame is the frame that was on the screen. The setting is on the
decoder, so every output shares the one cadence.

### The pipeline

`--dry-run` prints the `gst-launch-1.0` command and exits without touching
the board, the outdir or the port. It is the same command a real run
prints before it starts, so you can copy, edit and run it by hand.

The pipeline builds `vgacapttsrc` by name, never as a `vgacapbin` URI
([why](../../gst/README.md#vgacapbin-a-uri-in-video-out)).

### Checked arguments

Every numeric argument is range-checked before the board is contacted, and
a bad one is refused with the range it takes.

A `--clock-hz` above what the demo board has been measured to keep up with
*at the default buffer size* (60 kHz on an RP2040 demo board, 750 kHz on an
RP2350 one) is a **warning**, not an error. Watching the board overrun is a
legitimate thing to want, the closing `TIME` chunk reports it, and a larger
`--buf-words` moves the ceiling.

### The output directory

`--outdir` belongs to one run alone. A directory that already holds a
`frame-*.png` or a `capture.mkv` is **refused**, because a shorter capture
would overwrite the first frames and leave the rest: two runs mixed
together.
`--force` first deletes every `frame-*.png` and `capture.mkv` in it (the
refusal names them, so nothing goes unseen) and leaves everything else
alone.

A run that finishes without producing any frames exits **3**, the same code
`ttcap capture` uses for "no samples at all". The demo judges this on the
files, unless `--no-png`/`--no-video` switched them off, and otherwise on
the decoder's own bus messages, since a window or a stream cannot be
counted.

### Stopping

Ctrl-C ends the run cleanly. The demo forwards the signal once to
`gst-launch-1.0 -e`, which turns it into an end-of-stream, so the board winds
down through `vgacapttsrc`'s cooperative stop and the Matroska file is
finalized rather than truncated. Allow one DMA buffer for that: 2.2 s at a
60 kHz project clock. `SIGTERM`, `SIGHUP` and `SIGQUIT` do the same.
However the demo ends (an exception, a `kill`, a closed terminal), it tears
down the pipeline's whole process group on the way out, so a capture is
never left holding a shared bench board. A third signal terminates the
pipeline and a fourth kills it, at the cost of the video. Only `SIGKILL`
cannot be covered, since nothing in the process runs after it.

Because every one of those is a *graceful* stop, the demo then exits with
the pipeline's own status: a `kill` on a run that captured successfully
exits **0**, not 143.

## Reaching a Welland board

The Welland boards are a shared, public bench of Tiny Tapeout demo boards,
listed on [tinytapeout.fpgas.online](https://tinytapeout.fpgas.online). Each board is attached to its own
Raspberry Pi, which runs the fpgas.online bridge (`fpgas-tt`).

`--board` takes a slug from the `WELLAND` table in [`boards.py`](boards.py) (`tt03p5`,
`tt04`..`tt08`, `fpga-1`, `fpga-3`, `fpga-4`; boards move between ports,
see the comment on `WELLAND`) and resolves it to a link:

* **on the board's own Pi**, `serial:/dev/ttboard`. Stop the fpgas.online
  bridge first (`sudo systemctl stop fpgas-tt`), since it holds the device
  open;
* **anywhere else**, the bridge at `ws://10.21.2.<octet>:8765/serial`, which
  is on the bench network. The octet is the board's bench port number
  (7 for tt07, 34 for fpga-1). From a workstation that means an SSH
  tunnel:

```sh
ssh -N -L 18034:10.21.2.34:8765 tweed.welland.mithis.com   # fpga-1; 18000 + the octet
uv run ttcap demo --link ws://127.0.0.1:18034/serial --design tt_um_vga_pattern --clock-hz 60000 --outdir out
```

Any free local port works, and one per board keeps two tunnels from
colliding. Do not use the octet on its own (`-L 34:…`): every Welland slug's
octet is below 1024, and ssh refuses to forward those without root.

`--link` overrides the board entirely. A run over the bridge that fails to
connect prints that tunnel command before it exits.
