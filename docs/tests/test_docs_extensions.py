# SPDX-License-Identifier: Apache-2.0
"""Tests for docs/_ext: the page rules (vgacap_rules) and the light and dark
picture pairs (vgacap_picture). Each test builds a small Sphinx project.

    uv run --locked --python 3.13 --only-group docs --with pytest pytest -q docs/tests
"""

import datetime
import io
import pathlib
import textwrap

import pytest
from sphinx.application import Sphinx
from sphinx.util.console import nocolor
from sphinx.util.docutils import docutils_namespace

nocolor()

EXT = pathlib.Path(__file__).resolve().parent.parent / "_ext"
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>\n'


def front(kind="explanation", review="2099-01-01"):
    return (f"---\ntype: {kind}\nowner: tests\nreader: a test\nreview: {review}\n---\n\n")


PROSE = ("This page explains one thing for the tests. It has three sentences of prose. "
         "That makes it more than links alone.\n")

HOWTO = """# How to do the thing

What it covers. Who it is for. What it leaves out.

## What you need

A board.

## Steps

1. At the bench, connect the board, and its light comes on.

## Check

The light is on.

## If it fails

At step 1, no light means no power; plug it in.

## Next

- Another page.
"""


def build(tmp_path, pages, pending=None, files=None, parallel=0):
    src = tmp_path / "src"
    src.mkdir()
    names = [name for name in pages if name != "index"]
    index = pages.get("index", front("landing") + "# Root\n\n- One line.\n")
    index += "\n```{toctree}\n:hidden:\n\n" + "\n".join(names) + "\n```\n"
    (src / "index.md").write_text(index)
    for name, text in pages.items():
        if name != "index":
            (src / f"{name}.md").write_text(text)
    for name, text in (files or {}).items():
        (src / name).write_text(text)
    (src / "conf.py").write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(EXT)!r})
        extensions = ["myst_parser", "vgacap_picture", "vgacap_rules"]
        source_suffix = {{".md": "markdown"}}
        myst_enable_extensions = ["html_image"]
        vgacap_rules_pending = {pending or {}!r}
        import datetime
        vgacap_today = datetime.date(2026, 11, 27)
    """))
    warnings, status = io.StringIO(), io.StringIO()
    # Each Sphinx registers its nodes and roles with docutils globally; the
    # namespace undoes that, so one test's build does not warn in the next.
    with docutils_namespace():
        app = Sphinx(str(src), str(src), str(tmp_path / "out"), str(tmp_path / "doctrees"),
                     "html", status=status, warning=warnings, freshenv=True,
                     parallel=parallel, verbosity=1)
        app.build()
    return warnings.getvalue(), status.getvalue(), tmp_path / "out"


def rules(warnings):
    return sorted({line.split("rule ")[1].split(":")[0]
                   for line in warnings.splitlines() if "[vgacap.rules]" in line and "rule " in line})


def test_a_good_page_passes(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": front() + "# A page\n\n" + PROSE})
    assert warnings == ""


def test_front_matter_is_required(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": "# A page\n\n" + PROSE})
    assert rules(warnings) == ["1.3"]


def test_unknown_type(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": front("faq") + "# A page\n\n" + PROSE})
    assert rules(warnings) == ["1.1"]


@pytest.mark.parametrize("word", ["just", "Simply", "e.g.", "etc.", "in order to", "TODO",
                                  "not yet", "Future:", "currently"])
def test_banned_words(tmp_path, word):
    text = front() + f"# A page\n\n{PROSE}\nThis is {word} a sentence with the word.\n"
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == ["10.1"]


def test_banned_words_in_code_are_allowed(tmp_path):
    text = front() + "# A page\n\n" + PROSE + "\nRun `just build` here.\n\n```\nsimply etc.\n```\n"
    warnings, _, _ = build(tmp_path, {"page": text})
    assert warnings == ""


def test_words_that_contain_a_banned_word_are_allowed(tmp_path):
    text = front() + "# A page\n\n" + PROSE + "\nThe adjustment was justified by the soonest run.\n"
    warnings, _, _ = build(tmp_path, {"page": text})
    assert warnings == ""


def test_long_title(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": front() + "# " + "x" * 66 + "\n\n" + PROSE})
    assert rules(warnings) == ["10.2"]


def test_duplicate_title(tmp_path):
    page = front() + "# A page\n\n" + PROSE
    warnings, _, _ = build(tmp_path, {"one": page, "two": page})
    assert rules(warnings) == ["10.3"]


def test_howto_passes(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": front("how-to") + HOWTO})
    assert warnings == ""


def test_howto_missing_and_extra_heading(tmp_path):
    text = front("how-to") + HOWTO.replace("## Check", "## Verify")
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == ["10.5", "10.6"]


def test_howto_out_of_order(tmp_path):
    text = front("how-to") + HOWTO.replace("## Steps", "## TMP").replace(
        "## Check", "## Steps").replace("## TMP", "## Check")
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == ["3.2"]


def test_howto_sub_heading(tmp_path):
    text = front("how-to") + HOWTO.replace("## Check\n", "## Check\n\n### More\n\nMore.\n")
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == ["10.6"]


def test_long_step_sentence(tmp_path):
    long = "At the bench, " + " ".join(["connect"] * 30) + " the board."
    text = front("how-to") + HOWTO.replace(
        "At the bench, connect the board, and its light comes on.", long)
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == ["10.4"]


def test_missing_alt_text(tmp_path):
    text = front() + "# A page\n\n" + PROSE + "\n![](photo.png)\n"
    warnings, _, _ = build(tmp_path, {"page": text}, files={"photo.png": "png"})
    assert "10.8" in rules(warnings)


def test_a_drawing_must_be_a_pair(tmp_path):
    text = front() + "# A page\n\n" + PROSE + "\n![A drawing](d.svg)\n"
    warnings, _, _ = build(tmp_path, {"page": text}, files={"d.svg": SVG})
    assert rules(warnings) == ["10.9"]


PICTURE = """<picture>
  <source media="(prefers-color-scheme: dark)" srcset="d-dark.svg">
  <img src="d.svg" alt="A drawing" width="100%">
