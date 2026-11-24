# SPDX-License-Identifier: Apache-2.0
"""The pico-sdk capture firmware, off hardware.

`vgacap-fw-sim` is the firmware's own C core (firmware/core: the protocol,
the capture engines, the PIO program builder) on a simulated board fed
with a synthetic VGA design, one `uo_out` byte per project clock. These
tests drive it through ttcap's firmware transport -- `FirmwareLink`,
`FirmwareSession`, `ttcap capture --firmware picosdk` -- and check:

* the PIO words it would load are MicroPython's `rp2.asm_pio` words for
  capture_rp2.py's and frame_rp2.py's programs, operand for operand, and
  pioasm's for the checked-in firmware/pio/*.pio sources;
* a streamed capture is byte-exact: every RAW chunk holds the design's
  consecutive samples and the trailer is capture_rp2.py's;
* frame and window captures reconstruct pixel-exactly with vgacap-frames;
* stops, overruns and refusals behave as the MicroPython programs' do.
"""

from __future__ import annotations

import ast
import io
import pathlib
import struct
import subprocess
import threading

import numpy as np
import pytest
from fw_sim_link import FRAMES, ROOT, SimLink

from ttcap import cli, mp
from ttcap.boards import RP2350_DBV3
from ttcap.capture import (
    CaptureError,
    CaptureRequest,
    frames_to_max_bytes,
    run_session,
    stream_header_bytes,
)
from ttcap.emu.board import assemble
from ttcap.firmware import FirmwareLink, FirmwareSession, capture_command, setup, teardown
from vgacap.modes import MODES
from vgacap.ppm import read_ppm
from vgacap.stream import Writer, parse_header, read_chunks
from vgacap.synth import bars, colour6_to_rgb, frame_samples, grid

VGA = MODES["640x480@60"]


@pytest.fixture(scope="module")
def frames_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("fw-sim")


@pytest.fixture(scope="module")
def bars_uo(frames_dir) -> tuple[pathlib.Path, np.ndarray]:
    image = bars(VGA.h_active, VGA.v_active)
    path = frames_dir / "bars.uo"
    path.write_bytes(frame_samples(VGA, image).tobytes())
    return path, image


@pytest.fixture
def fw(bars_uo):
    link = SimLink(bars_uo[0])
    firmware = FirmwareLink(link)
    firmware.sync()
    yield firmware
    link.close()


def request(**kwargs) -> CaptureRequest:
    kwargs.setdefault("profile", RP2350_DBV3)
    kwargs.setdefault("project", None)
    kwargs.setdefault("design", None)
    kwargs.setdefault("seconds", 0)
    kwargs.setdefault("max_bytes", 0)
    return CaptureRequest(**kwargs)


def capture(fw: FirmwareLink, req: CaptureRequest):
    setup(fw, req)
    out = io.BytesIO()
    stats = run_session(FirmwareSession(fw, req), out)
    return stats, out.getvalue()


def pictures(tmp_path, data: bytes) -> list[pathlib.Path]:
    stream = tmp_path / "capture.vgacap"
    stream.write_bytes(data)
    subprocess.run([str(FRAMES), str(stream), str(tmp_path / "f")], capture_output=True, check=True)
    return sorted(tmp_path.glob("f-*.ppm"))


def assert_pictures_exact(frames, image6):
    assert frames, "no picture was reconstructed"
    want = colour6_to_rgb(image6)
    for frame in frames:
        got = read_ppm(frame)
        assert got.shape == want.shape, frame.name
        assert np.array_equal(got, want), "%s: %d pixels differ" % (
            frame.name, int(np.any(got != want, axis=-1).sum()))


# -- the protocol ---------------------------------------------------------------


def test_hello(fw):
    assert fw.hello["chip"] == "rp2350"
    assert fw.hello["proto"] == "1"
    assert int(fw.hello["arena_bytes"]) >= 481 * 800  # a whole 640x480 frame region


def test_errors_are_capture_errors(fw):
    with pytest.raises(CaptureError, match="unknown command 'nonsense'"):
        fw.command("nonsense")
    with pytest.raises(CaptureError, match="missing key 'hz'"):
        fw.command("clock")
    # and the link is still in step afterwards
    assert fw.command("status")["clock_hz"] == "0"


