# vgacap-rp1: capture with the Raspberry Pi 5's RP1 PIO

`vgacap-rp1` captures a [Tiny Tapeout](https://tinytapeout.com) design's `uo_out` with a Raspberry
Pi 5 whose GPIO header is wired to the board's pins. The Pi 5's
RP1 I/O chip has a PIO block like the RP2040's and RP2350's, and
`vgacap-rp1` runs the same PIO sampler on it that the demo board runs
([`python/ttcap/mp/capture_rp2.py`](../python/ttcap/mp/capture_rp2.py)):

1. wait for the project clock edge;
2. `in pins, 8`, autopushing four samples a word (first sample in the top
   byte);
3. DMA the words into DRAM through
   [piolib](https://github.com/raspberrypi/utils/tree/master/piolib);
4. write a vgacap stream (`VGCH` header, `RAW` chunks, a closing `TIME`
   chunk).

```sh
git init -q utils
git -C utils remote add origin https://github.com/raspberrypi/utils.git
git -C utils fetch -q --depth 1 origin 05e8808e84ed99b168b14a4025dddb9118fa1c17
git -C utils checkout -q FETCH_HEAD
make -C capture-rp1 PIOLIB=$PWD/utils/piolib
./capture-rp1/vgacap-rp1 --out run.vgacap --samples 1310720 \
    --data-base 4 --clk 12 --edge rising --clock-hz 500000
```

The data are 8 consecutive GPIOs starting at `--data-base` (`uo_out[0..7]`),
and the project clock is on `--clk`. No Pi 5 is wired to a Tiny Tapeout
board yet, so the pin assignment is a parameter, not a fact.

## Proven on hardware: the loopback fixture

`--gen FILE` adds a second state machine that drives FILE's samples (one
byte each, looped) and a clock onto the same pins. `--gen-counter` drives a
descending 8-bit counter instead, with no host involvement at all. Results
on a Raspberry Pi 5 on the [Welland bench](../python/ttcap/README.md#reaching-a-welland-board-bench-access-needed) with nothing on its GPIO header,
2026-11-24:

| pattern | samples | rate (M samples/s) | samples lost per 32 KB buffer | check |
|---|---|---|---|---|
| counter | 4,194,304 | 0.5 | 0 | every step exactly -1 |
| 640x480@60 bars | 1,310,720 | 0.5 | 0 | 2 pixel-identical pictures |
| counter | -- | 1.25 to 5 | ~150 to 320 | -- |

So with the stock `rp1-pio` driver, continuous capture is lossless up to
about 0.5 M samples/s. The driver re-arms its DMA for each buffer, and the
FIFO covers that gap only up to that rate. The way past it is a cyclic-DMA
kernel module ([`mithro/rpi5-rp1-pio-bench`](https://github.com/mithro/rpi5-rp1-pio-bench), 45-56 MB/s).

Those losses are not counted, so `vgacap-rp1` refuses a `--clock-hz` above
500000 unless `--unproven-rate` is given. A capture above that rate, or one
with no `--clock-hz`, warns and writes its closing `TIME` chunk with
`dropped=unknown` in the message and `dropped_samples` = 0xFFFFFFFF, so a
check for nothing dropped fails rather than passes. Only a declared rate of
500000 or less records `dropped_samples` = 0. The tool does not measure the
clock: it trusts the declared `--clock-hz`, and `--gen` and `--gen-counter`
run at whatever `--gen-div` and `--gen-delay` make them.

## What the driver needed

- Transfer sizes are `uint16_t` in the ioctl, so 64 KB is 0.
- The RX DMA request threshold must equal its burst of 8 (joined FIFO,
  DMACTRL `0x80000108`). With the driver's threshold of 1, each request read
  one real word and seven stale ones.
- The TX FIFO must be joined (8 deep) for the same bursts, with the
  driver's threshold left alone; writing TX DMACTRL wedged the channel.
- The first burst of a capture is stale even after `pio_sm_clear_fifos`, so
  it is captured and dropped.
- A DMA left waiting on a stopped state machine (a canceled thread, a
  generator stopped mid-transfer) leaves the driver unusable until a
  reboot, so teardown lets every transfer finish first.
- NULL read-ahead transfers (piolib's `rxsource` example) return `-EINVAL`
  on this kernel, and blocking calls serialize the whole RP1 firmware
  interface.
