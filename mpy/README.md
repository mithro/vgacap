# vgacap MicroPython module

`vgacap` is a MicroPython C module (a `USER_C_MODULE`) that captures a Tiny
Tapeout design's VGA output on `uo_out` with the RP2350's PIO and DMA and
writes it as vgacap chunks. It is the third way to run the capture engines,
next to the MicroPython scripts (`python/ttcap/mp/`) and the pico-sdk
capture firmware (`firmware/`), and it shares the firmware's engines: the
module is a thin binding over `firmware/core/fw_capture.c` and
`firmware/pico/hal_rp2.c`, not a reimplementation.

Built into the demo board's firmware (the `vgacap-module` branch of
mithro/tt-micropython-firmware, which pins this repository as a submodule),
it leaves the board exactly as it was -- the ttboard SDK, `tt.shuttle`, the
FPGA loader, the REPL -- and turns a capture into one call instead of a
shipped script. The host side is `ttcap capture --firmware mpy-module`.

## API

    import vgacap

    vgacap.API            # 1: the interface version ttcap checks

    vgacap.stream(*, buf_words, max_bytes, edge, pio, sm,
                  out=sys.stdout.buffer, stop=sys.stdin.buffer, buf=None)

    vgacap.frame(*, clock_hz, cpl, region_first, region_lines, window_lines,
                 hsync_active, vsync_active, max_frames, max_bytes, edge,
                 sample_delay, pio, sm,
                 out=sys.stdout.buffer, stop=sys.stdin.buffer, buf=None)

Both return `None` once the capture has ended; everything they report is in
the stream.

