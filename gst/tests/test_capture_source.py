# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for `vgacapttsrc` and `vgacapbin`, with no board.

The element's subject is a child process, so the test device is a fake
`ttcap`: `fake_ttcap.py` emits a real synthetic vgacap stream at a chosen
rate, keeps a copy of exactly what it sent, records the argv it was given,
and can be told to fail or to emit rubbish. Everything the element has to get
right - the command it builds, the bytes it forwards, how it stops, what it
does with a child that fails - is visible through that one script.

The module skips cleanly when GStreamer or the built plugin is absent, so
`uv run pytest` still passes without them.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

import numpy as np
import pytest
from PIL import Image

from vgacap.ppm import read_ppm

HERE = pathlib.Path(__file__).resolve().parent
FAKE_TTCAP = HERE / "fake_ttcap.py"
STOP_PROBE = HERE / "stop_probe.py"
ROOT = HERE.parents[1]
BUILD = ROOT / "build"
PLUGIN = BUILD / "libgstvgacap.so"
FRAMES_TOOL = BUILD / "vgacap-frames"

_MISSING = (
    shutil.which("gst-inspect-1.0") is None
    or shutil.which("gst-launch-1.0") is None
    or not PLUGIN.exists()
)
pytestmark = pytest.mark.skipif(
    _MISSING,
    reason="needs gst-inspect-1.0/gst-launch-1.0 and a built build/libgstvgacap.so "
           "(cmake -S . -B build && cmake --build build)",
)

#: Generous. Every test that cares about time asserts its own, tighter bound;
#: this one is only here so a hang fails instead of running for ever.
TIMEOUT = 120

#: The properties `vgacapbin` mirrors from `vgacapttsrc`.
MIRRORED = ("link", "project", "design", "clock-hz", "profile", "pio", "buf-words",
            "seconds", "ttcap-command", "stop-timeout")


def gst_env() -> dict:
    env = dict(os.environ)
    env["GST_PLUGIN_PATH"] = str(BUILD)
    return env


def run(argv, timeout: int = TIMEOUT, **env_extra) -> subprocess.CompletedProcess:
    env = gst_env()
    env.update(env_extra)
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                          env=env, timeout=timeout)


def fake_command(*extra: str) -> str:
    """The `ttcap-command` that runs the fake.

    `sys.executable` is the uv venv's interpreter - the one that can import
    `vgacap` - and naming it here is exactly what the property is for: no
    shebang, no PATH, no `uv run` inside a test.
    """
    return " ".join([sys.executable, str(FAKE_TTCAP), *extra])


def inspect_properties(element: str) -> dict[str, str]:
    """{property name: the type/flags/default block gst-inspect prints}."""
    proc = run(["gst-inspect-1.0", element])
    assert proc.returncode == 0, proc.stderr
    body = proc.stdout.split("Element Properties:", 1)[1]
    blocks: dict[str, list[str]] = {}
    name = None
    for line in body.splitlines():
        match = re.match(r"^  (\S+)\s+:", line)
        if match:
            name = match.group(1)
            blocks[name] = []
        elif name and line.strip():
            blocks[name].append(line.strip())
    return {key: " ".join(value) for key, value in blocks.items()}


# ---------------------------------------------------------------- inspection

def test_inspect_lists_the_source_and_its_properties():
    proc = run(["gst-inspect-1.0", "vgacapttsrc"])
    assert proc.returncode == 0, proc.stderr
    assert "Tiny Tapeout VGA capture source" in proc.stdout
    assert "application/x-vgacap" in proc.stdout
    for prop in MIRRORED:
        assert re.search(rf"^\s+{re.escape(prop)}\s+:", proc.stdout, re.M), \
            f"{prop} missing from gst-inspect vgacapttsrc"
    props = inspect_properties("vgacapttsrc")
    # The defaults the plan pins down.
    assert 'Default: "auto"' in props["profile"]
    assert 'Default: "uv run --no-sync ttcap"' in props["ttcap-command"]
    assert re.search(r"Default:\s+15\b", props["stop-timeout"])
    assert re.search(r"Default:\s+0\b", props["seconds"])


