# SPDX-License-Identifier: Apache-2.0
"""The board emulator (`ttcap.emu`), and the built capture script run on it.

The emulator's own pieces are checked against facts measured on hardware
or taken from the datasheet; then the exact script `ttcap capture` ships
(`mp.build(cfg, "capture_rp2.py")`) runs on it end to end and its stream is
reconstructed by `vgacap-frames`.
"""

from __future__ import annotations

import pathlib
import subprocess

import numpy as np
import pytest
from pio_stub import asm_pio as stub_asm_pio

from ttcap import mp
from ttcap.boards import RP2040_TT06, RP2350_DBV3
from ttcap.capture import CaptureRequest, capture_cfg, stream_header_bytes
from ttcap.emu import Board, BoardError, Waveform
from ttcap.emu.board import assemble
from vgacap.modes import MODES
from vgacap.ppm import read_ppm
from vgacap.stream import read_chunks
from vgacap.synth import bars, colour6_to_rgb, frame_samples, to_rp2040_layout

FRAMES = pathlib.Path(__file__).resolve().parents[2] / "build" / "vgacap-frames"
MODE = MODES["640x480@60"]


@pytest.fixture(scope="module")
def bars_uo() -> bytes:
    return frame_samples(MODE, bars(640, 480)).tobytes()


def time_messages(stream: bytes) -> list[str]:
    return [payload[18:].decode() for tag, payload in read_chunks(stream) if tag == "TIME"]


def reconstruct(tmp_path: pathlib.Path, profile, clock_hz: int, board_bytes: bytes):
    req = CaptureRequest(
        profile=profile, project=None, design=None, clock_hz=clock_hz, seconds=0, max_bytes=1
    )
    stream = tmp_path / "emu.vgacap"
    stream.write_bytes(stream_header_bytes(req) + board_bytes)
    out = subprocess.run(
        [str(FRAMES), str(stream), str(tmp_path / "f")], capture_output=True, text=True, check=True
    ).stdout
    return out, sorted(tmp_path.glob("f-*.ppm"))


# -- the waveform ---------------------------------------------------------


def test_waveform_clock_and_data_timing():
    wave = Waveform(bytes([0x00, 0xFF, 0x00]), uo_gpios=[33], clk_gpio=16, period=5, high=2, delay=1)

    assert [wave.value(16, c) for c in range(6)] == [1, 1, 0, 0, 0, 1]
    # uo_out takes clock k's value `delay` cycles after clock k's rising edge.
    assert [wave.value(33, c) for c in range(0, 12)] == [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 0]
    assert wave.next_time(33, 1, 0) == 6
    assert wave.next_time(33, 0, 6) == 11
    # Repeats: clock 3 is clock 0 again, so the next high is clock 4.
    assert wave.next_time(33, 1, 11) == 21
    assert wave.next_time(16, 0, 0) == 2
    assert wave.next_time(16, 1, 2) == 5


def test_configuring_the_clock_pad_stops_the_clock():
    # Measured on fpga-1: machine.Pin(clk, Pin.IN) takes the pad from the
    # firmware's PWM and the sampler waits forever.
    wave = Waveform(b"\0", uo_gpios=[], clk_gpio=16, period=4)
    machine = Board(wave).modules()["machine"]

    machine.Pin(16, machine.Pin.IN)

    assert wave.next_time(16, 1, 3) is None


# -- the assembler is MicroPython's own -----------------------------------


def test_the_stub_assembler_agrees_with_micropython_v1_24_0():
    def program(asm_pio, shift_left, join_rx):
        @asm_pio(in_shiftdir=shift_left, autopush=True, push_thresh=32, fifo_join=join_rx)
        def sampler():
            wrap_target()  # noqa: F821
            wait(1, gpio, 16)  # noqa: F821
            wait(0, gpio, 16)  # noqa: F821
            in_(pins, 8)  # noqa: F821
            wrap()  # noqa: F821

        return sampler

    upstream = assemble(lambda rp2: program(rp2.asm_pio, rp2.PIO.SHIFT_LEFT, rp2.PIO.JOIN_RX))
    stub = program(stub_asm_pio, 0, 2)

    assert list(upstream[0]) == list(stub.instructions) == [0x2090, 0x2010, 0x4008]


