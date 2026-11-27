---
type: how-to
owner: vgacap maintainers
reader: someone who wants the module firmware built from their own vgacap checkout
review: 2027-05-27
---

# How to build the module firmware

**You want [the module firmware](explanation-borrowing.md#the-module-firmware), built
from your vgacap checkout.**

`ttcap capture --firmware mpy-module` needs both the module and the SDK's `ttboard` package in the firmware.
This page follows the release workflow of the
[`vgacap-module` branch of mithro/tt-micropython-firmware](https://github.com/mithro/tt-micropython-firmware/tree/vgacap-module),
which publishes the same build as its
[`dev-vgacap-module` pre-release](https://github.com/mithro/tt-micropython-firmware/releases/tag/dev-vgacap-module).
That workflow also packs a filesystem image into its UF2. This page builds the firmware alone, which is all
[How to flash the module firmware and restore the board's own](how-to-flash.md) writes.

## What you need

- A vgacap checkout, and `VGACAP`, its absolute path.
- The ARM toolchain; on Debian or Ubuntu, `sudo apt-get install gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib`.
- CMake, GNU make, git and network access.
- An empty working directory outside the checkout.

## Steps

1. In the working directory, clone the SDK's `vgacap-module` branch with its `microcotb` submodule, which gives `tt-fw/src/ttboard`.

   ```console
   $ git clone -q --branch vgacap-module https://github.com/mithro/tt-micropython-firmware.git tt-fw
   $ git -C tt-fw submodule update --init microcotb
   ```

2. In the working directory, clone MicroPython at the commit the demo boards run, `b006887db6`.

   ```console
   $ git clone -q --filter=blob:none https://github.com/micropython/micropython.git micropython
   $ git -C micropython checkout -q b006887db6e4b18bce967ea87c7e63398f60b296
   ```

3. In the working directory, add the board definition at commit `596f456`, which gives `micropython/ports/rp2/boards/tt_rp2350_board`.

   ```console
   $ git clone -q https://github.com/psychogenic/tinytapeout_rp2350b.git tt_rp2350_board
   $ git -C tt_rp2350_board checkout -q 596f456
   $ cp -r tt_rp2350_board micropython/ports/rp2/boards/
   ```

4. In the working directory, copy the SDK's two packages into the port's `modules`, which the port's manifest freezes into the firmware.

   ```console
   $ mkdir -p micropython/ports/rp2/modules/ttboard micropython/ports/rp2/modules/microcotb
   $ cp -r tt-fw/src/ttboard/* micropython/ports/rp2/modules/ttboard/
   $ cp -r tt-fw/microcotb/src/microcotb/* micropython/ports/rp2/modules/microcotb/
   ```

5. In the working directory, build `mpy-cross` and fetch the rp2 port's submodules, which gives `micropython/mpy-cross/build/mpy-cross` and `micropython/lib/pico-sdk`.

   ```console
   $ make -C micropython/mpy-cross -j"$(nproc)"
   $ make -C micropython/ports/rp2 BOARD=tt_rp2350_board submodules
   ```

6. In the working directory, build the port with your checkout's module, which writes `micropython/ports/rp2/build-tt_rp2350_board/firmware.uf2`.

   ```console
   $ make -C micropython/ports/rp2 -j"$(nproc)" BOARD=tt_rp2350_board USER_C_MODULES="$VGACAP/mpy/micropython.cmake"
   ```

## Check

The UF2 exists and holds the module and both packages. The two counts are the frozen `.py` files of each package,
and the last command prints `1`:

```console
$ ls micropython/ports/rp2/build-tt_rp2350_board/firmware.uf2
$ grep -o 'ttboard/[a-z_/]*\.py' micropython/ports/rp2/build-tt_rp2350_board/frozen_content.c | sort -u | wc -l
$ grep -o 'microcotb/[a-z_/]*\.py' micropython/ports/rp2/build-tt_rp2350_board/frozen_content.c | sort -u | wc -l
$ grep -c 'MP_QSTR_vgacap\b' micropython/ports/rp2/build-tt_rp2350_board/genhdr/qstrdefs.generated.h
```

The UF2's blocks start at `0x10000000` and end below `0x100C8000`, where the board's filesystem starts.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| Check | Both counts are 0 | step 4 did not run, or ran before step 2 | Run step 4, then step 6 again after `make -C micropython/ports/rp2 BOARD=tt_rp2350_board clean` |
| Check | The last command prints 0 | `USER_C_MODULES` is missing or names a wrong path | Run step 6 with `VGACAP` set to the checkout's absolute path |
| 6 | A warning in a vgacap source stops it | the module's sources build with `-Wall -Wextra -Werror` | Fix the warning |
| 6 | `arm-none-eabi-gcc: not found` | no ARM toolchain | Install it, as under What you need |

## Next

- [How to flash the module firmware and restore the board's own](how-to-flash.md)
- [How to compile the module into MicroPython's rp2 port](how-to-build-rp2.md)
- [The vgacap module inside MicroPython](explanation-borrowing.md)
