# SPDX-License-Identifier: Apache-2.0
"""Tests for docs/_ext/vgacap_developing.py: each directive builds a table
from the real source file in this repository, and the rules table names
every rule docs/_ext/vgacap_rules.py checks.

    uv run --locked --python 3.13 --only-group docs pytest -q docs/tests
"""

import ast
import io
import pathlib
import re
import sys
import textwrap

import pytest
from sphinx.application import Sphinx
from sphinx.util.console import nocolor
from sphinx.util.docutils import docutils_namespace

nocolor()

EXT = pathlib.Path(__file__).resolve().parent.parent / "_ext"
sys.path.insert(0, str(EXT))

import vgacap_developing  # noqa: E402
import vgacap_landing  # noqa: E402
import vgacap_rules  # noqa: E402

DIRECTIVES = ["vgacap-ci-jobs", "vgacap-ci-env", "vgacap-hil-options", "vgacap-hil-checks",
              "vgacap-cmake-options", "vgacap-page-rules", "vgacap-sidebar-checks",
              "vgacap-page-limits",
              "vgacap-banned-words"]


def render(tmp_path, directive):
    """The HTML body of a one-page project holding `directive`, and the warnings."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.md").write_text(f"# Page\n\n```{{{directive}}}\n```\n")
    (src / "conf.py").write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(EXT)!r})
        extensions = ["myst_parser", "vgacap_developing"]
        source_suffix = {{".md": "markdown"}}
    """))
    warnings = io.StringIO()
    with docutils_namespace():
        app = Sphinx(str(src), str(src), str(tmp_path / "out"), str(tmp_path / "doctrees"),
                     "html", status=io.StringIO(), warning=warnings, freshenv=True)
        app.build()
    return (tmp_path / "out" / "index.html").read_text(), warnings.getvalue()


@pytest.mark.parametrize("directive", DIRECTIVES)
def test_directive_builds_cleanly(tmp_path, directive):
    html, warnings = render(tmp_path, directive)
    assert warnings == ""
    assert "<table" in html or "<p>" in html


def test_ci_jobs_lists_every_job(tmp_path):
    html, _ = render(tmp_path, "vgacap-ci-jobs")
    for job in vgacap_developing.load_ci()["jobs"]:
        assert f">{job}<" in html


def test_hil_options_lists_every_option_without_a_local_path(tmp_path):
    html, _ = render(tmp_path, "vgacap-hil-options")
    parser = vgacap_developing.load_hil().build_parser()
    for action in parser._actions:
        if action.option_strings and action.option_strings[0] != "-h":
            assert f">{action.option_strings[0]}<" in html
    assert str(vgacap_developing.REPO) not in html


def test_hil_checks_has_a_row_for_every_check(tmp_path):
    html, _ = render(tmp_path, "vgacap-hil-checks")
    for name in vgacap_developing.load_hil().ALL_CHECKS:
        assert f">{name}<" in html


def test_cmake_options_finds_the_options(tmp_path):
    html, _ = render(tmp_path, "vgacap-cmake-options")
    assert ">VGACAP_HOST_EXTRAS<" in html and ">VGACAP_JCC_PAD<" in html


def template(node):
    """A message as written in the code, with … for each part that varies."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "…" for v in node.values)
    if isinstance(node, ast.BinOp):
        return template(node.left) + template(node.right)
    return "…"


def messages(module):
    """(kind, rule, message) for every message the module prints: page.fail
    and page.note with their rule, report() and logger.warning, and the
    problems resolve() returns for a landing line."""
    out = []
    for node in ast.walk(ast.parse(pathlib.Path(module.__file__).read_text())):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name in ("fail", "note"):
                out.append((name, node.args[0].value, template(node.args[1])))
            elif name == "report":
                out.append(("report", None, template(node.args[2])))
            elif name == "warning":
                out.append(("warning", None, template(node.args[0])))
        elif (isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple)
              and len(node.value.elts) == 2 and isinstance(node.value.elts[1], ast.JoinedStr)):
            out.append(("problem", None, template(node.value.elts[1])))
    return out


def matches(row, message):
    """A row's message is the code's, or a start of it ending in …."""
    return row == message or (row.endswith("…") and message.startswith(row[:-1]))


def test_rules_table_has_one_row_for_each_check():
    found = messages(vgacap_rules)
    # report() and the two generic warnings only pass a message through.
    code = [(kind, rule, m) for kind, rule, m in found if m not in ("rule …: …", "…")]
    rows = list(vgacap_rules.CHECKS)
    for kind, rule, message in code:
        hits = [r for r in rows if matches(r[1], message)
                and (rule is None or r[0] == rule)]
        assert len(hits) == 1, f"{kind} {rule} {message!r}: {len(hits)} rows"
        rows.remove(hits[0])
        if kind == "note":
            assert hits[0][3] == "logged"
    assert rows == [], f"rows for no check: {rows}"


def test_sidebar_table_has_one_row_for_each_check():
    found = messages(vgacap_landing)
    rows = [message for message, _what in vgacap_landing.CHECKS]
    expected = []
    for kind, _rule, message in found:
        if kind == "problem":
            expected.append("landing line …: " + message)
        elif message != "landing line …: …":   # passes resolve()'s problems through
            expected.append(message)
    for message in expected:
        hits = [r for r in rows if matches(r, message)]
        assert len(hits) == 1, f"{message!r}: {len(hits)} rows"
        rows.remove(hits[0])
    assert rows == [], f"rows for no check: {rows}"


@pytest.mark.parametrize("on, words", [
    ("push", ["push", "for every branch"]),
    (["push", "pull_request"], ["push", "pull_request", "for every branch"]),
    ({"push": None, "pull_request": None}, ["push", "pull_request", "for every branch"]),
    ({"push": {"branches": ["main"]}}, ["push", "filters are in the file"]),
])
def test_triggers(on, words):
    text = vgacap_developing.triggers({True: on}).astext()
    for word in words:
        assert word in text
    if isinstance(on, dict) and any(on.values()):
        assert "every branch" not in text
