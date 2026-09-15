# SPDX-License-Identifier: Apache-2.0
"""Host-side tests for the MicroPython PIO+DMA capture script.

`rp2`/`machine` do not exist on the host, so the script as a whole can only
be `compile()`d. Everything else is tested by lifting individual top-level
functions out of the script with `ast` and running them against stubs:

* `make_sampler()` against `pio_stub`, which reproduces MicroPython's
  `asm_pio` contract -- including the globals swap that made the first
  version of this script die with `NameError` on the board -- and assembles
  real instruction words, so the assertions are on encodings rather than on
  substrings of the source;
* `init_input_pins()` against a recording `Pin` class;
* `write_raw_chunk()` / `write_time_chunk()` against `vgacap.stream`;
* `dma_reg()` against the RP2040/RP2350 DMA register map.
"""

from __future__ import annotations

import ast
import io
import struct

import pytest
from pio_stub import PIO as StubPIO
from pio_stub import asm_pio as stub_asm_pio

import ttcap.mp as mp
from ttcap.boards import RP2040_TT06, RP2350_DBV3, rx_dreq, rxf_addr
from ttcap.capture import capture_cfg
from vgacap.stream import read_chunks

SCRIPT = "capture_rp2.py"

#: A stub `rp2` module: only `asm_pio` and `PIO` are reachable from the
#: functions these tests lift out of the script.
STUB_RP2 = type("rp2", (), {"asm_pio": staticmethod(stub_asm_pio), "PIO": StubPIO})


@pytest.fixture(scope="module")
def source() -> str:
    return mp.load(SCRIPT)


def _exec_functions(source: str, names, globals_=None) -> dict:
    """Execute just the named top-level functions of `source`, in order."""
    tree = ast.parse(source)
    wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    missing = set(names) - {n.name for n in wanted}
    assert not missing, f"script has no top-level function(s) {sorted(missing)}"
    module = ast.Module(body=wanted, type_ignores=[])
    namespace: dict = dict(globals_ or {})
    exec(compile(module, SCRIPT, "exec"), namespace)  # noqa: S102 - lifting board code
    return namespace


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


def _assemble(source: str, cfg: dict, rising: bool = False):
    """Assemble the script's sampler for `cfg` using the stub assembler.

    `clk_gpio` goes in absolute, as `CLK_WAIT_GPIO` does on the board.
    """
    namespace = _exec_functions(source, ["make_sampler"], {"rp2": STUB_RP2})
    return namespace["make_sampler"](
        cfg["clk_gpio"], cfg["in_count"], cfg["push_thresh"], rising
    )


# -- (a) CFG substitution compiles for both profiles ----------------------


@pytest.mark.parametrize("profile", [RP2040_TT06, RP2350_DBV3], ids=lambda p: p.name)
def test_script_compiles_with_each_board_profile(source, profile):
    compile(mp.with_cfg(source, capture_cfg(profile)), SCRIPT, "exec")


@pytest.mark.parametrize("edge", ["falling", "rising"])
def test_script_compiles_for_each_edge(source, edge):
    compile(mp.with_cfg(source, capture_cfg(RP2040_TT06, edge=edge)), SCRIPT, "exec")


# -- (b) the assembled PIO program ----------------------------------------
#
# WAIT  0x2000 | polarity<<7 | src<<5 | index   (gpio is src 0)
# IN    0x4000 | src<<5 | (bits & 31)           (pins is src 0)


def test_falling_edge_program_waits_high_then_low_then_samples(source):
    program = _assemble(source, capture_cfg(RP2040_TT06))

    assert program.instructions == (0x2080, 0x2000, 0x400C)
    assert (program.wrap_target, program.wrap) == (0, 3)


def test_rising_edge_program_waits_low_then_high_then_samples(source):
    program = _assemble(source, capture_cfg(RP2040_TT06, edge="rising"), rising=True)

    assert program.instructions == (0x2000, 0x2080, 0x400C)


def test_rp2350_program_samples_eight_bits(source):
    program = _assemble(source, capture_cfg(RP2350_DBV3))

    # The wait index is the absolute clk GPIO, 16, even though the block's
    # window is based at 16: measured on fpga-1, `wait(1, gpio, 16)` samples
    # and `wait(1, gpio, 0)` stalls forever.
    assert program.instructions == (0x2090, 0x2010, 0x4008)


