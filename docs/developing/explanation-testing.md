---
type: explanation
owner: vgacap maintainers
reader: someone with a vgacap checkout who wants to know what its tests prove
review: 2027-05-27
---

# The test layers of vgacap

This page explains how vgacap is tested: which part of the capture path each kind of test runs as shipped code, and which part it replaces. It is for someone about to change vgacap who wants to know where a change is checked. The commands are on [How to build vgacap and run its tests](../development.md).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="test-layers-dark.svg">
  <img src="test-layers.svg" alt="The capture path from design uo_out down to frames, against four kinds of test: C tests, emulated RP2, simulated board and the HIL check." width="100%">
</picture>

## The capture path

A capture moves through six parts, in the order of the figure's rows. A design drives `uo_out`, the RP2's PIO and DMA sample it, and code on the board frames the samples into a stream. The stream crosses a link to the host, where `ttcap` writes it, and `vgacap-frames` rebuilds pictures from it. A test is worth what it runs as shipped code, so each kind of test below is described by the parts it runs and the parts it stands in for.

## C tests

The C tests in [`tests/`](../../tests) run under `ctest`. They check the stream reader and writer, the frame rebuilder `libvgaframe`, the simulation trace, the samplers and the vendored libraries. Their input is synthetic: [`tests/synth.c`](../../tests/synth.c) draws VGA signals with known pictures. The vgacap firmware's portable core runs here too, on [the simulated board](../../firmware/sim/hal_sim.c), with its host link in memory.

## The emulated RP2

[`ttcap.emu`](../../python/ttcap/emu/__init__.py) runs the exact MicroPython script that `ttcap capture` sends to a board, under CPython. Its PIO is a cycle-level interpreter, its DMA chains and raises interrupts, and its heap refuses blocks larger than a real board's largest free block. Each byte written to USB costs emulated time, as it does on a board. The script's stream then goes through `vgacap-frames` and is compared with the synthetic picture pixel for pixel. The emulator does not model side-set, interrupt latency, DMA bus contention or the input synchroniser.

## The simulated board

[`vgacap-fw-sim`](../../firmware/sim/sim_main.c) is the vgacap firmware's core on the simulated board, speaking the firmware protocol on a pipe instead of USB. The Python tests drive it with `ttcap`'s own firmware transport, so `ttcap capture --firmware picosdk` runs end to end. The vgacap MicroPython module gets the same treatment: [`mpy/build-unix.sh`](../../mpy/build-unix.sh) builds it into MicroPython's unix port, and a raw REPL on a pipe plays the board.

## Continuous integration

CI runs the three layers above on every push and pull request, in a configuration with GStreamer and one without. It also cross-builds the vgacap firmware and the MicroPython rp2 port, which no host test can run. In the GStreamer job, a skipped plugin, hardware-check or RTL test fails the job, because a skip would hide a check that did not run. The jobs and their steps are on [Continuous integration jobs](reference-ci-jobs.md).

## The hardware-in-the-loop check

Some questions only a board can settle: whether the profile detection reads a real `GPIOMap`, whether a real sampler overruns, and whether the picture is the one the design draws. [`tools/hil.py`](../../tools/hil.py) runs the whole path on a demo board, compares the pictures with a calibration design's reference render, and writes a dated report. It runs by hand on a bench board, never in CI. The first full pass is [the 2026-11-23 run on fpga-4](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-23-hil-fpga4), and the checks are listed on [Hardware-in-the-loop checks](../../tools/reference-hil-checks.md).
