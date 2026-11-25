# vgacap capture firmware (pico-sdk)

A C firmware for the Tiny Tapeout demo board v3 (RP2350B) that does what
the MicroPython capture programs (`python/ttcap/mp/`) do -- streaming
capture, whole-frame capture and windowed capture of a design's VGA output
on `uo_out` -- without MicroPython underneath. The host drives it with
`ttcap capture --firmware picosdk` over the protocol in
[PROTOCOL.md](PROTOCOL.md), and the streams it produces are read by the
same tools as the MicroPython ones.

It is built **no_flash**: the UF2 is loaded into SRAM by the RP2350's
bootloader and runs from there, and flash is never written. A reset, a
power cycle or `ttcap reboot` brings the board's stock
firmware back.

## Layout

| Path | What |
|---|---|
| `core/` | Portable C, no pico-sdk: the command loop and argument parser (`fw.c`, `fw_args.c`), the stream and frame engines (`fw_capture.c`, which the MicroPython module in `../mpy/` drives too), the PIO program builder (`fw_pio.c`), the project clock plan (`fw_clock.c`), the board's pins (`fw_board.c`), and the HAL contract (`fw_hal.h`). |
| `pico/` | The pico-sdk HAL: the capture half shared with the MicroPython module (`hal_rp2.c`: PIO, DMA) and the firmware's own half (`hal_pico.c`: USB, clock, reset, ui_in, reboot); USB descriptors, TinyUSB config, `main.c`, and the board header `boards/tt_demoboard_v3.h`. |
| `pio/` | The PIO programs' readable sources and pioasm's output for them. |
| `sim/` | The HAL on a host: a simulated board (`hal_sim.c`) and `vgacap-fw-sim`, the firmware speaking the protocol on stdin/stdout. |
| `CMakeLists.txt` | The firmware build: a pico-sdk project of its own. |

The root `CMakeLists.txt` builds `core/` and `sim/` for the host (no ARM
toolchain): `vgacap-fw-sim` and the C tests `test_fw_units` and
`test_fw_core`. The Python tests in `python/tests/test_fw_sim.py` drive
`vgacap-fw-sim` through ttcap's firmware transport.

## Building

Toolchain (Debian/Ubuntu): `sudo apt-get install gcc-arm-none-eabi
libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib`, plus a host C/C++
compiler and CMake for pioasm and picotool, which the SDK builds itself.

The SDK is pinned to **pico-sdk 2.2.0**, commit
`a1438dff1d38bd9c65dbd693f0e5db4b9ae91779`, with `lib/tinyusb` at its
submodule pin `86ad6e56c1700e85f1c5678607a762cfe3aa2f47`; the build refuses
any other SDK version.

    git clone --branch 2.2.0 --depth 1 https://github.com/raspberrypi/pico-sdk.git pico-sdk
    git -C pico-sdk submodule update --init --depth 1 lib/tinyusb
    cmake -S firmware -B firmware/build -G Ninja -DPICO_SDK_PATH=$PWD/pico-sdk
    cmake --build firmware/build

The result is `firmware/build/vgacap_fw.uf2` (family `rp2350-arm-s`,
loaded at `0x20000000`). The first configure fetches picotool 2.2.0 and
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
  firmware, which is what knows the shuttle and loads FPGA bitstreams, so
  the design has to be enabled with the stock firmware first and still be
  there after the switch. On the FPGA boards that relies on the iCE40
  keeping its configuration while the RP2350 resets into its bootloader
  and into this firmware. **Verified on fpga-4 (2026-11-24)**: after
  BOOTSEL and this firmware, frame, window and streaming captures of
  `tt_um_vgacal_bars` were pixel-exact, so the iCE40 kept its configuration
  (tt-vga-capture `docs/results/2026-11-24-picosdk-firmware-fpga4/`).
  GPIO 1 (`ctrl_sel_nrst`) is the iCE40's `CRESET_B` there, and the mux
  controller's reset on an ASIC carrier; low erases the FPGA or resets the
  mux. The stock firmware leaves it driven high once a design is enabled
  (`fabricfoxv2.py` raises `CRESET_B` and keeps it there after loading a
  bitstream; `ProjectMux.reset()` ends with `ncrst(1)`). While the RP2350
  is in reset and in its bootloader it is an input with the pad's default
  pull-down (the stock firmware's own boot does the same,
  `DemoboardDetect.rp_all_inputs()`), so the FPGA surviving the switch
  means the board's pull-up wins there, as it did on fpga-4. **This
  firmware drives GPIO 1 high first thing at boot** (`hal_pico_init()`),
  level before direction so it never glitches low: the level the stock
  firmware left, so nothing then depends on the pull-up. Only
  `asic_select` ever drives it low. On an ASIC carrier that only holds
  the mux out of reset, the stock firmware's idle level. GPIO 0
  (`ctrl_ena`) and GPIO 2 (`ctrl_sel_inc`) are not driven until an
  `asic_select`.
* **ASIC boards lose their project across the switch.** On tt09
  (2026-11-24) the stock firmware's frame capture of
  `tt_um_rejunity_vga_logo` was clean, and this firmware's, after the
  switch, saw no sync at all: nothing drives the mux lines while the
  RP2350 is in reset and in its bootloader (pad pull-downs), so the mux
  comes back with nothing selected, and driving GPIO 1 high at boot does
  not select anything. So this firmware selects the
  project itself with `asic_select index=N`, the stock SDK's sequence
  (PROTOCOL.md); `ttcap capture --firmware picosdk --asic-index N` sends
  it. N is `tt.shuttle.<name>.count` under the stock firmware. The
  firmware cannot tell an FPGA breakout from an ASIC carrier, so `ttcap`
  refuses `--asic-index` for an FPGA bench board, named by `--board`, by
  a `ws://` link (the bridge's address, the Pi's name or a tunnel's
  `18000 + octet` port) or by the Pi it runs on (for a `serial:` link),
  and when those disagree. `ttcap.firmware.setup()` called directly does
  not check: its caller must (`ttcap.demo.check_asic_index()`).
