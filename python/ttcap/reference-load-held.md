---
type: reference
owner: vgacap maintainers
reader: someone loading an FPGA bitstream from Python with no clock edge before clock 0
review: 2027-05-28
---

# The held-clock bitstream load, `load_held`

**What `load_held(repl, req, path_on_host)` sends to a demo board, what it returns, the errors it raises, and how `RawRepl.upload()` behaves under it.**

`load_held` is in [`capture.py`](capture.py), and its board script is [`mp/load_held_rp2.py`](mp/load_held_rp2.py). It loads the bitstream with the project clock held low and `rst_n` held low. It has no `ttcap` subcommand.

## What it refuses

Check is what `load_held` looks at before any board command, and Refused when the case it raises `ValueError` for.

| Check | Refused when |
|---|---|
| The request | it names a design |
| The board profile | its chip is not the RP2350 |
| The board profile's clock pin | it is not GPIO 16 |
| The file | it is empty |
| The file's first 1024 bytes | they hold no iCE40 sync word `7E AA 99 7E` |

The loader clears the FPGA before it sends a byte, and its count matches an empty or wrong file all the same. The window is `SYNC_WINDOW`. The [tt-vga-testpatterns](https://github.com/mithro/tt-vga-testpatterns) bitstreams have the sync word at byte 4, and 1024 bytes leaves room for a comment block before it.

## What it sends

Stage is the order, and When the requests or boards it applies to. Board command is what reaches the board, and Checked the read-back the host requires.

| Stage | When | Board command | Checked |
|---|---|---|---|
| 1 | always | `os.mkdir("/_vgacap")` | any error but `EEXIST` fails |
| 2 | always | `RawRepl.upload()` to `/_vgacap/held.bin` | length and crc32 |
| 3 | always | `tt.clock_project_stop()` | no traceback |
| 4 | with `ui_in` | `tt.mode = RPMode.ASIC_RP_CONTROL` | no traceback |
| 5 | on boards with a manual-clock monitor | `tt.manual_project_clock.monitoring = False` | no traceback |
| 6 | with `ui_in` | `tt.ui_in.value = N` | `ui_in` reads N |
| 7 | always | `tt.reset_project(True)` | `rst_n` reads 0 |
| 8 | always | `tt.pins.safe_bidir()` | no traceback |
| 9 | always | `tt.clock_project_stop()` | no traceback |
| 10 | always | `Pin(16, Pin.OUT, value=0)` | no traceback |
| 11 | always | `fabricfoxv2.spi_transferPIO("/_vgacap/held.bin")` | `Transmission complete, total bytes: N` once |
| 12 | always | `tt.rst_n()` | 0 |
| 13 | always | `Pin(16).value()` | 0 |
| 14 | always | `os.remove("/_vgacap/held.bin")` | no traceback |

Stages 3 to 7 are the reset sequence `select_project` uses, stopped before the clock starts ([#57](https://github.com/mithro/vgacap/issues/57)). Stages 8 to 13 are one command, the board script. The loader catches its own `OSError` and only prints it, so stage 11 is checked from what it printed.

## What it returns

Key is the key of the returned dict, and Value what it holds. The keys are those `select_project` returns, with `bytes` added and `rst_n_wait_ms` left out. A key whose stage did not run is absent.

| Key | Value |
|---|---|
| `project` | `None` |
| `enable` | `"held"` |
| `clock_hz` | the request's `clock_hz` |
| `mode_before` | the mode before stage 4 |
| `monitoring_before` | the monitor before stage 5 |
| `ui_in` | the request's `ui_in` |
| `bytes` | the loader's byte count |

## The board afterwards

Item is a part of the board's state, and State what `load_held` leaves it in.

| Item | State |
|---|---|
| The design | at its power-up value |
| `rst_n` | held at 0 |
| Project clock pad | driven at 0 by a `machine.Pin` |
| Input mode | as stage 4 left it |
| Manual-clock monitor | off |
| `/_vgacap` | kept |
| `/_vgacap/held.bin` | deleted |
| `tt.shuttle.enabled` | the design enabled before the load |
| fpgas.online daemon's design | the design enabled before the load |

The design has seen no clock edge since the load, so a design with a synchronous reset has not been reset. The clocked capture that follows must clock it with `rst_n` still low for its reset cycles, then release reset before clock 0.

Until that capture drives the clock pin, nothing may do any of these:

- call `tt.clock_project_stop()` or `stop_clock`;
- call `undo_selection` or `restore_mode`;
- write `tt.mode`.

Each one un-drives GPIO 16 and leaves it to the RP2's pull-down, and a mode write releases reset too. `mode_before` and `monitoring_before` are for putting the board back after the capture.

`tt.shuttle.enabled` still names the design enabled before the load. A later `select_project` naming that design takes it as already enabled, and captures whatever the FPGA holds. [`restart_sdk`](repl.py) clears the stale name: the soft reset makes the SDK load its default project again. Recording the load against the board's claim belongs to the caller, not to `load_held`.

## Errors

Message is the start of the error. Raised as is its type. Cause is what made it, and Board the state it is left in.

| Message | Raised as | Cause | Board |
|---|---|---|---|
| `load_held loads its own bitstream` | `ValueError` | the request names a design | untouched |
| `load_held needs the FPGA breakout's demo board` | `ValueError` | the profile is not the RP2350 board with the clock on GPIO 16 | untouched |
| `the bitstream is empty` | `ValueError` | the file has no bytes | untouched |
| `no iCE40 sync word 7E AA 99 7E` | `ValueError` | the file is not an iCE40 bitstream | untouched |
| `board rejected def _ttcap_mkdir` | `CaptureError` | stage 1 failed | untouched |
| `remote error executing` | `RuntimeError` | the board refused a stage 2 write | file as far as written |
| `timed out waiting for` | `TimeoutError` | stage 2 got no reply | file as far as written |
| `upload of '/_vgacap/held.bin' did not verify` | `UploadError` | the file's length or crc32 differs | file kept |
| `rst_n reads 1 while reset is held` | `CaptureError` | stage 7 failed | put back |
| `the loader failed and carried on` | `CaptureError` | the loader printed `Error accessing file` | put back |
| `the loader printed no 'Transmission complete` | `CaptureError` | the loader printed no count | put back |
| `the loader fed the FPGA N bytes` | `CaptureError` | the count is not the file's length | put back |
| `rst_n reads 1 after the load` | `CaptureError` | reset was released during the load | put back |
| `the clock pad reads 1 after the load` | `CaptureError` | the clock pad rose during the load | put back |
| `the held load said nothing` | `CaptureError` | the board script printed nothing | put back |
| `the held load failed on the board` | `CaptureError` | the board script raised | put back |
| `timed out waiting for` | `LoadHeldTimeout` | the board script ran past `LOAD_HELD_TIMEOUT` | mid-load |

Put back means the file deleted, reset released, the clock stopped, and the mode and monitor restored. A stage 3 to 7 failure that is a timeout leaves the file on the board.

## After a body timeout

Item is a part of the board's state, and State what a `LoadHeldTimeout` leaves it in. `load_held` sends nothing more, because the loader may still be running.

| Item | State |
|---|---|
| Input mode | as stage 4 left it |
| Manual-clock monitor | off |
| `rst_n` | held at 0 |
| Project clock pad | driven at 0 once stage 10 ran |
| FPGA | blank or partly configured |
| `/_vgacap/held.bin` | kept |

The exception's `selection` holds what `load_held` would have returned, without `bytes`. The caller calls `repl.recover()`, then `undo_load_held(repl, exc.selection)`, which puts the board back as above.

## `RawRepl.upload()`

Case is what [`RawRepl.upload()`](repl.py) finds or meets. Writes says whether it writes the file, Result what it returns or raises, and File the file afterwards ([#88](https://github.com/mithro/vgacap/issues/88)).

| Case | Writes | Result | File |
|---|---|---|---|
| The same length and crc32 already there | no | `False` | as it was |
| Any other file or none | yes | `True` | the data |
| The written file does not verify | yes | `UploadError` | as written |
| A board reply it does not expect | stops | `RuntimeError` | as far as written |
| A timeout | stops | `TimeoutError` | write handle left open |

Rule is one more thing a caller of `upload()` relies on, and Value the rule.

| Rule | Value |
|---|---|
| REPL global it reserves | `_ttcap_up_f` |
| Parent directory | not made |
| After a timeout | `recover()` before the next command |
