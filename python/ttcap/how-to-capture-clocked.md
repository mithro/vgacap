---
type: how-to
owner: vgacap maintainers
reader: someone with an FPGA breakout who wants a design's uo_out at every clock from reset, to compare with a simulation
review: 2027-05-28
---

# How to capture one sample per clock from reset

**You want a design's `uo_out` once per project clock, with every clock numbered from reset, so the capture lines up with a simulation of the same design.**

`ttcap capture --mode clocked` loads the bitstream with the clock and reset held ([The held-clock bitstream load](reference-load-held.md)). The board's PIO then makes the project clock itself, `--clocks` rising edges in all. The first `--reset-clocks` of them, 10 unless given, come with `rst_n` low. After each edge the PIO reads `uo_out` once, so sample k is the design's output after rising edge k. The PIO stops the clock, low, whenever the link falls behind, so no sample is lost however slow the link is.

The real clock is the one nearest `--clock-hz` that is not faster. A clock is H + L + 4 PIO cycles, and `ttcap` picks the PIO divider and that cycle count together, so a rate the two can make is made exactly: 500 kHz at a 150 MHz system clock is divider 10 and 30 cycles. `uo_out` is read H + 1 PIO cycles after the rising edge, at least 80 ns after it, so at a 133 MHz system clock the fastest clock is 9.5 MHz, with H 10 and L 0. The nearest-not-faster rule holds only up to the fastest clock: a `--clock-hz` above it, rounded up to a whole hertz, is refused, and the message gives the rate to ask for. At 133 MHz, 9500000 runs and 9500001 is refused.

The 80 ns floor was measured on one design and one board: `tt_um_vga_ca` on fpga-4, on 2026-11-28. Reads up to 53 ns after the edge got wrong values there, and every read from 60 ns on matched the reference; 80 ns is a third more than 60. A design whose outputs settle more slowly needs a later read, so give it a larger `--clock-high`.

## What you need

- A Tiny Tapeout FPGA breakout on an RP2350 demo board, running the stock firmware: clocked mode takes no other board.
- The design's iCE40 bitstream on your machine, such as `tt_um_vgacal_bars.bin` from [tt-vga-testpatterns](https://github.com/mithro/tt-vga-testpatterns).
- The board to yourself for the run. On a shared bench board, claim it first, as in [How to capture from a Welland board through the bridge](how-to-capture-welland-bridge.md).

## Steps

1. In the checkout, run the capture, which loads the bitstream, clocks the design 4,194,304 times and writes every sample:

   ```sh
   uv run ttcap capture serial:/dev/ttyACM0 --mode clocked --bitstream tt_um_vgacal_bars.bin \
       --clocks 4194304 --clock-hz 500000 --out bars-clocked.vgacap
   ```

2. In the terminal, read the first line, which gives the real clock as `clock_hz=`, and H and L as `high=` and `low=`.
3. In the terminal, read the stats line, which shows `status=ok` and `board_samples=` equal to `--clocks` for a clean run.
4. In the checkout, run `./build/vgacap-dump bars-clocked.vgacap`, which prints the header, both board records, a line for each chunk of samples, and the total.