# -- the shipped continuous-capture script, end to end --------------------


def test_capture_script_on_the_emulator_is_pixel_exact(tmp_path, bars_uo):
    profile = RP2350_DBV3
    # 500 kHz project clock at 133 MHz, the HIL check's rate.
    wave = Waveform(bars_uo, profile.uo_gpios, profile.clk_gpio, period=266)
    board = Board(wave)
    board.stop_after_bytes = 3 * MODE.cpl * MODE.lpf

    out = board.run_script(mp.build(capture_cfg(profile), "capture_rp2.py"))

    assert time_messages(out)[-1] == "overruns=0 rxstall=0 sysclk_hz=133000000"
    report, frames = reconstruct(tmp_path, profile, 500_000, out)
    assert "glitches_total=0" in report
    assert len(frames) >= 2
    want = colour6_to_rgb(bars(640, 480))
    for frame in frames:
        assert np.array_equal(read_ppm(frame), want), frame.name


def test_capture_script_on_an_rp2040_board_is_pixel_exact(tmp_path, bars_uo):
    # The RP2040's 12-bit window holds ui_in 9..12 between the uo_out pads;
    # the board drives those, so the emulator reads them as constants.
    profile = RP2040_TT06
    wave = Waveform(bars_uo, profile.uo_gpios, profile.clk_gpio, period=2216,
                    constants={9: 1, 10: 0, 11: 1, 12: 0})
    board = Board(wave, chip="rp2040", sysclk_hz=133_000_000,
                  heap_blocks=(48_000, 20_000), cycles_per_byte=887,
                  max_cycles=6_000_000_000)
    board.stop_after_bytes = 3 * MODE.cpl * MODE.lpf * 2

    out = board.run_script(mp.build(capture_cfg(profile), "capture_rp2.py"))

    assert time_messages(out)[-1].startswith("overruns=0 rxstall=0")
    report, frames = reconstruct(tmp_path, profile, 60_000, out)
    assert len(frames) >= 2
    want = colour6_to_rgb(bars(640, 480))
    assert all(np.array_equal(read_ppm(f), want) for f in frames)


def test_a_clock_the_link_cannot_carry_is_reported_as_overruns(bars_uo):
    profile = RP2350_DBV3
    # 2.66 MHz of one-byte samples is ~3.8x what the modelled USB carries.
    wave = Waveform(bars_uo, profile.uo_gpios, profile.clk_gpio, period=50)
    board = Board(wave)
    board.stop_after_bytes = 200_000

    out = board.run_script(mp.build(capture_cfg(profile), "capture_rp2.py"))

    messages = time_messages(out)
    assert "overrun" in messages
    summary = messages[-1]
    overruns = int(summary.split()[0].split("=")[1])
    assert overruns > 0
    # Every TIME chunk carries the cumulative dropped count, so it never
    # goes down.
    dropped = [int.from_bytes(p[12:16], "little") for t, p in read_chunks(out) if t == "TIME"]
    assert dropped == sorted(dropped)
    assert dropped[-1] == overruns * 4096 * 4


#: A counting design whose period (251) divides no buffer length, so a
#: chunk that does not continue the one before it shows as a jump.
COUNT = 251


def _raw_samples(payload: bytes) -> list[int]:
    """The 8-bit samples of an RP2350 RAW chunk, first sample in each
    word's top byte (FLAG_FIRST_SAMPLE_MSB)."""
    words = payload[4:]
    return [b for i in range(0, len(words), 4) for b in reversed(words[i : i + 4])]


def _damaged_and_reported(out: bytes) -> list[tuple[bool, bool]]:
    """(damaged, reported) per RAW chunk of the counting design: damaged
    if its samples jump inside it or it does not continue the chunk
    before, reported if the next chunk is an `overrun` TIME chunk."""
    items = list(read_chunks(out))
    result, last = [], None
    for i, (tag, payload) in enumerate(items):
        if tag != "RAW ":
            continue
        s = _raw_samples(payload)
        damaged = any((b - a) % COUNT != 1 for a, b in zip(s, s[1:]))
        damaged |= last is not None and (s[0] - last) % COUNT != 1
        last = s[-1]
        after = items[i + 1] if i + 1 < len(items) else ("", b"")
        result.append((damaged, after[0] == "TIME" and after[1][18:] == b"overrun"))
    return result


