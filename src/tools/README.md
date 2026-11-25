# vgacap-dump and vgacap-frames

The C command-line tools, built into `build/` by CMake.

## Usage

```sh
build/vgacap-dump capture.vgacap              # chunk list, total samples, checksum
mkdir -p out && build/vgacap-frames capture.vgacap out/frame   # out/frame-0000.ppm ...
uv run vgacap-bin2stream dump.bin out.vgacap  # wrap a one-byte-per-clock simulator dump
```

A frame is emitted at the frame boundary that closes it, once the timing is
known. A capture that starts mid-frame -- the normal case -- claims the frame
that its first vsync pulse begins, so two frame periods are enough for the
first picture and each further period adds one. A capture that happens to
start exactly on a vsync pulse cannot measure that pulse and waits for the
next one, so capture at least three frame periods to be sure.

Spurious sync pulses are tolerated: a capture whose hsync carries the odd 2
to 30 clock glitch (real silicon does) still reconstructs. `vgacap-frames`
reports `glitches=N` per frame, counting the pulses rejected since the
previous frame, and `glitches_total=N` for the whole stream.
