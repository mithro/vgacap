# GStreamer plugin

`gst/` builds `libgstvgacap.so`, a [GStreamer](https://gstreamer.freedesktop.org)
plugin that decodes a
[vgacap stream](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-09-15-stream-format.md)
to video, captures live from a [Tiny Tapeout](https://tinytapeout.com) demo
board, or does both from a URI. It has three elements:

- `vgadecode` turns a vgacap stream (`application/x-vgacap`) into
  `video/x-raw` RGB frames;
- `vgacapttsrc` produces such a stream live from a board;
- `vgacapbin` picks a source from a URI and puts `vgadecode` behind it.

The plugin is optional: CMake skips it when the GStreamer development files
are missing, and the plugin tests skip with it.

```sh
export GST_PLUGIN_PATH=$PWD/build
gst-inspect-1.0 vgadecode
gst-launch-1.0 filesrc location=capture.vgacap ! vgadecode ! \
    pngenc ! multifilesink location=frame-%04d.png

# ...or straight off a board, through either link.
gst-launch-1.0 vgacapbin \
    'uri="tt-ws://127.0.0.1:18034/serial?design=tt_um_vga_pattern&clock-hz=60000"' ! \
    videoconvert ! autovideosink
```

`127.0.0.1:18034` is an SSH tunnel to fpga-1, which needs bench access
([Reaching a Welland board](../python/ttcap/README.md#reaching-a-welland-board-bench-access-needed)).
On quoting, see [`vgacapbin`](#vgacapbin-a-uri-in-video-out).

## `vgadecode`: a stream in, video out

| property | default | unit | description |
|---|---|---|---|
| `repeat-last-frame` | `false` | -- | re-push the last frame to hold a steady `output-fps` cadence |
| `output-fps` | `30/1` | frames/s | fallback frame rate |
| `max-width` | `1400` | project clocks | widest line the reconstruction buffers hold |
| `max-height` | `900` | lines | tallest frame the reconstruction buffers hold |
| `force-mode` | `""` | -- | mode table entry to use instead of detecting one |
| `partial` | `false` | -- | also push frames whose lines were not all covered |

The reconstruction buffers are allocated once, when the element starts.

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

An overrun (a `TIME` chunk with the message `overrun`, or a rise in its
dropped count) discards the picture in progress, and the next frame out
carries `GST_BUFFER_FLAG_DISCONT`.

## `vgacapttsrc`: capturing from a board

`vgacapttsrc` runs `ttcap capture --out -` as
a child process and pushes its stdout as 64 KiB `application/x-vgacap`
buffers. The board protocol stays in Python ([`ttcap`](../python/ttcap/README.md)), and the same element works over
USB serial or over the [fpgas.online bridge](https://tinytapeout.fpgas.online)
(`fpgas-tt`).

| property | default | unit | default means | required | in a URI query | description |
|---|---|---|---|---|---|---|
| `link` | `""` | -- | -- | yes | no | board link |
| `clock-hz` | `0` | Hz | -- | yes | yes | project clock to program |
| `project` | `""` | -- | keep the selected project | no | yes | `tt.shuttle` macro to enable |
| `design` | `""` | -- | -- | no | yes | FPGA bitstream to enable |
| `profile` | `auto` | -- | ask the board for its `GPIOMap` | no | yes | board profile |
| `pio` | `-1` | -- | `ttcap`'s default | no | yes | PIO block for the sampler |
| `buf-words` | `-1` | words | `ttcap`'s default | no | yes | words per DMA buffer |
| `seconds` | `0` | s | until the element is stopped | no | yes | capture duration |
| `ttcap-command` | `uv run --no-sync ttcap` | -- | -- | no | no | command that runs `ttcap` |
| `stop-timeout` | `15` | s | -- | no | yes | how long a cooperative stop may take |
| `capture-mode` | `stream` | -- | -- | no | yes | `ttcap capture --mode` |
| `vga-mode` | `""` | -- | `ttcap`'s default (640x480@60) | no | yes | VGA timing |
| `window-lines` | `0` | lines | `ttcap`'s default | no | yes | most lines per captured frame |

`ttcap-command` is split with shell quoting rules and run with the
pipeline's own working directory, which is what `uv run` needs to find the
project; give an absolute `ttcap` (or an absolute interpreter and script)
when the pipeline runs from elsewhere.

### Stopping matters

On stop the element asks `ttcap` to finish its chunk and closing `TIME`
chunk, waiting up to `stop-timeout` before closing the pipe and then
killing the process group
([how long a stop takes](../python/ttcap/README.md#stopping)).

### A live capture cannot be paused, only stopped

The board samples in real time, and the element's pipe is the only buffer
between it and the pipeline. A PAUSED pipeline therefore gives the capture
exactly one pipe buffer of grace (measured at 65,548 bytes: about 87 ms on an
RP2350 at 750 kHz and about 0.55 s on an RP2040 at 60 kHz). After that the
DMA buffers overrun and samples are lost. Worse, the overrun count lives in
the closing `TIME` chunk, which the stop sequence drains and discards, so a
capture that was paused and then stopped is silently short, with nothing on
the bus to say so. Go straight to PLAYING and stop when finished; if
something must pause, treat what comes after as a different capture.

### Errors

A child that exits non-zero raises a pipeline `ERROR` quoting the last lines
of its stderr, so a capture that fails on the board is an error and not a
silent end of stream. The rest of its stderr is logged at `INFO` under the
`vgacapttsrc` debug category.

## `vgacapbin`: a URI in, video out

```sh
gst-launch-1.0 vgacapbin 'uri="tt-serial:///dev/ttyACM0?project=tt_um_x&clock-hz=60000"' ! ...
gst-launch-1.0 vgacapbin 'uri="tt-ws://127.0.0.1:18034/serial?clock-hz=60000"' ! ...   # or tt-wss://
gst-launch-1.0 vgacapbin 'uri="file:///captures/tt08.vgacap"' ! ...
```

**Quote the URI twice**, as in these examples. The outer quotes are for the
shell, which would otherwise background the command at the `&` and glob the
`?`. The inner ones are for GStreamer's own pipeline parser; they are needed
when the line is handed to `gst_parse_launch()` as a single string (from
Python, say) rather than as an already-split argument vector. An unquoted
URI usually fails before the element ever sees it.

The query string sets the source's properties by name, and the source's
properties are mirrored on the bin, so `?seconds=10` and `seconds=10` do the
same thing (the query wins if both are given). `tt-ws://host:8765` with no
path means the bridge's `/serial` endpoint.

A query may only set the properties marked "in a URI query" in the
[`vgacapttsrc` table](#vgacapttsrc-capturing-from-a-board). Naming anything
else is an error, not a silent no-op. That matters most for
`ttcap-command`, which is a program to run, and `link`, which would move the
capture somewhere other than where the URI says. A URI can arrive from
somewhere that is not a trusted shell, so those stay settable only as
element properties, by whoever builds the pipeline.
