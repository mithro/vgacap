# SPDX-License-Identifier: Apache-2.0
"""The hardware-in-the-loop check: everything only a board can settle.

The host test suites cover the code. What they cannot cover is the board
itself -- whether the profile auto-detection reads a real `GPIOMap`, whether
the sampler keeps up without overrunning, how long a cooperative stop really
takes when a DMA buffer is in flight, whether the reconstructed picture is
the picture the design draws. Every check here exists because a task report
or a review in this milestone named it as something only hardware could
answer:

| check | why it needs a board |
|---|---|
| `probe` | `--profile auto` reads the board's `GPIOMap` and matches it against a table; an unknown layout raises. A mis-seated hardware base reports the wrong pins (docs/research/2026-09-15-rp2350-micropython-pio-findings.md) and every capture made after that is wrong. It is also the reachability check: if this fails, nothing below can mean anything. |
| `capture` | the closing `TIME` chunk is the *only* `overruns`/`rxstall` report there is, and no host fake can produce a real overrun. |
| `reconstruct` | that the sample count corresponds to the project clock, and that `libvgaframe` finds sync in a real signal with real glitches. |
| `reference` | pixel-exact agreement with the calibration design's reference render. The only check that says the path is *correct* rather than self-consistent. |
| `frame` | whole frames at the design's real clock (`ttcap capture --mode frame`, 25 MHz by default): the trigger, the control-block DMA over a fragmented heap, and the sample point (swept on fpga-4: two system clocks of a four-clock pixel are right) are only real on a board. Every picture must be pixel-exact. |
| `demo-frames` | `ttcap demo`'s "nothing was captured" verdict (exit 3) and its `--fps` re-timing were both built and re-worked against a fake. |
| `video` | Task 3's PTS bug made `matroskamux` produce a file with no usable duration; only a real clock rate exercises the timeline. |
| `serve` | the MJPEG view drops frames for a viewer that falls behind, which needs a producer running at the board's own rate. |
| `stop` | Task 5 finding F1: a `SIGTERM` used to orphan `gst-launch` and `ttcap capture`, leaving them holding a shared bench board. Task 4 Important 1: `stop()` could hang for ever. And the stop latency itself -- 2.2 s at a 60 kHz project clock, 0.2 s in the fake -- is the one number host tests cannot stand in for. |
| `board-usable` | after all of the above the board must still answer, which is what says the run left nothing behind on it. |

Safety on a shared bench:

* one run at a time per board, enforced with a `flock`ed lock file;
* every board command runs in its own session, and whatever is left of that
  session is killed on the way out, however this script ends short of SIGKILL (SIGTERM, SIGHUP and
  SIGQUIT are caught and turned into an orderly stop);
* `--stop-bridge` is the only thing that touches `fpgas-tt`, and it always
  starts it again.

A board that does not answer is a clear failure naming the board, never a
hang: every command has a timeout.

Run it as `tools/hil.sh REPORT [options]`, or directly under uv:

    uv run tools/hil.py --board fpga-1 --design tt_um_vgacal_bars \\
        --clock-hz 500000 --link ws://127.0.0.1:18034/serial \\
        docs/results/

Exit status: 0 every check passed, 1 at least one failed, 2 it could not
start (lock held, missing tool, bad arguments).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import http.client
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

#: 640x480@60 is 800 clocks a line by 525 lines. `libvgaframe` detects the
#: mode from the signal; this is only used to predict how many frames a
#: capture of a given length at a given project clock should contain.
CLOCKS_PER_FRAME = 800 * 525

#: The shortest capture worth judging a frame count on: the first frame
#: period yields nothing (there is no measured vsync pulse yet) and a capture
#: that starts exactly on a pulse loses the second too, so two periods can
#: legitimately produce nothing at all. README says the same thing.
MIN_FRAME_PERIODS = 3

#: `vgacapttsrc`'s own default stop timeout. A cooperative stop that takes
#: longer than this is the element escalating to a signal, which is the
#: failure Task 5's F1 was about.
STOP_BUDGET_S = 15.0

#: Calibration designs have a reference renderer to compare against; any
#: other design has nothing to be compared with.
CALIBRATION_PREFIX = "tt_um_vgacal_"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

#: Every check, in the order it runs. Named here as well as called in `go`
#: so that a run cut short still produces a complete report: a check that is
#: missing from the table because the board died is indistinguishable from
#: one that was never written.
ALL_CHECKS = ("probe", "capture", "reconstruct", "reference", "frame",
              "demo-frames", "video", "serve", "stop", "board-usable")

#: Pictures the `frame` check takes; each must match the reference.
FRAME_PICTURES = 2

#: The checks that need a GStreamer pipeline, and so the plugin.
PIPELINE_CHECKS = ("demo-frames", "video", "serve", "stop")


class Interrupted(BaseException):
    """A stop signal arrived. BaseException, like KeyboardInterrupt, so no
    check's own `except Exception` can swallow it."""


def _on_stop_signal(signum, _frame):
    # Ignore any further stop signal: the cleanup it would interrupt is the
    # thing that releases the board.
    for sig in STOP_SIGNALS:
        signal.signal(sig, signal.SIG_IGN)
    raise Interrupted(signal.Signals(signum).name)


#: Signals that end a run. Without handlers SIGTERM and SIGHUP (a dropped
#: ssh, `timeout`, a cancelled job) kill this process with no `finally`, and
#: every board command -- each in its own session -- would keep running and
#: keep holding a shared board.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)


@contextlib.contextmanager
def signals_ignored():
    """SIGINT and every stop signal ignored, then put back as they were."""
    saved = {}
    try:
        for sig in (signal.SIGINT, *STOP_SIGNALS):
            saved[sig] = signal.signal(sig, signal.SIG_IGN)
        yield
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)


@contextlib.contextmanager
def signals_deferred():
    """SIGINT and every stop signal held until the block ends, then
    delivered to whatever handled them before.

    Unlike signals_ignored(), nothing is lost, and a child started inside
    the block does not inherit an ignored disposition (exec resets a
    caught signal to its default; an ignored one stays ignored).
    """
    pending: list[int] = []
    saved = {}
    try:
        for sig in (signal.SIGINT, *STOP_SIGNALS):
            saved[sig] = signal.signal(sig, lambda signum, _f: pending.append(signum))
        yield
    finally:
        # Blocked while the handlers go back (only here: a mask around the
        # block's Popen would be inherited by the child). Unblocked, a
        # second Ctrl-C would raise from the first handler restored and
        # leave the rest -- and anything pending -- as the deferring
        # lambda for good. Blocked, it waits in the kernel.
        sigs = tuple(saved)
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, sigs)
        try:
            # A signal that arrived just before the block has tripped
            # Python's flag; give it its (deferring) handler now, before
            # any real one is back.
            _run_pending_signal_handlers()
            for sig, handler in saved.items():
                signal.signal(sig, handler)
            # Queued while still blocked, so none can raise here and each
            # is delivered once the mask is lifted -- to the real handlers.
            for sig in dict.fromkeys(pending):
                with contextlib.suppress(OSError):  # one cannot stop the rest
                    os.kill(os.getpid(), sig)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)


