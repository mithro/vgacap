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
import time
import traceback
import types

import pytest

from ttcap.repl import CTRL_A, CTRL_B, CTRL_D, LinkClosed, RawRepl, SerialLink, WebSocketLink


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
                    self._write(b"raw REPL; CTRL-B to exit\r\n>")
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


def test_exec_stream_surfaces_stderr_containing_gt(repl):
    # The traceback's "<module>" frame name contains ">" -- a naive drain
    # that scans for the literal byte ">" right after the first 0x04 would
    # stop inside this traceback instead of at the real trailing prompt.
    chunks = list(repl.exec_stream("print('abc', end='')\nraise ValueError('boom')"))

    assert b"".join(chunks) == b"abc"
    assert "ValueError: boom" in repl.last_stderr
    assert "<module>" in repl.last_stderr

    # The link is left clean: a normal exec still works afterwards.
    assert repl.exec("print(1)") == ("1\r\n", "")


def test_exec_stream_no_stderr_leaves_last_stderr_empty(repl):
    list(repl.exec_stream("print('ok')"))
    assert repl.last_stderr == ""


def test_read_until_discards_buffer_on_timeout():
    master_fd, slave_fd = os.openpty()
    try:
        link = SerialLink(os.ttyname(slave_fd))
        repl = RawRepl(link)
        os.write(master_fd, b"partial-bytes-that-never-match")
        with pytest.raises(TimeoutError):
            repl._read_until(b"NEVER-SEEN", timeout=0.2)
        assert repl._buf == b""
        link.close()
    finally:
        os.close(master_fd)


def test_exec_reassembles_split_replies(fake_board):
    fake_board.reply_chunk_size = 3
    fake_board.reply_delay = 0.005
    link = SerialLink(fake_board.slave_name)
    r = RawRepl(link)
    r.enter()  # banner itself is delivered in 3-byte chunks
    try:
        assert r.exec("print('hello world')") == ("hello world\r\n", "")
    finally:
        r.exit()
        link.close()


def test_enter_twice_reprints_banner(repl):
    # `repl` already called enter() once via the fixture; a second call
    # exercises the board re-emitting the raw-REPL banner idempotently.
    repl.enter()
    assert repl.exec("print(1)") == ("1\r\n", "")


def _ws_echo_handler(websocket):
    websocket.send("ignored text frame")
    for message in websocket:
        if isinstance(message, (bytes, bytearray)):
            websocket.send(message)


@pytest.fixture
def ws_echo_server():
    from websockets.sync.server import serve

    server = serve(_ws_echo_handler, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.socket.getsockname()[:2]
        yield f"ws://{host}:{port}/"
    finally:
        server.shutdown()
        thread.join(timeout=2.0)


def test_websocket_link_reads_binary_and_ignores_text_frames(ws_echo_server):
    link = WebSocketLink(ws_echo_server)
    try:
        link.write(b"ping")
        assert link.read(2.0) == b"ping"
    finally:
        link.close()


def test_websocket_link_raises_linkclosed_when_server_closes():
    from websockets.sync.server import serve

    def _close_immediately(websocket):
        websocket.close()

    server = serve(_close_immediately, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.socket.getsockname()[:2]
        link = WebSocketLink(f"ws://{host}:{port}/")
        with pytest.raises(LinkClosed):
            link.read(2.0)
    finally:
        server.shutdown()
        thread.join(timeout=2.0)

