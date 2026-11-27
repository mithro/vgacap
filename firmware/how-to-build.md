---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who wants the vgacap firmware's UF2
review: 2027-05-27
---

# How to build the vgacap firmware

**You want `vgacap_fw.uf2`, the vgacap firmware for the Tiny Tapeout demo board v3, built from your checkout.**

The build is a CMake project of its own in `firmware/`, against [pico-sdk](https://github.com/raspberrypi/pico-sdk)
2.2.0. It also checks the PIO programs against their checked-in pioasm output. Loading the result is on
[How to run the vgacap firmware from RAM and go back to stock](how-to-load.md).

## What you need

- A vgacap checkout, and a shell at its root.
- The ARM toolchain and Ninja; on Debian or Ubuntu, `sudo apt-get install gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib ninja-build`.
- A host C and C++ compiler and CMake 3.13 or later: the SDK builds pioasm and [picotool](https://github.com/raspberrypi/picotool) with them.
- Network access for the first configure, which fetches picotool's source.

## Steps

1. At the checkout's root, clone pico-sdk 2.2.0 and its TinyUSB, which gives commit `a1438dff1d38bd9c65dbd693f0e5db4b9ae91779` with `lib/tinyusb` at `86ad6e56c1700e85f1c5678607a762cfe3aa2f47`.

   ```console
   $ git clone --branch 2.2.0 --depth 1 https://github.com/raspberrypi/pico-sdk.git pico-sdk
   $ git -C pico-sdk submodule update --init --depth 1 lib/tinyusb
   ```

2. At the checkout's root, configure the firmware build, which fetches and builds picotool 2.2.0 the first time.

   ```console
   $ cmake -S firmware -B firmware/build -G Ninja -DPICO_SDK_PATH=$PWD/pico-sdk
   ```

3. At the checkout's root, build it, which assembles `firmware/pio/*.pio`, compares the output with `firmware/pio/*.hex` and writes the UF2.

   ```console
   $ cmake --build firmware/build
   ```

## Check

`firmware/build/vgacap_fw.uf2` exists. The build log holds these two lines, and ends without an
error:

```text
pioasm vgacap_stream.pio, and compare with pio/vgacap_stream.hex
pioasm vgacap_frame.pio, and compare with pio/vgacap_frame.hex
```

The UF2's family is `rp2350-arm-s` and its blocks load at `0x20000000`, into SRAM.

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `this firmware is pinned to pico-sdk 2.2.0, found ...` at step 2 | `PICO_SDK_PATH` names another SDK | Clone 2.2.0 as in step 1 and point `-DPICO_SDK_PATH` at it |
| `Files ... are different` after a `pioasm` line at step 3 | a `.pio` file changed and its `.hex` did not | Copy `firmware/build/<name>.hex` into `firmware/pio/`, and commit both |
| A compiler warning stops step 3 | the firmware's own sources build with `-Wall -Wextra -Werror` | Fix the warning in the vgacap source |
| `No installed picotool with version 2.1.1 found - building from source` at step 2 | no picotool on the machine | Nothing: this is a warning, and the build fetches one |
| Each new build directory fetches picotool again | the fetched copy lives in the build directory | Add `-DPICOTOOL_FETCH_FROM_GIT_PATH=<dir>` at step 2 to share one copy |

## Next

- [How to run the vgacap firmware from RAM and go back to stock](how-to-load.md)
- [How to test the vgacap firmware off hardware](how-to-run-tests.md)
- [The vgacap firmware's source files](reference-files.md)