def _run_pending_signal_handlers() -> None:
    """A Python-level call: entering it is where CPython runs any signal
    handler whose C-level flag is already set."""


#: How long `--stop-bridge` waits between its two reads of the bridge's
#: client count. A count of 0 read once can be a visitor between two
#: connections; it has to stay 0 across this wait as well.
BRIDGE_SETTLE_S = 3.0


def bridge_state() -> str:
    """`fpgas-tt` as systemd sees it: "active", "inactive", or anything
    else (another state, or why it could not be asked) -- which is neither,
    and so never a reason to act."""
    try:
        proc = subprocess.run(["systemctl", "is-active", "fpgas-tt"],
                              capture_output=True, text=True, check=False,
                              timeout=60, start_new_session=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown (%s: %s)" % (type(exc).__name__, exc)
    if proc.returncode == 0:
        return "active"
    state = (proc.stdout or "").strip()
    # `failed` is stopped too: nothing holds the device.
    if state in ("inactive", "failed"):
        return "inactive"
    return state or "unknown (systemctl is-active exit %d)" % proc.returncode


class Fatal(Exception):
    """Cannot start, or cannot go on: reported, not a check result."""


@dataclass
class Check:
    name: str
    status: str
    detail: str
    #: Commands and output worth keeping in the report, newest last.
    evidence: list[str] = field(default_factory=list)


@dataclass
class Ran:
    """One finished board command."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float


# ------------------------------------------------------------------ plumbing


def _tail(text: str, lines: int = 12) -> str:
    kept = [line for line in text.splitlines() if line.strip()][-lines:]
    return "\n".join(kept)


#: How long one write to the board may block while restarting it.
RESTART_WRITE_TIMEOUT_S = 2.0

#: `ps` is local and quick; one that takes longer than this is stuck, and
#: cleanup -- deaf to signals -- must not wait on it for ever.
PS_TIMEOUT_S = 30.0


def _sessions_now() -> dict[str, list[str]]:
    """Every live process as `pid args`, by session id: one `ps`.

    A `ps` that fails or outlives PS_TIMEOUT_S is a Fatal, so a run that
    meets one is still reported.
    """
    try:
        proc = subprocess.run(["ps", "-eo", "pid=,sid=,args="],
                              capture_output=True, text=True, check=False,
                              timeout=PS_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Fatal("`ps` could not list this host's processes (%s: %s)"
                    % (type(exc).__name__, exc)) from None
    table: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3:
            table.setdefault(fields[1], []).append("%s %s" % (fields[0], fields[2]))
    return table


def _survivors(sid: int) -> list[str]:
    """Processes still alive in session `sid`, as `pid args` strings.

    A session, not a process group: `vgacapttsrc` puts its `ttcap` child in
    a group of its own (so it can signal it without signalling itself), so
    the group of the process we spawned does not contain the grandchild that
    actually holds the board. The session does.
    """
    return _sessions_now().get(str(sid), [])


def _reap(*sids: int) -> None:
    """Leave nothing of sessions `sids` running, from one `ps` snapshot.
    Belt and braces: this runs even when the command we spawned exited
    cleanly, because the whole point of the orphan check is that a clean
    exit is not proof."""
    table = _sessions_now()
    for sid in sids:
        for entry in table.get(str(sid), []):
            with contextlib.suppress(ValueError, ProcessLookupError, PermissionError):
                os.kill(int(entry.split()[0]), signal.SIGKILL)


# -------------------------------------------------------------------- the run


class Hil:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.checks: list[Check] = []
        self.sessions: list[int] = []
        self.notes: list[str] = []
        self.bridge_stopped = False
        self.workdir = pathlib.Path(args.workdir).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.ttcap = shlex.split(args.ttcap)
        self.frame_period_s = CLOCKS_PER_FRAME / args.clock_hz

    # ------------------------------------------------------------- reporting

    @property
    def board_name(self) -> str:
        """How the board is named in every message, so a failure always says
        which board it was about."""
        if self.args.board:
            return "%s (%s)" % (self.args.board, self.args.link)
        return self.args.link

    def record(self, name: str, ok: bool, detail: str, *evidence: str) -> Check:
        check = Check(name, PASS if ok else FAIL, detail, [e for e in evidence if e])
        self.checks.append(check)
        print("%-5s %-14s %s" % (check.status, name, detail), flush=True)
        return check

    def skip(self, name: str, why: str) -> Check:
        check = Check(name, SKIP, why)
        self.checks.append(check)
        print("%-5s %-14s %s" % (SKIP, name, why), flush=True)
        return check

    def fill_skips(self, why: str) -> None:
        """Account for every check that did not get to run."""
        done = {check.name for check in self.checks}
        for name in ALL_CHECKS:
            if name not in done:
                self.skip(name, "not run: %s" % why)

    # --------------------------------------------------------------- running

    def run(self, argv: list[str], timeout: float) -> Ran:
        """A board command, in its own session, with a deadline.

        The session is what makes the cleanup total: the command's children
        and grandchildren inherit it even when they change process group, so
        `_reap` can find every one of them afterwards.
        """
        started = time.monotonic()
        proc = self.spawn(argv)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _reap(proc.pid)
            out, err = proc.communicate(timeout=30)
            raise Fatal(
                "board %s did not answer within %.0fs: %s\n%s"
                % (self.board_name, timeout, shlex.join(str(a) for a in argv),
                   _tail(err or out))
            ) from None
        return Ran([str(a) for a in argv], proc.returncode, out, err,
                   time.monotonic() - started)

    def spawn(self, argv: list[str]) -> subprocess.Popen:
        # Deferred, not ignored: a signal between Popen returning and the
        # append would otherwise leave a session cleanup never reaps, and
        # the board command must not inherit an ignored SIGTERM.
        with signals_deferred():
            proc = subprocess.Popen(
                [str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True, env=self.env(), cwd=ROOT)
            self.sessions.append(proc.pid)
        return proc

    def env(self) -> dict:
        env = dict(os.environ)
        # The demo builds its pipeline out of elements from this build tree.
        env.setdefault("GST_PLUGIN_PATH", str(ROOT / "build"))
        return env

    def board_argv(self, *rest: object) -> list[str]:
        """`ttcap <rest>` with the link and the design/project settings that
        every board command shares."""
        argv = [*self.ttcap, *[str(r) for r in rest]]
        if self.args.project:
            argv += ["--project", self.args.project]
        if self.args.design:
            argv += ["--design", self.args.design]
        return argv

    # ---------------------------------------------------------- the checks

    def check_probe(self, name: str = "probe") -> bool:
        """Does the board answer, and with what?

        A board that cannot be reached is the one failure that must never be
        a hang or a traceback, so the timeout is caught here and turned into
        a check result naming the board.
        """
        argv = [*self.ttcap, "probe", self.args.link]
        try:
            ran = self.run(argv, self.args.timeout)
        except Fatal as exc:
            self.record(name, False, str(exc).splitlines()[0], shlex.join(argv))
            return False
        answer = " / ".join(
            line.strip() for line in ran.stdout.splitlines() if line.strip()
        )[:300]
        ok = ran.returncode == 0 and bool(answer)
        if ok:
            detail = "%s answered in %.1fs: %s" % (self.board_name, ran.seconds,
                                                   answer)
        else:
            detail = "%s: `ttcap probe` exited %d after %.1fs; %s" % (
                self.board_name, ran.returncode, ran.seconds,
                _tail(ran.stderr, 1) or _tail(ran.stdout, 1) or "and said nothing")
        self.record(name, ok, detail, shlex.join(ran.argv), _tail(ran.stderr))
        return ok

    def check_capture(self) -> pathlib.Path | None:
        """A short capture, judged on the board's own closing report."""
        stream = self.workdir / "hil.vgacap"
        argv = self.board_argv("capture", self.args.link,
                               "--clock-hz", self.args.clock_hz,
                               "--seconds", self.args.seconds,
                               "--profile", self.args.profile,
                               "--out", stream)
        if self.args.pio is not None:
            argv += ["--pio", str(self.args.pio)]
        ran = self.run(argv, self.args.timeout + 3 * self.args.seconds)
        # `ttcap capture`'s own summary line, which starts `bytes=`; the
        # `board:` lines below it can carry an `=` of their own.
        summary = next((line for line in ran.stdout.splitlines()
                        if line.startswith("bytes=")), "")
        stats = dict(re.findall(r"(\w+)=([-\w.]+)", summary))
        samples = int(stats.get("samples", 0))
        overruns = int(stats.get("overruns", -1))
        rxstall = int(stats.get("rxstall", -1))
        # A TIME trailer is what carries overruns/rxstall at all, so its
        # absence is a failure even when the bytes look fine.
        ok = (ran.returncode == 0 and samples > 0 and overruns == 0
              and rxstall == 0 and stream.exists())
        self.record(
            "capture", ok,
            "%s: %s, %d byte(s) on disk"
            % (self.board_name, summary or "no stats line",
               stream.stat().st_size if stream.exists() else 0),
            shlex.join(ran.argv), _tail(ran.stdout), _tail(ran.stderr))
        return stream if ok else None

    def check_reconstruct(self, stream: pathlib.Path) -> list[pathlib.Path]:
        """The captured bytes, through the reference renderer.

        The frame count is checked against the project clock rather than
        against itself: a capture of N seconds at C Hz covers
        N*C/CLOCKS_PER_FRAME frame periods. A frame is only claimed once a
        vsync pulse has been measured, so the first period never yields one
        and a capture that happens to start exactly on a pulse loses the
        second as well -- which is why `validate` insists on at least
        `MIN_FRAME_PERIODS`, and why the bound here is two short rather than
        one.
        """
        prefix = self.workdir / "frames" / "f"
        prefix.parent.mkdir(parents=True, exist_ok=True)
        for stale in prefix.parent.glob("f-*.ppm"):
            stale.unlink()
        proc = subprocess.run([str(self.args.vgacap_frames), str(stream), str(prefix)],
                              capture_output=True, text=True, check=False, timeout=300)
        ppms = sorted(prefix.parent.glob("f-*.ppm"))
        totals = next((line for line in proc.stdout.splitlines()
                       if line.startswith("frames=")), "no totals line")
        expected = int(self.args.seconds * self.args.clock_hz / CLOCKS_PER_FRAME)
        want = max(1, expected - 2)
        ok = proc.returncode == 0 and len(ppms) >= want
        self.record(
            "reconstruct", ok,
            "%d frame(s) from %d byte(s), wanted >=%d for %.1fs at %d Hz; %s"
            % (len(ppms), stream.stat().st_size, want, self.args.seconds,
               self.args.clock_hz, totals),
            "%s %s %s" % (self.args.vgacap_frames, stream, prefix),
            _tail(proc.stdout, 4), _tail(proc.stderr))
        return ppms if ok else []

    def check_reference(self, ppms: list[pathlib.Path]) -> None:
        """Pixel-exact comparison against the calibration design's reference.

        Only a named calibration design has a reference renderer, and it
        lives in the neighbouring repository, so this check says why it is
        skipped rather than passing vacuously.
        """
        design = self.args.design or self.args.project or ""
        if not design.startswith(CALIBRATION_PREFIX):
            self.skip("reference",
                      "not a calibration design (%s), so there is nothing to "
                      "compare against" % (design or "no --design/--project"))
            return
        tool = pathlib.Path(self.args.testpatterns) / "tools" / "compare_capture.py"
        if not tool.exists():
            self.skip("reference",
                      "tt-vga-testpatterns is not next door: no %s" % tool)
            return
        if not ppms:
            self.skip("reference", "no reconstructed frame to compare")
            return
        # The middle frame: the first complete frame of a capture is the one
        # most likely to be affected by whatever the board was doing when the
        # sampler started.
        frame = ppms[len(ppms) // 2]
        argv = ["uv", "run", "--no-project", "--with", "numpy", "--with", "pillow",
                "python", "tools/compare_capture.py", design, str(frame.resolve()),
                "--ui-in", str(self.args.ui_in)]
        proc = subprocess.run(argv, capture_output=True, text=True, check=False,
                              cwd=self.args.testpatterns, timeout=600)
        self.record("reference", proc.returncode == 0,
                    "%s vs %s: %s" % (design, frame.name,
                                      _tail(proc.stdout, 1) or "no output"),
                    shlex.join(argv), _tail(proc.stdout), _tail(proc.stderr))

    def check_frame(self) -> None:
        """Whole frames at the design's real clock, each one pixel-exact."""
        design = self.args.design or self.args.project or ""
        tool = pathlib.Path(self.args.testpatterns) / "tools" / "compare_capture.py"
        if not design.startswith(CALIBRATION_PREFIX):
            self.skip("frame", "not a calibration design (%s), so there is no "
                      "reference to hold whole frames to" % (design or "no --design/--project"))
            return
        if not self.args.frame_clock_hz:
            self.skip("frame", "--frame-clock-hz 0")
            return
        if not tool.exists():
            self.skip("frame", "tt-vga-testpatterns is not next door: no %s" % tool)
            return
        stream = self.workdir / "frame.vgacap"
        with contextlib.suppress(FileNotFoundError):
            stream.unlink()
        argv = self.board_argv("capture", self.args.link,
                               "--clock-hz", self.args.frame_clock_hz,
                               "--mode", "frame", "--vga-mode", self.args.frame_vga_mode,
                               "--frames", FRAME_PICTURES, "--seconds", 60,
                               "--profile", self.args.profile, "--out", stream)
        if self.args.ui_in:
            argv += ["--ui-in", str(self.args.ui_in)]
        if self.args.pio is not None:
            argv += ["--pio", str(self.args.pio)]
        ran = self.run(argv, self.args.timeout + 60)
        board = [line.strip()[len("board: "):] for line in ran.stdout.splitlines()
                 if line.strip().startswith("board: ")]
        summary = board[-1] if board else "no board summary"
        prefix = self.workdir / "frame-frames" / "f"
        prefix.parent.mkdir(parents=True, exist_ok=True)
        for stale in prefix.parent.glob("f-*.ppm"):
            stale.unlink()
        ppms: list[pathlib.Path] = []
        if ran.returncode == 0 and stream.exists():
            subprocess.run([str(self.args.vgacap_frames), str(stream), str(prefix)],
                           capture_output=True, text=True, check=False, timeout=300)
            ppms = sorted(prefix.parent.glob("f-*.ppm"))
        exact = 0
        verdicts = []
        for ppm in ppms:
            cmp = subprocess.run(
                ["uv", "run", "--no-project", "--with", "numpy", "--with", "pillow",
                 "python", "tools/compare_capture.py", design, str(ppm.resolve()),
                 "--ui-in", str(self.args.ui_in)],
                capture_output=True, text=True, check=False,
                cwd=self.args.testpatterns, timeout=600)
            exact += cmp.returncode == 0
            verdicts.append("%s: %s" % (ppm.name, _tail(cmp.stdout, 1) or "no output"))
        ok = (ran.returncode == 0 and len(ppms) == FRAME_PICTURES and exact == len(ppms)
              and "stalls=0" in summary and "rxstall=0" in summary)
        self.record(
            "frame", ok,
            "%d of %d picture(s) pixel-exact at %d Hz (%s); board: %s"
            % (exact, FRAME_PICTURES, self.args.frame_clock_hz,
               self.args.frame_vga_mode, summary),
            shlex.join(ran.argv), "\n".join(board), "\n".join(verdicts),
            _tail(ran.stderr))

    @staticmethod
    def fresh_outdir(outdir: pathlib.Path) -> None:
        """Remove what an earlier run left, so nothing on disk can answer for
        this one. `ttcap demo --force` clears it too, but only once it has got
        that far; a demo that dies first would otherwise be judged on old
        frames and an old video."""
        outdir.mkdir(parents=True, exist_ok=True)
        for old in [*outdir.glob("frame-*.png"), outdir / "capture.mkv"]:
            with contextlib.suppress(FileNotFoundError):
                old.unlink()

    def demo_argv(self, outdir: pathlib.Path, seconds: float,
                  serve: int | None = None) -> list[str]:
        argv = self.board_argv("demo", "--link", self.args.link,
                               "--clock-hz", self.args.clock_hz,
                               "--seconds", seconds,
                               "--profile", self.args.profile,
                               "--fps", self.args.fps,
                               "--outdir", outdir, "--force")
        if serve is not None:
            argv += ["--serve", str(serve)]
        return argv

    def check_demo(self) -> None:
        """One demo run, judged on its frames, its video and its stream."""
        outdir = self.workdir / "demo"
        port = self.args.serve_port
        argv = self.demo_argv(outdir, self.args.seconds, serve=port)
        self.fresh_outdir(outdir)
        reader = MjpegReader(port, self.args.seconds + self.args.timeout)
        reader.start()
        try:
            ran = self.run(argv, self.args.timeout + 3 * self.args.seconds)
        finally:
            reader.stop()
        pngs = sorted(outdir.glob("frame-*.png"))
        video = outdir / "capture.mkv"
        evidence = (shlex.join(ran.argv), _tail(ran.stderr, 20))

        # Frames. With --fps the decoder re-pushes the last frame to hold a
        # wall-clock cadence, so the count follows the wall clock, not the
        # project clock -- which is exactly what is being checked.
        wall_want = max(1, int(0.5 * self.args.fps_value * self.args.seconds))
        self.record("demo-frames",
                    ran.returncode == 0 and len(pngs) >= wall_want,
                    "exit %d, %d png(s) in %.1fs, wanted >=%d at %s fps"
                    % (ran.returncode, len(pngs), ran.seconds, wall_want,
                       self.args.fps),
                    *evidence)

        # Video: a file a player can open, at the rate that was asked for.
        if ran.returncode != 0:
            self.record("video", False, "the demo exited %d, so its video "
                        "proves nothing" % ran.returncode, *evidence)
        elif not video.exists():
            self.record("video", False, "no %s was written" % video, *evidence)
        else:
            duration, rate = video_duration_and_rate(video)
            want = len(pngs) / self.args.fps_value if self.args.fps_value else 0.0
            close = want and abs(duration - want) <= max(1.0, 0.25 * want)
            self.record("video", bool(duration > 0 and close
                                      and rate == self.args.fps_canonical),
                        "%s: %.2fs at %s, %d byte(s); %d frames at %s fps is "
                        "%.2fs" % (video.name, duration, rate or "?",
                                   video.stat().st_size, len(pngs), self.args.fps,
                                   want),
                        *evidence)

        # The browser view. One part proves the port answered; a stream has
        # to keep going, so two is the floor.
        self.record("serve", reader.parts >= 2,
                    "%d MJPEG part(s) from http://127.0.0.1:%d/stream.mjpg (%s)"
                    % (reader.parts, port, reader.note),
                    *evidence)

    def check_stop(self) -> None:
        """A cooperative stop, timed, and what it leaves behind.

        Run unbounded on purpose: `--seconds 0` captures until it is told to
        stop, which is the configuration that orphaned `gst-launch` and
        `ttcap capture` before Task 5's F1 fix, and the only one where the
        stop latency is the board's rather than the clock's.
        """
        outdir = self.workdir / "stop"
        argv = self.demo_argv(outdir, 0)
        self.fresh_outdir(outdir)
        proc = self.spawn(argv)
        # Wait until it is really capturing, so the stop lands mid-flight
        # rather than during setup.
        deadline = time.monotonic() + 1.5 * self.frame_period_s + self.args.timeout
        while time.monotonic() < deadline and proc.poll() is None:
            if any(outdir.glob("frame-*.png")):
                break
            time.sleep(0.2)
        started_capture = any(outdir.glob("frame-*.png"))
        # Then let it run a frame period longer, so the stop lands on a board
        # in the middle of filling a DMA buffer -- which is where the latency
        # this check exists to measure comes from. Stopping on the first frame
        # would time the setup path instead.
        if started_capture:
            time.sleep(1.0 + self.frame_period_s)
        sent = time.monotonic()
        with contextlib.suppress(ProcessLookupError):
            proc.send_signal(signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=STOP_BUDGET_S + self.args.timeout)
        except subprocess.TimeoutExpired:
            _reap(proc.pid)
            out, err = proc.communicate(timeout=30)
            self.record("stop", False,
                        "the demo did not exit within %.0fs of SIGTERM; killed it"
                        % (STOP_BUDGET_S + self.args.timeout),
                        shlex.join(str(a) for a in argv), _tail(err))
            return
        latency = time.monotonic() - sent
        survivors = _survivors(proc.pid)
        video = outdir / "capture.mkv"
        duration, _rate = video_duration_and_rate(video) if video.exists() else (0.0, "")
        ok = (proc.returncode == 0 and latency <= STOP_BUDGET_S
              and not survivors and duration > 0 and started_capture)
        self.record(
            "stop", ok,
            "SIGTERM -> exit %d in %.2fs (budget %.0fs), %d orphan(s), "
            "%s duration %.2fs%s"
            % (proc.returncode, latency, STOP_BUDGET_S, len(survivors),
               video.name, duration,
               "" if started_capture else ", but no frame had been written yet"),
            shlex.join(str(a) for a in argv),
            "orphans: " + ("; ".join(survivors) or "none"),
            _tail(err, 20))

    # ----------------------------------------------------------- the bridge

    def stop_bridge(self) -> None:
        """Take `fpgas-tt` down, remembering to put it back.

        Only on request: it is a shared service, and a script that stops it
        because it felt like it is worse than one that refuses to run.
        """
        if shutil.which("systemctl") is None:
            raise Fatal("--stop-bridge, but there is no systemctl on this host")
        state = bridge_state()
        if state == "inactive":
            self.notes.append("fpgas-tt was already stopped; left alone.")
            return
        if state != "active":
            raise Fatal("--stop-bridge refused: fpgas-tt is %s" % state)
        # The bridge serves visitors of the public site too, who cannot see
        # a claims file: never cut one off. Read twice, a settle apart, the
        # second time immediately before stopping.
        for attempt in range(2):
            if attempt:
                time.sleep(BRIDGE_SETTLE_S)
            clients = bridge_clients(self.args.bridge_health)
            if clients != 0:
                raise Fatal("--stop-bridge refused: the bridge at %s has %s "
                            "client(s) connected" % (self.args.bridge_health, clients))
        # Marked before the attempt: however it ends -- a failure, a
        # timeout, a signal, systemd finishing a stop job sudo was killed
        # after queueing -- cleanup must then look. That is safe for a stop
        # that never took effect: restore_bridge resets the board only when
        # systemd says fpgas-tt is down, and `systemctl start` on an active
        # unit does nothing.
        self.bridge_stopped = True
        try:
            # Deaf to signals for its (bounded) length, and in a session of
            # its own so a terminal Ctrl-C cannot reach sudo or systemctl.
            with signals_ignored():
                subprocess.run(["sudo", "-n", "systemctl", "stop", "fpgas-tt"],
                               check=True, timeout=120, start_new_session=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Fatal("--stop-bridge: `sudo -n systemctl stop fpgas-tt` failed "
                        "(%s: %s); fpgas-tt is now %s"
                        % (type(exc).__name__, exc, bridge_state())) from None
        self.notes.append("fpgas-tt stopped for this run, and started again after.")

    def restore_bridge(self) -> None:
        """Start `fpgas-tt` again, whatever else goes wrong on the way."""
        if not self.bridge_stopped:
            return
        try:
            # Put the board back first. fpgas-tt judges the board when it
            # starts ("safe start"); started while the capture's design is
            # still enabled, it reported `left: <design>` on tt09 until
            # restarted again (2026-11-24). Only with the daemon known to be
            # down: never reset a board under a running bridge's users.
            state = bridge_state()
            if state == "inactive":
                self.restart_board()
            else:
                self.notes.append(
                    "WARNING: fpgas-tt was %s at cleanup, so the board was not "
                    "restarted under it." % state)
        finally:
            # In a `finally`: a restart that raises, or anything that gets
            # past cleanup's signal guard, must not leave the bridge down.
            # The gate is cleared only after the start, so a cleanup cut
            # short before it is retried in full.
            self.start_bridge()
            self.bridge_stopped = False

    def start_bridge(self) -> None:
        try:
            code = subprocess.run(["sudo", "-n", "systemctl", "start", "fpgas-tt"],
                                  check=False, timeout=120,
                                  start_new_session=True).returncode
        except (OSError, subprocess.SubprocessError) as exc:
            code, why = -1, str(exc)
        else:
            why = "exit %d" % code
        if code == 0:
            return
        state = bridge_state()
        if state == "active":
            # After a stop that never took effect: the start had nothing to do.
            self.notes.append("`sudo systemctl start fpgas-tt` failed (%s), but "
                              "fpgas-tt is active." % why)
        else:
            self.notes.append(
                "WARNING: `sudo systemctl start fpgas-tt` failed (%s) -- the "
                "bridge is %s and the next user of this board will find it "
                "so." % (why, "still down" if state == "inactive" else state))

    def restart_board(self) -> None:
        """Ctrl-B, Ctrl-D on the run's own serial link (see restart_sdk)."""
        if not self.args.link.startswith("serial:"):
            return
        from ttcap.cli import link_from_url
        from ttcap.repl import restart_sdk
        try:
            # A write timeout: a USB CDC port that stops draining must not
            # block cleanup before `systemctl start`.
            link = link_from_url(self.args.link, write_timeout=RESTART_WRITE_TIMEOUT_S)
            try:
                ok, _text = restart_sdk(link)
            except BaseException:
                # Output stuck behind a write timeout would make close()
                # wait out the tty's closing_wait (30 s by default) before
                # `systemctl start`: drop it.
                discard = getattr(link, "discard_output", None)
                if discard is not None:
                    with contextlib.suppress(Exception):
                        discard()
                raise
            finally:
                link.close()
        except Exception as exc:  # noqa: BLE001 - reported, then the bridge still starts
            ok, why = False, "%s: %s" % (type(exc).__name__, exc)
        else:
            why = "no soft reboot seen"
        if ok:
            self.notes.append("Board restarted to the SDK's start state before "
                              "fpgas-tt was started again.")
        else:
            self.notes.append("WARNING: restarting the board before fpgas-tt "
                              "failed (%s); the bridge may report the capture's "
                              "design as left enabled." % why)

    # ------------------------------------------------------------------ main

    def go(self) -> None:
        if self.args.stop_bridge:
            self.stop_bridge()
        if not self.check_probe():
            self.fill_skips("%s did not answer" % self.board_name)
            return
        stream = self.check_capture()
        if stream:
            ppms = self.check_reconstruct(stream)
        else:
            ppms = []
            self.skip("reconstruct", "not run: the capture failed")
        self.check_reference(ppms)
        self.check_frame()
        why = self.no_pipeline_reason()
        if why:
            for name in PIPELINE_CHECKS:
                self.skip(name, why)
        else:
            self.check_demo()
            self.check_stop()
        self.check_probe("board-usable")

    def no_pipeline_reason(self) -> str:
        """Why the four pipeline checks cannot run, if they cannot.

        A Pi that has `ttcap` but not the GStreamer plugin is a real and
        supported setup -- the board checks above are the whole point of it --
        so this is a skip with a reason, not a failure.
        """
        if self.args.no_demo:
            return "--no-demo: the board checks only"
        if shutil.which("gst-launch-1.0") is None:
            return "gst-launch-1.0 is not installed here"
        plugin = pathlib.Path(self.env()["GST_PLUGIN_PATH"]) / "libgstvgacap.so"
        if not plugin.exists():
            return "no %s: build the plugin, or set GST_PLUGIN_PATH" % plugin
        return ""

    def cleanup(self) -> None:
        """Never leave a capture running, whatever happened above.

        Deaf to SIGINT and the stop signals while it runs: a second Ctrl-C
        or a SIGHUP here would otherwise leave board commands running, or
        the bridge stopped, with no warning and no report. Idempotent, so
        main() can run it again if a signal lands before that guard is up.
        """
        with signals_ignored():
            try:
                # One `ps` for every session: a stuck one costs PS_TIMEOUT_S
                # once, not once a session, while this is deaf to signals.
                if self.sessions:
                    try:
                        _reap(*self.sessions)
                    except Exception as exc:  # noqa: BLE001 - reported, then the bridge
                        self.notes.append(
                            "WARNING: could not check session(s) %s for leftover "
                            "board commands (%s: %s)."
                            % (", ".join(map(str, self.sessions)),
                               type(exc).__name__, exc))
            finally:
                self.restore_bridge()


# ------------------------------------------------------------- measurements


class MjpegReader(threading.Thread):
    """Count the parts `--serve` publishes, from a real HTTP client.

    A viewer, not a probe: `multipart/x-mixed-replace` only publishes a part
    when the *next* boundary arrives, so a client that connects, reads and
    stays connected is the only way to see more than one.
    """

    def __init__(self, port: int, budget: float) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.budget = budget
        self.parts = 0
        self.note = ""
        # Not `_stop`: threading.Thread has its own `_stop()` method, which
        # join() calls, and an Event under that name makes join() raise.
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=10)

    #: The boundary `multipartmux` and `ttcap.mjpeg` agree on.
    SEPARATOR = b"--vgacapframe"

    def run(self) -> None:
        deadline = time.monotonic() + self.budget
        conn, response = None, None
        # The server does not exist until the demo has built its pipeline, so
        # connecting is a retry loop, not a single attempt.
        while not self._halt.is_set() and time.monotonic() < deadline:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            try:
                conn.request("GET", "/stream.mjpg")
                response = conn.getresponse()
            except OSError as exc:
                self.note = "%s: %s" % (type(exc).__name__, exc)
                conn.close()
                time.sleep(0.5)
                continue
            if response.status != 200:
                self.note = "HTTP %d" % response.status
                conn.close()
                return
            self.note = response.getheader("Content-Type") or "no content type"
            break
        if response is None or conn is None:
            return
        tail = b""
        while not self._halt.is_set() and time.monotonic() < deadline:
            try:
                block = response.read(4096)
            except OSError as exc:
                self.note += "; read failed: %s" % exc
                break
            if not block:
                break
            buffered = tail + block
            self.parts += buffered.count(self.SEPARATOR)
            # Keep just enough of the tail to notice a separator split across
            # two reads. Keeping more (or truncating the buffer before
            # counting) is how a part gets counted twice or not at all.
            tail = buffered[-(len(self.SEPARATOR) - 1):]
        conn.close()


def video_duration_and_rate(path: pathlib.Path) -> tuple[float, str]:
    """What `gst-discoverer-1.0` makes of a written file.

    Asked of the file, not of the pipeline: a Matroska file whose muxer never
    finished has no duration to report, which is precisely the difference a
    cooperative stop has to not make.
    """
    if not shutil.which("gst-discoverer-1.0"):
        return (0.0, "gst-discoverer-1.0 is not installed")
    proc = subprocess.run(["gst-discoverer-1.0", str(path)], capture_output=True,
                          text=True, check=False, timeout=120)
    duration = 0.0
    match = re.search(r"Duration: (\d+):(\d\d):(\d\d)\.(\d+)", proc.stdout)
    if match:
        hours, minutes, seconds, fraction = match.groups()
        duration = ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)
                    + float("0." + fraction))
    rate = re.search(r"Frame rate: (\d+/\d+)", proc.stdout)
    return (duration, rate.group(1) if rate else "")


