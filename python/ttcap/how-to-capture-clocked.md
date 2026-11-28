---
type: how-to
owner: vgacap maintainers
reader: someone with an FPGA breakout who wants a design's uo_out at every clock from reset, to compare with a simulation
review: 2027-05-28
---

# How to capture one sample per clock from reset

**You want a design's `uo_out` once per project clock, with every clock numbered from reset, so the capture lines up with a simulation of the same design.**

`ttcap capture --mode clocked` loads the bitstream with the clock and reset held ([The held-clock bitstream load](reference-load-held.md)). The board's PIO then makes the project clock itself, `--clocks` rising edges in all. The first `--reset-clocks` of them, 10 unless given, come with `rst_n` low. After each edge the PIO reads `uo_out` once, so sample k is the design's output after rising edge k. The PIO stops the clock, low, whenever the link falls behind, so no sample is lost however slow the link is.

The real clock is the one nearest `--clock-hz` that is not faster. A clock is H + L + 4 PIO cycles, and `ttcap` picks the PIO divider and that cycle count together, so a rate the two can make is made exactly: 500 kHz at a 150 MHz system clock is divider 10 and 30 cycles. `uo_out` is read H + 1 PIO cycles after the rising edge, and the read sees the pin level of 2 system clocks earlier, at least 65 ns after the edge, so at a 133 MHz system clock the fastest clock is 9.5 MHz, with H 10 and L 0. The nearest-not-faster rule holds only up to the fastest clock: a `--clock-hz` above it, rounded up to a whole hertz, is refused, and the message gives the rate to ask for. At 133 MHz, 9500000 runs and 9500001 is refused.

The read time is the time from the side-set rising edge to the `in` that reads `uo_out`: (H + 1) PIO cycles. The pin time is the time of the pin level that `in` sees, the read time less the GPIO input synchroniser's 2 system clocks, 15 ns at 133 MHz.

The floor is 65 ns of pin time. The synchroniser's delay is fixed in system clocks, and the design's settling is fixed in nanoseconds, so a floor on the read time moves the pin time into the design's settling window at a lower system clock. At 48 MHz, an 80 ns read-time floor gives H 3, which reads at 83 ns but sees the pin at 42 ns. The 65 ns floor gives H 5 there.

The floor rests on one design and one board: `tt_um_vga_ca` on fpga-4, on 2026-11-28 (+1030), at 7 MHz, divider 1 and a 133 MHz system clock, with `--clock-high` 0 to 15 ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-28-clocked-fpga4/)):

| H | Read time | Pin time | Result |
|---|---|---|---|
| 0 | 7.5 ns | before the edge | the previous clock's output |
| 1-4 | 15.0-37.6 ns | 0.0-22.6 ns | the previous clock's output |
| 5 | 45.1 ns | 30.1 ns | a mix of the previous and new output |
| 6 | 52.6 ns | 37.6 ns | a mix of the previous and new output |
| 7-15 | 60.2-120.3 ns | 45.1-105.3 ns | exact |

For this design on this board, the output starts changing after 37.6 ns of read time and is complete by 60.2 ns. In pin time, that is 22.6 to 45.1 ns. The last wholly wrong read is H 4, and the first exact one is H 7. At 133 MHz the 65 ns floor is H 10, a read at 82.7 ns, about 68 ns in pin time. The margin is kept because only one design on one board was measured.

The floor before it was 80 ns of read time, from the same sweep. The 40 ns floor before that was a design-note choice, never measured. The 25 ns it built on came from frame mode on a different design: `tt_um_vgacal_bars` on fpga-4, with a 25 MHz project clock at a 100 MHz system clock. That page puts the output change at about 25 ns after the rising edge, input synchroniser included: falling+0 was wrong, and falling+1 and rising+0 were exact ([result](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-frame-capture-fpga4/README.md)).

The read point a design needs depends on its clock-to-output path. A design with a slower path can read the previous clock's value at the default H and still report a clean run, because every sample arrives, one clock late. Only a sweep of H against the design's reference shows it. To sweep, run once with each `--clock-high` from 0 to 15 at a rate that is divider 1, such as 7 MHz at a 133 MHz system clock, and compare each capture with the reference.

To have the read see the pin t_pin_ns after the edge, give `--clock-high H` with H = ceil((t_pin_ns × sysclk + 2e9) / (1e9 × pio_div)) − 1, where t_pin_ns is the pin time and pio_div is the `pio_div=` that the run's first line prints. At divider 1 that is H = ceil(t_pin_ns × sysclk / 1e9 + 2) − 1. For 90 ns of pin time at 133 MHz and divider 1, ceil(11.97 + 2) − 1 gives H 13, a read at 105.3 ns that sees the pin at 90.2 ns. No option takes the pin time in nanoseconds ([#97](https://github.com/mithro/vgacap/issues/97)).

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