def _old_overrun_check(script: str) -> str:
    # Before the fix: an overrun was counted only when a buffer completed
    # again while still FULL.
    for me, other in ((0, 1), (1, 0)):
        new = "if FULL[%d]:\n  OVERRUNS[0] += 1\n  TORN[%d] = True\n FULL[%d] = True" % (other, other, me)
        assert new in script
        script = script.replace(
            new, "if FULL[%d]:\n  OVERRUNS[0] += 1\n  TORN[%d] = True\n FULL[%d] = True" % (me, me, me)
        )
    return script


def _stalled_capture(check: str) -> bytes:
    profile = RP2350_DBV3
    period = 100  # a buffer fills in 16384 * 100 cycles
    fill = 16_384 * period
    wave = Waveform(bytes(range(COUNT)), profile.uo_gpios, profile.clk_gpio, period=period)
    # The link is twice as fast as the samples, so nothing is lost -- except
    # that halfway through the second chunk the host stops reading for 1.36
    # fills. By then the chain has started rewriting that buffer and runs
    # past the send's position; the send then finishes before the buffer
    # completes again.
    board = Board(wave, cycles_per_byte=period / 2)
    board.usb_stalls = [(16_396 + 8_000, int(1.36 * fill))]
    board.stop_after_bytes = 6 * 16_400
    script = mp.build(capture_cfg(profile), "capture_rp2.py")
    return board.run_script(_old_overrun_check(script) if check == "old" else script)


def test_a_chunk_the_dma_rewrote_while_it_was_sent_is_reported():
    chunks = _damaged_and_reported(_stalled_capture("new"))

    assert chunks[1] == (True, True)
    assert all(reported for damaged, reported in chunks if damaged)


def test_the_old_overrun_check_misses_that_chunk():
    # What the fix is for: counted at completion, the overrun is never seen,
    # and the damaged chunk goes out as if it were contiguous.
    chunks = _damaged_and_reported(_stalled_capture("old"))

    assert chunks[1] == (True, False)


def test_a_link_that_keeps_up_damages_and_reports_nothing():
    profile = RP2350_DBV3
    wave = Waveform(bytes(range(COUNT)), profile.uo_gpios, profile.clk_gpio, period=266)
    board = Board(wave)
    board.stop_after_bytes = 4 * 16_400

    out = board.run_script(mp.build(capture_cfg(profile), "capture_rp2.py"))

    chunks = _damaged_and_reported(out)
    assert chunks and not any(d or r for d, r in chunks)
    assert time_messages(out)[-1].startswith("overruns=0 ")


def _old_stream_teardown(script: str) -> str:
    new = "stop_dma((dma_a, dma_b))"
    assert new in script
    return script.replace(
        new,
        "for dma in (dma_a, dma_b):\n   if dma is not None:\n    dma.active(0)\n"
        "  for dma in (dma_a, dma_b):\n   if dma is not None:\n    dma.close()",
    )


@pytest.mark.parametrize("teardown", ["stop_dma", "old"])
def test_a_stopped_stream_leaves_no_dma_channel_running(bars_uo, teardown):
    # A and B chain to each other, so on RP2350 aborting one re-triggers
    # the other (RP2350-E5) unless EN is clear on both first.
    profile = RP2350_DBV3
    board = Board(Waveform(bars_uo, profile.uo_gpios, profile.clk_gpio, period=266))
    board.stop_after_bytes = 20_000

    script = mp.build(capture_cfg(profile), "capture_rp2.py")
    board.run_script(_old_stream_teardown(script) if teardown == "old" else script)

    busy = [ch.num for ch in board.channels if ch.busy]
    if teardown == "old":
        assert busy, "the emulator no longer models the re-trigger"
    else:
        assert busy == []


