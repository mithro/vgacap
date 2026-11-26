---
type: how-to
owner: vgacap maintainers
reader: someone who wants to know a capture of a calibration design is exactly right
review: 2027-05-27
---

# How to check a capture pixel-exact against a reference render

**You want to know that every pixel of a calibration design's capture is right.**

The calibration designs in [tt-vga-testpatterns](https://github.com/mithro/tt-vga-testpatterns) each come with a renderer that draws what the design should show. This page simulates `tt_um_vgacal_bars`, rebuilds a frame, and compares it with that render. The simulation is tt-vga-testpatterns' [`tools/dump_uo_out.py`](https://github.com/mithro/tt-vga-testpatterns/blob/main/tools/dump_uo_out.py), and the comparison its [`tools/compare_capture.py`](https://github.com/mithro/tt-vga-testpatterns/blob/main/tools/compare_capture.py). With a capture from a board, start at step 3.

## What you need

- A vgacap checkout built with [CMake](https://cmake.org), and a checkout of tt-vga-testpatterns beside it, in `../tt-vga-testpatterns`.
- [Icarus Verilog](https://github.com/steveicarus/iverilog) (`iverilog` and `vvp`) on the path, for the simulation.
- For a board capture instead: a capture of a calibration design (`tt_um_vgacal_bars`, `_counter`, `_grid`, `_modes` or `_prbs`) of at least three frame periods.

## Steps

1. In `../tt-vga-testpatterns`, run `uv run --no-project tools/dump_uo_out.py --top tt_um_vgacal_bars --out tmp/bars.bin --clocks 1260000 designs/tt_um_vgacal_bars/src/project.v designs/tt_um_vgacal_bars/src/hvsync_generator.v`, which writes three frames of `uo_out`.
2. In the vgacap checkout, run `uv run vgacap-bin2stream ../tt-vga-testpatterns/tmp/bars.bin capture.vgacap --clock-hz 25175000`, which writes the capture.
3. In the vgacap checkout, run `mkdir -p frames && build/vgacap-frames capture.vgacap frames/frame`, which writes the frames as `frames/frame-NNNN.ppm`.
4. In `../tt-vga-testpatterns`, run `uv run --no-project --with numpy --with pillow python tools/compare_capture.py tt_um_vgacal_bars ../vgacap/frames/frame-0001.ppm`, and it compares the frame with the render.
5. In `../tt-vga-testpatterns`, for `tt_um_vgacal_modes`, run step 4's command with `--ui-in N` added, and it compares against the render for that `ui_in`.

## Check

The comparison prints `PASS` and exits 0:

```text
tt_um_vgacal_bars: captured 640x480, reference 640x480 (fixed picture)
PASS: 307200 pixels identical to the reference render
```

Compare a frame after the first. The first frame of a board capture is the likeliest to hold what the board did as the sampler started.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `iverilog/vvp not on PATH` | Icarus Verilog is not installed | install it |
| 4 | `FAIL: N mismatching pixels of 307200`, then up to ten pixels with both colours | the capture differs from the design's output | look at the listed pixels, and capture again |
| 4 | `FAIL: size mismatch` | the frame was cropped to another mode | check the `mode=` on the frame's line from step 3 |
| 4 | `no reference renderer` | the design is not a calibration design | use a calibration design |
| 4 | `not a binary PPM` | the path is not a frame from step 3 | give the path of a `.ppm` that step 3 wrote |

## Next

- [How to turn a capture into frames](how-to-capture-to-frames.md)
- [How libvgaframe rebuilds frames](../frame/explanation-rebuilding-frames.md)
- [The calibration designs](https://github.com/mithro/tt-vga-testpatterns)
