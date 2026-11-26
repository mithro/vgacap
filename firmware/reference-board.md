---
type: reference
owner: vgacap maintainers
reader: someone looking up a pin, a USB identity, a buffer size or a clock limit of the vgacap firmware
review: 2027-05-27
---

# The vgacap firmware's GPIOs, USB identity, buffer and clocks

This page lists the fixed facts of the vgacap firmware on the Tiny Tapeout demo board v3 (RP2350B): the GPIOs it
uses, how it appears on USB, its capture buffer and its clock limits. It is for someone looking one of them up.
The commands that use them are on [The vgacap firmware protocol](PROTOCOL.md).

## GPIOs

GPIO is the RP2350B pin, and Signal the demo board net on it, from
[`firmware/core/fw_board.c`](core/fw_board.c). At boot is its state once the firmware starts, and Driven by the
command that changes it.

| GPIO | Signal | At boot | Driven by |
|---|---|---|---|
| 0 | `ctrl_ena` | as the bootloader left it | `asic_select` |
| 1 | `ctrl_sel_nrst` | driven high | `asic_select` |
| 2 | `ctrl_sel_inc` | as the bootloader left it | `asic_select` |
| 14 | `rst_n` | as the bootloader left it | `reset` |
| 16 | project clock | as the bootloader left it | `clock` |
| 17 to 24 | `ui_in[0]` to `ui_in[7]` | as the bootloader left them | `ui_in` |
| 33 to 40 | `uo_out[0]` to `uo_out[7]` | inputs | never: read by the PIO |
| 36 | `uo_out[3]`, vsync | input | never: waited on by the frame trigger |
| 40 | `uo_out[7]`, hsync | input | never: waited on by the frame trigger |

GPIO 1 is the iCE40's `CRESET_B` on an FPGA breakout, and the project mux's reset on an ASIC breakout. Low erases
the FPGA or resets the mux; only `asic_select` drives it low.

## PIO and DMA

Item names a resource a capture uses, and Value gives it.

| Item | Value |
|---|---|
| PIO blocks | 0 to 2 |
| State machines in a block | 0 to 3 |
| PIO GPIO window | based at GPIO 16, reaching GPIO 16 to 47 |
| Inputs | 8 pins from GPIO 33 |
| Samples in a FIFO word | 4 |
| DMA channels | 2, any free ones |
| DMA interrupt | `DMA_IRQ_0`, a shared handler |
| ttcap's PIO block | 1, unless `--pio` says otherwise |
| PIO block 0 through ttcap | refused, as for the stock firmware ([issue #44](https://github.com/mithro/vgacap/issues/44)) |
| PIO block 0 through the protocol | accepted |

## USB identity

Field names a USB descriptor field, from [`firmware/pico/usb_descriptors.c`](pico/usb_descriptors.c), and Value
gives it.

| Field | Value |
|---|---|
| Vendor ID | `0x2e8a` |
| Product ID | `0x0009` |
| Device release | `0x0100` |
| Manufacturer | `vgacap` |
| Product | `vgacap capture firmware` |
| Serial number | the chip's unique ID, 16 hexadecimal digits |
| Interface | one CDC ACM, named `vgacap capture` |
| Speed | full speed |
| Linux path | `/dev/serial/by-id/usb-vgacap_vgacap_capture_firmware_<serial>-if00` |
| Receive buffer | 256 bytes |
| Transmit buffer | 4096 bytes |

A udev rule that names the stock firmware's port, such as `/dev/ttboard` on the Welland bench, does not match
this port.

## Memory

Item names a part of the firmware's memory, and Value gives its size.

| Item | Value |
|---|---|
| Build type | `no_flash`: code, data and stacks in SRAM |
| Load address | `0x20000000` |
| UF2 family | `rp2350-arm-s`, `0xe48bff59` |
| Capture buffer (`arena_bytes`) | 450,560 bytes (440 KB), one static block |
| Largest `buf_words` | 56,320, two buffers of 225,280 bytes |
| Lines in one frame window | `arena_bytes` / `cpl`: 563 at `cpl=800` |
| 640x480@60 region | 481 lines of 800 samples, 384,800 bytes: one window |
| Frame buffers | 1 |
| Flash written | never |

## Clocks

Item names a clock limit, from [`firmware/core/fw_clock.c`](core/fw_clock.c), and Value gives it.

| Item | Value |
|---|---|
| Project clock accepted | 3 Hz to 125 MHz |
| System clock for a project clock up to 66.5 MHz | the stock firmware's choice, at most 133 MHz |
| System clock for a project clock above 66.5 MHz | the stock firmware's choice, at most twice the project clock and 250 MHz |
| Lowest system clock | 48 MHz |
| Core voltage | never changed |
| Fastest project clock run on a board | 25.175 MHz ([issue #32](https://github.com/mithro/vgacap/issues/32)) |
| Frame capture | needs 2 to 32 whole system clocks per project clock, within 0.5% |

Project clock is the clock asked for, System clock what the firmware sets, and Period the system clocks per
project clock. These are the clocks the stock firmware picked on fpga-4, which
[`tests/test_fw_units.c`](../tests/test_fw_units.c) holds the firmware to.

| Project clock | System clock | Period |
|---|---|---|
| 25 MHz | 100 MHz | 4 |
| 31.5 MHz | 126 MHz | 4 |
| 36 MHz | 72 MHz | 2 |
| 40 MHz | 80 MHz | 2 |
| 28.322 MHz | 113 MHz | 4 |
| 750 kHz | 132 MHz | 0 |
