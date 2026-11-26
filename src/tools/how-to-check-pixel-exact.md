---
type: how-to
owner: vgacap maintainers
reader: someone with a capture of a calibration design who wants to know it is exactly right
review: 2027-05-27
---

# How to check a capture pixel-exact against a reference render

**You have a capture of one of the calibration designs and want to know that every pixel of it is right.**

The calibration designs in [tt-vga-testpatterns](https://github.com/mithro/tt-vga-testpatterns) each come with a renderer that draws what the design should show. This page compares one rebuilt frame with that render. A design that is not a calibration design has no render to compare against.

## What you need

- A vgacap checkout built with CMake, and a checkout of tt-vga-testpatterns beside it, in `../tt-vga-testpatterns`.
- A capture of a calibration design (`tt_um_vgacal_bars`, `_counter`, `_grid`, `_modes` or `_prbs`) of at least three frame periods. A simulated one comes from tt-vga-testpatterns' `tools/dump_uo_out.py`, wrapped by [`vgacap-bin2stream`](reference-options.md#vgacap-bin2stream).
- For `tt_um_vgacal_modes`, the `ui_in` value it ran with.

## Steps

1. In the vgacap checkout, run `mkdir -p frames && build/vgacap-frames capture.vgacap frames/frame`, which writes the frames as `frames/frame-NNNN.ppm`.
2. In `../tt-vga-testpatterns`, run `uv run --no-project --with numpy --with pillow python tools/compare_capture.py tt_um_vgacal_bars ../vgacap/frames/frame-0001.ppm`, and it compares the frame with the render.
3. For `tt_um_vgacal_modes`, run step 2 with its design name and `--ui-in N` added, N being the `ui_in` it ran with.

Compare a frame after the first: the first frame of a board capture is the likeliest to hold whatever the board was doing as the sampler started.

## Check

The comparison prints `PASS` and exits 0:

```text
tt_um_vgacal_bars: captured 640x480, reference 640x480 (fixed picture)
PASS: 307200 pixels identical to the reference render
```

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `FAIL: N mismatching pixels of 307200`, then up to ten pixels with both colours | the capture differs from the design's output | look at the listed pixels, and capture again |
| 2 | `FAIL: size mismatch` | the frame was cropped to another mode, or is partial | check the `mode=` on the frame's line from step 1 |
| 2 | `no reference renderer` | the design is not a calibration design | use a calibration design |
| 2 | `not a binary PPM` | the path is not a frame from step 1 | give the path of a `.ppm` that step 1 wrote |

## Next

- [How to turn a capture into frames](how-to-capture-to-frames.md)
- [How libvgaframe rebuilds frames](../frame/explanation-rebuilding-frames.md)
- [The calibration designs](https://github.com/mithro/tt-vga-testpatterns)
