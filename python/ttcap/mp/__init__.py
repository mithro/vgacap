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

Capture programs come in two parts: `prelude.py`, shared by every one, and
a program body such as `capture_rp2.py`. `build(cfg, body)` is what goes to
the board, `source(body)` the readable whole the tests work on, and
`locate()`/`annotate()` turn a board traceback's line back into a file and
line. `throughput.py` is standalone and still goes through `with_cfg()`.

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
import re
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


#: The board-side code every capture program shares; see its docstring.
PRELUDE = "prelude.py"


def _parts(names: tuple[str, ...]) -> tuple[str, ...]:
    if not names:
        raise ValueError("name at least one program body, e.g. 'capture_rp2.py'")
    if PRELUDE in names:
        raise ValueError(f"{PRELUDE} is always included; name only the body")
    return (PRELUDE, *names)


def _join(texts) -> str:
    """Concatenate sources so each starts on a fresh line of its own.

    Every part ends in exactly one newline, so part i's line n is line
    `offset_i + n` of the result -- the arithmetic `locate()` undoes.
    """
    return "".join(text if text.endswith("\n") else text + "\n" for text in texts)


def source(*names: str) -> str:
    """The prelude plus the named program bodies, unminified, without CFG.

    Concatenation, not import: the board has no package path for these
    files, and one script is one compile. This is the readable form the
    host tests lift functions out of; `build()` is what goes to the board.
    """
    return _join(load(name) for name in _parts(names))


def _layout(names: tuple[str, ...]) -> list[tuple[str, int, str]]:
    """The built script's lines after CFG: (file, line in file, text).

    Each part is minified on its own, because `minify()` recognises only a
    *leading* docstring as one -- a body's docstring in the middle of a
    concatenation is just an expression statement. Then the blank lines
    `minify()` kept to preserve line numbers are dropped (~450 of them, 4%
    of the frame script), except inside a multi-line string, where a blank
    line is data. This list is the map back to the sources.
    """
    rows = []
    for name in _parts(names):
        text = minify(load(name))
        keep: set[int] = set()
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.STRING and token.end[0] > token.start[0]:
                keep.update(range(token.start[0], token.end[0] + 1))
        for number, line in enumerate(text.split("\n"), 1):
            if line.strip() or number in keep:
                rows.append((name, number, line))
    return rows


def build(cfg: dict, *names: str) -> str:
    """The script to send to the board: CFG, then the minified parts,
    without the blank lines (see `_layout`; `locate()` maps lines back)."""
    return with_cfg("\n".join(text for _, _, text in _layout(names)) + "\n", cfg)


def locate(line: int, *names: str) -> tuple[str, int]:
    """Map line `line` of `build(cfg, *names)` to (file, line in that file).

    Line 1 is `("CFG", 1)`; every other line is looked up in the layout
    `build()` produced, so blank lines dropped from the script cost nothing
    here.
    """
    if line < 1:
        raise ValueError(f"line numbers start at 1, got {line}")
    if line == 1:
        return ("CFG", 1)
    rows = _layout(names)
    if line - 2 >= len(rows):
        raise ValueError(f"line {line} is past the end of the built script")
    name, number, _ = rows[line - 2]
    return (name, number)


_TRACEBACK_LINE = re.compile(r'(File "<stdin>", line (\d+))')


def annotate(stderr: str, *names: str) -> str:
    """Add the file and line to each `File "<stdin>", line N` of a traceback.

    The board sees one concatenated script called `<stdin>`, so its
    tracebacks number lines in that; a reader wants `capture_rp2.py:123`.
    The original text is kept and the location appended, so nothing the
    board said is lost. Lines that do not map (past the end) are left as
    they are.
    """

    def repl(match: re.Match) -> str:
        try:
            name, number = locate(int(match.group(2)), *names)
        except ValueError:
            return match.group(1)
        return "%s [%s:%d]" % (match.group(1), name, number)

    return _TRACEBACK_LINE.sub(repl, stderr)


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

    Indentation is rewritten to one space per block level, and a line that
    continues a bracketed expression loses its indentation entirely (it has
    no meaning there). Four spaces a level was ~30% of the minified frame
    script, the difference between fitting the 10 KB compile budget and
    not.
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

    # Lines whose first token sits inside brackets, or that follow a
    # backslash continuation: their indentation is not block structure.
    continued: set[int] = set()
    depth = 0
    seen: set[int] = set()
    for token in tokens:
        row = token.start[0]
        if row not in seen and token.type not in (tokenize.NL, tokenize.NEWLINE,
                                                   tokenize.INDENT, tokenize.DEDENT,
                                                   tokenize.COMMENT):
            seen.add(row)
            if depth > 0:
                continued.add(row)
        if token.type == tokenize.OP and token.string in "([{":
            depth += 1
        elif token.type == tokenize.OP and token.string in ")]}":
            depth -= 1
    for number, text in enumerate(lines, 1):
        if text.rstrip().endswith("\\") and number not in protected:
            continued.add(number + 1)

    drop = _docstring_lines(ast.parse(source))
    out = []
    levels = [0]
    for number, text in enumerate(lines, 1):
        if number in drop:
            out.append("")
        elif number in protected:
            out.append(text)
        elif not text.strip():
            out.append("")
        elif number in continued:
            out.append(text.strip())
        else:
            width = len(text) - len(text.lstrip(" "))
            if width > levels[-1]:
                levels.append(width)
            else:
                while width < levels[-1]:
                    levels.pop()
            out.append(" " * (len(levels) - 1) + text.strip())
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
