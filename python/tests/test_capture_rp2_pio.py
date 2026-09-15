# SPDX-License-Identifier: Apache-2.0
"""Host-side tests for the MicroPython PIO+DMA capture script.

`rp2`/`machine` do not exist on the host, so the script as a whole can only
be `compile()`d, not executed. What *can* be checked here:

* `CFG` substitution produces valid Python for both board profiles;
* the PIO program text has the exact external-clock instruction sequence;
* the RX FIFO address and DREQ arithmetic inlined in the script agrees with
  `ttcap.boards.rxf_addr()` / `rx_dreq()` and with the pico-sdk values;
* the chunk framing helpers, lifted out of the script by name and executed
  on their own, emit chunks that `vgacap.stream.read_chunks()` parses.
"""

from __future__ import annotations

import ast
import io
import struct

import pytest

import ttcap.mp as mp
from ttcap.boards import RP2040_TT06, RP2350_DBV3, rx_dreq, rxf_addr
from ttcap.capture import capture_cfg
from vgacap.stream import read_chunks

SCRIPT = "capture_rp2.py"


@pytest.fixture(scope="module")
def source() -> str:
    return mp.load(SCRIPT)


def _exec_functions(source: str, names) -> dict:
    """Execute just the named top-level functions of `source`, in order."""
    tree = ast.parse(source)
    wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    missing = set(names) - {n.name for n in wanted}
    assert not missing, f"script has no top-level function(s) {sorted(missing)}"
    module = ast.Module(body=wanted, type_ignores=[])
    namespace: dict = {}
    exec(compile(module, SCRIPT, "exec"), namespace)  # noqa: S102 - lifting board code
    return namespace


# -- (a) CFG substitution compiles for both profiles ----------------------


@pytest.mark.parametrize("profile", [RP2040_TT06, RP2350_DBV3], ids=lambda p: p.name)
def test_script_compiles_with_each_board_profile(source, profile):
    compile(mp.with_cfg(source, capture_cfg(profile)), SCRIPT, "exec")


@pytest.mark.parametrize("edge", ["falling", "rising"])
def test_script_compiles_for_each_edge(source, edge):
    cfg = capture_cfg(RP2040_TT06, edge=edge)
    compile(mp.with_cfg(source, cfg), SCRIPT, "exec")


# -- (b) the PIO instruction sequence -------------------------------------


def test_falling_edge_program_waits_high_then_low_then_samples(source):
    high = source.index("wait(1, gpio,")
    low = source.index("wait(0, gpio,")
    sample = source.index("in_(pins,")
    assert high < low < sample, "falling-edge sampler must be wait(1) -> wait(0) -> in_"


def test_rising_edge_program_waits_low_then_high_then_samples(source):
    # The rising-edge variant is the second asm_pio block in the file.
    tail = source[source.index("in_(pins,") :]
    low = tail.index("wait(0, gpio,")
    high = tail.index("wait(1, gpio,")
    sample = tail.index("in_(pins,", 1)
    assert low < high < sample, "rising-edge sampler must be wait(0) -> wait(1) -> in_"


def test_program_uses_shift_right_autopush_and_join_rx(source):
    assert "in_shiftdir=rp2.PIO.SHIFT_RIGHT" in source
    assert "autopush=True" in source
    assert "push_thresh=PUSH_THRESH" in source
    assert "fifo_join=rp2.PIO.JOIN_RX" in source


def test_rp2350_sets_the_pio_gpio_base_before_the_state_machine(source):
    # gpio_base shifts the whole PIO block's pin window; it must be set
    # before the state machine is created, and only on RP2350 (base 16).
    assert ".gpio_base(GPIO_BASE)" in source
    assert source.index(".gpio_base(GPIO_BASE)") < source.index("rp2.StateMachine(")


# -- (c) RX FIFO address and DREQ arithmetic ------------------------------


def test_rxf_addr_matches_the_pico_sdk_layout():
    assert rxf_addr(0, 0) == 0x50200020
    assert rxf_addr(1, 2) == 0x50300028
    assert rxf_addr(2, 0) == 0x50400020


def test_rx_dreq_matches_the_pico_sdk_table():
    assert rx_dreq(0, 0) == 4
    assert rx_dreq(1, 1) == 13
    assert rx_dreq(2, 0) == 20


def test_script_inlines_the_same_address_and_dreq_arithmetic(source):
    cfg = capture_cfg(RP2350_DBV3)
    cfg["pio"] = 1
    cfg["sm"] = 2
    namespace: dict = {"CFG": cfg}
    # Execute only the constant block (everything before the first import of
    # a board-only module), which is where the script inlines the arithmetic.
    tree = ast.parse(mp.with_cfg(source, cfg))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if not isinstance(node, (ast.Assign, ast.Expr)):
            break
        body.append(node)
    exec(compile(ast.Module(body=body, type_ignores=[]), SCRIPT, "exec"), namespace)  # noqa: S102

    assert namespace["RXF_ADDR"] == rxf_addr(1, 2)
    assert namespace["DREQ"] == rx_dreq(1, 2)
    assert namespace["PIO_BASE"] == 0x50300000
    assert namespace["FDEBUG_ADDR"] == 0x50300008
    assert namespace["SAMPLES_PER_WORD"] == RP2350_DBV3.samples_per_word


