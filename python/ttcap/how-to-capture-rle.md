---
type: how-to
owner: vgacap maintainers
reader: someone with an RP2350 demo board who wants a run-length encoded capture of a design
review: 2027-05-27
---

# How to capture a run-length encoded stream

**You want every sample of a design whose picture has long runs of one colour, at a clock faster than a plain stream carries.**

`ttcap capture --mode rle` has the board's PIO count runs of equal samples and send one value and one run length for each. A picture of large areas then needs far fewer bytes than its samples, and a busy one needs more. Why that is so is on [Run-length capture](explanation-run-length-capture.md).

## What you need

- An RP2350 demo board running the stock firmware, set up as in [How to capture a stream from a demo board](how-to-capture-stream.md). On an RP2040 demo board, run-length capture has run only in the emulator ([#38](https://github.com/mithro/vgacap/issues/38)).
- A stop limit in seconds or bytes: `--frames` does not apply, because a frame's size depends on its picture.

## Steps

1. In the checkout, run a run-length capture for a time, which writes `RLE` chunks to the file:

   ```sh
   uv run ttcap capture serial:/dev/ttyACM0 --project tt_um_vga_clock \
       --mode rle --clock-hz 100000 --seconds 14 --out clock-rle.vgacap
   ```

2. In the terminal, read the first board line, which names the counting style the board chose and its period in system clocks.
3. In the terminal, read the stats line, which shows `rxstall=0` when every pair arrived.
4. In the checkout, run `mkdir -p out && uv run ttcap png clock-rle.vgacap out/clock`, which expands the runs and writes the frames as PNGs.

For one burst at the design's real clock, add `--rle-oneshot` and a large `--buf-words`. The board then fills its two buffers once at full speed and sends them, so the burst holds `--buf-words` pairs whatever the link carries. On an FPGA breakout, a burst at 25 MHz with `--buf-words 20000` matched the design exactly ([research note](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-11-24-pio-run-counting.md)).

## Check

The run from step 1, on the tt09 board, printed this:

```text
profile=rp2350-dbv3 project=tt_um_vga_clock enable=index
bytes=82117 samples=1513243 chunks=7 seconds=15.619 overruns=0 dropped=0 rxstall=0 samples_per_s=96886 mem_free=405776
  board: rle style=edge period=1500 mask=1
  board: overruns=0 rxstall=0 saturated=0 sysclk_hz=150000000
```

1.5 million samples came in 82 KB. Its pictures were pixel-identical to a plain stream of the same design ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-pio-rle-tt09)).

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `--frames does not apply to --mode rle` | `--frames` was given | use `--seconds` or `--max-bytes` |
| 1 | `the capture firmware has no rle mode` | `--firmware` is not `micropython` | leave `--firmware` at its default |
| 2 | `error=no exact rle style` | the board cannot count runs at this clock | pick a clock at most a sixth of the system clock with `--rle-mask 1`, a seventh with `0` |
| 2 | `error=SHIFTCTRL.IN_COUNT not set` | the one-instruction sample is not available | add `--rle-mask 0` |
| 3 | `rx stall: the RX FIFO overflowed`, exit 1 | pairs came faster than the link took them | lower `--clock-hz` or use `--rle-oneshot` |

After an RX stall the board stops at once, so the file holds every pair up to the stall and nothing after it. `--rle-mask 1` reads a sample in one instruction, on an RP2350 only. `--rle-mask 0` reads it in two, on either chip; on an RP2040 that has run only in the emulator ([#38](https://github.com/mithro/vgacap/issues/38)). `-1`, the default, lets the board choose.

## Next

- [Run-length capture](explanation-run-length-capture.md)
- [How to capture whole frames at the design's own clock](how-to-capture-frames.md)
- [ttcap commands and options](reference-commands.md)
