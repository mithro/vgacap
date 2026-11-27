---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who has changed the raw framer's Migen source
review: 2027-05-27
---

# How to regenerate the raw framer's Verilog and run its tests

**You changed [`vgacap_raw_framer.py`](vgacap_raw_framer.py), and want the committed Verilog to match it and the tests to pass.**

The Verilog is never edited by hand: a test fails when it differs from what the source generates. What the tests cover is listed in [`test_rtl_raw_framer.py`](../python/tests/test_rtl_raw_framer.py).

## What you need

- A vgacap checkout, and a shell in its top directory.
- [uv](https://docs.astral.sh/uv/), which installs Migen 0.9.2 from the project's `dev` dependency group.
- [Icarus Verilog](https://github.com/steveicarus/iverilog) (`iverilog` and `vvp`), for the tests on the generated Verilog; without it they are skipped.

## Steps

1. In the checkout, run `uv run python rtl/vgacap_raw_framer.py > rtl/vgacap_raw_framer.v` to regenerate the Verilog.
2. In the checkout, run `uv run pytest -q python/tests/test_rtl_raw_framer.py` to simulate the source in Migen and the Verilog in Icarus.
3. Commit `rtl/vgacap_raw_framer.py` and `rtl/vgacap_raw_framer.v` together.

## Check

Step 2 ends with every test passed and none skipped:

```text
17 passed
```

The Icarus tests run [`tb_vgacap_raw_framer.v`](tb_vgacap_raw_framer.v), which stops with `$fatal` if a byte offered and not taken changes. CI runs them in its `gstreamer` job, which installs Icarus.

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `test_the_committed_verilog_is_what_the_source_generates` fails | The Verilog is stale or was edited by hand | Repeat step 1 |
| `4 skipped` at step 2 | Icarus Verilog is not installed | Install `iverilog` and repeat step 2 |
| `hold violation` in a failing Icarus test | A change lets an offered byte change before it is taken | Fix the source so `out_data` holds until `out_ready` |

## Next

- [The raw framer](explanation-raw-framer.md)
- [The raw framer's ports, parameters and output](reference-raw-framer.md)
