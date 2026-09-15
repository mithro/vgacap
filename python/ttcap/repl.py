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
from contextlib import ExitStack
from typing import Iterator, Protocol, runtime_checkable

CTRL_A = b"\x01"
CTRL_B = b"\x02"
CTRL_C = b"\x03"
CTRL_D = b"\x04"

RAW_REPL_BANNER = b"raw REPL; CTRL-B to exit\r\n>"

_UPLOAD_CHUNK_SIZE = 256

#: Every chunk tag `vgacap.stream` knows (mirrors `MIN_CHUNK_LEN`'s keys).
#: `exec_chunks()` accepts only these as the start of an 8-byte chunk
#: header, so a desynchronised stream is caught at the first bad header
#: instead of being interpreted as a 4 GiB payload.
CHUNK_TAGS = (b"VGCH", b"RAW ", b"RLE ", b"FRAM", b"EVNT", b"TIME")

#: Bytes in a chunk header: `tag[4] + u32 little-endian payload length`.
CHUNK_HEADER_LEN = 8

#: Largest payload length `exec_chunks()` will believe, the same bound the
#: C reader applies (`VGACAP_MAX_CHUNK_LEN`, include/vgacap/stream.h:33).
#: A known tag followed by a corrupt length would otherwise be read as a
#: payload of up to 4 GiB, and the only thing that ends that read is the
#: whole `timeout` expiring -- with the real framing error still unreported
#: and every byte of it swallowed. The board's own chunks are one DMA
#: buffer each, four orders of magnitude below this.
MAX_CHUNK_LEN = 16 * 1024 * 1024


class LinkClosed(Exception):
    """Raised by a `ReplLink` when the underlying connection has closed."""


class ReplFramingError(Exception):
    """Raised when the board's binary output is not valid chunk framing."""


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

        # `connect()` is documented for use as a context manager, and using
        # its result directly is deprecated ("connect() must be used as a
        # context manager", websockets >= 15). A link outlives the call that
        # opened it, so the context is entered into an `ExitStack` that
        # `close()` unwinds. This also works whichever way the version in
        # use has `connect()` return: `enter_context` gets the connection
        # either from the connection's own `__enter__` or from the
        # connector's.
        self._stack = ExitStack()
        self._ws = self._stack.enter_context(connect(url, max_size=None))

    def write(self, data: bytes) -> None:
        self._ws.send(data)

    def read(self, timeout: float) -> bytes:
        # The bridge daemon also sends text frames carrying JSON status
        # events; those are not serial data and must be ignored here.
        from websockets.exceptions import ConnectionClosed

        while True:
            try:
                message = self._ws.recv(timeout=timeout)
            except TimeoutError:
                return b""
            except ConnectionClosed as exc:
                raise LinkClosed(f"websocket link closed: {exc}") from exc
            if isinstance(message, bytes):
                return message
            # else: text frame (JSON event) -- discard and keep waiting.

    def close(self) -> None:
        self._stack.close()


