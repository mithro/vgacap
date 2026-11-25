# GStreamer plugin

`gst/` builds `libgstvgacap.so`, a GStreamer plugin that decodes a vgacap
stream to video, captures live from a Tiny Tapeout demo board, or does both
from a URI. It has three elements:

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

# ...or straight off a board, through either link. The URI is quoted twice:
# once for the shell, once for GStreamer's pipeline parser (see "Quote the
# URI twice" under vgacapbin).
gst-launch-1.0 vgacapbin \
    'uri="tt-ws://127.0.0.1:18007/serial?project=tt_um_rejunity_vga&clock-hz=60000"' ! \
    videoconvert ! autovideosink
```

`127.0.0.1:18007` is an SSH tunnel to tt07
([Reaching a Welland board](../python/ttcap/README.md#reaching-a-welland-board)).

## `vgadecode`: a stream in, video out

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

## `vgacapttsrc`: capturing from a board

`vgacapttsrc` runs `ttcap capture --out -` as a child process and pushes its
stdout as 64 KiB `application/x-vgacap` buffers. The board protocol stays
in Python, and the same element works over USB serial or over the
fpgas.online websocket bridge.

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

### Stopping matters

The board samples into a DMA double buffer and can only stop at a buffer
boundary: about 82 ms at 750 kHz, but 2.2 s at 60 kHz, the RP2040's
measured project-clock ceiling at the default buffer size (a larger
`--buf-words` raises it). `ttcap` turns its first `SIGINT` into a cooperative
stop that finishes the chunk, writes its closing `TIME` chunk (the only
overrun and `rxstall` report there is) and exits 0.

So the element signals the child's process group and then waits *while
draining its stdout*, because a child blocked writing into a full pipe never
reaches the code that emits its trailer. Only when `stop-timeout` runs out
does the element close the read end (`ttcap` reads the `EPIPE` as a clean
end and still exits 0, but the trailer then has nowhere to go), and only
after that does it send `SIGKILL`. Every path reaps the child.

The default command makes `ttcap` a *grandchild*, with `uv run` in between.
If the wrapper exits first, a `SIGKILL` also goes to the process group;
otherwise the capture would carry on holding the board with nothing left to
signal. No wait in the sequence is unbounded: it all runs inside a state
change, and a state change that never returns is a pipeline nobody can shut
down.

### A live capture cannot be paused, only stopped

The board samples in real time, and the element's pipe is the only buffer
between it and the pipeline. A PAUSED pipeline therefore gives the capture
exactly one pipe buffer of grace (measured at 65,548 bytes: about 90 ms at
750 kHz and about 1.1 s at 60 kHz). After that the DMA buffers overrun and
samples are lost. Worse, the overrun count lives in the closing `TIME`
chunk, which the stop sequence drains and discards, so a capture that was
paused and then stopped is silently short, with nothing on the bus to say
so. Go straight to PLAYING and stop when finished; if something must pause,
treat what comes after as a different capture.

### Errors

A child that exits non-zero raises a pipeline `ERROR` quoting the last lines
of its stderr, so a capture that fails on the board is an error and not a
silent end of stream. The rest of its stderr is logged at `INFO` under the
`vgacapttsrc` debug category.

## `vgacapbin`: a URI in, video out

```sh
gst-launch-1.0 vgacapbin 'uri="tt-serial:///dev/ttyACM0?project=tt_um_x&clock-hz=60000"' ! ...
gst-launch-1.0 vgacapbin 'uri="tt-ws://127.0.0.1:18007/serial?clock-hz=60000"' ! ...   # or tt-wss://
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

A query may only carry the **capture parameters**: `project`, `design`,
`clock-hz`, `profile`, `pio`, `buf-words`, `seconds`, `stop-timeout`.
Naming anything else is an error, not a silent no-op. That matters most for
`ttcap-command`, which is a program to run, and `link`, which would move the
capture somewhere other than where the URI says. A URI can arrive from
somewhere that is not a trusted shell, so everything outside that list
stays settable only as an element property, by whoever builds the pipeline.
