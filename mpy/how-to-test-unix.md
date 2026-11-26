---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who changed the MicroPython module and wants to test it without a board
review: 2027-05-27
---

# How to build and test the module on MicroPython's unix port

**You changed the vgacap MicroPython module and want to run its tests, without a board.**

[`mpy/build-unix.sh`](build-unix.sh) builds MicroPython's unix port with the module on the simulated board, at the
MicroPython commit the demo boards run. [`python/tests/test_mpy_module.py`](../python/tests/test_mpy_module.py)
then drives it through ttcap, behind a raw REPL. The board build is on
[How to build the module firmware](how-to-build-release.md).

## What you need

- A vgacap checkout, a host C compiler, CMake, GNU make, git and [uv](https://docs.astral.sh/uv/).
- Network access for the first run of step 2, which clones MicroPython into `build/micropython`.

## Steps

1. At the checkout's root, build the host tree, which builds `build/vgacap-frames` for the tests' pixel checks.

   ```console
   $ cmake -S . -B build && cmake --build build -j
   ```

2. At the checkout's root, build the unix port with the module, which clones MicroPython at `b006887db6e4b18bce967ea87c7e63398f60b296` and ends with `built .../build/mpy-unix/micropython`.

   ```console
   $ mpy/build-unix.sh
   ```

3. At the checkout's root, run the module's tests, with the variable that makes them fail rather than skip when step 2's binary is missing.

   ```console
   $ VGACAP_REQUIRE_MPY_UNIX=1 uv run pytest -q python/tests/test_mpy_module.py
   ```

## Check

Step 3 ends with `24 passed`. Test checks names a group of the tests, and Holds what they hold the module to.

| Test checks | Holds |
|---|---|
| Frame and window captures | byte-identical to the vgacap firmware's, and pixel-exact through `vgacap-frames` |
| A heap fragmented into holes of about 144 KB | all 481 lines in one captured frame, through chained buffers |
| Streams | the design's consecutive samples |
| Stops, overruns, refusals and a caller's buffer | the vgacap firmware's behaviour |
| `ttcap capture --firmware mpy-module` | the whole path through ttcap |

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `1 skipped` when step 3 is run without the variable | no `build/mpy-unix/micropython` | Run step 2 |
| `RuntimeError: .../build/mpy-unix/micropython is missing: run mpy/build-unix.sh` | step 2 has not run | Run step 2 |
| `test_stream_stops_between_chunks_on_request` fails with `assert 3 >= 4` | the test's timing on a busy machine ([issue #31](https://github.com/mithro/vgacap/issues/31)) | Run step 3 again on a quieter machine |
| A compiler error in `modvgacap.c` or `hal_mpy.c` at step 2 | the change does not build against MicroPython | Fix the source; the unix port builds it with its own warnings |
| `git clone` fails at step 2 | no network | Clone `https://github.com/micropython/micropython.git` elsewhere and pass its path: `mpy/build-unix.sh <dir>` |

## Next

- [How to compile the module into MicroPython's rp2 port](how-to-build-rp2.md)
- [The vgacap module's Python API](reference-api.md)
- [The vgacap module's source files](reference-files.md)
