# SPDX-License-Identifier: Apache-2.0
"""A fake stock-firmware `tt` that behaves like tt-micropython-firmware v3.1.1.

`FakeBinaryBoard` runs the host's real commands against it, so a test sees
what the board would do with them, not what the host hoped it would do.
Each behaviour below cites the SDK source it copies (v3.1.1, d485c7a):

- Every `tt.mode` write rebuilds every pin (`demoboard.py:226-233` ->
  `pins.py:166-181` -> `begin_inputs_all`, `pins.py:200-211`). `rst_n`
  (GPIO14) comes back as a new input with a pull-down
  (`gpio_map_dbv3.py:188-193`). The write also stops an auto clock when the
  mode changes (`demoboard.py:226-231`).
- An input `rst_n` reads the board's external pull-up, which beats the
  internal pull-down (`demoboard.py:284`; fpga-4, 2026-11-27). Set
  `pullup=False` for a board where it does not.
- `reset_project(True)` makes `rst_n` an output and drives 0;
  `reset_project(False)` writes 1 to it only while it is an output, then
  makes it an input (`demoboard.py:265-291`).
- `ASIC_RP_CONTROL` drives `ui_in`, except a pin that reads high when the
  pins are rebuilt, which it leaves an input (`pins.py:236-250`). Other
  modes do not drive it at all (`pins.py:182-186`). `tt.ui_in.value` reads
  the pads (`rp2350.py:77-78`).
- In `ASIC_RP_CONTROL` the manual-clock monitor is on (`demoboard.py:234-238`).
  Its 3 Hz timer calls `clock_project_once()` after a rising edge on the
  manual-clock pin, which stops the auto clock (`manual_clock.py:76-87`,
  `demoboard.py:317-330`). `tick()` is one firing of that timer.
- The SDK logs at INFO on stdout (`log.py:44-54`), with the messages above,
  each in its level's ANSI colour (`log.py:23-28,54`, `colors.py:6,23-27,
  33-45`: `Enable = True`, and nothing in the SDK turns it off). A `ui_in`
  pin that reads high when `ASIC_RP_CONTROL` rebuilds the pins gets a WARN
  line of its own before the ERROR line (`pins.py:242-250`).
- `tt.pins.safe_bidir()` makes every `uio` pin an input, its pull kept
  (`pins.py:215-223`, `standard.py:60-64`); it logs at DEBUG only.
- `clock_project_stop()` makes the clock pin an input with its pull-down
  whether or not a clock was running (`demoboard.py:394-401`,
  `pins.py:279-285`); it logs at DEBUG only.

**On the emulator** (`board=` a `ttcap.emu.Board`), the pins are the
board's own `machine.Pin`s, GPIO numbers as on the RP2350 DB v3
(`gpio_map_dbv3.py`): `rst_n` 14, the clock 16, `uio` 25-32. A mode write
rebuilds them there; `rst_n` reads the board's pad, so its release is the
board's external pull (`Board(pulls={14: 1})`) against the pull-down, with
no `release_ms`; and while the manual-clock monitor is on, its 333 ms timer
(`manual_clock.py:47,63-65`) runs on the board's clock and its
`clock_project_once()` drives the clock pad (`demoboard.py:317-330`).
Construction stands for a boot that is already over: it sets `rst_n` and
`uio` as `begin_inputs_all` left them and leaves the clock pad to the
board's waveform, the PWM that started after boot. `clock_project_PWM` is
not emulated there (the waveform is the PWM).

The design behind the pins is modelled only as far as reset goes: `design`
counts the clock edges it saw while `rst_n` was low (a synchronous reset
needs some) and records what `ui_in` was when reset ended.

Time is the fake's own millisecond clock, `now_ms`, which only the
board-side `time.sleep_ms` moves (`mpy_time`). A raw-REPL round trip takes
no time here, so the edges a design gets in reset are only those the host's
commands wait for on the board: a host cannot count on the link's latency.
"""

from __future__ import annotations

import types

SAFE, ASIC_RP_CONTROL, ASIC_MANUAL_INPUTS = 0, 1, 2
MODE_NAMES = {SAFE: "SAFE", ASIC_RP_CONTROL: "ASIC_RP_CONTROL",
              ASIC_MANUAL_INPUTS: "ASIC_MANUAL_INPUTS"}
