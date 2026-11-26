# SPDX-License-Identifier: Apache-2.0
"""The docs.fpgas.online page rules that a machine can decide.

Rule numbers are those of docs.fpgas.online's "What a page contains"
(https://github.com/fpgas-online/fpgas.online-docs/blob/main/docs/developer/about-these-docs/page-contents.md).
Section 10 there lists what its build checks; each check here cites the
section 10 rule, or the rule it enforces when section 10 has none. A
failing rule is a warning of type `vgacap.rules`, so the `-W` build fails
on it. A rule that only warns there is logged at info level here.

`vgacap_rules_pending` (in conf.py) maps a page to the reason its rules
are not enforced yet. Its failures are logged at info level, and a
pending page that passes every rule is an error, so the list only shrinks.

Sphinx reports a page's warnings when it reads the page, so an incremental
build is silent about a failing page it did not re-read. CI builds afresh.
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

# 6.1: whole words or phrases, any case. A phrase matches across a line
# break; a hyphenated compound ("easy-to-use") counts. `e.g.` and `i.e.` end
# in a full stop, so they need no closing boundary; `etc` matches with or
# without its stop.
BANNED_WORDS = ["just", "simply", "easy", "easily", "straightforward", "please note",
                "click here", "currently", "soon", "in order to", "as of this writing",
                "unverified", "todo", "not yet", "as read on", "etc"]
BANNED_DOTTED = ["e.g.", "i.e."]


def phrase(words):
    return r"\s+".join(re.escape(w) for w in words.split())


# Not after "/": a path such as /etc/hosts is not the word.
BANNED_RE = re.compile(
    r"(?<!/)\b(?:" + "|".join(phrase(w) for w in BANNED_WORDS) + r")(?!\w)"
    r"|\b(?:" + "|".join(re.escape(w) for w in BANNED_DOTTED) + r")",
    re.IGNORECASE)
# 6.3: labels, so matched as written at the start of a paragraph or line.
BANNED_LABELS = re.compile(r"(?:^|\n)\s*(Future|Sources):", re.MULTILINE)

#: Every check this module makes: its rule number (None for a check of the
#: pending list, which no page rule covers), the start of the message
#: it prints (… for a part that varies), what it catches, and whether the
#: build fails or only logs it. docs/developing/site/reference-page-rules.md
#: is made from this table, and docs/tests/test_vgacap_developing.py holds
#: it to the messages in the code, one row for each.
CHECKS = (
    ("10.14", "front matter is missing …", "front matter without type, owner, reader or review", "fails"),
    ("10.14", "type … is not one of … (1.1)", "a page type that is not one of the five", "fails"),
    ("10.14", "review … is not a YYYY-MM-DD date", "a review date written another way", "fails"),
    ("10.14", "review … is not a date", "a review date that is no date", "fails"),
    ("10.21", "review date … has passed", "a review date that has passed", "logged"),
    ("10.1", "banned word or phrase … (6.1)", "a banned word or phrase in prose", "fails"),
    ("10.1", "forbidden label … (6.3)", "a paragraph or line that starts with Future: or Sources:", "fails"),
    ("10.2", "title is … characters, over …: …", "a title over the title limit", "fails"),
    ("10.3", "title … is also the title of …", "a title that another page has, on every page with it", "fails"),
    ("3.2", "how-to headings are out of order: …", "how-to headings out of order", "fails"),
    ("10.4", "step sentence of … words, over …: …", "a how-to step sentence over the sentence limit", "fails"),
    ("10.5", "how-to is missing …", "a how-to with a fixed heading missing", "fails"),
    ("10.6", "how-to has extra headings …", "a how-to with an extra heading", "fails"),
    ("10.6", "how-to heading … has sub-headings", "a how-to heading with a sub-heading", "fails"),
    ("10.8", "image … has no alt text", "an image with no alt text", "fails"),
    ("10.8", "alt text of … is … characters, over … (5.10)", "alt text over the alt text limit", "fails"),
    ("10.8", "an image in raw HTML is not checked or copied: …", "an image in raw HTML other than a picture element after a blank line", "fails"),
    ("10.9", "drawing … is not a light and dark pair …", "an SVG drawing outside a light and dark pair", "fails"),
    ("10.9", "… is shown only in the … theme", "a theme-switched image without its twin", "fails"),
    ("10.9", "… and its dark twin are both shown in every theme", "a drawing and its dark twin both shown unswitched", "fails"),
    ("10.10", "… sidebar entries under this page, over …", "more sidebar entries under one page than the sidebar limit", "fails"),
    ("10.11", "… heading levels, over …", "more heading levels than the heading limit", "fails"),
    ("10.12", "the page is links alone", "a page other than a landing that is links alone", "fails"),
    ("1.6", "the page holds … sentences or facts; more than two are needed", "a page other than a landing with two sentences or facts or fewer", "fails"),
    ("10.19", "… links, over …", "more links than the link limit", "logged"),
    ("10.20", "… callouts, over …", "more callouts than the callout limit", "logged"),
    (None, "vgacap_rules_pending names …, which is not a page", "a pending list entry that is not a page", "fails"),
    (None, "… passes every rule: take it out of vgacap_rules_pending (…)", "a page on the pending list that passes every rule", "fails"),
)

TITLE_MAX = 65          # 5.1
SENTENCE_MAX = 25       # 5.2, checked on steps (10.4)
LINKS_MAX = 15          # 5.7, warns (10.19)
CALLOUTS_MAX = 2        # 5.5, warns (10.20)
HEADING_LEVELS_MAX = 5  # 5.8
SIDEBAR_MAX = 7         # 5.9
ALT_MAX = 150           # 5.10

# Text in these nodes is code or markup, not prose.
CODE = (nodes.literal, nodes.literal_block)
NOT_PROSE = CODE + (nodes.raw, nodes.comment, nodes.target, nodes.substitution_definition,
                    addnodes.toctree)
URI_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def is_bare_url(node):
    """A link whose text is its address, as linkify makes it."""
    return isinstance(node, nodes.reference) and node.get("refuri") and \
        node.astext().strip() in (node["refuri"], node["refuri"].removeprefix("mailto:"))


def prose_text(node, code="skip"):
    """The node's text without markup or bare URLs. Code is left out, or,
    with code="word", counted as one word, "Code", capitalised so that a
    sentence starting with code still begins a new sentence."""
    parts = []

    def walk(n):
        if isinstance(n, CODE):
            if code == "word":
                parts.append(" Code ")
            return
        if isinstance(n, NOT_PROSE) or is_bare_url(n):
            return
        if isinstance(n, nodes.Text):
            parts.append(n.astext())
            return
        for child in n.children:
            walk(child)
    walk(node)
    return "".join(parts)


# A sentence ends at . ! or ?, then any closing quotes or brackets, then
# space and a capital, a digit, an opening quote or bracket, or code.
SENTENCE_END = re.compile(r"([.!?][\"'”’)\]]*)\s+(?=[A-Z0-9\"'“‘(`])")


def sentences(text):
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    pieces = SENTENCE_END.split(text)
    # split keeps each ending as its own item: join it back to its sentence
    out = [pieces[i] + (pieces[i + 1] if i + 1 < len(pieces) else "")
           for i in range(0, len(pieces), 2)]
    return [s.strip() for s in out if s.strip()]


def own_words(paragraph):
    """How many of the paragraph's words are not the text of a link."""
    linked = sum(len(prose_text(r).split()) for r in paragraph.findall(nodes.reference))
    linked += sum(len(prose_text(r).split()) for r in paragraph.findall(addnodes.pending_xref))
    return max(0, len(prose_text(paragraph).split()) - linked)


