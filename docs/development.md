# Developing vgacap

How to build and test vgacap, and what CI checks. The
[top-level README](../README.md) has the overview.

## Building and testing

The core libraries are C99 with no dependencies beyond libc. The GStreamer
plugin is optional: CMake builds it only when the GStreamer development
files are present.

```sh
cmake -S . -B build && cmake --build build
ctest --test-dir build --output-on-failure
uv run pytest -q
```

## Continuous integration

`.github/workflows/ci.yml` runs three jobs on every push and pull request,
on a pinned `ubuntu-24.04` image:

| job | what it proves |
|---|---|
| `c` | the C libraries and tools build and pass `ctest` on a machine with **no GStreamer** (`PKG_CONFIG_LIBDIR` is emptied), and the plugin is *skipped* there rather than failing the build |
| `python` | the Python suite passes without GStreamer, with the plugin tests skipped; `build/vgacap-frames` is built first because several tests compare against it |
| `gstreamer` | with GStreamer installed (the apt list in the workflow says what each package is for), the plugin is built, and its `ctest` and both pytest suites (`python/tests`, `gst/tests`) run for real |
