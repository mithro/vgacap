---
type: how-to
owner: vgacap maintainers
reader: someone with a demo board who wants a video file of their design
review: 2027-05-27
---

# How to make a video of a design with ttcap demo

**You want a video file and a set of PNG frames of your design, from one run against a demo board.**

`ttcap demo` runs a [GStreamer](https://gstreamer.freedesktop.org) pipeline: the vgacap plugin captures from the board and rebuilds frames, and one capture feeds every output. By default it writes `frame-%04d.png` files and `capture.mkv` into `--outdir`. Showing the capture live is on [How to watch a capture live in a window or a browser](how-to-watch-live.md).

## What you need

- A capture that works, as in [How to capture a stream from a demo board](how-to-capture-stream.md).
- GStreamer's tools (`gstreamer1.0-tools` on Debian) with `gstreamer1.0-plugins-good`, and the vgacap plugin built into `build/` ([Developing vgacap](../../docs/development.md)).
- A video encoder: `x264enc` from gst-plugins-ugly, or `vp8enc` from gst-plugins-good.

## Steps

1. In the checkout, point GStreamer at the plugin with `export GST_PLUGIN_PATH=$PWD/build`, so the demo finds `vgacapttsrc` and `vgadecode`.
2. In the checkout, run the demo with a new output directory, which captures for `--seconds` and writes the frames and the video there:

   ```sh
   uv run ttcap demo --link serial:/dev/ttyACM0 --project tt_um_rejunity_vga \
       --clock-hz 500000 --seconds 12 --fps 5 --outdir video
   ```

3. In the terminal, wait for the summary, which counts the PNGs and gives the size of `capture.mkv`.

Without `--fps`, each frame keeps the design's own time, which at a 60 kHz clock is one frame every seven seconds. `--fps 5` repeats the last frame to fill the gaps, so the video plays at wall-clock speed; it takes `30`, `30/1` or `29.97`. Ctrl-C ends the run early and still finishes the video file, after one DMA buffer for the board to stop. `--no-png` and `--no-video` switch off either output, and `--video-encoder` picks the encoder.

## Check

The demo prints the detected mode, the frames as they are written, and a summary. From an FPGA breakout running `tt_um_vgacal_bars` ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-demo-fpga1)):

```text
  mode 640x480@60: 800 clocks/line, 525 lines/frame, hsync negative, vsync negative, glitches 0
  1 frame(s), 3s elapsed
  48 frame(s), 13s elapsed

capture finished after 13.9s (gst-launch exit 0)
  51 png(s) in out
  out/capture.mkv, 19.8 KiB
```

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `GStreamer has no vgacapttsrc` | `GST_PLUGIN_PATH` does not name the plugin | repeat step 1 in this shell |
| 2 | `no video encoder` | neither encoder is installed | install one or pass `--no-video` |
| 2 | `already holds N file(s) from a previous run` | the directory holds an earlier run | pass `--force` or a new `--outdir` |
| 2 | `warning: --clock-hz N is above` | the clock is above the measured ceiling | lower `--clock-hz`, or accept overruns |
| 3 | `nothing was captured`, exit 3 | no frame closed before the end | raise `--seconds` to three frame periods or more |

`--force` deletes only the earlier run's `frame-*.png` files and `capture.mkv`, and names them. The clock warning appears with `--board`, where the demo knows the board: the ceilings are 60 kHz on an RP2040 demo board and 750 kHz on an RP2350 one. Every message is on [ttcap exit codes and messages](reference-exit-codes.md#demo).

## Next

- [How to watch a capture live in a window or a browser](how-to-watch-live.md)
- [How to print the demo's GStreamer pipeline](how-to-print-pipeline.md)
- [The GStreamer plugin](../../gst/README.md)
