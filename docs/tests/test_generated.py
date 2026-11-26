# SPDX-License-Identifier: Apache-2.0
"""Tests for docs/_ext/vgacap_generated: the reference tables built from
their source at build time.

    uv run --locked --python 3.13 --only-group docs --with pytest pytest -q docs/tests
"""

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
    warnings, html = build(tmp_path, "```{vgacap-modes}\n```\n")
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
