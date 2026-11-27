---
type: how-to
owner: vgacap maintainers
reader: someone with a Raspberry Pi 5 who wants to run vgacap-rp1
review: 2027-05-27
---

# How to build vgacap-rp1

**You want the `vgacap-rp1` capture tool built from a vgacap checkout.**

The build compiles [piolib](https://github.com/raspberrypi/utils/tree/master/piolib) into the tool, so no `libpio-dev` package is needed. It builds on any Linux machine, but the tool runs only on a Raspberry Pi 5. Running it is on [How to check vgacap-rp1 with the loopback fixture](how-to-capture-loopback.md).

## What you need

- A vgacap checkout, made with `git clone https://github.com/mithro/vgacap`, and a shell in its `vgacap` directory.
- `git`, `make` and a C compiler (`cc`).
- Network access to GitHub, for [raspberrypi/utils](https://github.com/raspberrypi/utils).

## Steps

1. In the checkout, run `git init -q utils` to make an empty repository for piolib.
2. In the checkout, run `git -C utils remote add origin https://github.com/raspberrypi/utils.git` to point it at raspberrypi/utils.
3. In the checkout, run `git -C utils fetch -q --depth 1 origin 05e8808e84ed99b168b14a4025dddb9118fa1c17` to fetch the commit the tool was proven with.
4. In the checkout, run `git -C utils checkout -q FETCH_HEAD` to check that commit out.
5. In the checkout, run `make -C capture-rp1 PIOLIB=$PWD/utils/piolib` to build `capture-rp1/vgacap-rp1` and `capture-rp1/vgacap-rp1-programs`.

## Check

`./capture-rp1/vgacap-rp1` with no arguments prints its usage and exits with status 2:

```text
usage: vgacap-rp1 --out F --samples N [--data-base 4] [--clk 12] [--edge rising|falling] [--clock-hz HZ [--unproven-rate]]
                  [--desc TEXT] [--gen FILE | --gen-counter] [--gen-div D] [--gen-delay K<=15]
```

`./capture-rp1/vgacap-rp1-programs 12 rising 3` prints the PIO programs, with the sampler as `200c 208c 4008`.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 5 | `hardware/pio.h: No such file or directory` | `PIOLIB` does not name the `piolib` directory | Give the absolute path, as in step 5 |
| 3 | `fatal: couldn't find remote ref` | The commit was mistyped | Copy the commit from step 3 again |

## Next

- [How to check vgacap-rp1 with the loopback fixture](how-to-capture-loopback.md)
- [vgacap-rp1 options](reference-options.md)
- [Capture on the Raspberry Pi 5's RP1](explanation-capture.md)
