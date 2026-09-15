# SPDX-License-Identifier: Apache-2.0
"""The `RawRepl` write lock: a stop byte may never split another write.

A long-running capture is ended by its *consumer*, which lives on another
thread (a GStreamer element's `stop()`, a signal handler's flag, a test's
timer). That thread calls `request_stop()` while the capture thread may be
part way through uploading a script -- and neither pyserial nor
`websockets.sync` is safe for concurrent writes. A 0x03 landing inside the
script text would not stop anything; it would corrupt the command, and the
board would answer a syntax error into a stream the host is reading by
length.

The fake link here is deliberately unfair: it sleeps in the middle of every
write, so an unlocked implementation interleaves on the first attempt.
"""

from __future__ import annotations

import threading
import time

from ttcap.repl import CTRL_C, RawRepl


class SlowLink:
    """A `ReplLink` that takes its time, and records what it really saw.

    Each `write()` is logged as an ("enter", data) / ("leave", data) pair
    around a sleep, so a write that began before another finished is visible
    in the log rather than having to be inferred.
    """

    def __init__(self, pause: float = 0.02) -> None:
        self.pause = pause
        self.events: list[tuple[str, bytes]] = []
        self.writes: list[bytes] = []
        self._log_lock = threading.Lock()

    def write(self, data: bytes) -> None:
        with self._log_lock:
            self.events.append(("enter", data))
        time.sleep(self.pause)
        with self._log_lock:
            self.events.append(("leave", data))
            self.writes.append(data)

    def read(self, timeout: float) -> bytes:
        time.sleep(min(0.01, max(timeout, 0.0)))
        return b""

    def close(self) -> None:
        pass


def _interleaved(events: list[tuple[str, bytes]]) -> bool:
    """True if any write began before the previous one finished."""
    depth = 0
    for kind, _data in events:
        depth += 1 if kind == "enter" else -1
        if depth > 1:
            return True
    return False


def test_a_stop_from_another_thread_does_not_split_a_command_write():
    link = SlowLink()
    repl = RawRepl(link)
    script = "CFG = {'buf_words': 4096}\nmain()\n"

    # The capture thread uploads a script; the stop thread fires in the
    # middle of it, which is exactly when a consumer changes its mind.
    uploader = threading.Thread(target=lambda: repl._write(script.encode()))
    uploader.start()
    time.sleep(link.pause / 2)
    repl.request_stop()
    uploader.join(timeout=5.0)

    assert not uploader.is_alive()
    assert not _interleaved(link.events)
    # Both writes went out whole, and the stop byte is its own write.
    assert sorted(link.writes) == sorted([script.encode(), CTRL_C])


def test_many_concurrent_stops_still_serialise():
    link = SlowLink(pause=0.005)
    repl = RawRepl(link)
    threads = [threading.Thread(target=repl.request_stop) for _ in range(8)]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert not any(thread.is_alive() for thread in threads)
    assert not _interleaved(link.events)
    assert link.writes == [CTRL_C] * 8


def test_recover_and_interrupt_take_the_same_lock():
    # All three stop spellings must be indivisible, not just the one the
    # capture session happens to call.
    link = SlowLink()
    repl = RawRepl(link)
    uploader = threading.Thread(target=lambda: repl._write(b"print(1)\x04"))
    uploader.start()
    time.sleep(link.pause / 2)
    repl.interrupt()
    repl.recover(timeout=0.01)
    uploader.join(timeout=5.0)

    assert not uploader.is_alive()
    assert not _interleaved(link.events)