def test_sync_recovers_from_an_abandoned_capture(bars_uo):
    link = SimLink(bars_uo[0])
    try:
        first = FirmwareLink(link)
        first.sync()
        first.command("clock hz=100000")
        first.start_capture("stream buf_words=256 max_bytes=0 edge=falling pio=1 sm=0")
        tag, _ = next(first.chunks(5.0))
        assert tag == b"RAW "
        # A new host on the same port, with the capture still streaming.
        second = FirmwareLink(link)
        assert second.sync()["chip"] == "rp2350"
        assert second.command("status")["clock_hz"] == "100000"
    finally:
        link.close()


# -- the PIO programs ---------------------------------------------------------------


def _micropython_builder(script: str, name: str):
    """`name` from the board script `script`, built against a given rp2."""
    tree = ast.parse(mp.source(script))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)

    def build(rp2, *args):
        namespace = {"rp2": rp2}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), script, "exec"), namespace)  # noqa: S102
        return namespace[name](*args)

    return build


def _program(reply: dict) -> tuple[list[int], int, int]:
    return (
        [int(w, 16) for w in reply["words"].split(",")],
        int(reply["shiftctrl"], 16),
        int(reply["execctrl"], 16),
    )


@pytest.mark.parametrize("rising", [False, True])
def test_stream_program_is_micropythons(fw, rising):
    words, shiftctrl, execctrl = _program(
        fw.command("program_stream edge=%s" % ("rising" if rising else "falling")))
    build = _micropython_builder("capture_rp2.py", "make_sampler")
    prog = assemble(lambda rp2: build(rp2, RP2350_DBV3.clk_gpio, RP2350_DBV3.in_count,
                                      RP2350_DBV3.push_thresh, rising))
    assert words == list(prog[0])
    assert shiftctrl == prog[4]
    assert execctrl == prog[3]


@pytest.mark.parametrize("hs_act,vs_act", [(0, 0), (0, 1), (1, 0), (1, 1)])
@pytest.mark.parametrize("edge,delay,period", [("rising", 0, 4), ("falling", 3, 2), ("rising", 31, 32)])
def test_frame_program_is_micropythons(fw, hs_act, vs_act, edge, delay, period):
    words, shiftctrl, execctrl = _program(fw.command(
        "program_frame edge=%s hsync_active=%d vsync_active=%d sample_delay=%d period=%d"
        % (edge, hs_act, vs_act, delay, period)))
    build = _micropython_builder("frame_rp2.py", "make_trigger")
    p = RP2350_DBV3
    prog = assemble(lambda rp2: build(
        rp2, p.clk_gpio, p.uo_gpios[7], p.uo_gpios[3], hs_act, vs_act, p.in_count, p.push_thresh,
        1 if edge == "rising" else 0, delay, period))
    assert words == list(prog[0])
    assert shiftctrl == prog[4]
    assert execctrl == prog[3]


def _pioasm_hex(name: str) -> list[int]:
    # pioasm's hex output keeps bit 5 of a `wait gpio` number (GPIO 32..47)
    # above the 16-bit instruction, in bit 16; the instruction is the low 16.
    text = (ROOT / "firmware" / "pio" / name).read_text()
    return [int(line, 16) & 0xFFFF for line in text.split()]


def test_pio_sources_are_what_the_firmware_builds(fw):
    # firmware/pio/*.pio at their default operands; the firmware build
    # checks *.hex is pioasm's output for them.
    words, _, _ = _program(fw.command("program_stream edge=falling"))
    assert words == _pioasm_hex("vgacap_stream.hex")
    words, _, _ = _program(fw.command(
        "program_frame edge=rising hsync_active=0 vsync_active=0 sample_delay=0 period=4"))
    assert words == _pioasm_hex("vgacap_frame.hex")


# -- streaming -------------------------------------------------------------------------