class Page:
    def __init__(self, app, docname, doctree):
        self.app, self.docname, self.doctree = app, docname, doctree
        self.pending = docname in app.config.vgacap_rules_pending
        self.failures = 0

    def fail(self, rule, message, node=None):
        self.failures += 1
        report(self.app, self.docname, f"rule {rule}: {message}", node)

    def note(self, rule, message, node=None):
        """A rule that only warns on docs.fpgas.online: logged, not failed."""
        location = node if node is not None and node.source else self.docname
        logger.info("rule %s (warning only): %s", rule, message, location=location)


def report(app, docname, text, node=None):
    location = node if node is not None and node.source else docname
    if docname in app.config.vgacap_rules_pending:
        logger.info("pending page %s: %s", docname, text, location=location)
    else:
        logger.warning(text, location=location, type="vgacap", subtype="rules")


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
        page.fail("10.14", "front matter is missing " + ", ".join(missing))
        return None
    if meta["type"] not in TYPES:
        page.fail("10.14", f"type {meta['type']!r} is not one of {', '.join(TYPES)} (1.1)")
        return None
    review = str(meta["review"])
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", review):
        page.fail("10.14", f"review {review!r} is not a YYYY-MM-DD date")
        return meta["type"]
    try:
        date = datetime.date.fromisoformat(review)
    except ValueError:
        page.fail("10.14", f"review {review!r} is not a date")
        return meta["type"]
    if date < page.app.config.vgacap_today:
        page.note("10.21", f"review date {date} has passed")
    return meta["type"]


