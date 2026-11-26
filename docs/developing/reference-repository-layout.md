---
type: reference
owner: vgacap maintainers
reader: someone with a vgacap checkout who wants to know where a change goes
review: 2027-05-27
---

# Where to make a change

This page lists the top-level directories of the vgacap repository, what each holds and the page that describes it. It is for someone about to change vgacap who needs to find the code, its tests and its pages. Each component's pages sit beside its code, so a change and its page travel in the same pull request.

Directory is the path from the repository root, Holds what it contains, and Tested by where its tests live.

| Directory | Holds | Tested by |
|---|---|---|
| `include/vgacap/` | the public C headers of the libraries | `tests/` |
| `src/stream/` | the stream reader and writer | `tests/` |
| `src/frame/` | `libvgaframe` and the VGA mode table | `tests/` |
| `src/trace/` | the simulation trace reader and writer | `tests/` |
| `src/sample/` | the trace samplers and the frame hashes | `tests/` |
| [`src/tools/`](../../src/tools/README.md) | the C tools: `vgacap-dump` and `vgacap-frames` and the trace tools | `python/tests/` |
| [`gst/`](../../gst/README.md) | the GStreamer plugin | `gst/tests/` |
| [`python/ttcap/`](../../python/ttcap/README.md) | `ttcap` and its board emulator | `python/tests/` |
| `python/vgacap/` | Python mirrors of the C libraries and the synthetic signals | `python/tests/` |
| [`firmware/`](../../firmware/README.md) | the vgacap firmware and its simulated board | `tests/` and `python/tests/` |
| [`mpy/`](../../mpy/README.md) | the vgacap MicroPython module | `python/tests/test_mpy_module.py` |
| [`capture-rp1/`](../../capture-rp1/README.md) | capture on a Raspberry Pi 5 | `python/tests/test_rp1_sampler.py` |
| [`rtl/`](../../rtl/README.md) | the capture framer for an FPGA design | `python/tests/test_rtl_raw_framer.py` |
| [`tools/`](../../tools/README.md) | the hardware-in-the-loop check and the board scripts | `python/tests/test_hil.py` |
| [`third_party/`](../../third_party/README.md) | the vendored libraries | `tests/test_third_party.c` |
| `bench/` | the run-length benchmark `bench-runs` | none |
| [`docs/`](../index.md) | the site's landing pages and its build checks | `docs/tests/` |

## Adding a file

Addition is what you add, and Where the place it is registered.

| Addition | Where |
|---|---|
| a C source file of a library or tool | its section of the top-level [`CMakeLists.txt`](../../CMakeLists.txt) |
| a C test | a `tests/test_*.c` file and one `vgacap_add_test` line in `CMakeLists.txt` |
| a Python test | a `test_*.py` file in `python/tests/` or `gst/tests/`, found by pytest |
| a page | beside its code, and one line on its section's landing page |
| a CI step | its job in [`ci.yml`](../../.github/workflows/ci.yml) |