def test_stream_is_byte_exact_and_reconstructs(fw, bars_uo, tmp_path):
    uo_path, image = bars_uo
    req = request(clock_hz=100_000, max_bytes=frames_to_max_bytes(RP2350_DBV3, 1))
    assert capture_command(req) == "stream buf_words=4096 max_bytes=%d edge=falling pio=1 sm=0" % req.max_bytes
    stats, data = capture(fw, req)
    assert stats.clean, stats.format()
    assert stats.overruns == 0 and stats.dropped == 0
    assert stats.messages == ("overruns=0 rxstall=0 sysclk_hz=133000000",)

    # Byte-exact: rebuild the whole stream from the design's own samples.
    header = stream_header_bytes(req)
    assert data.startswith(header)
    raws = [payload for tag, payload in read_chunks(data) if tag == "RAW "]
    assert all(struct.unpack_from("<I", p)[0] == 4096 * 4 for p in raws)
    # Each word is little-endian with the first sample on top.
    got = np.concatenate([np.frombuffer(p[4:], dtype=np.uint8).reshape(-1, 4)[:, ::-1].ravel()
                          for p in raws])
    # Where in the design's frame the capture began: the design's clock 0
    # is the vsync leading edge (frame_samples), active low here.
    uo = np.frombuffer(uo_path.read_bytes(), dtype=np.uint8)
    vsync = (got >> 3) & 1
    edge = int(np.flatnonzero((vsync[1:] == 0) & (vsync[:-1] == 1))[0]) + 1
    start = (-edge) % len(uo)
    want = np.take(uo, np.arange(start, start + len(got)), mode="wrap")
    assert np.array_equal(got, want), "the stream is not the design's consecutive samples"
    expected = io.BytesIO()
    writer = Writer(expected, parse_header(header[8:]))
    for k in range(len(raws)):
        writer.raw(want[k * 16384:(k + 1) * 16384].tolist())
    writer.time(0, 0, 0, "overruns=0 rxstall=0 sysclk_hz=133000000")
    assert expected.getvalue() == data
    assert stats.samples == len(got)

    assert_pictures_exact(pictures(tmp_path, data), image)


def test_stream_overruns_are_counted_like_capture_rp2(bars_uo):
    # A host that reads at 20 KB/s cannot keep up with 100 k samples/s: the
    # DMA comes round to buffers that were never sent.
    link = SimLink(bars_uo[0], "--bytes-per-s", "20000")
    try:
        fw = FirmwareLink(link)
        fw.sync()
        stats, data = capture(fw, request(clock_hz=100_000, max_bytes=200_000, buf_words=1024))
    finally:
        link.close()
    assert stats.overruns > 0
    assert "overrun" in stats.messages
    assert stats.dropped == stats.overruns * 1024 * 4
    assert stats.messages[-1] == "overruns=%d rxstall=0 sysclk_hz=133000000" % stats.overruns
    assert not stats.clean


def test_stream_stops_between_chunks_on_request(fw):
    seen = []
    req = request(clock_hz=100_000, buf_words=512, stop=lambda: len(seen) >= 3)
    setup(fw, req)
    session = FirmwareSession(fw, req)
    out = io.BytesIO()
    for block in session.chunks():
        seen.append(block[:4])
        out.write(block)
    stats = session.stats()
    assert stats.clean, stats.format()
    assert seen[-1] == b"TIME"
    # The simulator runs ahead of the reader by whatever the pipe holds, so
    # how many chunks were already on their way when the stop arrived is
    # not fixed; that the capture ended, cleanly, after the third is.
    assert stats.chunks >= 4
    assert all(tag == b"RAW " for tag in seen[1:-1])
    # the link is back in step for the next command
    assert fw.command("status")["clock_hz"] == "100000"


def test_request_stop_from_another_thread(fw):
    req = request(clock_hz=100_000, buf_words=512, stop=lambda: False)
    setup(fw, req)
    session = FirmwareSession(fw, req)
    timer = threading.Timer(0.2, session.request_stop)
    timer.start()
    try:
        blocks = list(session.chunks())
    finally:
        timer.cancel()
    assert blocks[-1][:4] == b"TIME"
    assert session.stats().clean


def test_consumer_closing_early_leaves_the_firmware_idle(fw):
    req = request(clock_hz=100_000, buf_words=512, stop=lambda: False)
    setup(fw, req)
    session = FirmwareSession(fw, req)
    chunks = session.chunks()
    next(chunks)  # the header
    next(chunks)  # one RAW chunk
    session.close()
    assert fw.command("status")["clock_hz"] == "100000"


