---
type: reference
owner: vgacap maintainers
reader: someone calling the vgacap MicroPython module, or reading what ttcap calls
review: 2027-05-27
---

# The vgacap module's Python API

This page lists what `import vgacap` gives on a demo-board MicroPython firmware built with the module, and on
the unix port built for its tests. It is for someone calling it from MicroPython, or reading what
[`python/ttcap/module.py`](../python/ttcap/module.py) sends. Its source is [`mpy/modvgacap.c`](modvgacap.c).

```python
import vgacap

vgacap.API            # 1

vgacap.stream(*, buf_words, max_bytes, edge, pio, sm,
              out=sys.stdout.buffer, stop=sys.stdin.buffer, buf=None)

vgacap.frame(*, clock_hz, cpl, region_first, region_lines, window_lines,
             hsync_active, vsync_active, max_frames, max_bytes, edge,
             sample_delay, pio, sm,
             out=sys.stdout.buffer, stop=sys.stdin.buffer, buf=None)
```

## Names

Name is what the module holds, Build the build that has it, and Meaning what it is.

| Name | Build | Meaning |
|---|---|---|
| `API` | both | the interface version ttcap checks, 1 |
| `stream()` | both | a streaming capture; returns `None` when it ends |
| `frame()` | both | a frame or window capture; returns `None` when it ends |
| `sim_board(path, *, bytes_per_s=1e6, rxstall=False)` | unix | starts the simulated board on a design file, one `uo_out` byte per project clock |
| `sim_clock(hz)` | unix | runs the simulated project clock as `tt.clock_project_PWM(hz)` would; returns `(sysclk_hz, actual_hz)` |
| `sim_now()` | unix | the simulated board's time, in seconds |
| `sim_frame_spans()` | unix | how many buffers the last frame window was captured into |

## Capture arguments

The capture arguments are keyword-only and all required. They are the `stream` and `frame` arguments of
[The vgacap firmware protocol](../firmware/PROTOCOL.md#arguments), with the same names, ranges and meanings, and
one more. Argument names it, Type gives the Python type, and Meaning what it sets beyond the protocol's meaning.

| Argument | Type | Meaning |
|---|---|---|
| any protocol argument | `int` | as in the protocol |
| `edge` | `str` | `"falling"` or `"rising"` |
| `clock_hz` | `int` | `frame()` only: the project clock the SDK runs, 1 to 125000000 |

`frame()` is told `clock_hz` because the SDK's `tt.clock_project_PWM()` runs the clock, not the module. It
refuses a capture whose system clock is not 2 to 32 whole periods of it.

## Environment arguments

Argument names an optional keyword argument, Default its value when left out, and Meaning what it does.

| Argument | Default | Meaning |
|---|---|---|
| `out` | `sys.stdout.buffer` | the stream the chunks are written to |
| `stop` | `sys.stdin.buffer` | the stream polled for a stop byte, `0x03` or `q`, between chunks; `None` for none |
| `buf` | `None` | a writable, 4-byte-aligned buffer to capture into, instead of buffers the module allocates |

## Buffers

Capture names the call, Without `buf` what the module allocates, and With `buf` how it uses the caller's buffer.

| Capture | Without `buf` | With `buf` |
|---|---|---|
| `stream()` | two buffers, `8 * buf_words` bytes in all | needs `8 * buf_words` bytes |
| `frame()` | heap blocks of whole lines, planned as below | a window of as many whole lines as fit |

`frame()` plans its blocks as [`frame_rp2.py`](../python/ttcap/mp/frame_rp2.py) does. Rule names a step of the
plan, and Value gives it.

| Rule | Value |
|---|---|
| First try | the whole window in one block |
| Then | blocks of 64, 16, 4 and 1 lines, biggest first |
| Most blocks | 128 |
| Held back while planning | 4 KB of heap |
| A window the blocks cannot hold | the region is swept in several windows |
| Blocks freed | when the call ends |

The opening `TIME` message, `frame window=W buffers=N ...`, gives the lines in a window and the blocks it
spans. The `FRAM` chunks are one for each window, whatever `N` is.

## Output

Item names a property of what a call writes, and Value gives it.

| Item | Value |
|---|---|
| Chunks | `RAW `, `FRAM` and `TIME`, exactly as the vgacap firmware writes them after its `ok` |
| `ok` line | none |
| `DONE` marker | none: the raw REPL's end of output ends a capture |
| `VGCH` header | none: the host writes it |
| Ctrl-C during the call | an ordinary byte, `micropython.kbd_intr(-1)` |
| Ctrl-C after the summary `TIME` chunk | a `KeyboardInterrupt` again |

## Exceptions

Exception names what a call raises, and When the condition.

| Exception | When |
|---|---|
| `TypeError` | a positional argument; a value that is neither `int` nor `str` |
| `ValueError` | an argument the firmware would refuse, with the text of its `err` reply, before anything is written |
| `ValueError` | `buf must be 4-byte aligned` |
| `MemoryError` | `stream()` without `buf`, when the heap cannot hold its two buffers |
| `RuntimeError` | `a capture is already running` |
| `OSError` | the `out` stream refused a write: the capture ended with nothing more written |
| any pending exception | raised by MicroPython's event handling during the capture: it ends at the next chunk, after the summary |

An exception during a capture is raised once the PIO and DMA have been given back. An exception in a
scheduled callback, from `micropython.schedule` or a soft timer, is printed to stdout, into the chunk stream,
and the capture carries on.

## Not in the module

Item names something the module does not do, and Where where it is done instead.

| Item | Where |
|---|---|
| The project clock | `tt.clock_project_PWM` |
| The project reset | `tt.reset_project` |
| `ui_in` | the SDK |
| Selecting a design | `tt.shuttle[...].enable()` |
| `asic_select` | the vgacap firmware only |
| PIO block 0 | nowhere: ttcap refuses `--pio 0`, and the module refuses a block whose program it cannot move |
