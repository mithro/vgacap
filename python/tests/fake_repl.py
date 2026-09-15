# SPDX-License-Identifier: Apache-2.0
"""Fake MicroPython raw REPLs for host-side tests (no hardware needed).

Two flavours, both speaking the raw-REPL protocol documented at
https://docs.micropython.org/en/latest/reference/repl.html#raw-mode-and-raw-paste-mode:

* `FakeRawRepl` serves the board side on a `pty` (`os.openpty()`), so
  `SerialLink` can talk to it through a real device node exactly as it would
  talk to `/dev/ttyACM0`. A pty is a terminal, so it is only suitable for
  *text* replies.
* `FakeBinaryBoard` is an in-memory `ReplLink` with a stubbed `sys` module
  whose `stdout.buffer.write()` appends to the reply buffer. It is the one to
  use for scripts that emit binary (the pty's line discipline would mangle
  control bytes), and it runs the real MicroPython script text on the host.
"""

from __future__ import annotations

import base64
import builtins
import contextlib
import io
import os
import threading
import time
import traceback
import types

from ttcap.repl import CTRL_A, CTRL_B, CTRL_C, CTRL_D

RAW_BANNER = b"raw REPL; CTRL-B to exit\r\n>"


class FakeRawRepl:
    """Plays the board side of the documented raw-REPL protocol on a pty.

    Keeps a single persistent globals dict across `exec()` calls, matching
    real MicroPython raw REPL sessions, and a tiny `ubinascii` shim so
    `RawRepl.upload()`'s generated code runs unmodified.

    Set `reply_chunk_size` (and optionally `reply_delay`) before driving the
    board to force replies to be written in several small `os.write()`
    calls, exercising the client's split-read reassembly.
    """

    def __init__(self) -> None:
        self.master_fd, self.slave_fd = os.openpty()
        self.slave_name = os.ttyname(self.slave_fd)
        self._stop = threading.Event()
        self.reply_chunk_size: int | None = None
        self.reply_delay: float = 0.0
        ubinascii = types.SimpleNamespace(a2b_base64=base64.b64decode)
        self._globals = {"__builtins__": __builtins__, "ubinascii": ubinascii}
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            os.close(self.master_fd)
        self._thread.join(timeout=2.0)

    def _write(self, data: bytes) -> None:
        if not self.reply_chunk_size:
            os.write(self.master_fd, data)
            return
        for offset in range(0, len(data), self.reply_chunk_size):
            os.write(self.master_fd, data[offset : offset + self.reply_chunk_size])
            if self.reply_delay:
                time.sleep(self.reply_delay)

    def _serve(self) -> None:
        buf = b""
        in_raw_mode = False
        while not self._stop.is_set():
            try:
                chunk = os.read(self.master_fd, 4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk

            progressed = True
            while progressed:
                progressed = False
                if CTRL_A in buf:
                    # Real MicroPython boards (re)print the raw-REPL banner
                    # on Ctrl-A idempotently, whether or not a raw-REPL
                    # session is already active.
                    buf = buf.split(CTRL_A, 1)[1]
                    in_raw_mode = True
                    self._write(RAW_BANNER)
                    progressed = True
                elif not in_raw_mode:
                    if buf:
                        buf = b""
                else:
                    if CTRL_B in buf:
                        buf = buf.split(CTRL_B, 1)[1]
                        in_raw_mode = False
                        progressed = True
                    elif CTRL_D in buf:
                        code, buf = buf.split(CTRL_D, 1)
                        out, err = self._run(code.decode("utf-8"))
                        self._write(
                            b"OK" + out.encode("utf-8") + CTRL_D + err.encode("utf-8") + CTRL_D + b">",
                        )
                        progressed = True

    def _run(self, code: str) -> tuple[str, str]:
        stdout = io.StringIO()
        stderr = ""
        try:
            with contextlib.redirect_stdout(stdout):
                exec(code, self._globals)
        except Exception:
            stderr = traceback.format_exc()
        # Real boards emit \r\n line endings over the serial console.
        return stdout.getvalue().replace("\n", "\r\n"), stderr.replace("\n", "\r\n")


class _StubBuffer:
    """The binary half of the stub `sys.stdout`."""

    def __init__(self, sink: bytearray) -> None:
        self._sink = sink

    def write(self, data) -> int:
        self._sink.extend(data)
        return len(data)

    def flush(self) -> None:
        pass


class _StubStdout:
    def __init__(self, sink: bytearray) -> None:
        self._sink = sink
        self.buffer = _StubBuffer(sink)

    def write(self, data) -> int:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._sink.extend(data)
        return len(data)

    def flush(self) -> None:
        pass


class FakeBinaryBoard:
    """An in-memory `ReplLink` that runs raw-REPL commands with a binary stdout.

    The script text the host sends is `exec()`d on the host with a stubbed
    `sys` module (and any extra `modules` the test supplies), so the real
    MicroPython script source is exercised. Anything the script writes to
    `sys.stdout.buffer` becomes the command's stdout, framed exactly as a
    board frames it: ``OK<stdout>\\x04<stderr>\\x04>``.

    `corrupt` is an optional callable taking the stdout `bytearray`; it may
    mutate it in place to simulate a lossy or corrupting link.
    """

    def __init__(self, chunk_size: int = 8192, corrupt=None, modules=None) -> None:
        self.chunk_size = chunk_size
        self.corrupt = corrupt
        self.closed = False
        self.commands: list[str] = []
        self._pending = bytearray()
        self._in = bytearray()
        self._in_raw_mode = False
        self._sink = bytearray()
        stub_sys = types.SimpleNamespace(
            stdout=_StubStdout(self._sink),
            version="3.4.0",
            implementation=types.SimpleNamespace(name="micropython"),
        )
        self._modules = {"sys": stub_sys}
        if modules:
            self._modules.update(modules)
        real_import = builtins.__import__

        def _import(name, *args, **kwargs):
            if name in self._modules:
                return self._modules[name]
            return real_import(name, *args, **kwargs)

        self._builtins = dict(vars(builtins))
        self._builtins["__import__"] = _import
        self._globals: dict = {"__builtins__": self._builtins}

    # -- ReplLink ---------------------------------------------------------
    def write(self, data: bytes) -> None:
        self._in += data
        progressed = True
        while progressed:
            progressed = False
            if CTRL_A in self._in:
                self._in = bytearray(self._in.split(CTRL_A, 1)[1])
                self._in_raw_mode = True
                self._pending += RAW_BANNER
                progressed = True
            elif not self._in_raw_mode:
                # Ctrl-C and any other pre-raw-mode noise is discarded.
                if self._in:
                    self._in = bytearray()
            elif CTRL_B in self._in:
                self._in = bytearray(self._in.split(CTRL_B, 1)[1])
                self._in_raw_mode = False
                progressed = True
            elif CTRL_C in self._in:
                self._in = bytearray(self._in.split(CTRL_C, 1)[1])
                progressed = True
            elif CTRL_D in self._in:
                code, rest = self._in.split(CTRL_D, 1)
                self._in = bytearray(rest)
                self._pending += self._run(bytes(code).decode("utf-8"))
                progressed = True

    def read(self, timeout: float) -> bytes:
        if not self._pending:
            return b""
        take = self._pending[: self.chunk_size]
        del self._pending[: self.chunk_size]
        return bytes(take)

    def close(self) -> None:
        self.closed = True

    # -- board side -------------------------------------------------------
    def _run(self, code: str) -> bytes:
        self.commands.append(code)
        del self._sink[:]
        err = ""
        try:
            exec(code, self._globals)  # noqa: S102 - that is the point
        except Exception:
            err = traceback.format_exc().replace("\n", "\r\n")
        if self.corrupt is not None:
            self.corrupt(self._sink)
        return b"OK" + bytes(self._sink) + CTRL_D + err.encode("utf-8") + CTRL_D + b">"