# --------------------------------------------------------------- the report


def report_text(hil: Hil, started: datetime.datetime, failures: int) -> str:
    args = hil.args
    lines = [
        "# Hardware-in-the-loop check: %s" % hil.board_name,
        "",
        "Date: %s" % started.strftime("%Y-%m-%d %H:%M:%S%z"),
        "",
        "| | |",
        "|---|---|",
        "| board | `%s` |" % (args.board or "(none given)"),
        "| link | `%s` |" % args.link,
        "| design / project | `%s` |" % (args.design or args.project or "(none)"),
        "| project clock | %d Hz (a 640x480 frame is %.3f s) |"
        % (args.clock_hz, hil.frame_period_s),
        "| capture length | %.1f s, video re-timed to %s fps |"
        % (args.seconds, args.fps),
        "| host | `%s`, %s |" % (os.uname().nodename, sys.version.split()[0]),
        "| vgacap | %s |" % describe_source(),
        "| `ttcap` | `%s` |" % args.ttcap,
        "| verdict | **%s** |"
        % ("PASS" if failures == 0 else "FAIL (%d check(s))" % failures),
        "",
        "## Checks",
        "",
        "| check | result | measured |",
        "|---|---|---|",
    ]
    for check in hil.checks:
        lines.append("| `%s` | %s | %s |"
                     % (check.name, check.status, check.detail.replace("|", "\\|")))
    if hil.notes:
        lines += ["", "## Notes", ""] + ["* %s" % note for note in hil.notes]
    lines += ["", "## Evidence", ""]
    for check in hil.checks:
        lines += ["### `%s` -- %s" % (check.name, check.status), "", check.detail, ""]
        if check.evidence:
            lines += ["```", *(e for e in check.evidence if e.strip()), "```", ""]
    return "\n".join(lines) + "\n"


