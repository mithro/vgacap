# SPDX-License-Identifier: Apache-2.0
"""Reference tables for `ttcap`, generated from its own source at build time.

Three directives, each reading the data a page would otherwise copy by
hand, so the page cannot drift from the code (rule 4.4 of docs.fpgas.online's
"What a page contains"):

- ``ttcap-commands``: every subcommand of `ttcap.cli.build_parser()`, as a
  section each, with its positional arguments and options as a definition
  list: the invocation, then the help text, the default, the choices, and
  whether it is required or excludes another option.
- ``ttcap-gpio-map pins`` and ``ttcap-gpio-map window``: the demo board
  pins each `ttcap.boards` profile samples, and the VGA signal on each,
  from the profile's `signal_map`; or the window of pins each samples.
- ``ttcap-welland-boards``: the `ttcap.boards.WELLAND` table: slug, Pi,
  bridge address, tunnel port and chip.

Help text is not handed to docutils as plain text: its smart quotes would
turn `--max-bytes` into an en dash and `'-'` into curly quotes, and a
copied option would then not work. So options, backtick spans and quoted
words become literals, and a Markdown file name that is a page becomes a
link to it.

Each page that uses one is rebuilt when ttcap's source changes.
"""

import argparse
import pathlib
import re
import sys

from docutils import nodes
from sphinx import addnodes
from sphinx.util.docutils import SphinxDirective

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
PYTHON = REPO / "python"
SOURCES = sorted((PYTHON / "ttcap").glob("*.py")) + [PYTHON / "vgacap" / "modes.py"]

#: `enum vgacap_signal` in include/vgacap/stream.h: the order of
#: `BoardProfile.signal_map`.
SIGNALS = ("hsync", "vsync", "R1", "R0", "G1", "G0", "B1", "B0")

#: The pieces of help text that are code, not prose: a backtick span, a
#: quoted word, an option, a Markdown file.
CODE = re.compile(r"`([^`]+)`|'([^'\s]+)'|(--[\w-]+)|((?:[\w.-]+/)+[\w.-]+\.md)\b")


def ttcap():
    """The `ttcap` package, imported from this checkout."""
    if str(PYTHON) not in sys.path:
        sys.path.insert(0, str(PYTHON))
    import ttcap.boards
    import ttcap.cli
    import ttcap.demo
    return ttcap


def para(*children):
    return nodes.paragraph("", "", *children)


def literal(text):
    return nodes.literal(text, text)


def page_link(path):
    """A link to the page `path` (a Markdown file from the repository root)."""
    ref = addnodes.pending_xref("", refdomain="std", reftype="doc",
                                reftarget="/" + path[:-len(".md")], refexplicit=True)
    ref += literal(path)
    return ref


def prose(text):
    """Plain help text; its " -- " is a dash, written as one so it is not
    left to smart quotes, which would make an en dash of it."""
    return nodes.Text(text.replace(" -- ", " \u2014 "))


def code_text(text):
    """`text` as nodes: prose as text, code as literals, page names as links."""
    out, at = [], 0
    for match in CODE.finditer(text):
        if match.start() > at:
            out.append(prose(text[at:match.start()]))
        tick, quoted, option, page = match.groups()
        if page and (REPO / page).is_file():
            out.append(page_link(page))
        else:
            out.append(literal(tick or quoted or option or page))
        at = match.end()
    if at < len(text):
        out.append(prose(text[at:]))
    return out


def table(headings, rows):
    """A docutils table: one header row, then `rows` of cells (nodes or text)."""
    group = nodes.tgroup(cols=len(headings))
    for _ in headings:
        group += nodes.colspec(colwidth=1)

    def row(cells):
        r = nodes.row()
        for cell in cells:
            entry = nodes.entry()
            entry += para(cell if isinstance(cell, nodes.Node) else nodes.Text(str(cell)))
            r += entry
        return r

    head = nodes.thead()
    head += row(headings)
    body = nodes.tbody()
    for cells in rows:
        body += row(cells)
    group += head
    group += body
    return nodes.table("", group)


def invocation(action):
    """`--clock-hz CLOCK_HZ`, `--to {flash,bootsel}`, `link`."""
    formatter = argparse.HelpFormatter("ttcap")
    if not action.option_strings:
        return formatter._metavar_formatter(action, action.dest)(1)[0]
    if action.nargs == 0:
        return ", ".join(action.option_strings)
    metavar = formatter._format_args(action, action.dest.upper())
    return ", ".join(f"{o} {metavar}" for o in action.option_strings)


def exclusive_with(parser, action):
    others = []
    for group in parser._mutually_exclusive_groups:
        if action in group._group_actions:
            others += [a for a in group._group_actions if a is not action]
    return [a.option_strings[0] for a in others]


def joined(lead, items):
    cells = [nodes.Text(lead)]
    for i, item in enumerate(items):
        cells += ([nodes.Text(", ")] if i else []) + [literal(str(item))]
    return para(*cells, nodes.Text("."))


