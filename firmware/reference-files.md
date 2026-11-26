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

Path is the file, and Contents what it holds. Firmware, Host, Module rp2 and Module unix say whether it is built
into the firmware, the host build of the repository root (`vgacap-fw-sim` and the C tests), the
[MicroPython module](../mpy/README.md) for the board, and the module on MicroPython's unix port.

| Path | Contents | Firmware | Host | Module rp2 | Module unix |
|---|---|---|---|---|---|
| [`core/fw.c`](core/fw.c) | command loop and replies | yes | yes | no | no |
| [`core/fw_args.c`](core/fw_args.c) | `key=value` parser | yes | yes | yes | yes |
| [`core/fw_capture.c`](core/fw_capture.c) | stream and frame engines | yes | yes | yes | yes |
| [`core/fw_pio.c`](core/fw_pio.c) | PIO program builder | yes | yes | yes | yes |
| [`core/fw_clock.c`](core/fw_clock.c) | project clock planner | yes | yes | yes | yes |
| [`core/fw_board.c`](core/fw_board.c) | the board's GPIOs | yes | yes | yes | yes |
| [`core/fw_hal.h`](core/fw_hal.h) | what the core needs from the machine under it | yes | yes | yes | yes |
| [`pico/hal_rp2.c`](pico/hal_rp2.c) | PIO and DMA on the RP2350 | yes | no | yes | no |
| [`pico/hal_pico.c`](pico/hal_pico.c) | USB link, clock, reset, `ui_in`, reboot, buffer, boot | yes | no | no | no |
| [`pico/main.c`](pico/main.c) | `main()` | yes | no | no | no |
| [`pico/usb_descriptors.c`](pico/usb_descriptors.c) | USB descriptors | yes | no | no | no |
| [`pico/tusb_config.h`](pico/tusb_config.h) | [TinyUSB](https://github.com/hathach/tinyusb) configuration | yes | no | no | no |
| [`pico/boards/tt_demoboard_v3.h`](pico/boards/tt_demoboard_v3.h) | pico-sdk board header | yes | no | no | no |
| [`pio/vgacap_stream.pio`](pio/vgacap_stream.pio) | streaming sampler source | checked | no | no | no |
| [`pio/vgacap_frame.pio`](pio/vgacap_frame.pio) | frame trigger source | checked | no | no | no |
| [`pio/vgacap_stream.hex`](pio/vgacap_stream.hex) | pioasm output, read by the tests | checked | no | no | no |
| [`pio/vgacap_frame.hex`](pio/vgacap_frame.hex) | pioasm output, read by the tests | checked | no | no | no |
| [`sim/hal_sim.c`](sim/hal_sim.c) | the simulated board | no | yes | no | yes |
| [`sim/sim.h`](sim/sim.h) | the simulated board's controls | no | yes | no | yes |
| [`sim/sim_main.c`](sim/sim_main.c) | `vgacap-fw-sim` | no | yes | no | no |
| [`CMakeLists.txt`](CMakeLists.txt) | the pico-sdk build | yes | no | no | no |
| [`pico_sdk_import.cmake`](pico_sdk_import.cmake) | pico-sdk's import script | yes | no | no | no |

`checked` means the firmware build assembles the file with pioasm, or compares pioasm's output with it, and
fails on a difference. [`src/stream/writer.c`](../src/stream/writer.c), the chunk writer, is built into all four.