def test_wait_index_is_the_absolute_gpio_not_the_window_offset(source):
    # A case where clk_gpio and gpio_base differ by something other than 0
    # or the base itself, so a stray subtraction would be visible.
    cfg = capture_cfg(RP2350_DBV3)
    cfg["clk_gpio"] = 20
    program = _assemble(source, cfg)

    assert program.instructions[0] == 0x2094
    assert program.instructions[1] == 0x2014


@pytest.mark.parametrize(
    "profile,push", [(RP2040_TT06, 24), (RP2350_DBV3, 32)], ids=lambda v: getattr(v, "name", v)
)
def test_program_config_is_shift_left_autopush_join_rx(source, profile, push):
    program = _assemble(source, capture_cfg(profile))

    assert program.config == {
        "in_shiftdir": StubPIO.SHIFT_LEFT,
        "autopush": True,
        "push_thresh": push,
        "fifo_join": StubPIO.JOIN_RX,
    }
    assert push == profile.push_thresh


def test_sampler_operands_are_closures_not_globals(source):
    # Regression guard for C1: asm_pio clears the decorated function's
    # globals, so a program body that reads a module-level constant dies
    # with NameError on the board. Prove the harness enforces that, then
    # prove the real script does not trip over it.
    namespace = {"rp2": STUB_RP2, "CLK": 7, "N": 12}
    bad = (
        "def make_bad():\n"
        "    @rp2.asm_pio(autopush=True, push_thresh=24)\n"
        "    def sampler():\n"
        "        wrap_target()\n"
        "        wait(1, gpio, CLK)\n"
        "        in_(pins, N)\n"
        "        wrap()\n"
        "    return sampler\n"
    )
    exec(compile(bad, "<bad>", "exec"), namespace)  # noqa: S102
    with pytest.raises(NameError):
        namespace["make_bad"]()
    # ... and the globals survive the failed assembly.
    assert namespace["CLK"] == 7

    _assemble(source, capture_cfg(RP2040_TT06))  # does not raise


def test_rp2350_sets_the_pio_gpio_base_before_the_state_machine(source):
    # gpio_base shifts the whole PIO block's pin window; it must be set
    # before the state machine is created.
    assert ".gpio_base(GPIO_BASE)" in source
    assert source.index(".gpio_base(GPIO_BASE)") < source.index("rp2.StateMachine(")


class _StubPioBlock:
    """A PIO block whose window may or may not be movable, like the real one."""

    def __init__(self, base: int, movable: bool) -> None:
        self.base = base
        self.movable = movable
        self.moves = 0

    def gpio_base(self, want=None):
        if want is None:
            # Matches ports/rp2/rp2_pio.c, which returns a Pin, not an int.
            return "Pin(GPIO%d, mode=IN)" % self.base
        self.moves += 1
        if not self.movable:
            # pico-sdk pio_set_gpio_base_unsafe() -> PICO_ERROR_INVALID_STATE
            # whenever the block's instruction memory is already in use.
            raise OSError(22, "EINVAL")
        self.base = want


def test_gpio_base_is_reads_the_pin_the_board_returns(source):
    check = _exec_functions(source, ["gpio_base_is"])["gpio_base_is"]

    assert check(_StubPioBlock(16, True), 16)
    assert not check(_StubPioBlock(0, True), 16)
    # Not a prefix match on the number: GPIO16 must not satisfy a want of 1.
    assert not check(_StubPioBlock(16, True), 1)


def test_main_only_moves_the_gpio_base_when_it_is_wrong(source):
    main_src = ast.unparse(_main_node(source))

    # Guard before the move, and a re-check after it, so a refusal is
    # reported rather than leaving the sampler pointed at the wrong pins.
    assert main_src.count("gpio_base_is(block, GPIO_BASE)") == 2
    assert main_src.index("gpio_base_is(block, GPIO_BASE)") < main_src.index(
        "block.gpio_base(GPIO_BASE)"
    )
    assert "gpio_base is not " in main_src
    assert "write_time_chunk" in main_src


