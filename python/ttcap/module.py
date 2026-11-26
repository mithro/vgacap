# SPDX-License-Identifier: Apache-2.0
"""Host side of the `vgacap` MicroPython module (mpy/README.md).

A demo-board firmware built with the module runs the capture engines of
the pico-sdk capture firmware (firmware/core) inside MicroPython, so the
board keeps its stock behaviour -- the ttboard SDK, the shuttle, the FPGA
loader -- and a capture is one call instead of a shipped script:

    import vgacap
    vgacap.frame(clock_hz=25000000, cpl=800, region_first=34, ...)

Everything else is the MicroPython path's: the raw REPL, `select_project()`
for the design and its clock, the host-written `VGCH` header, the
cooperative stop byte, `CaptureSession`'s accounting. The call's keyword
arguments are the firmware protocol's arguments (`ttcap.firmware.
capture_args`), plus `clock_hz` for a frame capture, which the module cannot
know: the SDK set the clock.
"""

from __future__ import annotations

from typing import Iterator

from .capture import CaptureError, CaptureRequest, CaptureSession, prepare_board
from .firmware import capture_args
from .repl import RawRepl

#: `ttcap capture --firmware` name for a board whose firmware has the module.
FIRMWARE = "mpy-module"

#: The module interface this host speaks (`VGACAP_API`, mpy/modvgacap.c).
API = 1

#: Free heap a capture call needs besides its buffers: compiling one line
#: of code and the chunk headers, generously.
CALL_HEADROOM_BYTES = 4096

_PROBE = (
    "try:\n"
    "    import vgacap\n"
    "    print(vgacap.API)\n"
    "except ImportError:\n"
    "    print('absent')\n"
)


def probe(repl: RawRepl) -> None:
    """Check the board's firmware has the module, at the API this host speaks."""
    stdout, stderr = repl.exec(_PROBE)
    if stderr.strip():
        raise CaptureError("board rejected the vgacap module probe:\n%s" % stderr.strip())
    answer = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    if answer == "absent":
        raise CaptureError(
            "this board's firmware has no vgacap module: flash a demo-board "
            "firmware built with it (mpy/README.md), or capture with "
            "--firmware micropython"
        )
    if answer != str(API):
        raise CaptureError(
            "the board's vgacap module speaks API %r, this host speaks %d" % (answer, API)
        )


def capture_code(req: CaptureRequest) -> str:
    """The code the raw REPL runs for `req`: one `vgacap.stream/frame` call."""
    name, args = capture_args(req)
    if name == "frame":
        args = {"clock_hz": req.clock_hz, **args}
    call = ", ".join("%s=%r" % item for item in args.items())
    return "import vgacap\nvgacap.%s(%s)" % (name, call)


def refusal(stderr: str) -> str:
    """The message of the exception a refused call raised (its last line)."""
    lines = [line.strip() for line in stderr.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no output and no error"


class ModuleSession(CaptureSession):
    """`CaptureSession` over the module instead of a shipped script.

    `_prepare()` starts the call and waits for its first byte, so a refused
    capture -- a `ValueError` with the firmware's `err` text, raised before
    anything is written -- fails before the header is yielded and leaves no
    header-only file, as `FirmwareSession` does. Accounting, stop rules,
    timeouts and teardown are the base class's.
    """

    def __init__(self, repl: RawRepl, req: CaptureRequest, **kwargs) -> None:
        super().__init__(repl, req, **kwargs)

    def _prepare(self) -> None:
        req, repl = self._req, self._repl
        code = capture_code(req)
        self._mem_free = prepare_board(repl, [])
        if req.is_frame:
            needed = CALL_HEADROOM_BYTES + 4 * req.cfg()["cpl"] // req.profile.samples_per_word
        else:
            needed = CALL_HEADROOM_BYTES + 8 * req.buf_words
        if self._mem_free < needed:
            raise CaptureError(
                "board has only %d bytes of free heap after a collect, need %d for the "
                "capture's buffers; reset the board%s, and try again"
                % (self._mem_free, needed, "" if req.is_frame else ", or ask for fewer buffer words")
            )
        try:
            refused = repl.begin_chunks(code, timeout=self._chunk_timeout)
        except BaseException as exc:
            # The call may be running with Ctrl-C disabled -- a stream whose
            # first buffer never fills (no clock) writes nothing -- and
            # `_iter` has no teardown for `_prepare()`: stop it here, or
            # every later command reads its output.
            self._abandon_prepared()
            if isinstance(exc, TimeoutError):
                raise CaptureError("board went quiet before the first chunk: %s" % exc) from exc
            raise
        if refused is not None:
            raise CaptureError("the vgacap module refused the capture: %s" % refusal(refused))
        # The first byte is the proof the capture is running, so a stop byte
        # can be delivered from here on -- including one asked for already.
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
        return self._repl.chunk_stream(self._chunk_timeout)

    def _board_stderr(self) -> str:
        # One line of code, not a concatenated script: nothing to annotate.
        return self._repl.last_stderr
