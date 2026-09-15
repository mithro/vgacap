# SPDX-License-Identifier: Apache-2.0
"""Tests for the browser view: the multipart broadcaster and its server.

No GStreamer and no board -- the broadcaster's whole job is cutting a byte
stream at part boundaries and handing the pieces to whoever is watching, and
the server's is answering two URLs. `test_ttcap_demo.py` drives the real
`jpegenc ! multipartmux` end of it.
"""

from __future__ import annotations

import threading
import urllib.request

from ttcap import mjpeg


def multipart(boundary: str, *bodies: bytes) -> bytes:
    """The bytes `multipartmux` writes for `bodies`, byte for byte."""
    return b"".join(
        b"--%s\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%s"
        % (boundary.encode(), len(body), body)
        for body in bodies
    )


def test_the_broadcaster_hands_out_whole_parts():
    caster = mjpeg.FrameBroadcaster("bnd")
    subscriber = caster.subscribe()
    stream = multipart("bnd", b"one", b"two", b"three")
    # Fed in awkward pieces, because that is how a pipe delivers it.
    for start in range(0, len(stream), 7):
        caster.feed(stream[start:start + 7])
    parts = [subscriber.get_nowait() for _ in range(2)]
    assert all(part.startswith(b"--bnd\r\n") for part in parts)
    assert parts[0].endswith(b"one") and parts[1].endswith(b"two")
    # The third part is still open -- nothing says it is complete until the
    # next boundary arrives, and half a JPEG is worse than a late one.
    assert subscriber.empty()


def test_rubbish_before_the_first_boundary_is_dropped():
    caster = mjpeg.FrameBroadcaster("bnd")
    subscriber = caster.subscribe()
    caster.feed(b"not a multipart stream at all" * 100)
    assert subscriber.empty()
    caster.feed(multipart("bnd", b"one") + b"--bnd\r\n")
    assert subscriber.get_nowait().endswith(b"one")


def test_a_subscriber_that_falls_behind_loses_frames_not_the_capture():
    caster = mjpeg.FrameBroadcaster("bnd")
    subscriber = caster.subscribe()
    bodies = [b"%04d" % i for i in range(mjpeg.FrameBroadcaster.DEPTH + 5)]
    caster.feed(multipart("bnd", *bodies) + b"--bnd\r\n")
    assert subscriber.qsize() == mjpeg.FrameBroadcaster.DEPTH
    kept = [subscriber.get_nowait() for _ in range(mjpeg.FrameBroadcaster.DEPTH)]
    # The newest parts, not the oldest: a live view wants now, not then.
    assert kept[-1].endswith(bodies[-1])


def test_every_subscriber_gets_the_same_parts():
    caster = mjpeg.FrameBroadcaster("bnd")
    first, second = caster.subscribe(), caster.subscribe()
    caster.feed(multipart("bnd", b"one", b"two"))
    assert first.get_nowait() == second.get_nowait()


def test_closing_the_broadcaster_ends_every_stream():
    caster = mjpeg.FrameBroadcaster("bnd")
    subscriber = caster.subscribe()
    caster.close()
    assert subscriber.get_nowait() is None
    # A browser that connects afterwards gets an empty stream, not a hang.
    assert caster.subscribe().get_nowait() is None


def test_the_server_answers_the_index_and_the_stream():
    caster = mjpeg.FrameBroadcaster()
    server = mjpeg.MjpegServer(0, caster)
    server.start()
    try:
        with urllib.request.urlopen(server.url, timeout=10) as response:
            body = response.read()
            assert response.headers["Content-Type"].startswith("text/html")
        assert b'<img src="/stream.mjpg"' in body

        # The stream ends when the capture does, so feed it from another
        # thread and close: an open-ended read would otherwise never return.
        def feed():
            caster.feed(multipart(caster.boundary, b"\xff\xd8\xff-one",
                                  b"\xff\xd8\xff-two", b"\xff\xd8\xff-three"))
            caster.close()

        thread = threading.Thread(target=feed)
        with urllib.request.urlopen(server.url + "stream.mjpg", timeout=10) as stream:
            content_type = stream.headers["Content-Type"]
            assert content_type == (
                "multipart/x-mixed-replace; boundary=%s" % caster.boundary
            )
            thread.start()
            seen = stream.read()
        thread.join(timeout=5)
        separator = b"--%s\r\n" % caster.boundary.encode()
        assert seen.startswith(separator)
        assert seen.count(separator) == 2  # the third part was never closed
        assert seen.count(b"\xff\xd8\xff") == 2
    finally:
        server.close()


def test_the_server_has_nothing_else_to_say():
    server = mjpeg.MjpegServer(0, mjpeg.FrameBroadcaster())
    server.start()
    try:
        try:
            urllib.request.urlopen(server.url + "etc/passwd", timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("the server answered a path it does not have")
    finally:
        server.close()


def test_the_server_stays_on_the_loopback_address():
    # A lab board's picture is not something to put on the office network by
    # accident, so the port is bound where only this machine can reach it.
    server = mjpeg.MjpegServer(0, mjpeg.FrameBroadcaster())
    try:
        assert server.server.server_address[0] == "127.0.0.1"
        assert server.url.startswith("http://127.0.0.1:")
    finally:
        server.close()
