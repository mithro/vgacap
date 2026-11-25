# vgacap capture firmware (pico-sdk)

A C firmware for the Tiny Tapeout demo board v3 (RP2350B) that captures a
design's VGA output on `uo_out` without MicroPython underneath. It does
what the MicroPython capture programs ([`python/ttcap/mp/`](../python/ttcap/mp/)) do: streaming
capture, whole-frame capture and windowed capture. The host drives it with
`ttcap capture --firmware picosdk` over the protocol in
[PROTOCOL.md](PROTOCOL.md), and the same tools read its [vgacap streams](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-09-15-stream-format.md) as
read the MicroPython ones.

It is built **no_flash**: the RP2350's bootloader loads the UF2 into SRAM
and runs it from there, and flash is never written. A reset, a power cycle
or `ttcap reboot` brings the board's stock firmware back.

The [hardware notes](#hardware-notes-and-assumptions) name the boards they
were measured on: `fpga-4` is a demo board with an FPGA breakout on the
shared Welland bench (see
[python/ttcap/README.md](../python/ttcap/README.md#reaching-a-welland-board)),
and `tt09` is a demo board with an ASIC breakout (a TT09 chip) at Welland.

## Layout

| path | contents | in the [`mpy/`](../mpy/README.md) rp2 build | in the `mpy/` unix build |
|---|---|---|---|
| [`core/fw.c`](core/fw.c) | command loop | no | no |
| [`core/fw_args.c`](core/fw_args.c) | argument parser | yes | yes |
| [`core/fw_capture.c`](core/fw_capture.c) | capture engines | yes | yes |
| [`core/fw_pio.c`](core/fw_pio.c) | PIO program builder | yes | yes |
| [`core/fw_clock.c`](core/fw_clock.c) | project clock plan | yes | yes |
| [`core/fw_board.c`](core/fw_board.c) | board pins | yes | yes |
| [`core/fw_hal.h`](core/fw_hal.h) | HAL contract | yes | yes |
| [`pico/hal_rp2.c`](pico/hal_rp2.c) | RP2350 HAL | yes | no |
| [`pico/hal_pico.c`](pico/hal_pico.c) | firmware-only HAL | no | no |
| [`pico/main.c`](pico/main.c) | `main()` | no | no |
| [`pico/usb_descriptors.c`](pico/usb_descriptors.c) | USB descriptors | no | no |
| [`pico/tusb_config.h`](pico/tusb_config.h) | TinyUSB configuration | no | no |
| [`pico/boards/tt_demoboard_v3.h`](pico/boards/tt_demoboard_v3.h) | board header | no | no |
| [`pio/`](pio/) | PIO programs | no | no |
| [`sim/hal_sim.c`](sim/hal_sim.c) | simulated board | no | yes |
| [`sim/sim_main.c`](sim/sim_main.c) | `vgacap-fw-sim` | no | no |
| [`CMakeLists.txt`](CMakeLists.txt) | pico-sdk firmware build | no | no |

The root [`CMakeLists.txt`](../CMakeLists.txt) builds `core/` and `sim/` for the host (no ARM
toolchain): `vgacap-fw-sim` and the C tests [`test_fw_units`](../tests/test_fw_units.c) and
[`test_fw_core`](../tests/test_fw_core.c). The Python tests in [`python/tests/test_fw_sim.py`](../python/tests/test_fw_sim.py) drive
`vgacap-fw-sim` through ttcap's firmware transport.

## Building

Toolchain (Debian/Ubuntu): `sudo apt-get install gcc-arm-none-eabi
libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib ninja-build`, plus a host C/C++
compiler and CMake for
[pioasm](https://github.com/raspberrypi/pico-sdk/tree/a1438dff1d38bd9c65dbd693f0e5db4b9ae91779/tools/pioasm)
and picotool, which the SDK builds itself.

The SDK is pinned to [**pico-sdk 2.2.0**](https://github.com/raspberrypi/pico-sdk/releases/tag/2.2.0), commit
[`a1438dff1d38bd9c65dbd693f0e5db4b9ae91779`](https://github.com/raspberrypi/pico-sdk/tree/a1438dff1d38bd9c65dbd693f0e5db4b9ae91779), with `lib/tinyusb` at its
submodule pin [`86ad6e56c1700e85f1c5678607a762cfe3aa2f47`](https://github.com/hathach/tinyusb/tree/86ad6e56c1700e85f1c5678607a762cfe3aa2f47); the build refuses
any other SDK version.

    git clone --branch 2.2.0 --depth 1 https://github.com/raspberrypi/pico-sdk.git pico-sdk
    git -C pico-sdk submodule update --init --depth 1 lib/tinyusb
    cmake -S firmware -B firmware/build -G Ninja -DPICO_SDK_PATH=$PWD/pico-sdk
    cmake --build firmware/build

The result is `firmware/build/vgacap_fw.uf2` (family `rp2350-arm-s`,
loaded at `0x20000000`). The first configure fetches [picotool](https://github.com/raspberrypi/picotool) 2.2.0 and
builds it; `-DPICOTOOL_FETCH_FROM_GIT_PATH=<dir>` shares that between
builds.

The build also assembles `pio/*.pio` with pioasm and fails if the output
differs from the checked-in `pio/*.hex` (which the host tests read). After
changing a `.pio` file, copy `firmware/build/<name>.hex` into `pio/`.

Our sources compile with `-Wall -Wextra -Werror` (the SDK's are not ours
to hold to that); the host build of `core/` and `sim/` adds `-Wshadow
-Wconversion`.

## Hardware notes and assumptions

* **The board is an RP2350B**, which is why the board header is not
  `pico2` (an RP2350A, GPIO 0..29): `uo_out` is GPIO 33..40, the project
  clock GPIO 16, `rst_n` GPIO 14, `ui_in` GPIO 17..24. Captures use a PIO
  block whose GPIO window is moved to 16 (PIO1 by default, as on the
  MicroPython path; with nothing else loaded any block works).
* **The firmware cannot select a design.** It runs instead of the stock
  firmware, and the stock firmware is what knows the shuttle and loads FPGA
  bitstreams. So the design has to be enabled with the stock firmware
  first and still be there after the switch. With an FPGA breakout, that
  relies on the iCE40 keeping its configuration while the RP2350 resets
  into its bootloader and then into this firmware.
* **Verified on fpga-4 (2026-11-24)**: after BOOTSEL and this firmware,
  frame, window and streaming captures of `tt_um_vgacal_bars` were
  pixel-exact, so the iCE40 kept its configuration (tt-vga-capture
  [`docs/results/2026-11-24-picosdk-firmware-fpga4/`](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4)).
* GPIO 1 (`ctrl_sel_nrst`) is the iCE40's `CRESET_B` on every Tiny Tapeout
  FPGA breakout, and the mux controller's reset on an ASIC breakout; low
  erases the FPGA or resets the mux.
  - The stock firmware leaves it driven high once a design is enabled
    ([`fabricfoxv2.py`](https://github.com/TinyTapeout/tt-micropython-firmware/blob/15ba5ced7aae7746ea2f9004cb412109c9fe9c83/src/ttboard/fpga/fabricfoxv2.py)
    raises `CRESET_B` and keeps it there after loading a
    bitstream;
    [`ProjectMux.reset()`](https://github.com/TinyTapeout/tt-micropython-firmware/blob/15ba5ced7aae7746ea2f9004cb412109c9fe9c83/src/ttboard/project_mux.py)
    ends with `ncrst(1)`).
  - While the RP2350 is in reset and in its bootloader it is an input with
    the pad's default pull-down, as in the stock firmware's own boot
    ([`DemoboardDetect.rp_all_inputs()`](https://github.com/TinyTapeout/tt-micropython-firmware/blob/15ba5ced7aae7746ea2f9004cb412109c9fe9c83/src/ttboard/boot/demoboard_detect.py)).
    The FPGA surviving the switch means the board's pull-up wins there, as
    it did on fpga-4.
  - **This firmware drives GPIO 1 high first thing at boot**
    ([`hal_pico_init()`](pico/hal_pico.c)), level before direction so it
    never glitches low. That is the level the stock firmware left, so
    nothing then depends on the pull-up, and on an ASIC breakout it only
    holds the mux out of reset, the stock firmware's idle level. Only
    `asic_select` ever drives it low.
  - GPIO 0 (`ctrl_ena`) and GPIO 2 (`ctrl_sel_inc`) are not driven until
    an `asic_select`.
* **ASIC breakouts lose their project across the switch.** On tt09
  (2026-11-24) the stock firmware's frame capture of
  `tt_um_rejunity_vga_logo` was clean, but this firmware's, after the
  switch, saw no sync at all. Nothing drives the mux lines while the
  RP2350 is in reset and in its bootloader (pad pull-downs), so the mux
  comes back with nothing selected, and driving GPIO 1 high at boot does
  not select anything. So this firmware selects the project itself with
  `asic_select index=N`, using the stock SDK's sequence
  ([PROTOCOL.md](PROTOCOL.md)); `ttcap capture --firmware picosdk
  --asic-index N` sends it. N is `tt.shuttle.<name>.count` under the stock
  firmware.
* The firmware cannot tell an FPGA breakout from an ASIC breakout, so
  `ttcap` refuses `--asic-index` for a bench demo board with an FPGA
  breakout, and when these disagree about which board it is:
  - `--board` (`ttcap capture --board SLUG`);
  - a `ws://` or `wss://` link: the bridge's address, the Pi's name, or a
    loopback address (127.0.0.0/8, `localhost`, `::1`) on a tunnel's
    `18000 + octet` port, which names the tunnel's board;
  - for a `serial:` link, or a loopback link on any other port (the Pi's
    own bridge), the Pi it runs on.

  The refusal names the boards with an ASIC breakout in the board table
  that can run this firmware (RP2350 only), or says that none is listed.
  [`ttcap.firmware.setup()`](../python/ttcap/firmware.py) called directly
  does not check; its caller must
  ([`ttcap.demo.check_asic_index()`](../python/ttcap/demo.py)).
* `ui_in` and `rst_n` are left untouched until `ui_in` / `reset` commands
  drive them; `ttcap` holds reset while it programs the clock and releases
  it last, as [`select_project()`](../python/ttcap/capture.py) does.
* The project clock comes from the PWM on GPIO 16, with the system clock
  set to what the stock firmware picks for that project clock. That is a
  whole multiple of the project clock whenever one exists (100 MHz for
  25 MHz), which frame capture's phase-locked sampler needs.
* Project clocks above 66.5 MHz raise the system clock to twice the
  project clock (up to 250 MHz), as the stock firmware does; like
  MicroPython's `machine.freq`, nothing raises the
  core voltage for that. No project clock above 66.5 MHz has been tried on
  a board.
* The capture buffer is a 440 KB static array: a whole 640x480@60 picture
  region (481 lines of 800 samples) is one capture; 800x600 takes two
  windows per picture.
* USB is a CDC ACM port, VID:PID `2e8a:0009`, manufacturer `vgacap`,
  product `vgacap capture firmware`, serial number the chip's unique ID:
  `/dev/serial/by-id/usb-vgacap_vgacap_capture_firmware_<id>-if00`. A udev
  rule that names the stock firmware's port (`/dev/ttboard` on the
  [fpgas.online](https://tinytapeout.fpgas.online) Pis) will not match it.

## PIO programs

[`pio/vgacap_stream.pio`](pio/vgacap_stream.pio) and [`pio/vgacap_frame.pio`](pio/vgacap_frame.pio) are the programs'
readable sources, written at the default operands of the demo board's
RP2350. The operands of one capture (edge, sync polarities, sample delay,
system clocks per sample) are only known when it starts, so [`core/fw_pio.c`](core/fw_pio.c)
builds the loaded words at run time, instruction for instruction in the
same order as the `.pio` source:

| `.pio` source | MicroPython file | MicroPython function | `fw_pio.c` function |
|---|---|---|---|
| [`vgacap_stream.pio`](pio/vgacap_stream.pio) | [`capture_rp2.py`](../python/ttcap/mp/capture_rp2.py) | `make_sampler()` | `fw_pio_sampler()` |
| [`vgacap_frame.pio`](pio/vgacap_frame.pio) | [`frame_rp2.py`](../python/ttcap/mp/frame_rp2.py) | `make_trigger()` | `fw_pio_frame()` |

The tests hold `fw_pio.c` to both: to pioasm's words for the `.pio` files
(through the checked-in `.hex`) and to MicroPython's `rp2.asm_pio` words
for every operand combination they try, configuration registers included
([`python/tests/test_fw_sim.py`](../python/tests/test_fw_sim.py), [`tests/test_fw_units.c`](../tests/test_fw_units.c)). A MicroPython
program could load the `.pio` files' words the same way.

## Off-hardware tests

    cmake -S . -B build && cmake --build build && ctest --test-dir build
    cd python && uv run pytest -q tests/test_fw_sim.py

* `test_fw_units`: argument parsing; the clock plans against the clocks
  the stock firmware was measured to pick on fpga-4 (25 MHz -> 100 MHz,
  36 MHz -> 72 MHz, 28.322 MHz -> 113 MHz (28.25 MHz project clock), ...); the PIO words.
* `test_fw_core`: every command's exact reply, and the engines' exact
  chunks on the simulated board: max_bytes, overruns (cumulative, and a
  chunk torn while it is being sent, reported straight after it), stop
  bytes, a host that drops DTR, frame windows, the arena limit, chained
  buffers (the MicroPython module's), refusals.
* `test_fw_sim.py`: the transport end to end against `vgacap-fw-sim`, with
  a synthetic VGA design: byte-exact streams, pixel-exact frames and
  windows through `vgacap-frames`, stops, overruns, the CLI, and the same
  FRAM chunks as `frame_rp2.py` on `ttcap.emu`.

The simulator models what the PIO and DMA deliver, not their cycles; see
[`sim/sim.h`](sim/sim.h) for what that leaves out.

## Running it on a board

These steps run on the Raspberry Pi the board is attached to (on the
fpgas.online bench, the board's own Pi), with this repository checked out
there as in
[Running on a Raspberry Pi](../python/ttcap/README.md#running-on-a-raspberry-pi)
and the UF2 copied there. `<dev>` is the stock firmware's serial port
(`/dev/ttboard` on the fpgas.online Pis).

1. With the stock firmware, enable the design to capture (the fpgas.online
   bridge (`fpgas-tt`)'s `/designs/<name>/enable`, or `ttcap capture`'s
   `--design`), and take a reference capture the usual way if wanted.
2. Stop anything else that owns the port, such as the bridge
   (`sudo systemctl stop fpgas-tt`, after checking it has no clients).
3. Into the bootloader:
   `uv run --no-sync ttcap reboot serial:<dev> --to bootsel`. The board
   reappears as a USB drive labeled `RP2350`.
4. Load the firmware:

       sudo mkdir -p /mnt/rp2350
       sudo mount /dev/disk/by-label/RP2350 /mnt/rp2350
       sudo cp vgacap_fw.uf2 /mnt/rp2350/ && sync
       sudo umount /mnt/rp2350    # may report the drive already gone

   The drive disappears and the vgacap firmware's serial port appears.
5. Capture, e.g.

       FW=$(ls /dev/serial/by-id/usb-vgacap_vgacap_capture_firmware_*-if00)
       uv run --no-sync ttcap capture --firmware picosdk serial:$FW --clock-hz 25000000 --mode frame --frames 2 --out frame.vgacap
       uv run --no-sync ttcap capture --firmware picosdk serial:$FW --clock-hz 25000000 --mode window --frames 1 --out window.vgacap
       uv run --no-sync ttcap capture --firmware picosdk serial:$FW --clock-hz 750000 --seconds 4 --out stream.vgacap

6. Back to the stock firmware: `uv run --no-sync ttcap reboot serial:$FW`
   (or reset / power cycle the board), then restart the bridge.
   `ttcap reboot` asks the board which firmware it runs (the capture
   firmware's `hello`, then the raw REPL); `--firmware picosdk` skips the
   question.
