---
type: how-to
owner: vgacap maintainers
reader: someone with an RP2350 demo board whose design must run at its real VGA clock
review: 2027-05-27
---

# How to capture whole frames at the design's own clock

**You want pictures of your design running at its real pixel clock, such as 25.175 MHz, which no USB link can stream sample by sample.**

`ttcap capture --mode frame` has the board capture one whole frame into its memory and send it while the frames after it pass. Each picture in the file is one captured frame when the board's heap holds the whole picture. To choose how many lines one capture takes, see [How to capture a window of lines from each frame](how-to-capture-window.md).

## What you need

- An RP2350 demo board running the stock firmware, set up as in [How to capture a stream from a demo board](how-to-capture-stream.md). On an RP2040 demo board, frame capture has run only in the emulator ([#37](https://github.com/mithro/vgacap/issues/37)).
- A design whose timing is one of the base modes `--vga-mode` lists on [ttcap commands and options](reference-commands.md).
- The design's pixel clock, which is the clock `--clock-hz` sets.

## Steps

1. In the checkout, run the frame capture with the design's mode and clock, which writes the pictures to the file:

   ```sh
   uv run ttcap capture serial:/dev/ttyACM0 --project tt_um_rejunity_vga_logo \
       --mode frame --vga-mode 640x480@60 --clock-hz 25175000 --frames 2 --out frames.vgacap
   ```

2. In the terminal, read the stats line, which shows `overruns=0 dropped=0 rxstall=0` when every picture is whole.
3. In the checkout, run `mkdir -p out && uv run ttcap png frames.vgacap out/frame`, which writes one PNG for each picture.

`--frames N` stops after N pictures. `--vga-mode` is `640x480@60` unless given. The board measures the sync polarities itself, and samples on the rising clock edge unless `--edge` and `--sample-delay` move the sample point.

## Check

Step 3 lists each picture with `partial=0`, and `out/frame-0000.png` shows the design. When the heap holds fewer lines than the picture, `ttcap` adds a note after the stats. On the tt09 board, the command in step 1 printed this ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)):

```text
note: each picture is assembled from 2 captured frames (410 of its 481 lines per frame): exact for a static design, torn for one that changes from frame to frame
```

Each picture then joins two captured frames where the first one's window ends. `tt_um_rejunity_vga_logo` dithers every frame, so its picture changes below about line 410. A still picture comes out clean: on an FPGA breakout, `tt_um_vgacal_bars` at 25.175 MHz gave 2 pictures from 478-line windows, each `partial=0` ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)).

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `argument --vga-mode: invalid choice` | the mode is not a base mode | stream the design instead, which detects any mode |
| 1 | `board error: error=project clock is not 2..32 whole system clocks` | the board cannot make the clock as a whole number of system clocks | use the clock the design was made for |
| 1 | `board error: error=not one line of heap` | the board has too little free memory | reset the board |
| 2 | `rxstall=1` | samples were lost inside a capture, so its picture is wrong | capture again |
| 3 | stray pixels at colour edges | the sample point is where `uo_out` changes | try `--edge falling --sample-delay 1` |

The sample point was swept on an FPGA breakout at 25 MHz. The rising edge with no delay was exact, and so was the falling edge with one cycle of delay ([sweep](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-frame-capture-fpga4/README.md#what-it-took-in-the-order-the-board-taught-it)).

## Next

- [How to capture a window of lines from each frame](how-to-capture-window.md)
- [How to turn a capture into PNG images](how-to-render-png.md)
- [Capture on the demo board](explanation-demo-board-capture.md)
