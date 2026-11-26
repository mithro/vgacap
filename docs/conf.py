# SPDX-License-Identifier: Apache-2.0
"""Sphinx configuration for the vgacap documentation (Read the Docs).

The pages are the repository's own Markdown files, read where they are so
that GitHub and the built site show the same text:

    uv run --locked --python 3.13 --only-group docs sphinx-build -W --keep-going -c docs . build/docs

The source directory is the repository root and this directory holds only
the configuration (`-c docs`). PAGES is the whole site, in navigation order.

MyST resolves links between pages, and their anchors, itself. After it has
parsed a page, `link_repository_files` changes the two kinds of link MyST
cannot resolve, so they go where they go on GitHub:

- a link to a repository file that is not Markdown (source code, LICENSE),
  which MyST would turn into a download, goes to the file on GitHub at the
  commit being built (a Markdown file has to be in PAGES: a link to one
  that is not fails the build);
- a link to a directory goes to its README page when that is a page, and
  otherwise to the directory on GitHub.

A link to a path that does not exist, or that leaves the repository, is
reported, so the build fails on it.
MyST drops a #fragment from a link to a file that is not a page (a GitHub
`file.c#L10` line link), so such a link goes to the top of the file.
index.html redirects the site root to the README page.
"""

import os
import pathlib
import posixpath
import subprocess

from docutils import nodes
from sphinx import addnodes
from sphinx.util import logging

logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parent.parent
GITHUB = "https://github.com/mithro/vgacap"

# Every page on the site, in navigation order. README is the front page.
PAGES = [
    "README",
    "python/ttcap/README",
    "firmware/README",
    "firmware/PROTOCOL",
    "mpy/README",
    "capture-rp1/README",
    "rtl/README",
    "gst/README",
    "src/tools/README",
    "tools/README",
    "docs/development",
    "docs/frames-format",
    "docs/trace-format",
    "third_party/README",
    "docs/diagrams/NOTICE",
]

project = "vgacap"
author = "vgacap contributors"
copyright = "vgacap contributors, Apache-2.0"

extensions = ["myst_parser"]
source_suffix = {".md": "markdown"}
root_doc = "README"
include_patterns = [page + ".md" for page in PAGES]

# GitHub-style ids for headings, so `file.md#some-heading` links resolve.
myst_heading_anchors = 6
# As on GitHub: a standalone <img> tag is an image (copied into the site),
# and a bare http(s):// or www. URL is a link. Not fuzzy: boards.py and
# README.md are not web sites (.py and .md are country domains).
myst_enable_extensions = ["html_image", "linkify"]
myst_linkify_fuzzy_links = False

html_theme = "furo"
html_title = "vgacap"
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")
# The Markdown is on GitHub; the copy Sphinx would publish carries the
# navigation appended to README below, which is not in the file.
html_copy_source = False


def commit():
    """The commit being built: Read the Docs says; otherwise ask git."""
    sha = os.environ.get("READTHEDOCS_GIT_COMMIT_HASH")
    if sha:
        return sha
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def on_source_read(app, docname, source):
    """The navigation, as a hidden toctree appended to the front page."""
    if docname == root_doc:
        entries = "\n".join(PAGES[1:])
        source[0] += f"\n\n```{{toctree}}\n:hidden:\n\n{entries}\n```\n"


def github_link(node, kind, rel, sha):
    link = nodes.reference("", "", *node.children, internal=False,
                           refuri=f"{GITHUB}/{kind}/{sha}/{rel}")
    node.replace_self(link)


def repository_path(docdir, path):
    """`path` as linked from a page in `docdir`, relative to the repository
    root (a leading / is the root, as on GitHub); None if it leaves it."""
    if path.startswith("/"):
        rel = posixpath.normpath(path.lstrip("/"))
    else:
        rel = posixpath.normpath(posixpath.join(docdir, path))
    return None if rel == ".." or rel.startswith("../") else rel


def link_repository_files(app, doctree):
    """Runs before Sphinx's own collectors, so a file MyST marked for
    download is never registered as one. A MyST download left over is a
    link this cannot place, and is reported, so -W fails on it."""
    sha = app.config.vgacap_commit
    docdir = posixpath.dirname(app.env.docname)
    for node in list(doctree.findall(addnodes.download_reference)):
        if node.get("reftype") != "myst":
            continue
        rel = repository_path(docdir, node["reftarget"])
        if rel is not None and (REPO / rel).is_file():
            github_link(node, "blob", rel, sha)
        else:
            logger.warning("link to %s is outside the repository or not a file",
                           node["reftarget"], location=node)
    for node in list(doctree.findall(addnodes.pending_xref)):
        if node.get("reftype") != "myst" or node.get("refdomain"):
            continue
        # An unresolved target keeps its #fragment; MyST's page links carry
        # it as reftargetid instead.
        path, _, anchor = node["reftarget"].partition("#")
        rel = repository_path(docdir, path) if path else None
        if rel is None or not (REPO / rel).is_dir():
            continue
        readme = posixpath.join(rel, "README") if rel != "." else "README"
        if readme in PAGES:
            node["refdomain"], node["reftarget"] = "doc", readme
            node["reftargetid"] = anchor or None
        else:
            github_link(node, "tree", rel, sha)


INDEX = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>vgacap</title>
<meta http-equiv="refresh" content="0; url={page}">
<link rel="canonical" href="{page}"></head>
<body><a href="{page}">vgacap</a></body></html>
"""


def write_index(app, exception):
    """The front page is README.html (README.md stays where GitHub shows
    it), and a web server serves index.html for the site root."""
    if exception is None and app.builder.format == "html":
        page = app.builder.get_target_uri(root_doc)
        (pathlib.Path(app.outdir) / "index.html").write_text(INDEX.format(page=page))


def setup(app):
    app.add_config_value("vgacap_commit", commit(), "env")
    app.connect("source-read", on_source_read)
    app.connect("doctree-read", link_repository_files, priority=100)
    app.connect("build-finished", write_index)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
