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
- The SDK logs at INFO on stdout (`log.py:44-54`), with the messages above.

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


def log(name: str, text: str) -> None:
    """One SDK log line, as `log.py` prints it at INFO."""
    print(f"{name}: {text}")


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
        self.mode = IN
        self.pull = PULL_DOWN
        self._out = 0

    def __call__(self, value: int | None = None):
        if value is not None:
            self._out = int(bool(value))
            self._tt.changed()
            return None
        return self._tt.rst_n_level(self)


class ManualClock:
    """`ManualClockPin` (`manual_clock.py`)."""

    def __init__(self, tt: "FakeTT") -> None:
        self._tt = tt
        self._monitoring = False
        #: The IRQ flag a rising edge on GPIO15 sets.
        self.pin_activated = False

    @property
    def monitoring(self) -> bool:
        return self._monitoring

    @monitoring.setter
    def monitoring(self, set_to: bool) -> None:
        if set_to:
            if self._monitoring:
                return
            log("ttboard.pins.manual_clock", "Enabling manual clock pin monitoring")
            self._monitoring = True
        else:
            if not self._monitoring:
                return
            log("ttboard.pins.manual_clock", "Disabling manual clock pin monitoring")
            self._monitoring = False


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


class FakeTT:
    """The `tt` DemoBoard, for the calls ttcap makes.

    `mode` is the RPMode it boots in (fpgas.online boards: 2).
    `switches` is the byte external switches hold on `ui_in`.
    `manual_clock` False is a board without the monitored pin (DB < v3.3,
    and fpga-4, where `tt.manual_project_clock` is None).
    `release_ms` is how long an input `rst_n` takes to read 1.
    """

    def __init__(self, mode: int = ASIC_MANUAL_INPUTS, *, pullup: bool = True,
                 switches: int = 0, manual_clock: bool = True, release_ms: int = 0) -> None:
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
        self.manual_project_clock = ManualClock(self) if manual_clock else None
        self._mode = mode
        self.rst_n = RstPin(self)
        self.pin_rebuilds = 0
        self._rebuild(mode)
        if self.manual_project_clock is not None and mode == ASIC_RP_CONTROL:
            self.manual_project_clock.monitoring = True
        self._last_rst = self.rst_n_level(self.rst_n)

    # -- what the pads read ------------------------------------------------
    def rst_n_level(self, pin: RstPin) -> int:
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
        if self._mode != set_to and self.clock_hz:
            freq = self.clock_hz
            self.clock_project_stop()
            log("ttboard.demoboard", f"Was auto-clocking @ {freq} but stopping for mode change")
        self._rebuild(set_to)
        if self.manual_project_clock is not None:
            self.manual_project_clock.monitoring = set_to == ASIC_RP_CONTROL
        self.changed()

    @property
    def mode_str(self) -> str:
        return MODE_NAMES[self._mode]

    def _rebuild(self, set_to: int) -> None:
        self._mode = set_to
        self.pin_rebuilds += 1
        log("ttboard.pins.pins", f"Setting mode to {MODE_NAMES[set_to]}")
        # begin_inputs_all: a new rst_n, an input with its pull-down.
        self.rst_n = RstPin(self)
        self._input_since_ms = self.now_ms
        self._ui_in_driven = 0
        if set_to == ASIC_RP_CONTROL:
            high = self.switches
            for bit in range(8):
                if not high & (1 << bit):
                    self._ui_in_driven |= 1 << bit
            if high:
                names = ",".join("ui_in%d" % b for b in range(8) if high & (1 << b))
                log("ttboard.pins.pins",
                    f"Following pins have not be set as outputs owing to contention: {names}")

    def reset_project(self, put_in_reset: bool) -> None:
        pin = self.rst_n
        if put_in_reset:
            if pin.mode != OUT:
                log("ttboard.demoboard", "Changing reset to output mode")
                pin.mode = OUT
            pin(0)
        else:
            if pin.mode == OUT:
                pin(1)
            pin.mode = IN
            self._input_since_ms = self.now_ms
        self.changed()

    def clock_project_PWM(self, freq_hz: int, *args, **kwargs):
        self.clock_hz = int(freq_hz)
        log("ttboard.demoboard", f"Clocking at {freq_hz}Hz")
        self.changed()

    def clock_project_stop(self) -> None:
        self.clock_hz = 0
        self.changed()

    def clock_project_once(self) -> None:
        if self.clock_hz:
            self.clock_project_stop()
        self.changed()

    def tick(self) -> None:
        """One firing of the manual-clock monitor's 3 Hz timer."""
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
