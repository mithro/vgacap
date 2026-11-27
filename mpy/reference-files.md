---
type: reference
owner: vgacap maintainers
reader: someone with a vgacap checkout looking for where a part of the MicroPython module lives
review: 2027-05-27
---

# The vgacap module's source files

This page lists the files under [`mpy/`](README.md), and the shared firmware files each MicroPython port builds
in with them. It is for someone changing the module. The firmware's own files are on
[The vgacap firmware's source files](../firmware/reference-files.md).

File is the file, and Contents what it holds. Port is the MicroPython port that builds it, and Board half the
file that supplies the PIO and DMA under the engines in that port.

| File | Contents | Port | Board half |
|---|---|---|---|
| [`modvgacap.c`](modvgacap.c) | the Python binding | rp2 and unix | none |
| [`hal_mpy.c`](hal_mpy.c) | the host link over MicroPython streams, idling and taking a state machine | rp2 and unix | none |
| [`hal_mpy.h`](hal_mpy.h) | the link's begin and end calls | rp2 and unix | none |
| [`micropython.cmake`](micropython.cmake) | build rules for CMake ports | rp2 | [`firmware/pico/hal_rp2.c`](../firmware/pico/hal_rp2.c) |
| [`micropython.mk`](micropython.mk) | build rules for make ports | unix | [`firmware/sim/hal_sim.c`](../firmware/sim/hal_sim.c) |
| [`build-unix.sh`](build-unix.sh) | clones MicroPython at the pinned commit and builds the unix port | unix | none |

Both ports also build the engines and their helpers from
[`firmware/core/`](../firmware/core/): `fw_capture.c`, `fw_args.c`, `fw_board.c`, `fw_clock.c` and `fw_pio.c`,
and the chunk writer [`src/stream/writer.c`](../src/stream/writer.c). Neither builds `fw.c`, the firmware's
command loop.

Setting names a compile definition, and rp2 and unix give its value in each port.

| Setting | rp2 | unix |
|---|---|---|
| `VGACAP_MPY_SIM` | 0 | 1 |
| `HAL_RP2_DMA_IRQ_INDEX` | 1 | not used |
| `VGACAP_SIM_NO_LINK` | not used | 1 |
| Warnings on the module's sources | `-Wall -Wextra -Wno-unused-parameter -Werror` | the port's own |
