# Developing vgacap

How to build and test vgacap, and what CI checks. The
[top-level README](../README.md) has the overview.

## Building and testing

The core libraries are C99 with no dependencies beyond libc. The
[GStreamer plugin](../gst/README.md) is optional: CMake builds it only when
the GStreamer development files are present.

```sh
cmake -S . -B build && cmake --build build
ctest --test-dir build --output-on-failure
uv run pytest -q
```

## Continuous integration

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs these jobs on
every push and pull request, on a pinned `ubuntu-24.04` image:

| step | `c` | `firmware` | `mpy-module` | `python` | `gstreamer` |
|---|---|---|---|---|---|
| host CMake build | yes | no | yes | yes | yes |
| [`vgacap-rp1`](../capture-rp1/README.md) against [piolib](https://github.com/raspberrypi/utils/tree/master/piolib) | yes | no | no | no | no |
| [`vgacap_fw.uf2`](../firmware/README.md) for the RP2350 | no | yes | no | no | no |
| MicroPython unix port with the module | no | no | yes | no | no |
| MicroPython rp2 port with the module | no | no | yes | no | no |
| GStreamer | absent | -- | absent | absent | installed |
| plugin | skipped | -- | skipped | skipped | built |
| `ctest` | yes | no | no | no | yes |
| `pytest` | no | no | [`test_mpy_module.py`](../python/tests/test_mpy_module.py) | all | all |