**Capture arguments** are keyword-only, all required, and are the
`stream`/`frame` arguments of the firmware protocol
([firmware/PROTOCOL.md](../firmware/PROTOCOL.md)), with the same meanings
and ranges: the module turns them back into the protocol's `key=value` text
and parses it with the firmware's own tables. Integers are ints, `edge` is
`"falling"` or `"rising"`. A bad argument raises `ValueError` with exactly
the text of the firmware's `err` reply (`unknown key 'bogus'`, `pio must be
an integer 0..2, got '3'`, `sysclk 133000000 is not 2..32 whole project
clocks of 100000`, ...), before anything is written.

`frame()` also takes **`clock_hz`**, the project clock: on this firmware the
ttboard SDK runs the clock (`tt.clock_project_PWM(hz)`), so the module is
told it, and refuses a capture whose system clock is not a whole 2..32
multiple of it, as the firmware does.

**Environment arguments:**

* `out`: the stream the chunks are written to. In the raw REPL the default,
  `sys.stdout.buffer`, is what the capture scripts write to, so a host reads
  the output exactly as it reads theirs.
* `stop`: the stream polled for a stop byte (0x03, or `q`), only between
  chunks, so a chunk is never cut short; `None` for none (the capture then
  ends only by `max_bytes`/`max_frames`). While a capture runs, Ctrl-C is an
  ordinary byte (`micropython.kbd_intr(-1)`), and it is a KeyboardInterrupt
  again once the closing summary has been written -- the scripts' rule.
* `buf`: a writable, 4-byte-aligned buffer to capture into (a `bytearray`),
  instead of the ones allocated per call. A frame capture's window is as
  many lines as fit in it; `stream()` needs `8 * buf_words` bytes.

Without `buf`, `stream()` allocates its two buffers (`8 * buf_words` bytes;
`MemoryError` if they do not fit), and `frame()` allocates heap blocks of
whole lines for the window as `frame_rp2.py` plans them: the whole window
in one block if the heap has one, else blocks of 64, 16, 4 and 1 lines,
biggest first, at most 128, with 4 KB held back while planning. The
capture's DMA walks them in order through control blocks (as
`frame_rp2.py` does; `firmware/pico/hal_rp2.c`), so a fragmented heap
still gives one captured frame the whole region: on fpga-1, whose largest
free block held 188 lines, all 481 lines of 640x480 in a handful of blocks.
Only a heap with less free memory in total than the region sweeps a
picture in several windows. The opening `frame window=W buffers=N` TIME
message says how many lines a window takes and in how many blocks; the
FRAM chunks are the firmware's (one chunk per window whatever N is), whose
`buffers` is always 1 (its 440 KB static arena).

**Output:** vgacap chunks only, `RAW `/`FRAM`/`TIME`, exactly as the firmware
writes them after its `ok` (PROTOCOL.md, "Capture output"), and no `DONE`
marker: the raw REPL's own end of output ends a capture. The host writes
the `VGCH` header, as on the other two paths.

**Errors during a capture:** nothing MicroPython raises unwinds through an
engine (they hold the PIO and DMA). A failed write ends the capture with
nothing more written; a pending exception raised by MicroPython's event
handling (`mp_sched_exception()`, say) ends it at the next chunk, with the
summary written. Either way the exception is raised once the hardware has
been given back. An exception in a scheduled callback (`micropython.schedule`,
a soft timer) is not one of these: MicroPython prints it to stdout -- into
the chunk stream -- and carries on.

### What it borrows from MicroPython

For the length of one call: state machine `sm` of PIO block `pio`, taken the
way the scripts take it (`rp2.PIO(pio).remove_program()` for a block other
than 0, then `rp2.StateMachine(4 * pio + sm)`, which refuses one claimed
outside MicroPython); two DMA channels (`dma_claim_unused_channel`, shared
with `rp2.DMA`); and a shared handler on `DMA_IRQ_1` (`rp2.DMA` owns
`DMA_IRQ_0`) that acknowledges only those two channels. Block 0 holds the
stock firmware's FPGA loader: its window cannot move to GPIO 16 while that
is loaded, so `pio=0` is refused, as on the other paths. One capture at a
time (`RuntimeError` otherwise).

Not in the module: the project clock, reset, `ui_in` and selecting a
design. Those are the SDK's (`tt.clock_project_PWM`, `tt.reset_project`,
`tt.shuttle[...].enable()`), and ttcap drives them as on the script path.
The firmware's `asic_select` command and the `hal_asic_ctrl()` HAL hook
behind it are firmware-only: the module does not link the firmware's
command layer, so it neither offers nor needs them.

## Building

The rp2 port (the demo board, RP2350B, board definition
`psychogenic/tinytapeout_rp2350b`):

    make -C micropython/ports/rp2 BOARD=tt_rp2350_board \
        USER_C_MODULES=<vgacap>/mpy/micropython.cmake

The demo-board firmware build does this in mithro/tt-micropython-firmware
(`vgacap-module` branch, `.github/workflows/release.yml`), with this
repository as the `vgacap` submodule. Our sources build with `-Wall
-Wextra -Werror` (less `-Wunused-parameter`, which MicroPython's own headers
trip).

The unix port, on the simulated board (`firmware/sim`), for the tests:

    mpy/build-unix.sh [MICROPYTHON_DIR]     # -> build/mpy-unix/micropython
    cd python && uv run pytest -q tests/test_mpy_module.py

Both pin MicroPython to b006887db6 (1.29.0-preview), what the demo boards
run. The unix build adds `vgacap.sim_board(path, *, bytes_per_s, rxstall)`,
`vgacap.sim_clock(hz)`, `vgacap.sim_now()` and `vgacap.sim_frame_spans()`
(the buffers the last frame window was captured into), to set up and
inspect the simulated board; the rp2 build has none of them.

## Tests

`python/tests/test_mpy_module.py` runs the unix build behind a raw REPL
(`python/tests/mpy_unix_board.py` plays the board) and drives it through
ttcap: frame and window captures byte-identical to the pico-sdk firmware's
(same engines, same simulator) and pixel-exact through `vgacap-frames`,
a heap fragmented into ~144 KB holes giving all 481 lines in one captured
frame through chained buffers,
streams of the design's consecutive samples, stops between chunks,
overruns, rxstall, refusals, a caller's buffer, and `ttcap capture
--firmware mpy-module`. The tests skip without `build/mpy-unix/micropython`
unless `VGACAP_REQUIRE_MPY_UNIX` is set, as CI does.

## Files

| File | What |
|---|---|
| `modvgacap.c` | The binding: arguments, buffers, the module table. |
| `hal_mpy.c` | The host link over MicroPython streams, idling, and the PIO hand-over. |
| `micropython.cmake` | The rp2 build (with `firmware/pico/hal_rp2.c`). |
| `micropython.mk` | The unix build (with `firmware/sim/hal_sim.c`). |
| `build-unix.sh` | Builds the unix port with the module, at the pinned MicroPython. |
