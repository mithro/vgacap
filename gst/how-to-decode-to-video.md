---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap capture file who wants it as a video or as pictures
review: 2027-05-27
---

# How to decode a capture to video with GStreamer

**You have a capture file and want it as a video file, or as one PNG a frame, through GStreamer.**

This page runs `vgadecode` in a `gst-launch-1.0` pipeline. The frames are the ones [`vgacap-frames`](../src/tools/how-to-capture-to-frames.md) rebuilds, with a timestamp each. Every property is on [The vgacap GStreamer elements](reference-elements.md).

## What you need

- The plugin built in `build/`, as [How to build the GStreamer plugin](how-to-build-plugin.md) shows.
- GStreamer's good and ugly plugin sets, for `matroskamux`, `pngenc` and `x264enc`.
- A capture of at least three frame periods.

## Steps

1. In the vgacap checkout, run `export GST_PLUGIN_PATH=$PWD/build`, so that GStreamer finds the vgacap elements in this shell.
2. In the same shell, run `gst-launch-1.0 filesrc location=capture.vgacap ! vgadecode ! videoconvert ! video/x-raw,format=I420 ! x264enc ! matroskamux ! filesink location=capture.mkv`, which writes the video.
3. For one PNG a frame instead, run `mkdir -p png && gst-launch-1.0 filesrc location=capture.vgacap ! vgadecode ! pngenc ! multifilesink location=png/frame-%04d.png`.

## Check

Each pipeline ends with `Got EOS from element "pipeline0".` and exits 0. `capture.mkv` plays at the design's own frame rate, 59.94 frames a second for 640x480@60 at 25.175 MHz. `png/` holds one PNG for each frame `vgacap-frames` would write.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `erroneous pipeline: no element "vgadecode"` | `GST_PLUGIN_PATH` is not set in this shell, or the plugin was not built | run step 1; build the plugin |
| 2 | `erroneous pipeline: no element "x264enc"` | GStreamer's ugly plugin set is missing | install it, or use `vp8enc ! webmmux` and a `.webm` file |
| 2 | the pipeline ends at once and the file is a few hundred bytes | the capture holds no complete frame | capture for longer |
| 2 | `unknown force-mode` | a `force-mode` that is not in [the mode table](../src/frame/reference-modes.md) | give a mode's exact name |
| 2 | the video plays fast or slow | the capture's header gives no clock, so frames go out at `output-fps` | set `vgadecode output-fps=60/1`, or capture with the clock set |

## Next

- [How to capture live in GStreamer](how-to-capture-live.md)
- [The vgacap GStreamer elements](reference-elements.md)
- [How libvgaframe rebuilds frames](../src/frame/explanation-rebuilding-frames.md)
