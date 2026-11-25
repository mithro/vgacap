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

| job | GStreamer | plugin | `ctest` | `pytest` | also builds |
|---|---|---|---|---|---|
| `c` | absent | skipped | all | -- | `vgacap-rp1` |
| `firmware` | -- | -- | -- | -- | `vgacap_fw.uf2` |
| `mpy-module` | -- | -- | -- | `test_mpy_module.py` | MicroPython rp2 port |
| `python` | absent | skipped | -- | all | -- |
| `gstreamer` | installed | built | all | all | -- |
