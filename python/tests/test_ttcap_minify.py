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

SCRIPTS = ["prelude.py", "capture_rp2.py", "throughput.py"]

#: What the board must be able to compile. The unminified capture script was
#: 25,358 bytes and did not reliably fit.
MAX_SCRIPT_BYTES = 10_000


@pytest.fixture(scope="module")
def capture_source() -> str:
    return mp.source("capture_rp2.py")


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
    script = mp.build(capture_cfg(profile), "capture_rp2.py")

    assert len(script) < MAX_SCRIPT_BYTES
    # ... and it is a real saving, not a file that was already small.
    assert len(script) < len(mp.source("capture_rp2.py")) / 2


def test_every_def_in_the_built_script_locates_back_to_its_file():
    # A board traceback numbers lines in the one concatenated script; each
    # top-level def must map back to the file and line a reader would open.
    built = mp.build(capture_cfg(RP2040_TT06), "capture_rp2.py").split("\n")
    located = []
    for line, text in enumerate(built, 1):
        if text.startswith("def "):
            name, number = mp.locate(line, "capture_rp2.py")
            assert mp.load(name).split("\n")[number - 1] == text
            located.append(name)
    expected = [
        name
        for name in ("prelude.py", "capture_rp2.py")
        for text in mp.load(name).split("\n")
        if text.startswith("def ")
    ]
    assert located == expected


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
    script = mp.build(capture_cfg(RP2040_TT06), "capture_rp2.py")

    assert "CFG" in mp.module_level_names(script)


# -- build / source / locate / annotate -----------------------------------


def test_build_is_cfg_then_the_prelude_then_the_body():
    script = mp.build(capture_cfg(RP2040_TT06), "capture_rp2.py")
    lines = script.split("\n")

    assert lines[0].startswith("CFG = {")
    # Exactly the two minified parts after the CFG line, in order.
    assert "\n".join(lines[1:]) == mp.minify(mp.load("prelude.py")) + mp.minify(
        mp.load("capture_rp2.py")
    )
    compile(script, "<stdin>", "exec")


def test_build_strips_the_body_docstring_too():
    # minify() only knows a *leading* docstring; concatenated first, the
    # body's would survive as a statement and cost the board heap.
    script = mp.build(capture_cfg(RP2040_TT06), "capture_rp2.py")

    assert "Continuous capture" not in script
    assert "Board-side code shared" not in script


def test_source_has_the_same_layout_as_build_without_the_cfg_line():
    built = mp.build(capture_cfg(RP2040_TT06), "capture_rp2.py")

    assert mp.source("capture_rp2.py").count("\n") == built.count("\n") - 1


def test_build_wants_a_body_and_never_a_second_prelude():
    with pytest.raises(ValueError, match="body"):
        mp.build({})
    with pytest.raises(ValueError, match="always included"):
        mp.build({}, "prelude.py")


def test_locate_maps_each_part():
    prelude_lines = mp.load("prelude.py").count("\n")

    assert mp.locate(1, "capture_rp2.py") == ("CFG", 1)
    assert mp.locate(2, "capture_rp2.py") == ("prelude.py", 1)
    assert mp.locate(1 + prelude_lines, "capture_rp2.py") == ("prelude.py", prelude_lines)
    assert mp.locate(2 + prelude_lines, "capture_rp2.py") == ("capture_rp2.py", 1)
    with pytest.raises(ValueError):
        mp.locate(10**6, "capture_rp2.py")
    with pytest.raises(ValueError):
        mp.locate(0, "capture_rp2.py")


def test_annotate_names_the_file_and_line_of_a_board_traceback():
    script = mp.build(capture_cfg(RP2040_TT06), "capture_rp2.py").split("\n")
    line = next(n for n, text in enumerate(script, 1) if text.startswith("def main("))
    own = mp.load("capture_rp2.py").split("\n")
    expected = next(n for n, text in enumerate(own, 1) if text.startswith("def main("))
    stderr = (
        "Traceback (most recent call last):\r\n"
        f'  File "<stdin>", line {line}, in main\r\n'
        '  File "<stdin>", line 999999, in <module>\r\n'
        "OSError: [Errno 12] ENOMEM\r\n"
    )

    out = mp.annotate(stderr, "capture_rp2.py")

    assert f'File "<stdin>", line {line} [capture_rp2.py:{expected}], in main' in out
    # A line that does not map is left exactly as the board said it.
    assert 'File "<stdin>", line 999999, in <module>' in out
    assert out.endswith("OSError: [Errno 12] ENOMEM\r\n")
