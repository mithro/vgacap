# SPDX-License-Identifier: Apache-2.0
"""Tests for docs/_ext/vgacap_generated: the reference tables built from
their source at build time.

    uv run --locked --python 3.13 --only-group docs --with pytest pytest -q docs/tests
"""

import argparse
import html as htmllib
import io
import pathlib
import sys
import textwrap

import pytest
from sphinx.application import Sphinx
from sphinx.util.console import nocolor
from sphinx.util.docutils import docutils_namespace

nocolor()

EXT = pathlib.Path(__file__).resolve().parent.parent / "_ext"
sys.path.insert(0, str(EXT))
import vgacap_generated  # noqa: E402


def build(tmp_path, body):
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.md").write_text("# Root\n\n" + body)
    (src / "conf.py").write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(EXT)!r})
        extensions = ["myst_parser", "vgacap_generated"]
        source_suffix = {{".md": "markdown"}}
    """))
    warnings = io.StringIO()
    with docutils_namespace():
        app = Sphinx(str(src), str(src), str(tmp_path / "out"), str(tmp_path / "doctrees"),
                     "html", status=io.StringIO(), warning=warnings, freshenv=True)
        app.build()
    return warnings.getvalue(), htmllib.unescape((tmp_path / "out" / "index.html").read_text())


def test_the_mode_table_is_read_from_modes_c():
    modes = vgacap_generated.read_modes()
    names = [m["name"] for m in modes]
    assert names[0] == "640x480@60" and "1280x1024@60" in names
    first = modes[0]
    assert (first["h_active"], first["h_front"], first["h_sync"], first["h_back"]) == (640, 16, 96, 48)
    assert len(names) == len(set(names))


def test_an_unreadable_entry_is_an_error(tmp_path):
    bad = tmp_path / "modes.c"
    bad.write_text('static const vgaframe_mode_t table[] = {\n    { "x", 1, 2 },\n};\n')
    with pytest.raises(vgacap_generated.SourceError):
        vgacap_generated.read_modes(bad)


def test_the_modes_directive_renders_every_mode(tmp_path):
    warnings, html = build(tmp_path, "```{vgacap-modes} horizontal\n```\n\n"
                                     "```{vgacap-modes} vertical\n```\n")
    assert warnings == ""
    for m in vgacap_generated.read_modes():
        assert m["name"] in html


def test_the_argparse_directive_renders_every_option(tmp_path):
    warnings, html = build(tmp_path, "```{vgacap-argparse} vgacap.bin2stream:parser\n```\n")
    assert warnings == ""
    for option in ("--desc", "--clock-hz", "--mode", "input", "output"):
        assert option in html


def test_a_missing_parser_is_an_error(tmp_path):
    warnings, _ = build(tmp_path, "```{vgacap-argparse} vgacap.bin2stream:nothing\n```\n")
    assert "cannot build the parser" in warnings


def test_a_polarity_other_than_0_or_1_is_an_error():
    mode = {"name": "x", "h_sync_positive": 2}
    with pytest.raises(vgacap_generated.SourceError):
        vgacap_generated.polarity(mode, "h_sync_positive")


def test_the_modes_directive_wants_an_axis(tmp_path):
    warnings, _ = build(tmp_path, "```{vgacap-modes} sideways\n```\n")
    assert "write horizontal or vertical" in warnings


def text_rows(ap):
    def text(cell):
        return cell.astext() if hasattr(cell, "astext") else str(cell)
    return [[text(c) for c in row] for row in vgacap_generated.argparse_rows(ap)]


def test_an_optional_positional_is_bracketed_and_not_required():
    ap = argparse.ArgumentParser()
    ap.add_argument("where", nargs="?", default="here", help="where")
    assert text_rows(ap) == [["where", "", "'here'", "", "where"]]


def test_a_suppressed_argument_is_left_out():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hidden", help=argparse.SUPPRESS)
    ap.add_argument("--shown", help="shown")
    assert [r[0] for r in text_rows(ap)] == ["--shown"]


def test_a_suppressed_default_is_blank():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", default=argparse.SUPPRESS, help="x")
    assert text_rows(ap)[0][2] == ""


def test_help_placeholders_are_filled_in():
    ap = argparse.ArgumentParser(prog="tool")
    ap.add_argument("--n", type=int, default=4, help="%(prog)s uses %(default)s")
    assert text_rows(ap)[0][4] == "tool uses 4"


def test_a_required_option_says_so():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="out")
    assert text_rows(ap)[0][2] == "required"


def test_a_tuple_metavar_names_each_value():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", nargs=2, metavar=("W", "H"), help="size")
    assert text_rows(ap)[0][1] == "W H"


def test_each_choice_is_a_row():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("a", "b"), default="a", help="mode")
    assert text_rows(ap) == [["--mode", "MODE", "'a'", "", "mode"], ["", "", "", "a", ""], ["", "", "", "b", ""]]


def test_subcommands_follow_with_their_names():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v", action="store_true", help="v")
    sub = ap.add_subparsers(dest="cmd")
    go = sub.add_parser("go", help="go somewhere")
    go.add_argument("--fast", action="store_true", help="fast")
    rows = text_rows(ap)
    assert [r[0] for r in rows] == ["--v", "go", "go --fast"]
    assert rows[1][4] == "go somewhere"


def test_a_suppress_equal_to_but_not_the_constant_is_left_out():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hidden", help="".join(["==", "SUPPRESS=="]))
    assert text_rows(ap) == []


def test_a_long_range_of_choices_is_one_row():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, choices=range(1, 23), help="level")
    assert text_rows(ap)[1][3] == "1 to 22"
    assert len(text_rows(ap)) == 2


def test_a_stray_percent_in_help_is_shown_as_written():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duty", help="duty cycle in % of a line")
    assert text_rows(ap)[0][4] == "duty cycle in % of a line"