def test_consumer_closing_before_the_first_chunk(fw):
    req = request(clock_hz=100_000, buf_words=512, stop=lambda: False)
    setup(fw, req)
    session = FirmwareSession(fw, req)
    chunks = session.chunks()
    assert next(chunks) == stream_header_bytes(req)
    session.close()
    assert fw.command("status")["clock_hz"] == "100000"


# -- frame and window -----------------------------------------------------------------


def frame_request(**kwargs) -> CaptureRequest:
    kwargs.setdefault("clock_hz", 25_000_000)
    kwargs.setdefault("script", "frame_rp2.py")
    kwargs.setdefault("edge", "rising")
    kwargs.setdefault("max_frames", 2)
    return request(**kwargs)


def test_frame_whole_pictures(fw, bars_uo, tmp_path):
    req = frame_request()
    assert capture_command(req) == (
        "frame cpl=800 region_first=34 region_lines=481 window_lines=0 hsync_active=-1 "
        "vsync_active=-1 max_frames=2 max_bytes=0 edge=rising sample_delay=0 pio=1 sm=0"
    )
    stats, data = capture(fw, req)
    assert stats.clean, stats.format()
    assert stats.messages[0] == "frame window=481 buffers=1 hsync_active=0 vsync_active=0"
    assert stats.messages[-1] == "frames=2 stalls=0 rxstall=0 sysclk_hz=100000000"
    frams = [struct.unpack_from("<IHHII", p) for t, p in read_chunks(data) if t == "FRAM"]
    assert frams == [(0, 34, 481, 800, 481 * 800), (1, 34, 481, 800, 481 * 800)]
    frames = pictures(tmp_path, data)
    assert len(frames) == 2
    assert_pictures_exact(frames, bars_uo[1])


def test_window_pictures(fw, bars_uo, tmp_path):
    stats, data = capture(fw, frame_request(window_lines=48, max_frames=1))
    assert stats.clean, stats.format()
    assert stats.messages[0] == "frame window=48 buffers=1 hsync_active=0 vsync_active=0"
    frams = [struct.unpack_from("<IHHII", p) for t, p in read_chunks(data) if t == "FRAM"]
    assert [f[1] for f in frams] == list(range(34, 34 + 481, 48))
    assert {f[0] for f in frams} == {0}
    assert sum(f[2] for f in frams) == 481
    assert_pictures_exact(pictures(tmp_path, data), bars_uo[1])


def test_frame_polarity_given_and_other_design(tmp_path):
    image = grid(VGA.h_active, VGA.v_active)
    uo = tmp_path / "grid.uo"
    uo.write_bytes(frame_samples(VGA, image).tobytes())
    link = SimLink(uo)
    try:
        fw = FirmwareLink(link)
        fw.sync()
        stats, data = capture(fw, frame_request(max_frames=1))
    finally:
        link.close()
    assert stats.clean, stats.format()
    assert_pictures_exact(pictures(tmp_path, data), image)


def test_800x600_needs_two_windows(tmp_path):
    mode = MODES["800x600@60"]
    image = bars(mode.h_active, mode.v_active)
    uo = tmp_path / "svga.uo"
    uo.write_bytes(frame_samples(mode, image).tobytes())
    link = SimLink(uo)
    try:
        fw = FirmwareLink(link)
        fw.sync()
        stats, data = capture(fw, frame_request(clock_hz=40_000_000, vga_mode="800x600@60",
                                                max_frames=1))
    finally:
        link.close()
    assert stats.clean, stats.format()
    window = int(stats.messages[0].split()[1].split("=")[1])
    assert window < mode.v_active + 1
    frams = [struct.unpack_from("<IHHII", p) for t, p in read_chunks(data) if t == "FRAM"]
    assert len(frams) == 2
    assert_pictures_exact(pictures(tmp_path, data), image)


def test_frame_refused_at_a_clock_it_cannot_lock_to(fw):
    req = frame_request(clock_hz=100_000)
    setup(fw, req)
    session = FirmwareSession(fw, req)
    with pytest.raises(CaptureError, match="sysclk 133000000 is not 2..32 whole project clocks of 100000"):
        next(session.chunks())
    assert fw.command("status")["clock_hz"] == "100000"


