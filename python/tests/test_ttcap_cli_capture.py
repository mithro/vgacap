# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for `ttcap capture` and `ttcap png`, driven through main().

The link is injected by monkeypatching `cli.link_from_url`, so the whole
argument-parsing-to-file path runs with `FakeChunkBoard` standing in for a
demo board. `ttcap png` is exercised against a synthetic stream and the
real `vgacap-frames` binary when it has been built.
"""

from __future__ import annotations

import io
import pathlib

import pytest
from fake_repl import FakeChunkBoard

from ttcap import cli
from ttcap.boards import RP2040_TT06, RP2350_DBV3
from ttcap.capture import CaptureError, CaptureStats, frames_to_max_bytes
from vgacap.stream import Header, Writer, read_stream


def gpio_map_reply(profile) -> str:
    """What the board prints for `GPIOMap.all()` on `profile`'s board."""
    entries = {"rp_projclk": profile.clk_gpio}
    for index, gpio in enumerate(profile.uo_gpios):
        entries["uo_out%d" % index] = gpio
    return repr(entries) + "\r\n"


def capture_board(profile, chunks, **kwargs) -> FakeChunkBoard:
    return FakeChunkBoard(
        chunks, replies={cli.GPIO_MAP_CODE: gpio_map_reply(profile)}, **kwargs
    )


def sample_chunks(profile, samples) -> list[bytes]:
    """One RAW chunk of `samples` plus a clean trailer, as the board sends them."""
    buf = io.BytesIO()
    writer = Writer(
        buf,
        Header(
            version=1,
            sample_bits=profile.sample_bits,
            mode=0,
            clock_hz=0,
            signal_map=profile.signal_map,
            samples_per_word=profile.samples_per_word,
            flags=profile.flags,
        ),
    )
    mark = buf.tell()
    writer.raw(samples)
    writer.time(0, 0, 0, "overruns=0 rxstall=0 sysclk_hz=133000000")
    return [buf.getvalue()[mark:]]


def script_of(board: FakeChunkBoard) -> str:
    (script,) = [c for c in board.commands if c.startswith("CFG = ")]
    return script


# -- ttcap capture --------------------------------------------------------


def test_capture_writes_a_stream_and_exits_zero(monkeypatch, capsys, tmp_path):
    profile = RP2350_DBV3
    board = capture_board(profile, sample_chunks(profile, list(range(32))))
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    out = tmp_path / "capture.vgacap"

    code = cli.main(
        [
            "capture", "serial:/dev/null",
            "--profile", "rp2350",
            "--project", "tt_um_vga",
            "--clock-hz", "100000",
            "--seconds", "0.05",
            "--out", str(out),
        ]
    )

    assert code == 0
    header, _items = read_stream(out.read_bytes())
    assert header.clock_hz == 100_000
    assert header.sample_bits == profile.sample_bits
    printed = capsys.readouterr().out
    assert "samples=32" in printed
    assert "board: overruns=0" in printed
    assert board.closed
    assert "tt.shuttle['tt_um_vga'].enable()" in board.commands
    # Stopping the clock afterwards is the default.
    assert board.commands[-1] == "tt.clock_project_stop()"


def test_capture_auto_profile_asks_the_board(monkeypatch, tmp_path):
    profile = RP2040_TT06
    board = capture_board(profile, sample_chunks(profile, [1, 2, 3, 4]))
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    out = tmp_path / "auto.vgacap"

    code = cli.main(
        ["capture", "serial:/dev/null", "--clock-hz", "1000",
         "--seconds", "0.05", "--out", str(out)]
    )

    assert code == 0
    assert cli.GPIO_MAP_CODE in board.commands
    header, _ = read_stream(out.read_bytes())
    assert header.sample_bits == 12
    assert "profile=rp2040-tt06map" in header.desc


