---
type: tutorial
owner: vgacap maintainers
reader: someone with an RP2040 Tiny Tapeout demo board on USB and a VGA design on its chip
review: 2027-05-27
---

# Your first capture from a demo board

**This tutorial takes you from a demo board on your desk to one PNG of the picture its design draws.**

It follows one board: an RP2040 demo board running the stock firmware, with a Tiny Tapeout chip whose design drives the [Tiny VGA Pmod](https://github.com/mole99/tiny-vga). The board is plugged into a Linux computer by USB. Other boards, modes and the Welland bench are on the pages under [Next](#next).

![A red and pink checkerboard of nested squares, 640 by 480 pixels, captured from tt_um_rejunity_vga on a Tiny Tapeout 7 chip](images/tt07-rejunity-vga.png)

At the end you will have a frame like this: `tt_um_rejunity_vga` on a Tiny Tapeout 7 chip, from a capture like the one below ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-ttcap-capture-serial-tt07)).

## Before you start

You need [Git](https://git-scm.com), [CMake](https://cmake.org) with a C compiler, and [uv](https://docs.astral.sh/uv/). Your user needs to open the board's serial port, which on Debian means being in the `dialout` group. You need your design's project name, which starts `tt_um_`.

## 1. Get vgacap and build its tools

Clone the repository and build the C tools, which turn a capture into frames:

```sh
git clone https://github.com/mithro/vgacap.git
cd vgacap
cmake -S . -B build && cmake --build build
uv sync
```

The build ends with `[100%] Built target` lines, and `build/vgacap-frames` now exists. `uv sync` installs `ttcap` and its Python packages into `.venv`.

## 2. Find the board

Plug the demo board in by USB. It appears as a serial device, `/dev/ttyACM0` when it is the only one. Ask it what it is:

```sh
uv run ttcap probe serial:/dev/ttyACM0
```

It answers with two lines, its firmware's version and its pin map. The tt07 board answered like this ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)):

```text
sys.version: 3.4.0; MicroPython v1.24.0 on 2024-10-25
GPIOMap.all(): {'uio1': 22, 'uio5': 26, ... 'rp_projclk': 0, ... 'uo_out0': 5, ...}
```

`'rp_projclk': 0` and `'uo_out0': 5` mark an RP2040 demo board. `ttcap` reads the same map during a capture to pick the board's pin layout. A `probe failed:` line means the port is wrong or busy; [How to probe a demo board](how-to-probe-board.md) lists the causes.

## 3. Capture two frames

Run the capture, putting your design's name after `--project`:

```sh
uv run ttcap capture serial:/dev/ttyACM0 --project tt_um_rejunity_vga \
    --clock-hz 60000 --frames 2 --seconds 0 --out capture.vgacap
```

`ttcap` enables the design, clocks it at 60 kHz, and records its `uo_out` pins on every clock. 60 kHz is the clock an RP2040 demo board streams without loss. Two frames take four frame periods of samples, about 28 seconds at that clock. The run prints these lines, with its own counts:

```text
profile=rp2040-tt06map project=tt_um_rejunity_vga enable=index
bytes=... samples=... chunks=... seconds=... overruns=0 dropped=0 rxstall=0 ...
  board: overruns=0 rxstall=0 sysclk_hz=132000000
wrote capture.vgacap
```

`overruns=0`, `dropped=0` and `rxstall=0` say no sample was lost. The same command on the same board was clean again on 2026-11-26, after a restart ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)).

## 4. Turn the capture into a PNG

Rebuild the frames from the capture and write them as PNGs:

```sh
mkdir -p out
uv run ttcap png capture.vgacap out/frame
```

Each frame found is listed, then the count written. On the tt07 board it printed this ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)):

```text
frame 0: 640x480 mode=640x480@60 cpl=800 lpf=525 hsync=pos vsync=pos partial=0 glitches=0
frame 1: 640x480 mode=640x480@60 cpl=800 lpf=525 hsync=pos vsync=pos partial=0 glitches=0
frame 2: 640x480 mode=640x480@60 cpl=800 lpf=525 hsync=pos vsync=pos partial=0 glitches=0
frames=3 glitches_total=0
wrote 3 png(s)
```

Open `out/frame-0000.png`. It is the picture your design drew, rebuilt from its sync signals and its six colour bits. The first frame of a capture is never whole, so a two-frame capture gives one to three PNGs.

`glitches=` counts the sync pulses the rebuild rejected as too short since the previous frame, and `glitches_total` counts them over the whole capture. A real chip can put out such pulses: the capture pictured above had `glitches_total=10`. The picture is still rebuilt whole ([Glitch pulses](../../src/frame/explanation-rebuilding-frames.md#glitch-pulses)). `partial=0` says every line of the frame was captured.

## If a step fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `probe failed:` | the port is wrong or busy | see [How to probe a demo board](how-to-probe-board.md) |
| 3 | `board error: MemoryError`, with `bytes=0` | the firmware's heap is too full for the capture buffers | restart the SDK, then repeat step 3 |
| 4 | `png failed:` | the capture holds no whole frame | see [How to turn a capture into PNG images](how-to-render-png.md) |

To restart the SDK, run `uv run python tools/board_restart.py serial:/dev/ttyACM0`, or press Ctrl-C, Ctrl-B and Ctrl-D in a serial terminal. A failed capture still prints `wrote capture.vgacap`, but exits 1 and holds no samples. On tt07 the first capture failed this way with `mem_free=74880`, and the retry after a restart succeeded ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)).

## Next

- [How to capture a stream from a demo board](how-to-capture-stream.md), with every limit and option, and the RP2350 demo board.
- [How to capture whole frames at the design's own clock](how-to-capture-frames.md), for a design that must run at full speed.
- [How to make a video of a design with ttcap demo](how-to-make-video.md).
- [Capture on the demo board](explanation-demo-board-capture.md): what happened between the pins and the file.