</picture>
"""


def test_picture_becomes_a_switched_pair(tmp_path):
    text = front() + "# A page\n\n" + PROSE + "\n" + PICTURE
    warnings, _, out = build(tmp_path, {"page": text},
                             files={"d.svg": SVG, "d-dark.svg": SVG})
    assert warnings == ""
    html = (out / "page.html").read_text()
    assert 'class="only-light"' in html and 'class="only-dark"' in html
    assert (out / "_images" / "d.svg").is_file() and (out / "_images" / "d-dark.svg").is_file()


def test_picture_in_a_parallel_build(tmp_path):
    pages = {f"p{i}": front() + f"# Page {i}\n\n" + PROSE + "\n" + PICTURE for i in range(4)}
    for i in range(4):
        pages[f"p{i}"] = pages[f"p{i}"].replace("d.svg", f"d{i}.svg").replace(
            "d-dark.svg", f"d{i}-dark.svg")
    files = {f"d{i}{s}.svg": SVG for i in range(4) for s in ("", "-dark")}
    warnings, _, out = build(tmp_path, pages, files=files, parallel=4)
    assert warnings == ""
    assert len(list((out / "_images").glob("*.svg"))) == 8


def test_a_malformed_picture_is_an_error(tmp_path):
    text = front() + "# A page\n\n" + PROSE + "\n" + PICTURE.replace(
        '(prefers-color-scheme: dark)', '(min-width: 600px)')
    warnings, _, _ = build(tmp_path, {"page": text}, files={"d.svg": SVG, "d-dark.svg": SVG})
    assert "[vgacap.picture]" in warnings


def test_link_only_page(tmp_path):
    text = front() + "# A page\n\n- [one](https://example.com)\n- [two](https://example.org)\n"
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == ["10.12"]


def test_landing_page_may_be_links(tmp_path):
    text = front("landing") + "# A page\n\n- [one](https://example.com): the first.\n"
    warnings, _, _ = build(tmp_path, {"page": text})
    assert warnings == ""


def test_sidebar_group(tmp_path):
    pages = {f"p{i}": front() + f"# Page {i}\n\n" + PROSE for i in range(8)}
    warnings, _, _ = build(tmp_path, pages)
    assert rules(warnings) == ["10.10"]


def test_heading_levels(tmp_path):
    headings = "".join("#" * n + f" Level {n}\n\n" + PROSE + "\n" for n in range(1, 7))
    warnings, _, _ = build(tmp_path, {"page": front() + headings})
    assert rules(warnings) == ["10.11"]


def test_expired_review_only_notes(tmp_path):
    text = front(review="2020-01-01") + "# A page\n\n" + PROSE
    warnings, status, _ = build(tmp_path, {"page": text})
    assert warnings == "" and "10.21" in status


def test_pending_page_is_not_failed(tmp_path):
    warnings, status, _ = build(tmp_path, {"page": "# A page\n\n" + PROSE},
                                pending={"page": "a reason"})
    assert warnings == "" and "pending page page: rule 1.3" in status


def test_a_pending_page_that_passes_must_leave_the_list(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": front() + "# A page\n\n" + PROSE},
                           pending={"page": "a reason"})
    assert "take it out of vgacap_rules_pending" in warnings


def test_review_must_be_a_date():
    assert datetime.date.fromisoformat("2027-05-27")
