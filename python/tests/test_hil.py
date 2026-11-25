# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/hil.py`, the hardware-in-the-loop check.

The script exists to be run against a board, so what can be tested here is
everything *around* the board: that an unreachable board is a clear failure
and not a hang, that a failing board check fails the run, that two runs
cannot share a board, that the report says what happened, and -- with
GStreamer and the plugin present -- that the whole check list passes against
`python/tests/hil_stub_ttcap.py`, which is `ttcap` with `fake_ttcap.py` where
the board should be.

The board-only tests pass `--no-demo`, so they need neither GStreamer nor the
plugin and run in about a second each.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HIL = ROOT / "tools" / "hil.py"
STUB = pathlib.Path(__file__).resolve().parent / "hil_stub_ttcap.py"
BUILD = ROOT / "build"
FRAMES_TOOL = BUILD / "vgacap-frames"
PLUGIN = BUILD / "libgstvgacap.so"

#: Generous: the happy path runs two real GStreamer pipelines, one of which
#: waits a frame period before stopping. The point of a timeout here is that
#: a hang fails instead of running for ever.
TIMEOUT = 300

needs_frames_tool = pytest.mark.skipif(
    not FRAMES_TOOL.exists(),
    reason="needs build/vgacap-frames (cmake -S . -B build && cmake --build build)")

#: The no-plugin case is only reachable on a host that has gst-launch-1.0:
#: without it hil.py reports the missing launcher first, which is right but
#: is not what that test is about.
needs_gst_launch = pytest.mark.skipif(
    shutil.which("gst-launch-1.0") is None,
    reason="needs gst-launch-1.0 (the plugin-missing message comes after it)")

needs_gstreamer = pytest.mark.skipif(
    shutil.which("gst-launch-1.0") is None or not PLUGIN.exists(),
    reason="needs gst-launch-1.0 and a built build/libgstvgacap.so")


def run_hil(*args: str, stub: str = "ok", timeout: int = 60,
            **env_extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HIL_STUB"] = stub
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HIL), "--ttcap", "%s %s" % (sys.executable, STUB), *args],
        capture_output=True, text=True, env=env, timeout=timeout)


@pytest.fixture
def workspace(tmp_path: pathlib.Path) -> dict:
    """The arguments every run shares: its own workdir, lock and report."""
    return {
        "common": ["--link", "stub://board", "--clock-hz", "500000",
                   "--seconds", "4",
                   "--workdir", str(tmp_path / "work"),
                   "--lock-dir", str(tmp_path / "lock")],
        "report": tmp_path / "report.md",
        "tmp_path": tmp_path,
    }


def statuses(stdout: str) -> dict[str, str]:
    """The PASS/FAIL/SKIP line for each check, by name."""
    found = {}
    for line in stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[0] in ("PASS", "FAIL", "SKIP"):
            found[fields[1]] = fields[0]
    return found


# ------------------------------------------------------------ the happy path


