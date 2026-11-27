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
import re
import threading
import time
import zlib
from contextlib import ExitStack
from typing import Iterator, Protocol, runtime_checkable

CTRL_A = b"\x01"
CTRL_B = b"\x02"
CTRL_C = b"\x03"
CTRL_D = b"\x04"

RAW_REPL_BANNER = b"raw REPL; CTRL-B to exit\r\n>"

#: Payload bytes per `upload()` command: 2,732 characters of base64.
_UPLOAD_SLICE_BYTES = 2048

#: The board-side name of `upload()`'s write handle, the only name it
#: binds in the REPL's globals while a write is open (apart from the module
#: `ubinascii`, which it imports and leaves).
_UPLOAD_HANDLE = "_ttcap_up_f"

#: Closes and unbinds the write handle, if one is bound; a no-op otherwise.
#: Sent before the check and after the slices, the failure path included.
#: A handle merely dropped is not safe: file objects are allocated with a
#: finaliser and `__del__` is close (`extmod/vfs_lfsx_file.c:93-95,207` in
#: MicroPython v1.24.0; finalisers are on at the rp2 port's ROM level,
#: `py/mpconfig.h:659-661`), and littlefs commits a dirty handle's size and
#: data at close (`lib/littlefs/lfs2.c:3182-3184,3367-3412`). So an orphaned
#: handle from a failed upload, finalised by some later GC, would commit
#: its stale contents over the file the retry wrote -- possibly after the
#: retry's read-back passed. Closing twice is a no-op
#: (`vfs_lfsx_file.c:180-183`).
_UPLOAD_CLOSE = f"""\
try:
    {_UPLOAD_HANDLE}
except NameError:
    pass
else:
    try:
        {_UPLOAD_HANDLE}.close()
    finally:
        del {_UPLOAD_HANDLE}
"""

#: Has the board report an uploaded file's length and crc32 (or that it
#: is absent), reading it back through a 2 KB buffer rather than whole --
#: the file exists so that 104 KB never has to sit on the heap at once.
#: `ubinascii.crc32` is C on the boards: `mod_binascii_crc32`,
#: `extmod/modbinascii.c:173-184` in MicroPython v1.24.0, compiled in
#: because the rp2 port builds at `MICROPY_CONFIG_ROM_LEVEL_EXTRA_FEATURES`
#: (`ports/rp2/mpconfigport.h:71`, `py/mpconfig.h:1639,1722-1723`).
#: Its working names are a function's locals, and the function is deleted,
#: so it leaves nothing in the REPL's globals. Only ENOENT is "absent": a
#: directory (littlefs gives EISDIR, `lfs2.c:3077-3079`) or a read error
#: raises, and nothing is written. (`OSError.errno` is `args[0]`,
#: `py/objexcept.c:286`.)
_UPLOAD_CHECK = """\
def _ttcap_up_check(p):
    try:
        f=open(p,'rb')
    except OSError as e:
        if e.errno==2:
            return 'absent'
        raise
    try:
        b=bytearray({size});m=memoryview(b);n=0;c=0
        while True:
            k=f.readinto(b)
            if not k:
                break
            n+=k;c=ubinascii.crc32(m[:k],c)
    finally:
        f.close()
    return 'len=%d crc32=%d'%(n,c)
try:
    print('ttcap upload',_ttcap_up_check({name!r}))
finally:
    del _ttcap_up_check
"""

_UPLOAD_CHECK_REPLY = re.compile(
    r"ttcap upload (?:absent|len=(\d+) crc32=(\d+))\r?\n"
)

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


class UploadError(RuntimeError):
    """An uploaded file's length or crc32 on the board is not the host's.

    The file is left as it is on the board: the next upload's check finds
    the mismatch and sends the file again.
    """


@runtime_checkable
class ReplLink(Protocol):
    def write(self, data: bytes) -> None: ...

    def read(self, timeout: float) -> bytes:
        """Return whatever is available within `timeout` seconds, or b"" on timeout."""
        ...

    def close(self) -> None: ...


