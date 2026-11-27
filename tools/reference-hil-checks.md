---
type: reference
owner: vgacap maintainers
reader: someone running or reading a hardware-in-the-loop check
review: 2027-05-27
---

# Hardware-in-the-loop checks

This page lists the checks of [`tools/hil.py`](hil.py), its options, its exit status and its report. It is for someone running the check or reading a report. Why each check needs a board is in the docstring at the top of `hil.py`. The steps are on [How to run the hardware-in-the-loop check through a tunnel](how-to-run-hil-through-a-tunnel.md).

The tables are made from `hil.py` when this site is built. [`tools/hil.sh`](hil.sh) runs `hil.py` in the checkout's uv environment and takes the same options.

## Checks

The checks run in this order. Needs GStreamer is `yes` for a check that runs a `ttcap demo` pipeline, which needs `gst-launch-1.0` and the vgacap plugin. Needs a reference render is `yes` for a check that needs a design named `tt_um_vgacal_*` and a tt-vga-testpatterns checkout. A check that cannot run prints `SKIP` with its reason.

```{vgacap-hil-checks}
```

## Options

Option is the name on the command line, Takes the value it takes, and Default its value when not given.

```{vgacap-hil-options}
```

## Exit status

| Status | Meaning |
|---|---|
| `0` | no check failed; skips do not count |
| `1` | a check failed or the run was cut short with a `board` FAIL row |
| `2` | the run could not start: a held lock or a missing tool or a bad argument |

## Report

A report path that is a directory, or that has no extension, gets a file named `<date>-hil-<board>.md` inside it. The report holds the run's settings and verdict, a table of checks, any notes and each check's commands and output.

## Safety on a shared board

| Guard | What it does |
|---|---|
| lock file | one run for each board at a time, for one user on one machine |
| `--lock-dir` | a lock directory that several users share |
| timeouts | every board command has one, so a silent board fails instead of hanging |
| sessions | every board command runs in its own session, which is killed on the way out |
| signals | SIGINT and SIGTERM and SIGHUP and SIGQUIT end the run with its cleanup and its report |
| `--stop-bridge` | refused unless the bridge's `/health` shows 0 clients on two reads 3 s apart |
| `--stop-bridge` refusal | exit status 1 with a `board` FAIL row and every check SKIP |
| bridge restart | the board is restarted before the bridge starts again, and the bridge always starts again |
| client count | counts the bridge's WebSocket sessions only, so an HTTP request in flight is not seen |
