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

Source is the capture source, Runs on the chip that samples, and Code where it lives. The `ttcap` option selects it. Fastest stream clock is the highest project clock captured continuously. Result is the dated measurement of that figure.

| Source | Runs on | Code | `ttcap` option | Fastest stream clock | Result |
|---|---|---|---|---|---|
| the stock firmware | RP2350 on the demo board | `python/ttcap/mp/` | `--firmware micropython` | 500 kHz | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
| the stock firmware | RP2040 on the demo board | `python/ttcap/mp/` | `--firmware micropython` | 60 kHz | [2026-09-15](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-ttcap-capture-serial-tt07) |
| the vgacap firmware | RP2350 on the demo board | `firmware/` | `--firmware picosdk` | 750 kHz | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) |
| the vgacap MicroPython module | RP2350 on the demo board | `mpy/` | `--firmware mpy-module` | 500 kHz | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) |
| FPGA capture logic | iCE40UP5K on an FPGA breakout | `rtl/` | -- | 250 kHz | [2026-11-24](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-rtl-framer-fpga4) |
| a simulator | the host CPU | `src/trace/` | -- | -- | -- |

On the stock firmware and an RP2350 demo board:

- 500 kHz was clean on USB serial in one 4-second run, and 600 kHz to 750 kHz overran, in the table's 2026-11-26 result. How long 500 kHz stays clean is open ([#52](https://github.com/mithro/vgacap/issues/52)).
- Through the fpgas.online bridge (`fpgas-tt`), 400 kHz was clean in four 10-second runs ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-hil-400k)), and 500 kHz overran in the 2026-11-26 result.
- On 2026-11-24, before the stock firmware 3.1.1, 750 kHz was clean on USB serial ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-direct-serial-fpga4)).

## Frame clock

Fastest frame clock is the highest project clock at which whole frames were captured, with an FPGA breakout. `--` means none was measured.

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
