# vgacap MicroPython module

`vgacap` is a [MicroPython](https://micropython.org) C module (a `USER_C_MODULE`) that captures a [Tiny
Tapeout](https://tinytapeout.com) design's VGA output on `uo_out` with the PIO and DMA of the
RP2350 on the Tiny Tapeout demo board, and writes it as vgacap chunks.

It is the third way to capture on the RP2350 demo board, next to the
MicroPython scripts ([`python/ttcap/mp/`](../python/ttcap/mp/)) and the pico-sdk capture firmware
([`firmware/`](../firmware/README.md)). It shares the firmware's capture
engines: the module is a thin binding over [`firmware/core/fw_capture.c`](../firmware/core/fw_capture.c)
and [`firmware/pico/hal_rp2.c`](../firmware/pico/hal_rp2.c), not a reimplementation.

The module is built into the demo board's MicroPython firmware (the
[`vgacap-module` branch of mithro/tt-micropython-firmware](https://github.com/mithro/tt-micropython-firmware/tree/vgacap-module), which pins this
repository as a submodule). The board otherwise stays exactly as it was
(the [ttboard SDK](https://github.com/TinyTapeout/tt-micropython-firmware), `tt.shuttle`, the FPGA loader, the REPL), and a capture
becomes one call instead of a script shipped over the REPL. The host side
is `ttcap capture --firmware mpy-module`.

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

Without `buf`, `stream()` allocates its two buffers (`8 * buf_words`
bytes; `MemoryError` if they do not fit).

`frame()` allocates heap blocks of whole lines for the window, planned as
[`frame_rp2.py`](../python/ttcap/mp/frame_rp2.py) plans them: the whole window in one block if the heap has
one, else blocks of 64, 16, 4 and 1 lines, biggest first, at most 128
blocks, with 4 KB held back while planning. The capture's DMA walks the
blocks in order through control blocks (as [`frame_rp2.py`](../python/ttcap/mp/frame_rp2.py) does; see
[`firmware/pico/hal_rp2.c`](../firmware/pico/hal_rp2.c)), so even a fragmented heap gives one captured
frame the whole region. On `fpga-1`, a demo board with an FPGA breakout
on the [Welland bench](../python/ttcap/README.md#reaching-a-welland-board) whose largest free block held 188 lines, all 481
lines of 640x480 fitted in 46 blocks. Only a heap with less free memory in total
than the region sweeps a picture in several windows.

The opening `frame window=W buffers=N` TIME message says how many lines a
window takes and in how many blocks. The FRAM chunks are the firmware's
(one chunk per window, whatever N is); the firmware's own `buffers` is
always 1 (its 440 KB static arena).

**Output:** vgacap chunks only, `RAW`/`FRAM`/`TIME`, exactly as the firmware
writes them after its `ok` ([PROTOCOL.md, "Capture output"](../firmware/PROTOCOL.md#capture-output)), and no `DONE`
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

The steps follow the `mpy-module` job in
[`ci.yml`](../.github/workflows/ci.yml). [`mpy/build-unix.sh`](build-unix.sh) clones
MicroPython into `build/micropython` and pins it, then builds the unix port
with the module on the simulated board ([`firmware/sim`](../firmware/sim/))
for the tests:

    cmake -S . -B build && cmake --build build -j
    mpy/build-unix.sh                       # -> build/mpy-unix/micropython
    uv run pytest -q python/tests/test_mpy_module.py

CI also compiles the rp2 port (RP2350B) with the module, as a compile
check only: that build has no frozen `ttboard` or `microcotb` SDK, which
`--firmware mpy-module` needs. On a board, use the
[release UF2](https://github.com/mithro/tt-micropython-firmware/releases/tag/dev-vgacap-module),
loaded and restored as in
[tt-vga-capture's fpga-1 run](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1),
which replaced only the firmware region and put it back afterwards. The
compile check uses the board definition
[`psychogenic/tinytapeout_rp2350b`](https://github.com/psychogenic/tinytapeout_rp2350b)
in `build/micropython/ports/rp2/boards/tt_rp2350_board` and needs
[the ARM toolchain](../firmware/README.md#building):

    git clone -q https://github.com/psychogenic/tinytapeout_rp2350b.git build/tt_rp2350_board
    git -C build/tt_rp2350_board checkout -q 596f456
    cp -r build/tt_rp2350_board build/micropython/ports/rp2/boards/tt_rp2350_board
    make -C build/micropython/ports/rp2 BOARD=tt_rp2350_board submodules
    make -C build/micropython/ports/rp2 -j"$(nproc)" BOARD=tt_rp2350_board \
        USER_C_MODULES="$PWD/mpy/micropython.cmake"

Our sources build with `-Wall -Wextra -Werror` (less `-Wunused-parameter`,
which MicroPython's own headers trip).

Both pin MicroPython to [b006887db6](https://github.com/micropython/micropython/commit/b006887db6) (1.29.0-preview), what fpga-1 and fpga-4
run. The unix build adds `vgacap.sim_board(path, *, bytes_per_s, rxstall)`,
`vgacap.sim_clock(hz)`, `vgacap.sim_now()` and `vgacap.sim_frame_spans()`
(how many buffers the last frame window was captured into), to set up and
inspect the simulated board; the rp2 build has none of them.

## Tests

[`python/tests/test_mpy_module.py`](../python/tests/test_mpy_module.py) runs the unix build behind a raw REPL
([`python/tests/mpy_unix_board.py`](../python/tests/mpy_unix_board.py) plays the board) and drives it through
ttcap. It checks:

* frame and window captures byte-identical to the pico-sdk firmware's
  (same engines, same simulator) and pixel-exact through
  [`vgacap-frames`](../src/tools/README.md);
* a heap fragmented into ~144 KB holes giving all 481 lines in one
  captured frame through chained buffers;
* streams of the design's consecutive samples;
* stops between chunks, overruns, refusals and a caller's buffer;
* `ttcap capture --firmware mpy-module`.

The tests skip without `build/mpy-unix/micropython` unless
`VGACAP_REQUIRE_MPY_UNIX` is set, as it is in CI.

## Files

| file | port | contents | HAL source built in |
|---|---|---|---|
| [`modvgacap.c`](modvgacap.c) | both | Python binding | -- |
| [`hal_mpy.c`](hal_mpy.c) | both | host link over MicroPython streams | -- |
| [`micropython.cmake`](micropython.cmake) | rp2 | module build rules | [`firmware/pico/hal_rp2.c`](../firmware/pico/hal_rp2.c) |
| [`micropython.mk`](micropython.mk) | unix | module build rules | [`firmware/sim/hal_sim.c`](../firmware/sim/hal_sim.c) |
| [`build-unix.sh`](build-unix.sh) | unix | build script at the pinned MicroPython | -- |