def test_inspect_lists_the_bin_and_its_properties():
    proc = run(["gst-inspect-1.0", "vgacapbin"])
    assert proc.returncode == 0, proc.stderr
    assert "Tiny Tapeout VGA capture bin" in proc.stdout
    assert "video/x-raw" in proc.stdout
    for prop in ("uri", *MIRRORED):
        assert re.search(rf"^\s+{re.escape(prop)}\s+:", proc.stdout, re.M), \
            f"{prop} missing from gst-inspect vgacapbin"


def test_the_bin_mirrors_the_sources_properties():
    # The bin declares its own copies of the source's property specs (GObject
    # specs belong to one class), so this is the guard against the two drifting
    # apart: same types, same ranges, same defaults, name for name.
    source = inspect_properties("vgacapttsrc")
    binned = inspect_properties("vgacapbin")
    for prop in MIRRORED:
        assert binned[prop] == source[prop], f"vgacapbin's {prop} differs from the source's"


# ------------------------------------------------------------- live capture

def decode_to_pngs(argv_head: list[str], outdir: pathlib.Path,
                   timeout: int = TIMEOUT) -> tuple[subprocess.CompletedProcess, list]:
    outdir.mkdir(parents=True, exist_ok=True)
    proc = run([*argv_head, "!", "pngenc", "!", "multifilesink", "sync=false",
                f"location={outdir}/frame-%04d.png"], timeout=timeout)
    return proc, sorted(outdir.glob("frame-*.png"))


def test_a_live_capture_decodes_to_the_frames_its_own_bytes_hold(tmp_path):
    # The fake keeps a copy of everything it wrote, so the pipeline's frames
    # can be checked against the reference decoder run over the very same
    # bytes - not over a second stream generated the same way.
    copy = tmp_path / "sent.vgacap"
    proc, pngs = decode_to_pngs(
        ["gst-launch-1.0", "vgacapttsrc",
         f"ttcap-command={fake_command('--frames', '6', '--copy-to', str(copy))}",
         "link=serial:/dev/fake", "clock-hz=60000", "!", "vgadecode"],
        tmp_path / "png")
    assert proc.returncode == 0, proc.stderr
    assert copy.exists(), "the fake never ran"

    prefix = tmp_path / "ref" / "f"
    prefix.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(FRAMES_TOOL), str(copy), str(prefix)], check=True,
                   capture_output=True, text=True)
    ppms = sorted(prefix.parent.glob("f-*.ppm"))

    assert len(pngs) == len(ppms) > 0, f"{len(pngs)} frames from the board, {len(ppms)} from its bytes"
    for png, ppm in zip(pngs, ppms):
        got = np.asarray(Image.open(png).convert("RGB"))
        np.testing.assert_array_equal(got, read_ppm(ppm), err_msg=f"{png} != {ppm}")


def test_the_element_builds_the_capture_command_it_promises(tmp_path):
    argv_file = tmp_path / "argv.json"
    proc = run(["gst-launch-1.0", "vgacapttsrc",
                f"ttcap-command={fake_command('--frames', '1', '--argv-file', str(argv_file))}",
                "link=ws://welland:8765/serial", "project=tt_um_rejunity_vga",
                "design=tt10_fpga", "clock-hz=60000", "profile=demoboard", "pio=1",
                "buf-words=4096", "seconds=2.5", "!", "vgadecode", "!", "fakesink",
                "sync=false"])
    assert proc.returncode == 0, proc.stderr
    argv = json.loads(argv_file.read_text())
    # Positional first, then every property that was set, spelled as ttcap
    # spells it. --out - is not negotiable: it is how the stream gets here.
    assert argv[argv.index("capture") + 1] == "ws://welland:8765/serial"
    assert argv[argv.index("--out") + 1] == "-"
    assert argv[argv.index("--clock-hz") + 1] == "60000"
    assert argv[argv.index("--project") + 1] == "tt_um_rejunity_vga"
    assert argv[argv.index("--design") + 1] == "tt10_fpga"
    assert argv[argv.index("--profile") + 1] == "demoboard"
    assert argv[argv.index("--pio") + 1] == "1"
    assert argv[argv.index("--buf-words") + 1] == "4096"
    assert argv[argv.index("--seconds") + 1] == "2.5"


