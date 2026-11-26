---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who wants to build it and run its tests
review: 2027-05-27
---

# How to build vgacap and run its tests

**You have a vgacap checkout, and you want the C libraries, the tools and the GStreamer plugin built, and every test run.**

This page builds the host side with CMake and runs its C and Python tests, as the CI jobs do. It does not build [the vgacap firmware](../firmware/README.md) for the RP2350, which its own pages cover. What each test layer covers is on [How vgacap is tested](developing/explanation-testing.md), and every option is on [Build options and test settings](developing/reference-build-options.md).

## What you need

- CMake 3.16 or later, a C99 compiler and `make`, and [uv](https://docs.astral.sh/uv/) for the Python side.
- For the [GStreamer plugin](../gst/README.md) and its tests, the GStreamer development files and tools that the `gstreamer` job of [`ci.yml`](../.github/workflows/ci.yml) installs. Without them the plugin is skipped, not failed.
- For the RTL framer's test, Icarus Verilog (`iverilog`).
- For the RP1 sampler's test, a checkout of [piolib](https://github.com/raspberrypi/utils/tree/master/piolib) at the commit `PIOLIB_COMMIT` in `ci.yml`.

## Steps

1. In the checkout, run `cmake -S . -B build` to configure the build, which prints a `vgacap:` line saying whether it builds the GStreamer plugin.
2. In the checkout, run `cmake --build build -j` to build the libraries, the C tools and the C tests into `build/`.
3. In the checkout, run `ctest --test-dir build --output-on-failure -LE perf` to run the C tests without the timing test labelled `perf`.
4. In the same shell, run `export VGACAP_PIOLIB=<utils checkout>/piolib` if you have piolib, so that the RP1 sampler's test runs instead of skipping.
5. In the checkout, run `uv run pytest -q -rs` to run the Python and GStreamer suites, which list every skipped test with its reason.
6. To check the build without the vendored libraries, repeat steps 1 to 3 with `-B build-off -DVGACAP_HOST_EXTRAS=OFF` and `--test-dir build-off`.
7. To test the vgacap MicroPython module, run `mpy/build-unix.sh`, then `uv run pytest -q python/tests/test_mpy_module.py`.

## Check

Step 3 ends with this line, where `N` is the count of tests in your configuration:

```text
100% tests passed, 0 tests failed out of N
```

Step 5 ends with a summary of passed and skipped tests and no failures. Without GStreamer, the plugin's tests are among the skips, each with its reason. Step 7 builds `build/mpy-unix/micropython`, and its tests pass instead of skipping.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `vgacap: GStreamer development files not found, skipping the plugin` | no GStreamer development files | Install them as the `gstreamer` job does, or go on without the plugin |
| 1 | `vgacap: pkg-config not found, skipping the GStreamer plugin` | no `pkg-config` | Install `pkg-config`, then run step 1 again |
| 1 | `vgacap: VGACAP_JCC_PAD: ... is not supported here` | a compiler or a machine other than GCC or Clang on x86-64 | Nothing: the build goes on without the padding |
| 2 | an `error:` from a warning | the build turns warnings into errors (`-Werror`) | Fix the warning in the code |
| 5 | skips that say `needs build/vgacap-frames` | step 2 has not run | Run steps 1 and 2 |
| 5 | a skip that says `VGACAP_PIOLIB (raspberrypi/utils' piolib) is not set` | step 4 has not run | Run step 4, or accept the skip |
| 5 | `test_mpy_module.py` skipped | no `build/mpy-unix/micropython` | Run step 7 |

## Next

- [How vgacap is tested](developing/explanation-testing.md)
- [Build options and test settings](developing/reference-build-options.md)
- [Continuous integration jobs](developing/reference-ci-jobs.md)
- [How to run the hardware-in-the-loop check through a tunnel](../tools/how-to-run-hil-through-a-tunnel.md)
