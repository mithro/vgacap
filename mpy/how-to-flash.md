---
type: how-to
owner: vgacap maintainers
reader: someone with a demo board who wants the vgacap module on it for a while, and the board exactly as it was afterwards
review: 2027-05-27
---

# How to flash the module firmware and restore the board's own

**You want a demo board running the module firmware for a while, and afterwards its own firmware back, byte for
byte.**

[The module firmware](explanation-borrowing.md#the-module-firmware) lives in the board's
flash, so this page backs up the board's firmware region and every file first. It writes
only the firmware region, then restores the backup. It follows the
[fpga-1 run](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-mpy-module-fpga1), which
needed no power cycle.

That run made its module image with `uf2tool.py cut $IMAGE module.uf2 0x100C8000`. This page uses `tobin` and
`frombin` instead, which also cover a `firmware.uf2` that stops short of `0x100C8000`. For the release UF2 the two
give byte-identical images, whose region hashes to `5dc230b2…`, the hash fpga-1 reported.

## What you need

- One Tiny Tapeout demo board v3 (RP2350B), running the stock firmware, with a USB cable to a Linux machine.

  <img src="../docs/diagrams/images/tt-etr-dbv3p2.jpg" alt="The Tiny Tapeout demo board v3, with a breakout plugged into its centre socket." width="240">

- On that machine: `sudo`, Python 3, `curl`, `timeout` and [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html).
- On that machine, a vgacap checkout set up to run `ttcap`, as on [ttcap](../python/ttcap/README.md), for steps 14 and 20.
- `DEV`, the stock firmware's serial port, set in the shell. On the Welland bench it is `/dev/ttboard` on the board's own Pi.
- `IMAGE`, the module firmware's UF2, set in the shell. It is the `tt-demo-rp2350-vgacap-module-*.uf2` asset of the
  [`dev-vgacap-module` pre-release](https://github.com/mithro/tt-micropython-firmware/releases/tag/dev-vgacap-module),
  or the `firmware.uf2` from [How to build the module firmware](how-to-build-release.md).
- An empty working directory with about 10 MB free, and `/mnt/rp2350`, made with `sudo mkdir -p /mnt/rp2350`.

The board's flash holds the firmware region, `0x10000000` to `0x100C8000`, then the littlefs filesystem from
`0x100C8000`. Every image written here stops at `0x100C8000`, so the filesystem is never written.

## Steps

1. In the working directory, fetch the backup and image tools from tt-vga-capture, which gives seven Python files.

   ```console
   $ for f in fw_probe.py fw_dump.py fs_dump.py manifest.py unpack_backup.py uf2tool.py fs_start.py; do curl -fsSLO https://raw.githubusercontent.com/mithro/tt-vga-capture/e4427aaadae934539590bf9ae89c17ed451bdf71/docs/results/2026-11-24-mpy-module-fpga1/$f; done
   ```

2. On the Welland bench, read the fpgas.online bridge's health, which must show `"clients": 0` before you go on.

   ```console
   $ curl -s localhost:8765/health
   ```

3. On the Welland bench, stop the bridge, which frees `/dev/ttboard`.

   ```console
   $ sudo systemctl stop fpgas-tt
   ```

4. In the working directory, dump the board's firmware region and files, which writes four text files.

   ```console
   $ mpremote connect $DEV run fw_probe.py > probe_before.txt
   $ mpremote connect $DEV run manifest.py > manifest_before.txt
   $ mpremote connect $DEV run fw_dump.py > fw_dump.txt
   $ mpremote connect $DEV run fs_dump.py > fs_dump.txt
   ```

5. In the working directory, unpack the dumps into `backup/` and check them against the board's own hashes, which prints `MATCH` and `0 do not`.

   ```console
   $ python3 unpack_backup.py fw_dump.txt fs_dump.txt manifest_before.txt probe_before.txt backup
   ```

6. In the working directory, make the module image over the whole firmware region, which prints `wrote 3200 blocks`.

   ```console
   $ python3 uf2tool.py tobin $IMAGE module.bin 0x10000000 0x100C8000
   $ python3 uf2tool.py frombin module.bin module.uf2 0x10000000 0xe48bff59
   ```

7. In the working directory, make the restore image from the backup and decode it again, which gives two equal hashes.

   ```console
   $ python3 uf2tool.py frombin backup/firmware_region.bin restore.uf2 0x10000000 0xe48bff59
   $ python3 uf2tool.py tobin restore.uf2 restore.bin 0x10000000 0x100C8000
   $ sha256sum restore.bin backup/firmware_region.bin
   ```

8. In the working directory, check where the image's MicroPython mounts its filesystem, which must be `0x100C8000`.

   ```console
   $ python3 fs_start.py $IMAGE
   ```

   A release UF2 carries a filesystem image, and its list must include `0x100c8008`, the superblock of a
   filesystem at `0x100C8000`. A `firmware.uf2` you built carries none, so the list cannot show it. That build
   mounts at `0x100C8000` all the same. Its board definition at `596f456`,
   [`mpconfigboard.h`](https://github.com/psychogenic/tinytapeout_rp2350b/blob/596f456/mpconfigboard.h), gives
   4 MB of flash and leaves the first 800 KB to MicroPython. Stop here if neither holds.

9. In the working directory, reboot the board into its bootloader, which brings up the `RP2350` drive within 60 seconds.

   ```console
   $ mpremote connect $DEV bootloader
   $ timeout 60 sh -c "until [ -e /dev/disk/by-label/RP2350 ]; do sleep 1; done" || echo "no RP2350 drive after 60 s"
   ```

10. In the working directory, copy the module image to the drive, which disappears as the board starts the module firmware.

    ```console
    $ sudo mount /dev/disk/by-label/RP2350 /mnt/rp2350 && sudo cp module.uf2 /mnt/rp2350/ && sync; sudo umount /mnt/rp2350
    $ timeout 60 sh -c "until [ -e $DEV ]; do sleep 1; done" || echo "no $DEV after 60 s"; sleep 3
    ```

11. In the working directory, probe the module firmware, whose `fw_sha256` line must equal `module.bin`'s hash.

    ```console
    $ mpremote connect $DEV run fw_probe.py > probe_module.txt
    $ grep fw_sha256 probe_module.txt; sha256sum module.bin
    ```

12. In the working directory, compare the board's files with the backup, which prints `files identical`.

    ```console
    $ mpremote connect $DEV run manifest.py > manifest_module.txt
    $ diff <(grep '^F ' manifest_before.txt) <(grep '^F ' manifest_module.txt) && echo files identical
    ```

13. In the working directory, import the module, which prints `1`.

    ```console
    $ mpremote connect $DEV exec "import vgacap; print(vgacap.API)"
    ```

14. In the vgacap checkout, restart the SDK, which ends with `tt.sdk_version=` and a `>>>` prompt.

    ```console
    $ uv run --no-sync python tools/board_restart.py serial:$DEV
    ```

    The flash leaves the SDK's `main.py` unrun, and a capture then fails with `NameError: name 'tt' isn't defined`.

15. Capture with the module, as on [How to capture with the vgacap MicroPython module](how-to-capture.md), for as long as you need it.

16. In the working directory, bring the board to its bootloader for the restore, which skips the reboot when the `RP2350` drive is already there.

    ```console
    $ [ -e /dev/disk/by-label/RP2350 ] || mpremote connect $DEV bootloader
    $ timeout 60 sh -c "until [ -e /dev/disk/by-label/RP2350 ]; do sleep 1; done" || echo "no RP2350 drive after 60 s"
    ```

    To take the drive-only path on purpose, as the bench did, run `mpremote connect $DEV bootloader` first; this
    step then finds the drive and skips the reboot.

17. In the working directory, copy the restore image to the drive, whether or not the steps before succeeded.

    ```console
    $ sudo mount /dev/disk/by-label/RP2350 /mnt/rp2350 && sudo cp restore.uf2 /mnt/rp2350/ && sync; sudo umount /mnt/rp2350
    $ timeout 60 sh -c "until [ -e $DEV ]; do sleep 1; done" || echo "no $DEV after 60 s"; sleep 3
    ```

18. In the working directory, probe the board and list its files again, which writes `probe_after.txt` and `manifest_after.txt`.

    ```console
    $ mpremote connect $DEV run fw_probe.py > probe_after.txt
    $ mpremote connect $DEV run manifest.py > manifest_after.txt
    ```

19. On the Welland bench, start the bridge again.

    ```console
    $ sudo systemctl start fpgas-tt
    ```

20. On the Welland bench, restart the SDK through the bridge, which brings the idle display back from `left: the SDK is not running`.

    ```console
    $ uv run --no-sync python tools/board_restart.py ws://127.0.0.1:8765/serial
    ```

21. On the Welland bench, wait for the idle display, which reads `loaded` about 70 seconds later.

    ```console
    $ timeout 300 sh -c "until curl -s localhost:8765/health | grep -q '\"state\": \"loaded\"'; do sleep 5; done" || echo "not loaded after 300 s"
    ```

## Check

The board's firmware region is its own again, and so is every file:

```console
$ grep fw_sha256 probe_before.txt probe_after.txt
$ diff <(grep '^F ' manifest_before.txt) <(grep '^F ' manifest_after.txt) && echo files identical
$ mpremote connect $DEV exec "import vgacap"
```

The two `fw_sha256` lines show the same hash, `diff` prints `files identical`, and `import vgacap` fails with
`ImportError`, as on the stock firmware. On fpga-1 on 2026-11-27 the hashes were `b57a7ffc…` before and
after, with all 46 files identical ([dated result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-fpga1)).

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `"clients":` above 0 at step 2 | someone is using the board through the bridge | Wait until the count is 0 |
| `MISMATCH` or `1 do not` at step 5 | a dump was cut short | Repeat step 4; never go on with a backup that does not match |
| Two different hashes at step 7 | the restore image is wrong | Repeat step 7; write nothing until they match |
| Step 8 shows no `0x100c8008` for a release UF2 | that image mounts its filesystem elsewhere | Nothing is written yet: run step 19 and stop |
| `no RP2350 drive after 60 s` at step 9 | the board did not reach its bootloader | If `$DEV` is still there, nothing is written: run step 19 and stop; otherwise see the last row |
| `no $DEV after 60 s`, with your port for `$DEV`, at step 10 | the module firmware did not start | If `/dev/disk/by-label/RP2350` exists, the bootrom fell back to it: go to step 17; otherwise see the last row |
| `fw_sha256` at step 11 differs from `module.bin` | the copy was cut short | Go to step 16 and restore |
| Step 12 prints a difference | the module firmware mounted its filesystem elsewhere | Restore with steps 16 to 18, then the files as below |
| `ImportError` at step 13 | `IMAGE` has no module | Restore with steps 16 to 18, and start again with the right image |
| A capture fails for want of `ttboard` | `IMAGE` is CI's rp2 compile check, which has no SDK | Restore with steps 16 to 18, and use the module firmware |
| `no RP2350 drive after 60 s` at step 16 while `$DEV` is there | the module firmware is busy in a capture | Send the stop byte with `printf q > $DEV`, then repeat step 17 |
| `no $DEV after 60 s`, with your port for `$DEV`, at step 17 | the restore image did not start | If the `RP2350` drive is still there, repeat step 17; otherwise see the last row |
| The `fw_sha256` lines differ in Check | the restore copy was cut short | Repeat steps 16 to 18, then Check; if they still differ, stop and keep the backup |
| The files differ in Check | the filesystem changed while the module firmware ran | Restore the files as below; if they still differ, stop and keep the backup |
| `not loaded after 300 s` at step 21 | the SDK restart did not reach the board | Repeat steps 20 and 21 |
| Neither `$DEV` nor the `RP2350` drive appears | the board runs nothing that answers on USB | Stop; recovery needs someone at the Welland bench to hold BOOTSEL while resetting the board |

The fpgas-online broker's `power_cycle` switches the Pi's PoE port, so it restarts the Pi
([`power.py`](https://github.com/fpgas-online/fpgas.online-mcp/blob/fe16b5f/src/fpgas_online_mcp/power.py), [`power.md`](https://github.com/fpgas-online/fpgas.online-mcp/blob/fe16b5f/skills/fpgas-online/references/power.md)). A board that restarts with it boots the same flash again. So
`power_cycle` cannot bring up the `RP2350` drive.

To put the files back from `backup/`, remove each file that only the board has, and make each directory in
`dirs.txt`. Then copy each file, and run Check again. `mkdir` reports a directory that exists already, which does
no harm.

```console
$ comm -13 <(grep '^F ' manifest_before.txt | cut -d' ' -f2 | sort) <(grep '^F ' manifest_after.txt | cut -d' ' -f2 | sort) | while read f; do mpremote connect $DEV fs rm ":$f" < /dev/null; done
$ while read d; do mpremote connect $DEV fs mkdir ":$d" < /dev/null; done < backup/dirs.txt
$ (cd backup/fs && find . -type f | while read f; do mpremote connect $DEV fs cp "$f" ":${f#.}" < /dev/null; done)
```

## Next

- [How to capture with the vgacap MicroPython module](how-to-capture.md)
- [The vgacap module inside MicroPython](explanation-borrowing.md)
- [How to build the module firmware](how-to-build-release.md)