def test_rxstall_is_reported(bars_uo):
    link = SimLink(bars_uo[0], "--rxstall")
    try:
        fw = FirmwareLink(link)
        fw.sync()
        stats, _ = capture(fw, frame_request(max_frames=1))
    finally:
        link.close()
    assert stats.rxstall == 1
    assert "rxstall" in stats.messages
    assert stats.messages[-1] == "frames=1 stalls=1 rxstall=1 sysclk_hz=100000000"


# -- setup and the CLI --------------------------------------------------------------------


def test_setup_refuses_a_design_selection(fw):
    with pytest.raises(CaptureError, match="cannot enable 'tt_um_x'"):
        setup(fw, request(clock_hz=100_000, max_bytes=1, project="tt_um_x"))


def test_setup_and_teardown_drive_ui_in(fw):
    did = setup(fw, request(clock_hz=25_000_000, max_bytes=1, ui_in=5))
    assert did["sysclk_hz"] == 100_000_000
    assert fw.command("status") == {"clock_hz": "25000000", "sysclk_hz": "100000000", "reset": "0", "ui_in": "5"}
    teardown(fw, did, stop_clock=True)
    assert fw.command("status") == {"clock_hz": "0", "sysclk_hz": "100000000", "reset": "0", "ui_in": "-1"}


def test_cli_frame_capture(monkeypatch, bars_uo, tmp_path, capsys):
    link = SimLink(bars_uo[0])
    monkeypatch.setattr(cli, "link_from_url", lambda url: link)
    out = tmp_path / "cli.vgacap"
    code = cli.main([
        "capture", "serial:/dev/ttyACM0", "--firmware", "picosdk", "--clock-hz", "25000000",
        "--mode", "frame", "--frames", "1", "--out", str(out),
    ])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "profile=rp2350-dbv3 project=None enable=kept (capture firmware)" in printed
    assert "frames=1 stalls=0 rxstall=0" in printed
    assert link.closed
    assert_pictures_exact(pictures(tmp_path, out.read_bytes()), bars_uo[1])


def test_cli_refuses_a_project_with_the_capture_firmware(monkeypatch, bars_uo, tmp_path, capsys):
    link = SimLink(bars_uo[0])
    monkeypatch.setattr(cli, "link_from_url", lambda url: link)
    out = tmp_path / "never.vgacap"
    code = cli.main([
        "capture", "serial:/dev/ttyACM0", "--firmware", "picosdk", "--clock-hz", "100000",
        "--project", "tt_um_x", "--out", str(out),
    ])
    assert code == 1
    assert "cannot enable 'tt_um_x'" in capsys.readouterr().err
    assert not out.exists()


# -- parity with the MicroPython programs ---------------------------------------------


def test_frame_chunks_match_frame_rp2_on_the_emulator(fw, bars_uo, tmp_path):
    # The same design through frame_rp2.py on ttcap.emu (MicroPython's own
    # assembler, the PIO interpreter, the DMA model) and through the
    # firmware: with a heap that holds the whole region both take one
    # capture a picture, so the FRAM chunks must agree field for field and
    # both hold the design's consecutive samples from the hsync edge that
    # starts line 34. Where exactly: the emulator runs the trigger cycle by
    # cycle, and its first sample lands 1-2 clocks after the edge depending
    # on the clock duty and the design's output delay (frame_rp2.py, THE
    # TRIGGER); the simulator models the delivery, not the cycles, and
    # starts on the edge (firmware/sim/sim.h). libvgaframe re-places x at
    # every hsync, so both reconstruct the same picture.
    from test_emu_frame import run_frame

    uo = np.frombuffer(bars_uo[0].read_bytes(), dtype=np.uint8)
    req = frame_request(max_frames=2)
    _, mp_out = run_frame(RP2350_DBV3, uo.tobytes(), heap=(400_000,), max_frames=2, edge="rising")
    _, fw_data = capture(fw, req)
    fw_out = fw_data[len(stream_header_bytes(req)):]

    def frams(out):
        return [payload for tag, payload in read_chunks(out) if tag == "FRAM"]

    def offset(payload):
        got = np.frombuffer(payload[16:], dtype=np.uint8).reshape(-1, 4)[:, ::-1].ravel()
        line34 = (VGA.v_sync + VGA.v_back - 1) * VGA.cpl
        for off in range(4):
            if np.array_equal(got, uo[line34 + off:line34 + off + len(got)]):
                return off
        return None

    assert len(frams(mp_out)) == len(frams(fw_out)) == 2
    for mine, theirs in zip(frams(fw_out), frams(mp_out)):
        assert mine[:16] == theirs[:16]  # counter, first line, lines, cpl, samples
        assert offset(mine) == 0
        assert offset(theirs) in (1, 2)
    times = [[p[18:].decode() for t, p in read_chunks(out) if t == "TIME"] for out in (mp_out, fw_out)]
    # The opening reports differ only in how many heap blocks MicroPython
    # needed, the summaries only in the system clock.
    assert times[0][0].split(" buffers=")[0] == times[1][0].split(" buffers=")[0]
    assert times[0][0].split()[-2:] == times[1][0].split()[-2:]
    assert times[0][-1].rsplit(" ", 1)[0] == times[1][-1].rsplit(" ", 1)[0]
    for name, out in (("mp", mp_out), ("fw", fw_out)):
        sub = tmp_path / name
        sub.mkdir()
        assert_pictures_exact(pictures(sub, stream_header_bytes(req) + out), bars_uo[1])


