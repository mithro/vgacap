# SPDX-License-Identifier: Apache-2.0
"""Board-side body: load an FPGA bitstream with the clock held low.

Run by `ttcap.capture.load_held()` after the prelude, with reset already
held (`hold_reset`) and the bitstream already at `CFG["path"]`
(`RawRepl.upload()`). It does what the stock firmware's
`FPGAMux.enable` does (tt-micropython-firmware v3.1.1,
`fpga/fpga_mux.py:144-160`) but for its last step: `safe_bidir()` and
`tt.shuttle.reset()`, which sets `tt.shuttle.enabled` to None as its
`reset_and_clock_mux()` does, then `spi_transferPIO(path)` unchanged. It
sets no new `enabled` and runs no callback, which would restart the PWM
clock. Before the transfer the clock is stopped and its pad driven low by
the RP2, so no edge reaches the design before clock 0.

CFG keys, on top of the prelude's:

    path    the bitstream on the board's filesystem, "/_vgacap/held.bin"

Output, one line each after the loader's own (`fabricfoxv2.py`):

    ttcap rst_n=N    tt.rst_n() after the load; 0 while reset is held
    ttcap clk=N      the clock pad after the load; 0 while held low

The loader prints "Transmission complete, total bytes: N" or, catching
the `OSError` itself, "Error accessing file: ..." (`fabricfoxv2.py:
167-170`). Both are read by the host, which alone knows the length.
"""

from machine import Pin
from ttboard.fpga import fabricfoxv2

HELD = CFG["path"]  # noqa: F821 - CFG is prepended by the host

tt.pins.safe_bidir()  # noqa: F821 - tt is the SDK's DemoBoard, in the REPL
tt.shuttle.reset()  # noqa: F821
tt.clock_project_stop()  # noqa: F821
Pin(CLK_GPIO, Pin.OUT, value=0)  # noqa: F821 - CLK_GPIO is the prelude's
fabricfoxv2.spi_transferPIO(HELD)
print("ttcap rst_n=%d" % tt.rst_n())  # noqa: F821
print("ttcap clk=%d" % Pin(CLK_GPIO).value())  # noqa: F821
