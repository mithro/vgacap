---
type: reference
owner: vgacap maintainers
reader: someone choosing a capture source by the hardware they have and the clock their design needs
review: 2027-05-27
---

# Capture sources compared

**You want each capture source's hardware, `ttcap` option, fastest clock, and the measurement that proves it.**

How each source works is on its own page, under [Capturing](capturing/index.md). Every figure and result links its dated measurement in [tt-vga-capture](https://github.com/mithro/tt-vga-capture).

## Stream clock

Source is the capture source, Runs on the chip that samples, and Code where it lives. The `ttcap` option selects it. Stock firmware version is the version in the run: `--` for another source, and not recorded where the result does not give it. Link is USB serial, or the fpgas.online bridge (`fpgas-tt`), named bridge here. Fastest stream clock is the highest project clock captured continuously over that link, and Result its dated measurement.

| Source | Runs on | Code | `ttcap` option | Stock firmware version | Link | Fastest stream clock | Result |
|---|---|---|---|---|---|---|---|
| the stock firmware | RP2350 on the demo board | [`python/ttcap/mp/`](../python/ttcap/mp/) | `--firmware micropython` | 3.1.1 | bridge | 400 kHz | [2026-11-27](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-hil-400k) |
| the stock firmware | RP2350 on the demo board | [`python/ttcap/mp/`](../python/ttcap/mp/) | `--firmware micropython` | 3.1.1 | USB serial | 400 kHz | [2026-11-27](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-stock-400k-usb) |
| the stock firmware | RP2040 on the demo board | [`python/ttcap/mp/`](../python/ttcap/mp/) | `--firmware micropython` | not recorded | USB serial | 60 kHz | [2026-09-15](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-ttcap-capture-serial-tt07) |
| the vgacap firmware | RP2350 on the demo board | [`firmware/`](../firmware/README.md) | `--firmware picosdk` | -- | USB serial | 750 kHz | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) |
| the vgacap MicroPython module | RP2350 on the demo board | [`mpy/`](../mpy/README.md) | `--firmware mpy-module` | -- | bridge | 500 kHz | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) |
| FPGA capture logic | iCE40UP5K on an FPGA breakout | [`rtl/`](../rtl/README.md) | -- | -- | bridge | 250 kHz | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-rtl-framer-fpga4) |
| a simulator | the host CPU | [`src/trace/`](../src/trace/) | -- | -- | -- | -- | -- |

### The stock firmware on an RP2350 demo board

Each row is one set of stream captures at one clock and length. Design is the design enabled, Length each capture's length, and Clean captures how many of the set had no overrun. With a VGA design, every capture at 400 kHz was clean, and 500 kHz overran in some captures on each link.

| Version | Link | Design | Stream clock | Length | Clean captures | Result |
|---|---|---|---|---|---|---|
| 3.1.1 | bridge | `tt_um_vgacal_bars` | 400 kHz | 10 s | 4 of 4 | [2026-11-27](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-hil-400k) |
| 3.1.1 | bridge | `tt_um_vgacal_bars` | 400 kHz | 10 s | 1 of 1 | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
| 3.1.1 | bridge | `tt_um_vgacal_bars` | 500 kHz | 10 s | 0 of 3 | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
| 3.1.1 | USB serial | `tt_um_vga_pattern` | 400 kHz | 4 s | 4 of 4 | [2026-11-27](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-stock-400k-usb) |
| 3.1.1 | USB serial | `tt_um_vga_pattern` | 400 kHz | 10 s | 1 of 1 | [2026-11-27](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-stock-400k-usb) |
| 3.1.1 | USB serial | `tt_um_vga_pattern` | 500 kHz | 4 s | 0 of 2 | [2026-11-27](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-56) and [#52](https://github.com/mithro/vgacap/issues/52) |
| 3.1.1 | USB serial | `tt_um_vga_pattern` | 500 kHz | 2 s | 1 of 1 | [2026-11-27](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-56) |
| 3.1.1 | USB serial | `tt_um_vgacal_bars` | 500 kHz | 10 s | 1 of 1 | [2026-11-27](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-fpga1) |
| 3.1.1 | USB serial | the idle display | 500 kHz | 4 s | 2 of 2 | [2026-11-27](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-56) |
| 3.1.1 | USB serial | not recorded | 500 kHz | 4 s | 1 of 1 | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
| 3.1.1 | USB serial | not recorded | 600 kHz | 4 s | 0 of 1 | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
| 3.1.1 | USB serial | not recorded | 650 kHz | 4 s | 0 of 1 | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
| 3.1.1 | USB serial | not recorded | 700 kHz | 4 s | 0 of 1 | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
| 3.1.1 | USB serial | not recorded | 750 kHz | 4 s | 0 of 4 | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
| 3.1.0 | USB serial | `tt_um_vgacal_bars` | 750 kHz | 4 s | 1 of 1 | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-direct-serial-fpga4) |

