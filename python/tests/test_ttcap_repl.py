# SPDX-License-Identifier: Apache-2.0
"""Tests for ttcap.repl against a fake MicroPython raw REPL on a pty.

No hardware needed: `fake_repl.FakeRawRepl` gives us a master/slave fd
pair via `os.openpty()`, a background thread plays the board side of the
raw-REPL protocol on the master fd, and `SerialLink` talks to the slave
device name exactly as it would talk to a real /dev/ttyACM0.
"""

from __future__ import annotations

import os
import threading

import pytest

from fake_repl import FakeRawRepl
from ttcap.repl import LinkClosed, RawRepl, SerialLink, WebSocketLink


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


def test_a_fresh_board_has_no_ubinascii_bound(repl):
    # What the board actually looks like: the demo board's main.py leaves
    # `tt` in the REPL's globals and nothing else, so anything using
    # `ubinascii` without importing it first fails there. The fake used to
    # pre-bind it, which hid exactly that bug in `upload()`.
    stdout, stderr = repl.exec("print(ubinascii)")

    assert stdout == ""
    assert "NameError" in stderr


def test_upload_imports_ubinascii_before_using_it(repl, tmp_path, monkeypatch):
    sent = []
    original = repl._exec_checked

    def record(code, **kwargs):
        sent.append(code)
        return original(code, **kwargs)

    monkeypatch.setattr(repl, "_exec_checked", record)
    repl.upload(str(tmp_path / "sampler.py"), "print('hi')\n")

    first_use = next(i for i, code in enumerate(sent) if "ubinascii." in code)
    assert "import ubinascii" in sent[:first_use]


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

