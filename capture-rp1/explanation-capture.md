---
type: explanation
owner: vgacap maintainers
reader: someone who wants to know how vgacap-rp1 captures and where its limits come from
review: 2027-05-27
---

# Capture on the Raspberry Pi 5's RP1

This page explains how `vgacap-rp1` captures with the PIO block of the Raspberry Pi 5's RP1 chip, and why it refuses some rates. It is for someone choosing or checking a capture rate. The steps to run it are on [How to check vgacap-rp1 with the loopback fixture](how-to-capture-loopback.md).

## The sampler and its path to memory

The RP1 has a PIO block like the RP2040's and the RP2350's. `vgacap-rp1` loads the demo board's own sampler onto one of its state machines, word for word. The sampler waits for an edge of the project clock, then reads the eight data GPIOs. Four samples fill a 32-bit word, the first in the top byte, and the state machine pushes each full word into its receive FIFO.

The tool talks to the PIO through [piolib](https://github.com/raspberrypi/utils/tree/master/piolib) and the kernel's `rp1-pio` driver. DMA moves the words from the FIFO into one buffer in DRAM, 32 KB a transfer. After the capture the tool writes the words out unchanged, as `RAW ` chunks of a vgacap stream. A `VGCH` header goes before them, and a closing `TIME` chunk after. piolib is compiled into the tool, so a 32-bit or a 64-bit userland needs no extra package.

## The loopback fixture

`vgacap-rp1` carries its own source, as no Tiny Tapeout board is wired to a Pi 5 ([#27](https://github.com/mithro/vgacap/issues/27)). A second state machine on the same PIO drives the same GPIOs. `--gen-counter` drives a descending 8-bit count with no help from the host, and `--gen` drives the bytes of a file. A loss of samples breaks the count, unless it is a whole multiple of 256.

## Why 500000 samples a second

The stock `rp1-pio` driver arms its DMA again for each transfer. While it does, the 8-word FIFO holds the samples that arrive, and it covers that gap only up to a rate. On pi-sw2-p4, a counter of 4,194,304 samples at 500000 samples a second stepped down by exactly one every sample. The 640x480 bars picture at that rate came back pixel-identical ([the work log, 2026-11-24](https://github.com/mithro/tt-vga-capture/blob/main/LOG.md)).

The log records 500000 samples a second as the stock driver's lossless ceiling. The tool cannot count losses above it, so it refuses a `--clock-hz` above 500000 unless `--unproven-rate` is given ([#28](https://github.com/mithro/vgacap/issues/28)).

A capture above that rate, or with no `--clock-hz`, warns on standard error. Its `TIME` chunk then says `dropped=unknown` and carries a dropped count of `0xFFFFFFFF`, so a check for nothing dropped fails. The tool does not measure the clock: it trusts the declared `--clock-hz`. The generators run at whatever `--gen-div` and `--gen-delay` make them.

## What the driver needed

Each of these was found on the hardware, and each is commented where the code deals with it.

- Transfer sizes are 16-bit in the driver's ioctl, so a 64 KB transfer is a size of 0 and runs unpaced.
- The receive DMA request threshold must equal its burst of 8 words. At the driver's threshold of 1, each request read one real word and seven stale ones.
- The transmit FIFO must be joined, 8 words deep, for the same bursts. Writing the transmit DMACTRL wedged the channel.
- The first burst of a capture is stale even after the FIFOs are cleared, so it is captured and dropped.
- A DMA transfer left waiting on a stopped state machine leaves the driver unusable until a reboot. So the teardown lets every transfer finish before it stops anything.
- piolib's NULL read-ahead transfers return `-EINVAL` on this kernel, and blocking calls serialise the whole RP1 firmware interface.