## Frame clock

Fastest frame clock is the highest project clock at which whole frames were captured. `--` means none was measured.

| Source | Runs on | Fastest frame clock | Result |
|---|---|---|---|
| the stock firmware | RP2350 on the demo board | 40 MHz | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-frame-capture-fpga4) |
| the stock firmware | RP2040 on the demo board | -- | -- |
| the vgacap firmware | RP2350 on the demo board | 25.175 MHz | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) |
| the vgacap MicroPython module | RP2350 on the demo board | 25 MHz | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) |

## Verification

Source and Chip are as above, and Breakout the breakout on the demo board. Stream, Frame and Window are the three capture modes. Pixel-exact means every pixel matched a reference render. Clean means the pictures were whole and looked right. `--` means the source has no such mode.

Stream mode:

| Source | Chip | Breakout | Stream | Result |
|---|---|---|---|---|
| the stock firmware | RP2350 | FPGA | pixel-exact | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-direct-serial-fpga4) |
| the stock firmware | RP2350 | ASIC | clean | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-pio-rle-tt09) |
| the stock firmware | RP2040 | ASIC | clean | [2026-09-15](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-ttcap-capture-serial-tt07) |
| the vgacap firmware | RP2350 | FPGA | pixel-exact | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) |
| the vgacap firmware | RP2350 | ASIC | clean | [2026-11-24](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-picosdk-firmware-fpga4/README.md#asic-board-tt09-silicon-with-asic_select) |
| the vgacap MicroPython module | RP2350 | FPGA | pixel-exact | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) |
| FPGA capture logic | iCE40UP5K | FPGA | pixel-exact | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-rtl-framer-fpga4) |
| a Raspberry Pi 5 | RP1 | -- | loopback only | [#27](https://github.com/mithro/vgacap/issues/27) |
| a simulator | -- | -- | clean | [2026-09-15](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-sim-tt_um_vga_pattern) |

Frame and window modes:

| Source | Chip | Breakout | Frame | Window | Result |
|---|---|---|---|---|---|
| the stock firmware | RP2350 | FPGA | pixel-exact | pixel-exact | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-frame-capture-fpga4) |
| the stock firmware | RP2350 | ASIC | clean | not checked | [2026-11-24](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-frame-capture-fpga4/README.md#real-silicon-tt09-at-its-design-clocks) |
| the stock firmware | RP2040 | ASIC | emulator only | emulator only | -- |
| the vgacap firmware | RP2350 | FPGA | pixel-exact | pixel-exact | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) |
| the vgacap firmware | RP2350 | ASIC | clean | clean | [2026-11-24](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-picosdk-firmware-fpga4/README.md#asic-board-tt09-silicon-with-asic_select) |
| the vgacap MicroPython module | RP2350 | FPGA | pixel-exact | pixel-exact | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) |

## Run-length capture

- Run-length capture on the stock firmware ([`ttcap capture --mode rle`](../python/ttcap/how-to-capture-rle.md)) was exact up to [25 MHz](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-11-24-pio-run-counting.md) in one-shot bursts (`--rle-oneshot`), with an FPGA breakout.
- With an ASIC breakout it [matched RAW capture at 100 kHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-pio-rle-tt09).
