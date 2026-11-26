---
type: landing
owner: vgacap maintainers
reader: someone with a Tiny Tapeout VGA design who wants its picture
review: 2027-05-27
---

# Capturing

- [ttcap](../../python/ttcap/README.md): capturing from a demo board running the stock firmware.
- [The vgacap firmware](../../firmware/README.md): a capture firmware for the RP2350 demo board.
- [The vgacap MicroPython module](../../mpy/README.md): the same capture built into MicroPython.
- [vgacap-rp1](../../capture-rp1/README.md): capturing with a Raspberry Pi 5.
- [vgacap/rtl](../../rtl/README.md): capture logic built into an FPGA design.
- [The C tools](../../src/tools/README.md): turning a capture into frames, checks and waveforms.
- [The GStreamer plugin](../../gst/README.md): turning a capture into video.

```{toctree}
:hidden:
:caption: The demo board

../../python/ttcap/README
../../firmware/README
../../mpy/README
```

```{toctree}
:hidden:
:caption: Other capture sources

../../capture-rp1/README
../../rtl/README
```

```{toctree}
:hidden:
:caption: From a capture to pictures

../../src/tools/README
../../gst/README
```
