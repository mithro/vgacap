# SPDX-License-Identifier: Apache-2.0
"""Tests for docs/_ext: the page rules (vgacap_rules) and the light and dark
picture pairs (vgacap_picture). Each test builds a small Sphinx project.

    uv run --locked --python 3.13 --only-group docs pytest -q docs/tests
"""

import io
import re
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


def build(tmp_path, pages, pending=None, files=None, parallel=0, src=None, fresh=True):
    """Build `pages` (name -> Markdown) under a root landing page that has
    one line for each, unless `pages` gives its own "index"."""
    src = src or tmp_path / "src"
    src.mkdir(exist_ok=True)
    names = [name for name in pages if name != "index"]
    index = pages.get("index", front("landing") + "# Root\n\n" + "".join(
        f"- [{name}]({name}.md): the {name} page.\n" for name in names))
    (src / "index.md").write_text(index)
    for name, text in pages.items():
        if name != "index":
            (src / f"{name}.md").write_text(text)
    for name, text in (files or {}).items():
        (src / name).write_text(text)
    (src / "conf.py").write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(EXT)!r})
        extensions = ["myst_parser", "vgacap_landing", "vgacap_picture", "vgacap_rules"]
        source_suffix = {{".md": "markdown"}}
        myst_enable_extensions = ["html_image", "linkify"]
        myst_linkify_fuzzy_links = False
        html_theme = "furo"   # the site's theme: its sidebar shows every level
        vgacap_rules_pending = {pending or {}!r}
        import datetime
        vgacap_today = datetime.date(2026, 11, 27)
    """))
    warnings, status = io.StringIO(), io.StringIO()
    # Each Sphinx registers its nodes and roles with docutils globally; the
    # namespace undoes that, so one test's build does not warn in the next.
    with docutils_namespace():
        app = Sphinx(str(src), str(src), str(tmp_path / "out"), str(tmp_path / "doctrees"),
                     "html", status=status, warning=warnings, freshenv=fresh,
                     parallel=parallel, verbosity=1)
        app.build()
    return warnings.getvalue(), status.getvalue(), tmp_path / "out"


def rules(warnings):
    return sorted({line.split("rule ")[1].split(":")[0]
                   for line in warnings.splitlines() if "[vgacap.rules]" in line and "rule " in line})


def page(body, kind="explanation", title="A page"):
    return front(kind) + f"# {title}\n\n" + PROSE + "\n" + body + "\n"


def test_a_good_page_passes(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": front() + "# A page\n\n" + PROSE})
    assert warnings == ""


def test_front_matter_is_required(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": "# A page\n\n" + PROSE})
    assert rules(warnings) == ["10.14"]


def test_unknown_type(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": front("faq") + "# A page\n\n" + PROSE})
    assert rules(warnings) == ["10.14"]


@pytest.mark.parametrize("review", ["20990101", "2099-13-01", "soon"])
def test_review_must_be_a_date(tmp_path, review):
    text = front(review=review).replace("soon", "someday") + "# A page\n\n" + PROSE
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == ["10.14"]


@pytest.mark.parametrize("word", ["just", "Simply", "e.g.", "etc.", "etc,", "in order to",
                                  "in order\nto", "TODO", "not yet", "currently",
                                  "easy-to-use", "soon-to-be"])
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


@pytest.mark.parametrize("label", ["Future:", "Sources:"])
def test_forbidden_labels(tmp_path, label):
    warnings, _, _ = build(tmp_path, {"page": page(f"{label} a list of things.")})
    assert rules(warnings) == ["10.1"]


def test_labels_are_words_in_a_sentence(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": page("It has two capture sources: a board and a Pi.")})
    assert warnings == ""


def test_a_bare_url_is_not_prose(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": page("The runner is at https://github.com/casey/just today.")})
    assert warnings == ""


def test_link_text_is_prose(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": page("See [just this](https://example.com) page.")})
    assert rules(warnings) == ["10.1"]


def test_long_title(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": front() + "# " + "x" * 66 + "\n\n" + PROSE})
    assert rules(warnings) == ["10.2"]


def test_duplicate_title(tmp_path):
    text = front() + "# A page\n\n" + PROSE
    warnings, _, _ = build(tmp_path, {"one": text, "two": text})
    assert rules(warnings) == ["10.3"]
    assert "one.md" in warnings and "two.md" in warnings


def test_duplicate_title_with_a_pending_page(tmp_path):
    text = front() + "# A page\n\n" + PROSE
    warnings, status, _ = build(tmp_path, {"one": text, "two": text}, pending={"two": "why"})
    # the page that is not pending fails; the pending one is reported, and
    # still counts as failing, so it is not told to leave the list
    assert rules(warnings) == ["10.3"] and "one.md" in warnings
    assert "take it out" not in warnings and "pending page two: rule 10.3" in status


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


@pytest.mark.parametrize("words,fails", [(25, False), (26, True)])
def test_step_sentence_limit(tmp_path, words, fails):
    step = " ".join(["word"] * (words - 1)) + " end."
    text = front("how-to") + HOWTO.replace(
        "At the bench, connect the board, and its light comes on.", step.capitalize())
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == (["10.4"] if fails else [])


def test_step_sentences_split_after_quotes(tmp_path):
    half = " ".join(["word"] * 15)
    step = f'Type "{half}." Then {half}.'
    text = front("how-to") + HOWTO.replace(
        "At the bench, connect the board, and its light comes on.", step)
    warnings, _, _ = build(tmp_path, {"page": text})
    assert warnings == ""


def test_code_in_a_step_counts_as_one_word(tmp_path):
    code = "`" + " ".join(["x"] * 40) + "`"
    step = "Run " + code + " " + " ".join(["word"] * 23) + " end."
    text = front("how-to") + HOWTO.replace(
        "At the bench, connect the board, and its light comes on.", step)
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == ["10.4"]


def test_a_nested_step_is_checked(tmp_path):
    long = "At the bench, " + " ".join(["connect"] * 30) + " the board."
    text = front("how-to") + HOWTO.replace(
        "At the bench, connect the board, and its light comes on.",
        "At the bench, connect it.\n   1. " + long)
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == ["10.4"]


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
    assert re.search(r'<img[^>]*class="only-light"[^>]*src="_images/d\.svg"'
                     r'|<img[^>]*src="_images/d\.svg"[^>]*class="only-light"', html)
    assert re.search(r'<img[^>]*class="only-dark"[^>]*src="_images/d-dark\.svg"'
                     r'|<img[^>]*src="_images/d-dark\.svg"[^>]*class="only-dark"', html)
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


def test_an_image_inside_html_is_an_error(tmp_path):
    text = page('<p align="center"><img src="d.png" alt="x"></p>')
    warnings, _, _ = build(tmp_path, {"page": text}, files={"d.png": "png"})
    assert rules(warnings) == ["10.8"]


def test_a_remote_svg_badge_is_not_a_drawing(tmp_path):
    text = page("![CI](https://github.com/o/r/actions/workflows/ci.yml/badge.svg)")
    warnings, _, _ = build(tmp_path, {"page": text})
    assert warnings == ""


def test_an_upper_case_svg_is_a_drawing(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": page("![A drawing](D.SVG)")}, files={"D.SVG": SVG})
    assert rules(warnings) == ["10.9"]


def test_a_lone_switched_image_fails(tmp_path):
    text = page("```{image} d.svg\n:alt: A drawing\n:class: only-light\n```")
    warnings, _, _ = build(tmp_path, {"page": text}, files={"d.svg": SVG})
    assert rules(warnings) == ["10.9"]


def test_a_twin_shown_unswitched_fails(tmp_path):
    text = page("![A photo](p.png)\n\n![A photo](p-dark.png)")
    warnings, _, _ = build(tmp_path, {"page": text}, files={"p.png": "png", "p-dark.png": "png"})
    assert rules(warnings) == ["10.9"]


def test_a_malformed_picture_is_an_error(tmp_path):
    text = front() + "# A page\n\n" + PROSE + "\n" + PICTURE.replace(
        '(prefers-color-scheme: dark)', '(min-width: 600px)')
    warnings, _, _ = build(tmp_path, {"page": text}, files={"d.svg": SVG, "d-dark.svg": SVG})
    assert "[vgacap.picture]" in warnings


def test_link_only_page(tmp_path):
    text = front() + "# A page\n\n- [one](https://example.com)\n- [two](https://example.org)\n"
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == ["1.6", "10.12"]


def test_two_sentences_alone_are_too_small(tmp_path):
    text = front() + "# A page\n\nThis is one sentence of prose. This is the second one.\n"
    warnings, _, _ = build(tmp_path, {"page": text})
    assert rules(warnings) == ["1.6"]


def test_a_reference_table_page_is_enough(tmp_path):
    table = "| Name | Value |\n|---|---|\n| one | 1 |\n| two | 2 |\n| three | 3 |\n"
    text = front("reference") + "# Values\n\nThe values a board reports. Each row is one.\n\n" + table
    warnings, _, _ = build(tmp_path, {"page": text})
    assert warnings == ""


def test_landing_page_may_be_links(tmp_path):
    text = front("landing") + "# A page\n\n- [other](other.md): the other page.\n"
    other = front() + "# Other\n\n" + PROSE
    pages = {"page": text, "other": other,
             "index": front("landing") + "# Root\n\n- [page](page.md): the landing.\n"}
    warnings, _, _ = build(tmp_path, pages)
    assert warnings == ""


def test_a_landing_page_needs_a_line(tmp_path):
    text = front("landing") + "# A page\n\n- [one](https://example.com): elsewhere.\n"
    warnings, _, _ = build(tmp_path, {"page": text})
    assert "[vgacap.landing]" in warnings


def test_sidebar_group(tmp_path):
    pages = {f"p{i}": front() + f"# Page {i}\n\n" + PROSE for i in range(8)}
    lines = "".join(f"- [p{i}](p{i}.md): page {i}.\n" for i in range(8))
    pages["group"] = front("landing") + "# Group\n\n" + lines
    pages["index"] = front("landing") + "# Root\n\n- [group](group.md): the group.\n"
    warnings, _, _ = build(tmp_path, pages)
    assert rules(warnings) == ["10.10"]


def test_a_landing_page_lines_are_the_sidebar(tmp_path):
    pages = {"a": front() + "# Page A\n\n" + PROSE, "b": front() + "# Page B\n\n" + PROSE}
    pages["index"] = front("landing") + "# Root\n\n- [B](b.md): second.\n- [A](a.md): first.\n"
    warnings, _, out = build(tmp_path, pages)
    assert warnings == ""
    html = (out / "index.html").read_text()
    assert html.index('href="b.html"') < html.index('href="a.html"')


def test_a_page_on_no_landing_page_fails(tmp_path):
    pages = {"a": front() + "# Page A\n\n" + PROSE, "b": front() + "# Page B\n\n" + PROSE}
    pages["index"] = front("landing") + "# Root\n\n- [A](a.md): the only line.\n"
    warnings, _, _ = build(tmp_path, pages)
    assert "isn't included in any toctree" in warnings


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
    assert warnings == "" and "pending page page: rule 10.14" in status


def test_a_pending_page_that_passes_must_leave_the_list(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": front() + "# A page\n\n" + PROSE},
                           pending={"page": "a reason"})
    assert "take it out of vgacap_rules_pending" in warnings


def test_an_incremental_build_tells_a_fixed_pending_page_to_leave(tmp_path):
    src = tmp_path / "src"
    build(tmp_path, {"page": "# A page\n\n" + PROSE}, pending={"page": "why"}, src=src)
    (src / "page.md").write_text(front() + "# A page\n\n" + PROSE)
    warnings, _, _ = build(tmp_path, {"page": front() + "# A page\n\n" + PROSE},
                           pending={"page": "why"}, src=src, fresh=False)
    assert "take it out of vgacap_rules_pending" in warnings


# -- the sidebar from the landing pages (vgacap_landing) ----------------------

A = front() + "# Page A\n\n" + PROSE
B = front() + "# Page B\n\n" + PROSE


def sidebar(out, name="index"):
    """The (level, title) entries of the built page's sidebar."""
    html = (out / f"{name}.html").read_text()
    return re.findall(r'class="toctree-l(\d)[^"]*"><a class="reference internal" '
                      r'href="[^"]*">([^<]*)<', html)