IN, OUT = 0, 1
PULL_DOWN = 2
DEBUG, INFO, WARN, ERROR = 10, 20, 30, 40
#: RP2350 DB v3 GPIOs (gpio_map_dbv3.py): rst_n, the project clock, uio.
RST_GPIO, CLK_GPIO = 14, 16
UIO_GPIOS = tuple(range(25, 33))
#: log.py:23-28 `colorMap`, as colors.py:37 indexes COLORS (colors.py:7).
_COLOUR = {DEBUG: 3, INFO: 2, WARN: 3, ERROR: 1}


def log(name: str, text: str, level: int = INFO) -> None:
    """One SDK log line, as `log.py:44-54` prints it once the boot prefix is
    gone (`demoboard.py:182`): `name: ` and the text in its level's colour,
    bright (`colors.py:23-27,33-45`). DEBUG is below the default level 20
    (`log.py:12,46`) and prints nothing."""
    if level >= INFO:
        print(f"{name}: \033[3{_COLOUR[level]};1m{text}\033[0m")


class Design:
    """What the design behind the pins saw of its reset."""

    def __init__(self) -> None:
        #: The clock ran while rst_n was low.
        self.clocked_in_reset = False
        #: Sum of clock_hz x milliseconds while rst_n was low and clocked.
        self._reset_hz_ms = 0
        #: The level of ui_in each time rst_n went from 0 to 1.
        self.ui_in_at_release: list[int] = []
        #: rst_n levels in the order they changed.
        self.rst_n_levels: list[int] = []

    @property
    def edges_in_reset(self) -> int:
        """Rising clock edges while rst_n was low."""
        return self._reset_hz_ms // 1000


class RstPin:
    """The SDK's `StandardPin` for GPIO14, `nprojectrst`."""

    IN, OUT = IN, OUT

    def __init__(self, tt: "FakeTT") -> None:
        self._tt = tt
        self._mode = IN
        self.pull = PULL_DOWN
        self._out = 0
        #: On the emulator, the board's own Pin (StandardPin.raw_pin).
        self.raw_pin = None
        if tt.board is not None:
            pin_cls = tt.machine.Pin
            self.raw_pin = pin_cls(RST_GPIO, pin_cls.IN, pull=pin_cls.PULL_DOWN)

    @property
    def mode(self) -> int:
        return self._mode

    @mode.setter
    def mode(self, set_to: int) -> None:
        # standard.py:60-64: raw_pin.init(mode, pull=self._pull).
        self._mode = set_to
        if self.raw_pin is not None:
            self.raw_pin.init(set_to, pull=self.pull)

    def __call__(self, value: int | None = None):
        if value is not None:
            self._out = int(bool(value))
            if self.raw_pin is not None:
                self.raw_pin.value(self._out)
            self._tt.changed()
            return None
        return self._tt.rst_n_level(self)


