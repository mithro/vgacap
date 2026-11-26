# Developing vgacap

How to build and test vgacap, and what CI checks.

## Building and testing

The core libraries are C99 with no dependencies beyond libc. The
[GStreamer plugin](../gst/README.md) is optional: CMake builds it only when
the GStreamer development files are present. The Python side runs under
[uv](https://docs.astral.sh/uv/).

```sh
cmake -S . -B build && cmake --build build
ctest --test-dir build --output-on-failure
uv run pytest -q
```

`VGACAP_JCC_PAD` (default `ON`) assembles every C file, `third_party/`
included, with `-Wa,-mbranches-within-32B-boundaries` (GCC or Clang on x86-64,
when the assembler takes it; CMake checks and otherwise builds without). The
Skylake-family JCC-erratum microcode slows loops whose jumps touch a 32-byte
boundary; the flag adds NOP padding only, so outputs are unchanged.

## Documentation site

The [Read the Docs](https://about.readthedocs.com) site is built from the
Markdown files where they are, by Sphinx with
[MyST](https://myst-parser.readthedocs.io). [`docs/conf.py`](conf.py) lists
the pages, in navigation order; a new page goes there. Warnings are errors:

```sh
uv run --locked --only-group docs sphinx-build -W --keep-going -c docs . build/docs
```

## Continuous integration

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs these jobs on
every push and pull request, on a pinned `ubuntu-24.04` image:

| step | `c` | `firmware` | `mpy-module` | `docs` | `python` | `gstreamer` |
|---|---|---|---|---|---|---|
| host CMake build | yes | no | yes | no | yes | yes |
| [`vgacap-rp1`](../capture-rp1/README.md) against [piolib](https://github.com/raspberrypi/utils/tree/master/piolib) | yes | no | no | no | no | no |
| piolib for the RP1 sampler test | no | no | no | no | yes | yes |
| [`vgacap_fw.uf2`](../firmware/README.md) for the RP2350 | no | yes | no | no | no | no |
| MicroPython unix port with the module | no | no | yes | no | no | no |
| MicroPython rp2 port with the module | no | no | yes | no | no | no |
| [documentation site](#documentation-site) | no | no | no | yes | no | no |
| GStreamer installed | no | no | no | no | no | yes |
| plugin built | no | no | no | no | no | yes |
| Icarus (`iverilog`) for the RTL test | no | no | no | no | no | yes |
| `ctest` | yes | no | no | no | no | yes |
| `pytest` | no | no | [`test_mpy_module.py`](../python/tests/test_mpy_module.py) | no | all | all |
