# SPDX-License-Identifier: Apache-2.0
"""MicroPython scripts that ttcap ships to the board, plus loaders for them.

The `.py` files in this package are *board-side* sources: they are written in
the MicroPython subset (no f-strings, no walrus, no `match`, no `typing`, no
dataclasses) and are never imported on the host -- they are read as text with
`load()`, given their parameters by `with_cfg()`, and handed to
`ttcap.repl.RawRepl.exec()`, `.exec_stream()` (text) or `.exec_chunks()`
(binary vgacap chunks).

Each script expects a module-level `CFG` dict that the host prepends; the
per-script docstring documents the keys it reads.
"""

from __future__ import annotations

from importlib.resources import files


def load(name: str) -> str:
    """Return the source text of the MicroPython script `name`.

    `name` is a bare file name inside this package, e.g. `"throughput.py"`.
    """
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"script name must be a bare file name, got {name!r}")
    return files(__package__).joinpath(name).read_text(encoding="utf-8")


def with_cfg(source: str, cfg: dict) -> str:
    """Prepend a `CFG = {...}` literal to a board-side script source.

    `repr()` of a dict of ints/strings is valid MicroPython, and putting it
    first keeps the script's own line numbers close to the originals in any
    traceback the board reports.
    """
    return "CFG = " + repr(cfg) + "\n" + source
