# SPDX-License-Identifier: Apache-2.0
"""A drawing's light and dark twins, written once for GitHub and the site.

A page shows a drawing as one HTML block, which GitHub renders with the
viewer's theme:

    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="overview-dark.svg">
      <img src="overview.svg" alt="What the drawing shows" width="100%">
    </picture>

The block has no blank line inside it, so CommonMark reads it as one HTML
block. After MyST parses a page, this turns each such block into the pair
the theme switches between: the `<img>` as an image with the class
`only-light`, the dark `<source>` as one with `only-dark`. They are image
nodes, so Sphinx copies both files and rebuilds the page when either
changes. A `<picture>` block that is not of this form is an error.
"""

import html.parser

from docutils import nodes
from sphinx.util import logging

logger = logging.getLogger(__name__)

DARK = "(prefers-color-scheme: dark)"
IMG_ATTRIBUTES = ("width", "height")


class PictureParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def parse(block):
    """(light src, dark src, alt, img attributes) or a reason it is not a pair."""
    parser = PictureParser()
    parser.feed(block)
    names = [t for t, _ in parser.tags]
    if names != ["picture", "source", "img"]:
        return f"expected <picture><source><img>, found {' '.join(f'<{n}>' for n in names)}"
    source, img = parser.tags[1][1], parser.tags[2][1]
    if source.get("media") != DARK:
        return f'the <source> media must be "{DARK}"'
    if not source.get("srcset") or not img.get("src"):
        return "the <source> needs a srcset and the <img> a src"
    extra = {k: img[k] for k in IMG_ATTRIBUTES if k in img}
    return img["src"], source["srcset"], img.get("alt", ""), extra


def image(uri, alt, extra, theme):
    node = nodes.image(uri=uri, alt=alt, classes=[f"only-{theme}"])
    for key, value in extra.items():
        node[key] = value
    return node


def on_doctree_read(app, doctree):
    """Runs before Sphinx's image collector (priority 500), so both files are
    registered as the page's images."""
    for raw in list(doctree.findall(nodes.raw)):
        text = raw.astext().strip()
        if raw.get("format") != "html" or not text.lower().startswith("<picture"):
            continue
        result = parse(text)
        if isinstance(result, str):
            logger.warning(f"<picture> block: {result}", location=raw,
                           type="vgacap", subtype="picture")
            continue
        light, dark, alt, extra = result
        pair = [image(light, alt, extra, "light"), image(dark, alt, extra, "dark")]
        for node in pair:
            node.source, node.line = raw.source, raw.line
        raw.replace_self(pair)


def setup(app):
    app.connect("doctree-read", on_doctree_read, priority=100)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
