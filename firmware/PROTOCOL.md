# vgacap firmware protocol, version 1

What a host sends the vgacap firmware ([`firmware/`](README.md)) over its USB CDC
serial port, and what comes back. The host side is [`python/ttcap/firmware.py`](../python/ttcap/firmware.py);
the firmware side is [`firmware/core/fw.c`](core/fw.c).

The serial settings do not matter (it is USB CDC), but the host must assert
DTR, as [pyserial](https://github.com/pyserial/pyserial) does when it opens a port: the firmware only writes while
DTR is set, and a capture whose host drops DTR stops.

A bridge that keeps the port open keeps DTR set for as long as it does.
The fpgas.online bridge (`fpgas-tt`) opens the port through
[pyserial-asyncio](https://github.com/pyserial/pyserial-asyncio), so DTR is asserted, and holds it open whether or not a
ws:// client is connected: a client that leaves mid-capture does not drop
DTR, and the capture runs on to `max_bytes` or the next host's `0x03`.

## Commands and replies

A command is one line of ASCII: a command word, then `key=value` arguments
separated by spaces, ending in `\n`. `0x03` discards the line so far;
`\r` and other control characters are ignored, an empty line does
nothing, and a line over 256 bytes is refused whole. Every argument a
command takes must be given exactly once; an unknown or repeated key is
an error, and so is a value outside its range.
There are no defaults: the host always says what it wants.

Each command gets exactly one reply line: `ok` followed by `key=value`
fields, or `err` followed by a message for a person.

| command | arguments | `ok` reply | after the reply |
|---|---|---|---|
| `hello` | none | `ok vgacap-fw proto=1 chip=rp2350 sysclk_hz=N arena_bytes=N` | -- |
| `status` | none | `ok clock_hz=N sysclk_hz=N reset=0\|1 ui_in=-1..255` | -- |
| `clock` | `hz=1..125000000` | `ok clock_hz=N actual_hz=N sysclk_hz=N period=P` | -- |
| `clock` | `hz=0` | `ok clock_hz=0 sysclk_hz=N` | -- |
| `reset` | `hold=0\|1` | `ok reset=0\|1` | -- |
| `ui_in` | `value=-1..255` | `ok ui_in=V` | -- |
| `asic_select` | `index=0..1023` | `ok asic_select index=N` | -- |
| `stream` | [see below](#stream) | `ok stream` | a capture |
| `frame` | [see below](#frame) | `ok frame` | a capture |
| `program_stream` | `edge=falling\|rising` | `ok words=... wrap_target=N wrap=N shiftctrl=0x... execctrl=0x...` | -- |
| `program_frame` | `edge=falling\|rising hsync_active=0..1 vsync_active=0..1 sample_delay=0..31 period=2..32` | as for `program_stream` | -- |
| `reboot` | none | `ok reboot` | the board reboots into its flash firmware |
| `bootsel` | none | `ok bootsel` | the board reboots into the USB bootloader |

* `clock hz=N` starts the project clock on GPIO 16, choosing the system
  clock the stock firmware would choose for that project clock (so a design
  runs, and is sampled, exactly as it is under the stock firmware) and
  MicroPython's PWM settings at it. `actual_hz` is what the PWM makes;
  `period` is system clocks per project clock if that is a whole 2..32
  (what frame capture needs), else 0. `hz=0` stops the clock and releases
  the pin. A clock the firmware cannot plan gets `err`, and the clock stays
  as it was; if setting the system clock fails after the PWM has stopped,
  the reply is `err`, the clock stays stopped and `status` reports
  `clock_hz=0`.
* `reset hold=1` drives the project's `rst_n` (GPIO 14) low, `hold=0` high.
  Until the first `reset` the pin is left alone (the board pulls it up).
* `ui_in value=V` drives `ui_in` (GPIO 17..24) with V; `value=-1` releases
  the pins. Until then they are left alone: a demo board with an FPGA
  breakout takes `ui_in` from its own switches.
* `asic_select index=N` (ASIC breakouts only) selects the project at mux
  index N exactly as the stock firmware's
  [`ProjectMux.reset_and_clock_mux()`](https://github.com/TinyTapeout/tt-micropython-firmware/blob/15ba5ced7aae7746ea2f9004cb412109c9fe9c83/src/ttboard/project_mux.py)
  does: `ctrl_sel_inc` (GPIO 2), `ctrl_sel_nrst` (GPIO 1) and `ctrl_ena`
  (GPIO 0) low, 10 ms, `ctrl_sel_nrst` high, 10 ms, N pulses on
  `ctrl_sel_inc` (1 ms high, 1 ms low), then `ctrl_ena` high. N is the
  stock SDK's `tt.shuttle.<name>.count`. The reply comes after about
  20 ms + 2 ms per index. **Never on an FPGA breakout**: GPIO 1 is the
  iCE40's `CRESET_B` there, and pulling it low erases the FPGA's
  configuration; `asic_select` is the only thing that ever drives it low.
  Why it is needed, and when `ttcap` refuses it:
  [README.md, "Hardware notes"](README.md#hardware-notes-and-assumptions).
* `program_stream` / `program_frame` print the PIO program a capture with
  those operands would load, in hex, with its configuration -- for
  diagnosis and for the tests that compare it with MicroPython's assembler.
  `period` is system clocks per sample, 2..32.
* `reboot` returns the board to the firmware in its flash (the stock
  firmware); `bootsel` is for loading another UF2 without it.

At boot the firmware drives GPIO 1 high and makes `uo_out` (GPIO 33..40)
inputs; every other pin stays as the bootloader left it until a command
drives it ([Hardware notes](README.md#hardware-notes-and-assumptions)).

A host can always get the firmware into a known state by sending
`\x03\nhello\n` and discarding everything before the `ok vgacap-fw` reply:
the stop byte ends a capture left running by a host that went away, and
an idle firmware discards the line received so far on it, so a half-sent
command (say `reboo`) is never completed by what follows; the empty line
then does nothing.

## Captures

### `stream`

    stream buf_words=1..arena_bytes/8 max_bytes=0..4294967295 edge=falling|rising pio=0..2 sm=0..3

Continuous capture, [`python/ttcap/mp/capture_rp2.py`](../python/ttcap/mp/capture_rp2.py)'s semantics: one sample
of `uo_out` on every chosen edge of the project clock, into two DMA
buffers of `buf_words` 32-bit words (4 samples each) that alternate. Each
full buffer is sent as a `RAW ` chunk, read from the live buffer. The DMA
starts rewriting a buffer as soon as the other one fills, so a fill that
starts on a buffer not yet completely sent is an *overrun*: that buffer
is torn, and its `RAW ` chunk (sent before, during or after the rewrite)
mixes two fills. A `TIME` chunk with the message `overrun` follows that
very `RAW ` chunk, before any other, its `dropped_samples` the cumulative
total so far (an upper bound: a rewrite that started during the send but
never overtook it counts too). [vgacap-frames](../src/tools/README.md#overruns)
and [vgadecode](../gst/README.md#vgadecode-a-stream-in-video-out) drop the
picture in progress there. `max_bytes` stops the capture once the chunks sent (RAW and
overrun TIME, headers included) reach it; 0 means only a stop byte ends it.
The project clock must be running.

### `frame`

    frame cpl=1..65535 region_first=0..65535 region_lines=1..65535 window_lines=0..65535
          hsync_active=-1..1 vsync_active=-1..1 max_frames=0..4294967295
          max_bytes=0..4294967295 edge=falling|rising sample_delay=0..31 pio=0..2 sm=0..3

Whole-frame and windowed capture, [`frame_rp2.py`](../python/ttcap/mp/frame_rp2.py)'s semantics: the project
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
([`include/vgacap/stream.h`](../include/vgacap/stream.h)): `tag[4]`, `u32` little-endian length, payload.
Only these:

* `RAW ` and `FRAM`, the samples, packed 4 to a word with the first
  sample in the most significant byte (`VGACAP_FLAG_FIRST_SAMPLE_MSB`);
* `TIME`, with `host_time_ns` and `clock_hz` zero and a message:
  * `frame window=W buffers=1 hsync_active=H vsync_active=V` opens a frame
    capture (the firmware's one arena; the [MicroPython module](../mpy/README.md) and
    `frame_rp2.py` say how many heap blocks a window is captured into);
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
`DONE`), and the vgacap readers,
[`vgacap-dump` and `vgacap-frames`](../src/tools/README.md), read it
as they read any other.

### Stopping

During a capture the host may send `0x03` (or `q`). The firmware checks for
it only between chunks -- while waiting for the next buffer or window, and
after sending each chunk -- so a chunk is never cut short; it then writes
the summary `TIME` chunk and `DONE`. Any other byte received during a
capture is discarded.
