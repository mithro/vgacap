# ttcap

The `ttcap` command and library: capture from a Tiny Tapeout demo board (its RP2040 or RP2350 microcontroller samples the design's `uo_out` with PIO), turn captures into PNGs, and run a live demo. `mp/` holds the MicroPython board programs, `emu/` the RP2040/RP2350 emulator the tests run them on.

## Capturing from a Tiny Tapeout demo board (stock firmware)

This is the source that needs nothing installed on the board: `ttcap` pushes
the capture program through the stock Tiny Tapeout firmware's MicroPython
REPL. The other sources have their own READMEs: `firmware/README.md`
(pico-sdk firmware), `mpy/README.md` (MicroPython C module),
`capture-rp1/README.md` (Raspberry Pi 5) and `rtl/README.md` (FPGA).


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

## `ttcap demo`: a board in, video out

```sh
uv run ttcap demo --board tt07 --project tt_um_rejunity_vga \
    --clock-hz 60000 --seconds 60 --outdir out            # PNGs + out/capture.mkv
uv run ttcap demo --board tt07 --clock-hz 60000 --outdir out --window --serve 8080
uv run ttcap demo --board tt07 --clock-hz 60000 --outdir out --dry-run   # just the pipeline
```

One capture feeds every output, through a `tee`:

| output | on by default | pipeline |
|---|---|---|
| `out/frame-%04d.png` | yes (`--no-png` to drop) | `pngenc ! multifilesink` |
| `out/capture.mkv` | yes (`--no-video` to drop) | `x264enc`, else `vp8enc`, into `matroskamux` |
| a window | `--window` | `autovideosink` |
| `http://localhost:PORT/` | `--serve PORT` | `jpegenc ! multipartmux` into an MJPEG HTTP server |

`--serve` publishes `multipart/x-mixed-replace` on the loopback address and
an index page with an `<img src="/stream.mjpg">` in it, which is all a
browser needs. It is standard library only, and a viewer that falls behind
loses frames rather than slowing the capture down.

At a 60 kHz project clock a 640x480 frame is 800x525 clocks, so **seven
seconds long** -- honest timing, and a slideshow rather than a video.
`--fps 30` (or `30/1`, or `29.97`) sets `vgadecode`'s `repeat-last-frame` and
`output-fps`, which re-pushes the last frame to fill the gaps so the video
plays at wall-clock speed. Nothing is invented: a repeated frame is the frame
that was on the screen. It is set on the decoder, so every output shares the
one cadence.

`--dry-run` prints the `gst-launch-1.0` command and exits without touching
the board, the outdir or the port -- the same command a real run prints
before it starts, so it can be copied, edited and run by hand. The demo
drives `gst-launch-1.0` as a child process rather than building the pipeline
in-process, because the GStreamer Python bindings (`python3-gi`) are an OS
package and `ttcap` runs under `uv`; the pipeline text is the same either
way, and `ttcap.demo.use_gst_python()` is the check.

Every numeric argument is range-checked before the board is contacted, and a
bad one is refused with the range it takes. GObject answers an out-of-range
property with a `CRITICAL` on stderr and then *ignores* it, so `--seconds -5`
used to leave `seconds` at its default of 0 — which means "capture until
stopped", so a typo started an unbounded capture on a shared board.
`--seconds 0` still means exactly that when it is asked for. A `--clock-hz`
above what the demo board has been measured to keep up with *at the default
buffer size* (60 kHz on an RP2040 demo board, 750 kHz on an RP2350) is a
**warning**, not an error: watching the board overrun is a legitimate thing
to want, the closing `TIME` chunk reports it, and a larger `--buf-words`
moves the ceiling.

`--outdir` is one run's alone. A directory that already holds a
`frame-*.png` or a `capture.mkv` is **refused**, because a shorter capture
would overwrite the first frames and leave the rest — two runs mixed
together, which is the worst thing to find in `docs/results/` later.
`--force` deletes every `frame-*.png` and `capture.mkv` in it first (the
refusal names them, so nothing goes unseen) and leaves everything else
alone. A run that finishes without producing any frames exits **3**, the
same code `ttcap capture` uses for "no samples at all" — judged on the
files when `--no-png`/`--no-video` did not switch them off, and on the
decoder's own bus messages when the only output is a window or a stream,
neither of which can be counted.

Ctrl-C ends the run cleanly: the signal is forwarded once to
`gst-launch -e`, which turns it into an end-of-stream, so the board winds
down through `vgacapttsrc`'s cooperative stop and the Matroska file is
finalised rather than truncated. Allow one DMA buffer for that -- 2.2 s at a
60 kHz project clock. `SIGTERM`, `SIGHUP` and `SIGQUIT` do the same, and
however the demo ends -- an exception, a `kill`, a closed terminal -- it
tears the pipeline's whole process group down on the way out, so a capture
is never left holding a shared bench board. A third signal terminates the
pipeline and a fourth kills it, at the cost of the video. Only `SIGKILL` is
uncoverable, since nothing in the process runs after it.

Because every one of those is a *graceful* stop, the demo then exits with
the pipeline's own status: a `kill` on a run that captured successfully
exits **0**, not 143. A supervisor reading exit status should treat that as
the success it is.

## Reaching a Welland board

`--board` takes a slug from the `WELLAND` table (`tt03p5`, `tt04`..`tt08`,
`fpga-1`, `fpga-3`, `fpga-4`; ports move, see the comment on `WELLAND`)
and resolves it to a link:

* **on the board's own Pi**, `serial:/dev/ttboard` -- stop the fpgas.online
  bridge first (`sudo systemctl stop fpgas-tt`), since it holds the device
  open;
* **anywhere else**, the bridge at `ws://10.21.2.<port>:8765/serial`, which
  is on the bench network. From a workstation that means an SSH tunnel:

```sh
ssh -N -L 18007:10.21.2.7:8765 tweed.welland.mithis.com   # tt07; 18000 + the octet
uv run ttcap demo --link ws://127.0.0.1:18007/serial --clock-hz 60000 --outdir out
```

Any free local port works; one per board keeps two tunnels from colliding.
Not the octet on its own (`-L 7:…`): every Welland slug's octet is below
1024, which ssh refuses to forward without root.

`--link` overrides the board entirely, and a run over the bridge that fails
to connect prints that tunnel command before it exits.

The source is built by name with its settings as element properties --
never as a `vgacapbin` URI. `ttcap-command`, which names the program that
talks to the board, is one of those settings, and a URI query is not allowed
to set it (see above); a command that publishes a stream to a browser is
exactly the place that restriction is for.