def test_the_board_tuning_defaults_are_left_to_ttcap(tmp_path):
    argv_file = tmp_path / "argv.json"
    proc = run(["gst-launch-1.0", "vgacapttsrc",
                f"ttcap-command={fake_command('--frames', '1', '--argv-file', str(argv_file))}",
                "link=serial:/dev/fake", "clock-hz=60000", "!", "vgadecode", "!",
                "fakesink", "sync=false"])
    assert proc.returncode == 0, proc.stderr
    argv = json.loads(argv_file.read_text())
    assert "--pio" not in argv and "--buf-words" not in argv
    assert "--design" not in argv and "--project" not in argv
    # seconds is always passed: 0 means "until stopped", which is not ttcap's
    # own default and so cannot be left out.
    assert argv[argv.index("--seconds") + 1] == "0"


@pytest.mark.parametrize("missing,expected", [
    (["clock-hz=60000"], "link"),
    (["link=serial:/dev/fake"], "clock-hz"),
])
def test_the_source_refuses_to_start_without_what_it_needs(missing, expected):
    proc = run(["gst-launch-1.0", "vgacapttsrc", f"ttcap-command={fake_command()}",
                *missing, "!", "fakesink"])
    assert proc.returncode != 0
    assert expected in proc.stdout + proc.stderr


# -------------------------------------------------------------------- errors

def test_a_child_that_fails_is_a_pipeline_error_quoting_its_stderr():
    proc = run(["gst-launch-1.0", "vgacapttsrc",
                f"ttcap-command={fake_command('--mode', 'fail', '--exit-code', '3')}",
                "link=serial:/dev/nope", "clock-hz=60000", "!", "vgadecode", "!",
                "fakesink", "sync=false"])
    text = proc.stdout + proc.stderr
    assert proc.returncode != 0, text
    assert "ERROR" in text
    assert "exit status 3" in text
    # The whole point: the child's own account of the failure reaches the bus.
    assert "the board did not reach the REPL" in text
    assert "capture failed: no such device" in text


def test_garbage_from_the_child_does_not_hang_the_pipeline():
    began = time.monotonic()
    proc = run(["gst-launch-1.0", "vgacapttsrc",
                f"ttcap-command={fake_command('--mode', 'garbage')}",
                "link=serial:/dev/fake", "clock-hz=60000", "!", "vgadecode", "!",
                "fakesink", "sync=false"], timeout=60)
    took = time.monotonic() - began
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Got EOS" in proc.stdout
    assert took < 30, f"a stream of rubbish took {took:.1f}s to reach EOS"


# --------------------------------------------------------------------- stops

def system_python_path() -> str | None:
    """`gi` (gst-python) is an OS package, so the stop probe needs the system
    interpreter, not the uv venv this test runs in."""
    override = os.environ.get("VGACAP_SYSTEM_PYTHON")
    if override:
        return override
    for candidate in ("/usr/bin/python3", "/usr/local/bin/python3"):
        if pathlib.Path(candidate).exists():
            return candidate
    return None


@pytest.mark.parametrize("chunk_delay", ["0.05", "0"])
def test_stopping_mid_stream_is_prompt_and_reaps_the_child(tmp_path, chunk_delay):
    # Both pacings matter. The paced child is the ordinary case; the unpaced
    # one writes faster than the pipeline drinks, so its 64 KiB pipe is full
    # the moment the streaming thread stops reading -- and a child blocked in
    # write() never reaches the code that emits its trailer. That is what the
    # draining half of the stop sequence is for, and this is what tests it.
    system_python = system_python_path()
    if system_python is None:
        pytest.skip("no system python3 to run the gst-python stop probe with")
    pid_file = tmp_path / "child.pid"
    copy = tmp_path / "sent.vgacap"
    stop_timeout = 10.0
    command = fake_command("--chunk-delay", chunk_delay, "--stop-latency", "0.5",
                           "--pid-file", str(pid_file), "--copy-to", str(copy))

    proc = run([system_python, STOP_PROBE, "--ttcap-command", command,
                "--pid-file", str(pid_file), "--stop-timeout", str(stop_timeout),
                "--min-buffers", "10"], timeout=90)
    if proc.returncode != 0 and "No module named 'gi'" in proc.stderr:
        pytest.skip("gst-python (gi) is not importable by the system python3")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)

    # Prompt: the stop costs about one simulated DMA buffer, nowhere near the
    # timeout that would mean the cooperative path had failed.
    assert result["stop_seconds"] < stop_timeout, result
    assert result["stop_seconds"] >= 0.4, \
        f"the stop did not wait for the board's last buffer: {result}"
    # Reaped: not merely dead, but waited for, and by the instant teardown
    # returned rather than within the probe's grace -- this pid is the
    # element's own child, which stop_child() waits for before it returns, so
    # the assertion can be exact. A "Z" here is the zombie the element would
    # leave if it only signalled and walked away.
    assert result["child_at_once"] == "gone", result

    # And patient: the stream ends whole, with the trailer the board only
    # sends when it was asked to stop rather than killed.
    tail = subprocess.run([str(BUILD / "vgacap-dump"), str(copy)], check=True,
                          capture_output=True, text=True).stdout.splitlines()
    assert any(line.startswith("TIME") for line in tail[-2:]), tail[-4:]


