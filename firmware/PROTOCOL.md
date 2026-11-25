# vgacap capture firmware protocol, version 1

What a host sends the capture firmware (`firmware/`) over its USB CDC
serial port, and what comes back. The host side is `python/ttcap/firmware.py`;
the firmware side is `firmware/core/fw.c`.

The serial settings do not matter (it is USB CDC), but the host must assert
DTR, as pyserial does when it opens a port: the firmware only writes while
DTR is set, and a capture whose host drops DTR stops.

## Commands and replies

A command is one line of ASCII: a command word, then `key=value` arguments
separated by spaces, ending in `\n`. `\r` and other control characters are
ignored, an empty line does nothing, and a line over 256 bytes is refused
whole. Every argument a command takes must be given exactly once; an
unknown or repeated key is an error, and so is a value outside its range.
There are no defaults: the host always says what it wants.

Each command gets exactly one reply line: `ok` followed by `key=value`
fields, or `err` followed by a message for a person.

| Command | Arguments | `ok` reply |
|---|---|---|
| `hello` | none | `ok vgacap-fw proto=1 chip=rp2350 sysclk_hz=N arena_bytes=N` |
| `status` | none | `ok clock_hz=N sysclk_hz=N reset=0\|1 ui_in=-1..255` |
| `clock` | `hz=0..125000000` | `ok clock_hz=N actual_hz=N sysclk_hz=N period=P`, or for `hz=0` `ok clock_hz=0 sysclk_hz=N` |
| `reset` | `hold=0\|1` | `ok reset=0\|1` |
| `ui_in` | `value=-1..255` | `ok ui_in=V` |
| `stream` | see below | `ok stream`, then a capture |
| `frame` | see below | `ok frame`, then a capture |
| `program_stream` | `edge` | `ok words=... wrap_target=N wrap=N shiftctrl=0x... execctrl=0x...` |
| `program_frame` | `edge hsync_active vsync_active sample_delay period` | the same |
| `reboot` | none | `ok reboot`, then the board reboots into its flash firmware |
| `bootsel` | none | `ok bootsel`, then the board reboots into the USB bootloader |

* `clock hz=N` starts the project clock on GPIO 16, choosing the system
  clock the stock firmware would choose for that project clock (so a design
  runs, and is sampled, exactly as it is under the stock firmware) and
  MicroPython's PWM settings at it. `actual_hz` is what the PWM makes;
  `period` is system clocks per project clock if that is a whole 2..32
  (what frame capture needs), else 0. `hz=0` stops the clock and releases
  the pin.
* `reset hold=1` drives the project's `rst_n` (GPIO 14) low, `hold=0` high.
  Until the first `reset` the pin is left alone (the board pulls it up).
* `ui_in value=V` drives `ui_in` (GPIO 17..24) with V; `value=-1` releases
  the pins. Until then they are left alone: the FPGA boards take `ui_in`
  from their own switches.
* `program_stream` / `program_frame` print the PIO program a capture with
  those operands would load, in hex, with its configuration -- for
  diagnosis and for the tests that compare it with MicroPython's assembler.
  `period` is system clocks per sample, 2..32.
* `reboot` returns the board to the firmware in its flash (the stock
  firmware); `bootsel` is for loading another UF2 without it.

A host can always get the firmware into a known state by sending
`\x03\nhello\n` and discarding everything before the `ok vgacap-fw` reply:
the stop byte ends a capture left running by a host that went away, an
idle firmware ignores it, and the empty line ends any half-sent command.

## Captures

### `stream`

    stream buf_words=1..A/8 max_bytes=0..4294967295 edge=falling|rising pio=0..2 sm=0..3