def test_capture_exits_three_when_nothing_was_sampled(monkeypatch, capsys, tmp_path):
    board = capture_board(RP2350_DBV3, [])
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    out = tmp_path / "empty.vgacap"

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350",
         "--clock-hz", "1000", "--seconds", "0.05", "--out", str(out)]
    )

    assert code == 3
    assert "no samples" in capsys.readouterr().err
    # The file is still a valid, header-only stream.
    read_stream(out.read_bytes())


def test_capture_exits_one_on_a_board_error(monkeypatch, capsys, tmp_path):
    board = capture_board(
        RP2350_DBV3, [], errors={"tt.clock_project_PWM(1000)": "AttributeError: nope\r\n"}
    )
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    out = tmp_path / "never.vgacap"

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350",
         "--clock-hz", "1000", "--seconds", "0.05", "--out", str(out)]
    )

    assert code == 1
    assert "AttributeError" in capsys.readouterr().err
    # Setup failed before the file was opened, so no stub file is left.
    assert not out.exists()


def test_capture_can_leave_the_clock_running(monkeypatch, tmp_path):
    profile = RP2350_DBV3
    board = capture_board(profile, sample_chunks(profile, [7, 7, 7, 7]))
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350",
         "--clock-hz", "1000", "--seconds", "0.05",
         "--out", str(tmp_path / "s.vgacap"), "--no-stop-clock"]
    )

    assert code == 0
    # Only the one stop that select_project does before reprogramming.
    assert board.commands.count("tt.clock_project_stop()") == 1


def test_capture_passes_the_pio_block_and_edge_through(monkeypatch, tmp_path):
    profile = RP2350_DBV3
    board = capture_board(profile, sample_chunks(profile, [1, 2, 3, 4]))
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "1000",
         "--seconds", "0.05", "--out", str(tmp_path / "s.vgacap"),
         "--pio", "2", "--edge", "rising", "--buf-words", "256"]
    )

    cfg_line = script_of(board).splitlines()[0]
    assert "'pio': 2" in cfg_line
    assert "'edge': 'rising'" in cfg_line
    assert "'buf_words': 256" in cfg_line


def test_capture_max_bytes_reaches_the_board(monkeypatch, tmp_path):
    profile = RP2350_DBV3
    board = capture_board(profile, sample_chunks(profile, [1, 2, 3, 4]))
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "1000",
         "--seconds", "0", "--max-bytes", "65536",
         "--out", str(tmp_path / "s.vgacap")]
    )

    assert code == 0  # --seconds 0 is legal once a byte limit is set
    assert "'max_bytes': 65536" in script_of(board).splitlines()[0]


def test_capture_frames_converts_to_bytes_for_the_profile(monkeypatch, tmp_path):
    profile = RP2350_DBV3
    board = capture_board(profile, sample_chunks(profile, [1, 2, 3, 4]))
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "1000",
         "--seconds", "0", "--frames", "3", "--out", str(tmp_path / "s.vgacap")]
    )

    expected = frames_to_max_bytes(profile, 3)
    assert "'max_bytes': %d" % expected in script_of(board).splitlines()[0]


def test_capture_rejects_frames_and_max_bytes_together(capsys, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            ["capture", "serial:/dev/null", "--clock-hz", "1000",
             "--frames", "3", "--max-bytes", "1000",
             "--out", str(tmp_path / "s.vgacap")]
        )

    assert excinfo.value.code == 2  # argparse's usage code
    assert "not allowed with" in capsys.readouterr().err


def test_capture_seconds_zero_without_a_byte_limit_is_rejected(monkeypatch, capsys, tmp_path):
    board = capture_board(RP2350_DBV3, [])
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "1000",
         "--seconds", "0", "--out", str(tmp_path / "s.vgacap")]
    )

    assert code == 1
    assert "never stop" in capsys.readouterr().err


