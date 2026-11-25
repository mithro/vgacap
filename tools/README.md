# tools/

Scripts that work against real [Tiny Tapeout](https://tinytapeout.com) demo boards:

- [`board_exec.py`](board_exec.py) runs a MicroPython file on a board through [ttcap](../python/ttcap/README.md)'s raw
  REPL.
- [`board_restart.py`](board_restart.py) puts a demo board back to the [SDK](https://github.com/TinyTapeout/tt-micropython-firmware)'s start state
  (Ctrl-B, Ctrl-D).
- [`hil.py`](hil.py) is the [hardware-in-the-loop check](#hardware-in-the-loop-check).

## Hardware-in-the-loop check

CI cannot see a board, so `tools/hil.py` runs the checks that only a real
board can settle and writes a dated Markdown report. The checks are:

- `probe`: profile auto-detection;
- `capture`: overruns and
  [`rxstall`](../firmware/PROTOCOL.md#capture-output), from the closing
  `TIME` chunk;
- `reconstruct`: frame count against the project clock;
- `reference`: pixel-exact against a [`tt-vga-testpatterns`](https://github.com/mithro/tt-vga-testpatterns) calibration
  render;
- `frame`: whole frames at the design's real clock, each one pixel-exact;
- `demo-frames`: a `ttcap demo` run writes PNGs at the requested frame rate;
- `video`: its video opens, at the requested rate and the expected length;
- `serve`: its MJPEG stream delivers at least two parts;
- `stop`: cooperative stop latency, and no orphaned processes;
- `board-usable`: the board still answers afterwards.

[`tools/hil.py`](hil.py) lists why each check needs hardware.

The example runs against `fpga-1`, a demo board with an FPGA breakout on
the shared Welland bench (see [python/ttcap/README.md](../python/ttcap/README.md#reaching-a-welland-board)).
It needs the calibration bitstream uploaded and enabled first, with
tt-vga-testpatterns'
[`tools/upload.py`](https://github.com/mithro/tt-vga-testpatterns/blob/main/tools/upload.py):

```sh
ssh -N -L 18034:10.21.2.34:8765 tweed.welland.mithis.com
tools/hil.sh --board fpga-1 --design tt_um_vgacal_bars --clock-hz 500000 \
    --link ws://127.0.0.1:18034/serial docs/results/
```

[`tools/hil.sh`](hil.sh) only runs `hil.py` in this checkout's uv environment;
`tools/hil.py --help` lists every option. Useful ones:

- `--no-demo` for a host without GStreamer (the pipeline checks then SKIP,
  with the reason);
- `--ui-in` for `tt_um_vgacal_modes`;
- `--stop-bridge` when running on the board's own Pi with
  `serial:/dev/ttboard` (it stops the fpgas.online bridge (`fpgas-tt`),
  which holds the device, and starts it again afterwards if it stopped it);
- `--testpatterns DIR` if tt-vga-testpatterns is not checked out next to
  vgacap (the `reference` and `frame` checks SKIP otherwise).

Every check prints PASS, FAIL or SKIP with what it measured. The exit
status is 0 if no check failed (SKIPs included), 1 if any failed, and 2 if
the run could not start (lock held, missing tool, bad arguments).

## Running on shared boards

The Welland boards are shared and public, so `hil.py` is careful with
them:

- one run per board at a time is enforced with a lock file (per user per
  machine; `--lock-dir` makes it shared);
- every board command has a timeout;
- whatever a run started is killed on the way out, including when the run
  itself gets SIGINT, SIGTERM, SIGHUP or SIGQUIT (it still writes its
  report).

Before running against a bench board, check its page on
[tinytapeout.fpgas.online](https://tinytapeout.fpgas.online) (`/board/<slug>/status.json`) for other clients,
and confirm the board's USB serial: boards move between ports.
