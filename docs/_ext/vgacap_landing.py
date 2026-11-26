# SPDX-License-Identifier: Apache-2.0
"""A landing page's lines are its place in the sidebar.

A landing page (front matter `type: landing`) is one line for each
destination: a list item that starts with a link to a page. Before MyST
parses the page, this appends a hidden toctree holding those destinations,
in the page's order, so the sidebar nests exactly like the landing pages:
a page joins the sidebar through its line on the landing page above it.
A landing page needs no toctree of its own, and on GitHub it reads as the
plain list it is.
"""

import posixpath
import re

from sphinx.util import logging

logger = logging.getLogger(__name__)

FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
LANDING = re.compile(r"^type:\s*landing\s*$", re.M)
# A list item whose first thing is a link to a Markdown page.
LINE = re.compile(r"^\s{0,3}[-*+]\s+\[[^\]]*\]\(<?([^)>\s#]+\.md)(?:#[^)>\s]*)?>?\)", re.M)


def destinations(docname, text):
    """The docnames a landing page's lines link to, in order, once each."""
    docdir = posixpath.dirname(docname)
    found = []
    for match in LINE.finditer(text):
        target = posixpath.normpath(posixpath.join(docdir, match[1]))[:-len(".md")]
        if target not in found:
            found.append(target)
    return found


def on_source_read(app, docname, source):
    head = FRONT_MATTER.match(source[0])
    if not head or not LANDING.search(head[1]):
        return
    pages = destinations(docname, source[0])
    if not pages:
        logger.warning("landing page has no line linking to a page",
                       location=docname, type="vgacap", subtype="landing")
        return
    docdir = posixpath.dirname(docname)
    entries = "\n".join("/" + page if docdir else page for page in pages)
    source[0] += f"\n\n```{{toctree}}\n:hidden:\n\n{entries}\n```\n"


def setup(app):
    app.connect("source-read", on_source_read)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