def test_capture_prints_the_boards_traceback_when_the_cleanup_is_rejected(
    monkeypatch, capsys, tmp_path
):
    # The pre-run cleanup is the first thing the board sees, and when it
    # fails the traceback is the only clue -- so it has to reach the user
    # whole, one line per line, not truncated to its first line.
    traceback = (
        "Traceback (most recent call last):\r\n"
        "  File \"<stdin>\", line 5, in <module>\r\n"
        "NameError: name 'gc' isn't defined\r\n"
    )
    profile = RP2350_DBV3
    board = capture_board(profile, [])
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    board.errors = {
        command: traceback
        for command in [cli_cleanup_command(profile)]
    }

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "1000",
         "--seconds", "0.05", "--out", str(tmp_path / "s.vgacap")]
    )

    assert code == 1
    printed = capsys.readouterr().err.splitlines()
    assert printed[0].startswith("capture failed: CaptureError")
    # Every traceback line is on a line of its own, the NameError included.
    assert printed[-3:] == traceback.strip().splitlines()
    # And no zero-byte .vgacap pretending a capture happened.
    assert not (tmp_path / "s.vgacap").exists()


def test_a_refused_run_leaves_no_empty_file_behind(monkeypatch, capsys, tmp_path):
    # The heap check refuses before a single byte is written, so there is
    # nothing to keep: an empty `.vgacap` is worse than no file, because
    # every tool downstream has to decide what an empty stream means.
    profile = RP2350_DBV3
    board = capture_board(profile, sample_chunks(profile, [1, 2]), mem_free=1000)
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    out = tmp_path / "refused.vgacap"

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "1000",
         "--seconds", "0.05", "--out", str(out)]
    )

    assert code == 1
    assert "free heap" in capsys.readouterr().err
    assert not out.exists()


def test_a_run_that_wrote_something_keeps_its_file(monkeypatch, tmp_path):
    # The other half of the rule: whatever reached the file is a valid
    # stream and is never deleted, however the run ended.
    profile = RP2350_DBV3
    board = capture_board(
        profile, [], terminate=False, on_quiet_stderr="KeyboardInterrupt\r\n"
    )
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    monkeypatch.setattr(cli, "run_capture", _short_timeout(cli.run_capture))
    out = tmp_path / "partial.vgacap"

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "1000",
         "--seconds", "0.05", "--out", str(out)]
    )

    assert code == 1
    assert out.exists() and out.stat().st_size > 0


def cli_cleanup_command(profile) -> str:
    """The exact cleanup snippet `run_capture` will send for `profile`."""
    from ttcap.capture import _CLEANUP, capture_cfg, cleanup_names

    return _CLEANUP % (cleanup_names(capture_cfg(profile)),)


def test_capture_reports_a_link_failure_without_a_traceback(monkeypatch, capsys, tmp_path):
    def _explode(url):
        raise OSError(2, "no such device")

    monkeypatch.setattr(cli, "link_from_url", _explode)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "1000",
         "--seconds", "0.05", "--out", str(tmp_path / "s.vgacap")]
    )

    assert code == 1
    # Named, not a traceback. (errno 2 makes Python pick the OSError
    # subclass, which is exactly the shape pyserial raises too.)
    assert "capture failed: FileNotFoundError" in capsys.readouterr().err


def test_capture_exits_one_when_the_board_goes_quiet(monkeypatch, capsys, tmp_path):
    profile = RP2350_DBV3
    board = capture_board(
        profile, [], terminate=False, on_quiet_stderr="KeyboardInterrupt\r\n"
    )
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    out = tmp_path / "quiet.vgacap"
    monkeypatch.setattr(cli, "run_capture", _short_timeout(cli.run_capture))

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "1000",
         "--seconds", "0.05", "--out", str(out)]
    )

    assert code == 1
    printed = capsys.readouterr().out
    assert "timed_out=yes" in printed  # the stats still reached the user
    assert "wrote %s" % out in printed


def _short_timeout(run_capture):
    """Wrap `run_capture` so the quiet-board test does not wait 30 s."""

    def _call(repl, req, out, chunk_timeout=0.3):
        return run_capture(repl, req, out, chunk_timeout=chunk_timeout)

    return _call


