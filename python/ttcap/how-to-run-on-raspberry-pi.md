---
type: how-to
owner: vgacap maintainers
reader: someone who wants ttcap on the Raspberry Pi a demo board is plugged into
review: 2027-05-27
---

# How to run ttcap on a Raspberry Pi

**You want `ttcap` on a Raspberry Pi, with only the packages it needs to drive a board.**

`ttcap` needs only [pyserial](https://github.com/pyserial/pyserial) and [websockets](https://github.com/python-websockets/websockets). The checkout's `dev` group adds numpy, Pillow and other test packages, which a Pi does not need, so this page leaves it out. Turning captures into pictures is better done on another machine.

## What you need

- A Raspberry Pi with Git, network access, and the demo board on USB.
- No other program holding the board's serial port. On a Welland bench Pi the bridge holds it, so stop `fpgas-tt` first, as in [How to capture on a Welland board's own Pi](how-to-capture-welland-pi.md).
- [uv](https://docs.astral.sh/uv/) built for the Pi's userland, which can differ from its kernel.

## Steps

1. On the Pi, run `getconf LONG_BIT`, which prints 32 for a 32-bit userland and 64 for a 64-bit one.
2. On a Pi that printed 32, install uv's static 32-bit build, which then prints `uv 0.10.8`:

   ```sh
   curl -LO https://github.com/astral-sh/uv/releases/download/0.10.8/uv-armv7-unknown-linux-musleabihf.tar.gz
   tar -xzf uv-armv7-unknown-linux-musleabihf.tar.gz
   mkdir -p ~/.local/bin && cp uv-armv7-unknown-linux-musleabihf/uv ~/.local/bin/
   export PATH=$HOME/.local/bin:$PATH
   uv --version
   ```

3. On the Pi, clone vgacap and sync it without the `dev` group, which installs `ttcap`, pyserial and websockets alone:

   ```sh
   git clone https://github.com/mithro/vgacap.git && cd vgacap
   export UV_NO_DEV=1
   uv sync
   ```

4. In the checkout, run `ttcap` with `--no-sync`, so uv does not add the `dev` group back:

   ```sh
   uv run --no-sync ttcap probe serial:/dev/ttyACM0
   ```

The `export` in step 2 matters where the Pi already has a uv. A bench Pi also has uv 0.13.1 in `/usr/local/bin`, which a shell without `~/.local/bin` first on `PATH` will find ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)). A Pi that printed 64 in step 1 takes uv from its [installation page](https://docs.astral.sh/uv/getting-started/installation/) and skips step 2. On this checkout `ttcap png` needs Pillow, from `uv sync --extra synth`, and the C tools.

## Check

Step 3 ends by installing three packages:

```text
 + pyserial==3.5
 + vgacap==0.0.1 (from file:///home/pi/vgacap)
 + websockets==17.1
```

Step 4 prints the board's `sys.version` and `GPIOMap.all()` ([How to probe a demo board](how-to-probe-board.md)). This setup ran `ttcap capture` on the tt07 board's own Pi ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-09-15-ttcap-capture-serial-tt07)). On fpga-4's Pi, a 32-bit userland, steps 1 to 3 installed these three packages and no others ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)).

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `uv --version` names another version than 0.10.8 | another uv comes first on `PATH` | repeat the `export` from step 2 |
| 3 | `uv: No such file or directory` for a file that exists | a 64-bit uv on a 32-bit userland | do step 2 |
| 3 | numpy or Pillow being built | `UV_NO_DEV` is not set in this shell | repeat `export UV_NO_DEV=1` |
| 4 | `Permission denied` on the device | the user cannot open the port | add the user to `dialout` and log in again |
| 4 | `probe failed: TimeoutError:` | another program holds the port | stop it, as on [the Welland bench](how-to-capture-welland-pi.md) |

## Next

- [How to capture a stream from a demo board](how-to-capture-stream.md)
- [How to capture on a Welland board's own Pi](how-to-capture-welland-pi.md)