def two_sections(s1_lines, s2_lines="- [A](../a.md): page A.\n"):
    return {
        "a": A, "b": B,
        "s1/index": front("landing") + "# One\n\n" + s1_lines,
        "s2/index": front("landing") + "# Two\n\n" + s2_lines,
        "index": front("landing") + "# Root\n\n- [One](s1/index.md): one.\n"
                 "- [Two](s2/index.md): two.\n",
    }


def build_tree(tmp_path, pages, **kw):
    for name in pages:
        (tmp_path / "src" / name).parent.mkdir(parents=True, exist_ok=True)
    return build(tmp_path, pages, **kw)


def test_a_line_back_up_is_a_cross_link(tmp_path):
    pages = two_sections("- [B](../b.md): page B.\n- [Up](../index.md): the site.\n")
    warnings, _, out = build_tree(tmp_path, pages)
    assert warnings == ""
    assert sidebar(out) == [("1", "One"), ("2", "Page B"), ("1", "Two"), ("2", "Page A")]


def test_a_page_on_two_landing_pages_fails(tmp_path):
    pages = two_sections("- [B](../b.md): page B.\n- [A](../a.md): page A.\n")
    warnings, _, _ = build_tree(tmp_path, pages)
    assert "a is a line on s1/index and on s2/index" in warnings