def check_words(page):
    for para in page.doctree.findall(nodes.TextElement):
        if isinstance(para, NOT_PROSE) or isinstance(para.parent, nodes.TextElement):
            continue
        text = prose_text(para)
        for match in BANNED_RE.finditer(text):
            found = re.sub(r"\s+", " ", match[0])
            page.fail("10.1", f"banned word or phrase {found!r} (6.1)", para)
        for match in BANNED_LABELS.finditer(text):
            page.fail("10.1", f"forbidden label {match[1] + ':'!r} (6.3)", para)


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
        for para in section.findall(nodes.paragraph):
            if not in_numbered_step(para):
                continue
            for s in sentences(prose_text(para, code="word")):
                if len(s.split()) > SENTENCE_MAX:
                    page.fail("10.4", f"step sentence of {len(s.split())} words, over "
                              f"{SENTENCE_MAX}: {s[:60]!r}...", para)


def in_numbered_step(node):
    """Whether the node sits in an item of a numbered list."""
    while node is not None:
        if isinstance(node, nodes.list_item) and isinstance(node.parent, nodes.enumerated_list):
            return True
        node = node.parent
    return False


def is_remote(uri):
    return bool(URI_SCHEME.match(uri)) or uri.startswith("//")


def theme(image):
    classes = set(image.get("classes", []))
    return "light" if "only-light" in classes else "dark" if "only-dark" in classes else None


def check_images(page):
    images = list(page.doctree.findall(nodes.image))
    for image in images:
        alt = image.get("alt", "").strip()
        if not alt:
            page.fail("10.8", f"image {image['uri']} has no alt text", image)
        elif len(alt) > ALT_MAX:
            page.fail("10.8", f"alt text of {image['uri']} is {len(alt)} characters, "
                      f"over {ALT_MAX} (5.10)", image)
    # 8.13 / 10.9: a drawing (a local SVG) is shown as a theme-switched pair,
    # and a switched image is always one of a light and dark pair.
    for i, image in enumerate(images):
        uri, mode = image["uri"], theme(image)
        if mode is None:
            if uri.lower().endswith(".svg") and not is_remote(uri):
                page.fail("10.9", f"drawing {uri} is not a light and dark pair "
                          "(write it as a <picture> element)", image)
            continue
        j = i + 1 if mode == "light" else i - 1
        partner = images[j] if 0 <= j < len(images) else None
        want = "dark" if mode == "light" else "light"
        if partner is None or theme(partner) != want:
            page.fail("10.9", f"{uri} is shown only in the {mode} theme", image)
    shown = {image["uri"] for image in images if theme(image) is None}
    for uri in shown:
        stem, dot, ext = uri.rpartition(".")
        if dot and f"{stem}-dark.{ext}" in shown:
            page.fail("10.9", f"{uri} and its dark twin are both shown in every theme")