def describe_source() -> str:
    """Which commit is being checked, so the report is attributable."""
    proc = subprocess.run(["git", "-C", str(ROOT), "describe", "--always", "--dirty"],
                          capture_output=True, text=True, check=False)
    return "`%s`" % (proc.stdout.strip() or "unknown revision")


def report_path(given: str, started: datetime.datetime, key: str) -> pathlib.Path:
    """The path to write to.

    A directory gets a dated file inside it, so `docs/results/` is a valid
    thing to pass -- including when it does not exist yet, which is why a
    trailing separator or a missing extension counts as a directory too. A
    report is always a `.md` file, so there is no ambiguity to lose.
    """
    path = pathlib.Path(given)
    if path.is_dir() or given.endswith(os.sep) or path.suffix == "":
        path = path / ("%s-hil-%s.md" % (started.strftime("%Y-%m-%d"), key))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# ----------------------------------------------------------------- the lock


@contextlib.contextmanager
def only_one(key: str, lock_dir: str | None):
    """Refuse to run twice against the same board.

    Two captures on one board produce two wrong answers, and the second one
    can leave the first's DMA channels claimed. The lock is per user per
    machine (in `$XDG_RUNTIME_DIR` by default) rather than per checkout: the
    board is the shared thing, not the working copy. It cannot see another
    user or another machine; pass a shared `--lock-dir` for the first, and
    for the second check the board's status page for other clients first. `--board` gives the key, so two
    runs naming the same board collide even from different checkouts; two
    runs naming only a `--link` collide only if they spell it the same way.
    """
    directory = pathlib.Path(lock_dir or os.environ.get("XDG_RUNTIME_DIR")
                             or tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("vgacap-hil-%s.lock" % key)
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Fatal(
                "another hil run already holds %s -- it is using this board. "
                "Wait for it, or pass --lock-dir if it is not really the same "
                "board." % path
            ) from None
        handle.seek(0)
        handle.truncate()
        handle.write("pid %d started %s\n" % (os.getpid(), datetime.datetime.now()))
        handle.flush()
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


# ------------------------------------------------------------------- the CLI


def resolve_link(args: argparse.Namespace) -> str:
    """`--link` wins; otherwise the board's bridge URL.

    On the board's own Pi the bridge is not the way in -- the serial device
    is -- but working that out belongs to `ttcap demo`, which already does
    it; here an explicit `--link` is the answer, and the message says so.
    """
    if args.link:
        return args.link
    from ttcap.boards import WELLAND, bridge_ws_url

    if args.board is None:
        raise Fatal("give --link, or --board (%s) to use its bridge"
                    % ", ".join(sorted(WELLAND)))
    if args.board not in WELLAND:
        raise Fatal("--board %s is not a Welland slug (%s)"
                    % (args.board, ", ".join(sorted(WELLAND))))
    return bridge_ws_url(args.board)


def bridge_clients(url: str, timeout: float = 5.0) -> int | str:
    """`clients` from fpgas-tt's /health, or why it could not be read.

    Anything but an int is a refusal to stop the bridge: a bridge that
    cannot say it is idle is not known to be idle.
    """
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            clients = json.load(resp).get("clients")
    except Exception as exc:  # noqa: BLE001 - every failure is a refusal
        return "unknown (%s: %s)" % (type(exc).__name__, exc)
    # `type() is int`, not isinstance: JSON `false` is a bool, which is an
    # int, and would read as 0 clients.
    if type(clients) is int and clients >= 0:
        return clients
    return "unknown (%r)" % (clients,)


def parse_fps(text: str) -> tuple[float, str]:
    """`30`, `30/1` and `29.97` as a number and as GStreamer spells it."""
    if "/" in text:
        # Taken as written: the rates a person types (5/1, 30/1) are already
        # in lowest terms, which is how gst-discoverer reports them back.
        num, den = text.split("/", 1)
        return (int(num) / int(den), "%d/%d" % (int(num), int(den)))
    value = float(text)
    if value == int(value):
        return (value, "%d/1" % int(value))
    # 29.97 is 30000/1001 in every muxer that matters, and gst-discoverer
    # reports it that way.
    return (value, "%d/1001" % round(value * 1001))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hil.py",
        description="Run the checks only a Tiny Tapeout board can settle, and "
                    "write a dated report.",
        epilog="Every check prints PASS, FAIL or SKIP with the numbers it "
               "measured; the exit status is 1 if any check failed.")
    parser.add_argument("report", help="report file, or a directory to put a "
                                       "dated one in (docs/results/, say)")
    parser.add_argument("--board", help="Welland slug, e.g. fpga-1")
    parser.add_argument("--link", help="serial:/dev/ttboard or ws://host:8765/serial; "
                                       "required unless --board resolves one")
    parser.add_argument("--design", help="FPGA boards: the bitstream to enable")
    parser.add_argument("--project", help="ASIC shuttles: the macro to enable")
    parser.add_argument("--clock-hz", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=10.0,
                        help="length of each capture (default 10)")
    parser.add_argument("--fps", default="5",
                        help="video rate for the demo runs (default 5)")
    parser.add_argument("--profile", default="auto")
    parser.add_argument("--pio", type=int,
                        help="PIO block for the sampler; ttcap's default otherwise")
    parser.add_argument("--ui-in", type=int, default=0,
                        help="ui_in the board drives, for the tt_um_vgacal_modes "
                             "reference comparison")
    parser.add_argument("--frame-clock-hz", type=int, default=25_000_000,
                        help="project clock for the `frame` check: the design's "
                             "real one (default 25 MHz; 0 skips the check)")
    parser.add_argument("--frame-vga-mode", default="640x480@60",
                        help="VGA timing the `frame` check expects (default 640x480@60)")
    parser.add_argument("--serve-port", type=int, default=8099)
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="how long a board command may take before its own "
                             "work starts (default 60); captures get this plus "
                             "three times --seconds")
    parser.add_argument("--ttcap", default="uv run --no-sync ttcap",
                        help="the ttcap command (default %(default)r)")
    parser.add_argument("--vgacap-frames", default=str(ROOT / "build" / "vgacap-frames"))
    parser.add_argument("--testpatterns", default=str(ROOT.parent / "tt-vga-testpatterns"),
                        help="the tt-vga-testpatterns checkout, for the "
                             "pixel-exact comparison (default: next door)")
    parser.add_argument("--workdir", default=str(ROOT / "tmp" / "hil"),
                        help="where captures, frames and demo outputs go")
    parser.add_argument("--no-demo", action="store_true",
                        help="run only the checks that need no GStreamer "
                             "pipeline: probe, capture, reconstruct, reference")
    parser.add_argument("--stop-bridge", action="store_true",
                        help="stop fpgas-tt first (it holds /dev/ttboard open) "
                             "and start it again afterwards; refused while "
                             "the bridge has clients, and the board is "
                             "restarted (Ctrl-B, Ctrl-D) before it starts")
    parser.add_argument("--bridge-health", default="http://127.0.0.1:8765/health",
                        help="fpgas-tt's /health, read for its client count "
                             "before --stop-bridge (default: %(default)s)")
    parser.add_argument("--lock-dir",
                        help="where the per-board lock file lives "
                             "(default $XDG_RUNTIME_DIR, else the temp dir)")
    return parser


