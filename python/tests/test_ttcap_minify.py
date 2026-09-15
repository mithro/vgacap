# SPDX-License-Identifier: Apache-2.0
"""Tests for `ttcap.mp.minify()` and `module_level_names()`.

The board-side scripts are written for a reader and stripped on the way to
the board, because the RP2040 demo board leaves only ~80 KB of free heap and
compiling a 25 KB script needs most of it at once -- on tt07 the full
`capture_rp2.py` failed at compile time on some runs, twice fatally enough
to need a power cycle. What matters here is that the stripping is safe: same
program, same line numbers, small enough.
"""

from __future__ import annotations

import ast
import io
import tokenize

import pytest

from ttcap import mp
from ttcap.boards import RP2040_TT06, RP2350_DBV3
from ttcap.capture import capture_cfg

SCRIPTS = ["capture_rp2.py", "throughput.py"]

#: What the board must be able to compile. The unminified capture script was
#: 25,358 bytes and did not reliably fit.
MAX_SCRIPT_BYTES = 10_000


@pytest.fixture(scope="module")
def capture_source() -> str:
    return mp.load("capture_rp2.py")


def strip_docstrings(tree: ast.AST) -> ast.AST:
    """Drop the docstring from the module and from every def, in place."""
    holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders) or not node.body:
            continue
        if len(node.body) == 1 and not isinstance(node, ast.Module):
            continue  # minify() leaves these, so this must too
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = node.body[1:]
    return tree


def comment_tokens(source: str) -> list[str]:
    readline = io.StringIO(source).readline
    return [t.string for t in tokenize.generate_tokens(readline) if t.type == tokenize.COMMENT]


# -- the shipped scripts --------------------------------------------------


@pytest.mark.parametrize("name", SCRIPTS)
def test_minified_script_compiles(name):
    compile(mp.minify(mp.load(name)), name, "exec")


@pytest.mark.parametrize("name", SCRIPTS)
def test_minify_keeps_the_line_count(name):
    source = mp.load(name)
    assert mp.minify(source).count("\n") == source.count("\n")


@pytest.mark.parametrize("name", SCRIPTS)
def test_minified_script_has_no_comments_left(name):
    assert comment_tokens(mp.minify(mp.load(name))) == []


@pytest.mark.parametrize("name", SCRIPTS)
def test_minify_preserves_the_program(name):
    source = mp.load(name)

    expected = ast.dump(strip_docstrings(ast.parse(source)))
    assert ast.dump(ast.parse(mp.minify(source))) == expected


@pytest.mark.parametrize("profile", [RP2040_TT06, RP2350_DBV3], ids=lambda p: p.name)
def test_the_uploaded_capture_script_fits_in_the_board(profile):
    script = mp.with_cfg(mp.minify(mp.load("capture_rp2.py")), capture_cfg(profile))

    assert len(script) < MAX_SCRIPT_BYTES
    # ... and it is a real saving, not a file that was already small.
    assert len(script) < len(mp.load("capture_rp2.py")) / 2


def test_minify_keeps_line_numbers_pointing_at_the_source(capture_source):
    minified = mp.minify(capture_source)

    for line, text in enumerate(capture_source.split("\n")):
        if text.startswith("def main("):
            assert minified.split("\n")[line] == text
            break
    else:
        raise AssertionError("capture_rp2.py has no top-level main()")


# -- the stripping rules --------------------------------------------------


def test_minify_removes_whole_line_and_trailing_comments():
    source = "# a header\nx = 1  # why\ny = 2\n"

    assert mp.minify(source) == "\nx = 1\ny = 2\n"


def test_minify_keeps_a_hash_inside_a_string():
    source = 'x = "# not a comment"  # this one is\n'

    assert mp.minify(source) == 'x = "# not a comment"\n'


def test_minify_keeps_blank_lines_inside_a_multiline_string():
    # A blank line in a data string is content, not whitespace to squeeze.
    source = 'TEXT = """one\n\ntwo"""\nz = 1\n'

    assert mp.minify(source) == source


def test_minify_removes_module_and_function_docstrings():
    source = '"""Module."""\n\n\ndef f():\n    """Doc."""\n    return 1\n'

    assert mp.minify(source) == "\n\n\ndef f():\n\n    return 1\n"


def test_minify_keeps_a_docstring_that_is_the_whole_body():
    # Removing it would leave an empty block, which will not compile.
    source = 'def f():\n    """Only this."""\n'

    minified = mp.minify(source)
    assert minified == source
    compile(minified, "<test>", "exec")


# -- module_level_names ---------------------------------------------------


def test_module_level_names_covers_what_a_run_leaves_behind(capture_source):
    names = mp.module_level_names(capture_source)

    # Constants, derived constants, the IRQ state, the functions, and the
    # imports -- every name the script binds in the board's REPL globals.
    for expected in ("CLK_GPIO", "SAMPLES_PER_WORD", "FULL", "on_a", "main", "machine"):
        assert expected in names
    assert names == sorted(set(names))
    # Nothing local to a function leaks in.
    assert "sampler" not in names and "bufs" not in names


def test_module_level_names_includes_the_prepended_cfg():
    script = mp.with_cfg(mp.minify(mp.load("capture_rp2.py")), capture_cfg(RP2040_TT06))

    assert "CFG" in mp.module_level_names(script)
