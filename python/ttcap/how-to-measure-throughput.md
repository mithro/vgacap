---
type: how-to
owner: vgacap maintainers
reader: someone choosing the fastest project clock a demo board can stream
review: 2027-05-27
---

# How to measure a demo board's link throughput

**You want the bytes a second your demo board sends over its link, which set the fastest clock a stream keeps up with.**

`ttcap throughput` runs a pattern generator on the board, times the bytes that arrive, and checks each one. The project clock a stream can carry is the rate in bytes a second divided by the bytes one sample takes. It changes nothing on the board.

## What you need

- A board that answers `ttcap probe`, as in [How to probe a demo board](how-to-probe-board.md).

## Steps

1. In the checkout, run the measurement through the board's link, which sends a million bytes and prints one result line:

   ```sh
   uv run ttcap throughput serial:/dev/ttyACM0
   ```

2. In the result, divide `kbytes_per_s` by the bytes a sample takes, which gives the fastest stream clock in thousands of hertz.

A sample takes 1 byte on an RP2350 demo board, which packs four 8-bit samples into a word. It takes 2 bytes on an RP2040 one, which packs two 12-bit samples. `--bytes N` sets the payload and `--block N` the board's write size, 1,000,000 and 32768 unless given.

## Check

The run prints one line and exits 0:

```text
bytes=1000000 seconds=1.395 kbytes_per_s=700.0 corrupt=no
```

That is an RP2350 demo board on its own Pi ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-direct-serial-fpga4)). The rate is an upper bound. A board that sent 716 KB/s overran at 600 kHz, and was clean at 500 kHz in a 4-second run on USB serial ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)). How long a clock stays clean is open ([#52](https://github.com/mithro/vgacap/issues/52)).

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `corrupt=yes first_mismatch=N`, exit 1 | a byte arrived wrong | check the cable and the link, then run again |
| 1 | `short_by=N`, exit 1 | fewer bytes arrived than were sent | read `board_error=` on the same line |
| 1 | `throughput failed:` | the board or link did not answer | probe the board as in [How to probe a demo board](how-to-probe-board.md) |

## Next

- [How to capture a stream from a demo board](how-to-capture-stream.md)
- [How to capture a run-length encoded stream](how-to-capture-rle.md)
