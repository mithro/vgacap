---
type: how-to
owner: vgacap maintainers
reader: someone with a demo board running the module firmware who wants a capture of its design
review: 2027-05-27
---

# How to capture with the vgacap MicroPython module

**You want a `.vgacap` capture of a design's VGA output, taken through the vgacap module with
`ttcap capture --firmware mpy-module`.**

The module firmware keeps the stock firmware's SDK. So ttcap enables the design and runs its clock as it does
with the stock firmware, and only the capture itself is the module's. Putting the module on a board is on
[How to flash the module firmware and restore the board's own](how-to-flash.md). Every other `ttcap capture`
option is on [ttcap](../python/ttcap/README.md).

## What you need

- A demo board running the module firmware, flashed as on
  [How to flash the module firmware and restore the board's own](how-to-flash.md).
- `DEV`, its serial port, or the board's bridge address on the Welland bench, and a vgacap checkout set up to run
  `ttcap`.
- The design's name for `--project`, or nothing when it is enabled already, and its project clock in Hz.
  On the Welland bench, enable an uploaded design through the bridge before stopping it, with
  `curl -s -X POST localhost:8765/designs/<name>/enable`: `tt.shuttle` does not know uploaded designs.
- For step 4, a machine with CMake and a C compiler.

## Steps

1. At the checkout's root, take two whole pictures at the project's own clock, which writes `frame.vgacap`.

   ```console
   $ uv run --no-sync ttcap capture --firmware mpy-module serial:$DEV --clock-hz 25000000 --mode frame --frames 2 --out frame.vgacap
   ```

2. At the checkout's root, take one picture 48 lines at a time, which writes `window.vgacap`.

   ```console
   $ uv run --no-sync ttcap capture --firmware mpy-module serial:$DEV --clock-hz 25000000 --mode window --frames 1 --out window.vgacap
   ```

3. At the checkout's root, stream every sample for four seconds at a clock the USB link carries, which writes `stream.vgacap`.

   ```console
   $ uv run --no-sync ttcap capture --firmware mpy-module serial:$DEV --clock-hz 500000 --seconds 4 --out stream.vgacap
   ```

4. On a machine with `build/vgacap-frames` built, render a capture to PNG images, which writes `frame-0000.png` and on.

   ```console
   $ cmake -S . -B build && cmake --build build --target vgacap-frames
   $ uv run --no-sync ttcap png frame.vgacap frame
   ```

   The Welland bench's Pis have no CMake, so copy the `.vgacap` file to a workstation checkout for this step.

Add `--project <name>` to a capture to enable a design first, as with the stock firmware.

## Check

Each capture prints its board report and the file it wrote. A frame capture on fpga-1 printed this, with the
481-line region in one window of 52 heap blocks ([dated result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-fpga1)):

```text
profile=rp2350-dbv3 project=None enable=None
bytes=769804 samples=769600 chunks=4 seconds=1.010 overruns=0 dropped=0 rxstall=0 samples_per_s=762212 mem_free=421632
  board: frame window=481 buffers=52 hsync_active=0 vsync_active=0
  board: frames=2 stalls=0 rxstall=0 sysclk_hz=100000000
wrote frame.vgacap
```

A good capture reports `overruns=0` and `rxstall=0`, and its PNG shows the design's picture.

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `this board's firmware has no vgacap module` | the board runs the stock firmware | Flash the module firmware, or capture with `--firmware micropython` |
| `the board's vgacap module speaks API ...` | the module and ttcap are from different versions | Flash the module firmware built from your checkout |
| `the capture firmware has no rle mode; use --firmware micropython` | `--mode rle` was given | Use `stream`, `frame` or `window` |
| `pio 0 would have to move its pin window to GPIO 16 ...` | `--pio 0` was given | Use `--pio 1` or `--pio 2` |
| `board has only N bytes of free heap ...` | the heap is too full for the buffers | Reset the board and try again, or lower `--buf-words` for a stream |
| `... is not a shuttle entry on this board ... has to be enabled through the board's daemon first` | an uploaded design that `tt.shuttle` does not know | Enable it through the bridge: `curl -s -X POST localhost:8765/designs/<name>/enable` |
| `NameError: name 'tt' isn't defined` | the SDK's `main.py` has not run since the flash | Restart it: `uv run --no-sync python tools/board_restart.py serial:$DEV` |
| `the vgacap module refused the capture: ...` | an argument the firmware's tables refuse | Read the message against [the protocol's arguments](../firmware/PROTOCOL.md#arguments) |
| `--asic-index is for --firmware picosdk ...` | `--asic-index` was given | Drop it; select an ASIC project with `--project` |
| `overruns=` above 0 in a stream | the project clock is faster than the USB link carries | Lower `--clock-hz`, or use `--mode frame` |

## Next

- [How to flash the module firmware and restore the board's own](how-to-flash.md)
- [The vgacap module's Python API](reference-api.md)
- [The vgacap module inside MicroPython](explanation-borrowing.md)