def test_capture_rejects_naming_both_a_project_and_a_design(monkeypatch, capsys, tmp_path):
    board = capture_board(RP2350_DBV3, [])
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "1000",
         "--seconds", "0.05", "--out", str(tmp_path / "s.vgacap"),
         "--project", "a", "--design", "b"]
    )

    assert code == 1
    assert "only one" in capsys.readouterr().err


def _stats(messages) -> CaptureStats:
    return CaptureStats(
        bytes=0, samples=0, chunks=0, overruns=len(messages), seconds=1.0, rxstall=0,
        messages=tuple(messages),
    )


def test_report_coalesces_consecutive_identical_board_messages(capsys, tmp_path):
    # A high sample rate can overrun once per TIME chunk for a while (e.g.
    # 320 chunks at 1.5 MHz); one "board: overrun" line per chunk would
    # flood the terminal, so consecutive repeats collapse into one line
    # with a count.
    messages = ["overrun"] * 320 + ["overruns=320 rxstall=0 sysclk_hz=125000000"]

    cli._report(_stats(messages), str(tmp_path / "s.vgacap"))

    printed = capsys.readouterr().out.splitlines()
    assert "  board: overrun (x320)" in printed
    assert printed.count("  board: overrun (x320)") == 1
    assert "  board: overrun" not in [line for line in printed if "(x320)" not in line]
    # The final summary line is never repeated, so it is unchanged.
    assert "  board: overruns=320 rxstall=0 sysclk_hz=125000000" in printed


def test_report_does_not_add_a_count_to_a_lone_message(capsys, tmp_path):
    messages = ["overrun", "overruns=1 rxstall=0 sysclk_hz=125000000"]

    cli._report(_stats(messages), str(tmp_path / "s.vgacap"))

    printed = capsys.readouterr().out
    assert "  board: overrun\n" in printed
    assert "(x1)" not in printed


def test_report_does_not_coalesce_non_consecutive_repeats(capsys, tmp_path):
    # Two separate runs of "overrun" split by a different message must stay
    # two lines, not merge into one count -- groupby is intentionally
    # order-sensitive here, not a global tally.
    messages = ["overrun", "overrun", "rxstall", "overrun"]

    cli._report(_stats(messages), str(tmp_path / "s.vgacap"))

    printed = capsys.readouterr().out.splitlines()
    assert printed.count("  board: overrun (x2)") == 1
    assert printed.count("  board: overrun") == 1  # the lone trailing one
    assert "  board: rxstall" in printed


# -- ttcap png ------------------------------------------------------------


def test_png_renders_a_synthetic_capture(tmp_path):
    pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    from vgacap.modes import MODES
    from vgacap.synth import bars, write_stream

    if not pathlib.Path(cli.find_vgacap_frames()).exists():
        pytest.skip("vgacap-frames is not built")

    mode = MODES["640x480@60"]
    stream = tmp_path / "s.vgacap"
    write_stream(stream, mode, bars(mode.h_active, mode.v_active), frames=3)

    written = cli.png(str(stream), str(tmp_path / "f"))

    assert written
    with Image.open(written[-1]) as image:
        assert image.size == (mode.h_active, mode.v_active)


def test_png_reports_a_renderer_failure(monkeypatch, tmp_path):
    missing = tmp_path / "does-not-exist.vgacap"
    missing.write_bytes(b"")
    if not pathlib.Path(cli.find_vgacap_frames()).exists():
        pytest.skip("vgacap-frames is not built")

    with pytest.raises(CaptureError):
        cli.png(str(missing), str(tmp_path / "f"))


def test_find_vgacap_frames_prefers_the_explicit_path():
    assert cli.find_vgacap_frames("/somewhere/vgacap-frames") == "/somewhere/vgacap-frames"


