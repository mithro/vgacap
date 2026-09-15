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

The sources are written for a reader, and then `minify()` strips the prose
out on the way to the board. That is not a style preference: the RP2040
demo board leaves about 80 KB of free heap, and compiling a 25 KB script
needs most of it at once, so the full `capture_rp2.py` failed to compile on
some runs of tt07 -- once as a reported `MemoryError`, twice as a `FATAL:
uncaught exception` that needed a power cycle. `module_level_names()` exists
for the other half of that problem: what one run leaves behind in the
board's REPL namespace for the next one to trip over.
"""

from __future__ import annotations

import ast
import io
import tokenize
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


def _docstring_lines(tree: ast.Module) -> set[int]:
    """Line numbers occupied by docstrings, module and every def included.

    A docstring that is the *whole* body of a def is left in place: removing
    it would leave an empty block, and no script here has one.
    """
    spans: set[int] = set()
    holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders) or not node.body:
            continue
        first = node.body[0]
        if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
            continue
        if not isinstance(first.value.value, str):
            continue
        if len(node.body) == 1 and not isinstance(node, ast.Module):
            continue
        spans.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return spans


def minify(source: str) -> str:
    """Strip comments, docstrings and blank lines, keeping the line numbers.

    Every removed line becomes an empty line rather than disappearing, so a
    traceback from the board still names the line the reader would look at
    in `capture_rp2.py`. Trailing comments are cut off their line, leaving
    the code.

    Comments are found by tokenizing, not by searching for `#`, so a `#`
    inside a string literal survives; lines inside a multi-line string
    literal are passed through untouched, so a blank line that is part of
    the data is not swallowed.
    """
    lines = source.split("\n")
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))

    protected: set[int] = set()
    for token in tokens:
        if token.type == tokenize.STRING and token.end[0] > token.start[0]:
            protected.update(range(token.start[0], token.end[0] + 1))
    for token in tokens:
        if token.type == tokenize.COMMENT:
            row, column = token.start
            lines[row - 1] = lines[row - 1][:column]

    drop = _docstring_lines(ast.parse(source))
    out = []
    for number, text in enumerate(lines, 1):
        if number in drop:
            out.append("")
        elif number in protected:
            out.append(text)
        elif not text.strip():
            out.append("")
        else:
            out.append(text.rstrip())
    return "\n".join(out)


def module_level_names(source: str) -> list[str]:
    """Every name `source` binds at module level, sorted.

    The board runs these scripts straight into the REPL's own globals, so
    each run leaves its names there -- about 64 of them, ~5 KB, for
    `capture_rp2.py`. On a board with ~80 KB of heap that is the difference
    between the next run compiling and not, so the host deletes them before
    it sends the next script.

    Import names are included: the module objects stay in `sys.modules`, so
    re-importing costs nothing. That does mean the deleting snippet cannot
    rely on any import of its own surviving the loop -- see
    `ttcap.capture._CLEANUP`, which imports `gc` afterwards for exactly that
    reason.
    """
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return sorted(names)
