# tools/

Scripts that work against real Tiny Tapeout demo boards:

- `board_exec.py` runs a MicroPython file on a board through ttcap's raw
  REPL.
- `board_restart.py` puts a demo board back to the SDK's start state
  (Ctrl-B, Ctrl-D).
- `hil.py` is the hardware-in-the-loop check, described next.

## Hardware-in-the-loop check

CI cannot see a board, so `tools/hil.py` runs the checks that only a real
board can settle and writes a dated Markdown report. The checks are:

- `probe`: profile auto-detection;
- `capture`: overruns and `rxstall`, from the closing `TIME` chunk;
- `reconstruct`: frame count against the project clock;
- `reference`: pixel-exact against a `tt-vga-testpatterns` calibration
  render;
- `demo-frames`, `video` and `serve`;
- `stop`: cooperative stop latency, and no orphaned processes;
- `board-usable`: the board still answers afterwards.

`tools/hil.py` lists why each check needs hardware.

The example runs against `fpga-1`, an FPGA board on the shared Welland
bench (see [python/ttcap/README.md](../python/ttcap/README.md#reaching-a-welland-board)),
whose Pi is at 10.21.2.34:

```sh
# from a workstation: a tunnel to the board's bridge (local port 18000 + the last octet of the Pi's address)
ssh -N -L 18034:10.21.2.34:8765 tweed.welland.mithis.com
# upload and enable the calibration bitstream (tt-vga-testpatterns/tools/upload.py), then
tools/hil.sh --board fpga-1 --design tt_um_vgacal_bars --clock-hz 500000 \
    --link ws://127.0.0.1:18034/serial docs/results/
```

`tools/hil.sh` only runs `hil.py` in this checkout's uv environment;
`tools/hil.py --help` lists every option. Useful ones:

- `--no-demo` for a host without GStreamer (the pipeline checks then SKIP,
  with the reason);
- `--ui-in` for `tt_um_vgacal_modes`;
- `--stop-bridge` when running on the board's own Pi with
  `serial:/dev/ttboard` (it stops `fpgas-tt`, which holds the device, and
  always restarts it).

Every check prints PASS, FAIL or SKIP with what it measured. The exit
status is 0 if all checks passed, 1 if any failed, and 2 if the run could
not start (lock held, missing tool, bad arguments).

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
tinytapeout.fpgas.online (`/board/<slug>/status.json`) for other clients,
and confirm the board's USB serial: boards move between ports.