def test_a_cross_link_is_not_a_line(tmp_path):
    pages = two_sections("- [B](../b.md): page B.\n- Page A is also under [Two](../a.md).\n")
    warnings, _, out = build_tree(tmp_path, pages)
    assert warnings == ""
    assert sidebar(out) == [("1", "One"), ("2", "Page B"), ("1", "Two"), ("2", "Page A")]


def test_a_toctree_in_a_code_example_is_allowed(tmp_path):
    text = page("````markdown\n```{toctree}\nother\n```\n````")
    pages = {"page": text, "other": B,
             "index": front("landing") + "# Root\n\n- [P](page.md): p.\n- [O](other.md): o.\n"}
    warnings, _, _ = build(tmp_path, pages)
    assert warnings == ""


def test_a_line_to_a_file_name_with_a_space(tmp_path):
    pages = {"a b": A, "index": front("landing") + "# Root\n\n- [A](<a b.md>): page A.\n"}
    warnings, _, out = build(tmp_path, pages)
    assert warnings == "" and ("1", "Page A") in sidebar(out)


@pytest.mark.parametrize("line", [
    '- [B](../b.md "Page B"): with a title.',
    "- **[B](../b.md)**: in bold.",
    "- [B](/b.md): from the repository root.",
    "- [B [the second]](../b.md): brackets in the text.",
])
def test_line_forms(tmp_path, line):
    warnings, _, out = build_tree(tmp_path, two_sections(line + "\n"))
    assert warnings == "" and ("2", "Page B") in sidebar(out)