def test_gpio_base_is_lets_a_block_already_at_the_right_base_be_left_alone(source):
    # PIO1 and PIO2 on the stock RP2350 firmware are already at base 16 and
    # would refuse to be moved; the capture must not ask them to.
    check = _exec_functions(source, ["gpio_base_is"])["gpio_base_is"]
    block = _StubPioBlock(16, movable=False)

    assert check(block, 16)
    assert block.moves == 0


# -- (c) RX FIFO address, DREQ and DMA register arithmetic ----------------


def test_rxf_addr_matches_the_pico_sdk_layout():
    assert rxf_addr(0, 0) == 0x50200020
    assert rxf_addr(1, 2) == 0x50300028
    assert rxf_addr(2, 0) == 0x50400020


def test_rx_dreq_matches_the_pico_sdk_table():
    assert rx_dreq(0, 0) == 4
    assert rx_dreq(1, 1) == 13
    assert rx_dreq(2, 0) == 20


def test_script_inlines_the_same_address_and_dreq_arithmetic(source):
    cfg = capture_cfg(RP2350_DBV3, pio=1, sm=2)
    namespace = _exec_constant_block(source, cfg)

    assert namespace["RXF_ADDR"] == rxf_addr(1, 2)
    assert namespace["DREQ"] == rx_dreq(1, 2)
    assert namespace["PIO_BASE"] == 0x50300000
    assert namespace["FDEBUG_ADDR"] == 0x50300008
    assert namespace["RXSTALL_BIT"] == 1 << 2
    assert namespace["SAMPLES_PER_WORD"] == RP2350_DBV3.samples_per_word


@pytest.mark.parametrize("profile", [RP2040_TT06, RP2350_DBV3], ids=lambda p: p.name)
def test_the_two_pin_conventions_are_applied_the_right_way_round(source, profile):
    # `wait ... gpio` takes the absolute GPIO; `StateMachine(in_base=...)`
    # takes a pin relative to the block's 32-pin window. Verified on fpga-1
    # (RP2350, MicroPython 1.29-preview): swapping either one gives a state
    # machine that never produces a sample.
    namespace = _exec_constant_block(source, capture_cfg(profile))

    assert namespace["CLK_WAIT_GPIO"] == profile.clk_gpio
    assert namespace["IN_PIO_INDEX"] == profile.in_base - profile.pio_gpio_base
    assert 0 <= namespace["IN_PIO_INDEX"]
    assert namespace["IN_PIO_INDEX"] + profile.in_count <= 32


def test_state_machine_gets_the_window_relative_in_base(source):
    assert "in_base=machine.Pin(IN_PIO_INDEX)" in source
    # ... while the pads are still brought up by their absolute numbers.
    assert "init_input_pins(machine.Pin, IN_BASE, IN_COUNT)" in source


def test_dma_register_offsets_are_the_non_trigger_aliases(source):
    namespace = _exec_functions(source, ["dma_reg"], _exec_constant_block(source, capture_cfg(RP2040_TT06)))
    dma_reg = namespace["dma_reg"]

    # RP2040/RP2350 DMA: base 0x50000000, 0x40 per channel, READ_ADDR +0x00,
    # WRITE_ADDR +0x04, TRANS_COUNT +0x08, CTRL_TRIG +0x0c.
    assert namespace["DMA_BASE"] == 0x50000000
    assert namespace["DMA_CH_STRIDE"] == 0x40
    assert namespace["DMA_CH_WRITE_ADDR"] == 0x04
    assert namespace["DMA_CH_TRANS_COUNT"] == 0x08
    assert dma_reg(0, namespace["DMA_CH_WRITE_ADDR"]) == 0x50000004
    assert dma_reg(0, namespace["DMA_CH_TRANS_COUNT"]) == 0x50000008
    assert dma_reg(3, namespace["DMA_CH_WRITE_ADDR"]) == 0x500000C4
    # Never the trigger aliases: CTRL_TRIG (+0x0c) or AL1_TRANS_COUNT_TRIG
    # (+0x1c) would start the channel instead of arming it.
    assert namespace["DMA_CH_WRITE_ADDR"] not in (0x0C, 0x1C)
    assert namespace["DMA_CH_TRANS_COUNT"] not in (0x0C, 0x1C)


# -- (d) input pin initialisation -----------------------------------------


