# vgacap-dump and vgacap-frames

The C command-line tools for inspecting a
[vgacap stream](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-09-15-stream-format.md)
and turning it into pictures, and the Python
[`vgacap-bin2stream`](../../python/vgacap/bin2stream.py) that makes one from a
simulator dump. CMake builds the C tools into `build/`.

## Usage

```sh
build/vgacap-dump capture.vgacap              # chunk list, total samples, checksum
mkdir -p out && build/vgacap-frames capture.vgacap out/frame   # out/frame-0000.ppm ...
uv run vgacap-bin2stream dump.bin out.vgacap  # wrap a one-byte-per-clock simulator dump
```

## When frames appear

A frame is emitted at the frame boundary that closes it, once the timing is
known. A capture starts mid-frame: the tail of that frame is unusable, the
next boundary only tells the reconstruction where frames begin, and the
whole frame after it is the first that can be rendered. So capture at
least three frame periods for one picture; [`ttcap capture --frames N`](../../python/ttcap/README.md#capturing-with-the-stock-firmware)
captures N + 2.

## Glitches

Spurious sync pulses are tolerated: a capture whose hsync carries the odd
glitch of 2 to 30 clocks ([real silicon does](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-tt08-tiny-logo-glitch-tolerance)) still reconstructs.
`vgacap-frames` reports `glitches=N` per frame, counting the pulses rejected
since the previous frame, and `glitches_total=N` for the whole stream.