def test_find_vgacap_frames_falls_back_to_the_environment(monkeypatch, tmp_path):
    # Hide the in-tree build so the $VGACAP_FRAMES branch is the one taken.
    monkeypatch.setattr(cli.pathlib.Path, "exists", lambda self: False)
    monkeypatch.setenv("VGACAP_FRAMES", str(tmp_path / "frames"))

    assert cli.find_vgacap_frames() == str(tmp_path / "frames")


def test_find_vgacap_frames_explains_itself_when_there_is_nothing(monkeypatch):
    monkeypatch.setattr(cli.pathlib.Path, "exists", lambda self: False)
    monkeypatch.delenv("VGACAP_FRAMES", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    with pytest.raises(CaptureError, match="VGACAP_FRAMES"):
        cli.find_vgacap_frames()


# -- --ui-in leaves a shared board as it found it ---------------------------

MODE_QUERY = "print(int(tt.mode))"


def _ui_in_board(profile, **kwargs) -> FakeChunkBoard:
    board = capture_board(profile, [], **kwargs)
    board.replies[MODE_QUERY] = "2\r\n"  # ASIC_MANUAL_INPUTS, as fpgas.online
    return board


def _capture_with_ui_in(tmp_path, *extra) -> int:
    return cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "1000",
         "--seconds", "0.05", "--ui-in", "5", "--out", str(tmp_path / "s.vgacap"), *extra]
    )


def test_a_capture_that_fails_after_ui_in_puts_the_mode_back(monkeypatch, tmp_path):
    profile = RP2350_DBV3
    board = _ui_in_board(profile, mem_free=1000)  # refused: not enough heap
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    assert _capture_with_ui_in(tmp_path) == 1

    switched = board.commands.index(
        "from ttboard.mode import RPMode\ntt.mode = RPMode.ASIC_RP_CONTROL\ntt.ui_in.value = 5"
    )
    after = board.commands[switched + 1 :]
    assert after[-2:] == ["tt.clock_project_stop()", "tt.mode = 2"]


def test_an_interrupted_capture_after_ui_in_puts_the_mode_back(monkeypatch, tmp_path):
    profile = RP2350_DBV3
    board = _ui_in_board(profile)
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    def _interrupted(repl, req, out, chunk_timeout=0.3):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_capture", _interrupted)

    with pytest.raises(KeyboardInterrupt):
        _capture_with_ui_in(tmp_path, "--no-stop-clock")

    # --no-stop-clock is still honoured; the mode is put back regardless.
    assert board.commands[-1] == "tt.mode = 2"
    assert board.commands.count("tt.clock_project_stop()") == 1


def test_a_failed_restore_is_reported_with_the_capture_failure(monkeypatch, capsys, tmp_path):
    profile = RP2350_DBV3
    board = _ui_in_board(profile, mem_free=1000)
    board.errors["tt.mode = 2"] = "OSError: link\r\n"
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    assert _capture_with_ui_in(tmp_path) == 1

    err = capsys.readouterr().err
    assert "free heap" in err
    assert "putting the board back failed too" in err


def test_a_bad_sample_delay_is_refused_before_the_board_is_touched(monkeypatch, capsys, tmp_path):
    profile = RP2350_DBV3
    board = _ui_in_board(profile)
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    assert _capture_with_ui_in(tmp_path, "--mode", "frame", "--sample-delay", "40") == 1

    assert "sample_delay" in capsys.readouterr().err
    assert not any(c.startswith("tt.") or "RPMode" in c for c in board.commands)


# -- --mode rle -----------------------------------------------------------


def rle_chunks(profile, pairs, summary) -> list[bytes]:
    """One RLE chunk of `pairs` and the closing TIME chunks, as rle_rp2.py
    sends them."""
    buf = io.BytesIO()
    writer = Writer(
        buf,
        Header(
            version=1,
            sample_bits=profile.sample_bits,
            mode=0,
            clock_hz=0,
            signal_map=profile.signal_map,
            samples_per_word=profile.samples_per_word,
            flags=profile.flags,
        ),
    )
    mark = buf.tell()
    writer.rle(pairs)
    for message in summary:
        writer.time(0, 0, 0, message)
    return [buf.getvalue()[mark:]]