Continuous capture, `python/ttcap/mp/capture_rp2.py`'s semantics: one sample
of `uo_out` on every chosen edge of the project clock, into two DMA
buffers of `buf_words` 32-bit words (4 samples each) that alternate. Each
full buffer is sent as a `RAW ` chunk, read from the live buffer. The DMA
starts rewriting a buffer as soon as the other one fills, so a fill that
starts on a buffer not yet completely sent is an *overrun*: that buffer
is torn, and its `RAW ` chunk (sent before, during or after the rewrite)
mixes two fills. A `TIME` chunk with the message `overrun` follows that
very `RAW ` chunk, before any other, its `dropped_samples` the cumulative
total so far (an upper bound: a rewrite that started during the send but
never overtook it counts too). vgacap-frames and vgadecode drop the
picture in progress there. `max_bytes` stops the capture once the chunks sent (RAW and
overrun TIME, headers included) reach it; 0 means only a stop byte ends it.
The project clock must be running.

### `frame`

    frame cpl=1..65535 region_first=0..65535 region_lines=1..65535 window_lines=0..65535
          hsync_active=-1..1 vsync_active=-1..1 max_frames=0..4294967295
          max_bytes=0..4294967295 edge=falling|rising sample_delay=0..31 pio=0..2 sm=0..3

Whole-frame and windowed capture, `frame_rp2.py`'s semantics: the project
clock runs at its real rate, the state machine waits for the vsync leading
edge, counts hsync leading edges to the first line wanted, then samples
once every `period` system clocks (phase-locked: the system clock is a
whole multiple of the project clock, which `clock` arranged) for a window
of whole lines into SRAM, which is sent between frames as one `FRAM`
chunk. `cpl` is clocks per line (a multiple of 4); line 0 is the first
line of the vsync pulse. A window is `window_lines` lines (0: the whole
region), or fewer if the capture buffer (`arena_bytes`) holds fewer;
windows sweep `region_lines` lines from `region_first`, and every window of
one sweep carries the same `frame_counter` (the picture number).
`hsync_active`/`vsync_active` are the sync levels during the pulse, or -1
to measure them (2000 reads 10 us apart; the idle level is the majority).
`sample_delay` is extra system clocks after the edge before each sample.
`max_frames` stops after that many pictures, `max_bytes` as for `stream`
(the opening TIME chunk counts); 0 means no limit. Refused unless the
project clock is running at 2..32 whole system clocks per clock.

### Capture output

After `ok stream` / `ok frame` the firmware writes binary vgacap chunks
(`include/vgacap/stream.h`): `tag[4]`, `u32` little-endian length, payload.
Only these:

* `RAW ` and `FRAM`, the samples, packed 4 to a word with the first
  sample in the most significant byte (`VGACAP_FLAG_FIRST_SAMPLE_MSB`);
* `TIME`, with `host_time_ns` and `clock_hz` zero and a message:
  * `frame window=W buffers=1 hsync_active=H vsync_active=V` opens a frame
    capture;
  * `overrun` (streaming) and `rxstall` (one frame window lost samples in
    the RX FIFO) as they happen;
  * the summary, always last: `overruns=N rxstall=R sysclk_hz=N`
    (streaming, `dropped_samples` the overrun total) or
    `frames=N stalls=N rxstall=R sysclk_hz=N` (frame).

Then the 8 bytes `DONE` + `u32 0`, which is not a vgacap chunk: it ends the
capture's output and is never part of the stream. After it the firmware
reads commands again.

The firmware writes no `VGCH` header: the host writes it, because only the
host knows the signal map, the project clock it programmed and the
description, exactly as on the MicroPython path. So a capture file is the
host's `VGCH` chunk followed by the firmware's chunks verbatim (without
`DONE`), and the vgacap readers, `vgacap-dump` and `vgacap-frames` read it
as they read any other.

### Stopping

During a capture the host may send `0x03` (or `q`). The firmware checks for
it only between chunks -- while waiting for the next buffer or window, and
after sending each chunk -- so a chunk is never cut short; it then writes
the summary `TIME` chunk and `DONE`. Any other byte received during a
capture is discarded.
