# vgacap-rp1: capture with the Raspberry Pi 5's RP1 PIO

The same PIO sampler the Tiny Tapeout demo board's RP2040/RP2350 runs
(`ttcap/mp/capture_rp2.py`), on the Pi 5's own I/O chip:
wait for the project-clock edge, `in pins, 8`, autopush four samples a word
(first sample in the top byte), DMA into DRAM through
[piolib](https://github.com/raspberrypi/utils/tree/master/piolib), write a
vgacap stream (`VGCH` header, `RAW ` chunks, a closing `TIME` chunk).

```sh
git clone --depth 1 https://github.com/raspberrypi/utils.git
make -C capture-rp1 PIOLIB=$PWD/utils/piolib
./capture-rp1/vgacap-rp1 --out run.vgacap --samples 1310720 \
    --data-base 4 --clk 12 --edge rising --clock-hz 500000
```

The data are 8 consecutive GPIOs from `--data-base` (uo_out[0..7]) and the
project clock is `--clk`. No Pi 5 is wired to a Tiny Tapeout board yet, so the
pin assignment is a parameter, not a fact.

## Proven on hardware: the loopback fixture

`--gen FILE` adds a second state machine that drives FILE's samples (one byte
each, looped) and a clock onto the same pins; `--gen-counter` drives a
descending 8-bit counter instead, with no host involvement at all. On
pi-sw2-p4 (Pi 5, nothing on its header), 2026-11-24:

| test | rate | result |
|---|---|---|
| counter, 4,194,304 samples | 500 k samples/s | every step exactly -1 |
| 640x480@60 bars, 1,310,720 samples | 500 k samples/s | 2 pictures, pixel-identical |
| counter | 1.25-5 M samples/s | ~150-320 samples lost at every 32 KB buffer boundary |

So with the stock `rp1-pio` driver, continuous capture is lossless up to about
0.5 M samples/s: the driver re-arms its DMA per buffer, and the FIFO covers
that gap only that far. The way past it is a cyclic-DMA kernel module
(`mithro/rpi5-rp1-pio-bench`, 45-56 MB/s).

## What the driver needed (all measured, all in the code's comments)

- transfer sizes are `uint16_t` in the ioctl: 64 KB is 0;
- the RX DMA request threshold must equal its burst of 8 (joined FIFO,
  DMACTRL `0x80000108`); the driver's threshold of 1 made each request read
  one real word and seven stale ones;
- the TX FIFO must be joined (8 deep) for the same bursts, with the driver's
  threshold left alone; writing TX DMACTRL wedged the channel;
- the first burst of a capture is stale even after `pio_sm_clear_fifos`, so it
  is captured and dropped;
- a DMA left waiting on a stopped state machine (a cancelled thread, a
  generator stopped mid-transfer) leaves the driver unusable until a reboot,
  so teardown lets every transfer finish first;
- NULL read-ahead transfers (piolib's `rxsource` example) return `-EINVAL` on
  this kernel; blocking calls serialise the whole RP1 firmware interface.
