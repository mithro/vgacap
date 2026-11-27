# SPDX-License-Identifier: Apache-2.0
"""Reference tables built from their source at build time (rule 4.4), so a
page cannot go stale against the code (rule 10.15).

    ```{vgacap-modes} horizontal
    ```

(or `vertical`) is one axis of the VGA mode table, read from src/frame/modes.c, the one table every
reader of a capture uses.

    ```{vgacap-argparse} vgacap.bin2stream:parser
    ```

is the options of a Python command, from the argparse parser that the
named function returns. The module is imported from python/ in this
repository, so it may use the standard library only: the docs build
installs nothing else.

Each directive records its source file as a dependency, so a change to
the source rebuilds the page. A source it cannot read is an error.
"""

import argparse
import importlib
import pathlib
import re
import sys

from docutils import nodes
from sphinx.util.docutils import SphinxDirective

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MODES_C = REPO / "src" / "frame" / "modes.c"
PYTHON = REPO / "python"

# One entry of `table[]` in modes.c: the name, then eleven integers.
ENTRY = re.compile(r'\{\s*"([^"]+)"\s*,((?:\s*\d+\s*,){10}\s*\d+)\s*\}')
FIELDS = ("h_active", "h_front", "h_sync", "h_back", "v_active", "v_front", "v_sync",
          "v_back", "h_sync_positive", "v_sync_positive", "extended")


class SourceError(Exception):
    pass


def read_modes(path=MODES_C):
    """The entries of modes.c's table, in order, as dicts."""
    text = path.read_text()
    start = text.find("table[] = {")
    end = text.find("};", start)
    if start < 0 or end < 0:
        raise SourceError(f"{path}: no `table[] = {{ ... }};`")
    body = text[start:end]
    rows = [line.strip() for line in body.splitlines()[1:]]
    entries = [line for line in rows if line.startswith("{")]
    modes = []
    for line in entries:
        m = ENTRY.fullmatch(line.rstrip(","))
        if not m:
            raise SourceError(f"{path}: cannot read the table entry {line!r}")
        values = [int(v) for v in m.group(2).split(",")]
        modes.append({"name": m.group(1), **dict(zip(FIELDS, values))})
    if not modes:
        raise SourceError(f"{path}: the table has no entries")
    return modes


def table(headers, rows):
    """A docutils table: a header row, then the rows, every cell text or a node."""
    tgroup = nodes.tgroup(cols=len(headers))
    for _ in headers:
        tgroup += nodes.colspec(colwidth=1)
    head = nodes.thead()
    tbody = nodes.tbody()

    def row(cells, parent):
        r = nodes.row()
        for cell in cells:
            entry = nodes.entry()
            para = nodes.paragraph()
            para += cell if isinstance(cell, nodes.Node) else nodes.Text(str(cell))
            entry += para
            r += entry
        parent += r

    row(headers, head)
    for cells in rows:
        row(cells, tbody)
    tgroup += head
    tgroup += tbody
    return nodes.table("", tgroup)


def code(text):
    return nodes.literal(text, text)


POLARITY = {1: "positive", 0: "negative"}


def polarity(mode, field):
    value = mode[field]
    if value not in POLARITY:
        raise SourceError(f"{mode['name']}: {field} is {value}, not 0 or 1")
    return POLARITY[value]


class ModesDirective(SphinxDirective):
    """`{vgacap-modes} horizontal` or `{vgacap-modes} vertical`: one axis of
    the table, so neither is too wide to read."""
    required_arguments = 1
    has_content = False

    def run(self):
        axis = self.arguments[0]
        if axis not in ("horizontal", "vertical"):
            raise self.error(f"{axis!r}: write horizontal or vertical")
        self.env.note_dependency(str(MODES_C))
        try:
            modes = read_modes()
            rows = []
            for m in modes:
                if axis == "horizontal":
                    total = m["h_active"] + m["h_front"] + m["h_sync"] + m["h_back"]
                    rows.append([code(m["name"]), m["h_active"], m["h_front"], m["h_sync"],
                                 m["h_back"], total, polarity(m, "h_sync_positive"),
                                 "no" if m["extended"] else "yes"])
                else:
                    total = m["v_active"] + m["v_front"] + m["v_sync"] + m["v_back"]
                    rows.append([code(m["name"]), m["v_active"], m["v_front"], m["v_sync"],
                                 m["v_back"], total, polarity(m, "v_sync_positive")])
        except (OSError, SourceError) as e:
            raise self.error(str(e))
        if axis == "horizontal":
            headers = ["Mode", "Active", "Front porch", "Sync", "Back porch", "Clocks a line",
                       "Hsync pulse", "Window match"]
        else:
            headers = ["Mode", "Active", "Front porch", "Sync", "Back porch", "Lines a frame",
                       "Vsync pulse"]
        return [table(headers, rows)]


