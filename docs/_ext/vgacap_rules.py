# SPDX-License-Identifier: Apache-2.0
"""The docs.fpgas.online page rules that a machine can decide.

Rule numbers are those of docs.fpgas.online's "What a page contains"
(https://github.com/fpgas-online/fpgas.online-docs/blob/main/docs/developer/about-these-docs/page-contents.md),
section 10. A failing rule is a warning of type `vgacap.rules`, so the
`-W` build fails on it; a rule that only warns there is logged at info
level here, so it is visible and does not fail the build.

`vgacap_rules_pending` (in conf.py) maps a page to the reason its rules
are not enforced yet. Its failures are logged at info level, and a
pending page that passes every rule is an error, so the list only shrinks.
"""

import datetime
import re

from docutils import nodes
from sphinx import addnodes
from sphinx.util import logging

logger = logging.getLogger(__name__)

TYPES = ("tutorial", "how-to", "reference", "explanation", "landing")
FRONT_MATTER = ("type", "owner", "reader", "review")
HOWTO_HEADINGS = ["What you need", "Steps", "Check", "If it fails", "Next"]

# 6.1 and 6.3: whole words or phrases, any case. `etc.`, `e.g.` and `i.e.`
# end in a full stop, so they are matched without a closing word boundary.
BANNED = ["just", "simply", "easy", "easily", "straightforward", "please note",
          "click here", "currently", "soon", "in order to", "as of this writing",
          "future:", "sources:", "unverified", "todo", "not yet", "as read on"]
BANNED_DOTTED = ["e.g.", "i.e.", "etc."]
BANNED_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in BANNED) + r")(?![\w-])"
    r"|\b(?:" + "|".join(re.escape(w) for w in BANNED_DOTTED) + r")",
    re.IGNORECASE)

#: Every rule this module checks: its number, what it catches, and whether
#: the build fails or only logs it. docs/developing/reference-page-rules.md
#: is generated from this table, and a test holds it to the checks below.
CHECKS = (
    ("1.1", "a page type that is not one of the five", "fails"),
    ("1.3", "front matter without type, owner, reader or review", "fails"),
    ("1.3", "a review date that is not YYYY-MM-DD", "fails"),
    ("10.1", "a banned word or phrase in prose", "fails"),
    ("10.2", "a title over the title limit", "fails"),
    ("10.3", "a title that another page has", "fails"),
    ("10.4", "a how-to step sentence over the sentence limit", "fails"),
    ("10.5", "a how-to with a fixed heading missing", "fails"),
    ("10.6", "a how-to with an extra heading or a sub-heading", "fails"),
    ("3.2", "how-to headings out of order", "fails"),
    ("10.8", "an image with no alt text", "fails"),
    ("5.10", "alt text over the alt text limit", "fails"),
    ("10.9", "an SVG drawing outside a light and dark pair", "fails"),
    ("10.10", "a toctree with more entries than the sidebar limit", "fails"),
    ("10.11", "more heading levels than the heading limit", "fails"),
    ("10.12", "a page other than a landing with two sentences or fewer", "fails"),
    ("10.19", "more links than the link limit", "logged"),
    ("10.20", "more callouts than the callout limit", "logged"),
    ("10.21", "a review date that has passed", "logged"),
)

TITLE_MAX = 65          # 5.1
SENTENCE_MAX = 25       # 5.2, checked on steps (10.4)
LINKS_MAX = 15          # 5.7, warns (10.19)
CALLOUTS_MAX = 2        # 5.5, warns (10.20)
HEADING_LEVELS_MAX = 5  # 5.8
SIDEBAR_MAX = 7         # 5.9
ALT_MAX = 150           # 5.10

# Text in these nodes is code, a URL or markup, not prose.
NOT_PROSE = (nodes.literal, nodes.literal_block, nodes.raw, nodes.comment,
             nodes.target, nodes.substitution_definition, addnodes.toctree)


def prose_text(node):
    """The node's text, without code and markup."""
    parts = []

    def walk(n):
        if isinstance(n, NOT_PROSE):
            return
        if isinstance(n, nodes.Text):
            parts.append(n.astext())
            return
        for child in n.children:
            walk(child)
    walk(node)
    return "".join(parts)


def own_words(paragraph):
    """The paragraph's prose words that are not the text of a link."""
    linked = sum(len(prose_text(r).split()) for r in paragraph.findall(nodes.reference))
    linked += sum(len(prose_text(r).split()) for r in paragraph.findall(addnodes.pending_xref))
    words = prose_text(paragraph).split()
    return words[linked:] if linked < len(words) else []


