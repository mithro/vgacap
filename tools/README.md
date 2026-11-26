# tools/

Scripts that work against real boards. `board_exec.py` runs a MicroPython file on a board through ttcap's raw REPL; `board_restart.py` puts a demo board back to the SDK's start state (Ctrl-B, Ctrl-D); `hil.py` is the hardware-in-the-loop check below.

## Hardware-in-the-loop check

CI cannot see a board. `tools/hil.py` runs the checks only a real board can
settle and writes a dated Markdown report: `probe` (profile auto-detection),
`capture` (overruns and `rxstall` from the closing `TIME` chunk), `reconstruct`
(frame count against the project clock), `reference` (pixel-exact against a
`tt-vga-testpatterns` calibration render), `demo-frames`, `video`, `serve`,
`stop` (cooperative stop latency, no orphaned processes) and `board-usable`
(the board still answers afterwards). `tools/hil.py` lists why each one needs
hardware.

```sh
# from a workstation: a tunnel to the board's bridge (18000 + the Pi's port)
ssh -N -L 18034:10.21.2.34:8765 tweed.welland.mithis.com
# upload and enable the calibration bitstream (tt-vga-testpatterns/tools/upload.py), then
tools/hil.sh --board fpga-1 --design tt_um_vgacal_bars --clock-hz 500000 \
    --link ws://127.0.0.1:18034/serial docs/results/
```

`tools/hil.sh` only runs `hil.py` in this checkout's uv environment;
`tools/hil.py --help` gives every option. Useful ones: `--no-demo` for a host
without GStreamer (the pipeline checks then SKIP with the reason), `--ui-in`
for `tt_um_vgacal_modes`, `--stop-bridge` when running on the board's own Pi
with `serial:/dev/ttboard` (it stops `fpgas-tt`, which holds the device, and
always restarts it; it refuses unless the bridge's `/health` says 0 clients
on two reads a few seconds apart, but that count is WebSocket serial sessions
only, so an in-flight HTTP request -- a design enable, a bitstream upload, a
demo sync -- is not seen). Every check prints PASS, FAIL or SKIP with what it
measured. Exit status is 0 if all passed, 1 if any failed, 2 if it could not
start (lock held, missing tool, bad arguments).

The Welland boards are shared and public. One run per board at a time is
enforced with a lock file (per user per machine; `--lock-dir` makes it
shared), every board command has a timeout, and whatever a run started is
killed on the way out, including when the run itself gets SIGINT, SIGTERM,
SIGHUP or SIGQUIT (it still writes its report). Before running against a bench board,
check its page on tinytapeout.fpgas.online (`/board/<slug>/status.json`) for
other clients, and confirm the board's USB serial: boards move between
ports.