def test_cli_reboot_with_the_capture_firmware(monkeypatch, bars_uo, capsys):
    link = SimLink(bars_uo[0])
    monkeypatch.setattr(cli, "link_from_url", lambda url: link)
    assert cli.main(["reboot", "serial:/dev/ttyACM0", "--firmware", "picosdk"]) == 0
    assert "rebooting to flash" in capsys.readouterr().out
    assert link.proc.wait(timeout=10) == 0  # the simulated board is gone


def test_cli_reboot_into_bootsel_from_micropython(monkeypatch, capsys):
    from fake_repl import FakeBinaryBoard

    board = FakeBinaryBoard()
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    assert cli.main(["reboot", "serial:/dev/ttboard", "--to", "bootsel"]) == 0
    assert board.commands[-1] == "import machine\nmachine.bootloader()"
    assert board.closed


# -- ASIC carriers: the firmware selects the project itself ----------------------------


def _stream_samples(data: bytes) -> np.ndarray:
    raws = [payload for tag, payload in read_chunks(data) if tag == "RAW "]
    return np.concatenate([np.frombuffer(p[4:], dtype=np.uint8).reshape(-1, 4)[:, ::-1].ravel()
                           for p in raws])


@pytest.fixture
def asic_fw(bars_uo):
    """The simulated board as an ASIC carrier, the design at mux index 5."""
    link = SimLink(bars_uo[0], "--asic-index", "5")
    firmware = FirmwareLink(link)
    firmware.sync()
    yield firmware
    link.close()


def test_an_asic_project_is_dark_until_selected(asic_fw):
    """What tt09 showed on 2026-11-24: after the switch to this firmware the
    mux has no project selected, so nothing comes out at all."""
    _, data = capture(asic_fw, request(clock_hz=100_000, max_bytes=40_000))
    samples = _stream_samples(data)
    assert len(samples) and not samples.any()


def test_asic_select_brings_the_design_back(asic_fw, bars_uo, tmp_path):
    req = request(clock_hz=100_000, max_bytes=frames_to_max_bytes(RP2350_DBV3, 1), asic_index=5)
    did = setup(asic_fw, req)
    assert did["asic_index"] == 5
    out = io.BytesIO()
    stats = run_session(FirmwareSession(asic_fw, req), out)
    assert stats.clean, stats.format()
    assert_pictures_exact(pictures(tmp_path, out.getvalue()), bars_uo[1])


def test_the_wrong_asic_index_selects_another_project(asic_fw):
    _, data = capture(asic_fw, request(clock_hz=100_000, max_bytes=40_000, asic_index=4))
    assert not _stream_samples(data).any()


def test_asic_select_validates_its_index(fw):
    with pytest.raises(CaptureError, match="index"):
        fw.command("asic_select index=1024")
    with pytest.raises(CaptureError, match="missing key 'index'"):
        fw.command("asic_select")
    assert fw.command("asic_select index=0") == {"index": "0"}


def test_asic_index_needs_the_capture_firmware(tmp_path):
    with pytest.raises(ValueError, match="--asic-index is for --firmware picosdk"):
        cli.capture("serial:/dev/null", tmp_path / "x.vgacap", clock_hz=100_000,
                    seconds=1, max_bytes=0, asic_index=3)