class ManualClock:
    """`ManualClockPin` (`manual_clock.py`)."""

    def __init__(self, tt: "FakeTT") -> None:
        self._tt = tt
        self._monitoring = False
        self._timer = None
        #: The IRQ flag a rising edge on GPIO15 sets.
        self.pin_activated = False

    @property
    def monitoring(self) -> bool:
        return self._monitoring

    @monitoring.setter
    def monitoring(self, set_to: bool) -> None:
        board = self._tt.board
        if set_to:
            if self._monitoring:
                return
            log("ttboard.pins.manual_clock", "Enabling manual clock pin monitoring")
            self._monitoring = True
            if board is not None:
                # manual_clock.py:47,63-65: a 333 ms periodic Timer.
                self._timer = board.add_timer(board.sysclk_hz * 333 // 1000, self._tt.tick)
        else:
            if not self._monitoring:
                return
            log("ttboard.pins.manual_clock", "Disabling manual clock pin monitoring")
            self._monitoring = False
            if board is not None:
                board.remove_timer(self._timer)
                self._timer = None


class UiIn:
    """`tt.ui_in`: writes drive the pins the mode lets it, reads the pads."""

    def __init__(self, tt: "FakeTT") -> None:
        self._tt = tt

    @property
    def value(self) -> int:
        return self._tt.ui_in_level()

    @value.setter
    def value(self, v: int) -> None:
        self._tt.ui_in_written = int(v) & 0xFF
        self._tt.changed()


class Pins:
    """`tt.pins` (`pins.py`), for the calls ttcap makes."""

    def __init__(self, tt: "FakeTT") -> None:
        self._tt = tt

    def safe_bidir(self) -> None:
        """pins.py:215-223: every `uio` pin an input, its pull kept."""
        log("ttboard.pins.pins", "Setting bidirs to safe mode (inputs)", DEBUG)
        tt = self._tt
        if tt.board is not None:
            pin_cls = tt.machine.Pin
            for gpio in UIO_GPIOS:
                pin_cls(gpio, pin_cls.IN, pull=pin_cls.PULL_DOWN)
        tt.uio_safe = True

    def project_clk_driven_by_RP2(self, rp_controlled: bool) -> None:
        """pins.py:279-285: the clock pin an output, or an input with its
        pull-down (`gpio_map_dbv3.py:189-193`)."""
        tt = self._tt
        if tt.board is not None:
            pin_cls = tt.machine.Pin
            pin_cls(CLK_GPIO, pin_cls.OUT if rp_controlled else pin_cls.IN,
                    pull=pin_cls.PULL_DOWN)


class FakeTT:
    """The `tt` DemoBoard, for the calls ttcap makes.

    `mode` is the RPMode it boots in (fpgas.online boards: 2).
    `switches` is the byte external switches hold on `ui_in`.
    `manual_clock` False is a board without the monitored pin (DB < v3.3,
    and fpga-4, where `tt.manual_project_clock` is None).
    `release_ms` is how long an input `rst_n` takes to read 1.
    `board` is a `ttcap.emu.Board` whose pins these are (module docstring).
    """

    def __init__(self, mode: int = ASIC_MANUAL_INPUTS, *, pullup: bool = True,
                 switches: int = 0, manual_clock: bool = True, release_ms: int = 0,
                 board=None) -> None:
        self.board = board
        self.machine = board.modules()["machine"] if board is not None else None
        self.pullup = pullup
        self.switches = switches & 0xFF
        self.release_ms = release_ms
        self.now_ms = 0
        self._input_since_ms = 0
        self.design = Design()
        self.ui_in = UiIn(self)
        self.ui_in_written = 0
        self._ui_in_driven = 0
        self.clock_hz = 0
        #: Set by pins.safe_bidir(), cleared by a pin rebuild.
        self.uio_safe = False
        self.pins = Pins(self)
        self.manual_project_clock = ManualClock(self) if manual_clock else None
        self._mode = mode
        self.rst_n = RstPin(self)
        self.pin_rebuilds = 0
        self._rebuild(mode, boot=True)
        if self.manual_project_clock is not None and mode == ASIC_RP_CONTROL:
            self.manual_project_clock.monitoring = True
        self._last_rst = self.rst_n_level(self.rst_n)

    # -- what the pads read ------------------------------------------------
    def rst_n_level(self, pin: RstPin) -> int:
        if self.board is not None:
            board = self.board
            return board.pad_level(RST_GPIO, board.now)
        if pin is not self.rst_n:
            # A pin object the rebuild replaced no longer owns the pad.
            pin = self.rst_n
        if pin.mode == OUT:
            return pin._out
        if not self.pullup:
            return 0
        return int(self.now_ms - self._input_since_ms >= self.release_ms)

    def ui_in_level(self) -> int:
        return (self.ui_in_written & self._ui_in_driven) | (self.switches & ~self._ui_in_driven & 0xFF)

    def changed(self) -> None:
        """Note the design's view after any pin or clock change."""
        level = self.rst_n_level(self.rst_n)
        if level != self._last_rst:
            self.design.rst_n_levels.append(level)
            if level == 1:
                self.design.ui_in_at_release.append(self.ui_in_level())
            self._last_rst = level
        if level == 0 and self.clock_hz:
            self.design.clocked_in_reset = True

    def advance(self, ms: int) -> None:
        """Let `ms` milliseconds pass on the board, clocking the design."""
        for _ in range(int(ms)):
            if self.rst_n_level(self.rst_n) == 0 and self.clock_hz:
                self.design._reset_hz_ms += self.clock_hz
            self.now_ms += 1
            self.changed()

    # -- the SDK's calls -----------------------------------------------------
    @property
    def mode(self) -> int:
        return self._mode

    @mode.setter
    def mode(self, set_to: int) -> None:
        # demoboard.py:226-238.
        if self._mode != set_to and self.clock_hz:
            freq = self.clock_hz
            self.clock_project_stop()
            log("ttboard.demoboard", f"Was auto-clocking @ {freq} but stopping for mode change",
                WARN)
        self._rebuild(set_to)
        if self.manual_project_clock is not None:
            self.manual_project_clock.monitoring = set_to == ASIC_RP_CONTROL
        self.changed()

    @property
    def mode_str(self) -> str:
        return MODE_NAMES[self._mode]

    def _rebuild(self, set_to: int, boot: bool = False) -> None:
        """pins.py:166-186: log, then begin_inputs_all and the mode's own
        pins. At `boot` on the emulator the clock pad is left alone."""
        self._mode = set_to
        self.pin_rebuilds += 1
        log("ttboard.pins.pins", f"Setting mode to {MODE_NAMES[set_to]}")
        # begin_inputs_all (pins.py:200-211): every pin a new input with its
        # pull-down -- rst_n, uio and the clock among them.
        self.rst_n = RstPin(self)
        self.uio_safe = False
        if self.board is not None:
            pin_cls = self.machine.Pin
            for gpio in UIO_GPIOS:
                pin_cls(gpio, pin_cls.IN, pull=pin_cls.PULL_DOWN)
            if not boot:
                self.pins.project_clk_driven_by_RP2(False)
        self._input_since_ms = self.now_ms
        self._ui_in_driven = 0
        if set_to == ASIC_RP_CONTROL:
            high = self.switches
            for bit in range(8):
                if not high & (1 << bit):
                    self._ui_in_driven |= 1 << bit
                else:
                    log("ttboard.pins.pins",
                        f"Trying to control ui_in{bit} but possible contention (it is reading HIGH)",
                        WARN)
            if high:
                names = ",".join("ui_in%d" % b for b in range(8) if high & (1 << b))
                log("ttboard.pins.pins",
                    f"Following pins have not be set as outputs owing to contention: {names}",
                    ERROR)
            if self.board is not None and not boot:
                self.pins.project_clk_driven_by_RP2(True)  # pins.py:251

    def reset_project(self, put_in_reset: bool) -> None:
        # demoboard.py:267-291.
        pin = self.rst_n
        if put_in_reset:
            if pin.mode != OUT:
                log("ttboard.demoboard", "Changing reset to output mode")
                pin.mode = OUT
            pin(0)
        else:
            if pin.mode == OUT:
                log("ttboard.demoboard", "Taking out of reset", DEBUG)
                pin(1)
            pin.mode = IN
            self._input_since_ms = self.now_ms
        self.changed()

    def clock_project_PWM(self, freq_hz: int, *args, **kwargs):
        if self.board is not None:
            raise NotImplementedError(
                "clock_project_PWM on the emulator: the board's Waveform is the PWM clock")
        self.clock_hz = int(freq_hz)
        log("ttboard.demoboard", f"Clocking at {freq_hz}Hz")
        self.changed()

    def clock_project_stop(self) -> None:
        # demoboard.py:394-401: whether or not a clock ran, the clock pin
        # ends an input.
        if self.clock_hz:
            log("ttboard.demoboard", "PWM auto-clock stop", DEBUG)
        self.clock_hz = 0
        self.pins.project_clk_driven_by_RP2(False)
        self.changed()

    def clock_project_once(self) -> None:
        # demoboard.py:317-330: the clock pin an output, toggled twice.
        log("ttboard.demoboard", "clock project once", DEBUG)
        if self.clock_hz:
            self.clock_project_stop()
        if self.board is not None:
            self.pins.project_clk_driven_by_RP2(True)
            clk = self.machine.Pin(CLK_GPIO)
            clk.toggle()
            clk.toggle()
        self.changed()

    def tick(self) -> None:
        """One firing of the manual-clock monitor's 333 ms timer
        (manual_clock.py:76-80)."""
        mc = self.manual_project_clock
        if mc is not None and mc.monitoring and mc.pin_activated:
            mc.pin_activated = False
            self.clock_project_once()


def mpy_time(tt: FakeTT) -> types.ModuleType:
    """MicroPython's `time`, on the fake board's own millisecond clock."""
    module = types.ModuleType("time")
    module.ticks_ms = lambda: tt.now_ms
    module.ticks_diff = lambda a, b: a - b

    def sleep_ms(ms: int) -> None:
        tt.advance(ms)

    module.sleep_ms = sleep_ms
    return module


def sdk_modules(tt: FakeTT) -> dict:
    """The modules the host's commands import, for `FakeBinaryBoard(modules=...)`."""
    mode = types.ModuleType("ttboard.mode")
    mode.RPMode = types.SimpleNamespace(SAFE=SAFE, ASIC_RP_CONTROL=ASIC_RP_CONTROL,
                                        ASIC_MANUAL_INPUTS=ASIC_MANUAL_INPUTS)
    return {"ttboard.mode": mode, "time": mpy_time(tt)}


# -- the FPGA loader -----------------------------------------------------------


class FakeLoader:
    """`ttboard.fpga.fabricfoxv2` at v3.1.1, as far as `spi_transferPIO` goes.

    A fake, not vendored code: the firmware is LGPL-2.1 and vgacap is
    Apache-2.0. It reads the file from `files` (path -> bytes, the board's
    filesystem), records what it fed the FPGA in `loads`, and prints the
    SDK's own lines (`fabricfoxv2.py:97,106,133,164,167,170,181`). An
    `OSError` -- a missing file, or `fail`, raised once the first
    `fail_after` bytes have gone -- prints "Error accessing file: ..." and
    is swallowed, as the real one swallows it (`fabricfoxv2.py:169-170`).

    `drain_lines` is how many "Waiting for FIFO to drain, free slots: 1"
    lines it prints before "Transmission complete" (`fabricfoxv2.py:163-
    165`; `tx_fifo()` is the FIFO's level, `rp2_pio.c:891-893` in
    MicroPython v1.24.0). On hardware that is 0 or more, as the last word
    is still shifting out; the default is 0.

    On an emulator board it drives the loader's SIO pins as the real one
    does around the transfer (`fabricfoxv2.py:19-40,67-73,92,112-130,174`;
    `gpio_map_dbv3.py`: sck GPIO 6, mosi 3, ss 5, the FPGA's reset 1) and
    takes the time a transfer at `freq` takes, then calls the design's
    `configure()`, if it has one. Not modelled: the real loader's PIO0
    state machine 0, which takes GPIO 6 and 3 for PIO0 and leaves its
    program loaded.
    """

    SCK, MOSI, SS, RESET = 6, 3, 5, 1

    def __init__(self, board=None, files: dict[str, bytes] | None = None, *,
                 fail: OSError | None = None, fail_after: int = 0,
                 drain_lines: int = 0) -> None:
        self.board = board
        self.drain_lines = drain_lines
        self.files = dict(files or {})
        self.fail = fail
        self.fail_after = fail_after
        #: The bytes each call fed the FPGA, a failed one included.
        self.loads: list[bytes] = []

    def _sleep_us(self, us: int) -> None:
        if self.board is not None:
            self.board.advance(self.board.sysclk_hz * us // 1_000_000)

    def spi_transferPIO(self, filepath: str, freq: int = 1_000_000) -> None:  # noqa: N802
        board = self.board
        pins = {}
        if board is not None:
            pin_cls = board.modules()["machine"].Pin
            for name in ("SCK", "MOSI", "SS", "RESET"):
                pins[name] = pin_cls(getattr(self, name), pin_cls.OUT)
            pins["RESET"].high()
        print(f"Configuring PIO with frequency: {freq * 2 * 8} Hz")
        print("State machine activated")
        fed = bytearray()
        try:
            if filepath not in self.files:
                raise OSError(2, "ENOENT")
            data = self.files[filepath]
            if pins:  # fpga_reset, then the dummy byte with SS high
                pins["RESET"].low()
                pins["SS"].low()
                self._sleep_us(15000)
                pins["RESET"].high()
                self._sleep_us(15000)
                pins["SS"].high()
                self._sleep_us(2000 + 20)
                pins["SS"].low()
                self._sleep_us(2000)
            print("SS low, starting transmission")
            if self.fail is not None:
                fed += data[: self.fail_after]
                raise self.fail
            fed += data
            if board is not None:
                # 8 bits a byte, the file and the 6 termination bytes.
                board.advance(board.sysclk_hz * 8 * (len(fed) + 6) // freq)
            for _ in range(self.drain_lines):
                print("Waiting for FIFO to drain, free slots: 1")
            print(f"Transmission complete, total bytes: {len(fed)}")
            if board is not None and hasattr(board.wave, "configure"):
                board.wave.configure()
        except OSError as e:
            print(f"Error accessing file: {e}")
        finally:
            self.loads.append(bytes(fed))
            if pins:
                pins["SS"].high()
            print("State machine deactivated, SS high")


def board_sdk(board, *, loader: FakeLoader | None = None, **kwargs) -> FakeTT:
    """The fake SDK on an emulator board: a `FakeTT(board=board, **kwargs)`
    to pass as `run_script(globals={"tt": ...})`, with `ttboard.mode` and,
    given `loader`, `ttboard.fpga.fabricfoxv2` importable there."""
    tt = FakeTT(board=board, **kwargs)
    board.extra_modules.update(sdk_modules(tt))
    board.extra_modules.pop("time")  # the board's own, on its clock
    if loader is not None:
        module = types.ModuleType("ttboard.fpga.fabricfoxv2")
        module.spi_transferPIO = loader.spi_transferPIO
        board.extra_modules["ttboard.fpga.fabricfoxv2"] = module
    return tt
