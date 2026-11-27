# SPDX-License-Identifier: Apache-2.0
"""Reference tables for the Developing vgacap pages, made from their source.

Each directive reads the file that is the truth for its table when the page
is built, and records it as a dependency, so a change to the file rebuilds
the page and the table cannot go stale (docs.fpgas.online rules 4.4 and
10.15):

    ```{vgacap-ci-jobs}
    ```                     the jobs and steps of .github/workflows/ci.yml

    ```{vgacap-ci-env}
    ```                     its pinned commits and environment variables

    ```{vgacap-hil-options}
    ```                     tools/hil.py's command line, from build_parser()

    ```{vgacap-hil-checks}
    ```                     tools/hil.py's checks, in the order they run

    ```{vgacap-cmake-options}
    ```                     the option() lines of every CMakeLists.txt

    ```{vgacap-page-rules}
    ```                     the checks of docs/_ext/vgacap_rules.py

    ```{vgacap-sidebar-checks}
    ```                     the checks of docs/_ext/vgacap_landing.py

    ```{vgacap-page-limits}
    ```                     its limits, front matter, types and headings

    ```{vgacap-banned-words}
    ```                     its banned words and phrases
"""

import argparse
import importlib.util
import pathlib
import re
import sys

import yaml
from docutils import nodes
from sphinx.util.docutils import SphinxDirective

import vgacap_landing
import vgacap_rules

REPO = pathlib.Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"
HIL = REPO / "tools" / "hil.py"
RULES = pathlib.Path(vgacap_rules.__file__).resolve()
LANDING = pathlib.Path(vgacap_landing.__file__).resolve()


# ------------------------------------------------------------------ tables


def cell(content):
    """A table cell. `content` is a string (text), a node, or a list of them."""
    entry = nodes.entry()
    para = nodes.paragraph()
    for item in content if isinstance(content, list) else [content]:
        para += nodes.Text(item) if isinstance(item, str) else item
    entry += para
    return entry


def code(text):
    return nodes.literal(text, text)


def table(headers, rows, title=None):
    """A docutils table: a header row, then one row for each of `rows`."""
    tbl = nodes.table()
    if title is not None:
        tbl += nodes.title("", "", *(title if isinstance(title, list) else [nodes.Text(title)]))
    group = nodes.tgroup(cols=len(headers))
    tbl += group
    for _ in headers:
        group += nodes.colspec(colwidth=1)
    head = nodes.thead()
    row = nodes.row()
    for header in headers:
        row += cell(header)
    head += row
    group += head
    body = nodes.tbody()
    for values in rows:
        row = nodes.row()
        for value in values:
            row += cell(value)
        body += row
    group += body
    return tbl


class SourceDirective(SphinxDirective):
    """A directive whose table is made from `self.source`."""

    source = None

    def run(self):
        for path in self.sources():
            self.env.note_dependency(str(path))
        return self.make()

    def sources(self):
        return [self.source]


# --------------------------------------------------------------------- CI


def load_ci():
    with CI.open(encoding="utf-8") as fp:
        return yaml.safe_load(fp)


def step_text(step):
    """A step as the CI log names it: its name, else its action, else its
    command's first line."""
    if "name" in step:
        return [step["name"]]
    if "uses" in step:
        return [code(step["uses"])]
    lines = [line for line in str(step["run"]).splitlines() if line.strip()]
    first = lines[0].strip()
    return [code(first + (" ..." if len(lines) > 1 else ""))]


class CiJobs(SourceDirective):
    """One table for each job: its steps, in order."""

    source = CI

    def make(self):
        ci = load_ci()
        out = []
        for job_id, job in ci["jobs"].items():
            rows = [[str(n), step_text(step)] for n, step in enumerate(job["steps"], 1)]
            title = [code(job_id), nodes.Text(": %s, on %s" % (job["name"], job["runs-on"]))]
            out.append(table(["Step", "What it does"], rows, title=title))
        return out


class CiEnv(SourceDirective):
    """Every environment variable the workflow sets, and where."""

    source = CI

    def make(self):
        ci = load_ci()
        rows = []
        for name, value in (ci.get("env") or {}).items():
            rows.append([code(name), code(str(value)), "every job"])
        for job_id, job in ci["jobs"].items():
            for name, value in (job.get("env") or {}).items():
                rows.append([code(name), code(str(value)), [code(job_id), " job"]])
            for step in job["steps"]:
                for name, value in (step.get("env") or {}).items():
                    where = [code(job_id), " job, step "] + step_text(step)
                    rows.append([code(name), code(str(value)), where])
        return [triggers(ci), table(["Variable", "Value", "Set for"], rows)]


