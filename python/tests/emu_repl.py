# SPDX-License-Identifier: Apache-2.0
"""A raw REPL whose commands run on an emulator `Board`, for 4d's host side.

`FakeBinaryBoard` gives the raw-REPL framing, globals that persist from
one command to the next, and the board's filesystem (`files`, `dirs`, its
`open()` and `os`, parent directories enforced). The emulator `Board`
gives the rest: `machine`, `rp2`, `time` on its clock, and the fake SDK
(`fake_tt.board_sdk`) as `tt`, with `ttboard.fpga.fabricfoxv2` the fake
loader. The loader reads through the REPL's `open()`, so it loads exactly
what `RawRepl.upload()` left on the filesystem.

What a command prints is its reply, as on a board; unlike `Board.
run_script`, printing costs no emulated time. `sys.stdin` and `select` are
the board's: a script polling for a stop byte gets one once
`board.stop_requested` says so (`Board.stop_after_bytes`, `stop_when`).
"""

from __future__ import annotations

import builtins
import types

from fake_repl import FakeBinaryBoard
from fake_tt import FakeLoader, FakeTT, board_sdk


class EmuRepl(FakeBinaryBoard):
    """A `ReplLink` onto `board` with the fake SDK as `tt`.

    `files` and `dirs` are the filesystem it starts with; `loader` holds
    `FakeLoader` keyword arguments (`fail`, `fail_after`, `drain_lines`);
    the rest go to `FakeTT` (`mode`, `switches`, `manual_clock`).
    """

    def __init__(self, board, *, files: dict[str, bytes] | None = None, dirs=(),
                 loader: dict | None = None, **tt_kwargs) -> None:
        super().__init__(files=files, dirs=dirs, parents=True)
        self.board = board
        self.loader = FakeLoader(board, open=self._open, **(loader or {}))
        self.tt: FakeTT = board_sdk(board, loader=self.loader, **tt_kwargs)
        self._globals["tt"] = self.tt
        modules = dict(board.modules())
        # The stub's stdout is the command's reply; stdin is the board's,
        # which reads a stop byte once `board.stop_requested` says so.
        self._modules["sys"].stdin = modules.pop("sys").stdin
        for dotted, module in board.extra_modules.items():
            modules[dotted] = module
            parts = dotted.split(".")
            for i in range(len(parts) - 1, 0, -1):
                parent = ".".join(parts[:i])
                if parent not in modules:
                    modules[parent] = types.ModuleType(parent)
                setattr(modules[parent], parts[i], modules[".".join(parts[: i + 1])])
        for name, module in modules.items():
            self._modules.setdefault(name, module)
        real_import = builtins.__import__
        known = self._modules

        def _import(name, globals=None, locals=None, fromlist=(), level=0):
            if name in known:
                module = known[name]
                if isinstance(module, BaseException):
                    raise module
                if not fromlist and "." in name:
                    return known[name.split(".")[0]]  # `import a.b` binds `a`
                return module
            return real_import(name, globals, locals, fromlist, level)

        self._builtins["__import__"] = _import