`--clocks` must be a multiple of 4, because the board packs four samples to a word. To set `ui_in` for the run, add `--ui-in`, as in [How to set a design's inputs during a capture](how-to-set-ui-in.md). To move the read point within the clock, give H with `--clock-high`, 0 to 15. The clock is then the nearest not faster than `--clock-hz` in which that H fits, and L takes up the rest; a `--clock-hz` above the fastest clock that H allows, rounded up to a whole hertz, is refused. An H whose read sees the pin under 65 ns after the edge still runs, with a warning printed before the board is loaded, and the header and the board's first record say `below_floor=1`: such a run is a sweep point, not a reference capture. Clocked mode refuses `--seconds`, `--max-bytes`, `--frames`, `--project`, `--design`, `--edge`, `--vga-mode`, `--window-lines`, `--sample-delay`, the `--rle-` options and `--no-stop-clock`. It also refuses `--firmware picosdk`, `--firmware mpy-module` and `--profile rp2040`, all before the board is touched.

Once `ttcap` has finished, however the run ended, the board has the clock stopped, reset released, and its input mode and manual-clock monitor as they were. A killed `ttcap` can leave the design in reset, and the mode and monitor as the capture set them, as [If it fails](#if-it-fails) says. A load that runs to its end leaves no file behind: it deletes `/_vgacap/held.bin`, then removes `/_vgacap` if it is empty, even an empty `/_vgacap` that was there before the run. The header records the bitstream's name, length and crc32, and the run's clocks, reset clocks, system clock and PIO timing. Its mode is `SELFCLK`, and its `clock_hz` is the real clock.

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
| 1 | `warning: --clock-high H reads uo_out N ns after the rising edge` | the H given sees the pin under 65 ns after the edge | none for a sweep; leave `--clock-high` out for a reference |
| 1 | `is above the fastest clocked rate` | above the fastest clock the `--clock-high` H allows, or the 65 ns pin-time floor without it | ask for the `--clock-hz` the message gives, or less |
| 1 | `is below the slowest clocked rate` | the clock needs a larger PIO divider than there is | raise `--clock-hz` |
| 1 | `needs the FPGA breakout's RP2350 demo board` | `--profile auto` found an RP2040 | use the FPGA breakout's board |
| 3 | `not clean: status=error`, exit 1 | the board stopped the run with an `error=` record | read the `error=` line among the board lines |
| 3 | `error=rst_n stuck low` | reset did not read high 100 ms after the release | check the reset button and the board's pull-up |
| 3 | `error=clock pad was taken from PIO1 mid-run` | something else drove the clock pin during the run | stop whatever else runs on the board |
| 3 | `not clean: CLKDIV reads pio_div=` | the PIO divider the board read back is not the one `ttcap` worked out | capture again with nothing else using the board |
| 3 | `error=sysclk_hz N is not the M` | the system clock changed between the host's read and the run | capture again with nothing else using the board |
| 3 | `not clean: status=stopped`, exit 1 | a Ctrl-C, or the reader of `--out -` left early | let the run finish |

A failed run that wrote anything keeps its file, and every sample in it is good. One that failed before the first chunk leaves no file. If the board stopped answering, `ttcap` interrupts it and puts it back before it reports the failure. Every other message is on [ttcap exit codes and messages](reference-exit-codes.md).

What the board is left with when `ttcap` itself is killed depends on when it dies.

- Killed during the bitstream upload, the board is as it was, but `/_vgacap/held.bin`, or part of it, may be left on it. The next clocked capture overwrites it, then deletes it.
- Killed after the upload but before the board script starts, the board stops part way through [the load's stages](reference-load-held.md#what-it-sends). From stage 8 on, the design stays in reset, with `rst_n` 0. From stage 12 on, the clock pin is held low by the load's SIO `Pin`, not by PIO1. The input mode and the manual-clock monitor are as those stages left them. After a kill anywhere from stage 4 to stage 16, `/_vgacap/held.bin`, the whole bitstream, about 100 KB for a typical design, stays on the board's filesystem until a later clocked capture reaches stage 16.
- Killed once `clocked_rp2.py` has started, even during the reset clocks, the board finishes the run on its own. It releases reset and leaves the clock pin low on PIO1, unless the run fails before the reset release (the firmware or system clock check, the PIO claim, the reset handshake or the clock pad check). Then `rst_n` stays 0, and if it failed before the state machine was made (the firmware, system clock or PIO claim check), the clock pin stays on the load's SIO `Pin`. The input mode and the manual-clock monitor stay as the capture set them. A killed host may instead leave the board's USB write blocked, so that the run does not finish; this has not been tested on hardware.

No `ttcap` command puts the input mode and the monitor back after a kill: only a `ttcap` that runs to its end does. To put back the values from before the capture, set them in the board's REPL: `tt.mode` to the mode from before, and `tt.manual_project_clock.monitoring = True` if the monitor was on. You need to know those values. `ttcap reboot LINK` does not put them back. It runs `machine.reset()`, and the firmware then boots from the board's `config.ini`, whose `[DEFAULT]` section in the stock firmware 3.1.1 has `project = tt_um_factory_test` and `mode = ASIC_RP_CONTROL` ([config.ini](https://github.com/TinyTapeout/tt-micropython-firmware/blob/v3.1.1/src/config.ini)). That gives the boot settings, not the ones from before the capture, and replaces the clocked design. Any later capture releases reset.

## Next

- [The held-clock bitstream load](reference-load-held.md)
- [How to set a design's inputs during a capture](how-to-set-ui-in.md)
- [ttcap commands and options](reference-commands.md)