def describe(parser, action):
    """The definition of one argument: help, then the facts argparse holds."""
    formatter = argparse.HelpFormatter("ttcap")
    out = []
    help_text = ""
    if action.help and action.help is not argparse.SUPPRESS:
        help_text = formatter._expand_help(action)
        out.append(para(*code_text(help_text)))
    if action.required and action.option_strings:
        out.append(para(nodes.Text("Required.")))
    if action.choices is not None:
        out.append(joined("Choices: ", action.choices))
    default = action.default
    if (action.option_strings and action.nargs != 0 and default not in (None, "")
            and default is not argparse.SUPPRESS and "default" not in help_text.lower()):
        out.append(joined("Default: ", [default]))
    others = exclusive_with(parser, action)
    if others:
        out.append(joined("Not with ", others))
    return out


class Commands(SphinxDirective):
    """``ttcap-commands``: one section for each subcommand."""

    def run(self):
        for source in SOURCES:
            self.env.note_dependency(str(source))
        try:
            parser = ttcap().cli.build_parser()
        except Exception as exc:  # noqa: BLE001 - reported as a build error
            raise self.error(f"cannot build ttcap's parser: {type(exc).__name__}: {exc}")
        sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        helps = {a.dest: a.help for a in sub._choices_actions}
        names = {}
        for name, subparser in sub.choices.items():   # an alias maps to its parser again
            names.setdefault(id(subparser), (name, subparser, []))[2].append(name)
        sections = []
        for name, subparser, aliases in names.values():
            title = f"ttcap {name}"
            section = nodes.section(ids=[nodes.make_id(title)],
                                    names=[nodes.fully_normalize_name(title)])
            section += nodes.title(title, "", literal(title))
            if helps.get(name):
                text = helps[name][0].upper() + helps[name][1:] + "."
                section += para(*code_text(text))
            if aliases[1:]:
                section += joined("Also called ", aliases[1:])
            if subparser.description:
                section += para(*code_text(subparser.description))
            usage = subparser.format_usage().strip()
            block = nodes.literal_block(usage, usage)
            block["language"] = "text"
            section += block
            items = nodes.definition_list()
            for action in subparser._actions:
                if isinstance(action, argparse._HelpAction):
                    continue
                item = nodes.definition_list_item()
                item += nodes.term("", "", literal(invocation(action)))
                definition = nodes.definition()
                for child in describe(subparser, action):
                    definition += child
                item += definition
                items += item
            section += items
            if subparser.epilog:
                section += para(*code_text(subparser.epilog))
            self.state.document.note_implicit_target(section)
            sections.append(section)
        return sections


def uo_signals(profile):
    """(uo_out index, GPIO, sample bit, VGA signal) for each uo_out pin."""
    rows = []
    for index, gpio in enumerate(profile.uo_gpios):
        bit = gpio - profile.in_base
        signal = SIGNALS[profile.signal_map.index(bit)]
        rows.append((index, gpio, bit, signal))
    return rows


class GpioMap(SphinxDirective):
    """``ttcap-gpio-map pins`` or ``ttcap-gpio-map window``: the pins of the
    two profiles side by side, or the window of pins each one samples."""

    required_arguments = 1

    def run(self):
        if self.arguments[0] not in ("pins", "window"):
            raise self.error("ttcap-gpio-map takes pins or window")
        self.env.note_dependency(str(PYTHON / "ttcap" / "boards.py"))
        boards = ttcap().boards
        profiles = (boards.RP2040_TT06, boards.RP2350_DBV3)
        names = [p.name for p in profiles]
        if self.arguments[0] == "window":
            return [table(
                ["Profile", "Chip", "First sampled GPIO", "Bits sampled", "Samples per word",
                 "PIO GPIO base"],
                [[literal(p.name), p.chip.upper(), f"GPIO {p.in_base}", p.in_count,
                  p.samples_per_word, p.pio_gpio_base] for p in profiles])]
        try:
            per = [uo_signals(p) for p in profiles]
        except ValueError as exc:
            raise self.error(f"a profile's signal_map does not cover its uo_out pins: {exc}")
        pins = [[literal("project clock"), "--"]
                + [f"GPIO {p.clk_gpio}" for p in profiles] + ["--"] * len(profiles)]
        for index in range(8):
            signal = per[0][index][3]
            if any(rows[index][3] != signal for rows in per):
                raise self.error(f"the profiles put different VGA signals on uo_out[{index}]")
            pins.append([literal(f"uo_out[{index}]"), signal]
                        + [f"GPIO {rows[index][1]}" for rows in per]
                        + [str(rows[index][2]) for rows in per])
        return [table(["Signal", "VGA signal"] + [f"{n} GPIO" for n in names]
                      + [f"{n} sample bit" for n in names], pins)]


class WellandBoards(SphinxDirective):
    """``ttcap-welland-boards``: `boards.WELLAND`, one row a slug."""

    def run(self):
        for source in ("boards.py", "demo.py"):
            self.env.note_dependency(str(PYTHON / "ttcap" / source))
        pkg = ttcap()
        boards, demo = pkg.boards, pkg.demo
        rows = []
        for slug in sorted(boards.WELLAND):
            pi, profile = boards.WELLAND[slug]
            rows.append([literal(slug), literal(pi), literal(boards.bridge_ws_url(slug)),
                         str(demo.tunnel_port(slug)), profile.chip.upper()])
        return [table(["Slug", "Pi", "Bridge", "Tunnel port", "Chip"], rows)]


def setup(app):
    app.add_directive("ttcap-commands", Commands)
    app.add_directive("ttcap-gpio-map", GpioMap)
    app.add_directive("ttcap-welland-boards", WellandBoards)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
