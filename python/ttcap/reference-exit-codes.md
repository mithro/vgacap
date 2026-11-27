---
type: reference
owner: vgacap maintainers
reader: someone whose ttcap run ended with an error or an unexpected exit code
review: 2027-05-27
---

# ttcap exit codes and messages

**What each `ttcap` exit code means, and the messages a failed run prints, with their causes.**

The codes and messages are those of [`cli.py`](cli.py), [`capture.py`](capture.py) and [`demo.py`](demo.py). The options named here are on [ttcap commands and options](reference-commands.md). A failure caught by `ttcap` prints one line on stderr, `<subcommand> failed: <exception type>: <message>`, then any notes under it.

## Exit codes

Code is the exit status, Subcommands the subcommands that use it, and Meaning what it says about the run.

| Code | Subcommands | Meaning |
|---|---|---|
| 0 | all | the run did what it was asked |
| 1 | all | a board error, a link error, a tool error, a refused request, a capture that lost samples, or a clocked capture that is not clean |
| 2 | all | a usage error, reported by the argument parser, or a `capture` option its `--mode` does not take |
| 3 | `capture` and `demo` | the run produced no samples or no frames, and the board reported no error |
| 130 | `demo` | Ctrl-C arrived before the pipeline started or after it ended |
| 128 + N | `demo` | the pipeline was ended by signal N |