class SerialLink:
    """A ReplLink over a local serial device (pyserial)."""

    def __init__(self, port: str, baudrate: int = 115200,
                 write_timeout: float | None = None) -> None:
        import serial  # local import: pyserial is an optional runtime dependency

        # `write_timeout` None blocks, as captures always have; a number
        # makes a write that cannot drain raise SerialTimeoutException.
        self._serial = serial.Serial(port, baudrate=baudrate, exclusive=True, timeout=0,
                                     write_timeout=write_timeout)

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

    def discard_output(self) -> None:
        """Drop whatever is written but not yet sent (tcflush), so a close
        after a write timeout does not wait for it to drain."""
        self._serial.reset_output_buffer()

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
    """Drives MicroPython's raw REPL over a `ReplLink`.

    One thread drives a command from end to end. The exception is the
    cooperative stop -- `request_stop()`, `interrupt()`, `recover()` -- which
    a *second* thread may send while the first is blocked reading a chunk,
    because that is how a long-running capture is ended by its consumer.
    Every write therefore goes through `_write()` and its lock: neither
    pyserial nor `websockets.sync` is safe for concurrent writes, and a stop
    byte spliced into the middle of an uploaded script would desynchronise
    the framing in a way no reader could recover from.

    The lock covers the write only, never the read that follows it. Holding
    it across a read would deadlock exactly the case it exists for: a stop
    byte cannot wait for the 30-second chunk read it is meant to cut short.
    """

    def __init__(self, link: ReplLink) -> None:
        self._link = link
        self._buf = b""
        self._write_lock = threading.RLock()
        #: stderr text captured by the most recent `exec_stream()` or
        #: `exec_chunks()` call (empty string if that command produced no
        #: stderr, or if neither has been called yet).
        self.last_stderr: str = ""

    def _write(self, data: bytes) -> None:
        """Put `data` on the link as one indivisible write."""
        with self._write_lock:
            self._link.write(data)

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
        self._write(CTRL_C + CTRL_C)
        self._write(CTRL_A)
        self._read_until(RAW_REPL_BANNER, timeout)

    def exit(self) -> None:
        self._write(CTRL_B)

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
        self._write(CTRL_C)

    def request_stop(self) -> None:
        """Ask a cooperative script to stop at its next safe point.

        The same byte as `interrupt()`, named for what the capture script
        does with it: picks it up off stdin *between* chunk writes, so no
        chunk is ever cut short on the wire. 0x03 rather than something
        exotic so that it still works as a real Ctrl-C on a script that
        never disabled the keyboard interrupt.
        """
        self._write(CTRL_C)

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
        self._write(CTRL_C)
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

    def start(self, code: str, timeout: float = 10.0) -> None:
        """Send `code` and wait only for the board's `OK`, not for it to end.

        For code that never returns a prompt -- `machine.reset()`,
        `machine.bootloader()` -- where waiting for the end of its output
        would only ever time out.
        """
        self._write(code.encode("utf-8") + CTRL_D)
        self._read_until(b"OK", timeout)

    def exec(self, code: str, timeout: float = 10.0) -> tuple[str, str]:
        self._write(code.encode("utf-8") + CTRL_D)
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
        self._write(code.encode("utf-8") + CTRL_D)
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
        self._write(code.encode("utf-8") + CTRL_D)
        self._read_until(b"OK", timeout)
        yield from self._chunk_stream(timeout)

    def begin_chunks(self, code: str, timeout: float = 10.0) -> str | None:
        """Run `code` and wait for the first byte of its output.

        For a command that either streams chunks or refuses with an
        exception before writing anything (the `vgacap` module's captures):
        if its stdout ends at once, the command produced nothing and this
        returns its stderr (also left in `last_stderr`), with the link at
        the next prompt. Otherwise it returns None with that first byte
        still to be read, and `chunk_stream()` reads the chunks.
        """
        self.last_stderr = ""
        self._write(code.encode("utf-8") + CTRL_D)
        self._read_until(b"OK", timeout)
        first = self.read_exact(1, timeout)
        if first == CTRL_D:
            stderr_and_marker = self._read_until(CTRL_D, timeout)
            self.last_stderr = stderr_and_marker[:-1].decode("utf-8")
            self._read_until(b">", timeout)
            return self.last_stderr
        self._buf = first + self._buf
        return None

    def chunk_stream(self, timeout: float = 10.0) -> Iterator[tuple[bytes, bytes]]:
        """The running command's chunks (after `begin_chunks()`), to its end."""
        return self._chunk_stream(timeout)

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

    def upload(self, name: str, data: bytes | str) -> bool:
        """Write `data` to the file `name` on the board, and check it there.

        A `str` is written as its UTF-8. The board is first asked for the
        file's length and crc32 (`zlib.crc32` on the host): if both match,
        nothing is sent and this returns False. Otherwise the file is
        written in 2 KB base64 slices, read back the same way, and this
        returns True. A file that does not match afterwards raises
        `UploadError` with both lengths and both crc32s, and is left on the
        board for the next upload's check to find. A board without
        `ubinascii.crc32` raises `RuntimeError` naming it, as does a target
        that is a directory.

        Every handle it opens on the board is closed before it returns or
        raises, and before the file is reopened. It leaves no names in the
        REPL's globals but `ubinascii`; while a write is open the handle is
        bound as `_ttcap_up_f`, so a caller must not use that name.
        """
        if isinstance(data, str):
            data = data.encode("utf-8")
        data = bytes(data)
        want = (len(data), zlib.crc32(data))
        # `ubinascii` is not in a fresh raw REPL's globals: the demo board's
        # `main.py` leaves `tt` there and nothing else, so without this the
        # first chunk write below dies with `NameError`. Importing a module
        # that is already in `sys.modules` costs only the rebind.
        self._exec_checked("import ubinascii")
        # A handle a lost host never closed is closed before anything
        # reopens the file (see `_UPLOAD_CLOSE`).
        self._exec_checked(_UPLOAD_CLOSE)
        if self._uploaded(name) == want:
            return False
        self._exec_checked(f"{_UPLOAD_HANDLE}=open({name!r},'wb')")
        try:
            for offset in range(0, len(data), _UPLOAD_SLICE_BYTES):
                piece = data[offset : offset + _UPLOAD_SLICE_BYTES]
                encoded = base64.b64encode(piece).decode("ascii")
                self._exec_checked(
                    f"{_UPLOAD_HANDLE}.write(ubinascii.a2b_base64(b'{encoded}'))"
                )
        except BaseException:
            # Best effort: the slice's own failure is the one to report. If
            # this close does not get through either, the next upload's
            # first close does it.
            try:
                self._exec_checked(_UPLOAD_CLOSE)
            except Exception:  # noqa: BLE001
                pass
            raise
        self._exec_checked(_UPLOAD_CLOSE)
        got = self._uploaded(name)
        if got != want:
            board = (
                "absent" if got is None
                else f"len={got[0]} crc32=0x{got[1]:08x}"
            )
            raise UploadError(
                f"upload of {name!r} did not verify: host len={want[0]} "
                f"crc32=0x{want[1]:08x}, board {board}; the file is left "
                "on the board"
            )
        return True

    def _uploaded(self, name: str) -> tuple[int, int] | None:
        """The board's (length, crc32) of file `name`, None if it is absent."""
        code = _UPLOAD_CHECK.format(name=name, size=_UPLOAD_SLICE_BYTES)
        reply = self._exec_checked(code, timeout=30.0)
        m = _UPLOAD_CHECK_REPLY.fullmatch(reply)
        if m is None:
            raise RuntimeError(
                f"unexpected reply to the upload check of {name!r}: {reply!r}"
            )
        if m.group(1) is None:
            return None
        return int(m.group(1)), int(m.group(2))

    def _exec_checked(self, code: str, timeout: float = 10.0) -> str:
        stdout, stderr = self.exec(code, timeout=timeout)
        if stderr:
            # Only the command's head: a slice command is 2.8 KB of base64.
            head = code if len(code) <= 80 else code[:80] + "..."
            raise RuntimeError(
                f"remote error executing {head!r} ({len(code)} chars): {stderr}"
            )
        return stdout