def _exec_constant_block(source: str, cfg: dict) -> dict:
    """Execute the script's leading constant assignments with `cfg` bound."""
    namespace: dict = {"CFG": cfg}
    body = []
    for node in ast.parse(mp.with_cfg(source, cfg)).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if not isinstance(node, (ast.Assign, ast.Expr)):
            break
        body.append(node)
    exec(compile(ast.Module(body=body, type_ignores=[]), SCRIPT, "exec"), namespace)  # noqa: S102
    return namespace


@pytest.mark.parametrize("profile", [RP2040_TT06, RP2350_DBV3], ids=lambda p: p.name)
def test_clk_wait_index_is_relative_to_the_pio_gpio_base(source, profile):
    # PIO hardware adds the block's GPIOBASE to a WAIT GPIO index and the
    # assembler emits that index verbatim (it cannot know which block the
    # program will be loaded into), so the script must subtract GPIO_BASE.
    namespace = _exec_constant_block(source, capture_cfg(profile))

    assert namespace["CLK_PIO_INDEX"] == profile.clk_gpio - profile.pio_gpio_base
    assert 0 <= namespace["CLK_PIO_INDEX"] < 32
    assert "wait(1, gpio, CLK_PIO_INDEX)" in source


# -- (d) chunk framing ----------------------------------------------------


def test_write_raw_chunk_frames_a_buffer_vgacap_can_parse():
    buf_words = 8
    samples_per_word = RP2040_TT06.samples_per_word
    helpers = _exec_functions(mp.load(SCRIPT), ["_le32", "_write_all", "write_raw_chunk"])

    out = io.BytesIO()
    buf = bytearray(range(4 * buf_words))
    helpers["write_raw_chunk"](out, buf, buf_words * samples_per_word)

    chunks = list(read_chunks(out.getvalue()))
    assert len(chunks) == 1
    tag, payload = chunks[0]
    assert tag == "RAW "
    assert len(payload) == 4 + 4 * buf_words
    assert struct.unpack_from("<I", payload, 0)[0] == buf_words * samples_per_word
    assert payload[4:] == bytes(buf)


def test_write_time_chunk_frames_an_overrun_report():
    helpers = _exec_functions(mp.load(SCRIPT), ["_le32", "_write_all", "write_time_chunk"])

    out = io.BytesIO()
    helpers["write_time_chunk"](out, 4096, b"overrun")

    (tag, payload), = read_chunks(out.getvalue())
    assert tag == "TIME"
    host_time_ns, clock_hz, dropped, msg_len = struct.unpack_from("<QIIH", payload, 0)
    assert (host_time_ns, clock_hz, dropped, msg_len) == (0, 0, 4096, 7)
    assert payload[18 : 18 + msg_len] == b"overrun"
    assert len(payload) == 18 + msg_len


def test_raw_chunk_payload_matches_the_profile_sample_count():
    helpers = _exec_functions(mp.load(SCRIPT), ["_le32", "_write_all", "write_raw_chunk"])
    for profile in (RP2040_TT06, RP2350_DBV3):
        cfg = capture_cfg(profile, buf_words=4096)
        samples_per_word = cfg["push_thresh"] // cfg["in_count"]
        assert samples_per_word == profile.samples_per_word

        out = io.BytesIO()
        helpers["write_raw_chunk"](
            out, bytearray(4 * cfg["buf_words"]), cfg["buf_words"] * samples_per_word
        )
        (tag, payload), = read_chunks(out.getvalue())
        assert tag == "RAW "
        assert struct.unpack_from("<I", payload, 0)[0] == cfg["buf_words"] * samples_per_word
        assert len(payload) == 4 + 4 * cfg["buf_words"]


# -- capture_cfg ----------------------------------------------------------


def test_capture_cfg_for_rp2040():
    cfg = capture_cfg(RP2040_TT06)
    assert cfg == {
        "clk_gpio": 0,
        "in_base": 5,
        "in_count": 12,
        "gpio_base": 0,
        "push_thresh": 24,
        "buf_words": 4096,
        "max_bytes": 0,
        "edge": "falling",
        "pio": 0,
        "sm": 0,
        "sysclk_hz": 133_000_000,
    }


def test_capture_cfg_for_rp2350():
    cfg = capture_cfg(RP2350_DBV3, buf_words=1024, max_bytes=65536, edge="rising")
    assert cfg["clk_gpio"] == 16
    assert cfg["in_base"] == 33
    assert cfg["in_count"] == 8
    assert cfg["gpio_base"] == 16
    assert cfg["push_thresh"] == 32
    assert cfg["buf_words"] == 1024
    assert cfg["max_bytes"] == 65536
    assert cfg["edge"] == "rising"


def test_capture_cfg_rejects_an_unknown_edge():
    with pytest.raises(ValueError):
        capture_cfg(RP2040_TT06, edge="both")


def test_capture_cfg_rejects_a_non_positive_buf_words():
    with pytest.raises(ValueError):
        capture_cfg(RP2040_TT06, buf_words=0)
