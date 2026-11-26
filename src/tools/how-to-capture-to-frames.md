---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap capture file who wants its frames as pictures
review: 2027-05-27
---

# How to turn a capture into frames

**You have a capture file, a vgacap stream, and want each frame of it as a picture.**

This page runs `vgacap-frames`, which writes one PPM image a frame. It works on a capture from any source: `ttcap capture`, the Raspberry Pi 5, an FPGA or `vgacap-bin2stream`. A simulation trace is turned into frames by `vgacap-sample` instead ([options](reference-options.md#vgacap-sample)).

## What you need

- A vgacap checkout built with CMake, so that `build/vgacap-frames` exists ([Developing vgacap](../../docs/development.md)).
- A capture of at least three frame periods: 1,260,000 clocks of a 640x480@60 design. Why it takes that many is on [How libvgaframe rebuilds frames](../frame/explanation-rebuilding-frames.md#when-the-first-frame-appears).

## Steps

1. In the vgacap checkout, run `mkdir -p frames`, which makes the directory the frames go into.
2. In the same directory, run `build/vgacap-frames capture.vgacap frames/frame`, and it writes `frames/frame-0000.ppm` onwards, one line a frame.
3. To keep frames whose lines were not all covered as well, run step 2 again with `--partial` added.

## Check

Each frame written prints one line, and the last line counts them:

```text
frame 0: 640x480 mode=640x480@60 cpl=800 lpf=525 hsync=neg vsync=neg partial=0 glitches=0
frame 1: 640x480 mode=640x480@60 cpl=800 lpf=525 hsync=neg vsync=neg partial=0 glitches=0
frames=2 glitches_total=0
```

The exit code is 0, and `frames/` holds one `.ppm` for each `frame` line. Every output line is listed on [the tool reference](reference-options.md#vgacap-frames).

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `frames=0` and exit code 2 | the capture is under two frame periods, or holds no sync | capture for longer; run `build/vgacap-dump capture.vgacap` to see what it holds |
| 2 | `a simulation trace (samples_per_clock 2) is read with vgacap-sample` | the file is a simulation trace | run `vgacap-sample` on it instead |
| 2 | `failed to write frames/frame-0000.ppm` | the directory does not exist | run step 1 |
| 2 | `mode=?` on the frame lines | the timing matches no mode in [the mode table](../frame/reference-modes.md) | read the cropped frames as they are, or look at `cpl` and `lpf` |
| 2 | `overrun after sample S` on stderr | the board's buffers overran; that frame was dropped | capture at a lower clock |
| 2 | `resync skipped=N` on stderr | bytes of the capture were lost on the way | capture again over a better link |

## Next

- [How to check a capture pixel-exact against a reference render](how-to-check-pixel-exact.md)
- [How to decode a capture to video with GStreamer](../../gst/how-to-decode-to-video.md)
- [How libvgaframe rebuilds frames](../frame/explanation-rebuilding-frames.md)
