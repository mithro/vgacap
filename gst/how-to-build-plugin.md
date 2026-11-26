---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout on Debian or Ubuntu who wants the GStreamer elements
review: 2027-05-27
---

# How to build the GStreamer plugin

**You have a vgacap checkout and want `libgstvgacap.so`, the plugin with `vgadecode`, `vgacapttsrc` and `vgacapbin`.**

The plugin is built by the same CMake run as the rest of vgacap, and only when GStreamer's development files are installed. This page installs them on Debian or Ubuntu, builds, and checks the elements load. The rest of the build is on [Developing vgacap](../docs/development.md).

## What you need

- CMake 3.16 or later and a C compiler.
- GStreamer 1.16 or later, and `sudo` to install its packages.

## Steps

1. In a shell, run `sudo apt-get install libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good`, which installs the headers and tools.
2. In the vgacap checkout, run `cmake -S . -B build`, and it prints `vgacap: building the GStreamer plugin against` and the version.
3. In the same directory, run `cmake --build build -j`, which writes `build/libgstvgacap.so`.
4. In the same directory, run `ctest --test-dir build -R 'gst|uri'`, which loads each element and tests the URI parser.

## Check

Step 4 ends with `100% tests passed, 0 tests failed out of 4`. With `export GST_PLUGIN_PATH=$PWD/build`, `gst-inspect-1.0 vgadecode` prints the element's properties.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `vgacap: GStreamer development files not found, skipping the plugin` | the `-dev` packages are missing, or older than 1.16 | run step 1, then step 2 again |
| 2 | `vgacap: pkg-config not found, skipping the GStreamer plugin` | `pkg-config` is missing | run `sudo apt-get install pkg-config`, then step 2 again |
| 3 | no `build/libgstvgacap.so` | step 2 skipped the plugin | read step 2's output for the line above |
| 4 | `gst_plugin_inspect_vgadecode` fails | `gst-inspect-1.0` is missing | install `gstreamer1.0-tools` |

## Next

- [How to decode a capture to video with GStreamer](how-to-decode-to-video.md)
- [How to capture live in GStreamer](how-to-capture-live.md)
- [The vgacap GStreamer elements](reference-elements.md)
