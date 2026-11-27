---
type: reference
owner: vgacap maintainers
reader: someone with a vgacap checkout who changes how it builds or tests
review: 2027-05-27
---

# Build options and test settings

This page lists the CMake options of the vgacap build and the environment variables its tests read. It is for someone building or testing vgacap in a configuration other than the default. The steps are on [How to build vgacap and run its tests](../../development.md).

## CMake options

The table is made from the `option()` lines of every `CMakeLists.txt` when this site is built. Option is the cache variable, given as `-D<option>=ON` or `OFF` when configuring. Default is its value when not given, and Set in is the file that declares it.

```{vgacap-cmake-options}
```

`VGACAP_HOST_EXTRAS` builds the [vendored libraries](../../../third_party/reference-vendored-libraries.md) and the simulation trace tools that need them. With it `OFF`, `vgacap-dump` reads plain streams only and refuses a zstd file with a message. `VGACAP_JCC_PAD` adds NOP padding only, so every output is the same with it on or off. It applies only to GCC or Clang on x86-64, where CMake checks that the assembler takes the flag.

[`src/sample/centrev1.c`](../../../src/sample/centrev1.c) is compiled with `-O3` under GCC or Clang, in the `Release` and `RelWithDebInfo` build types. At `-O2` GCC does not vectorise its row loops.

## Test labels

| Label | Tests | Run |
|---|---|---|
| `perf` | `test_centrev1_speed` | by hand: `ctest --test-dir build -L perf` |
| `perf` | `test_hash_speed` | by hand: `ctest --test-dir build -L perf` |
| `perf` | `test_trace_push_speed` | by hand: `ctest --test-dir build -L perf` |
| `perf` | `test_tbv1_speed` | by hand: `ctest --test-dir build -L perf` |

Every `ctest` command on these pages and in CI leaves the `perf` label out with `-LE perf`. A timing means something only on a known machine.

## Environment variables

Variable is what the tests read, Read by the test file, and Effect what it changes.

| Variable | Read by | Effect |
|---|---|---|
| `VGACAP_PIOLIB` | `python/tests/test_rp1_sampler.py` | piolib's directory: the RP1 sampler's test runs instead of skipping |
| `VGACAP_REQUIRE_MPY_UNIX` | `python/tests/test_mpy_module.py` | any value: a missing `build/mpy-unix/micropython` fails instead of skipping |
| `VGACAP_SAMPLE_REF` | `python/tests/test_sample_equivalence.py` | an earlier `vgacap-sample`: the equivalence tests run instead of skipping |
| `VGACAP_SAMPLE_NEW` | `python/tests/test_sample_equivalence.py` | the `vgacap-sample` under test, in place of `build/vgacap-sample` |
| `VGACAP_SYSTEM_PYTHON` | `gst/tests/test_plugin.py` | the Python with `gi`, in place of `/usr/bin/python3` |
| `VGACAP_SYSTEM_PYTHON` | `gst/tests/test_capture_source.py` | the Python with `gi`, in place of `/usr/bin/python3` |
| `CI` | `python/tests/test_rp1_sampler.py` | any value: a missing `VGACAP_PIOLIB` fails instead of skipping |