def _rle_capture(monkeypatch, tmp_path, chunks, *extra) -> int:
    board = capture_board(RP2350_DBV3, chunks)
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    return cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--mode", "rle",
         "--clock-hz", "25000000", "--out", str(tmp_path / "rle.vgacap"), *extra]
    )


def test_an_rle_capture_counts_the_runs_and_a_saturated_one_in_full(monkeypatch, capsys, tmp_path):
    # Through main(): a 2^32 run arrives as two pairs of 2^31.
    pairs = [(7, 3), (9, 1 << 31), (9, 1 << 31), (4, 5)]
    chunks = rle_chunks(RP2350_DBV3, pairs,
                        ["overruns=0 rxstall=0 saturated=1 sysclk_hz=150000000"])

    assert _rle_capture(monkeypatch, tmp_path, chunks, "--max-bytes", "65536") == 0

    out = capsys.readouterr().out
    assert "samples=%d " % (3 + (1 << 32) + 5) in out
    assert "saturated=1" in out


def test_an_rle_capture_with_an_rx_stall_exits_one(monkeypatch, capsys, tmp_path):
    # A lost run word puts every later pair out of step: the board stops
    # with an error chunk, and the run is a failure even though what was
    # written before the stall is good.
    chunks = rle_chunks(RP2350_DBV3, [(7, 3), (9, 4)],
                        ["error=rxstall", "overruns=0 rxstall=1 saturated=0 sysclk_hz=150000000"])

    assert _rle_capture(monkeypatch, tmp_path, chunks, "--max-bytes", "65536") == 1

    captured = capsys.readouterr()
    assert "rxstall=1" in captured.out
    assert "rx stall" in captured.err
    _, items = read_stream((tmp_path / "rle.vgacap").read_bytes())
    assert [tuple(i[1:]) for i in items if i[0] == "run"] == [(7, 3), (9, 4)]


def test_a_stream_capture_with_an_rx_stall_still_exits_zero(monkeypatch, capsys, tmp_path):
    # Only RLE makes the stall fatal; a RAW stream with a stall is short of
    # samples, but every word it has is in step.
    profile = RP2350_DBV3
    chunks = sample_chunks(profile, list(range(32)))
    chunks[0] = chunks[0].replace(b"rxstall=0", b"rxstall=1")
    board = capture_board(profile, chunks)
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "100000",
         "--seconds", "0.05", "--out", str(tmp_path / "s.vgacap")]
    )

    assert code == 0
    assert "rxstall=1" in capsys.readouterr().out


def test_rle_refuses_frames_before_touching_the_board(monkeypatch, capsys, tmp_path):
    # A frame's bytes depend on the picture under RLE, so --frames cannot
    # become a byte budget.
    board = capture_board(RP2350_DBV3, [])
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--mode", "rle",
         "--clock-hz", "25000000", "--frames", "2", "--out", str(tmp_path / "rle.vgacap")]
    )

    assert code == 1
    assert "--frames does not apply to --mode rle" in capsys.readouterr().err
    assert not any(c.startswith("CFG = ") for c in board.commands)
    assert not (tmp_path / "rle.vgacap").exists()


def test_an_rle_oneshot_runs_with_seconds_zero_and_no_byte_limit(monkeypatch, tmp_path):
    # The oneshot stops itself after its two buffers; no other limit needed.
    chunks = rle_chunks(RP2350_DBV3, [(7, 3)],
                        ["overruns=0 rxstall=0 saturated=0 sysclk_hz=150000000"])

    assert _rle_capture(monkeypatch, tmp_path, chunks, "--seconds", "0", "--rle-oneshot") == 0
    assert _rle_capture(monkeypatch, tmp_path, chunks, "--seconds", "0") == 1