def check_raw_html(page):
    """An <img> or <picture> MyST left as raw HTML ships unchecked and
    without its file: on the site it is a broken image."""
    for raw in page.doctree.findall(nodes.raw):
        text = re.sub(r"<!--.*?-->", "", raw.astext(), flags=re.S).lower()
        if raw.get("format") == "html" and ("<img" in text or "<picture" in text):
            page.fail("10.8", "an image in raw HTML is not checked or copied: write a "
                      "standalone <img> or a <picture> element after a blank line, "
                      "not inside other HTML", raw)


def check_structure(page, page_type):
    levels = depth(page.doctree)
    if levels > HEADING_LEVELS_MAX:
        page.fail("10.11", f"{levels} heading levels, over {HEADING_LEVELS_MAX}")
    # 10.10: the sidebar heading this page makes holds all its toctree entries.
    entries = sum(len(t.get("entries", [])) for t in page.doctree.findall(addnodes.toctree))
    if entries > SIDEBAR_MAX:
        page.fail("10.10", f"{entries} sidebar entries under this page, over {SIDEBAR_MAX}")
    if page_type == "landing":
        return
    paragraphs = list(page.doctree.findall(nodes.paragraph))
    if not any(own_words(p) >= 3 for p in paragraphs):
        page.fail("10.12", "the page is links alone")
    # 1.6: more than two sentences. A table row or a list item is one fact.
    told = sum(len(sentences(prose_text(p))) for p in paragraphs
               if not isinstance(p.parent, (nodes.entry, nodes.list_item)))
    told += sum(1 for _ in page.doctree.findall(nodes.row)
                if not isinstance(_.parent, nodes.thead))
    told += sum(1 for _ in page.doctree.findall(nodes.list_item))
    if told <= 2:
        page.fail("1.6", f"the page holds {told} sentences or facts; more than two are needed")
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
    page = Page(app, docname, doctree)
    meta = app.env.metadata.get(docname, {})
    page_type = check_front_matter(page, meta)
    check_title(page)
    check_words(page)
    check_images(page)
    check_raw_html(page)
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
    failures = dict(getattr(env, "vgacap_rule_failures", {}))
    by_title = {}
    for docname in sorted(env.found_docs):
        if docname in env.titles:
            by_title.setdefault(env.titles[docname].astext(), []).append(docname)
    for title, docnames in by_title.items():
        if len(docnames) < 2:
            continue
        for docname in docnames:
            others = ", ".join(d for d in docnames if d != docname)
            report(app, docname, f"rule 10.3: title {title!r} is also the title of {others}")
            failures[docname] = failures.get(docname, 0) + 1
    for docname, reason in app.config.vgacap_rules_pending.items():
        if docname not in env.found_docs:
            logger.warning(f"vgacap_rules_pending names {docname}, which is not a page",
                           type="vgacap", subtype="rules")
        elif failures.get(docname) == 0:
            logger.warning(f"{docname} passes every rule: take it out of vgacap_rules_pending "
                           f"({reason})", location=docname, type="vgacap", subtype="rules")


def setup(app):
    app.add_config_value("vgacap_rules_pending", {}, "env")
    # "" (no rebuild): a new day does not re-read every page.
    app.add_config_value("vgacap_today", datetime.date.today(), "")
    app.connect("doctree-read", on_doctree_read)
    app.connect("env-purge-doc", on_env_purge_doc)
    app.connect("env-merge-info", on_env_merge_info)
    app.connect("env-check-consistency", on_check_consistency)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