def run_stop_probe(tmp_path, fake_flags: list[str], stop_timeout: float,
                   chunk_delay: str = "0.05", timeout: int = 90,
                   probe_flags: list[str] = (), wrapper: list[str] = ()) -> dict:
    system_python = system_python_path()
    if system_python is None:
        pytest.skip("no system python3 to run the gst-python stop probe with")
    pid_file = tmp_path / "child.pid"
    command = fake_command("--chunk-delay", chunk_delay, "--pid-file", str(pid_file),
                           *fake_flags)
    argv = [*wrapper, system_python, STOP_PROBE, "--ttcap-command", command,
            "--pid-file", str(pid_file), "--stop-timeout", str(stop_timeout),
            "--min-buffers", "10", *probe_flags]
    try:
        proc = run(argv, timeout=timeout)
    except subprocess.TimeoutExpired:
        # A teardown that never returns looks exactly like this, so say so
        # rather than leaving a bare TimeoutExpired to be puzzled over.
        pytest.fail(f"the probe did not finish in {timeout}s: the pipeline is stuck "
                    f"in its state change ({' '.join(str(a) for a in argv)})")
    if proc.returncode != 0 and "No module named 'gi'" in proc.stderr:
        pytest.skip("gst-python (gi) is not importable by the system python3")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_a_child_that_ignores_sigint_is_stopped_by_closing_the_pipe(tmp_path):
    # Rung two of the stop ladder. The signal goes nowhere, stop-timeout runs
    # out, the read end is closed, and the child's next write gets EPIPE -
    # which needs no handler to be running, so it works where the signal did
    # not.
    result = run_stop_probe(tmp_path, ["--ignore-sigint"], stop_timeout=1.0)
    # The element's own child, so gone by the time teardown returns -- no
    # grace, or a stop() that returned before reaping would slip through.
    assert result["child_at_once"] == "gone", result
    assert 1.0 <= result["stop_seconds"] < 3.0, result


@pytest.mark.parametrize("flags", [["--deaf"], ["--deaf", "--flood"]])
def test_a_child_that_ignores_everything_is_killed_and_reaped(tmp_path, flags):
    # Rung three: deaf to the signal and to the broken pipe alike, so only
    # SIGKILL ends it. What matters is that the element still reaps it -
    # a killed child left unwaited-for is exactly the zombie to avoid.
    #
    # --flood aims at the drain's own trap: a child that writes without pause
    # could keep the drain loop permanently fed, and a loop that never hands
    # its deadline back would never escalate. It does not in fact get there -
    # a Python child cannot outrun 16 KiB reads for long enough to keep the
    # pipe non-empty - so the sweep cap in drain_stdout() stays a guard this
    # test does not force. What the case does establish is that the SIGKILL
    # rung still reaps a child writing flat out.
    result = run_stop_probe(tmp_path, flags, stop_timeout=1.0, chunk_delay="0")
    assert result["child_at_once"] == "gone", result
    assert 3.0 <= result["stop_seconds"] < 8.0, result


