---
type: how-to
owner: vgacap maintainers
reader: someone with a demo board on USB who wants its design's picture live in a GStreamer pipeline
review: 2027-05-27
---

# How to capture live in GStreamer

**You have a demo board on a USB port, running the stock firmware, and want its VGA design's picture on screen or in a video file as it runs.**

This page uses `vgacapbin`, which builds a `vgacapttsrc` from a URI and puts `vgadecode` behind it. The capture runs at a clock the USB link can carry, so the design runs slower than real time. How the element runs and stops `ttcap` is on [How vgacapttsrc runs and stops a capture](explanation-live-source.md).

## What you need

- The plugin built in `build/`, as [How to build the GStreamer plugin](how-to-build-plugin.md) shows, and `uv run ttcap --help` working in the vgacap checkout.
- A demo board with an RP2350, on `/dev/ttyACM0`, with nothing else holding the port.
- The name of a VGA design on it: a `tt_um_` project on an ASIC breakout, or a bitstream on an FPGA breakout.

## Steps

1. In the vgacap checkout, run `export GST_PLUGIN_PATH=$PWD/build`, so that GStreamer finds the vgacap elements in this shell.
2. In the same directory, run `gst-launch-1.0 vgacapbin 'uri="tt-serial:///dev/ttyACM0?project=tt_um_x&clock-hz=500000&seconds=10"' ! videoconvert ! autovideosink`, and a window shows the picture.
3. On a demo board with an FPGA breakout, run step 2 with `design=` in place of `project=` in the URI.
4. To record instead, run step 2 with `-e` after `gst-launch-1.0`, and `! videoconvert ! video/x-raw,format=I420 ! x264enc ! matroskamux ! filesink location=live.mkv` as its end.

Quote the URI twice, as step 2 does: the shell would otherwise stop at `&`.

## Check

A window opens after a few seconds with the design's picture, and closes when the 10 seconds are up. The pipeline ends with `Got EOS from element "pipeline0".` and exits 0. In step 4, `live.mkv` plays at about 1.19 frames a second, the rate 500 kHz gives 640x480@60.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `no element "vgacapbin"` | `GST_PLUGIN_PATH` is not set in this shell | run step 1 |
| 2 | an `ERROR` quoting `ttcap`'s last lines | the board is not on that port, the port is held, or the project name is wrong | read the quoted lines; free the port or fix the name |
| 2 | `the uri's query string sets 'link', which a uri may not set` | a key the URI may not set | set it as an element property, or leave it out |
| 2 | `tt-serial takes a device path, so write tt-serial:///dev/ttyACM0 with three slashes` | two slashes after `tt-serial:` | write three |
| 2 | the command runs in the background, or `no matches found` | the URI was not quoted for the shell | quote it twice, as step 2 does |

## Next

- [How to decode a capture to video with GStreamer](how-to-decode-to-video.md)
- [The vgacap GStreamer elements](reference-elements.md)
- [ttcap](../python/ttcap/README.md)
