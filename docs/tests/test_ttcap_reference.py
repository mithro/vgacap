# SPDX-License-Identifier: Apache-2.0
"""Tests for docs/_ext/ttcap_reference: the ttcap reference tables built
from ttcap's own source. Each test builds a one-page Sphinx project.

    uv run --locked --python 3.13 --only-group docs --with pytest pytest -q docs/tests
"""

import io
import pathlib
import sys
import textwrap

from sphinx.application import Sphinx
from sphinx.util.console import nocolor
from sphinx.util.docutils import docutils_namespace

nocolor()

EXT = pathlib.Path(__file__).resolve().parent.parent / "_ext"
PYTHON = EXT.parent.parent / "python"


def build(tmp_path, body):
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.md").write_text("# Page\n\n" + body)
    (src / "conf.py").write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(EXT)!r})
        extensions = ["myst_parser", "ttcap_reference"]
        source_suffix = {{".md": "markdown"}}
    """))
    warnings, status = io.StringIO(), io.StringIO()
    with docutils_namespace():
        app = Sphinx(str(src), str(src), str(tmp_path / "out"), str(tmp_path / "doctrees"),
                     "html", status=status, warning=warnings, freshenv=True)
        app.build()
    return warnings.getvalue(), (tmp_path / "out" / "index.html").read_text()


def ttcap():
    sys.path.insert(0, str(PYTHON))
    import ttcap.boards
    import ttcap.cli
    return ttcap


def test_every_subcommand_and_option_is_listed(tmp_path):
    warnings, page = build(tmp_path, "```{ttcap-commands}\n```\n")
    assert warnings == ""
    parser = ttcap().cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    for name, subparser in sub.choices.items():
        assert f'id="ttcap-{name}"' in page
        for action in subparser._actions:
            for option in action.option_strings:
                if option not in ("-h", "--help"):
                    assert option in page, (name, option)


def test_defaults_choices_and_exclusions(tmp_path):
    _, page = build(tmp_path, "```{ttcap-commands}\n```\n")
    # --edge's choices, --seconds' default and --frames' exclusive partner.
    assert "<code class=\"docutils literal notranslate\"><span class=\"pre\">rising</span></code>" in page
    assert "Default: <code class=\"docutils literal notranslate\"><span class=\"pre\">5.0</span>" in page
    assert "Not with <code class=\"docutils literal notranslate\"><span class=\"pre\">--max-bytes" in page


def test_gpio_map_follows_the_profiles(tmp_path):
    warnings, page = build(tmp_path, "```{ttcap-gpio-map} pins\n```\n\n"
                                     "```{ttcap-gpio-map} window\n```\n")
    assert warnings == ""
    boards = ttcap().boards
    for profile in (boards.RP2040_TT06, boards.RP2350_DBV3):
        assert profile.name in page
        for gpio in profile.uo_gpios:
            assert f"GPIO {gpio}<" in page
    assert "hsync" in page and "B0" in page


def test_gpio_map_needs_a_table_name(tmp_path):
    warnings, _ = build(tmp_path, "```{ttcap-gpio-map} other\n```\n")
    assert "ttcap-gpio-map takes pins or window" in warnings


def test_welland_boards_follow_the_table(tmp_path):
    warnings, page = build(tmp_path, "```{ttcap-welland-boards}\n```\n")
    assert warnings == ""
    boards = ttcap().boards
    for slug, (pi, _) in boards.WELLAND.items():
        assert slug in page and pi in page
        assert boards.bridge_ws_url(slug) in page