@needs_gstreamer
@needs_frames_tool
def test_every_check_passes_against_the_stub(workspace):
    """The whole list, end to end, with a fake board and real everything else."""
    proc = run_hil(*workspace["common"], str(workspace["report"]), timeout=TIMEOUT)
    result = statuses(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL" not in result.values(), proc.stdout
    for name in ("probe", "capture", "reconstruct", "demo-frames", "video",
                 "serve", "stop", "board-usable"):
        assert result[name] == "PASS", (name, proc.stdout)
    # No calibration design was named, so there is nothing to compare with.
    assert result["reference"] == "SKIP"
    assert result["frame"] == "SKIP"
    assert "no reference to hold whole frames to" in proc.stdout
    report = workspace["report"].read_text()
    assert "| verdict | **PASS** |" in report
    assert "MJPEG part(s)" in report and "SIGTERM -> exit 0" in report


@needs_gstreamer
@needs_frames_tool
def test_the_stop_check_reports_no_orphan(workspace):
    """The check that Task 5's F1 was about: a cooperative stop must leave
    nothing holding the board."""
    proc = run_hil(*workspace["common"], str(workspace["report"]), timeout=TIMEOUT)
    stop = [line for line in proc.stdout.splitlines() if " stop " in line]
    assert stop, proc.stdout
    assert "0 orphan(s)" in stop[0], stop
    assert stop[0].startswith("PASS"), stop


# --------------------------------------------------------- the no-board path


def test_an_unreachable_board_fails_and_names_it(workspace):
    proc = run_hil("--board", "fpga-1", "--no-demo", *workspace["common"],
                   str(workspace["report"]), stub="probe-fails")
    result = statuses(proc.stdout)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert result["probe"] == "FAIL"
    # Every other check accounted for, so the report cannot be read as
    # "these passed".
    assert [name for name, status in result.items() if status == "SKIP"]
    assert all(status != "PASS" for status in result.values())
    assert "fpga-1" in proc.stdout
    assert "fpga-1" in workspace["report"].read_text()


def test_a_board_that_never_answers_times_out_rather_than_hanging(workspace):
    """A hung board is the failure most likely to waste an operator's evening,
    so it has to be bounded and to say which board it was."""
    proc = run_hil("--board", "fpga-3", "--no-demo", "--timeout", "3",
                   *workspace["common"], str(workspace["report"]),
                   stub="probe-hangs", timeout=90)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert statuses(proc.stdout)["probe"] == "FAIL"
    assert "fpga-3" in proc.stdout and "did not answer within 3s" in proc.stdout


@needs_frames_tool
def test_a_board_that_overruns_fails_the_capture_check(workspace):
    """`overruns` from the closing TIME chunk is the whole reason the capture
    check runs on hardware, so a non-zero count must fail."""
    proc = run_hil("--no-demo", *workspace["common"], str(workspace["report"]),
                   stub="overruns")
    result = statuses(proc.stdout)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert result["capture"] == "FAIL" and result["probe"] == "PASS"
    assert "overruns=3" in proc.stdout
    # A failed capture has no stream to reconstruct, and says so.
    assert result["reconstruct"] == "SKIP"


@needs_frames_tool
def test_the_board_is_probed_again_at_the_end(workspace):
    """The run must leave the board usable, which is only checkable by asking
    it again afterwards."""
    proc = run_hil("--no-demo", *workspace["common"], str(workspace["report"]))
    assert statuses(proc.stdout)["board-usable"] == "PASS", proc.stdout
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------- the guards


@needs_frames_tool
def test_two_runs_cannot_share_a_board(workspace, tmp_path):
    """Two captures on one board give two wrong answers, so the second run is
    refused while the first holds the lock."""
    lock_dir = tmp_path / "lock"
    lock_dir.mkdir(parents=True)
    mark = "lock-%d" % os.getpid()
    held = subprocess.Popen(
        [sys.executable, str(HIL), "--ttcap", "%s %s" % (sys.executable, STUB),
         "--board", "fpga-1", "--no-demo", "--timeout", "30",
         "--link", "stub://board", "--clock-hz", "500000", "--seconds", "4",
         "--workdir", str(tmp_path / "w1"), "--lock-dir", str(lock_dir),
         str(tmp_path / "first.md")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "HIL_STUB": "probe-hangs", "HIL_TEST_MARK": mark})
    try:
        # Wait for the lock file to appear: the first run has it by then.
        deadline = 60
        while deadline and not list(lock_dir.glob("*.lock")):
            deadline -= 1
            time.sleep(0.2)
        second = run_hil("--board", "fpga-1", "--no-demo",
                         "--link", "stub://board", "--clock-hz", "500000",
                         "--seconds", "4", "--workdir", str(tmp_path / "w2"),
                         "--lock-dir", str(lock_dir), str(tmp_path / "second.md"))
        assert second.returncode == 2, second.stdout + second.stderr
        assert "already holds" in second.stderr
        assert "vgacap-hil-fpga-1.lock" in second.stderr
    finally:
        # SIGTERM, as a person's `kill` would send: hil.py then stops the
        # board command it started. SIGKILL (this test's old ending) gives
        # it no chance, and left one hanging stub per run, orphaned.
        held.terminate()
        held.communicate(timeout=30)
        leftover = []
        for _ in range(50):
            leftover = _processes_marked(mark)
            if not leftover:
                break
            time.sleep(0.1)
        for pid in leftover:
            os.kill(pid, signal.SIGKILL)
    assert leftover == [], "hil.py left board commands running: %s" % leftover


def test_a_capture_too_short_to_contain_a_frame_is_refused(workspace, tmp_path):
    """Refused rather than failed: two frame periods are entitled to produce
    no frames, so the check would be measuring the arguments."""
    proc = run_hil("--board", "fpga-1", "--no-demo", "--link", "stub://board",
                   "--clock-hz", "60000", "--seconds", "10",
                   "--workdir", str(tmp_path / "w"),
                   "--lock-dir", str(tmp_path / "l"), str(workspace["report"]))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "1.4 frame period(s)" in proc.stderr
    assert "--seconds 21 or more" in proc.stderr


def test_an_unknown_board_slug_lists_the_real_ones(tmp_path):
    proc = run_hil("--board", "tt99", "--clock-hz", "500000",
                   "--workdir", str(tmp_path / "w"),
                   "--lock-dir", str(tmp_path / "l"), str(tmp_path / "r.md"))
    assert proc.returncode == 2
    assert "fpga-1" in proc.stderr and "tt08" in proc.stderr


def test_nothing_runs_without_a_link_or_a_board(tmp_path):
    proc = run_hil("--clock-hz", "500000", "--workdir", str(tmp_path / "w"),
                   "--lock-dir", str(tmp_path / "l"), str(tmp_path / "r.md"))
    assert proc.returncode == 2
    assert "give --link" in proc.stderr


# ---------------------------------------------------------------- the report


@needs_frames_tool
def test_a_directory_gets_a_dated_report(workspace, tmp_path):
    """`docs/results/` is the natural thing to pass, and the operator should
    not have to invent today's date."""
    outdir = tmp_path / "results"
    proc = run_hil("--board", "fpga-4", "--no-demo", *workspace["common"],
                   str(outdir) + os.sep)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    written = list(outdir.glob("*.md"))
    assert len(written) == 1, written
    assert written[0].name.endswith("-hil-fpga-4.md")
    text = written[0].read_text()
    assert "# Hardware-in-the-loop check: fpga-4 (stub://board)" in text
    assert "| check | result | measured |" in text
    # The report names the revision it was run against, so it can be believed
    # later.
    assert "| vgacap |" in text


@needs_frames_tool
def test_the_report_says_a_calibration_design_was_not_available(workspace,
                                                               tmp_path):
    """A skipped comparison must say why: a reference check that quietly
    passes is worse than none."""
    proc = run_hil("--design", "tt_um_vgacal_bars", "--no-demo",
                   "--testpatterns", str(tmp_path / "nowhere"),
                   *workspace["common"], str(workspace["report"]))
    assert statuses(proc.stdout)["reference"] == "SKIP"
    assert "tt-vga-testpatterns is not next door" in proc.stdout
    assert statuses(proc.stdout)["frame"] == "SKIP"


@needs_frames_tool
@needs_gst_launch
def test_a_host_without_the_plugin_skips_the_pipeline_checks(workspace, tmp_path):
    """A Pi with `ttcap` but no plugin is a supported setup: the board checks
    are the point of it, and the pipeline ones must say why they did not run
    rather than fail."""
    empty = tmp_path / "no-plugins"
    empty.mkdir()
    proc = run_hil(*workspace["common"], str(workspace["report"]),
                   GST_PLUGIN_PATH=str(empty), timeout=TIMEOUT)
    result = statuses(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for name in ("demo-frames", "video", "serve", "stop"):
        assert result[name] == "SKIP", (name, proc.stdout)
    assert "libgstvgacap.so" in proc.stdout
    assert result["probe"] == "PASS" and result["capture"] == "PASS"


# ------------------------------------------------------------- the unit bits


def test_survivors_finds_a_live_process_in_a_session():
    """The orphan check is only worth anything if `_survivors` really sees
    processes, so point it at this one's own session."""
    sys.path.insert(0, str(ROOT / "tools"))
    import hil

    mine = hil._survivors(os.getsid(0))
    assert any(line.startswith("%d " % os.getpid()) for line in mine), mine
    # And a session id nothing can be in.
    assert hil._survivors(0x7FFFFFFF) == []





@pytest.mark.parametrize("text,value,canonical", [
    ("5", 5.0, "5/1"),
    ("30", 30.0, "30/1"),
    ("30/1", 30.0, "30/1"),
    ("25/2", 12.5, "25/2"),
    ("29.97", 29.97, "30000/1001"),
])
def test_fps_is_understood_the_way_gstreamer_spells_it(text, value, canonical):
    sys.path.insert(0, str(ROOT / "tools"))
    import hil

    assert hil.parse_fps(text) == (pytest.approx(value), canonical)


# ------------------------------------------------------- stop signals, stale files


def _processes_marked(mark: str) -> list[int]:
    """PIDs whose environment carries `mark`: every board command hil.py
    started inherits it, whatever session or group it moved to."""
    found = []
    needle = ("HIL_TEST_MARK=%s" % mark).encode()
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if needle in (entry / "environ").read_bytes().split(b"\0"):
                found.append(int(entry.name))
        except OSError:
            continue
    return found


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_a_stop_signal_cleans_up_and_still_reports(workspace, sig):
    """A dropped ssh or a `kill` used to end hil.py with no cleanup at all,
    leaving its board commands running against a shared board."""
    mark = "sig-%d-%d" % (sig, os.getpid())
    env = dict(os.environ, HIL_STUB="probe-hangs", HIL_TEST_MARK=mark)
    proc = subprocess.Popen(
        [sys.executable, str(HIL), "--ttcap", "%s %s" % (sys.executable, STUB),
         "--no-demo", "--timeout", "120", *workspace["common"],
         str(workspace["report"])],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    # Wait until the hung probe is really running, so the signal lands with
    # a board command outstanding.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and len(_processes_marked(mark)) < 2:
        time.sleep(0.1)
    assert len(_processes_marked(mark)) >= 2, "the probe never started"
    proc.send_signal(sig)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 1, out + err
    assert "interrupted by %s" % signal.Signals(sig).name in out, out + err
    assert workspace["report"].exists()
    # Nothing it started outlives it.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _processes_marked(mark):
        time.sleep(0.1)
    assert _processes_marked(mark) == []


@needs_gstreamer
@needs_frames_tool
def test_frames_left_by_an_earlier_run_do_not_count(workspace):
    """A demo that dies before clearing its outdir must not be judged on the
    frames and video an earlier run left there."""
    first = run_hil(*workspace["common"], str(workspace["report"]), timeout=TIMEOUT)
    assert first.returncode == 0, first.stdout + first.stderr
    demo = workspace["tmp_path"] / "work" / "demo"
    assert any(demo.glob("frame-*.png")) and (demo / "capture.mkv").exists()
    # Second run: a demo that fails before it reaches its outdir.
    second = run_hil(*workspace["common"], str(workspace["report"]),
                     stub="demo-fails", timeout=TIMEOUT)
    result = statuses(second.stdout)
    assert result["demo-frames"] == "FAIL", second.stdout
    assert result["video"] == "FAIL", second.stdout


# ------------------------------------------------- --stop-bridge etiquette


def _health_server(body: bytes):
    """A one-thread HTTP server answering every GET with `body`."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the http.server spelling
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d/health" % server.server_port


def test_bridge_clients_reads_the_daemons_count():
    sys.path.insert(0, str(ROOT / "tools"))
    import hil

    server, url = _health_server(b'{"clients": 2, "kind": "fpga"}')
    try:
        assert hil.bridge_clients(url) == 2
    finally:
        server.shutdown()


@pytest.mark.parametrize("body", [b"not json", b'{"kind": "fpga"}', b'{"clients": "1"}',
                                  b'{"clients": false}', b'{"clients": -1}',
                                  b'{"clients": 0.0}', b'[0]'])
def test_bridge_clients_that_cannot_be_read_are_not_zero(body):
    sys.path.insert(0, str(ROOT / "tools"))
    import hil

    server, url = _health_server(body)
    try:
        got = hil.bridge_clients(url)
    finally:
        server.shutdown()
    assert got != 0 and isinstance(got, str), got


def test_an_unreachable_bridge_is_not_idle():
    sys.path.insert(0, str(ROOT / "tools"))
    import hil

    got = hil.bridge_clients("http://127.0.0.1:9/health", timeout=2)
    assert isinstance(got, str) and got.startswith("unknown"), got


def test_stop_bridge_refuses_while_a_visitor_is_connected(tmp_path, monkeypatch):
    """The bridge also serves the public site, whose visitors cannot see a
    claims file: with a client connected, nothing may be stopped."""
    sys.path.insert(0, str(ROOT / "tools"))
    import hil

    server, url = _health_server(b'{"clients": 1}')
    calls = []

    def fake_run(argv, **_kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)  # "fpgas-tt is active"

    monkeypatch.setattr(hil.shutil, "which", lambda _name: "/bin/systemctl")
    monkeypatch.setattr(hil.subprocess, "run", fake_run)
    args = hil.build_parser().parse_args(
        [str(tmp_path / "report.md"), "--clock-hz", "500000",
         "--link", "serial:/dev/null", "--stop-bridge", "--bridge-health", url,
         "--workdir", str(tmp_path)])
    run = hil.Hil(args)
    try:
        with pytest.raises(hil.Fatal, match="1 client"):
            run.stop_bridge()
    finally:
        server.shutdown()
    assert not run.bridge_stopped
    assert not any("stop" in argv for argv in calls), calls


def test_restart_sdk_leaves_raw_repl_then_soft_resets():
    from ttcap.repl import restart_sdk

    class FakeLink:
        def __init__(self):
            self.written = []
            self.replies = [b"MPY: soft reboot\r\n", b"tt.sdk_version=3.1.0\r\n>>> "]

        def write(self, data):
            self.written.append(data)

        def read(self, timeout):
            return self.replies.pop(0) if self.replies else b""

        def close(self):
            raise AssertionError("restart_sdk must leave the link to its caller")

    link = FakeLink()
    started = time.monotonic()
    ok, text = restart_sdk(link, wait=30.0)
    assert ok and "sdk_version" in text
    # Ctrl-C twice first, so code still running on the board cannot hold
    # the restart up.
    assert link.written == [b"\r\x03\x03", b"\x02", b"\r\n", b"\x04"]
    # Done as soon as the board is back at a prompt, not after all of `wait`.
    assert time.monotonic() - started < 10.0


def test_restart_sdk_without_a_reboot_says_so():
    from ttcap.repl import restart_sdk

    class SilentLink:
        written: list = []

        def write(self, data):
            self.written.append(data)

        def read(self, timeout):
            return b">>> "  # a prompt, but no reboot ever

        def close(self):
            pass

    ok, _text = restart_sdk(SilentLink(), wait=0.5)
    assert not ok
