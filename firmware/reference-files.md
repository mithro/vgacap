---
type: reference
owner: vgacap maintainers
reader: someone with a vgacap checkout looking for where a part of the firmware lives
review: 2027-05-27
---

# The vgacap firmware's source files

This page lists the files under [`firmware/`](README.md) and which build each goes into. It is for someone
changing the firmware or the MicroPython module, which shares its capture code. How to build each is on
[How to build the vgacap firmware](how-to-build.md) and
[How to test the vgacap firmware off hardware](how-to-run-tests.md).

Path is the file, and Contents what it holds. Firmware and Host say which builds take it in. Firmware is the
vgacap firmware, and Host the host build of the repository root: `vgacap-fw-sim` and the C tests. Which files
[the module firmware](../mpy/explanation-borrowing.md#the-module-firmware) builds in is on
[The vgacap module's source files](../mpy/reference-files.md). Issue names an open vgacap issue about the file,
such as [#45](https://github.com/mithro/vgacap/issues/45).

| Path | Contents | Firmware | Host | Issue |
|---|---|---|---|---|
| [`core/fw.c`](core/fw.c) | command loop and replies | yes | yes | none |
| [`core/fw_args.c`](core/fw_args.c) | `key=value` parser | yes | yes | none |
| [`core/fw_capture.c`](core/fw_capture.c) | stream and frame engines | yes | yes | none |
| [`core/fw_pio.c`](core/fw_pio.c) | PIO program builder | yes | yes | #45 |
| [`core/fw_clock.c`](core/fw_clock.c) | project clock planner | yes | yes | none |
| [`core/fw_board.c`](core/fw_board.c) | the board's GPIOs | yes | yes | none |
| [`core/fw_hal.h`](core/fw_hal.h) | what the core needs from the machine under it | yes | yes | none |
| [`pico/hal_rp2.c`](pico/hal_rp2.c) | PIO and DMA on the RP2350 | yes | no | none |
| [`pico/hal_pico.c`](pico/hal_pico.c) | the firmware's own half of the RP2350 HAL | yes | no | none |
| [`pico/main.c`](pico/main.c) | `main()` | yes | no | none |
| [`pico/usb_descriptors.c`](pico/usb_descriptors.c) | USB descriptors | yes | no | none |
| [`pico/tusb_config.h`](pico/tusb_config.h) | TinyUSB configuration | yes | no | none |
| [`pico/boards/tt_demoboard_v3.h`](pico/boards/tt_demoboard_v3.h) | pico-sdk board header | yes | no | none |
| [`pio/vgacap_stream.pio`](pio/vgacap_stream.pio) | streaming sampler source | checked | no | #45 |
| [`pio/vgacap_frame.pio`](pio/vgacap_frame.pio) | frame trigger source | checked | no | #45 |
| [`pio/vgacap_stream.hex`](pio/vgacap_stream.hex) | pioasm output that the tests read | checked | no | none |
| [`pio/vgacap_frame.hex`](pio/vgacap_frame.hex) | pioasm output that the tests read | checked | no | none |
| [`sim/hal_sim.c`](sim/hal_sim.c) | the simulated board | no | yes | none |
| [`sim/sim.h`](sim/sim.h) | the simulated board's controls | no | yes | none |
| [`sim/sim_main.c`](sim/sim_main.c) | `vgacap-fw-sim` | no | yes | none |
| [`CMakeLists.txt`](CMakeLists.txt) | the pico-sdk build | yes | no | none |
| [`pico_sdk_import.cmake`](pico_sdk_import.cmake) | pico-sdk's import script | yes | no | none |

`checked` means the firmware build assembles the file with pioasm, or compares pioasm's output with it, and
fails on a difference. [`src/stream/writer.c`](../src/stream/writer.c), the chunk writer, is built into both.
