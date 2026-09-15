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
import atexit
import contextlib
import functools
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


#: Where a tunnel's near end is put, per board: `TUNNEL_PORT_BASE + octet`.
#:
#: Not the octet on its own, which is what this used to suggest. Every
#: Welland slug has an octet of 3-8 or 33-36, so every board's headline
#: command was `ssh -N -L 7:...` -- a privileged local port, which ssh
#: refuses for an ordinary user with "Privileged ports can only be forwarded
#: by root." A hint whose first command cannot be run is worse than none.
#: The base keeps one port per board, so two boards can be tunnelled at once.
TUNNEL_PORT_BASE = 18000


def tunnel_port(board: str) -> int:
    """The local port `tunnel_hint` suggests for `board`."""
    return TUNNEL_PORT_BASE + int(welland_octet(board))


def tunnel_hint(board: str) -> str:
    """How to reach `board`'s bridge from a workstation, in full.

    The bench network is not routed off the bench, so the daemon at
    10.21.2.x:8765 is reachable only through the gateway. This is printed
    when a capture over a bridge link fails, because "connection refused" on
    its own sends people to the board.
    """
    port = tunnel_port(board)
    return (
        "%s is on the Welland bench network, which a workstation reaches only\n"
        "through an SSH tunnel:\n"
        "\n"
        "    ssh -N -L %d:%s:8765 %s\n"
        "\n"
        "then run the demo again against the near end of it:\n"
        "\n"
        "    ttcap demo --link ws://127.0.0.1:%d/serial ...\n"
        "\n"
        "(any free local port works; %d is just this board's. A port below\n"
        " 1024 is not free -- ssh refuses to forward one without root.)"
        % (
            bridge_ws_url(board),
            port,
            bridge_host(board),
            WELLAND_GATEWAY,
            port,
            port,
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


#: The project-clock rate each demo board has been measured to keep up with
#: cleanly, and what to call that board in a sentence:
#: `docs/research/2026-09-15-micropython-capture-rate.md`, the same two
#: numbers the stop-latency notes quote throughout. The sweep measured tt07
#: and fpga-1; every other slug inherits its number by sharing a profile,
#: which is why the warning names the *demo board* and not the slug -- only
#: one slug per family was actually on the bench.
#:
#: Keyed on the board *profile*, unlike `board_takes_a_design` above -- and
#: for the opposite reason. What was measured is the MicroPython host loop
#: and the USB path of the demo board's own MCU, which is exactly what the
#: profile describes; ASIC-versus-FPGA is a property of what is plugged into
#: it, which the profile only appears to know.
CLOCK_CEILINGS = {
    "rp2040-tt06map": (60_000, "an RP2040 demo board"),
    "rp2350-dbv3": (750_000, "an RP2350 demo board"),
}


def clock_ceiling(board: str | None) -> tuple[int, str] | None:
    """The clean project-clock ceiling for `board`, and what to call it.

    Only `--board` can answer this: behind a `--link` there is no telling
    which demo board is on the other end, and a warning about the wrong
    ceiling is worse than none.
    """
    if board is None or board not in WELLAND:
        return None
    _, profile = WELLAND[board]
    return CLOCK_CEILINGS.get(profile.name)


#: Welland slugs that carry an FPGA rather than a shuttle ASIC.
#:
#: Keyed on the slug, not on `WELLAND[board]`'s profile. The profile says
#: which *demo board* is underneath (RP2040 or RP2350), which happens to
#: line up today but is not the same question: a future ASIC shuttle on an
#: RP2350 demo board would make the profile say "FPGA" about a chip.
FPGA_SLUG_PREFIX = "fpga-"


def board_takes_a_design(board: str) -> bool:
    """True when `board` is one of the bench's FPGA boards.

    An FPGA board is loaded with a bitstream (`--design`); an ASIC shuttle
    carries fixed macros and is asked for one by name (`--project`). Both go
    through the same `tt.shuttle`, which is why the wrong one is accepted all
    the way to the board before anything complains.
    """
    return board.startswith(FPGA_SLUG_PREFIX)


def fpga_slugs() -> str:
    return ", ".join(sorted(s for s in WELLAND if s.startswith(FPGA_SLUG_PREFIX)))


def asic_slugs() -> str:
    return ", ".join(sorted(s for s in WELLAND if not s.startswith(FPGA_SLUG_PREFIX)))


def check_board_wants(board: str | None, project: str | None, design: str | None) -> None:
    """Refuse `--project` on an FPGA board, and `--design` on an ASIC.

    Both reach `tt.shuttle` and both fail there -- as a MicroPython traceback
    out of `select_project`, several GStreamer ERROR blocks deep, after the
    board has been opened and a capture set up. The table already knows which
    kind of board a slug is, so the answer is available before anything is
    contacted.

    Only when `--board` names a bench slug: with `--link` alone there is no
    way to know what is on the other end, and guessing would refuse a
    perfectly good run.
    """
    if board is None or board not in WELLAND:
        return
    if design and not board_takes_a_design(board):
        raise CaptureError(
            "--design is for the FPGA boards (%s); %s is an ASIC shuttle, so "
            "name the macro with --project instead" % (fpga_slugs(), board)
        )
    if project and board_takes_a_design(board):
        raise CaptureError(
            "--project is for the ASIC shuttles (%s); %s is an FPGA board, so "
            "name the bitstream with --design instead" % (asic_slugs(), board)
        )


# ------------------------------------------------------------- the numbers


#: What each numeric flag will take: (low, high, what it is).
#:
#: The bounds are `vgacapttsrc`'s own property ranges, because a value
#: outside them is refused by GObject with a `CRITICAL` on stderr and then
#: *ignored* -- which for `--seconds -5` meant the property kept its default
#: of 0, and 0 means "capture until stopped". A typo therefore started an
#: unbounded capture on a shared bench board, which is the worst way for a
#: number to be wrong.
#:
#: `-1` is the element's "use ttcap's default" sentinel for `buf-words`;
#: this command says that by leaving the flag out, so the sentinel is not in
#: the range a person may type.
NUMBER_RANGES = {
    "seconds": (0.0, 86400.0, "a duration in seconds", "0 captures until stopped"),
    "clock_hz": (1, 200_000_000, "a project clock in Hz", ""),
    "buf_words": (1, 1 << 24, "words per DMA buffer", "omit it for ttcap's default"),
    "serve": (1, 65535, "a TCP port", ""),
}


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def check_numbers(args: argparse.Namespace) -> list[str]:
    """Refuse a numeric argument the pipeline would silently ignore.

    Returns the warnings worth printing -- things that are legal and
    probably not meant, which is a different thing from an error and is
    treated as one. Raises `CaptureError` for the values that are simply
    wrong.
    """
    for name, (low, high, what, note) in NUMBER_RANGES.items():
        value = getattr(args, name, None)
        if value is None:
            continue
        if not low <= value <= high:
            raise CaptureError(
                "%s %s is out of range: %s takes %s between %s and %s%s"
                % (_flag(name), _number(value), _flag(name), what,
                   _number(low), _number(high),
                   " (%s)" % note if note else "")
            )

    # `--fps` validates itself, but it has to happen *here*, with the other
    # numbers, rather than where the pipeline is built: that is past the
    # GStreamer check, and a mistyped frame rate should not need a plugin
    # installed before anyone will say so.
    if getattr(args, "fps", None) is not None:
        parse_fps(args.fps)

    warnings = []
    measured = clock_ceiling(args.board)
    if measured is not None and args.clock_hz > measured[0]:
        # A warning and not an error on purpose: watching the board overrun
        # is a legitimate thing to want, and it is how the ceilings were
        # measured in the first place.
        ceiling, board_name = measured
        warnings.append(
            "--clock-hz %d is above the %d Hz %s has been measured to keep up "
            "with cleanly at the default buffer size; expect overruns and "
            "dropped samples. That is allowed -- the closing TIME chunk "
            "reports them -- and a larger --buf-words moves the ceiling."
            % (args.clock_hz, ceiling, board_name)
        )
    return warnings


# ------------------------------------------------------------- the outdir


#: What a run writes into `--outdir`, as globs. Used to find a previous
#: run's leftovers, and to count what this one produced.
OUTPUT_GLOBS = ("frame-*.png", VIDEO_FILENAME)


def existing_outputs(outdir: pathlib.Path) -> list[pathlib.Path]:
    """Files in `outdir` that a previous run of this command left there."""
    if not outdir.is_dir():
        return []
    found: list[pathlib.Path] = []
    for pattern in OUTPUT_GLOBS:
        found.extend(sorted(outdir.glob(pattern)))
    return found


def prepare_outdir(outdir: pathlib.Path, force: bool = False) -> None:
    """Make `--outdir`, and make sure it is this run's alone.

    A capture writes `frame-0000.png` upwards, so a short run into a
    directory holding a long one overwrites the first few frames and leaves
    the rest: a silently mixed set, some of them a different design, which is
    the worst possible thing to find in `docs/results/` later. It used to
    happen without a word.

    So a directory that already holds outputs is refused, and `--force`
    clears them first rather than blending into them. Anything else in the
    directory is left alone -- it is not ours to delete.
    """
    # `lexists`, not `exists`: a symlink pointing nowhere is not a directory
    # and never will be, but `exists()` follows it and answers False, which
    # sent it to `mkdir` and back out as a raw FileExistsError.
    if os.path.lexists(outdir) and not outdir.is_dir():
        raise CaptureError(
            "--outdir %s is not a directory; pass a directory to write the "
            "frames and the video into" % outdir
        )
    stale = existing_outputs(outdir)
    if stale and not force:
        raise CaptureError(
            "--outdir %s already holds %d file(s) from a previous run (%s%s). "
            "A shorter capture would overwrite the first frames and leave the "
            "rest, mixing two runs together. Pass --force to clear them, or "
            "give a fresh --outdir."
            % (
                outdir,
                len(stale),
                ", ".join(p.name for p in stale[:3]),
                ", ..." if len(stale) > 3 else "",
            )
        )
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        for path in stale:
            # A *directory* named frame-0000.png is not something this made
            # and not something it will remove; `unlink` on one raises
            # IsADirectoryError, which is the class of message this whole
            # function exists to replace.
            if path.is_dir():
                raise CaptureError(
                    "--outdir %s holds a directory called %s, which is where a "
                    "frame would go; move it aside, or use a different --outdir"
                    % (outdir, path.name)
                )
            path.unlink()
    except OSError as exc:
        raise CaptureError(
            "--outdir %s could not be prepared: %s" % (outdir, exc.strerror or exc)
        ) from None


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
    would have typed -- and `1234567`, never `1.23457e+06`.

    `%g` gave the scientific form above six significant figures, which is
    both lossy and not a number `gst-launch` would read back the same way:
    the printed pipeline has to be the pipeline. Whole values print as
    integers and the rest keep `repr`'s round-trip guarantee.
    """
    if isinstance(value, int) or float(value).is_integer():
        return "%d" % int(value)
    return repr(float(value))


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


#: `demo()`'s exit code for a run that finished without producing anything.
#: The same 3 `ttcap capture` uses for "no samples at all" -- the same
#: verdict, one layer up, and the one failure worth telling apart from a
#: board or link error.
EXIT_NOTHING_CAPTURED = 3


@dataclass
class Outcome:
    """What the evidence says the run actually did.

    Three separate questions, which used to be one boolean and were wrong in
    both directions because of it:

    * `made` -- an output exists that somebody can open. Any one is proof.
    * `conclusive` -- a *file* output was asked for. Only then does emptiness
      settle the matter: `--window` has nothing to count at all, and
      `--serve`'s count is one part behind by construction, since a part is
      not published until the next boundary arrives. Reading either zero as
      "nothing was captured" is how a working window-only run came to exit 3.
    * `saw_frames` -- the decoder reported a detected mode or a written
      frame on the bus. That is evidence the capture worked even when
      nothing reached the disk, and it is what a run with nothing countable
      is judged on.
    """

    made: bool = False
    conclusive: bool = False
    saw_frames: bool = False

    @property
    def nothing_captured(self) -> bool:
        """True only for a run that genuinely produced no frames.

        `EXIT_NOTHING_CAPTURED` means "the sampler never saw a clock edge",
        and it must not be reachable by a run that captured perfectly well
        into an output this function cannot count.
        """
        if self.conclusive:
            return not self.made
        return not (self.made or self.saw_frames)


def _summarise(plan: DemoPlan, progress: Progress, returncode: int,
               server: MjpegServer | None = None) -> Outcome:
    """Say what the run produced, and judge whether it produced anything.

    The outputs are counted from the files themselves, so a sink that
    stopped writing halfway through cannot be summarised as a success --
    and only files *this* run wrote are counted, because `prepare_outdir`
    cleared the directory of any others before it started. Counting a
    previous run's frames as this one's was how a run that wrote nothing at
    all came to report "6 png(s)" and exit 0.
    """
    outcome = Outcome(
        conclusive=bool(plan.png or plan.video),
        saw_frames=progress.timing is not None or progress.frames > 0,
    )
    progress.say("")
    progress.say("capture %s after %.1fs (gst-launch exit %d)"
                 % ("finished" if returncode == 0 else "failed",
                    progress.elapsed, returncode))
    if progress.timing is not None:
        progress.say("  " + describe_timing(progress.timing))
    if plan.png:
        pngs = sorted(plan.outdir.glob("frame-*.png"))
        outcome.made = outcome.made or bool(pngs)
        progress.say("  %d png(s) in %s" % (len(pngs), plan.outdir))
        # The bus said one thing, the directory another: a sink that failed
        # part way, or something else writing into the same names.
        if len(pngs) != progress.frames:
            progress.say(
                "  (multifilesink reported %d file(s); %d are on disk)"
                % (progress.frames, len(pngs))
            )
    if plan.video and plan.video_path is not None:
        size = plan.video_path.stat().st_size if plan.video_path.exists() else 0
        if size > 0:
            outcome.made = True
            progress.say("  %s, %.1f KiB" % (plan.video_path, size / 1024))
        elif plan.video_path.exists():
            progress.say("  %s is empty" % plan.video_path)
        else:
            progress.say("  %s was not written" % plan.video_path)
    if plan.window:
        # Nothing to count: the frames went to a screen. Whether they
        # arrived is the bus's story, not this function's.
        progress.say("  a window (autovideosink)")
    if server is not None:
        parts = server.broadcaster.parts
        outcome.made = outcome.made or bool(parts)
        progress.say("  %d frame(s) published on port %s" % (parts, plan.serve_port))
    return outcome


#: The signals that mean "this run is over": Ctrl-C, Ctrl-\, `kill`, and the
#: terminal going away. All of them are forwarded to the pipeline as SIGINT,
#: which is the only one `gst-launch -e` turns into an end-of-stream, so a
#: `kill` winds the board down and finalises the video exactly as Ctrl-C
#: does. Handling only SIGINT is what used to leave a capture running on a
#: shared bench board with no terminal left to stop it.
#:
#: Every default-fatal signal a person or a supervisor sends on purpose is
#: here. SIGKILL is the one that cannot be: nothing in this process runs
#: after it, which is why the teardown also has to be something the board
#: can survive on its own.
STOP_SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")

#: How long the last-resort teardown gives each rung of the ladder.
TEARDOWN_GRACE = 10.0


def stop_child_group(child: subprocess.Popen, grace: float = TEARDOWN_GRACE,
                     say=None) -> None:
    """Make sure `child` and everything it started are gone.

    The pipeline is its own session, so `ttcap` and whatever `uv run` put
    between them are all in one process group that `killpg` reaches -- which
    is the point of the session, and the reason signalling `child.pid` alone
    is not enough.

    Three rungs, because the first two are how the board gets to wind down:
    SIGINT is `gst-launch -e`'s end-of-stream, SIGTERM is its blunter cousin,
    and SIGKILL is what is left. A run that ended normally takes none of
    them.

    This is the backstop, not the normal path: it runs from `run_demo`'s
    `finally` and again from an `atexit` hook, so an exception, a `kill`, or
    a `sys.exit` from anywhere still tears the capture down. Nothing can
    cover a SIGKILL of this process itself.
    """
    if child.poll() is not None:
        return
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(child.pid), number)
        except (ProcessLookupError, PermissionError, OSError):
            return  # already gone, or never ours to signal
        if say is not None and number is not signal.SIGINT:
            say("stopping the pipeline with %s" % signal.Signals(number).name)
        try:
            child.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue
    with contextlib.suppress(Exception):  # pragma: no cover - after SIGKILL
        child.wait(timeout=grace)


def run_demo(
    plan: DemoPlan,
    *,
    env: dict[str, str] | None = None,
    progress: Progress | None = None,
    server: MjpegServer | None = None,
) -> int:
    """Run the pipeline, report as it goes, and always stop it cleanly.

    The pipeline runs in a session of its own, so the only signal it gets is
    the one forwarded here. That matters: `gst-launch` treats a *second*
    interrupt as "stop now", which truncates the Matroska file, and a Ctrl-C
    typed at a terminal reaches every process in the foreground group at
    once. One signal in, one signal out.

    The cost of that session is that the child hears nothing this process
    does not tell it, so every way this run can end has to end the child too:
    `STOP_SIGNALS` are forwarded, and `stop_child_group` runs from a
    `finally` and from an `atexit` hook. Without those a `kill`, a closed
    terminal or an OOM kill left `ttcap` holding a shared bench board with
    nothing left to signal it.
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

    at_exit = functools.partial(stop_child_group, child)
    atexit.register(at_exit)
    interrupts = {"count": 0}

    def on_stop_signal(signum, frame):  # noqa: ARG001
        """Forward the stop, escalating if it is repeated.

        The first two go to `gst-launch` as SIGINT: one is its end-of-stream,
        and its own handling of a second still finalises the Matroska file.
        Only a third and a fourth escalate, and they go to the *group*, so a
        wedged pipeline cannot keep the board. A caller that has asked three
        times is not asking for the video any more.
        """
        interrupts["count"] += 1
        count = interrupts["count"]
        name = signal.Signals(signum).name
        try:
            if count == 1:
                progress.say(
                    "\n%s: asking the pipeline to end the stream. The board "
                    "needs one DMA buffer to wind down, so give it a moment."
                    % name
                )
                child.send_signal(signal.SIGINT)
            elif count == 2:
                progress.say("\n%s again: stopping now" % name)
                child.send_signal(signal.SIGINT)
            elif count == 3:
                progress.say("\n%s again: terminating the pipeline" % name)
                os.killpg(os.getpgid(child.pid), signal.SIGTERM)
            else:
                progress.say("\n%s again: killing the pipeline" % name)
                os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass  # it already finished, or is no longer ours to signal

    previous: dict[int, object] = {}
    for name in STOP_SIGNALS:
        number = getattr(signal, name, None)
        if number is None:  # pragma: no cover - SIGHUP is POSIX-only
            continue
        try:
            previous[number] = signal.signal(number, on_stop_signal)
        except (ValueError, OSError):  # pragma: no cover - not the main thread
            pass
    try:
        assert child.stdout is not None
        for line in child.stdout:
            progress.line(line.rstrip("\n"))
        returncode = child.wait()
    finally:
        # The child first: restoring a handler before the thing it guards is
        # gone would leave a window with neither.
        stop_child_group(child, say=progress.say)
        atexit.unregister(at_exit)
        for number, handler in previous.items():
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(number, handler)
        if server is not None:
            server.close()
    outcome = _summarise(plan, progress, returncode, server)
    if returncode != 0 and plan.link.through_bridge:
        progress.say("")
        progress.say(tunnel_hint_for(plan.link))
    if returncode == 0 and outcome.nothing_captured:
        # Exit 0 on a run that captured nothing is the one lie this command
        # must not tell: the pipeline ran, so gst-launch is content, but
        # there is no capture. The diagnosis differs by how far it got --
        # a detected mode means the samples arrived and only the frames did
        # not, which is a different thing to go and check.
        progress.say("")
        progress.say(
            "nothing was captured: the board's sync was detected but no "
            "complete frame closed. A frame is only emitted at the boundary "
            "that closes it, so capture at least three frame periods."
            if outcome.saw_frames else
            "nothing was captured: the pipeline ran but produced no frames. "
            "Check that the design is driving the Tiny VGA Pmod, and that "
            "--clock-hz is a rate the link can keep up with."
        )
        return EXIT_NOTHING_CAPTURED
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

#: The plugin's own elements. Checked by name before the pipeline is built,
#: because `gst-launch` says only `erroneous pipeline: no element
#: "vgacapttsrc"` and then exits 1 through the ordinary "capture finished"
#: path -- the likeliest setup mistake with the least useful message.
PLUGIN_ELEMENTS = ("vgacapttsrc", "vgadecode")


def check_gstreamer(required: bool = True) -> None:
    """Refuse early when GStreamer or this plugin is not installed.

    `required=False` downgrades it to a warning, which is what `--dry-run`
    wants: printing a pipeline to run on the Pi from a workstation that has
    no plugin is a perfectly good reason to ask, and a dry run cannot fail
    on a missing element because it does not run anything. The warning is
    still worth having, since the far more common reason is that the plugin
    was never built.
    """
    try:
        _require_gstreamer()
    except CaptureError:
        if required:
            raise
        print("warning: %s" % sys.exc_info()[1], file=sys.stderr)


def _require_gstreamer() -> None:
    if shutil.which("gst-launch-1.0") is None:
        raise CaptureError(
            "cannot find gst-launch-1.0, which is what runs the pipeline: "
            "install GStreamer's tools (gstreamer1.0-tools on Debian and "
            "Raspbian) along with gstreamer1.0-plugins-good, and make sure "
            "GST_PLUGIN_PATH names the directory holding libgstvgacap.so"
        )
    missing = [name for name in PLUGIN_ELEMENTS if not have_element(name)]
    if missing:
        raise CaptureError(
            "GStreamer has no %s element: that is this repository's own "
            "plugin, libgstvgacap.so, and GStreamer cannot see it. Build it "
            "(cmake -S . -B build && cmake --build build) and point "
            "GST_PLUGIN_PATH at the directory holding it "
            "(export GST_PLUGIN_PATH=$PWD/build), or install it alongside "
            "GStreamer's own plugins. GST_PLUGIN_PATH is currently %s."
            % (
                " or ".join(missing),
                repr(os.environ["GST_PLUGIN_PATH"])
                if os.environ.get("GST_PLUGIN_PATH")
                else "unset",
            )
        )


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
    check_board_wants(args.board, args.project, args.design)
    # Before the GStreamer check: a mistyped number is the person's to fix
    # and should not wait on anything being installed.
    for warning in check_numbers(args):
        print("warning: %s" % warning, file=sys.stderr)
    check_gstreamer(required=not dry_run)
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

    def close_the_pipe() -> None:
        if dry_run:
            return
        for fd in (read_fd, mjpeg_fd):
            if fd is not None:
                os.close(fd)

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
            # `is not None`, not truthiness: `--fps ''` used to be silently
            # dropped, which is the same "ignored rather than refused" that
            # made a bad --seconds dangerous.
            fps=parse_fps(args.fps) if args.fps is not None else None,
        )
    except BaseException:
        close_the_pipe()
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
        try:
            server = MjpegServer(args.serve, broadcaster)
        except OSError as exc:
            # The one message in this command that used to arrive as a bare
            # errno: "Address already in use" on a line that also carries an
            # --outdir and a --link gives three candidates for which of them
            # was refused. Also the one place the careful pipe cleanup above
            # did not reach, since the bind happens after it.
            close_the_pipe()
            raise CaptureError(
                "--serve %d: %s. %s"
                % (
                    args.serve,
                    exc.strerror or exc,
                    "Ports below 1024 need root; pick one above that."
                    if args.serve < 1024
                    else "Pick another port, or stop whatever is using this one.",
                )
            ) from None
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
    stale = existing_outputs(plan.outdir)
    prepare_outdir(plan.outdir, args.force)
    if stale:
        progress.say("--force: removed %d file(s) from a previous run in %s"
                     % (len(stale), plan.outdir))
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
    # Exclusive, because `tt.shuttle` takes one or the other and says so from
    # the board, several GStreamer ERROR blocks deep, once the capture is
    # already set up. argparse can say it before anything is contacted.
    what = parser.add_mutually_exclusive_group()
    what.add_argument(
        "--project",
        help="ASIC shuttles: tt.shuttle macro to enable, e.g. tt_um_rejunity_vga",
    )
    what.add_argument(
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
        "--force",
        action="store_true",
        help=for_help(
            "delete every %s and %s in --outdir first; without it, an "
            "--outdir that already holds one is refused (and names them, so "
            "nothing goes unseen)" % (PNG_PATTERN.replace("%04d", "*"),
                                      VIDEO_FILENAME)
        ),
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
