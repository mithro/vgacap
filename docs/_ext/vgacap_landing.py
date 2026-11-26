# SPDX-License-Identifier: Apache-2.0
"""The sidebar, built from the landing pages' lines.

A landing page (front matter `type: landing`) is one line for each
destination: a list item that starts with a link. The site's sidebar is the
tree those lines make, walked breadth-first from the root landing page: a
page's parent is the first landing page that reaches it, in line order.
Every page therefore has one place in the sidebar. A later line to the same
page, or a line back up to a landing page above, is a cross-link and adds
no entry, so lines may link in any direction without making a cycle.

Before MyST parses a landing page, this appends a hidden toctree of its
children in the tree. A page writes no toctree directive of its own; one
that does fails the build. The lines are read with markdown-it, as MyST
reads them, so a link in a code block is not a line. A line's link resolves
as the build resolves links elsewhere: relative to the page, `/` for the
repository root, a directory to its README. A line to a Markdown file or a
directory that is not a page fails the build, naming the line.

A change to any landing page can move a page between parents, so it makes
every landing page be read again.
"""

import posixpath
import re

from markdown_it import MarkdownIt
from sphinx.util import logging

logger = logging.getLogger(__name__)

FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
LANDING = re.compile(r"^type:\s*landing\s*$", re.M)
TOCTREE = re.compile(r"^\s*(```+|~~~+|:::+)\s*\{toctree\}", re.M)
SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.I)
WRAPPERS = ("strong_open", "em_open")
PARSER = MarkdownIt("commonmark")


def is_landing(text):
    head = FRONT_MATTER.match(text)
    return bool(head and LANDING.search(head[1]))


def resolve(docname, href, found_docs, srcdir):
    """(docname or None, problem or None) for a line's link."""
    if not href or href.startswith("#") or SCHEME.match(href) or href.startswith("//"):
        return None, None                      # an anchor here, or another site
    path = href.split("#", 1)[0].split("?", 1)[0]
    if path.startswith("/"):
        rel = posixpath.normpath(path.lstrip("/"))
    else:
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(docname), path))
    if rel == ".." or rel.startswith("../"):
        return None, f"{href} leaves the repository"
    if rel.endswith(".md"):
        target = rel[:-3]
    elif (srcdir / rel).is_dir():
        target = posixpath.join(rel, "README") if rel != "." else "README"
    else:
        return None, None                      # a source file: a link, not a page
    if target not in found_docs:
        return None, f"{href} is not a page"
    return target, None


def lines(docname, text, found_docs, srcdir, warn=True):
    """The docnames the landing page's lines link to, in order."""
    body = text[FRONT_MATTER.match(text).end():] if FRONT_MATTER.match(text) else text
    offset = text[:len(text) - len(body)].count("\n")
    tokens = PARSER.parse(body)
    out = []
    for i, token in enumerate(tokens):
        if token.type != "list_item_open" or i + 2 >= len(tokens):
            continue
        inline = tokens[i + 2]
        if tokens[i + 1].type != "paragraph_open" or inline.type != "inline":
            continue
        # what the line starts with, past emphasis and the empty text
        # markdown-it puts before it
        children = [c for c in inline.children if c.type not in WRAPPERS
                    and not (c.type == "text" and not c.content.strip())]
        if not children or children[0].type != "link_open":
            continue
        line = offset + (token.map[0] + 1 if token.map else 0)
        target, problem = resolve(docname, children[0].attrGet("href"), found_docs, srcdir)
        if problem and warn:
            logger.warning(f"landing line {line}: {problem}", location=(docname, line),
                           type="vgacap", subtype="landing")
        elif target:
            out.append(target)
    return out


def tree(app):
    """{landing docname: [child docnames]}, cached for the build."""
    if getattr(app, "vgacap_tree", None) is not None:
        return app.vgacap_tree
    env = app.env
    texts = {}
    for docname in sorted(env.found_docs):
        text = env.doc2path(docname).read_text(encoding="utf-8")
        if is_landing(text):
            texts[docname] = text
    root = app.config.root_doc
    children = {docname: [] for docname in texts}
    placed, queue = {root}, [root]
    while queue:
        parent = queue.pop(0)
        if parent not in texts:
            continue
        for target in lines(parent, texts[parent], env.found_docs, env.srcdir):
            if target not in placed:
                placed.add(target)
                children[parent].append(target)
                queue.append(target)
    app.vgacap_tree = children
    app.vgacap_landings = set(texts)
    return children


def on_env_get_outdated(app, env, added, changed, removed):
    app.vgacap_tree = None
    landings = set(tree(app))
    previous = getattr(env, "vgacap_landings", set())
    env.vgacap_landings = landings
    if (added | changed | removed) & (landings | previous) or added or removed:
        return sorted(landings - removed)
    return []


def on_source_read(app, docname, source):
    if TOCTREE.search(source[0]):
        logger.warning("a page writes no toctree: the sidebar comes from the landing "
                       "pages' lines (docs/_ext/vgacap_landing.py)", location=docname,
                       type="vgacap", subtype="landing")
    children = tree(app).get(docname)
    if children is None:
        return
    if not lines(docname, source[0], app.env.found_docs, app.env.srcdir, warn=False):
        logger.warning("landing page has no line linking to a page", location=docname,
                       type="vgacap", subtype="landing")
    if children:
        entries = "\n".join("/" + child for child in children)
        source[0] += f"\n\n```{{toctree}}\n:hidden:\n\n{entries}\n```\n"


def setup(app):
    app.connect("env-get-outdated", on_env_get_outdated)
    app.connect("source-read", on_source_read)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
