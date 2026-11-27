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
    # --edge's choices, --max-bytes' default and --frames' exclusive partner.
    assert "<code class=\"docutils literal notranslate\"><span class=\"pre\">rising</span></code>" in page
    assert "Default: <code class=\"docutils literal notranslate\"><span class=\"pre\">0</span>" in page
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


def test_options_survive_smart_quotes(tmp_path):
    """`--max-bytes` in help text must not become an en dash, nor '-' curly."""
    _, page = build(tmp_path, "```{ttcap-commands}\n```\n")
    assert "–" not in page and "&#8211;" not in page
    assert "‘-’" not in page
    assert '<span class="pre">--max-bytes</span>' in page


def test_no_empty_or_repeated_default(tmp_path):
    _, page = build(tmp_path, "```{ttcap-commands}\n```\n")
    assert "Default: ." not in page
    # --seconds' help already says "(default 5)", so no Default line repeats it.
    seconds = page.split('<span class="pre">--seconds')[1].split("</dd>")[0]
    assert "Default:" not in seconds


def _definition(page, option):
    """The definition of the term that is `option` on `page`, as text: the
    first one, `ttcap capture`'s, since its section comes before `demo`'s."""
    import html
    import re
    for term, body in re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd>(.*?)</dd>", page, re.S):
        words = html.unescape(re.sub(r"<[^>]+>", "", term)).split()
        if words and words[0] == option:
            return re.sub(r"<[^>]+>", "", body)
    raise AssertionError(f"no term {option} on the page")


def test_the_options_a_mode_refuses_say_so(tmp_path):
    # From ttcap.cli's own NOT_CLOCKED and CLOCKED_ONLY, so the page cannot
    # drift from what `ttcap capture` refuses.
    _, page = build(tmp_path, "```{ttcap-commands}\n```\n")
    cli = ttcap().cli
    for option in cli.NOT_CLOCKED.values():
        assert "Not with --mode clocked." in _definition(page, option), option
    for option in cli.CLOCKED_ONLY.values():
        assert "Only with --mode clocked." in _definition(page, option), option
    for option in ("--out", "--ui-in", "--pio", "--buf-words"):
        entry = _definition(page, option)
        assert "--mode clocked" not in entry, option


def test_usage_is_plain_text(tmp_path):
    _, page = build(tmp_path, "```{ttcap-commands}\n```\n")
    assert 'highlight-text' in page


def rows(page):
    """The table rows of `page`, as lists of cell texts."""
    import re
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        out.append([re.sub(r"<[^>]+>", "", c).strip() for c in cells])
    return out


def test_gpio_map_cells_are_in_their_columns(tmp_path):
    _, page = build(tmp_path, "```{ttcap-gpio-map} pins\n```\n")
    table = rows(page)
    head, body = table[0], {r[0]: r for r in table[1:]}
    assert head == ["Signal", "VGA signal", "rp2040-tt06map GPIO", "rp2350-dbv3 GPIO",
                    "rp2040-tt06map sample bit", "rp2350-dbv3 sample bit"]
    assert body["uo_out[7]"] == ["uo_out[7]", "hsync", "GPIO 16", "GPIO 40", "11", "7"]
    assert body["uo_out[3]"] == ["uo_out[3]", "vsync", "GPIO 8", "GPIO 36", "3", "3"]
    assert body["uo_out[0]"] == ["uo_out[0]", "R1", "GPIO 5", "GPIO 33", "0", "0"]
    assert body["project clock"][2:4] == ["GPIO 0", "GPIO 16"]


def test_welland_cells_are_in_their_columns(tmp_path):
    _, page = build(tmp_path, "```{ttcap-welland-boards}\n```\n")
    table = rows(page)
    assert table[0] == ["Slug", "Pi", "Bridge", "Tunnel port", "Chip"]
    body = {r[0]: r for r in table[1:]}
    assert body["fpga-1"] == ["fpga-1", "pi-sw2-p34", "ws://10.21.2.34:8765/serial", "18034",
                              "RP2350"]