`ttcap demo` exits with the status of its `gst-launch-1.0` pipeline when that is not 0. A stop signal sent to the demo ends the pipeline cleanly, so a stopped run that captured exits 0. A timed run whose capture lost samples exits 1, and keeps its frames and video. A stopped run whose capture lost samples still exits 0, and prints nothing about the loss ([#64](https://github.com/mithro/vgacap/issues/64)).

## Capture

Message is the start of what `ttcap capture` prints. Exit is the exit code. Cause is what made it, and Fix what to do.

| Message | Exit | Cause | Fix |
|---|---|---|---|
| `no samples captured: the sampler never saw a clock edge` | 3 | no project clock reached the sampler | check the design is enabled and clocked |
| `board has only N bytes of free heap after a collect` | 1 | the board lacks the heap for the buffers | reset the board or lower `--buf-words` |
| `board error:` | 1 | the board raised or refused a setting | read the board's reason after the colon |
| `rx stall: the RX FIFO overflowed and the RLE capture stopped there` | 1 | run-length pairs came faster than the link took them | lower `--clock-hz` or add `--rle-oneshot` |
| `overruns: N buffers torn` | 1 | the board's DMA buffers filled faster than the link drained them | lower `--clock-hz` or raise `--buf-words` |
| `rx stall: the RX FIFO overflowed and samples were lost` | 1 | the PIO's FIFO overflowed in `stream`, `frame` or `window` mode | lower `--clock-hz` |
| `timed_out=yes` in the stats line | 1 | the board went quiet during the capture | check the project clock is running |
| `error='framing lost` in the stats line | 1 | the stream lost its chunk framing | reset the board and capture again |
| `board did not reply with a GPIOMap.all() dict` | 1 | the board printed something else | pass `--profile rp2040` or `--profile rp2350` |
| `is not a shuttle entry on this board` | 1 | `--project` or `--design` names no design on the board | name a design the board has |
| `rst_n reads 1 while reset is held` | 1 | `tt.reset_project(True)` did not pull the reset line low | check nothing else drives the board's reset line |
| `rst_n stuck low after release` | 1 | the reset line stayed low for 100 ms after the release | check the reset button and the board's pull-up |
| `ui_in reads 0xNN after writing 0xMM` | 1 | the stock firmware left an input undriven because it read high | turn the board's input switches off |
| `--frames does not apply to --mode rle` | 1 | a run-length frame has no fixed size | use `--max-bytes` or `--seconds` |
| `the capture would never stop` | 1 | `--seconds 0` with no other limit | add `--max-bytes` or `--frames` |
| `pio 0 would have to move its pin window` | 1 | PIO block 0 holds the stock firmware's program | use `--pio 1` or `--pio 2` |
| `unsupported link url` | 1 | the link is not `serial:` or `ws://` | write the link as `serial:/dev/ttyACM0` |
| `capture ended: the consumer closed the stream` | 0 | the program reading `--out -` stopped | none: the capture ended cleanly |
| `--mode clocked does not take` | 2 | an option clocked mode refuses | leave that option out |
| `only --mode clocked takes` | 2 | `--bitstream`, `--clocks`, `--reset-clocks` or `--clock-high` in another mode | add `--mode clocked` or leave the option out |
| `--mode clocked needs` | 2 | `--bitstream` or `--clocks` is missing | give both |
| `--clocks must be a positive multiple of 4` | 2 | the board packs four samples to a word | round `--clocks` to a multiple of 4 |
| `--reset-clocks must be at least 1` | 2 | `--reset-clocks 0` | give at least 1 |
| `--clocks must be more than --reset-clocks` | 2 | no clock would come after reset | raise `--clocks` |
| `--reset-clocks must be at most` | 2 | the reset clocks would fill the buffer and the PIO's FIFO before reset is released | raise `--buf-words` or lower `--reset-clocks` |
| `--clock-high must be 0..15` | 2 | H is outside the PIO's delay field | give 0 to 15 |
| `not clean:` | 1 | a clocked capture did not make and send every clock with `status=ok` | read the reasons after the colon |
| `CLKDIV reads pio_div=` | 1 | the PIO divider a clocked capture's board read back is not the one worked out | capture again with nothing else using the board |
| `is above the fastest clocked rate` | 1 | `--clock-hz` would read `uo_out` under 40 ns after the edge | lower `--clock-hz` |
| `is below the slowest clocked rate` | 1 | `--clock-hz` needs a larger PIO divider than there is | raise `--clock-hz` |

The `overruns:` and `rx stall:` lines follow the board's closing report in every `--mode` but `clocked`, including after `capture ended:`. A clocked capture prints `not clean:` instead, with every reason, also after `capture ended:` ([How to capture one sample per clock from reset](how-to-capture-clocked.md)). The stream written before them is kept and is still valid. A capture that lost no samples exits 0.

## Throughput, probe and reboot

Subcommand is the command, and Message the start of what it prints. The other columns are as above.

| Subcommand | Message | Exit | Cause | Fix |
|---|---|---|---|---|
| `throughput` | `corrupt=yes` | 1 | a byte came back wrong | check the cable and the link |
| `throughput` | `short_by=N` | 1 | fewer bytes arrived than were sent | read `board_error=`, if the line has one |
| `probe` | `probe failed:` | 1 | the link cannot be opened or the board does not answer | check the link and that nothing else holds the port |
| `reboot` | `reboot failed:` | 1 | the link cannot be opened or the board does not answer | check the link and that nothing else holds the port |

## PNG

Message is the start of what `ttcap png` prints. The other columns are as above.

| Message | Exit | Cause | Fix |
|---|---|---|---|
| `png failed: cannot find vgacap-frames` | 1 | the C tools are not built | build them or pass `--vgacap-frames` |
| `png failed: ... exited 2:` | 1 | `vgacap-frames` decoded no whole frame | capture at least three frame periods |
| `png failed: Pillow is needed to write PNGs` | 1 | Pillow is not installed | run `uv sync --extra synth` |

After `exited 2:` comes what `vgacap-frames` printed on its error output, or `no frame decoded` when it printed nothing.

## Demo

Message is the start of what `ttcap demo` prints. The other columns are as above.

| Message | Exit | Cause | Fix |
|---|---|---|---|
| `nothing was captured: the board's sync was detected` | 3 | the capture ended before a frame closed | capture at least three frame periods |
| `nothing was captured: the pipeline ran but produced no frames` | 3 | no VGA signal reached the decoder | check the design and `--clock-hz` |
| `the capture failed (exit status 1): … overruns: N buffers torn` | 1 | the capture lost samples | lower `--clock-hz` |
| `already holds N file(s) from a previous run` | 1 | `--outdir` holds an earlier run's output | pass `--force` or a new `--outdir` |
| `no board: pass --board` | 1 | neither `--board` nor `--link` was given | give one of them |
| `unknown board` | 1 | `--board` names no Welland bench board | use `--link` for another board |
| `--design is for the FPGA boards` | 1 | `--design` on an ASIC breakout | use `--project` |
| `--project is for the ASIC shuttles` | 1 | `--project` on an FPGA breakout | use `--design` |
| `is out of range` | 1 | a number outside what the pipeline accepts | use a value in the range printed |
| `cannot find gst-launch-1.0` | 1 | the GStreamer tools are not installed | install `gstreamer1.0-tools` |
| `GStreamer has no vgacapttsrc` | 1 | the vgacap plugin is not built or not found | build it and set `GST_PLUGIN_PATH` |
| `no video encoder` | 1 | neither `x264enc` nor `vp8enc` is installed | install one or pass `--no-video` |
| `nothing to write` | 1 | every output is switched off | keep at least one output |
| `--serve N:` | 1 | the port is taken or needs root | pick a free port above 1023 |
