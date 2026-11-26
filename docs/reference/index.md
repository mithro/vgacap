---
type: landing
owner: vgacap maintainers
reader: anyone looking up a format, a protocol or a value
review: 2027-05-27
---

# Reference

- [The vgacap firmware protocol](../../firmware/PROTOCOL.md): the commands, replies and capture output of the vgacap firmware.
- [`frames.txt` and `frame_hashes.<method>.txt`](../frames-format.md): the frame list and frame hashes a sampler writes.
- [The simulation trace format](../trace-format.md): the trace a simulated design writes.
- [Vendored third-party code](../../third_party/README.md): the libraries built into vgacap, with their versions and licenses.
- [Images and logos in the overview figure](../diagrams/NOTICE.md): where each picture comes from, and its license.
- [ttcap commands and options](../../python/ttcap/reference-commands.md): every `ttcap` subcommand and option.
- [ttcap exit codes and messages](../../python/ttcap/reference-exit-codes.md): what each exit code and error message means.
- [Demo board GPIO maps for capture](../../python/ttcap/reference-gpio-maps.md): the pins the RP2040 and RP2350 demo boards sample.
- [Welland bench boards in ttcap](../../python/ttcap/reference-welland-boards.md): the bench boards `ttcap demo --board` names.

```{toctree}
:hidden:

../../firmware/PROTOCOL
../frames-format
../trace-format
../../third_party/README
../diagrams/NOTICE
```
