---
type: reference
owner: vgacap maintainers
reader: someone loading an FPGA bitstream from Python with no clock edge before clock 0
review: 2027-05-28
---

# The held-clock bitstream load, `load_held`

**What `load_held(repl, req, path_on_host)` sends to a demo board, what it returns, the errors it raises, and how `RawRepl.upload()` behaves under it.**

`load_held` is in [`capture.py`](capture.py), and its board script is [`mp/load_held_rp2.py`](mp/load_held_rp2.py). It loads the bitstream with the project clock held low and the design held in reset. It has no `ttcap` subcommand.

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
| `rst_n` | held at 0 |
| Project clock pad | driven at 0 |
| Input mode | as stage 4 left it |
| Manual-clock monitor | off |
| `/_vgacap` | kept |
| `/_vgacap/held.bin` | deleted |
| `tt.shuttle.enabled` | the design enabled before the load |
| fpgas.online daemon's design | the design enabled before the load |

`restore_mode` or `undo_selection` on the returned dict puts the input mode and the monitor back. Recording the load against the board's claim belongs to the caller, not to `load_held`.

## Errors

Message is the start of the error. Raised as is its type. Cause is what made it, and Board the state it is left in.

| Message | Raised as | Cause | Board |
|---|---|---|---|
| `load_held loads its own bitstream` | `ValueError` | the request names a design | untouched |
| `board rejected def _ttcap_mkdir` | `CaptureError` | stage 1 failed | untouched |
| `upload of '/_vgacap/held.bin' did not verify` | `UploadError` | the file's length or crc32 differs | file kept |
| `rst_n reads 1 while reset is held` | `CaptureError` | stage 7 failed | put back |
| `the loader failed and carried on` | `CaptureError` | the loader printed `Error accessing file` | put back |
| `the loader printed no 'Transmission complete` | `CaptureError` | the loader printed no count | put back |
| `the loader fed the FPGA N bytes` | `CaptureError` | the count is not the file's length | put back |
| `rst_n reads 1 after the load` | `CaptureError` | reset was released during the load | put back |
| `the clock pad reads 1 after the load` | `CaptureError` | the clock pad rose during the load | put back |
| `the held load said nothing` | `CaptureError` | the board script printed nothing | put back |
| `the held load failed on the board` | `CaptureError` | the board script raised | put back |
| `timed out waiting for` | `TimeoutError` | the board script ran past `LOAD_HELD_TIMEOUT` | as it was |

Put back means the file deleted, reset released, the clock stopped, and the mode and monitor restored. After a timeout `load_held` sends nothing more, because the loader may still be running. The caller then calls `repl.recover()` before anything else.

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