def restart_sdk(link: ReplLink, wait: float = 15.0) -> tuple[bool, str]:
    """Put a demo board back to the SDK's start state: Ctrl-C, Ctrl-B, Ctrl-D.

    Interrupts whatever is still running (Ctrl-C twice, as mpremote does:
    code busy on the board would otherwise delay or swallow the rest),
    leaves the raw REPL (Ctrl-B) and soft-resets from the friendly REPL
    (Ctrl-D), so `main.py` runs again and the SDK re-initialises with its
    default project. Never `machine.soft_reset()` from the raw REPL:
    measured on fpga-4, that leaves "the SDK is not running".

    Reads until the board is back at a `>>> ` prompt after the soft reboot
    (main.py has finished), or for at most `wait` seconds.

    Returns (whether a soft reboot was seen, the last of what the board
    printed). The link is left open; closing it is the caller's.
    """
    link.write(b"\r\x03\x03")  # interrupt any running code
    time.sleep(0.5)
    link.write(b"\x02")  # leave the raw REPL, if in it
    time.sleep(0.5)
    link.write(b"\r\n")
    time.sleep(0.5)
    link.write(b"\x04")  # soft reset from the friendly REPL
    deadline = time.monotonic() + wait
    seen = b""
    while time.monotonic() < deadline:
        seen += link.read(0.5)
        lowered = seen.lower()
        rebooted = lowered.find(b"soft reboot")
        if rebooted >= 0 and b">>> " in seen[rebooted:]:
            break
    text = seen.decode("utf-8", "replace")
    return "soft reboot" in text.lower(), text[-1500:]