class _RecordingPin:
    IN = "IN"
    OUT = "OUT"
    calls: list = []

    def __init__(self, gpio, mode=None, pull=None):
        _RecordingPin.calls.append((gpio, mode, pull))


@pytest.mark.parametrize("profile", [RP2040_TT06, RP2350_DBV3], ids=lambda p: p.name)
def test_init_input_pins_brings_up_every_sampled_pad(source, profile):
    namespace = _exec_functions(source, ["init_input_pins"])
    _RecordingPin.calls = []

    namespace["init_input_pins"](_RecordingPin, profile.in_base, profile.in_count)

    gpios = [gpio for gpio, _, _ in _RecordingPin.calls]
    assert gpios == list(range(profile.in_base, profile.in_base + profile.in_count))
    # Every pad is configured as an input, and no pulls are enabled: the
    # project drives these lines.
    assert all(mode == _RecordingPin.IN for _, mode, _ in _RecordingPin.calls)
    assert all(pull is None for _, _, pull in _RecordingPin.calls)


@pytest.mark.parametrize("profile", [RP2040_TT06, RP2350_DBV3], ids=lambda p: p.name)
def test_init_input_pins_never_touches_the_project_clock_pad(source, profile):
    # Regression: `machine.Pin(clk, Pin.IN)` moves the pad's FUNCSEL from
    # PWM to SIO, which stops the clock `tt.clock_project_PWM()` is
    # generating -- on fpga-1 that left the state machine waiting forever
    # and no chunk was ever emitted. The PIO reads the pad's input
    # synchroniser whatever its FUNCSEL is, so the pad needs no setup.
    namespace = _exec_functions(source, ["init_input_pins"])
    _RecordingPin.calls = []

    namespace["init_input_pins"](_RecordingPin, profile.in_base, profile.in_count)

    assert profile.clk_gpio not in [gpio for gpio, _, _ in _RecordingPin.calls]
    assert "init_input_pins(machine.Pin, IN_BASE, IN_COUNT)" in source


def test_main_initialises_pins_before_creating_the_state_machine(source):
    assert source.index("init_input_pins(machine.Pin") < source.index("rp2.StateMachine(")


# -- (e) shutdown structure -----------------------------------------------


def _main_node(source: str) -> ast.FunctionDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("script has no top-level main()")


def test_main_cleans_up_in_a_finally_block(source):
    main = _main_node(source)
    tries = [n for n in main.body if isinstance(n, ast.Try) and n.finalbody]
    assert len(tries) == 1, "main() must wrap the DMA lifetime in one try/finally"

    finally_src = ast.unparse(ast.Module(body=tries[0].finalbody, type_ignores=[]))
    assert "sm.active(0)" in finally_src
    assert "dma.close()" in finally_src
    assert "write_time_chunk" in finally_src, "the closing TIME chunk is emitted from finally"

    # The channels are allocated inside the try, so a failure part-way
    # through setup still closes whichever one was claimed.
    try_src = ast.unparse(ast.Module(body=tries[0].body, type_ignores=[]))
    assert "rp2.DMA()" in try_src
    assert "rp2.DMA()" not in ast.unparse(
        ast.Module(body=[n for n in main.body if not isinstance(n, ast.Try)], type_ignores=[])
    )


def test_main_catches_keyboardinterrupt(source):
    main = _main_node(source)
    handlers = [h for t in main.body if isinstance(t, ast.Try) for h in t.handlers]
    assert any(
        isinstance(h.type, ast.Name) and h.type.id == "KeyboardInterrupt" for h in handlers
    )


def _function_node(source: str, name: str) -> ast.FunctionDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"script has no top-level {name}()")


def test_irq_handlers_rearm_before_flagging(source):
    # The re-arm must happen before the flag: the partner's chain can
    # re-trigger this channel as soon as it completes.
    handler = ast.unparse(_function_node(source, "on_a"))
    assert handler.index("MEM[WR_A] = ADDR_A") < handler.index("FULL[0] = True")
    assert handler.index("MEM[TC_A] = BUF_WORDS") < handler.index("FULL[0] = True")
    assert "OVERRUNS[0] += 1" in handler


