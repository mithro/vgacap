---
type: explanation
owner: vgacap maintainers
reader: anyone arriving at vgacap who wants to know what it does
review: 2027-05-27
---

# vgacap

vgacap captures the VGA output of a [Tiny Tapeout](https://tinytapeout.com) design, turns it into pictures and video, and checks it pixel by pixel. This page says what it captures, from where, and what it makes of a capture. The rest of the documentation starts at [the vgacap documentation](docs/index.md).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/overview-dark.svg">
  <img src="docs/diagrams/overview.svg" alt="A demo board or a simulator captures a design's uo_out into one vgacap stream, which is rebuilt into pictures, video and checks." width="100%">
</picture>

## What it captures

A design that drives the [Tiny VGA Pmod](https://github.com/mole99/tiny-vga) puts its VGA signals on its `uo_out` pins. The capture records those pins, and the screen is rebuilt from them. It needs no VGA monitor and no video capture card. The pins themselves are the record.

## Where a capture comes from

The demo board's own microcontroller, an RP2040 or RP2350, records `uo_out` once a project clock. It runs the stock firmware, the vgacap firmware or the vgacap MicroPython module. On a demo board with an FPGA breakout, capture logic can sit beside the design in the FPGA instead. A simulator records a design's RTL with no hardware: every pin of the design, twice a clock.

A Raspberry Pi 5 capture tool exists too. It has been shown only on a loopback fixture, not on a Tiny Tapeout board ([#27](https://github.com/mithro/vgacap/issues/27)). Each source's speed and proof are on [Capture sources compared](docs/reference-capture-sources.md).

## One stream, then pictures

Every source writes the same format, the [vgacap stream](src/stream/explanation-stream.md). So one library, [libvgaframe](src/frame/explanation-rebuilding-frames.md), rebuilds the frames from any capture. The same tools turn them into PPM pictures, video and waveforms. They also check a calibration design's capture pixel by pixel against its reference render.

> **Caution: AI in use.** This project is being built with [Claude Code](https://claude.com/claude-code).
> Check the code and the measurements before relying on them.

## Where the rest lives

The design, research notes, measurements and work log are in [mithro/tt-vga-capture](https://github.com/mithro/tt-vga-capture). The calibration designs and their reference renders are in [mithro/tt-vga-testpatterns](https://github.com/mithro/tt-vga-testpatterns). Both are separate repositories, linked from these pages where a fact comes from them. vgacap is licensed under Apache-2.0: see [LICENSE](LICENSE).