* `ui_in` and `rst_n` are left untouched until `ui_in` / `reset` commands
  drive them; `ttcap` holds reset while it programs the clock and releases
  it last, as `select_project()` does.
* The project clock comes from the PWM on GPIO 16, at the system clock the
  stock firmware picks for that clock (a whole multiple of it whenever one
  exists: 100 MHz for 25 MHz), which frame capture's phase-locked sampler
  needs.
* Project clocks above 66.5 MHz raise the system clock to twice the
  project clock (up to 250 MHz), as the stock firmware does; like
  MicroPython's `machine.freq` (checked at b006887db6), nothing raises the
  core voltage for that. Only 100 MHz has been used on the boards so far.
* The capture buffer is a 440 KB static array: a whole 640x480@60 picture
  region (481 lines of 800 samples) is one capture; 800x600 takes two
  windows per picture.
* USB is a CDC ACM port, VID:PID `2e8a:0009`, manufacturer `vgacap`,
  product `vgacap capture firmware`, serial number the chip's unique ID:
  `/dev/serial/by-id/usb-vgacap_vgacap_capture_firmware_<id>-if00`. A udev
  rule that names the stock firmware's port (`/dev/ttboard` on the
  fpgas.online Pis) will not match it.

## PIO programs

`pio/vgacap_stream.pio` and `pio/vgacap_frame.pio` are the programs'
readable sources, written at the RP2350 board's default operands. The
operands of one capture (edge, sync polarities, sample delay, system
clocks per sample) are only known when it starts, so the words the
firmware loads are built by `core/fw_pio.c` in the same order. One
instruction for one instruction:

| `.pio` | MicroPython (`python/ttcap/mp/`) | `core/fw_pio.c` |
|---|---|---|
| `vgacap_stream.pio` | `capture_rp2.py` `make_sampler()` | `fw_pio_sampler()` |
| `vgacap_frame.pio` | `frame_rp2.py` `make_trigger()` | `fw_pio_frame()` |

The tests hold `fw_pio.c` to both: to pioasm's words for the `.pio` files
(through the checked-in `.hex`) and to MicroPython's `rp2.asm_pio` words
for every operand combination they try, configuration registers included
(`python/tests/test_fw_sim.py`, `tests/test_fw_units.c`). A MicroPython
program could load the `.pio` files' words the same way.

## Off-hardware tests

    cmake -S . -B build -G Ninja && cmake --build build && (cd build && ctest)
    cd python && uv run pytest -q tests/test_fw_sim.py

* `test_fw_units`: argument parsing; the clock plans against the clocks
  the stock firmware was measured to pick on fpga-4 (25 MHz -> 100 MHz,
  36 MHz -> 72 MHz, 28.322 MHz -> 113 MHz = 28.25 MHz, ...); the PIO words.
* `test_fw_core`: every command's exact reply, and the engines' exact
  chunks on the simulated board: max_bytes, overruns (cumulative, and a
  chunk torn while it is being sent reported straight after it), stop
  bytes, a host that drops DTR, frame windows, the arena limit, rxstall
  reports, refusals.
* `test_fw_sim.py`: the transport end to end against `vgacap-fw-sim`, with
  a synthetic VGA design: byte-exact streams, pixel-exact frames and
  windows through `vgacap-frames`, stops, overruns, the CLI, and the same
  FRAM chunks as `frame_rp2.py` on `ttcap.emu`.

The simulator models what the PIO and DMA deliver, not their cycles; see
`sim/sim.h` for what that leaves out.

## Running it on a board

The steps, on the board's own Pi, with `ttcap` from this repository and the
UF2 copied there. `<dev>` is the stock firmware's port (`/dev/ttboard` on the
fpgas.online Pis).

1. With the stock firmware, enable the design to capture (the fpgas-tt
   daemon's `/designs/<name>/enable`, or `ttcap capture --design`), and
   take a reference capture the usual way if wanted.
2. Stop anything else that owns the port (`sudo systemctl stop fpgas-tt`,
   after checking the bridge has no clients).
3. Into the bootloader: `ttcap reboot serial:<dev> --to bootsel`. The board
   reappears as a USB drive labelled `RP2350`.
4. Load the firmware:

       sudo mkdir -p /mnt/rp2350
       sudo mount /dev/disk/by-label/RP2350 /mnt/rp2350
       sudo cp vgacap_fw.uf2 /mnt/rp2350/ && sync
       sudo umount /mnt/rp2350    # may report the drive already gone

   The drive disappears and the serial port above appears.
5. Capture, e.g.

       FW=$(ls /dev/serial/by-id/usb-vgacap_vgacap_capture_firmware_*-if00)
       ttcap capture --firmware picosdk serial:$FW --clock-hz 25000000 --mode frame --frames 2 --out frame.vgacap
       ttcap capture --firmware picosdk serial:$FW --clock-hz 25000000 --mode window --frames 1 --out window.vgacap
       ttcap capture --firmware picosdk serial:$FW --clock-hz 750000 --seconds 4 --out stream.vgacap
       ttcap png frame.vgacap frame

6. Back to the stock firmware: `ttcap reboot serial:$FW` (or reset / power
   cycle the board), then restart `fpgas-tt`. `ttcap reboot` asks the board
   which firmware it runs (the capture firmware's `hello`, then the raw
   REPL); `--firmware picosdk` skips the question.
