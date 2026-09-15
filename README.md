# vgacap

Capture the Tiny VGA Pmod output of a [Tiny Tapeout](https://tinytapeout.com)
project with the PIO block of the demo board's RP2040 / RP2350 (later the
Raspberry Pi 5 RP1), reconstruct the picture on a host, and present it as a
virtual video source through GStreamer.

This repository holds the code: the capture stream formats, the
reconstruction library, the RP2 and RP1 capture backends, the GStreamer
plugin and the Python tools. The design, research notes, work log and task
list live in [mithro/tt-vga-capture](https://github.com/mithro/tt-vga-capture).

> **Caution: AI in use.** This project is being built with Claude Code.
> Check the code and the measurements before relying on them.

## Building and testing

The core libraries are C99 with no dependencies beyond libc.

```sh
cmake -S . -B build && cmake --build build
ctest --test-dir build --output-on-failure
uv run pytest -q
```

## License

Apache-2.0, see [LICENSE](LICENSE).
