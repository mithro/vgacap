---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who changed the MicroPython module and wants to know it builds for the board
review: 2027-05-27
---

# How to compile the module into MicroPython's rp2 port

**You changed the vgacap MicroPython module and want to know it compiles for the demo board's RP2350B, as CI
checks.**

This is the `mpy-module` job's rp2 step in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml): MicroPython's
rp2 port with the module, for the board definition the demo boards use. The result has no Tiny Tapeout SDK in it,
so it is a compile check, not a firmware for a board. That one is on
[How to build the module firmware](how-to-build-release.md).

## What you need

- The MicroPython clone in `build/micropython` that `mpy/build-unix.sh` makes, from
  [How to build and test the module on MicroPython's unix port](how-to-test-unix.md).
- The ARM toolchain; on Debian or Ubuntu, `sudo apt-get install gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib`.
- CMake, GNU make and network access, for the board definition and the port's submodules.

## Steps

1. At the checkout's root, clone the board definition [`psychogenic/tinytapeout_rp2350b`](https://github.com/psychogenic/tinytapeout_rp2350b) at the commit CI uses, and copy it into the port, which adds `ports/rp2/boards/tt_rp2350_board`.

   ```console
   $ git clone -q https://github.com/psychogenic/tinytapeout_rp2350b.git build/tt_rp2350_board
   $ git -C build/tt_rp2350_board checkout -q 596f456
   $ cp -r build/tt_rp2350_board build/micropython/ports/rp2/boards/tt_rp2350_board
   ```

2. At the checkout's root, fetch the rp2 port's submodules, which include pico-sdk and TinyUSB.

   ```console
   $ make -C build/micropython/ports/rp2 BOARD=tt_rp2350_board submodules
   ```

3. At the checkout's root, build the port with the module, whose own sources compile with `-Werror`.

   ```console
   $ make -C build/micropython/ports/rp2 -j"$(nproc)" BOARD=tt_rp2350_board USER_C_MODULES="$PWD/mpy/micropython.cmake"
   ```

## Check

Step 3 ends without an error, and `build/micropython/ports/rp2/build-tt_rp2350_board/firmware.uf2` exists.

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `No such file or directory` for `build/micropython` at step 1 | the unix build has not run | Run `mpy/build-unix.sh` first |
| `cp` makes `tt_rp2350_board/tt_rp2350_board` | step 1 ran twice | Remove `build/micropython/ports/rp2/boards/tt_rp2350_board` and copy again |
| A warning in a vgacap source stops step 3 | the module's sources build with `-Wall -Wextra -Werror` | Fix the warning |
| `arm-none-eabi-gcc: not found` at step 3 | no ARM toolchain | Install it, as under What you need |

## Next

- [How to build the module firmware](how-to-build-release.md)
- [The vgacap module's source files](reference-files.md)
