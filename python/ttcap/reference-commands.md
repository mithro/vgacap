---
type: reference
owner: vgacap maintainers
reader: someone looking up a ttcap subcommand or option
review: 2027-05-27
---

# ttcap commands and options

**Every `ttcap` subcommand, with each of its arguments and options.**

This list is built from the argument parser in [`cli.py`](cli.py) and [`demo.py`](demo.py) each time the site is built, so it matches the code. Exit codes and messages are on [ttcap exit codes and messages](reference-exit-codes.md). For each subcommand, the usage line comes first, then each argument. An argument has its help text, then its choices, its default, and the options it excludes.

A link is `serial:` followed by a serial device, or a `ws://` or `wss://` address of an fpgas.online bridge (`fpgas-tt`).

```{ttcap-commands}
```