def test_a_capture_carried_on_by_an_orphan_still_tears_down(tmp_path):
    # The shape `uv run --no-sync ttcap` can take when the wrapper dies first:
    # the process the element spawned exits at once and a fork of it carries
    # the capture on, holding both pipes. The element sees the leader end
    # within milliseconds, so no escalation is triggered and no EOF ever
    # arrives on stderr -- which, before this was fixed, left stop() blocked in
    # an unbounded join for ever, with the board still held.
    #
    # --deaf so nothing but SIGKILL to the *group* can end the orphan.
    #
    # This is the one case that needs the grace: the orphan is not the
    # element's child and cannot be waited for, so the element's job ends at
    # delivering the signal and the kernel and init do the rest.
    result = run_stop_probe(tmp_path, ["--orphan", "--deaf"], stop_timeout=8.0,
                            timeout=60)
    assert result["child"] == "gone", result
    # No rung of the ladder applies to a leader that has already ended, so
    # this is the group sweep and the bounded join, and both are prompt.
    assert result["stop_seconds"] < 5.0, result


def test_an_ordinary_end_of_stream_signals_nothing_afterwards(tmp_path):
    # The capture ends by itself, the element learns of it inside create(),
    # and the pipeline then stands for a while before anyone tears it down.
    # Nothing may be signalled at that teardown: if the child had been reaped
    # when its exit was noticed, its pid would be the kernel's to hand out
    # again, and `kill(-pid, SIGINT)` at the end of an arbitrarily long dwell
    # would be a SIGINT to whatever now holds that number -- a stranger's job,
    # and a SIGINT to a job's process group is not a harmless stray.
    #
    # The element instead keeps the child unreaped until the last act, so the
    # id stays reserved, and refuses to signal once nothing of ours is left.
    # strace is the only honest way to see "no signal was sent"; the test
    # skips where it cannot run.
    strace = shutil.which("strace")
    if strace is None:
        pytest.skip("no strace to watch for stray signals with")
    trace = tmp_path / "kills.txt"
    try:
        result = run_stop_probe(
            tmp_path, ["--frames", "4"], stop_timeout=8.0, timeout=90,
            probe_flags=["--until-eos", "--dwell", "3"],
            wrapper=[strace, "-f", "-e", "trace=kill,tgkill", "-o", str(trace)])
    except AssertionError:
        if trace.exists() and "ptrace" in trace.read_text():
            pytest.skip("strace cannot attach here (ptrace_scope)")
        raise
    assert result["eos"] is True, result
    assert result["child_at_once"] == "gone", result

    text = trace.read_text() if trace.exists() else ""
    if "ptrace" in text and "kill(" not in text:
        pytest.skip("strace cannot attach here (ptrace_scope)")
    stray = [line for line in text.splitlines() if "kill(" in line]
    assert not stray, (
        "the element signalled after the capture had already ended:\n"
        + "\n".join(stray))


# ----------------------------------------------------------------- vgacapbin