def sentences(text):
    text = re.sub(r"\s+", " ", text).strip()
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`\"(])", text) if s]


class Page:
    def __init__(self, app, docname, doctree):
        self.app, self.docname, self.doctree = app, docname, doctree
        self.pending = docname in app.config.vgacap_rules_pending
        self.failures = 0

    def fail(self, rule, message, node=None):
        self.failures += 1
        text = f"rule {rule}: {message}"
        location = node if node is not None and node.source else self.docname
        if self.pending:
            logger.info("pending page %s: %s", self.docname, text, location=location)
        else:
            logger.warning(text, location=location, type="vgacap", subtype="rules")

    def note(self, rule, message, node=None):
        """A rule that only warns on docs.fpgas.online: logged, not failed."""
        location = node if node is not None and node.source else self.docname
        logger.info("rule %s (warning only): %s", rule, message, location=location)


def top_sections(doctree):
    """The page's level-2 sections (the level-1 section is the title)."""
    title_sections = [s for s in doctree.children if isinstance(s, nodes.section)]
    if not title_sections:
        return []
    return [s for s in title_sections[0].children if isinstance(s, nodes.section)]


def section_title(section):
    return prose_text(section[0]).strip() if section.children else ""


def depth(node, level=0):
    deepest = level
    for s in node.children:
        if isinstance(s, nodes.section):
            deepest = max(deepest, depth(s, level + 1))
    return deepest


def check_front_matter(page, meta):
    missing = [key for key in FRONT_MATTER if not str(meta.get(key, "")).strip()]
    if missing:
        page.fail("1.3", "front matter is missing " + ", ".join(missing))
        return None
    if meta["type"] not in TYPES:
        page.fail("1.1", f"type {meta['type']!r} is not one of {', '.join(TYPES)}")
        return None
    try:
        review = datetime.date.fromisoformat(str(meta["review"]))
    except ValueError:
        page.fail("1.3", f"review {meta['review']!r} is not a YYYY-MM-DD date")
        return meta["type"]
    if review < page.app.config.vgacap_today:
        page.note("10.21", f"review date {review} has passed")
    return meta["type"]


def check_words(page):
    for para in page.doctree.findall(nodes.TextElement):
        if isinstance(para, NOT_PROSE) or isinstance(para.parent, nodes.TextElement):
            continue
        for match in BANNED_RE.finditer(prose_text(para)):
            page.fail("10.1", f"banned word or phrase {match[0]!r}", para)


def check_title(page):
    title = page.app.env.titles[page.docname].astext()
    if len(title) > TITLE_MAX:
        page.fail("10.2", f"title is {len(title)} characters, over {TITLE_MAX}: {title!r}")


def check_howto(page):
    sections = top_sections(page.doctree)
    headings = [section_title(s) for s in sections]
    if headings != HOWTO_HEADINGS:
        missing = [h for h in HOWTO_HEADINGS if h not in headings]
        extra = [h for h in headings if h not in HOWTO_HEADINGS]
        if missing:
            page.fail("10.5", "how-to is missing " + ", ".join(repr(h) for h in missing))
        if extra:
            page.fail("10.6", "how-to has extra headings " + ", ".join(repr(h) for h in extra))
        if not missing and not extra:
            page.fail("3.2", "how-to headings are out of order: " + " · ".join(headings))
    for section in sections:
        if any(isinstance(c, nodes.section) for c in section.children):
            page.fail("10.6", f"how-to heading {section_title(section)!r} has sub-headings",
                      section)
        if section_title(section) != "Steps":
            continue
        for item in section.findall(nodes.list_item):
            if not isinstance(item.parent, nodes.enumerated_list):
                continue
            for para in item.children:
                if not isinstance(para, nodes.paragraph):
                    continue
                for s in sentences(prose_text(para)):
                    if len(s.split()) > SENTENCE_MAX:
                        page.fail("10.4", f"step sentence of {len(s.split())} words, over "
                                  f"{SENTENCE_MAX}: {s[:60]!r}...", para)


def check_images(page):
    for image in page.doctree.findall(nodes.image):
        alt = image.get("alt", "").strip()
        if not alt:
            page.fail("10.8", f"image {image['uri']} has no alt text", image)
        elif len(alt) > ALT_MAX:
            page.fail("5.10", f"alt text of {image['uri']} is {len(alt)} characters, "
                      f"over {ALT_MAX}", image)
        # 8.13 / 10.9: a drawing (SVG) is shown as a theme-switched pair.
        classes = set(image.get("classes", []))
        if image["uri"].endswith(".svg") and not classes & {"only-light", "only-dark"}:
            page.fail("10.9", f"drawing {image['uri']} is not a light and dark pair "
                      "(write it as a <picture> element)", image)


def check_structure(page, page_type):
    levels = depth(page.doctree)
    if levels > HEADING_LEVELS_MAX:
        page.fail("10.11", f"{levels} heading levels, over {HEADING_LEVELS_MAX}")
    for toctree in page.doctree.findall(addnodes.toctree):
        entries = toctree.get("entries", [])
        if len(entries) > SIDEBAR_MAX:
            caption = toctree.get("caption") or "the sidebar group"
            page.fail("10.10", f"{caption} has {len(entries)} entries, over {SIDEBAR_MAX}",
                      toctree)
    if page_type == "landing":
        return
    # 1.6: more than links alone, and more than two sentences. A paragraph
    # counts when, without its link text, at least four words remain.
    told = [s for p in page.doctree.findall(nodes.paragraph)
            if len(own_words(p)) >= 4 for s in sentences(prose_text(p))]
    if len(told) <= 2:
        page.fail("10.12", "page has links and at most two sentences (1.6)")
    links = [r for r in page.doctree.findall(nodes.reference)
             if r.get("refuri") or r.get("refid") or r.get("internal")]
    links += list(page.doctree.findall(addnodes.pending_xref))
    if len(links) > LINKS_MAX:
        page.note("10.19", f"{len(links)} links, over {LINKS_MAX}")
    callouts = list(page.doctree.findall(nodes.Admonition))
    if len(callouts) > CALLOUTS_MAX:
        page.note("10.20", f"{len(callouts)} callouts, over {CALLOUTS_MAX}")


def on_doctree_read(app, doctree):
    docname = app.env.docname
    if docname in app.config.vgacap_rules_exempt:
        return
    page = Page(app, docname, doctree)
    meta = app.env.metadata.get(docname, {})
    page_type = check_front_matter(page, meta)
    check_title(page)
    check_words(page)
    check_images(page)
    check_structure(page, page_type)
    if page_type == "how-to":
        check_howto(page)
    app.env.vgacap_rule_failures = getattr(app.env, "vgacap_rule_failures", {})
    app.env.vgacap_rule_failures[docname] = page.failures


def on_env_purge_doc(app, env, docname):
    getattr(env, "vgacap_rule_failures", {}).pop(docname, None)


def on_env_merge_info(app, env, docnames, other):
    env.vgacap_rule_failures = getattr(env, "vgacap_rule_failures", {})
    env.vgacap_rule_failures.update(getattr(other, "vgacap_rule_failures", {}))


def on_check_consistency(app, env):
    seen = {}
    for docname in sorted(env.found_docs):
        if docname in app.config.vgacap_rules_exempt or docname not in env.titles:
            continue
        title = env.titles[docname].astext()
        if title in seen:
            message = f"rule 10.3: title {title!r} is also the title of {seen[title]}"
            if docname in app.config.vgacap_rules_pending:
                logger.info("pending page %s: %s", docname, message, location=docname)
            else:
                logger.warning(message, location=docname, type="vgacap", subtype="rules")
        seen.setdefault(title, docname)
    failures = getattr(env, "vgacap_rule_failures", {})
    for docname, reason in app.config.vgacap_rules_pending.items():
        if docname not in env.found_docs:
            logger.warning(f"vgacap_rules_pending names {docname}, which is not a page",
                           type="vgacap", subtype="rules")
        elif failures.get(docname) == 0:
            logger.warning(f"{docname} passes every rule: take it out of vgacap_rules_pending "
                           f"({reason})", location=docname, type="vgacap", subtype="rules")


def setup(app):
    app.add_config_value("vgacap_rules_pending", {}, "env")
    app.add_config_value("vgacap_rules_exempt", (), "env")
    app.add_config_value("vgacap_today", datetime.date.today(), "env")
    app.connect("doctree-read", on_doctree_read)
    app.connect("env-purge-doc", on_env_purge_doc)
    app.connect("env-merge-info", on_env_merge_info)
    app.connect("env-check-consistency", on_check_consistency)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
