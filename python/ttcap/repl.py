# SPDX-License-Identifier: Apache-2.0
"""A link-agnostic client for MicroPython's raw REPL.

`ReplLink` is a tiny byte-pipe protocol (write/read/close) implemented by
`SerialLink` (pyserial, for a USB-CDC serial device) and `WebSocketLink`
(the fpgas.online serial-bridge WebSocket). `RawRepl` drives the MicroPython
raw-REPL protocol documented at
https://docs.micropython.org/en/latest/reference/repl.html#raw-mode-and-raw-paste-mode
over either link, without using raw-paste mode.
"""

from __future__ import annotations

import base64
import time
from typing import Iterator, Protocol, runtime_checkable

CTRL_A = b"\x01"
CTRL_B = b"\x02"
CTRL_C = b"\x03"
CTRL_D = b"\x04"

RAW_REPL_BANNER = b"raw REPL; CTRL-B to exit\r\n>"

_UPLOAD_CHUNK_SIZE = 256


@runtime_checkable
class ReplLink(Protocol):
    def write(self, data: bytes) -> None: ...

    def read(self, timeout: float) -> bytes:
        """Return whatever is available within `timeout` seconds, or b"" on timeout."""
        ...

    def close(self) -> None: ...


class SerialLink:
    """A ReplLink over a local serial device (pyserial)."""

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        import serial  # local import: pyserial is an optional runtime dependency

        self._serial = serial.Serial(port, baudrate=baudrate, exclusive=True, timeout=0)

    def write(self, data: bytes) -> None:
        self._serial.write(data)

    def read(self, timeout: float) -> bytes:
        self._serial.timeout = timeout
        chunk = self._serial.read(1)
        if not chunk:
            return b""
        waiting = self._serial.in_waiting
        if waiting:
            chunk += self._serial.read(waiting)
        return chunk

    def close(self) -> None:
        self._serial.close()


class WebSocketLink:
    """A ReplLink over the fpgas.online serial-bridge WebSocket."""

    def __init__(self, url: str) -> None:
        from websockets.sync.client import connect

        self._ws = connect(url, max_size=None)

    def write(self, data: bytes) -> None:
        self._ws.send(data)

    def read(self, timeout: float) -> bytes:
        # The bridge daemon also sends text frames carrying JSON status
        # events; those are not serial data and must be ignored here.
        while True:
            try:
                message = self._ws.recv(timeout=timeout)
            except TimeoutError:
                return b""
            if isinstance(message, bytes):
                return message
            # else: text frame (JSON event) -- discard and keep waiting.

    def close(self) -> None:
        self._ws.close()


class RawRepl:
    """Drives MicroPython's raw REPL over a `ReplLink`."""

    def __init__(self, link: ReplLink) -> None:
        self._link = link
        self._buf = b""
        #: stderr text captured by the most recent `exec_stream()` call
        #: (empty string if that command produced no stderr, or if
        #: `exec_stream()` has not been called yet).
        self.last_stderr: str = ""

    def _read_until(self, marker: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while marker not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"timed out waiting for {marker!r}; buffered so far: {self._buf!r}"
                )
            chunk = self._link.read(min(remaining, 0.5))
            if chunk:
                self._buf += chunk
        idx = self._buf.index(marker) + len(marker)
        result, self._buf = self._buf[:idx], self._buf[idx:]
        return result

    def enter(self, timeout: float = 5.0) -> None:
        # Two Ctrl-C first, to interrupt anything already running.
        self._link.write(CTRL_C + CTRL_C)
        self._link.write(CTRL_A)
        self._read_until(RAW_REPL_BANNER, timeout)

    def exit(self) -> None:
        self._link.write(CTRL_B)

    def exec(self, code: str, timeout: float = 10.0) -> tuple[str, str]:
        self._link.write(code.encode("utf-8") + CTRL_D)
        self._read_until(b"OK", timeout)
        stdout_and_marker = self._read_until(CTRL_D, timeout)
        stderr_and_marker = self._read_until(CTRL_D, timeout)
        self._read_until(b">", timeout)
        stdout = stdout_and_marker[:-1].decode("utf-8")
        stderr = stderr_and_marker[:-1].decode("utf-8")
        return stdout, stderr

    def exec_stream(self, code: str, timeout: float = 1.0, ok_timeout: float = 10.0) -> Iterator[bytes]:
        """Run `code` and yield raw stdout bytes as they arrive.

        Yields chunks of stdout after the "OK" ack, up to (not including)
        the first 0x04. `timeout` is the per-read timeout used while polling
        the link for more data -- for a long-running capture the caller may
        get empty reads for a while before more chunks are yielded.

        Once the generator is exhausted, `self.last_stderr` holds the
        board's stderr text for this command (empty string if none).
        """
        self._link.write(code.encode("utf-8") + CTRL_D)
        self._read_until(b"OK", ok_timeout)
        while CTRL_D not in self._buf:
            if self._buf:
                chunk, self._buf = self._buf, b""
                yield chunk
                continue
            more = self._link.read(timeout)
            if more:
                self._buf += more
        idx = self._buf.index(CTRL_D)
        if idx:
            yield self._buf[:idx]
        # self._buf[idx] is the first 0x04 (end of stdout). Drop it, then
        # read through the *second* 0x04 to consume the stderr block --
        # stderr text (e.g. a traceback naming "<module>") can itself
        # contain ">", so we must not search for the trailing prompt until
        # the whole stderr block has been consumed.
        self._buf = self._buf[idx + 1 :]
        stderr_and_marker = self._read_until(CTRL_D, ok_timeout)
        self.last_stderr = stderr_and_marker[:-1].decode("utf-8")
        self._read_until(b">", ok_timeout)

    def upload(self, name: str, source: str) -> None:
        """Write `source` to a file named `name` on the board."""
        self._exec_checked(f"f=open({name!r},'wb')")
        data = source.encode("utf-8")
        for offset in range(0, len(data), _UPLOAD_CHUNK_SIZE):
            chunk = data[offset : offset + _UPLOAD_CHUNK_SIZE]
            encoded = base64.b64encode(chunk).decode("ascii")
            self._exec_checked(f"f.write(ubinascii.a2b_base64(b'{encoded}'))")
        self._exec_checked("f.close()")

    def _exec_checked(self, code: str, timeout: float = 10.0) -> str:
        stdout, stderr = self.exec(code, timeout=timeout)
        if stderr:
            raise RuntimeError(f"remote error executing {code!r}: {stderr}")
        return stdout