def test_the_bin_reads_a_file_uri(tmp_path):
    stream = tmp_path / "capture.vgacap"
    subprocess.run([sys.executable, str(FAKE_TTCAP), "--frames", "6", "--copy-to",
                    str(stream), "capture", "serial:/dev/fake", "--out", "-",
                    "--clock-hz", "60000", "--seconds", "0"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc, pngs = decode_to_pngs(["gst-launch-1.0", "vgacapbin", f"uri=file://{stream}"],
                                tmp_path / "png")
    assert proc.returncode == 0, proc.stderr
    assert len(pngs) > 0


def test_the_bins_uri_query_reaches_the_source(tmp_path):
    argv_file = tmp_path / "argv.json"
    command = fake_command("--frames", "1", "--argv-file", str(argv_file))
    proc = run(["gst-launch-1.0", "vgacapbin",
                "uri=tt-ws://welland:8765/serial?project=tt_um_x&clock-hz=60000&seconds=1.5",
                f"ttcap-command={command}", "!", "fakesink", "sync=false"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    argv = json.loads(argv_file.read_text())
    assert argv[argv.index("capture") + 1] == "ws://welland:8765/serial"
    assert argv[argv.index("--project") + 1] == "tt_um_x"
    assert argv[argv.index("--clock-hz") + 1] == "60000"
    assert argv[argv.index("--seconds") + 1] == "1.5"


def test_the_bins_own_properties_reach_the_source(tmp_path):
    # Set on the bin rather than in the URI; ttcap-command itself is one of
    # them, so the fake running at all is half the assertion.
    argv_file = tmp_path / "argv.json"
    command = fake_command("--frames", "1", "--argv-file", str(argv_file))
    proc = run(["gst-launch-1.0", "vgacapbin", "uri=tt-serial:///dev/ttboard",
                f"ttcap-command={command}", "project=tt_um_y", "clock-hz=31500",
                "buf-words=2048", "!", "fakesink", "sync=false"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    argv = json.loads(argv_file.read_text())
    assert argv[argv.index("capture") + 1] == "serial:/dev/ttboard"
    assert argv[argv.index("--project") + 1] == "tt_um_y"
    assert argv[argv.index("--clock-hz") + 1] == "31500"
    assert argv[argv.index("--buf-words") + 1] == "2048"


def test_the_uri_query_beats_a_property_set_on_the_bin(tmp_path):
    argv_file = tmp_path / "argv.json"
    command = fake_command("--frames", "1", "--argv-file", str(argv_file))
    proc = run(["gst-launch-1.0", "vgacapbin",
                "uri=tt-serial:///dev/ttboard?project=from-the-uri",
                f"ttcap-command={command}", "project=from-the-property",
                "clock-hz=60000", "!", "fakesink", "sync=false"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    argv = json.loads(argv_file.read_text())
    assert argv[argv.index("--project") + 1] == "from-the-uri"


@pytest.mark.parametrize("uri,expected", [
    ("http://example.com/x", "unknown scheme"),
    ("tt-serial://", "names no serial device"),
    ("/dev/ttyACM0", "no scheme"),
    ("tt-serial:///dev/ttboard?nonesuch=1", "a uri may not set"),
    ("tt-serial:///dev/ttboard?clock-hz=sixty", "not a guint"),
])
def test_a_bad_uri_is_a_pipeline_error(uri, expected):
    proc = run(["gst-launch-1.0", "vgacapbin", f"uri={uri}", "!", "fakesink"])
    text = proc.stdout + proc.stderr
    assert proc.returncode != 0, text
    assert expected in text, text


@pytest.mark.parametrize("key", ["ttcap-command", "link", "location"])
def test_a_uri_cannot_name_the_program_to_run(tmp_path, key):
    # A URI can arrive from somewhere that is not a trusted shell, and
    # ttcap-command names a program; a query that could set it would be
    # arbitrary command execution by whoever chose the URI. The same goes for
    # anything that would move the capture away from the link the URI names.
    sentinel = tmp_path / "executed"
    payload = f"/bin/sh -c touch\\ {sentinel}"
    proc = run(["gst-launch-1.0", "vgacapbin",
                f"uri=tt-serial:///dev/ttboard?clock-hz=60000&{key}={payload}",
                "!", "fakesink", "sync=false"])
    text = proc.stdout + proc.stderr
    # First, because it is the thing that matters: the URI's command never ran.
    assert not sentinel.exists(), f"the uri's command ran\n{text}"
    assert proc.returncode != 0, text
    assert "a uri may not set" in text and key in text, text


def test_the_allowed_query_keys_all_still_work(tmp_path):
    argv_file = tmp_path / "argv.json"
    command = fake_command("--frames", "1", "--argv-file", str(argv_file))
    query = ("project=tt_um_z&design=bits&clock-hz=25175000&profile=rp2350"
             "&pio=0&buf-words=8192&seconds=3&stop-timeout=9")
    proc = run(["gst-launch-1.0", "vgacapbin",
                f"uri=tt-serial:///dev/ttboard?{query}",
                f"ttcap-command={command}", "!", "fakesink", "sync=false"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    argv = json.loads(argv_file.read_text())
    for flag, want in (("--project", "tt_um_z"), ("--design", "bits"),
                       ("--clock-hz", "25175000"), ("--profile", "rp2350"),
                       ("--pio", "0"), ("--buf-words", "8192"), ("--seconds", "3")):
        assert argv[argv.index(flag) + 1] == want, argv
    # stop-timeout is the element's own, so it never reaches the child; the
    # capture running at all is the evidence it was accepted.
    assert "--stop-timeout" not in argv
