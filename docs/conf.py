# SPDX-License-Identifier: Apache-2.0
"""Sphinx configuration for the vgacap documentation (Read the Docs).

The pages are the repository's own Markdown files, read where they are so
that GitHub and the built site show the same text:

    uv run --only-group docs sphinx-build -W --keep-going -c docs . build/docs

The source directory is the repository root and this directory holds only
the configuration (`-c docs`). PAGES is the whole site, in navigation
order. A Markdown link to a repository file that is not a page (source
code, LICENSE, a directory without a README) is rewritten to that file on
GitHub at the commit being built. Images referenced by raw HTML `<img>` tags
are copied next to the page that uses them, at the same relative path, and
index.html redirects the site root to the README page.
"""

import os
import pathlib
import posixpath
import re
import shutil
import subprocess

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

html_theme = "furo"
html_title = "vgacap"


def commit():
    """The commit being built: Read the Docs says; otherwise ask git."""
    sha = os.environ.get("READTHEDOCS_GIT_COMMIT_HASH")
    if sha:
        return sha
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


MD_LINK = re.compile(r"(\]\()([^)\s]+)(\))")
HTML_SRC = re.compile(r"""(<img\b[^>]*\bsrc=")([^"]+)(")""")
# Fenced code blocks, then inline code spans (a run of backticks closed by
# the same run). Links and tags inside either are examples, not links.
FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,}).*?^\1\2[ \t]*$", re.M | re.S)
SPAN = re.compile(r"(`+).+?\1", re.S)


def prose_and_code(text):
    """Split `text` into alternating (is_code, piece) parts, in order."""
    parts, at = [], 0
    for fence in FENCE.finditer(text):
        parts += split_spans(text[at:fence.start()])
        parts.append((True, fence[0]))
        at = fence.end()
    return parts + split_spans(text[at:])


def split_spans(text):
    parts, at = [], 0
    for span in SPAN.finditer(text):
        parts.append((False, text[at:span.start()]))
        parts.append((True, span[0]))
        at = span.end()
    parts.append((False, text[at:]))
    return parts


def is_local(target):
    return not (target.startswith("#") or re.match(r"[a-z][a-z0-9+.-]*:", target))


def resolve(docname, target):
    """(repository-relative path, anchor) for a local link from `docname`."""
    path, _, anchor = target.partition("#")
    rel = posixpath.normpath(posixpath.join(posixpath.dirname(docname), path))
    return rel, anchor


def rewrite_link(docname, target, sha):
    if not is_local(target):
        return target
    rel, anchor = resolve(docname, target)
    on_disk = REPO / rel
    if on_disk.is_dir() and (on_disk / "README.md").is_file() and \
            posixpath.join(rel, "README") in PAGES:
        rel = posixpath.join(rel, "README.md")
        on_disk = REPO / rel
    if rel.endswith(".md") and rel[:-3] in PAGES:
        page = posixpath.relpath(rel, posixpath.dirname(docname) or ".")
        return page + ("#" + anchor if anchor else "")
    if not on_disk.exists():
        return target  # left alone, so Sphinx reports it and -W fails the build
    kind = "tree" if on_disk.is_dir() else "blob"
    return f"{GITHUB}/{kind}/{sha}/{rel}" + ("#" + anchor if anchor else "")


def toctree():
    entries = "\n".join(PAGES[1:])
    return f"\n\n```{{toctree}}\n:hidden:\n\n{entries}\n```\n"


def on_source_read(app, docname, source):
    sha = app.config.vgacap_commit
    pieces = []
    for is_code, piece in prose_and_code(source[0]):
        if not is_code:
            piece = MD_LINK.sub(lambda m: m[1] + rewrite_link(docname, m[2], sha) + m[3], piece)
            for m in HTML_SRC.finditer(piece):
                if is_local(m[2]):
                    app.env.vgacap_images.add(resolve(docname, m[2])[0])
        pieces.append(piece)
    text = "".join(pieces)
    if docname == root_doc:
        text += toctree()
    source[0] = text


def on_env_before_read_docs(app, env, docnames):
    env.vgacap_images = set()


INDEX = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>vgacap</title>
<meta http-equiv="refresh" content="0; url={page}">
<link rel="canonical" href="{page}"></head>
<body><a href="{page}">vgacap</a></body></html>
"""


def on_build_finished(app, exception):
    if exception is not None or app.builder.format != "html":
        return
    # The front page is README.html (README.md stays where GitHub shows it),
    # and a web server serves index.html for the site root.
    index = pathlib.Path(app.outdir) / "index.html"
    index.write_text(INDEX.format(page=app.builder.get_target_uri(root_doc)))
    for rel in sorted(getattr(app.env, "vgacap_images", ())):
        src = REPO / rel
        if not src.is_file():
            raise FileNotFoundError(f"<img> source {rel} does not exist")
        dst = pathlib.Path(app.outdir) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


def setup(app):
    app.add_config_value("vgacap_commit", commit(), "env")
    app.connect("env-before-read-docs", on_env_before_read_docs)
    app.connect("source-read", on_source_read)
    app.connect("build-finished", on_build_finished)