def validate(args: argparse.Namespace) -> None:
    if args.clock_hz <= 0:
        raise Fatal("--clock-hz must be positive, not %d" % args.clock_hz)
    if args.frame_clock_hz < 0:
        raise Fatal("--frame-clock-hz must be positive, or 0 to skip the frame check")
    if args.seconds <= 0:
        raise Fatal("--seconds must be positive: this script never starts an "
                    "unbounded capture it is not going to stop")
    if args.project and args.design:
        raise Fatal("--project and --design are alternatives; tt.shuttle takes one")
    periods = args.seconds * args.clock_hz / CLOCKS_PER_FRAME
    if periods < MIN_FRAME_PERIODS:
        # Refused rather than reported as a failing frame count: a capture
        # this short is entitled to contain no frames, so the check would be
        # measuring the arguments rather than the board.
        raise Fatal(
            "--seconds %g at %d Hz is %.1f frame period(s); a capture needs at "
            "least %d to be sure of a frame. Use --seconds %.0f or more."
            % (args.seconds, args.clock_hz, periods, MIN_FRAME_PERIODS,
               -(-MIN_FRAME_PERIODS * CLOCKS_PER_FRAME // args.clock_hz)))
    if not pathlib.Path(args.vgacap_frames).exists():
        raise Fatal("no %s: build it with `cmake -S . -B build && cmake --build "
                    "build`" % args.vgacap_frames)
    try:
        args.fps_value, args.fps_canonical = parse_fps(args.fps)
    except ValueError:
        raise Fatal("--fps %s is not a rate: try 5, 30/1 or 29.97" % args.fps) from None
    if args.fps_value <= 0:
        raise Fatal("--fps must be positive, not %s" % args.fps)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = datetime.datetime.now().astimezone()
    try:
        # The link first: "that is not a board" is a more fundamental
        # complaint than anything about the numbers.
        args.link = resolve_link(args)
        validate(args)
    except Fatal as exc:
        print("hil: %s" % exc, file=sys.stderr)
        return 2
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", args.board or args.link)[:80]
    for sig in STOP_SIGNALS:
        signal.signal(sig, _on_stop_signal)
    hil = Hil(args)
    failures = 0
    try:
        with only_one(key, args.lock_dir):
            print("hil: %s, %s, %d Hz" % (hil.board_name,
                                          args.design or args.project or "no design",
                                          args.clock_hz), flush=True)
            try:
                hil.go()
            except Fatal as exc:
                hil.record("board", False, str(exc).splitlines()[0])
                hil.fill_skips("the run was cut short")
                hil.notes.append("Run cut short: %s" % exc)
            except (Interrupted, KeyboardInterrupt) as exc:
                why = str(exc) or "SIGINT"
                hil.record("board", False, "interrupted by %s" % why)
                hil.fill_skips("the run was interrupted by %s" % why)
                hil.notes.append("Run interrupted by %s; everything it "
                                 "started was stopped." % why)
            finally:
                # A signal between this `finally` starting and cleanup()'s
                # guard going up would skip it; cleanup() is idempotent, so
                # run it until it completes.
                while True:
                    try:
                        hil.cleanup()
                        break
                    except (Interrupted, KeyboardInterrupt):
                        continue
    except Fatal as exc:
        print("hil: %s" % exc, file=sys.stderr)
        return 2
    failures = sum(1 for check in hil.checks if check.status == FAIL)
    path = report_path(args.report, started, key)
    path.write_text(report_text(hil, started, failures), encoding="utf-8")
    print("\n%d check(s): %d passed, %d failed, %d skipped"
          % (len(hil.checks),
             sum(1 for c in hil.checks if c.status == PASS), failures,
             sum(1 for c in hil.checks if c.status == SKIP)))
    print("report: %s" % path)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