def triggers(ci):
    """A sentence naming the events the workflow runs on. `on` is a string,
    a list or a map of event to filters (and YAML 1.1 reads the key as
    True); "for every branch" is said only when no event has a filter."""
    on = ci.get("on", ci.get(True))
    if isinstance(on, str):
        events, filtered = [on], False
    elif isinstance(on, list):
        events, filtered = list(on), False
    else:
        events = list(on)
        filtered = any(value for value in on.values())
    intro = nodes.paragraph()
    intro += nodes.Text("The workflow runs on ")
    for i, event in enumerate(events):
        if i:
            intro += nodes.Text(" and " if i == len(events) - 1 else ", ")
        intro += code(event)
    intro += nodes.Text(" events" + ("; each event's filters are in the file." if filtered
                                     else ", for every branch."))
    return intro


# -------------------------------------------------------------------- HIL


def load_hil():
    """tools/hil.py as a module, read afresh. It is registered in sys.modules
    while it runs, as its dataclasses need."""
    spec = importlib.util.spec_from_file_location("vgacap_docs_hil", HIL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


def shown_default(value):
    """A default as a reader would type it: paths inside the checkout or
    next to it relative to the checkout, not to the machine that built
    the docs."""
    text = str(value)
    if text.startswith(str(REPO) + "/"):
        return text[len(str(REPO)) + 1:]
    if text.startswith(str(REPO.parent) + "/"):
        return "../" + text[len(str(REPO.parent)) + 1:]
    return text


def takes(action):
    if action.nargs == 0:
        return "nothing"
    if action.type is int:
        return "a whole number"
    if action.type is float:
        return "a number"
    return "text"


def help_text(parser, action):
    """The help as `--help` prints it, with %(default)s filled in."""
    if not action.help:
        return ""
    params = dict(vars(action), prog=parser.prog)
    params["default"] = shown_default(action.default)
    return action.help % params


def help_cell(parser, action):
    """The help with its quoted values, backticked words and options as code,
    so that no `--option` is set as a dash."""
    text = re.sub(r"'([^']*)'", r"`\1`", help_text(parser, action))
    parts = text.split("`")
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"(--[a-z][a-z-]*)", r"`\1`", parts[i])
    return inline_code("`".join(parts))


class HilOptions(SourceDirective):
    source = HIL

    def make(self):
        parser = load_hil().build_parser()
        rows = []
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            name = action.option_strings[0] if action.option_strings else action.dest
            if action.required or not action.option_strings:
                default = "required"
            elif action.nargs == 0:
                default = "off"
            elif action.default is None:
                default = "none"
            else:
                default = code(shown_default(action.default))
            rows.append([code(name), takes(action), default, help_cell(parser, action)])
        return [table(["Option", "Takes", "Default", "What it sets"], rows)]


#: What each check passes on. Keyed by hil.ALL_CHECKS, which the build holds
#: this to: a check added there and not here fails the build.
HIL_PASSES = {
    "probe": "`ttcap probe` exits 0 and the board answers",
    "capture": "a capture with samples and with no overruns or rxstall",
    "reconstruct": "`vgacap-frames` gives at least the frames the clock predicts, less two",
    "reference": "the middle frame is pixel-exact against the design's reference render",
    "frame": "two whole frames at the frame clock are pixel-exact with no stalls",
    "demo-frames": "`ttcap demo` exits 0 and writes PNGs at half the rate or more",
    "video": "the video's length and rate match the PNGs and `--fps`",
    "serve": "the MJPEG stream delivers two parts or more",
    "stop": "SIGTERM stops the demo within 15 s with exit 0 and a video with a length and no process left",
    "board-usable": "the board still answers `ttcap probe` after the other checks",
}

#: The checks that compare with a reference render.
HIL_REFERENCE = ("reference", "frame")


def inline_code(text):
    """`text` with its backticked parts as code."""
    parts = re.split(r"`([^`]*)`", text)
    return [code(p) if i % 2 else p for i, p in enumerate(parts) if p]