@pytest.mark.parametrize("name", ["on_a", "on_b"])
def test_irq_handlers_are_module_level_not_closures(source, name):
    # A hard IRQ runs with the heap locked. Measured on fpga-1 under
    # micropython.heap_lock(): this exact body as a closure over main()'s
    # locals raises MemoryError at its first statement, and as a
    # module-level function reading module globals it runs clean.
    _function_node(source, name)  # raises unless it is top level

    nested = [
        node.name
        for node in ast.walk(_main_node(source))
        if isinstance(node, ast.FunctionDef)
    ]
    assert name not in nested


@pytest.mark.parametrize("name", ["on_a", "on_b"])
def test_irq_handlers_allocate_nothing(source, name):
    # Arithmetic on a register or buffer address allocates: they are big
    # ints on this 31-bit-small-int build. A precomputed one used as a
    # `mem32` index does not. So the handler body may only store and update
    # small ints -- no BinOp, no call, no literal above the small-int range.
    node = _function_node(source, name)

    assert not [n for n in ast.walk(node) if isinstance(n, ast.BinOp)]
    assert not [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    constants = [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, int)
    ]
    assert all(abs(value) < 2**30 for value in constants)


def test_main_hands_the_module_level_handlers_to_the_dma(source):
    main_src = ast.unparse(_main_node(source))

    assert "dma_a.irq(on_a, hard=True)" in main_src
    assert "dma_b.irq(on_b, hard=True)" in main_src
    # The addresses the handlers read are all computed here, once.
    for name in ("WR_A", "WR_B", "TC_A", "TC_B", "ADDR_A", "ADDR_B"):
        assert "%s = " % name in main_src
    assert "global WR_A" in main_src


def test_dma_is_armed_before_the_state_machine(source):
    assert source.index("dma_a.active(1)") < source.index("sm.active(1)")


# -- (f) chunk framing ----------------------------------------------------


def test_write_raw_chunk_frames_a_buffer_vgacap_can_parse(source):
    buf_words = 8
    samples_per_word = RP2040_TT06.samples_per_word
    helpers = _exec_functions(source, ["_le32", "_write_all", "write_raw_chunk"])

    out = io.BytesIO()
    buf = bytearray(range(4 * buf_words))
    written = helpers["write_raw_chunk"](out, buf, buf_words * samples_per_word)

    assert written == len(out.getvalue()) == 8 + 4 + 4 * buf_words
    chunks = list(read_chunks(out.getvalue()))
    assert len(chunks) == 1
    tag, payload = chunks[0]
    assert tag == "RAW "
    assert len(payload) == 4 + 4 * buf_words
    assert struct.unpack_from("<I", payload, 0)[0] == buf_words * samples_per_word
    assert payload[4:] == bytes(buf)


def test_write_time_chunk_frames_an_overrun_report(source):
    helpers = _exec_functions(source, ["_le32", "_write_all", "write_time_chunk"])

    out = io.BytesIO()
    written = helpers["write_time_chunk"](out, 4096, b"overrun")

    assert written == len(out.getvalue())
    (tag, payload), = read_chunks(out.getvalue())
    assert tag == "TIME"
    host_time_ns, clock_hz, dropped, msg_len = struct.unpack_from("<QIIH", payload, 0)
    assert (host_time_ns, clock_hz, dropped, msg_len) == (0, 0, 4096, 7)
    assert payload[18 : 18 + msg_len] == b"overrun"
    assert len(payload) == 18 + msg_len


def test_raw_chunk_payload_matches_the_profile_sample_count(source):
    helpers = _exec_functions(source, ["_le32", "_write_all", "write_raw_chunk"])
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
        # Not PIO0: the stock firmware keeps its own program there, which
        # on RP2350 also makes that block's pin window immovable.
        "pio": 1,
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


def test_capture_cfg_takes_pio_and_sm():
    cfg = capture_cfg(RP2040_TT06, pio=1, sm=3)
    assert (cfg["pio"], cfg["sm"]) == (1, 3)


@pytest.mark.parametrize("kwargs", [{"edge": "both"}, {"buf_words": 0}, {"max_bytes": -1}, {"pio": -1}, {"sm": 4}])
def test_capture_cfg_rejects_bad_arguments(kwargs):
    with pytest.raises(ValueError):
        capture_cfg(RP2040_TT06, **kwargs)
