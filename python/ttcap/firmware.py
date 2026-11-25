# SPDX-License-Identifier: Apache-2.0
"""Host side of the pico-sdk capture firmware (firmware/PROTOCOL.md).

The MicroPython path ships a script through the raw REPL for every
capture; this one talks to `firmware/`, which runs instead of the stock
firmware and already holds the capture code. It speaks a line protocol:

    hello                      -> ok vgacap-fw proto=1 chip=rp2350 ...
    clock hz=25000000          -> ok clock_hz=... sysclk_hz=... period=4
    frame cpl=800 ...          -> ok frame, then vgacap chunks, then DONE

and the chunks are the ones the MicroPython programs write (the firmware's
engines are those programs ported), so everything after the transport --
`CaptureRequest`, `CaptureSession`'s accounting and stop rules, the header
the host writes, the readers -- is shared. `FirmwareLink` is the transport,
`FirmwareSession` the `CaptureSession` that uses it, and `setup()` /
`teardown()` put the project in the state `select_project()` puts it in on
the MicroPython path.

`FirmwareLink` offers the subset of `RawRepl` the session and its recovery
paths call (`request_stop`, `drain_chunks`, `recover`, `last_stderr`), with
the same threading rule: every write goes through one lock, so another
thread's stop byte can never land inside a command.
"""

from __future__ import annotations

import threading
import time
from typing import Iterator

from .boards import RP2350_DBV3, BoardProfile
from .capture import CaptureError, CaptureRequest, CaptureSession
from .repl import CHUNK_HEADER_LEN, CHUNK_TAGS, MAX_CHUNK_LEN, ReplFramingError, ReplLink

#: `--firmware` names: what is running on the board.
FIRMWARES = ("micropython", "picosdk")

#: The protocol version this host speaks (`FW_PROTOCOL_VERSION`, fw.h).
PROTOCOL_VERSION = 1

#: Ends a capture's binary output: a chunk header whose tag is not a
#: vgacap tag (fw.c `out_done`). Never forwarded to the stream.
DONE_TAG = b"DONE"

#: Ctrl-C: asks a running capture to stop between chunks (fw.h).
STOP_BYTE = b"\x03"

#: The boards the firmware is built for, by the `chip=` its hello reports.
PROFILES = {"rp2350": RP2350_DBV3}


