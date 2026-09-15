# SPDX-License-Identifier: Apache-2.0
"""Tests for ttcap.repl against a fake MicroPython raw REPL on a pty.

No hardware needed: `os.openpty()` gives us a master/slave fd pair, a
background thread plays the board side of the raw-REPL protocol on the
master fd, and `SerialLink` talks to the slave device name exactly as it
would talk to a real /dev/ttyACM0.
"""

from __future__ import annotations

import base64
import contextlib
import io
import os
import threading
import traceback
import types

import pytest

from ttcap.repl import CTRL_A, CTRL_B, CTRL_D, RawRepl, SerialLink


class FakeRawRepl:
    """Plays the board side of the documented raw-REPL protocol on a pty.

    Keeps a single persistent globals dict across `exec()` calls, matching
    real MicroPython raw REPL sessions, and a tiny `ubinascii` shim so
    `RawRepl.upload()`'s generated code runs unmodified.
    """

    def __init__(self) -> None:
        self.master_fd, self.slave_fd = os.openpty()
        self.slave_name = os.ttyname(self.slave_fd)
        self._stop = threading.Event()
        ubinascii = types.SimpleNamespace(a2b_base64=base64.b64decode)
        self._globals = {"__builtins__": __builtins__, "ubinascii": ubinascii}
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            os.close(self.master_fd)
        self._thread.join(timeout=2.0)

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
                if not in_raw_mode:
                    if CTRL_A in buf:
                        buf = buf.split(CTRL_A, 1)[1]
                        in_raw_mode = True
                        os.write(self.master_fd, b"raw REPL; CTRL-B to exit\r\n>")
                        progressed = True
                    elif buf:
                        buf = b""
                else:
                    if CTRL_B in buf:
                        buf = buf.split(CTRL_B, 1)[1]
                        in_raw_mode = False
                        progressed = True
                    elif CTRL_D in buf:
                        code, buf = buf.split(CTRL_D, 1)
                        out, err = self._run(code.decode("utf-8"))
                        os.write(
                            self.master_fd,
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


@pytest.fixture
def fake_board():
    board = FakeRawRepl()
    try:
        yield board
    finally:
        board.close()


@pytest.fixture
def repl(fake_board):
    link = SerialLink(fake_board.slave_name)
    r = RawRepl(link)
    r.enter()
    try:
        yield r
    finally:
        r.exit()
        link.close()


def test_exec_print(repl):
    assert repl.exec("print(1+1)") == ("2\r\n", "")


def test_exec_no_output(repl):
    assert repl.exec("x = 1") == ("", "")


def test_exec_stderr_on_exception(repl):
    stdout, stderr = repl.exec("raise ValueError('boom')")
    assert stdout == ""
    assert "ValueError: boom" in stderr


def test_exec_stream_yields_stdout_up_to_eot(repl):
    chunks = list(repl.exec_stream("for i in range(3):\n    print(i)"))
    assert b"".join(chunks) == b"0\r\n1\r\n2\r\n"

    # The link is left clean: a normal exec still works afterwards.
    assert repl.exec("print('done')") == ("done\r\n", "")


def test_upload_writes_file_via_base64_chunks(repl, tmp_path):
    target = tmp_path / "sampler.py"
    source = "print('sampler loaded')\n" + ("# padding\n" * 40)

    repl.upload(str(target), source)

    assert target.read_text() == source

