---
type: how-to
owner: vgacap maintainers
reader: someone with a demo board on USB who wants its design's picture live in a GStreamer pipeline
review: 2027-05-27
---

# How to capture live in GStreamer

**You have a demo board with the stock firmware, on USB or on the bench through the bridge, and want its design's picture live.**

This page uses `vgacapbin`. It builds a `vgacapttsrc` from a URI, and puts `vgadecode` behind it. The capture runs at a clock the USB link can carry, so the design runs slower than real time. How the element runs and stops `ttcap` is on [How vgacapttsrc runs and stops a capture](explanation-live-source.md).

## What you need

- The plugin built in `build/`, as [How to build the GStreamer plugin](how-to-build-plugin.md) shows, and `uv run ttcap --help` working in the vgacap checkout.
- A demo board with an RP2350 on this machine's `/dev/ttyACM0`, with nothing else holding the port.
- Or a bench board, reached through the fpgas.online bridge (`fpgas-tt`) as [How to capture from a Welland board through the bridge](../python/ttcap/how-to-capture-welland-bridge.md) shows.
- The name of a VGA design on it: a `tt_um_` project on an ASIC breakout, or a bitstream on an FPGA breakout.

## Steps

1. In the vgacap checkout, run `export GST_PLUGIN_PATH=$PWD/build`, so that GStreamer finds the vgacap elements in this shell.
2. In the same directory, run `gst-launch-1.0 vgacapbin 'uri="tt-serial:///dev/ttyACM0?project=tt_um_x&clock-hz=500000&seconds=10"' ! videoconvert ! autovideosink`, and a window shows the picture.
3. In the same directory, for a bench board, run step 2 with `tt-ws://127.0.0.1:PORT/serial` in place of `tt-serial:///dev/ttyACM0`, which captures through the tunnel on PORT.
4. In that directory, for a demo board with an FPGA breakout, run step 2 with `design=` in place of `project=`, which selects the design.
5. In the same directory, to record instead, run step 2 with `-e` after `gst-launch-1.0`, and `! videoconvert ! video/x-raw,format=I420 ! x264enc ! matroskamux ! filesink location=live.mkv` as its end, which writes `live.mkv`.

Quote the URI twice, as step 2 does: the shell would otherwise stop at `&`.

## Check

A window opens after a few seconds with the design's picture, and closes when the 10 seconds are up. The pipeline ends with `Got EOS from element "pipeline0".` and exits 0. In step 5, `live.mkv` plays at about 1.19 frames a second, the rate 500 kHz gives 640x480@60. A [bench run on fpga-1](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-fpga1) recorded 10 frames at 640x480 and 25/21 frames a second.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `no element "vgacapbin"` | `GST_PLUGIN_PATH` is not set in this shell | run step 1 |
| 2 | an `ERROR` quoting `ttcap`'s last lines | the board is not on that port | give the port the board is on |
| 2 | an `ERROR` quoting `ttcap`'s last lines | another program holds the port | stop that program |
| 2 | an `ERROR` quoting `ttcap`'s last lines | the project name is wrong | give the project's exact `tt_um_` name |
| 2 | `the uri's query string sets 'link', which a uri may not set` | a key the URI may not set | set it as an element property |
| 2 | `tt-serial takes a device path, so write tt-serial:///dev/ttyACM0 with three slashes` | two slashes after `tt-serial:` | write three |
| 2 | the command runs in the background | the URI was not quoted for the shell | quote it twice, as step 2 does |
| 4 | `is not a shuttle entry on this board and is not the enabled design either` | the FPGA bitstream is not enabled on the board | on the board's Pi, run `curl -s -X POST localhost:8765/designs/<name>/enable` ([How to capture from a Welland board through the bridge](../python/ttcap/how-to-capture-welland-bridge.md)), then step 4 again |

## Next

- [How to decode a capture to video with GStreamer](how-to-decode-to-video.md)
- [The vgacap GStreamer elements](reference-elements.md)
- [ttcap](../python/ttcap/README.md)