def suppressed(value):
    """argparse.SUPPRESS is a string; compare by value, as argparse does."""
    return isinstance(value, str) and value == argparse.SUPPRESS


def help_text(ap, action):
    """The help as argparse prints it: %(default)s and the like filled in.
    A help whose % is not a placeholder is shown as written."""
    if not action.help or suppressed(action.help):
        return ""
    params = dict(vars(action), prog=ap.prog)
    for name in list(params):
        if suppressed(params[name]):
            del params[name]
    if params.get("choices") is not None:
        params["choices"] = ", ".join(str(c) for c in params["choices"])
    try:
        return action.help % params
    except (KeyError, ValueError, TypeError):
        return action.help


def value_text(action):
    """What follows the option: its metavar, one name for each value."""
    if action.nargs == 0:
        return ""
    metavar = action.metavar or action.dest.upper()
    names = metavar if isinstance(metavar, tuple) else (metavar,)
    if action.nargs == "?":
        return " ".join(f"[{n}]" for n in names)
    if action.nargs in ("*", "+"):
        return " ".join(f"{n} ..." for n in names)
    if isinstance(action.nargs, int) and len(names) == 1:
        return " ".join(names * action.nargs)
    return " ".join(names)


def default_text(action):
    if action.required:
        return "required"
    if not action.option_strings and action.nargs not in ("?", "*"):
        return "required"
    if action.nargs == 0 or action.default is None or suppressed(action.default):
        return ""
    return repr(action.default)


# More choices than this are one row: their range, not a row each.
CHOICES_MAX = 10


def choice_rows(action):
    """The Choice cells: one a choice, or one for a long run of them."""
    if not action.choices:
        return []
    choices = list(action.choices)
    if len(choices) <= CHOICES_MAX:
        return [str(c) for c in choices]
    if isinstance(action.choices, range) and action.choices.step == 1:
        return [f"{choices[0]} to {choices[-1]}"]
    return [f"{len(choices)} values, {choices[0]} to {choices[-1]}"]


def argparse_rows(ap, prefix=""):
    """One row an argument, then a row for each of its choices; then each
    subcommand, a row with its help and the rows of its own arguments."""
    rows, subs = [], []
    for action in ap._actions:
        if isinstance(action, argparse._HelpAction) or suppressed(action.help):
            continue
        if isinstance(action, argparse._SubParsersAction):
            helps = {a.dest: a.help or "" for a in action._choices_actions}
            subs.extend((name, sub, helps.get(name, "")) for name, sub in action.choices.items())
            continue
        name = ", ".join(action.option_strings) if action.option_strings else (
            action.metavar if isinstance(action.metavar, str) else action.dest)
        value = value_text(action) if action.option_strings else ""
        default = default_text(action)
        rows.append([code(prefix + name), code(value) if value else "",
                     code(default) if default else "", "", help_text(ap, action)])
        for choice in choice_rows(action):
            rows.append(["", "", "", code(choice), ""])
    for sub_name, sub, sub_help in subs:
        rows.append([code(prefix + sub_name), "", "", "", sub_help])
        rows.extend(argparse_rows(sub, prefix + sub_name + " "))
    return rows


class ArgparseDirective(SphinxDirective):
    required_arguments = 1
    has_content = False

    def run(self):
        target = self.arguments[0]
        module_name, _, func = target.partition(":")
        if not func:
            raise self.error(f"{target!r}: write it as module:function")
        if str(PYTHON) not in sys.path:
            sys.path.insert(0, str(PYTHON))
        try:
            module = importlib.import_module(module_name)
            ap = getattr(module, func)()
        except Exception as e:  # any failure to build the parser fails the page
            raise self.error(f"{target}: cannot build the parser: {e!r}")
        if not isinstance(ap, argparse.ArgumentParser):
            raise self.error(f"{target} did not return an argparse.ArgumentParser")
        self.env.note_dependency(module.__file__)
        headers = ["Argument", "Value", "Default", "Choice", "What it sets"]
        return [table(headers, argparse_rows(ap))]


def setup(app):
    app.add_directive("vgacap-modes", ModesDirective)
    app.add_directive("vgacap-argparse", ArgparseDirective)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
