---
type: reference
owner: vgacap maintainers
reader: someone looking up what vgacap-rp1 sets on the RP1's PIO, DMA and GPIOs
review: 2027-05-27
---

# RP1 PIO and GPIO facts for vgacap-rp1

**The PIO programs, state machine settings, DMA sizes, GPIOs and stream header that `vgacap-rp1` uses on a Raspberry Pi 5.**

The values come from [`vgacap_rp1.c`](vgacap_rp1.c) and [`programs.h`](programs.h). Why each DMA setting is what it is, is on [Capture on the Raspberry Pi 5's RP1](explanation-capture.md). The options that change them are on [vgacap-rp1 options](reference-options.md).

## GPIOs

Role is what the tool does with the GPIO, and Default GPIO the RP1 GPIO without options. Option is the flag that moves it.

| Role | Default GPIO | Option |
|---|---|---|
| data: sample bits 0 to 7 | GPIO 4 to GPIO 11 | `--data-base` |
| clock: the edge each sample waits for | GPIO 12 | `--clk` |

The data GPIOs are always eight in a row, with sample bit 0 on the lowest. The generators drive the same GPIOs.

## PIO programs

Program is the state machine's job, and Instruction its PIO assembly. Word is the 16-bit instruction for the default GPIOs, the rising edge and a `--gen-delay` of 3. `vgacap-rp1-programs CLK EDGE DELAY` prints the words for other values.

| Program | Instruction | Word |
|---|---|---|
| sampler | `wait 0 gpio 12` | `200c` |
| sampler | `wait 1 gpio 12` | `208c` |
| sampler | `in pins, 8` | `4008` |
| generator (`--gen`) | `out pins, 8 side 0 [3]` | `6308` |
| generator (`--gen`) | `nop side 1 [3]` | `b342` |
| counter (`--gen-counter`) | `mov pins, x side 0 [3]` | `a301` |
| counter (`--gen-counter`) | `jmp x-- 0 side 1 [3]` | `1340` |

On the falling edge the sampler's two `wait` instructions swap their polarity. The sampler is the RP2 demo board's sampler, word for word ([`capture_rp2.py`](../python/ttcap/mp/capture_rp2.py)).

## State machine settings

Setting is the PIO configuration, and the other columns its value on each state machine.

| Setting | Sampler | Generator | Counter |
|---|---|---|---|
| Pins | `in` from the first data GPIO | `out` to the eight data GPIOs | `out` to the eight data GPIOs |
| Side-set pin | none | the clock GPIO | the clock GPIO |
| Shift | left with autopush at 32 bits | right with autopull at 32 bits | none |
| FIFO | RX joined: 8 words | TX joined: 8 words | not joined |
| Clock divider | 1.0 | `--gen-div` | `--gen-div` |
| PIO clocks for each generated clock | | 2 × (`--gen-delay` + 1) × `--gen-div` | 2 × (`--gen-delay` + 1) × `--gen-div` |

The generator changes the data with the clock low, and raises the clock (`--gen-delay` + 1) × `--gen-div` PIO clocks later.

## DMA

Item is the DMA setting, and Value what `vgacap-rp1` uses.

| Item | Value |
|---|---|
| PIO device | `/dev/pio0` |
| Receive transfer | 32768 bytes: 8192 words |
| Receive buffers queued | 4 |
| Receive DMACTRL | `0x80000108`: request threshold 8 words |
| Words dropped at the start | 8: the first burst |
| Transmit transfer | the largest multiple of 4 bytes up to 32768 that divides the `--gen` file |
| Transmit buffers queued | 4 |
| Largest transfer the driver takes | 65535 bytes: its size field is 16 bits |

## Stream written

Field is a field of the stream, and Value what `vgacap-rp1` writes. The format is defined in [`include/vgacap/stream.h`](../include/vgacap/stream.h).

| Field | Value |
|---|---|
| Header version | 1 |
| `sample_bits` | 8 |
| `samples_per_word` | 4 |
| `flags` | `VGACAP_FLAG_FIRST_SAMPLE_MSB`: first sample in bits 31:24 |
| `mode` | 0: extclk |
| `clock_hz` | `--clock-hz` |
| `signal_map` | 7 3 0 4 1 5 2 6: the Tiny VGA Pmod map |
| Samples in each `RAW ` chunk | 32768 (the last chunk fewer) |
| Closing `TIME` chunk's `dropped_samples` | 0 at a declared rate of 1 to 500000 Hz |
| Closing `TIME` chunk's `dropped_samples` otherwise | `0xFFFFFFFF`: unknown |
| Closing `TIME` chunk's message | `rp1 samples=N seconds=S samples_per_s=R dropped=D` |
