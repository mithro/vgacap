---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who changed the firmware and wants to test it without a board
review: 2027-05-27
---

# How to test the vgacap firmware off hardware

**You changed the vgacap firmware and want to know it still works, without a board.**

The firmware's portable core runs on the host against a simulated board, `vgacap-fw-sim`, which the C and Python
tests drive. The ARM build and its PIO check are on [How to build the vgacap firmware](how-to-build.md). The
simulator models what the PIO and DMA deliver, not their cycles; [`firmware/sim/sim.h`](sim/sim.h) says what that
leaves out.

## What you need

- A vgacap checkout, a host C compiler, CMake and [uv](https://docs.astral.sh/uv/). No ARM toolchain.

## Steps

1. At the checkout's root, build the host tree, which builds `build/vgacap-fw-sim`, `test_fw_units` and `test_fw_core`.

   ```console
   $ cmake -S . -B build && cmake --build build -j
   ```

2. At the checkout's root, run the two C tests of the firmware, which check its parser, clock plans, PIO words, replies and chunks.

   ```console
   $ ctest --test-dir build -R test_fw
   ```

3. At the checkout's root, run the Python tests, which drive `vgacap-fw-sim` through ttcap's firmware transport.

   ```console
   $ uv run pytest -q python/tests/test_fw_sim.py
   ```

## Check

Step 2 ends with `100% tests passed, 0 tests failed out of 2`. Step 3 ends with a line such as `55 passed in
15.21s`, with no failure.

Test is the test, and Checks what it holds the firmware to.

| Test | Checks |
|---|---|
| [`tests/test_fw_units.c`](../tests/test_fw_units.c) | argument parsing; the clock plans against the clocks the stock firmware picked on fpga-4; the PIO words |
| [`tests/test_fw_core.c`](../tests/test_fw_core.c) | every command's exact reply; the engines' exact chunks on the simulated board, with overruns, stops, a host that drops DTR, frame windows, the buffer limit, chained buffers and refusals |
| [`python/tests/test_fw_sim.py`](../python/tests/test_fw_sim.py) | byte-exact streams, pixel-exact frames and windows through `vgacap-frames`, stops, overruns, the CLI, and the PIO words against MicroPython's assembler |

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `Failed to change working directory to .../build` at step 2 | step 1 did not run, or ran elsewhere | Run step 1 at the checkout's root |
| Step 3 fails to start `build/vgacap-fw-sim` | step 1 did not build it | Run step 1 |
| `test_trace_push_speed` fails when you run all of `ctest` | a speed test, labelled `perf`, on a busy machine | Leave out `perf` with `-LE perf`, as CI does; it does not test the firmware |
| A `test_fw_core` case names a reply or a chunk | the firmware's output changed | Fix the firmware, or the protocol reference and the test together |

## Next

- [How to build the vgacap firmware](how-to-build.md)
- [The vgacap firmware's source files](reference-files.md)
- [The vgacap firmware protocol](PROTOCOL.md)
