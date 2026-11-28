---
type: explanation
owner: vgacap maintainers
reader: someone with a vgacap checkout who wants to know what its tests prove
review: 2027-05-27
---

# The test layers of vgacap

This page explains how vgacap is tested. For each kind of test, it says which parts of the capture path run as shipped and which are stand-ins. It is for someone about to change vgacap who wants to know where a change is checked. The commands are on [How to build vgacap and run its tests](../development.md).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="test-layers-dark.svg">
  <img src="test-layers.svg" alt="For C tests, the emulated RP2, the simulated board and the hardware-in-the-loop check: what each puts at the design, PIO and DMA, board code and link." width="100%">
</picture>

## The capture path

A capture moves through six parts. A design drives `uo_out`, the RP2's PIO and DMA sample it, and code on the board frames the samples into a stream. The stream crosses a link to the host, where `ttcap` writes it, and `vgacap-frames` rebuilds pictures from it. The figure shows the four board-side parts, as its rows. On the host side, `vgacap-frames` runs as shipped in every kind of test, and `ttcap` in every kind but the C tests.

## C tests

The C tests in [`tests/`](../../tests) run under `ctest`. They check the stream reader and writer, the frame rebuilder `libvgaframe`, the simulation trace, the samplers and the vendored libraries. Their input is synthetic: [`tests/synth.c`](../../tests/synth.c) draws VGA signals with known pictures. The vgacap firmware's portable core runs here too, on [the simulated board](../../firmware/sim/hal_sim.c), with its host link in memory.

## The emulated RP2

[`ttcap.emu`](../../python/ttcap/emu/__init__.py) runs the exact MicroPython script that `ttcap capture` sends to a board, under CPython. Its PIO is a cycle-level interpreter, its DMA chains and raises interrupts, and its heap refuses blocks larger than a real board's largest free block. Each byte written to USB costs emulated time, as it does on a board. The script's stream then goes through `vgacap-frames` and is compared with the synthetic picture pixel for pixel. The emulator does not model interrupt latency, timer latency (a `Board.add_timer` callback runs at the end of the advance it falls due in), DMA bus contention, the input synchroniser or the PIO clock divider: a `StateMachine` given a `freq=` is refused. It also refuses an IRQ flag shared by two running state machines, a state machine reading back a pin PIO drives, an input with no pull that nothing drives, side-set onto a pad that a `Pin` init took back from PIO, and a `ClockedDesign`'s `rst_n` and clock driven by two state machines. It refuses too much in one place: a pad a `Pin` init took back is still refused to `in pins` and `wait`, which a board allows.

Its pins follow MicroPython v1.24.0. `Pin(n, Pin.OUT, value=v)` drives a pad, any mode takes the pad from PIO, and `Pin(n).value()` reads the pad whatever drives it. `Board(pulls=...)` sets external pulls, which beat the RP2's own. `ClockedDesign` is an exact counter. It is clocked only by the rising edges really driven on its clock pad, resets synchronously while `rst_n` is low, and shows its value on `uo_out` `settle` cycles after each edge. Because the input synchroniser is not modelled, where a clocked capture samples relative to the edge is checked on hardware only. `ClockedDesign.configure()`, a bitstream load, forgets the design before it: `count_at` for a cycle before the load returns `initial`, not what the old design held then. The tests' fake SDK and FPGA loader ([`fake_tt.py`](../../python/tests/fake_tt.py)) run on these pins. They do not model:

- `uo_out`, `ui_in` and the pins the SDK always makes outputs (GPIO 0-3, 5, 6, 11, 12, the loader's 3 and 6 among them) in a mode write's pin rebuild;
- `ui_in` on the emulator's pads: `tt.ui_in` is the fake's own model, and touches no pad;
- the real loader's PIO0 state machine 0, which takes GPIO 3 and 6 for PIO0 and leaves its program loaded;
- the real loader's transfer time: the fake takes about the time the bits take at `freq`, while the real loop's pace is set by its `sleep_us(1)` polls.

## The simulated board

[`vgacap-fw-sim`](../../firmware/sim/sim_main.c) is the vgacap firmware's core on the simulated board, speaking the firmware protocol on a pipe instead of USB. The Python tests drive it with `ttcap`'s own firmware transport, so `ttcap capture --firmware picosdk` runs end to end. The vgacap MicroPython module gets the same treatment: [`mpy/build-unix.sh`](../../mpy/build-unix.sh) builds it into MicroPython's unix port, and a raw REPL on a pipe plays the board.

## Continuous integration

CI runs the three layers above on every push and pull request, in a configuration with GStreamer and one without. It also cross-builds the vgacap firmware and the MicroPython rp2 port, which no host test can run. In the GStreamer job, a skipped plugin, hardware-check or RTL test fails the job, because a skip would hide a check that did not run. The sanitizers job runs the samplers' C tests, `test_tbv1` and `test_centrev1`, under AddressSanitizer and UndefinedBehaviorSanitizer. A write one byte past a frame buffer changes no output that a plain test can see, but the sanitizers stop on it. The jobs and their steps are on [Continuous integration jobs](reference/reference-ci-jobs.md).

## The hardware-in-the-loop check

Some questions only a board can settle. Does the profile detection read a real `GPIOMap`? Does a real sampler overrun, and is the picture the one the design draws?

[`tools/hil.py`](../../tools/hil.py) runs the whole path on a demo board, compares the pictures with a calibration design's reference render, and writes a dated report. It runs by hand on a bench board, never in CI. The first full pass is [the 2026-11-23 run on fpga-4](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-23-hil-fpga4), and the checks are listed on [Hardware-in-the-loop checks](../../tools/reference-hil-checks.md).