def test_another_site_and_code_are_not_lines(tmp_path):
    lines = ("- [B](../b.md): page B.\n- [Upstream](https://example.com/r/README.md): elsewhere.\n"
             "\n```\n- [A](../a.md): in code.\n```\n")
    warnings, _, out = build_tree(tmp_path, two_sections(lines, "- [A](../a.md): page A.\n"))
    assert warnings == ""
    assert sidebar(out) == [("1", "One"), ("2", "Page B"), ("1", "Two"), ("2", "Page A")]


def test_a_line_to_no_page_names_the_line(tmp_path):
    pages = two_sections("- [B](../b.md): page B.\n- [C](../c.md): not a page.\n")
    warnings, _, _ = build_tree(tmp_path, pages)
    assert "landing line" in warnings and "../c.md is not a page" in warnings


def test_a_page_writes_no_toctree(tmp_path):
    text = page("```{toctree}\n:hidden:\n\nother\n```")
    warnings, _, _ = build(tmp_path, {"page": text, "other": B})
    assert "writes no toctree" in warnings


def test_a_step_starting_with_code_is_its_own_sentence(tmp_path):
    first = "At the bench, " + " ".join(["connect"] * 14) + " the board."
    second = "`ttcap` prints " + " ".join(["word"] * 14) + " here."
    text = front("how-to") + HOWTO.replace(
        "At the bench, connect the board, and its light comes on.", first + " " + second)
    warnings, _, _ = build(tmp_path, {"page": text})
    assert warnings == ""


def test_a_path_is_not_a_banned_word(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": page("The file lives in /etc/hosts on the Pi.")})
    assert warnings == ""


def test_an_image_in_an_html_comment_is_not_an_image(tmp_path):
    warnings, _, _ = build(tmp_path, {"page": page('<!-- <img src="x.png"> was here -->')})
    assert warnings == ""
