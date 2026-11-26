---
type: reference
owner: vgacap maintainers
reader: someone looking up a vgacap-rp1 option or exit status
review: 2027-05-27
---

# vgacap-rp1 options

**Every option, exit status and environment variable of `vgacap-rp1`, as its parser in [`vgacap_rp1.c`](vgacap_rp1.c) reads them.**

The tool has no `--help`: a wrong or missing option prints the usage line and exits with status 2. What the options drive on the chip is on [RP1 PIO and GPIO facts for vgacap-rp1](reference-rp1-pio.md).

## Options

Option is the flag, and Value what follows it. Default is what the tool uses without the flag. Limit is what the parser refuses outside.

| Option | Value | Default | Limit | Meaning |
|---|---|---|---|---|
| `--out` | a path | none: required | | the vgacap stream to write |
| `--samples` | an integer | none: required | at least 1 | how many samples to capture |
| `--data-base` | a GPIO number | 4 | `--data-base` + 8 at most 28 | the first data GPIO, which carries `uo_out[0]` |
| `--clk` | a GPIO number | 12 | 0 to 27 and outside the data GPIOs | the project clock GPIO |
| `--edge` | `rising` or `falling` | `rising` | | the clock edge each sample is taken on |
| `--clock-hz` | hertz | 0 (unknown) | 500000 at most without `--unproven-rate` | the project clock written into the stream header |
| `--unproven-rate` | none | off | | allows a `--clock-hz` above 500000 |
| `--desc` | text | `vgacap-rp1` | | the stream header's description |
| `--gen` | a path | off | not with `--gen-counter`; not an empty file | drives the file's bytes in a loop onto the data GPIOs |
| `--gen-counter` | none | off | not with `--gen` | drives a descending 8-bit count onto the data GPIOs |
| `--gen-div` | a number | 1.0 | at least 1.0 | the generator's PIO clock divider |
| `--gen-delay` | an integer | 3 | 0 to 15 | extra PIO clocks the generator holds each half clock |
| `--poll` | none | off | | reads the receive FIFO word by word instead of by DMA |

A `--clock-hz` of 0, or above 500000, makes the tool warn on standard error. The stream's `TIME` chunk then records its dropped count as unknown.

## Exit statuses

Status is the exit status, and When the case that returns it.

| Status | When |
|---|---|
| 0 | the capture ran and the stream was written |
| 1 | a step after the options failed: its message names it |
| 2 | an option was wrong or missing |
| 2 | `--clock-hz` is above 500000 without `--unproven-rate` |

## Environment

Variable is the name, and Effect what setting it to any value does.

| Variable | Effect |
|---|---|
| `VGACAP_RP1_DEBUG` | prints the sampler's and the generator's PIO instruction words on standard error |