def test_a_dma_buffer_larger_than_the_heap_allows_fails_like_the_board(bars_uo):
    profile = RP2350_DBV3
    wave = Waveform(bars_uo, profile.uo_gpios, profile.clk_gpio, period=266)
    board = Board(wave, heap_blocks=(10_000,))
    board.stop_after_bytes = 1

    with pytest.raises(MemoryError):
        board.run_script(mp.build(capture_cfg(profile, buf_words=4096), "capture_rp2.py"))


def test_unsupported_instructions_are_refused_not_guessed():
    def program(rp2):
        @rp2.asm_pio()
        def p():
            irq(0)  # noqa: F821

        return p

    wave = Waveform(b"\0", uo_gpios=[], clk_gpio=0, period=2)
    board = Board(wave)
    rp2 = board.modules()["rp2"]
    with pytest.raises(BoardError, match="not emulated"):
        rp2.StateMachine(4, program(rp2))


# -- instructions the PIO run-length program added --------------------------


def _sm_with(board, build, in_base, pio=0):
    rp2 = board.modules()["rp2"]
    machine = board.modules()["machine"]
    return rp2.StateMachine(4 * pio, build(rp2), in_base=machine.Pin(in_base)), board.blocks[pio].sms[0]


def _mov_isr_pins(rp2):
    @rp2.asm_pio(push_thresh=32)
    def p():
        mov(isr, pins)  # noqa: F821
        push(noblock)  # noqa: F821

    return p


def test_mov_from_pins_reads_32_pins_from_in_base_round_the_window():
    # RP2040 datasheet 3.4.8: `mov` from PINS uses the IN mapping, 32 pins
    # from IN_BASE wrapping modulo 32. Pad 3 is uo_out bit 0 and pad 1 a
    # constant 1, so with in_base 3 the constant lands at bit 30; bit 29
    # is the clock pad (GPIO 0), high in the first half of the period.
    wave = Waveform(bytes([1]), uo_gpios=[3], clk_gpio=0, period=4, constants={1: 1})
    board = Board(wave, chip="rp2040")
    sm, pio_sm = _sm_with(board, _mov_isr_pins, 3)
    sm.active(1)
    board.advance(2)
    assert pio_sm.rx[0] == 1 | (1 << 29) | (1 << 30)


def test_in_count_masks_mov_from_pins_on_rp2350_only():
    wave = Waveform(bytes([0xA5]), uo_gpios=list(range(33, 41)), clk_gpio=16, period=4,
                    constants={41: 1, 17: 1})
    board = Board(wave)
    mem32 = board.modules()["machine"].mem32
    board.modules()["rp2"].PIO(1).gpio_base(16)
    sm, pio_sm = _sm_with(board, _mov_isr_pins, 33, pio=1)
    shiftctrl = 0x50300000 + 0xD0
    mem32[shiftctrl | 0x2000] = 8  # the atomic SET alias
    assert mem32[shiftctrl] & 0x1F == 8
    sm.active(1)
    board.advance(2)
    assert pio_sm.rx == [0xA5]

    other = Board(wave, chip="rp2040")
    with pytest.raises(BoardError, match="RP2040"):
        other.modules()["machine"].mem32[(0x50200000 + 0xD0) | 0x2000] = 8


def test_a_nonblocking_push_to_a_full_fifo_drops_the_word_and_sets_rxstall():
    # RP2040 datasheet, FDEBUG.RXSTALL: "also set when a nonblocking PUSH
    # to a full FIFO took place, in which case the state machine has
    # dropped data".
    wave = Waveform(b"\0", uo_gpios=[], clk_gpio=0, period=4)
    board = Board(wave)

    def build(rp2):
        @rp2.asm_pio()
        def p():
            set(x, 7)  # noqa: F821
            in_(x, 32)  # noqa: F821
            push(noblock)  # noqa: F821

        return p

    sm, pio_sm = _sm_with(board, build, 0)
    sm.active(1)
    board.advance(3 * 6)
    assert pio_sm.rx == [7, 7, 7, 7]
    assert board.blocks[0].fdebug & 1