class FirmwareLink:
    """firmware/PROTOCOL.md over a `ReplLink` (serial, WebSocket, a pipe)."""

    def __init__(self, link: ReplLink) -> None:
        self._link = link
        self._buf = b""
        self._write_lock = threading.RLock()
        #: `RawRepl` compatibility for `CaptureSession`: the firmware has no
        #: stderr, so this stays empty.
        self.last_stderr = ""
        #: The `hello` reply's fields, once `sync()` has run.
        self.hello: dict[str, str] = {}

    # -- bytes ------------------------------------------------------------

    def _write(self, data: bytes) -> None:
        with self._write_lock:
            self._link.write(data)

    def _fill(self, deadline: float, what: str) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            buffered, self._buf = self._buf, b""
            raise TimeoutError("timed out waiting for %s; buffered: %r" % (what, buffered[:200]))
        chunk = self._link.read(min(remaining, 0.5))
        if chunk:
            self._buf += chunk

    def read_exact(self, n: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while len(self._buf) < n:
            self._fill(deadline, "%d bytes" % n)
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def read_line(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buf:
            self._fill(deadline, "a reply line")
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("utf-8", "replace").rstrip("\r")

    # -- commands ---------------------------------------------------------

    def sync(self, timeout: float = 5.0) -> dict[str, str]:
        """Bring the firmware to a known state and check what it is.

        A stop byte first, in case a capture is still running from a host
        that went away; that capture then finishes its chunk, writes its
        trailer and goes idle; to an idle firmware the 0x03 discards any
        half-sent command. Then an empty line (which does nothing) and
        `hello`, and everything before the hello reply is discarded.
        """
        self._buf = b""
        self._write(STOP_BYTE + b"\nhello\n")
        deadline = time.monotonic() + timeout
        # The reply can follow the tail of a stopped capture with no newline
        # in between (its DONE marker), so look for it anywhere.
        marker = b"ok vgacap-fw "
        while marker not in self._buf:
            # Keep only what could be the start of the marker.
            self._buf = self._buf[-len(marker):]
            self._fill(deadline, "the hello reply")
        self._buf = self._buf[self._buf.index(marker):]
        line = self.read_line(max(deadline - time.monotonic(), 0.1))
        fields = dict(tok.split("=", 1) for tok in line.split()[2:] if "=" in tok)
        if fields.get("proto") != str(PROTOCOL_VERSION):
            raise CaptureError(
                "capture firmware speaks protocol %s, this host speaks %d: %r"
                % (fields.get("proto"), PROTOCOL_VERSION, line)
            )
        self.hello = fields
        return fields

    def command(self, line: str, timeout: float = 10.0) -> dict[str, str]:
        """Send one command and return its `ok` reply's `key=value` fields.

        `err` replies raise `CaptureError` with the firmware's message.
        """
        self._write(line.encode("ascii") + b"\n")
        reply = self.read_line(timeout)
        if reply.startswith("err "):
            raise CaptureError("capture firmware refused %r: %s" % (line, reply[4:]))
        if reply != "ok" and not reply.startswith("ok "):
            raise CaptureError("capture firmware answered %r with %r" % (line, reply))
        return dict(tok.split("=", 1) for tok in reply.split()[1:] if "=" in tok)

    def start_capture(self, line: str, timeout: float = 10.0) -> None:
        """Send a `stream`/`frame` command and read its `ok`; chunks follow."""
        self._write(line.encode("ascii") + b"\n")
        reply = self.read_line(timeout)
        want = "ok " + line.split()[0]
        if reply != want:
            if reply.startswith("err "):
                raise CaptureError("capture firmware refused the capture: %s" % reply[4:])
            raise CaptureError("capture firmware answered %r with %r" % (line, reply))

    def chunks(self, timeout: float) -> Iterator[tuple[bytes, bytes]]:
        """The running capture's chunks, until its `DONE` marker.

        Length-driven like `RawRepl.exec_chunks()`, with the same tag
        whitelist and length bound, so a desynchronised link is caught at
        the first bad header. `timeout` bounds each read, not the run.
        """
        while True:
            header = self.read_exact(CHUNK_HEADER_LEN, timeout)
            tag = header[:4]
            length = int.from_bytes(header[4:8], "little")
            if tag == DONE_TAG:
                if length:
                    raise ReplFramingError("DONE marker with length %d" % length)
                return
            if tag not in CHUNK_TAGS:
                raise ReplFramingError("expected a vgacap chunk header, got %r" % header)
            if length > MAX_CHUNK_LEN:
                raise ReplFramingError(
                    "chunk %r declares %d bytes, over the %d-byte limit" % (tag, length, MAX_CHUNK_LEN)
                )
            yield tag, self.read_exact(length, timeout)

    # -- RawRepl's recovery verbs, for CaptureSession -------------------------

    def request_stop(self) -> None:
        """Ask the running capture to stop between chunks. Thread-safe."""
        self._write(STOP_BYTE)

    def drain_chunks(self, timeout: float = 5.0) -> bool:
        """Read and discard the rest of a capture, through its `DONE`."""
        try:
            for _tag, _payload in self.chunks(timeout):
                pass
        except (TimeoutError, ReplFramingError):
            return False
        return True

    def recover(self, timeout: float = 5.0) -> str:
        """Stop whatever is running and resynchronise on a `hello`."""
        try:
            self.sync(timeout)
        except (TimeoutError, CaptureError):
            pass
        return ""

    def close(self) -> None:
        self._link.close()


def profile_for(link: FirmwareLink, name: str) -> BoardProfile:
    """The board profile for `--profile`, checked against the firmware."""
    chip = link.hello.get("chip", "")
    if chip not in PROFILES:
        raise CaptureError("capture firmware reports chip %r, which no profile matches" % chip)
    profile = PROFILES[chip]
    if name not in ("auto", "rp2350"):
        raise CaptureError(
            "--profile %s does not match the capture firmware, which runs on %s" % (name, chip)
        )
    return profile


def capture_command(req: CaptureRequest) -> str:
    """The `stream`/`frame` command line for `req` (PROTOCOL.md).

    The values are the ones the MicroPython path puts in its `CFG` dict --
    `req.cfg()` is called for both, so the region planning and every check
    `capture_cfg()`/`frame_cfg()` make apply here too.
    """
    if req.profile != RP2350_DBV3:
        raise CaptureError("the capture firmware is built for %s only" % RP2350_DBV3.name)
    if req.is_rle:
        # Its CFG has buf_words like a stream's, and would quietly become one.
        raise CaptureError("the capture firmware has no rle mode; use --firmware micropython")
    cfg = req.cfg()
    if req.is_frame:
        return (
            "frame cpl=%d region_first=%d region_lines=%d window_lines=%d "
            "hsync_active=%d vsync_active=%d max_frames=%d max_bytes=%d edge=%s "
            "sample_delay=%d pio=%d sm=%d"
            % (
                cfg["cpl"], cfg["region_first"], cfg["region_lines"], cfg["window_lines"],
                cfg["hsync_active"], cfg["vsync_active"], cfg["max_frames"], cfg["max_bytes"],
                cfg["edge"], cfg["sample_delay"], cfg["pio"], cfg["sm"],
            )
        )
    return "stream buf_words=%d max_bytes=%d edge=%s pio=%d sm=%d" % (
        cfg["buf_words"], cfg["max_bytes"], cfg["edge"], cfg["pio"], cfg["sm"],
    )


def setup(link: FirmwareLink, req: CaptureRequest) -> dict:
    """Start the project clock, in `select_project()`'s order.

    The clock is stopped and the project held in reset before the new
    frequency is programmed, `ui_in` is set while it is held, and reset is
    released last. The firmware cannot select a design: it runs in place of
    the stock firmware, which is what knows the shuttle, so `--project` /
    `--design` are refused rather than ignored.

    `req.asic_index` is sent as is: nothing here knows which board is on
    the link, and on an FPGA breakout `asic_select` erases the FPGA. A
    caller other than `ttcap capture` must call
    `ttcap.demo.check_asic_index()` first (as `cli.capture()` does).
    """
    if req.selection:
        raise CaptureError(
            "the pico-sdk capture firmware cannot enable %r: enable the design with "
            "the stock firmware before loading this one (firmware/README.md)" % req.selection
        )
    did: dict = {"project": None, "enable": "kept (capture firmware)", "clock_hz": req.clock_hz}
    link.command("clock hz=0")
    link.command("reset hold=1")
    if req.asic_index is not None:
        # Nothing drives the mux lines while the RP2350 is in its
        # bootloader, so an ASIC board comes back with no project selected
        # (tt09, 2026-11-24).
        link.command("asic_select index=%d" % req.asic_index, timeout=30.0)
        did["asic_index"] = req.asic_index
    if req.ui_in is not None:
        link.command("ui_in value=%d" % req.ui_in)
        did["ui_in"] = req.ui_in
    clock = link.command("clock hz=%d" % req.clock_hz)
    did["sysclk_hz"] = int(clock["sysclk_hz"])
    did["actual_hz"] = int(clock["actual_hz"])
    link.command("reset hold=0")
    return did


def teardown(link: FirmwareLink, did: dict, stop_clock: bool) -> None:
    """Stop the clock if asked, and release `ui_in` if `setup()` drove it."""
    if stop_clock:
        link.command("clock hz=0")
    if "ui_in" in did:
        link.command("ui_in value=-1")


class FirmwareSession(CaptureSession):
    """`CaptureSession` over the pico-sdk firmware instead of the raw REPL.

    Only the start differs: `_prepare()` sends the capture command and
    reads its `ok` -- so a refused capture fails before the header is
    yielded and leaves no header-only file -- and `_start_stream()` reads
    the chunks. Accounting, stop rules, timeouts and teardown are the base
    class's, through `FirmwareLink`'s `RawRepl`-shaped verbs.
    """

    def __init__(self, link: FirmwareLink, req: CaptureRequest, **kwargs) -> None:
        super().__init__(link, req, **kwargs)  # type: ignore[arg-type]

    def _prepare(self) -> None:
        self._repl.start_capture(capture_command(self._req), timeout=self._chunk_timeout)
        # The `ok` is the proof the capture is running, so a stop byte can
        # be delivered from here on -- including one asked for already.
        with self._lock:
            self._streaming = True
        if self._stop_requested.is_set():
            self._send_stop()

    def _abandon_prepared(self) -> None:
        self._end_streaming()
        try:
            self._repl.request_stop()
            if not self._repl.drain_chunks(5.0):
                self._repl.recover()
        except Exception:
            pass

    def _start_stream(self) -> Iterator[tuple[bytes, bytes]]:
        return self._repl.chunks(self._chunk_timeout)