`--clocks` must be a multiple of 4, because the board packs four samples to a word. To set `ui_in` for the run, add `--ui-in`, as in [How to set a design's inputs during a capture](how-to-set-ui-in.md). To move the read point within the clock, give H with `--clock-high`, 0 to 15. The clock is then the nearest not faster than `--clock-hz` in which that H fits, and L takes up the rest; a `--clock-hz` above the fastest clock that H allows is refused. An H that reads `uo_out` under 80 ns after the edge still runs, with a warning printed before the board is loaded, and the header and the board's first record say `below_floor=1`: such a run is a sweep point, not a reference capture. Clocked mode refuses `--seconds`, `--max-bytes`, `--frames`, `--project`, `--design`, `--edge`, `--vga-mode`, `--window-lines`, `--sample-delay`, the `--rle-` options and `--no-stop-clock`. It also refuses `--firmware picosdk`, `--firmware mpy-module` and `--profile rp2040`, all before the board is touched.

After the run, however it ended, the board has the clock stopped, reset released, and its input mode and manual-clock monitor as they were. The load leaves no file behind: it deletes `/_vgacap/held.bin`, then removes `/_vgacap` if it is empty, even an empty `/_vgacap` that was there before the run. The header records the bitstream's name, length and crc32, and the run's clocks, reset clocks, system clock and PIO timing. Its mode is `SELFCLK`, and its `clock_hz` is the real clock.

## Check

A clean run exits 0. A run of 4,096 clocks at 5 MHz on the emulator, with a 150 MHz system clock, printed this:

```text
profile=rp2350-dbv3 bitstream=counter.bin clocks=4096 reset_clocks=10 clock_hz=5000000 pio_div=1 high=13 low=13
bytes=4309 samples=4096 chunks=3 seconds=0.039 overruns=0 dropped=0 rxstall=0 samples_per_s=105826 mem_free=347391 status=ok board_samples=4096 stalled_buffers=0
  board: clocked clocks=4096 reset_clocks=10 pio_div=1 cycles_per_clock=30
  board: samples=4096 stalled_buffers=0 overruns=0 status=ok sysclk_hz=150000000 elapsed_ms=0
```

Clean means all of these: the samples in the file add up to `--clocks` and to the board's `samples=`; the board says `status=ok` and `overruns=0`; its first record matches the request, with the PIO divider the board read back after setting it; and its closing record is there. `stalled_buffers=` counts the buffers during which the link held the clock, and does not make a run unclean.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `--mode clocked does not take`, exit 2 | an option clocked mode refuses | leave that option out |
| 1 | `--clocks must be a positive multiple of 4`, exit 2 | `--clocks` is not a multiple of 4 | round `--clocks` to a multiple of 4 |
| 1 | `--reset-clocks must be at least 1`, exit 2 | `--reset-clocks 0` | give at least 1 |
| 1 | `--clocks must be more than --reset-clocks`, exit 2 | no clock would come after reset | raise `--clocks` |
| 1 | `--reset-clocks must be at most`, exit 2 | the reset clocks would fill the buffer and the PIO's FIFO before reset is released | raise `--buf-words` or lower `--reset-clocks` |
| 1 | `--clock-high must be 0..15`, exit 2 | H is outside the PIO's delay field | give 0 to 15 |
| 1 | `warning: --clock-high H reads uo_out N ns after the rising edge` | the H given reads under 80 ns after the edge | none for a sweep; leave `--clock-high` out for a reference |
| 1 | `is above the fastest clocked rate` | a clock that short would read `uo_out` under 80 ns after the edge | ask for the `--clock-hz` the message gives, or less |
| 1 | `is below the slowest clocked rate` | the clock needs a larger PIO divider than there is | raise `--clock-hz` |
| 1 | `needs the FPGA breakout's RP2350 demo board` | `--profile auto` found an RP2040 | use the FPGA breakout's board |
| 3 | `not clean: status=error`, exit 1 | the board stopped the run with an `error=` record | read the `error=` line among the board lines |
| 3 | `error=rst_n stuck low` | reset did not read high 100 ms after the release | check the reset button and the board's pull-up |
| 3 | `error=clock pad was taken from PIO1 mid-run` | something else drove the clock pin during the run | stop whatever else runs on the board |
| 3 | `not clean: CLKDIV reads pio_div=` | the PIO divider the board read back is not the one `ttcap` worked out | capture again with nothing else using the board |
| 3 | `error=sysclk_hz N is not the M` | the system clock changed between the host's read and the run | capture again with nothing else using the board |
| 3 | `not clean: status=stopped`, exit 1 | a Ctrl-C, or the reader of `--out -` left early | let the run finish |

A failed run that wrote anything keeps its file, and every sample in it is good. One that failed before the first chunk leaves no file. If the board stopped answering, `ttcap` interrupts it and puts it back before it reports the failure. Every other message is on [ttcap exit codes and messages](reference-exit-codes.md).

## Next

- [The held-clock bitstream load](reference-load-held.md)
- [How to set a design's inputs during a capture](how-to-set-ui-in.md)
- [ttcap commands and options](reference-commands.md)
