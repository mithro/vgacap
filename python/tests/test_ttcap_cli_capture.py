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
from ttcap.capture import CaptureError, frames_to_max_bytes
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