class RawRepl:
    """Drives MicroPython's raw REPL over a `ReplLink`."""

    def __init__(self, link: ReplLink) -> None:
        self._link = link
        self._buf = b""
        #: stderr text captured by the most recent `exec_stream()` or
        #: `exec_chunks()` call (empty string if that command produced no
        #: stderr, or if neither has been called yet).
        self.last_stderr: str = ""

    def reset(self) -> None:
        """Discard any bytes buffered from a previous, unfinished command.

        `_read_until` already does this automatically when it times out, so
        this is mainly for a caller that wants to force a clean slate (e.g.
        after handling a `TimeoutError` itself, or before reusing a `RawRepl`
        whose link may have delivered stray bytes).
        """
        self._buf = b""

    def _read_until(self, marker: bytes, timeout: float) -> bytes:
        """Accumulate bytes from the link until `marker` appears, or raise.

        On `TimeoutError`, the internal buffer is discarded (any partially
        received framing bytes are thrown away) so a timed-out command can't
        leak stale bytes into the next command's framing.
        """
        deadline = time.monotonic() + timeout
        while marker not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                buffered = self._buf
                self._buf = b""
                raise TimeoutError(
                    f"timed out waiting for {marker!r}; buffered so far: {buffered!r}"
                )
            chunk = self._link.read(min(remaining, 0.5))
            if chunk:
                self._buf += chunk
        idx = self._buf.index(marker) + len(marker)
        result, self._buf = self._buf[:idx], self._buf[idx:]
        return result

    def read_exact(self, n: int, timeout: float) -> bytes:
        """Return exactly `n` bytes from the link, or raise `TimeoutError`.

        The counterpart to `_read_until` for length-driven reads: the bytes
        may take any value, 0x04 included, so nothing is scanned for. As
        with `_read_until`, a timeout discards the partial buffer rather
        than leaving half a chunk to desynchronise the next read.
        """
        deadline = time.monotonic() + timeout
        while len(self._buf) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                buffered = self._buf
                self._buf = b""
                raise TimeoutError(
                    f"timed out waiting for {n} bytes; got {len(buffered)}: {buffered!r}"
                )
            chunk = self._link.read(min(remaining, 0.5))
            if chunk:
                self._buf += chunk
        result, self._buf = self._buf[:n], self._buf[n:]
        return result

    def enter(self, timeout: float = 5.0) -> None:
        # Two Ctrl-C first, to interrupt anything already running.
        self._link.write(CTRL_C + CTRL_C)
        self._link.write(CTRL_A)
        self._read_until(RAW_REPL_BANNER, timeout)

    def exit(self) -> None:
        self._link.write(CTRL_B)

    def interrupt(self) -> None:
        """Send a single Ctrl-C.

        What that does depends on the running script. Normally it raises
        `KeyboardInterrupt` on the board; a script that has called
        `micropython.kbd_intr(-1)` -- as the capture script does, so that a
        stop cannot land inside a chunk write -- instead receives it as an
        ordinary byte on stdin. Either way the script's `finally` gets to
        run and emit its trailer, so the caller must keep draining the
        iterator afterwards.
        """
        self._link.write(CTRL_C)

    def request_stop(self) -> None:
        """Ask a cooperative script to stop at its next safe point.

        The same byte as `interrupt()`, named for what the capture script
        does with it: picks it up off stdin *between* chunk writes, so no
        chunk is ever cut short on the wire. 0x03 rather than something
        exotic so that it still works as a real Ctrl-C on a script that
        never disabled the keyboard interrupt.
        """
        self._link.write(CTRL_C)

    def recover(self, timeout: float = 5.0) -> str:
        """Interrupt a running script and resynchronise to the next prompt.

        The recovery path for a command whose framing has been lost (a
        timed-out `exec_chunks()`, say): the script is still running on the
        board, and leaving it there would make every later command read its
        output. Ctrl-C raises `KeyboardInterrupt` in it, and the trailer it
        then prints ends `0x04 >` -- the one two-byte sequence worth
        scanning for, since a lost position rules out reading by length.
        Sets and returns `last_stderr`, which for an interrupted script is
        its traceback.
        """
        self._link.write(CTRL_C)
        try:
            tail = self._read_until(CTRL_D + b">", timeout)
        except TimeoutError:
            self.last_stderr = ""
            return ""
        body = tail[: -len(CTRL_D + b">")]
        # Everything after the *last* 0x04 in the body is stderr: the ones
        # before it end the script's stdout (or are payload bytes).
        end_of_stdout = body.rfind(CTRL_D)
        stderr = body[end_of_stdout + 1 :] if end_of_stdout >= 0 else b""
        self.last_stderr = stderr.decode("utf-8", "replace")
        return self.last_stderr

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

    def exec_chunks(self, code: str, timeout: float = 10.0) -> Iterator[tuple[bytes, bytes]]:
        """Run `code` and yield the vgacap chunks it writes to stdout.

        The raw REPL ends a script's stdout with an *unescaped* 0x04, and
        binary sample data contains 0x04 like any other byte -- so
        `exec_stream()`, which scans for that marker, cannot be used for a
        capture. This reads length-driven instead: each chunk is
        `tag[4] + u32 little-endian length + payload`, so the payload
        length is known before a single payload byte is read and no byte of
        it is ever inspected.

        End of stdout is detected on the *first* byte of what would be the
        next header: only there can a 0x04 not be payload. It is read on
        its own because the trailer that follows it (stderr, 0x04, `>`) can
        be as short as three bytes -- waiting for a full 8-byte header
        would hang. Afterwards `self.last_stderr` holds the board's stderr
        for this command and the link is positioned at the next command,
        exactly as `exec()` leaves it.

        `timeout` bounds each individual read, not the whole run: a capture
        may legitimately stream for minutes, but a gap longer than
        `timeout` between bytes is treated as a dead board.
        """
        self._link.write(code.encode("utf-8") + CTRL_D)
        self._read_until(b"OK", timeout)
        yield from self._chunk_stream(timeout)

    def _chunk_stream(self, timeout: float) -> Iterator[tuple[bytes, bytes]]:
        """Yield chunks until the end of the running command's stdout.

        Split out of `exec_chunks()` so that `drain_chunks()` can read the
        rest of a command whose chunks nobody wants any more: the framing
        rules are identical, only the code that starts the command is not.
        """
        while True:
            first = self.read_exact(1, timeout)
            if first == CTRL_D:
                stderr_and_marker = self._read_until(CTRL_D, timeout)
                self.last_stderr = stderr_and_marker[:-1].decode("utf-8")
                self._read_until(b">", timeout)
                return
            header = first + self.read_exact(CHUNK_HEADER_LEN - 1, timeout)
            tag = header[:4]
            if tag not in CHUNK_TAGS:
                raise ReplFramingError(
                    f"expected a vgacap chunk header, got {header!r}"
                )
            length = int.from_bytes(header[4:8], "little")
            if length > MAX_CHUNK_LEN:
                raise ReplFramingError(
                    f"chunk {tag!r} declares {length} bytes, over the "
                    f"{MAX_CHUNK_LEN}-byte limit the readers accept: the "
                    "stream has desynchronised"
                )
            yield tag, self.read_exact(length, timeout)

    def drain_chunks(self, timeout: float = 5.0) -> bool:
        """Read and discard chunks until the running command's prompt.

        The tidy half of abandoning a capture: the script on the board is
        still writing, and its bytes have to be taken off the wire (and its
        stderr and prompt consumed) before anything else can use the link.
        Returns True if the command really did end -- `last_stderr` is then
        set exactly as `exec_chunks()` would leave it -- and False if the
        board did not stop within `timeout` or its framing was already
        lost, which leaves a real Ctrl-C (`recover()`) as the only way out.
        """
        try:
            for _tag, _payload in self._chunk_stream(timeout):
                pass
        except (TimeoutError, ReplFramingError, LinkClosed):
            return False
        return True

    def upload(self, name: str, source: str) -> None:
        """Write `source` to a file named `name` on the board."""
        # `ubinascii` is not in a fresh raw REPL's globals: the demo board's
        # `main.py` leaves `tt` there and nothing else, so without this the
        # first chunk write below dies with `NameError`. Importing a module
        # that is already in `sys.modules` costs only the rebind.
        self._exec_checked("import ubinascii")
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
