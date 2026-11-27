---
type: how-to
owner: vgacap maintainers
reader: someone with a .vgacap capture who wants to look at its frames
review: 2027-05-27
---

# How to turn a capture into PNG images

**You have a `.vgacap` capture and want each frame in it as a PNG file.**

`ttcap png` runs the C tool `vgacap-frames`, which rebuilds each frame from the capture's sync signals, then converts its PPM files to PNG with [Pillow](https://github.com/python-pillow/Pillow). It works on a capture from any source. It does not touch a board.

## What you need

- The C tools built into `build/`, as in [How to build vgacap and run its tests](../../docs/development.md), or `vgacap-frames` somewhere else.
- Pillow, which `uv sync` installs; on a checkout synced with `UV_NO_DEV=1`, run `uv sync --extra synth` first.

## Steps

1. In the checkout, make a directory for the frames with `mkdir -p out`, which holds them apart from other files.
2. In the checkout, render the capture, which writes `out/frame-0000.png` and one more for each frame:

   ```sh
   uv run ttcap png capture.vgacap out/frame
   ```

`ttcap png` finds `vgacap-frames` from `--vgacap-frames PATH` first, then `build/` in this checkout, then `$VGACAP_FRAMES`, then `PATH`. The PPM files `vgacap-frames` wrote stay beside the PNGs. A frame appears only once the boundary after it has been captured, so a capture needs three frame periods for its first frame.

## Check

Step 2 lists each frame with its mode and sync polarities, then the count written. For a capture of `tt_um_rejunity_vga` on the TT07 chip it printed this:

```text
frame 0: 640x480 mode=640x480@60 cpl=800 lpf=525 hsync=pos vsync=pos partial=0 glitches=0
frame 1: 640x480 mode=640x480@60 cpl=800 lpf=525 hsync=pos vsync=pos partial=0 glitches=0
frames=2 glitches_total=0
wrote 2 png(s)
```

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `png failed: cannot find vgacap-frames` | the C tools are not built | run `cmake --build build` or pass `--vgacap-frames` |
| 2 | `png failed: ... exited 2:` | `vgacap-frames` decoded no whole frame | capture at least three frame periods |
| 2 | `png failed: Pillow is needed to write PNGs` | Pillow is not installed | run `uv sync --extra synth` |

After `exited 2:` comes what `vgacap-frames` printed on its error output, or `no frame decoded` when it printed nothing.

## Next

- [How to make a video of a design with ttcap demo](how-to-make-video.md)
- [The vgacap command-line tools](../../src/tools/README.md)
