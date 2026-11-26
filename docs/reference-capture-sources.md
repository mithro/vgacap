---
type: reference
owner: vgacap maintainers
reader: someone choosing a capture source by the hardware they have and the clock their design needs
review: 2027-05-27
---

# Capture sources compared

**You want each capture source's hardware, `ttcap` option, fastest clock, and the measurement that proves it.**

How each source works is on its own page, under [Capturing](capturing/index.md). Every figure and result links its dated measurement in [tt-vga-capture](https://github.com/mithro/tt-vga-capture).

## Speed

Source is the capture source, Runs on the chip that samples, and Code where it lives. The `ttcap` option selects it. Fastest stream clock is the highest project clock captured continuously. Fastest frame clock is the highest at which whole frames were captured. Each figure links its dated result; `--` means none.

| Source | Runs on | Code | `ttcap` option | Fastest stream clock | Fastest frame clock |
|---|---|---|---|---|---|
| the stock firmware | RP2350 on the demo board | [`python/ttcap/mp/`](../python/ttcap/mp/) | `--firmware micropython` | [750 kHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-direct-serial-fpga4) | [40 MHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-frame-capture-fpga4) |
| the stock firmware | RP2040 on the demo board | [`python/ttcap/mp/`](../python/ttcap/mp/) | `--firmware micropython` | [60 kHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-ttcap-capture-serial-tt07) | -- |
| the vgacap firmware | RP2350 on the demo board | [`firmware/`](../firmware/README.md) | `--firmware picosdk` | [750 kHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) | [25.175 MHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) |
| the vgacap MicroPython module | RP2350 on the demo board | [`mpy/`](../mpy/README.md) | `--firmware mpy-module` | [500 kHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) | [25 MHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) |
| FPGA capture logic | iCE40UP5K on an FPGA breakout | [`rtl/`](../rtl/README.md) | -- | [250 kHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-rtl-framer-fpga4) | -- |
| a simulator | the host CPU | [`src/trace/`](../src/trace/) | -- | -- | -- |

## Verification

Source and Chip are as above, and Breakout the breakout on the demo board. Stream, Frame and Window are the three capture modes. Pixel-exact means every pixel matched a reference render. Clean means the pictures were whole and looked right. `--` means the source has no such mode.

| Source | Chip | Breakout | Stream | Frame | Window |
|---|---|---|---|---|---|
| the stock firmware | RP2350 | FPGA | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-direct-serial-fpga4) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-frame-capture-fpga4) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-frame-capture-fpga4) |
| the stock firmware | RP2350 | ASIC | [clean](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-pio-rle-tt09) | [clean](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-frame-capture-fpga4/README.md#real-silicon-tt09-at-its-design-clocks) | not checked |
| the stock firmware | RP2040 | ASIC | [clean](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-ttcap-capture-serial-tt07) | emulator only | emulator only |
| the vgacap firmware | RP2350 | FPGA | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4) |
| the vgacap firmware | RP2350 | ASIC | [clean](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-picosdk-firmware-fpga4/README.md#asic-board-tt09-silicon-with-asic_select) | [clean](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-picosdk-firmware-fpga4/README.md#asic-board-tt09-silicon-with-asic_select) | [clean](https://github.com/mithro/tt-vga-capture/blob/main/docs/results/2026-11-24-picosdk-firmware-fpga4/README.md#asic-board-tt09-silicon-with-asic_select) |
| the vgacap MicroPython module | RP2350 | FPGA | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1) |
| FPGA capture logic | iCE40UP5K | FPGA | [pixel-exact](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-rtl-framer-fpga4) | -- | -- |
| a Raspberry Pi 5 | RP1 | -- | [loopback only (#27)](https://github.com/mithro/vgacap/issues/27) | -- | -- |
| a simulator | -- | -- | [clean](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-sim-tt_um_vga_pattern) | -- | -- |

## Run-length capture

- Run-length capture on the stock firmware ([`ttcap capture --mode rle`](../python/ttcap/README.md)) was exact up to [25 MHz](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-11-24-pio-run-counting.md) in one-shot bursts (`--rle-oneshot`), with an FPGA breakout.
- With an ASIC breakout it [matched RAW capture at 100 kHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-pio-rle-tt09).
