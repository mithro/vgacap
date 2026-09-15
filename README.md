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

`gst/` builds `libgstvgacap.so`, with three elements: `vgadecode` turns a
capture stream (`application/x-vgacap`) into `video/x-raw` RGB frames,
`vgacapttsrc` produces such a stream live from a board, and `vgacapbin`
picks a source from a URI and puts `vgadecode` behind it. The plugin is
optional: CMake skips it when the GStreamer development files are missing,
and the plugin tests skip with it.

```sh
export GST_PLUGIN_PATH=$PWD/build
gst-inspect-1.0 vgadecode
gst-launch-1.0 filesrc location=capture.vgacap ! vgadecode ! \
    pngenc ! multifilesink location=frame-%04d.png

# ...or straight off a board, through either link. The URI is quoted twice:
# once for the shell, once for GStreamer's pipeline parser -- see below.
gst-launch-1.0 vgacapbin \
    'uri="tt-ws://welland:8765/serial?project=tt_um_rejunity_vga&clock-hz=60000"' ! \
    videoconvert ! autovideosink
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
and at `output-fps` otherwise. A clock rate that changes mid-stream (a `TIME`
chunk reporting a measured rate, say) freezes the elapsed time and continues
from there, so the timeline never runs backwards.

A partial frame carries `GST_BUFFER_FLAG_CORRUPTED`, and every frame's
counter rides in `GST_BUFFER_OFFSET`. That counter is the *source's* frame
number, not an output index: it skips the frames the `partial` property
filters out, and it restarts at zero when the stream restarts (a second
`VGCH` header, or a flushing seek). Number output frames downstream, or use
the PTS, if you need something that only ever goes up.

The detected timing is posted on the bus as an element message named
`vgacap-timing`, once when it is first known and again whenever it changes.

### `vgacapttsrc`: capturing from a board

`vgacapttsrc` runs `ttcap capture --out -` as a child process and pushes its
stdout as 64 KiB `application/x-vgacap` buffers, so the board protocol stays
in Python and the same element works over serial or over the bridge.

| property | default | |
|---|---|---|
| `link` | `""` | `serial:/dev/ttyACM0`, or `ws://host:8765/serial`; required |
| `clock-hz` | 0 | project clock to program; required |
| `project`, `design` | `""` | `tt.shuttle` macro / FPGA bitstream to enable |
| `profile` | `auto` | board profile, or ask the board for its `GPIOMap` |
| `pio`, `buf-words` | -1 | board tuning; -1 leaves `ttcap`'s own defaults |
| `seconds` | 0 | capture duration; 0 captures until the element is stopped |
| `ttcap-command` | `uv run --no-sync ttcap` | split with shell quoting rules, so an absolute path or another interpreter works |
| `stop-timeout` | 15 | how long a cooperative stop may take |

`ttcap-command` is run with the pipeline's own working directory, which is
what `uv run` needs to find the project; give an absolute `ttcap` (or an
absolute interpreter and script) when the pipeline runs from elsewhere.

**Stopping matters.** The board samples into a DMA double buffer and can only
stop at a buffer boundary -- about 82 ms at 750 kHz, but 2.2 s at the
RP2040's 60 kHz project-clock ceiling -- and `ttcap` turns its first `SIGINT`
into a cooperative stop that finishes the chunk, writes its closing `TIME`
chunk (the only overrun and RXSTALL report there is) and exits 0. So the
element signals the child's process group and then waits *while draining its
stdout*, because a child blocked writing into a full pipe never reaches the
code that emits its trailer. Only when `stop-timeout` runs out does it close
the read end (`ttcap` reads the `EPIPE` as a clean end and still exits 0, but
the trailer then has nowhere to go), and only after that does it `SIGKILL`.
Every path reaps the child. The default command makes `ttcap` a *grandchild*
(`uv run` is in between), so if the wrapper exits first a `SIGKILL` also goes
to the process group -- otherwise the capture would carry on holding the
board with nothing left to signal. No wait in the sequence is unbounded: it
all runs inside a state change, and a state change that never returns is a
pipeline nobody can shut down.

**A live capture cannot be paused, only stopped.** The board samples in real
time and the element's pipe is the only buffer between it and the pipeline,
so a PAUSED pipeline gives the capture exactly one pipe buffer of grace --
measured at 65,548 bytes, about 90 ms at 750 kHz and about 1.1 s at 60 kHz --
and after that the DMA buffers overrun and samples are lost. Worse, the
overrun count lives in the closing `TIME` chunk, which the stop sequence
drains and discards, so a capture that was paused and then stopped is
silently short with nothing on the bus to say so. Go straight to PLAYING and
stop when finished; if something must pause, treat what comes after as a
different capture.

A child that exits non-zero raises a pipeline `ERROR` quoting the last lines
of its stderr, so a capture that fails on the board is an error and not a
silent end of stream; the rest of its stderr is logged at `INFO` under the
`vgacapttsrc` debug category.

### `vgacapbin`: a URI in, video out

```sh
gst-launch-1.0 vgacapbin 'uri="tt-serial:///dev/ttyACM0?project=tt_um_x&clock-hz=60000"' ! ...
gst-launch-1.0 vgacapbin 'uri="tt-ws://welland:8765/serial?clock-hz=60000"' ! ...   # or tt-wss://
gst-launch-1.0 vgacapbin 'uri="file:///captures/tt08.vgacap"' ! ...
```

**Quote the URI twice**, as above. The outer quotes are for the shell, which
would otherwise background the command at the `&` and glob the `?`; the inner
ones are for GStreamer's own pipeline parser, needed when the line is handed
to `gst_parse_launch()` as a single string -- from Python, say -- rather than
as an already-split argument vector. An unquoted URI usually fails before the
element ever sees it.

The query string sets the source's properties by name, and the source's
properties are mirrored on the bin, so `?seconds=10` and `seconds=10` do the
same thing (the query wins if both are given). `tt-ws://host:8765` with no
path means the bridge's `/serial` endpoint.

A query may only carry the **capture parameters**: `project`, `design`,
`clock-hz`, `profile`, `pio`, `buf-words`, `seconds`, `stop-timeout`. Naming
anything else -- in particular `ttcap-command`, which is a program to run, or
`link`, which would move the capture somewhere other than where the URI says
-- is an error, not a silent no-op. A URI can arrive from somewhere that is
not a trusted shell, so those stay settable only as element properties, by
whoever builds the pipeline.

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
