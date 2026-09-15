# SPDX-License-Identifier: Apache-2.0
"""Board profiles for the Tiny Tapeout demo board's PIO sampler.

A `BoardProfile` describes the GPIO layout and PIO sampler configuration for
one RP2040/RP2350 demo-board firmware family. `profile_from_gpio_map` picks
the right profile from the dict returned by the board's `GPIOMap.all()`, and
`WELLAND` maps chip-lab slugs (as used on the fpgas.online "Welland" bench)
to their fixed power-switch pin and expected profile.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoardProfile:
    name: str  # "rp2040-tt06map" | "rp2350-dbv3"
    clk_gpio: int
    uo_gpios: tuple[int, ...]  # 8 entries, uo_out[0..7]
    pio_gpio_base: int  # 0 or 16
    in_base: int  # PIO in_base GPIO for the sampler
    in_count: int  # bits per `in pins`
    sample_bits: int
    samples_per_word: int
    flags: int
    signal_map: tuple[int, ...]


RP2040_TT06 = BoardProfile(
    "rp2040-tt06map", 0, (5, 6, 7, 8, 13, 14, 15, 16), 0, 5, 12, 12, 2, 0, (11, 3, 0, 8, 1, 9, 2, 10)
)
RP2350_DBV3 = BoardProfile(
    "rp2350-dbv3", 16, tuple(range(33, 41)), 16, 33, 8, 8, 4, 0, (7, 3, 0, 4, 1, 5, 2, 6)
)


def profile_from_gpio_map(m: dict[str, int]) -> BoardProfile:
    """Pick a BoardProfile from a board's `GPIOMap.all()` dict.

    Keys on `rp_projclk` and `uo_out0..7`, since those are the pins that
    differ between the RP2040 (tt06+) and RP2350 (dbv3) demo board GPIO maps.
    """
    try:
        clk_gpio = m["rp_projclk"]
        uo_gpios = tuple(m[f"uo_out{i}"] for i in range(8))
    except KeyError as exc:
        raise ValueError(f"gpio map is missing key {exc}: {m!r}") from exc

    for profile in (RP2040_TT06, RP2350_DBV3):
        if (clk_gpio, uo_gpios) == (profile.clk_gpio, profile.uo_gpios):
            return profile

    raise ValueError(
        f"unrecognized board gpio layout: rp_projclk={clk_gpio}, uo_out0..7={uo_gpios}"
    )


# slug -> (fpgas.online power-switch pin name, expected BoardProfile)
WELLAND: dict[str, tuple[str, BoardProfile]] = {
    "tt03p5": ("pi-sw2-p3", RP2040_TT06),
    "tt04": ("pi-sw2-p4", RP2040_TT06),
    "tt05": ("pi-sw2-p5", RP2040_TT06),
    "tt06": ("pi-sw2-p6", RP2040_TT06),
    "tt07": ("pi-sw2-p7", RP2040_TT06),
    "tt08": ("pi-sw2-p8", RP2040_TT06),
    "fpga-1": ("pi-sw2-p33", RP2350_DBV3),
    "fpga-2": ("pi-sw2-p34", RP2350_DBV3),
    "fpga-3": ("pi-sw2-p35", RP2350_DBV3),
    "fpga-4": ("pi-sw2-p36", RP2350_DBV3),
}


def _welland_host_octet(slug: str) -> str:
    pin_name, _ = WELLAND[slug]
    return pin_name.rsplit("p", 1)[-1]


def daemon_url(slug: str) -> str:
    """HTTP base URL for the fpgas.online bridge daemon serving `slug`."""
    return f"http://10.21.2.{_welland_host_octet(slug)}:8765"


def bridge_ws_url(slug: str) -> str:
    """WebSocket URL for the fpgas.online serial bridge serving `slug`."""
    return f"ws://10.21.2.{_welland_host_octet(slug)}:8765/serial"
