# SPDX-License-Identifier: Apache-2.0
"""Reference tables built from their source at build time (rule 4.4), so a
page cannot go stale against the code (rule 10.15).

    ```{vgacap-modes}
    ```

is the VGA mode table, read from src/frame/modes.c, the one table every
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


class ModesDirective(SphinxDirective):
    has_content = False

    def run(self):
        self.env.note_dependency(str(MODES_C))
        try:
            modes = read_modes()
        except (OSError, SourceError) as e:
            raise self.error(str(e))
        polarity = {1: "positive", 0: "negative"}
        rows = []
        for m in modes:
            cpl = m["h_active"] + m["h_front"] + m["h_sync"] + m["h_back"]
            lpf = m["v_active"] + m["v_front"] + m["v_sync"] + m["v_back"]
            rows.append([code(m["name"]), m["h_active"], m["h_front"], m["h_sync"], m["h_back"],
                         cpl, polarity[m["h_sync_positive"]], m["v_active"], m["v_front"],
                         m["v_sync"], m["v_back"], lpf, polarity[m["v_sync_positive"]],
                         "no" if m["extended"] else "yes"])
        headers = ["Mode", "H active", "H front", "H sync", "H back", "Clocks a line",
                   "Hsync", "V active", "V front", "V sync", "V back", "Lines a frame",
                   "Vsync", "Window match"]
        return [table(headers, rows)]


def argparse_rows(ap):
    rows = []
    for action in ap._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if action.option_strings:
            name = ", ".join(action.option_strings)
            value = "" if action.nargs == 0 else (action.metavar or action.dest.upper())
            default = "" if action.default in (None, argparse.SUPPRESS) else repr(action.default)
            if action.nargs == 0:
                default = ""
        else:
            name, value, default = action.dest, "", "required"
        choices = ", ".join(str(c) for c in action.choices) if action.choices else ""
        rows.append([code(name), code(value) if value else "", code(default) if default else "",
                     choices, action.help or ""])
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
        headers = ["Argument", "Value", "Default", "Choices", "What it sets"]
        return [table(headers, argparse_rows(ap))]


def setup(app):
    app.add_directive("vgacap-modes", ModesDirective)
    app.add_directive("vgacap-argparse", ArgparseDirective)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
