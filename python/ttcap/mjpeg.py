# SPDX-License-Identifier: Apache-2.0
"""The browser view: one multipart stream in, an MJPEG endpoint out.

`jpegenc ! multipartmux` already produces exactly what
`multipart/x-mixed-replace` wants -- `--boundary`, part headers, the JPEG --
so nothing here re-encodes anything. The work is only in cutting the stream
at part boundaries, so that a browser which connects mid-frame starts at the
beginning of one, and in handing the same parts to every viewer at once.

Standard library only, on purpose: the demo already needs GStreamer and a
board, and a web framework on top of that would be a third thing to install
on a Raspberry Pi to see a picture.
"""

from __future__ import annotations

import http.server
import os
import queue
import threading

#: The multipart boundary shared by `multipartmux` and the HTTP server. Any
#: token would do; a fixed one keeps the printed pipeline reproducible.
BOUNDARY = "vgacapframe"

INDEX_HTML = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>vgacap live</title>
<style>
  body { margin: 0; background: #111; color: #eee;
         font: 14px system-ui, sans-serif; text-align: center; }
  h1 { font-size: 1rem; font-weight: 600; padding: 0.75rem; margin: 0; }
  img { max-width: 100%; image-rendering: pixelated; background: #000; }
</style>
<h1>vgacap live capture</h1>
<img src="/stream.mjpg" alt="live capture">
"""


class FrameBroadcaster:
    """One multipart stream in, a copy of it to every browser watching.

    The bytes are relayed exactly as `multipartmux` wrote them; the only
    work is finding the part boundaries, so that a browser which connects
    mid-frame starts at the beginning of one rather than halfway through a
    JPEG.

    A subscriber that cannot keep up loses frames rather than slowing the
    capture: the queues are short and the oldest part is dropped when one
    fills. A live capture cannot be paused, so there is no other option.
    """

    #: Parts a slow subscriber may fall behind by before frames are dropped.
    DEPTH = 4

    def __init__(self, boundary: str = BOUNDARY) -> None:
        self.boundary = boundary
        self._separator = b"--" + boundary.encode("ascii") + b"\r\n"
        self._buffer = b""
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._closed = False
        #: Complete parts seen, for the final summary.
        self.parts = 0

    def subscribe(self) -> queue.Queue:
        with self._lock:
            if self._closed:
                q: queue.Queue = queue.Queue()
                q.put(None)
                return q
            q = queue.Queue(maxsize=self.DEPTH)
            self._subscribers.append(q)
            return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def feed(self, data: bytes) -> None:
        """Take bytes from the pipeline; publish whole parts."""
        self._buffer += data
        while True:
            start = self._buffer.find(self._separator)
            if start < 0:
                # No part has begun yet. Keep only enough to recognise a
                # separator split across two reads.
                if len(self._buffer) > len(self._separator):
                    self._buffer = self._buffer[-len(self._separator) :]
                return
            nxt = self._buffer.find(self._separator, start + len(self._separator))
            if nxt < 0:
                self._buffer = self._buffer[start:]
                return
            self._publish(self._buffer[start:nxt])
            self._buffer = self._buffer[nxt:]

    def _publish(self, part: bytes) -> None:
        self.parts += 1
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(part)
            except queue.Full:
                try:
                    q.get_nowait()  # drop the oldest, keep the newest
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(part)
                except queue.Full:  # pragma: no cover - another thread got in
                    pass

    def close(self) -> None:
        """End every subscriber's stream."""
        with self._lock:
            self._closed = True
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for q in subscribers:
            try:
                q.put_nowait(None)
            except queue.Full:  # pragma: no cover - the reader will see closed
                pass


def pump(read_fd: int, broadcaster: FrameBroadcaster) -> None:
    """Move a pipeline's multipart bytes into `broadcaster` until EOF."""
    try:
        while True:
            data = os.read(read_fd, 65536)
            if not data:
                return
            broadcaster.feed(data)
    except OSError:
        return
    finally:
        broadcaster.close()


class _MjpegHandler(http.server.BaseHTTPRequestHandler):
    # Set by `MjpegServer`.
    broadcaster: FrameBroadcaster

    protocol_version = "HTTP/1.0"  # one response per connection; no keep-alive
    server_version = "vgacap-demo"

    def log_message(self, fmt, *args):  # noqa: A003 - the base class's name
        """Quiet by default: the demo's own progress is the interesting
        output, and a browser polling an image would bury it."""
        if os.environ.get("VGACAP_DEMO_HTTP_LOG"):
            super().log_message(fmt, *args)

    def do_GET(self) -> None:  # noqa: N802 - the base class's name
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_bytes("text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
        elif path == "/stream.mjpg":
            self._send_stream()
        else:
            self.send_error(404, "no such thing here")

    def _send_bytes(self, content_type: str, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_stream(self) -> None:
        q = self.broadcaster.subscribe()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "multipart/x-mixed-replace; boundary=%s" % self.broadcaster.boundary,
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            while True:
                part = q.get()
                if part is None:
                    return
                self.wfile.write(part)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return  # the browser went away, which is how this normally ends
        finally:
            self.broadcaster.unsubscribe(q)


class MjpegServer:
    """A threaded HTTP server publishing one `FrameBroadcaster`.

    Deliberately bound to the loopback address: the stream is a view of a lab
    board, and the machine running the demo is usually on a network the board
    is not supposed to be on.
    """

    def __init__(self, port: int, broadcaster: FrameBroadcaster,
                 host: str = "127.0.0.1") -> None:
        handler = type("_Handler", (_MjpegHandler,), {"broadcaster": broadcaster})
        self.server = http.server.ThreadingHTTPServer((host, port), handler)
        self.server.daemon_threads = True
        self.broadcaster = broadcaster
        self.thread = threading.Thread(
            target=self.server.serve_forever, name="vgacap-mjpeg", daemon=True
        )
        self._started = False

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return "http://%s:%d/" % (host, port)

    def start(self) -> None:
        self._started = True
        self.thread.start()

    def close(self) -> None:
        """Stop serving and give the port back.

        The port is taken in `__init__`, so a server that was constructed
        and never started still has something to release -- and
        `shutdown()`, which waits for `serve_forever` to notice, would wait
        for ever on a loop that was never entered. Hence the flag.
        """
        self.broadcaster.close()
        if self._started:
            self.server.shutdown()
            self.thread.join(timeout=2.0)
        self.server.server_close()
