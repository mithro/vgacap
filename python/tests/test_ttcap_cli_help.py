# SPDX-License-Identifier: Apache-2.0
"""`--help` has to work, for every subcommand there is.

argparse expands each help string with `% params` so that `%(default)s`
works, which means a literal per cent in help *text* is read as a format
placeholder. `ttcap demo --help` died with `TypeError: %d format: a real
number is required, not dict` because one help string named the
`frame-%04d.png` pattern -- and `--help` is the first thing anybody types.

The subcommands come from the parser itself, so a subcommand added later is
covered without this file being touched.
"""

from __future__ import annotations

import argparse

import pytest

from ttcap import cli


def subcommand_names() -> list[str]:
    """Every subcommand `ttcap` registers.

    Walking `_actions` for the subparsers action is argparse's only route to
    this -- there is no public accessor -- and it fails loudly rather than
    silently returning nothing if that ever changes.
    """
    for action in cli.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    raise AssertionError("ttcap's parser has no subcommands any more")


def test_the_parser_still_exposes_its_subcommands():
    names = subcommand_names()
    assert {"probe", "throughput", "capture", "png", "demo"} <= set(names)


@pytest.mark.parametrize("name", subcommand_names())
def test_every_subcommand_can_print_its_help(name, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main([name, "--help"])
    assert exit_info.value.code == 0
    printed = capsys.readouterr().out
    assert printed.startswith("usage: ttcap %s" % name)
    assert "-h, --help" in printed


def test_the_top_level_help_works_too(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    printed = capsys.readouterr().out
    for name in subcommand_names():
        assert name in printed


@pytest.mark.parametrize("name", subcommand_names())
def test_no_help_string_carries_an_unescaped_per_cent(name):
    """The bug itself, not just its symptom.

    `format_help()` is what raised; asking each *action* to expand its own
    help says which option is at fault when one does, instead of pointing at
    the whole parser.
    """
    for action in cli.build_parser()._subparsers._group_actions[0].choices[name]._actions:
        if not action.help:
            continue
        params = dict(vars(action), prog="ttcap %s" % name)
        for key in list(params):
            if params[key] is argparse.SUPPRESS:
                del params[key]
        action.help % params  # raises TypeError/ValueError on a stray per cent
