---
type: how-to
owner: vgacap maintainers
reader: someone with a demo board running the vgacap firmware who wants a capture of its design
review: 2027-05-27
---

# How to capture with the vgacap firmware

**You want a `.vgacap` capture of a design's VGA output, taken through the vgacap firmware with
`ttcap capture --firmware picosdk`.**

This page covers frame, window and stream captures. The firmware cannot enable a design, so the design must
already be on the board. [How to run the vgacap firmware from RAM and go back to stock](how-to-load.md) says
how, and every other `ttcap capture` option is on [ttcap commands and options](../python/ttcap/reference-commands.md).

## What you need

- A demo board running the vgacap firmware, and `FW`, its serial port, both from
  [How to run the vgacap firmware from RAM and go back to stock](how-to-load.md).
- On that machine, a vgacap checkout set up to run `ttcap`.
- On a demo board with an FPGA breakout, a VGA design enabled before the firmware was loaded, as in step 2 of
  [How to run the vgacap firmware from RAM and go back to stock](how-to-load.md). Without one, frame captures fail
  with `board went quiet`. A capture of a bench board's idle display holds no VGA frames, and `ttcap png` prints `frames=0`.
- For step 4, a workstation with CMake, a C compiler and a vgacap checkout with `uv sync` run, for Pillow.
  A bench Pi has no room or time for Pillow, as [How to run ttcap on a Raspberry Pi](../python/ttcap/how-to-run-on-raspberry-pi.md) says, so step 4 copies the capture there.
- The design's project clock in Hz: 25000000 for a 640x480@60 design at 25 MHz.
- With an ASIC breakout, the project's mux index: [How to select an ASIC project with the vgacap firmware](how-to-select-asic-project.md).

## Steps

1. At the checkout's root, take two whole pictures at the project's own clock, which writes `frame.vgacap`.

   ```console
   $ uv run --no-sync ttcap capture --firmware picosdk serial:$FW --clock-hz 25000000 --mode frame --frames 2 --out frame.vgacap
   ```

2. At the checkout's root, take one picture 48 lines at a time, which writes `window.vgacap`.

   ```console
   $ uv run --no-sync ttcap capture --firmware picosdk serial:$FW --clock-hz 25000000 --mode window --frames 1 --out window.vgacap
   ```

3. At the checkout's root, stream every sample for four seconds at a clock the USB link carries, which writes `stream.vgacap`.

   ```console
   $ uv run --no-sync ttcap capture --firmware picosdk serial:$FW --clock-hz 750000 --seconds 4 --out stream.vgacap
   ```

4. In the workstation's checkout, copy the capture from the Pi and render it to PNG images, which writes `frame-0000.png` and on.

   ```console
   $ scp -J tweed.welland.mithis.com pi@<pi>:vgacap/frame.vgacap .
   $ cmake -S . -B build && cmake --build build --target vgacap-frames
   $ uv run ttcap png frame.vgacap frame
   ```

With an ASIC breakout, add `--asic-index N` to each capture command. In step 4, `<pi>` is the Pi's address on the bench network, such as `10.21.2.35` for fpga-4's Pi. Step 4 copies `frame.vgacap`, the file step 1 wrote; `window.vgacap` and `stream.vgacap` render the same way.

## Check

Each capture prints its board report and the file it wrote. A frame capture on fpga-4 printed this, with the
whole 481-line region in one window and one buffer
([dated result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4)):

```text
profile=rp2350-dbv3 project=None enable=kept (capture firmware)
bytes=769803 samples=769600 chunks=4 seconds=1.043 overruns=0 dropped=0 rxstall=0 samples_per_s=738153
  board: frame window=481 buffers=1 hsync_active=0 vsync_active=0
  board: frames=2 stalls=0 rxstall=0 sysclk_hz=100000000
wrote frame.vgacap
```

A good capture reports `overruns=0` and `rxstall=0`, and its PNG shows the design's picture.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 to 3 | `the pico-sdk capture firmware cannot enable ...` | `--project` or `--design` was given | Drop it; enable the design under the stock firmware before loading this one |
| 1 to 3 | `the capture firmware has no rle mode; use --firmware micropython` | `--mode rle` was given | Use `stream`, `frame` or `window`, or the stock firmware |
| 1 to 3 | `--asic-index is for --firmware picosdk ...` | `--asic-index` without `--firmware picosdk` | Add `--firmware picosdk` |
| 1 to 3 | `--asic-index is for ...; ... is an FPGA board, where asic_select pulls GPIO 1 -- the iCE40's CRESET_B -- low and erases the FPGA` | the board is a demo board with an FPGA breakout | Drop `--asic-index`: an FPGA keeps its design |
| 1 and 2 | `board went quiet` and a frame capture writes almost nothing | no sync on `uo_out`: an ASIC project that was not selected again, or no design | Add `--asic-index N` on an ASIC breakout; enable the design again on an FPGA breakout |
| 3 | `overruns=` above 0, exit 1 | the project clock is faster than the USB link carries | Lower `--clock-hz`, or use `--mode frame` |
| 1 and 2 | `sysclk N is not 2..32 whole project clocks of N` | a frame clock with no whole system clock ratio | Choose a project clock from [the clock table](reference-board.md#clocks) |
| 1 to 3 | `pio 0 would have to move its pin window to GPIO 16 ...` | `--pio 0` was given: ttcap refuses it for every firmware ([issue #44](https://github.com/mithro/vgacap/issues/44)) | Use `--pio 1` or `--pio 2` |
| 1 to 3 | `--profile ... does not match the capture firmware` | `--profile` names another board | Drop `--profile`, or give `rp2350` |

## Next

- [How to run the vgacap firmware from RAM and go back to stock](how-to-load.md)
- [The vgacap firmware protocol](PROTOCOL.md)
- [Capture inside the vgacap firmware](explanation-capture.md)