class HilChecks(SourceDirective):
    source = HIL

    def make(self):
        hil = load_hil()
        missing = [c for c in hil.ALL_CHECKS if c not in HIL_PASSES]
        extra = [c for c in HIL_PASSES if c not in hil.ALL_CHECKS]
        if missing or extra:
            raise self.error("vgacap-hil-checks: tools/hil.py's checks are %s; "
                             "docs/_ext/vgacap_developing.py's HIL_PASSES has no row for %s "
                             "and a row for no check %s" % (", ".join(hil.ALL_CHECKS),
                                                            missing, extra))
        rows = []
        for name in hil.ALL_CHECKS:
            rows.append([code(name), inline_code(HIL_PASSES[name]),
                         "yes" if name in hil.PIPELINE_CHECKS else "no",
                         "yes" if name in HIL_REFERENCE else "no"])
        return [table(["Check", "Passes when", "Needs GStreamer", "Needs a reference render"],
                      rows)]


# ------------------------------------------------------------------ CMake

OPTION_RE = re.compile(r'^\s*option\(\s*(\w+)\s+"([^"]*)"\s+(\w+)\s*\)', re.MULTILINE)


def cmake_files():
    """Every CMakeLists.txt of vgacap's own, outside build trees, checkouts
    and the vendored libraries; judged on the path inside the repository."""
    skip = {"third_party", ".worktrees", "tmp", ".venv"}
    out = []
    for path in REPO.glob("**/CMakeLists.txt"):
        parts = path.relative_to(REPO).parts[:-1]
        if skip & set(parts) or any(part.startswith("build") for part in parts):
            continue
        out.append(path)
    return sorted(out)


class CmakeOptions(SourceDirective):
    def sources(self):
        return cmake_files()

    def make(self):
        rows = []
        for path in cmake_files():
            rel = path.relative_to(REPO).as_posix()
            for name, text, default in OPTION_RE.findall(path.read_text(encoding="utf-8")):
                text = re.sub(r"\((-[^)\s]+)\)", r"(`\1`)", text)
                rows.append([code(name), code(default), inline_code(text), code(rel)])
        return [table(["Option", "Default", "What it does", "Set in"], rows)]


# ------------------------------------------------------------------ rules


class PageRules(SourceDirective):
    source = RULES

    def make(self):
        rows = [["a page" if rule else "the pending list", code(rule) if rule else "none",
                 what, code(message), result]
                for rule, message, what, result in vgacap_rules.CHECKS]
        return [table(["Checks", "Rule", "What the build catches", "Message", "Result"], rows)]


class SidebarChecks(SourceDirective):
    source = LANDING

    def make(self):
        rows = [[what, code(message)] for message, what in vgacap_landing.CHECKS]
        return [table(["What the build catches", "Message"], rows)]


class PageLimits(SourceDirective):
    source = RULES

    def make(self):
        r = vgacap_rules
        rows = [
            ["title limit", str(r.TITLE_MAX), "characters"],
            ["sentence limit", str(r.SENTENCE_MAX), "words in a how-to step"],
            ["link limit", str(r.LINKS_MAX), "links on a page"],
            ["callout limit", str(r.CALLOUTS_MAX), "callouts on a page"],
            ["heading limit", str(r.HEADING_LEVELS_MAX), "heading levels"],
            ["sidebar limit", str(r.SIDEBAR_MAX), "entries in one toctree"],
            ["alt text limit", str(r.ALT_MAX), "characters"],
        ]
        lists = [
            ["page types", [*join_code(r.TYPES)]],
            ["front matter keys", [*join_code(r.FRONT_MATTER)]],
            ["how-to headings", [*join_code(r.HOWTO_HEADINGS)]],
        ]
        return [table(["Limit", "Value", "Counted in"], rows),
                table(["Fixed set", "Members, in order"], lists)]


def join_code(items):
    out = []
    for i, item in enumerate(items):
        if i:
            out.append(" · ")
        out.append(code(item))
    return out


class BannedWords(SourceDirective):
    source = RULES

    def make(self):
        words = vgacap_rules.BANNED_WORDS + vgacap_rules.BANNED_DOTTED
        para = nodes.paragraph()
        for node in join_code(words):
            para += nodes.Text(node) if isinstance(node, str) else node
        return [para]


def setup(app):
    app.add_directive("vgacap-ci-jobs", CiJobs)
    app.add_directive("vgacap-ci-env", CiEnv)
    app.add_directive("vgacap-hil-options", HilOptions)
    app.add_directive("vgacap-hil-checks", HilChecks)
    app.add_directive("vgacap-cmake-options", CmakeOptions)
    app.add_directive("vgacap-page-rules", PageRules)
    app.add_directive("vgacap-sidebar-checks", SidebarChecks)
    app.add_directive("vgacap-page-limits", PageLimits)
    app.add_directive("vgacap-banned-words", BannedWords)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
