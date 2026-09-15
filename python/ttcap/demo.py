# SPDX-License-Identifier: Apache-2.0
"""`ttcap demo`: a Welland board in, PNGs, video, a window and a browser out.

One capture feeds every output. `vgacapttsrc` runs `ttcap capture --out -`
against the board, `vgadecode` turns the stream into RGB frames, and a `tee`
fans those out to as many sinks as were asked for:

* `frame-%04d.png` in `--outdir`, through `pngenc ! multifilesink`;
* `capture.mkv`, through `x264enc` (or `vp8enc` where x264 is absent) into
  `matroskamux`;
* a window, through `autovideosink` (`--window`);
* an MJPEG stream a browser can open, through `jpegenc ! multipartmux` and
  the small HTTP server in `ttcap.mjpeg` (`--serve PORT`).

### Why a subprocess and not `Gst` from Python

The GStreamer Python bindings (`gi`, the `python3-gi`/`pygobject` OS package)
are not installable into the `uv` environment this command runs in -- they
are built against the system GStreamer -- and `ttcap` is a `uv` script. So
the pipeline is built as a `gst-launch-1.0` argument vector, which is the
same `gst_parse_launch` grammar `Gst.parse_launch` would take, and run as a
child process. `--dry-run` prints exactly that command and stops, so the
pipeline is always copy-pasteable. `use_gst_python()` says which way a given
machine would go, and the module falls back without being asked.

### Why the source is never addressed by URI

`vgacapbin` accepts a `uri`, and a URI query string may set capture
parameters only -- never `ttcap-command`, which names a program to run, and
never `link`, which names where the capture happens. That allow-list exists
precisely because this command can publish a stream to a browser, and a URI
that reached the pipeline from outside would otherwise be arbitrary command
execution. So the demo does not build a URI at all: it names `vgacapttsrc`
and `vgadecode` itself and sets every setting, `ttcap-command` included, as
an element property, which only whoever builds the pipeline can do.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import pathlib
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

from .boards import WELLAND, bridge_ws_url
# Same package, and the octet is the one part of a Welland address the tunnel
# hint has to spell out; `bridge_ws_url` hands back a whole URL.
from .boards import _welland_host_octet as welland_octet
from .capture import CaptureError
from .mjpeg import BOUNDARY as MJPEG_BOUNDARY, FrameBroadcaster, MjpegServer, pump

#: The serial device the fpgas.online image gives the demo board it hosts.
#: On the Pi itself this is the direct link, with no bridge in the way.
PI_SERIAL_LINK = "serial:/dev/ttboard"

#: The bastion a workstation reaches the Welland bench network through.
WELLAND_GATEWAY = "tweed.welland.mithis.com"

#: `vgacapttsrc`'s own default, repeated so `--dry-run` prints what will run.
DEFAULT_TTCAP_COMMAND = "uv run --no-sync ttcap"

#: Video encoders the demo knows how to put into a Matroska file, best first.
#: `x264enc` is in gst-plugins-ugly and is not everywhere (Raspbian has it,
#: some minimal images do not); `vp8enc` comes with gst-plugins-good.
VIDEO_ENCODERS = (
    ("x264enc", "x264enc tune=zerolatency speed-preset=veryfast key-int-max=30"),
    ("vp8enc", "vp8enc deadline=1 cpu-used=4"),
)

VIDEO_FILENAME = "capture.mkv"
PNG_PATTERN = "frame-%04d.png"


# --------------------------------------------------------------- environment


def use_gst_python() -> bool:
    """True when `Gst` can be imported here, so a pipeline could be built
    in-process instead of shelled out to `gst-launch-1.0`.

    It is checked rather than assumed because the two halves are packaged by
    different people: `gi` is an OS package built against the system
    GStreamer, and the interpreter running this is usually a `uv` virtual
    environment that cannot see it. The answer only decides *how* the
    pipeline runs; the pipeline itself is the same string either way.
    """
    try:
        import gi  # noqa: F401

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


def have_element(name: str) -> bool:
    """True when GStreamer has a factory called `name`.

    `Gst.ElementFactory.find` when the bindings are here, and
    `gst-inspect-1.0 --exists` when they are not -- which is the same
    registry, asked from outside.
    """
    if use_gst_python():
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        if not Gst.is_initialized():
            Gst.init(None)
        return Gst.ElementFactory.find(name) is not None
    inspect = shutil.which("gst-inspect-1.0")
    if inspect is None:
        raise CaptureError(
            "cannot find gst-inspect-1.0: install GStreamer's tools "
            "(gstreamer1.0-tools on Debian/Raspbian)"
        )
    return subprocess.run([inspect, "--exists", name], check=False).returncode == 0


def pick_video_encoder(wanted: str = "auto") -> str:
    """The encoder branch for `capture.mkv`, as pipeline text.

    `auto` takes the first of `VIDEO_ENCODERS` this machine actually has.
    Naming one directly still checks it is there, so a missing encoder is a
    sentence rather than a GStreamer parse error forty lines into the run.
    """
    if wanted != "auto":
        for name, branch in VIDEO_ENCODERS:
            if name == wanted:
                if not have_element(name):
                    raise CaptureError(
                        f"--video-encoder {wanted} but GStreamer has no {name} "
                        f"element; the others the demo knows are "
                        + ", ".join(n for n, _ in VIDEO_ENCODERS if n != name)
                    )
                return branch
        raise CaptureError(f"unknown --video-encoder {wanted!r}")
    for name, branch in VIDEO_ENCODERS:
        if have_element(name):
            return branch
    raise CaptureError(
        "no video encoder: none of %s is installed. Install gst-plugins-good "
        "(vp8enc) or gst-plugins-ugly (x264enc), or pass --no-video."
        % ", ".join(name for name, _ in VIDEO_ENCODERS)
    )


# ------------------------------------------------------------- board -> link


def board_choices() -> str:
    return ", ".join(sorted(WELLAND))


def pi_hostname(board: str) -> str:
    """The host name of the Pi that holds `board`.

    The fpgas.online bench names each Pi after the power-switch port its
    board hangs off -- `pi-sw2-p7` for tt07 -- which is the same name
    `WELLAND` already records and the same number its 10.21.2.x address ends
    in.
    """
    pin_name, _ = WELLAND[board]
    return pin_name


def running_on_board_pi(board: str, hostname: str | None = None) -> bool:
    """True when this process is on the Pi that holds `board`.

    The hostname is the test, as the bench names its Pis after the switch
    port; a fully qualified name counts, so `pi-sw2-p7.welland...` matches.
    Failing that, the board's bridge address being one of this machine's own
    addresses says the same thing without relying on a naming convention --
    binding a socket to an address succeeds only locally, and binds nothing
    anyone can reach because the port is left to the kernel.
    """
    host = (hostname if hostname is not None else socket.gethostname()).lower()
    if host.split(".", 1)[0] == pi_hostname(board).lower():
        return True
    return _address_is_local(bridge_host(board))


def bridge_host(board: str) -> str:
    return "10.21.2.%s" % welland_octet(board)


def _address_is_local(address: str) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind((address, 0))
    except OSError:
        return False
    return True


def tunnel_hint(board: str) -> str:
    """How to reach `board`'s bridge from a workstation, in full.

    The bench network is not routed off the bench, so the daemon at
    10.21.2.x:8765 is reachable only through the gateway. This is printed
    when a capture over a bridge link fails, because "connection refused" on
    its own sends people to the board.
    """
    octet = welland_octet(board)
    return (
        "%s is on the Welland bench network, which a workstation reaches only\n"
        "through an SSH tunnel:\n"
        "\n"
        "    ssh -N -L %s:%s:8765 %s\n"
        "\n"
        "then run the demo again against the near end of it:\n"
        "\n"
        "    ttcap demo --link ws://127.0.0.1:%s/serial ...\n"
        "\n"
        "(a local port below 1024 needs root, so any free port does as well:\n"
        " ssh -N -L 18765:%s:8765 %s, then --link ws://127.0.0.1:18765/serial)"
        % (
            bridge_ws_url(board),
            octet,
            bridge_host(board),
            WELLAND_GATEWAY,
            octet,
            bridge_host(board),
            WELLAND_GATEWAY,
        )
    )


@dataclass(frozen=True)
class Link:
    """Where the capture runs, and what the user should know about it."""

    url: str
    #: Why this URL, in one line, for the run's opening report.
    why: str
    #: Things to do before the capture works at all; printed up front.
    notes: tuple[str, ...] = ()
    #: True when a failure to connect is worth a tunnel hint.
    through_bridge: bool = False


def resolve_link(
    board: str | None, link: str | None = None, hostname: str | None = None
) -> Link:
    """Pick the link for `--board`/`--link`.

    `--link` always wins: it is how a tunnel, a bench machine or a board on
    someone's desk is named, and none of those are in `WELLAND`. Otherwise
    the board decides, and *where this is running* decides how to reach it --
    on the board's own Pi the demo board is a serial device and the bridge is
    a detour (and one that has the device open), so the serial link wins
    there.
    """
    if link:
        return Link(link, "from --link")
    if board is None:
        raise CaptureError(
            "no board: pass --board (one of %s) or --link serial:/dev/ttyACM0 "
            "or --link ws://host:8765/serial" % board_choices()
        )
    if board not in WELLAND:
        raise CaptureError(
            "unknown board %r: --board takes one of %s. A board that is not on "
            "the Welland bench is reached with --link instead, e.g. --link "
            "serial:/dev/ttyACM0." % (board, board_choices())
        )
    if running_on_board_pi(board, hostname):
        return Link(
            PI_SERIAL_LINK,
            "%s is this machine's own board" % board,
            notes=(
                "The fpgas.online bridge holds /dev/ttboard open, so stop it "
                "first: sudo systemctl stop fpgas-tt (and start it again "
                "afterwards).",
            ),
        )
    return Link(
        bridge_ws_url(board),
        "%s is on %s" % (board, pi_hostname(board)),
        through_bridge=True,
    )


# ----------------------------------------------------------- the pipeline


@dataclass
class DemoPlan:
    """Everything the run needs, worked out before anything is started."""

    link: Link
    argv: list[str]
    outdir: pathlib.Path
    png: bool
    video: bool
    video_path: pathlib.Path | None
    window: bool
    serve_port: int | None
    mjpeg_fd: int | None = None
    #: Outputs, in the order they were added, for the opening report.
    outputs: list[str] = field(default_factory=list)


def source_properties(
    link: str,
    *,
    clock_hz: int,
    seconds: float,
    project: str | None,
    design: str | None,
    profile: str,
    buf_words: int | None,
    ttcap_command: str,
) -> list[str]:
    """`vgacapttsrc`'s settings, as `name=value` tokens.

    Every one of them is an element property. `ttcap-command` especially:
    see the module docstring for why it never travels in a URI.
    """
    props = [
        "link=%s" % link,
        "clock-hz=%d" % clock_hz,
        "seconds=%s" % _number(seconds),
        "profile=%s" % profile,
        "ttcap-command=%s" % ttcap_command,
    ]
    if project:
        props.append("project=%s" % project)
    if design:
        props.append("design=%s" % design)
    if buf_words is not None:
        props.append("buf-words=%d" % buf_words)
    return props


def _number(value: float) -> str:
    """`5` rather than `5.0`, so the printed pipeline reads like one a person
    would have typed."""
    return "%g" % value


def parse_fps(text: str) -> str:
    """`--fps` as the `output-fps` fraction `vgadecode` takes.

    `30`, `30/1` and `7.5` all mean the same thing to a person, and none of
    them is what a `GstFraction` is spelled as except the middle one. A
    decimal is turned into a fraction by its decimal places rather than by
    `Fraction.limit_denominator`, so `29.97` stays exactly 2997/100 and not a
    near miss.
    """
    text = text.strip()
    try:
        if "/" in text:
            numerator_text, denominator_text = text.split("/", 1)
            numerator, denominator = int(numerator_text), int(denominator_text)
        elif "." in text:
            whole, places = text.split(".", 1)
            denominator = 10 ** len(places)
            numerator = int((whole or "0") + places)
            if whole.startswith("-"):
                numerator = -abs(numerator)
        else:
            numerator, denominator = int(text), 1
    except ValueError:
        raise CaptureError(
            "--fps %r is not a frame rate: write 30, 30/1 or 29.97" % text
        ) from None
    if denominator <= 0 or numerator <= 0:
        raise CaptureError("--fps %s must be positive" % text)
    # vgadecode's own range; refused here so it is a sentence rather than a
    # GObject warning from inside the pipeline.
    if not (denominator <= numerator * 1000 and numerator <= denominator * 1000):
        raise CaptureError(
            "--fps %s is outside vgadecode's 1/1000 to 1000/1 range" % text
        )
    return "%d/%d" % (numerator, denominator)


def decode_properties(fps: str | None) -> list[str]:
    """`vgadecode`'s settings, as `name=value` tokens.

    Without `--fps` the element times frames from the project clock, which is
    the honest picture: at the RP2040's 60 kHz floor a 640x480 frame is
    800x525 clocks, so seven seconds each, and the video is a slideshow of
    what the board really did.

    With it, `repeat-last-frame` re-pushes the last frame to fill the gaps
    and the video plays at wall-clock speed -- the same frames, at a rate a
    player and a browser can show. Nothing is invented: a repeated frame is
    the frame that was on the screen.
    """
    if fps is None:
        return []
    return ["repeat-last-frame=true", "output-fps=%s" % fps]


def build_pipeline(
    link: str,
    outdir: pathlib.Path,
    *,
    clock_hz: int,
    seconds: float,
    project: str | None = None,
    design: str | None = None,
    profile: str = "auto",
    buf_words: int | None = None,
    ttcap_command: str = DEFAULT_TTCAP_COMMAND,
    png: bool = True,
    video: bool = True,
    video_encoder: str = "auto",
    window: bool = False,
    mjpeg_fd: int | None = None,
    fps: str | None = None,
) -> tuple[list[str], list[str]]:
    """The `gst-launch-1.0` argument vector, and what it writes.

    `-e` is not optional: it is what turns Ctrl-C into an end-of-stream that
    travels the whole pipeline, so the muxer writes its index and the
    Matroska file is playable. Without it an interrupted run leaves a
    truncated file. `-m` puts every bus message on stdout, which is where the
    detected mode and the frame count are read from.
    """
    branches: list[list[str]] = []
    outputs: list[str] = []

    if png:
        branches.append(
            shlex.split(
                "t. ! queue ! pngenc ! multifilesink post-messages=true "
                "location=%s" % shlex.quote(str(outdir / PNG_PATTERN))
            )
        )
        outputs.append(str(outdir / PNG_PATTERN))
    if video:
        encoder = pick_video_encoder(video_encoder)
        video_path = outdir / VIDEO_FILENAME
        branches.append(
            shlex.split(
                "t. ! queue ! videoconvert ! %s ! matroskamux ! filesink "
                "location=%s" % (encoder, shlex.quote(str(video_path)))
            )
        )
        outputs.append(str(video_path))
    if window:
        branches.append(shlex.split("t. ! queue ! videoconvert ! autovideosink"))
        outputs.append("a window (autovideosink)")
    if mjpeg_fd is not None:
        branches.append(
            shlex.split(
                "t. ! queue leaky=downstream max-size-buffers=4 ! videoconvert "
                "! jpegenc ! multipartmux boundary=%s ! fdsink sync=false fd=%d"
                % (MJPEG_BOUNDARY, mjpeg_fd)
            )
        )
        outputs.append("an MJPEG stream")

    if not branches:
        raise CaptureError(
            "nothing to write: --no-png and --no-video with no --window and "
            "no --serve leaves the capture with nowhere to go"
        )

    argv = [
        "gst-launch-1.0",
        "-e",
        "-m",
        "vgacapttsrc",
        "name=src",
        *source_properties(
            link,
            clock_hz=clock_hz,
            seconds=seconds,
            project=project,
            design=design,
            profile=profile,
            buf_words=buf_words,
            ttcap_command=ttcap_command,
        ),
        "!",
        "vgadecode",
        "name=dec",
        *decode_properties(fps),
        "!",
        "tee",
        "name=t",
    ]
    for branch in branches:
        argv.extend(branch)
    return argv, outputs


def format_pipeline(argv: list[str]) -> str:
    """`argv` as a shell command, broken at the pipeline's own joints.

    One line per element chain, which is how a pipeline is read, and every
    token quoted for the shell, which is what makes it paste-able: the
    `ttcap-command` property in particular is several words and has to
    survive as one.
    """
    lines: list[str] = []
    current: list[str] = []
    for token in argv:
        if token == "t." and current:
            lines.append(_join(current))
            current = [token]
            continue
        current.append(token)
    if current:
        lines.append(_join(current))
    return " \\\n    ".join(lines)


def _join(tokens: list[str]) -> str:
    """`shlex.join`, but leaving the pipeline's `!` separators bare.

    `shlex.quote` quotes `!` because an interactive bash would treat it as
    history expansion; a `!` with a space after it never is, and every
    GStreamer pipeline ever written has bare ones. Quoting them would make
    the printed line correct and unreadable at the same time.
    """
    return " ".join("!" if t == "!" else shlex.quote(t) for t in tokens)


# ------------------------------------------------------------ progress


#: `gst-launch-1.0 -m` prints one line per bus message. Two of them matter.
TIMING_RE = re.compile(r'\(element\): vgacap-timing, (?P<fields>.*);\s*$')
MULTIFILE_RE = re.compile(r"\(element\): GstMultiFileSink, filename=\(string\)")
FIELD_RE = re.compile(r'(?P<name>[\w-]+)=\((?P<type>[\w]+)\)(?P<value>"[^"]*"|[^,;]+)')


def parse_timing(line: str) -> dict[str, str] | None:
    """The `vgacap-timing` element message as a plain dict, or None."""
    match = TIMING_RE.search(line)
    if not match:
        return None
    fields = {}
    for field_match in FIELD_RE.finditer(match.group("fields")):
        value = field_match.group("value").strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        # gst escapes the `@` of a mode name when it quotes the string.
        fields[field_match.group("name")] = value.replace("\\", "")
    return fields


def describe_timing(fields: dict[str, str]) -> str:
    return "mode %s: %s clocks/line, %s lines/frame, hsync %s, vsync %s, glitches %s" % (
        fields.get("mode") or "(unrecognised)",
        fields.get("clocks-per-line", "?"),
        fields.get("lines-per-frame", "?"),
        "positive" if fields.get("hsync-positive") == "true" else "negative",
        "positive" if fields.get("vsync-positive") == "true" else "negative",
        fields.get("glitches", "?"),
    )


class Progress:
    """What the run says while it is running.

    Frames are counted from `multifilesink post-messages=true`, which posts
    one element message per file written -- so the count is of files that
    exist, not of buffers that went past. With `--no-png` there is no such
    sink and no count; the mode and the summary still arrive.
    """

    def __init__(self, stream=None, interval: float = 1.0) -> None:
        self.stream = sys.stderr if stream is None else stream
        self.interval = interval
        self.frames = 0
        self.timing: dict[str, str] | None = None
        self._last = 0.0
        self._started = time.monotonic()

    def say(self, text: str) -> None:
        print(text, file=self.stream, flush=True)

    def line(self, line: str) -> None:
        """One line of `gst-launch -m` output."""
        timing = parse_timing(line)
        if timing is not None:
            self.timing = timing
            self.say("  " + describe_timing(timing))
            return
        if MULTIFILE_RE.search(line):
            self.frames += 1
            now = time.monotonic()
            if now - self._last >= self.interval:
                self._last = now
                self.say(
                    "  %d frame(s), %.0fs elapsed" % (self.frames, now - self._started)
                )

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started


# ------------------------------------------------------------- the run


def line_buffered(argv: list[str]) -> list[str]:
    """`argv`, run so its bus messages arrive as they happen.

    `gst-launch-1.0 -m` prints with C stdio, which block-buffers when stdout
    is a pipe -- and a pipeline whose frames are seven seconds apart (a
    60 kHz project clock) would then report nothing for minutes at a time.
    `stdbuf -oL` is the one-word fix; where it does not exist the messages
    still arrive, just in lumps, which is better than not running.

    It is kept out of `DemoPlan.argv` on purpose: the printed pipeline is
    the pipeline, and how this process reads the child's stdout is not part
    of it.
    """
    stdbuf = shutil.which("stdbuf")
    return [stdbuf, "-oL", *argv] if stdbuf else list(argv)


def _summarise(plan: DemoPlan, progress: Progress, returncode: int,
               server: MjpegServer | None = None) -> None:
    """What the run produced, counted from the files themselves.

    The PNGs are counted on disk rather than from the bus, so a sink that
    stopped writing halfway through cannot be summarised as a success.
    """
    progress.say("")
    progress.say("capture finished after %.1fs (gst-launch exit %d)"
                 % (progress.elapsed, returncode))
    if progress.timing is not None:
        progress.say("  " + describe_timing(progress.timing))
    if plan.png:
        pngs = sorted(plan.outdir.glob("frame-*.png"))
        progress.say("  %d png(s) in %s" % (len(pngs), plan.outdir))
    if plan.video and plan.video_path is not None:
        if plan.video_path.exists():
            progress.say(
                "  %s, %.1f KiB" % (plan.video_path, plan.video_path.stat().st_size / 1024)
            )
        else:
            progress.say("  %s was not written" % plan.video_path)
    if server is not None:
        progress.say("  %d frame(s) published on port %s"
                     % (server.broadcaster.parts, plan.serve_port))


def run_demo(
    plan: DemoPlan,
    *,
    env: dict[str, str] | None = None,
    progress: Progress | None = None,
    server: MjpegServer | None = None,
) -> int:
    """Run the pipeline, report as it goes, and stop it cleanly on SIGINT.

    The pipeline runs in a session of its own, so the only interrupt it gets
    is the one forwarded here. That matters: `gst-launch` treats a *second*
    interrupt as "stop now", which truncates the Matroska file, and a Ctrl-C
    typed at a terminal reaches every process in the foreground group at
    once. One signal in, one signal out.
    """
    progress = progress or Progress()
    pass_fds = () if plan.mjpeg_fd is None else (plan.mjpeg_fd,)
    child = subprocess.Popen(
        line_buffered(plan.argv),
        stdout=subprocess.PIPE,
        stderr=None,  # the pipeline's own errors go straight to the terminal
        text=True,
        bufsize=1,
        env=env,
        pass_fds=pass_fds,
        start_new_session=True,
    )
    if plan.mjpeg_fd is not None:
        os.close(plan.mjpeg_fd)  # the child holds the only writing end now

    interrupts = {"count": 0}

    def on_interrupt(signum, frame):  # noqa: ARG001
        interrupts["count"] += 1
        if interrupts["count"] == 1:
            progress.say(
                "\ninterrupted: asking the pipeline to end the stream. The board "
                "needs one DMA buffer to wind down, so give it a moment."
            )
        else:
            progress.say("\ninterrupted again: stopping now")
        try:
            child.send_signal(signal.SIGINT)
        except ProcessLookupError:  # pragma: no cover - it already finished
            pass

    previous = None
    try:
        previous = signal.signal(signal.SIGINT, on_interrupt)
    except ValueError:  # pragma: no cover - not the main thread
        pass
    try:
        assert child.stdout is not None
        for line in child.stdout:
            progress.line(line.rstrip("\n"))
        returncode = child.wait()
    finally:
        if previous is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(signal.SIGINT, previous)
        if server is not None:
            server.close()
    _summarise(plan, progress, returncode, server)
    if returncode != 0 and plan.link.through_bridge:
        progress.say("")
        progress.say(tunnel_hint_for(plan.link))
    return returncode


def tunnel_hint_for(link: Link) -> str:
    """The tunnel hint for whichever Welland board `link` names.

    Looked up from the URL rather than carried along, because `Link` is
    about where the capture goes and only this one message is about how the
    operator's own machine gets there.
    """
    for board in WELLAND:
        if bridge_ws_url(board) == link.url:
            return tunnel_hint(board)
    return ""  # pragma: no cover - through_bridge is only set for a WELLAND url


# -------------------------------------------------------------- the command


#: The descriptor `--dry-run` shows for the MJPEG branch. The real run uses
#: whatever `os.pipe()` hands out, and prints that number; a dry run has no
#: pipe to name and must not open one, so it shows the first free descriptor
#: a process normally has.
DRY_RUN_MJPEG_FD = 3


def plan_demo(
    args: argparse.Namespace, *, dry_run: bool = False
) -> tuple[DemoPlan, MjpegServer | None]:
    """Turn parsed arguments into a pipeline, without starting anything.

    A dry run opens no pipe, binds no port and creates no directory: it only
    needs the text. The MJPEG branch is shown against `DRY_RUN_MJPEG_FD`,
    since the descriptor a real run passes is not decided until there is one.
    """
    # The board first, because a slug that is not on the bench is a typo the
    # person can fix without installing anything.
    link = resolve_link(args.board, args.link)
    if shutil.which("gst-launch-1.0") is None:
        raise CaptureError(
            "cannot find gst-launch-1.0, which is what runs the pipeline: "
            "install GStreamer's tools (gstreamer1.0-tools on Debian and "
            "Raspbian) along with gstreamer1.0-plugins-good, and make sure "
            "GST_PLUGIN_PATH names the directory holding libgstvgacap.so"
        )
    outdir = pathlib.Path(args.outdir)

    server: MjpegServer | None = None
    mjpeg_fd: int | None = None
    read_fd: int | None = None
    if args.serve is not None:
        if dry_run:
            mjpeg_fd = DRY_RUN_MJPEG_FD
        else:
            read_fd, mjpeg_fd = os.pipe()
            os.set_inheritable(mjpeg_fd, True)

    try:
        argv, outputs = build_pipeline(
            link.url,
            outdir,
            clock_hz=args.clock_hz,
            seconds=args.seconds,
            project=args.project,
            design=args.design,
            profile=args.profile,
            buf_words=args.buf_words,
            ttcap_command=args.ttcap_command,
            png=args.png,
            video=args.video,
            video_encoder=args.video_encoder,
            window=args.window,
            mjpeg_fd=mjpeg_fd,
            fps=parse_fps(args.fps) if args.fps else None,
        )
    except BaseException:
        if not dry_run:
            for fd in (read_fd, mjpeg_fd):
                if fd is not None:
                    os.close(fd)
        raise

    plan = DemoPlan(
        link=link,
        argv=argv,
        outdir=outdir,
        png=args.png,
        video=args.video,
        video_path=outdir / VIDEO_FILENAME if args.video else None,
        window=args.window,
        serve_port=args.serve,
        mjpeg_fd=mjpeg_fd,
        outputs=outputs,
    )
    if args.serve is not None and not dry_run:
        assert read_fd is not None
        broadcaster = FrameBroadcaster()
        server = MjpegServer(args.serve, broadcaster)
        threading.Thread(
            target=pump, args=(read_fd, broadcaster), name="vgacap-mjpeg-pump",
            daemon=True,
        ).start()
    return plan, server


def demo(args: argparse.Namespace) -> int:
    """`ttcap demo`, from arguments to an exit code."""
    plan, server = plan_demo(args, dry_run=args.dry_run)
    pipeline = format_pipeline(plan.argv)

    if args.dry_run:
        # stdout, so it can be piped into a shell; everything else this
        # command says is on stderr.
        print(pipeline)
        return 0

    progress = Progress()
    progress.say("board link: %s (%s)" % (plan.link.url, plan.link.why))
    for note in plan.link.notes:
        progress.say("  note: %s" % note)
    plan.outdir.mkdir(parents=True, exist_ok=True)
    for output in plan.outputs:
        progress.say("writing %s" % output)
    if server is not None:
        server.start()
        progress.say("serving %s -- open it in a browser" % server.url)
    progress.say("")
    progress.say(pipeline)
    progress.say("")

    returncode = run_demo(plan, progress=progress, server=server)
    # A pipeline killed by a signal comes back as -N, which an exit status
    # cannot carry; the shell convention is 128+N and it is what a caller
    # will be reading.
    return returncode if returncode >= 0 else 128 - returncode


def for_help(text: str) -> str:
    """`text` as an argparse help string, with its per cents kept literal.

    argparse runs every help string through `% params` so that `%(default)s`
    works, which means a `%` that is part of the *text* is read as a format
    placeholder: `frame-%04d.png` in a help string made
    `ttcap demo --help` die with `TypeError: %d format: a real number is
    required, not dict`, and the first thing anyone types is `--help`.

    Applied to values interpolated into help, not to the help string's own
    `%(default)s` -- those are placeholders and are meant to be expanded.
    """
    return text.replace("%", "%%")


def add_parser(subparsers) -> argparse.ArgumentParser:
    """Register `demo` on `ttcap`'s subparsers."""
    parser = subparsers.add_parser(
        "demo",
        help="capture a board and write PNGs, video, a window and a browser stream",
        description=(
            "Point the capture pipeline at a Tiny Tapeout board and produce "
            "video. One capture feeds every output that is switched on."
        ),
        # An epilog is only interpolated when it contains `%(prog)`, so it is
        # *not* escaped the way a help string is -- doubling a per cent here
        # would print it doubled. Neither value below carries one.
        epilog=(
            "Welland boards (%s) are reached through the fpgas.online bridge, "
            "which is on the bench network; from a workstation open an SSH "
            "tunnel to it and pass the near end as --link. On the board's own "
            "Pi the demo uses %s instead, and the fpgas.online bridge has to "
            "be stopped first (sudo systemctl stop fpgas-tt)."
            % (board_choices(), PI_SERIAL_LINK)
        ),
    )
    parser.add_argument(
        "--board", help=for_help("Welland board slug: %s" % board_choices())
    )
    parser.add_argument(
        "--link",
        help="reach the board this way instead: serial:/dev/ttyACM0, or "
        "ws://host:8765/serial (the near end of an SSH tunnel, usually)",
    )
    parser.add_argument(
        "--project", help="tt.shuttle macro to enable, e.g. tt_um_rejunity_vga"
    )
    parser.add_argument(
        "--design", help="FPGA boards: bitstream to enable, via the same tt.shuttle"
    )
    parser.add_argument(
        "--clock-hz", type=int, required=True, help="project clock to program"
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=10.0,
        help="how long to capture; 0 runs until Ctrl-C (default 10)",
    )
    parser.add_argument(
        "--outdir", required=True, help="where the PNGs and the video go"
    )
    parser.add_argument(
        "--window", action="store_true", help="also show the capture in a window"
    )
    parser.add_argument(
        "--serve",
        type=int,
        metavar="PORT",
        help="also publish an MJPEG stream at http://localhost:PORT/",
    )
    parser.add_argument(
        "--fps",
        metavar="RATE",
        help="re-time the video to RATE (30, 30/1 or 29.97), repeating the "
        "last frame to fill the gaps. Without it frames keep project time, "
        "which at a 60 kHz project clock is one frame every 7 seconds",
    )
    parser.add_argument(
        "--no-png",
        dest="png",
        action="store_false",
        help=for_help("do not write %s" % PNG_PATTERN),
    )
    parser.add_argument(
        "--no-video",
        dest="video",
        action="store_false",
        help=for_help("do not write %s" % VIDEO_FILENAME),
    )
    parser.add_argument(
        "--video-encoder",
        choices=["auto", *(name for name, _ in VIDEO_ENCODERS)],
        default="auto",
        help=for_help(
            "encoder for %s; auto takes the first one installed (default)"
            % VIDEO_FILENAME
        ),
    )
    parser.add_argument(
        "--profile",
        default="auto",
        help="board profile; auto asks the board for its GPIOMap (default)",
    )
    parser.add_argument(
        "--buf-words", type=int, help="words per DMA buffer; also the stop latency"
    )
    parser.add_argument(
        "--ttcap-command",
        default=DEFAULT_TTCAP_COMMAND,
        help=for_help(
            "the command vgacapttsrc runs to capture (default %r)"
            % DEFAULT_TTCAP_COMMAND
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the pipeline and exit, touching no board and no file",
    )
    return parser
