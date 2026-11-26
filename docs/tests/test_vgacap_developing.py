# SPDX-License-Identifier: Apache-2.0
"""Tests for docs/_ext/vgacap_developing.py: each directive builds a table
from the real source file in this repository, and the rules table names
every rule docs/_ext/vgacap_rules.py checks.

    uv run --locked --python 3.13 --only-group docs --with pytest pytest -q docs/tests
"""

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
import vgacap_rules  # noqa: E402

DIRECTIVES = ["vgacap-ci-jobs", "vgacap-ci-env", "vgacap-hil-options", "vgacap-hil-checks",
              "vgacap-cmake-options", "vgacap-page-rules", "vgacap-page-limits",
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


def test_rules_table_names_every_rule_the_checks_raise():
    source = pathlib.Path(vgacap_rules.__file__).read_text()
    raised = set(re.findall(r'\.(?:fail|note)\("([\d.]+)"', source))
    raised |= set(re.findall(r'"rule ([\d.]+):', source))
    listed = {rule for rule, _what, _result in vgacap_rules.CHECKS}
    assert raised == listed
