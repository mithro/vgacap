---
type: how-to
owner: vgacap maintainers
reader: someone with a demo board on USB who wants every sample of a design's output
review: 2027-05-27
---

# How to capture a stream from a demo board

**You want every sample of your design's `uo_out` in a `.vgacap` file, from a demo board running the stock firmware.**

A stream holds one sample per project clock, so the design runs at a clock the USB link can carry. For a design at its real VGA clock, capture whole frames instead ([How to capture whole frames at the design's own clock](how-to-capture-frames.md)). Every option is on [ttcap commands and options](reference-commands.md).

## What you need

- A vgacap checkout with `uv sync` run in it, as in [Your first capture from a demo board](tutorial-first-capture.md).
- The demo board on USB, here `/dev/ttyACM0`, with no other program holding the port.
- A project clock the board keeps up with: 60 kHz on an RP2040 demo board ([measured](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-ttcap-capture-serial-tt07)), 500 kHz on an RP2350 one, clean in a 4-second run ([measured](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)). How long it stays clean is open ([#52](https://github.com/mithro/vgacap/issues/52)).

## Steps

1. In the checkout, choose the limit that stops the capture: `--seconds N`, `--max-bytes N` or `--frames N` for 640x480 frames.
2. In the checkout, run the capture with the clock and limit you chose, which starts the clock and writes the file:

   ```sh
   uv run ttcap capture serial:/dev/ttyACM0 --clock-hz 500000 --seconds 4 --out capture.vgacap
   ```

3. In the terminal, read the stats line, which ends the run with `overruns=0 dropped=0 rxstall=0` when no sample was lost.

This command captures the design already enabled, for 4 seconds at 500 kHz, which was clean on an RP2350 demo board's USB serial. On an RP2040 demo board use `--clock-hz 60000`. Add `--project NAME` to enable a project on an ASIC breakout, or `--design NAME` for a bitstream on an FPGA breakout.

`--frames N` asks for N + 2 frame periods of 640x480 timing, because a capture starts part way through a frame. A design with other timing needs `--max-bytes` worked out by hand. `--seconds` is 5 unless given, and `--seconds 0` leaves only the other limit.

## Check

The run prints the profile it chose, the stats and the file name:

```text
profile=rp2350-dbv3 project=None enable=None
bytes=1902002 samples=1900544 chunks=117 seconds=4.035 overruns=0 dropped=0 rxstall=0 samples_per_s=471046 mem_free=421040
  board: overruns=0 rxstall=0 sysclk_hz=133000000
wrote capture.vgacap
```

That is the command in step 2 on fpga-1, an RP2350 demo board with an FPGA breakout, from [this run](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap). On the same board 750 kHz overran 6 or 7 times in 4 seconds. An earlier [750 kHz run on fpga-4](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-direct-serial-fpga4) was clean, before its firmware was TT SDK 3.1.1. `ttcap png capture.vgacap out/frame` turns the file into pictures ([How to turn a capture into PNG images](how-to-render-png.md)).

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `board has only N bytes of free heap` | the two DMA buffers do not fit | reset the board or lower `--buf-words` |
| 2 | `no samples captured`, exit 3 | the sampler saw no project clock | check the design is enabled and clocked |
| 2 | `is not a shuttle entry on this board` | the board has no design of that name | check the name with the board's own tools |
| 3 | `overruns: N buffers torn`, exit 1 | the clock is faster than the link carries | lower `--clock-hz` or raise `--buf-words` |
| 3 | `rx stall: the RX FIFO overflowed`, exit 1 | the PIO's FIFO overflowed and samples were lost | lower `--clock-hz` |

The heap `ttcap` needs before it starts is `8 × --buf-words + 24000` bytes: 56,768 bytes at the default 4096. Each overrun is marked in the file, and the tools that read it drop the picture in progress there. Every other message is on [ttcap exit codes and messages](reference-exit-codes.md).

## Next

- [How to turn a capture into PNG images](how-to-render-png.md)
- [How to stream a capture into another program](how-to-capture-to-stdout.md)
- [How to measure a demo board's link throughput](how-to-measure-throughput.md)
- [Capture on the demo board](explanation-demo-board-capture.md)
