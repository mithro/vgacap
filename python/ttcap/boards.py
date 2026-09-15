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

#: `VGACAP_FLAG_FIRST_SAMPLE_MSB` from include/vgacap/stream.h (mirrored as
#: `vgacap.stream.FLAG_FIRST_SAMPLE_MSB`): sample 0 occupies the most
#: significant slot of each packed word. Both profiles set it -- see
#: `BoardProfile.flags`.
FLAG_FIRST_SAMPLE_MSB = 1


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
    #: Always `FLAG_FIRST_SAMPLE_MSB`: the sampler shifts the ISR left, so
    #: the first sample of a word ends up in its most significant slot.
    flags: int
    signal_map: tuple[int, ...]

    @property
    def push_thresh(self) -> int:
        """PIO autopush threshold in bits: the sample bits in one FIFO word.

        24 for the RP2040's 12-bit x2 layout, 32 for the RP2350's 8-bit x4.

        The PIO always pushes all 32 ISR bits; only the *shift counter* is
        compared against the threshold. With `in_shiftdir=SHIFT_LEFT` data
        enters at the LSB end and earlier samples move up, so after
        `samples_per_word` samples the word is right-aligned with sample 0 in
        the top occupied slot -- which is exactly
        `flags=FLAG_FIRST_SAMPLE_MSB`, whatever the threshold is. (With
        SHIFT_RIGHT the pair would be left-justified in the word whenever the
        threshold is below 32, which no `flags` value can express.)
        """
        return self.sample_bits * self.samples_per_word


RP2040_TT06 = BoardProfile(
    "rp2040-tt06map",
    0,
    (5, 6, 7, 8, 13, 14, 15, 16),
    0,
    5,
    12,
    12,
    2,
    FLAG_FIRST_SAMPLE_MSB,
    (11, 3, 0, 8, 1, 9, 2, 10),
)
RP2350_DBV3 = BoardProfile(
    "rp2350-dbv3",
    16,
    tuple(range(33, 41)),
    16,
    33,
    8,
    8,
    4,
    FLAG_FIRST_SAMPLE_MSB,
    (7, 3, 0, 4, 1, 5, 2, 6),
)


#: Base address of PIO block 0. PIO blocks are 1 MiB apart on both chips:
#: RP2040 has PIO0 at 0x5020_0000 and PIO1 at 0x5030_0000; RP2350 keeps those
#: and adds PIO2 at 0x5040_0000 (pico-sdk `addressmap.h`).
PIO0_BASE = 0x50200000
PIO_BLOCK_STRIDE = 0x00100000
#: Offset of RXF0 within a PIO block; RXF1..3 follow at 4-byte steps
#: (pico-sdk `hardware/structs/pio.h`: `rxf[4]` at 0x20).
PIO_RXF0_OFFSET = 0x20
#: Offset of FDEBUG within a PIO block. Its RXSTALL field is bits 0..3, one
#: per state machine, and is write-1-to-clear.
PIO_FDEBUG_OFFSET = 0x08
#: DREQ_PIO0_TX0 == 0 and DREQ_PIO0_RX0 == 4, with 8 DREQs per PIO block
#: (pico-sdk `hardware/regs/dreq.h`); same layout on RP2040 and RP2350.
DREQ_PIO0_RX0 = 4
DREQ_PIO_STRIDE = 8


def pio_base(pio: int) -> int:
    """Base address of PIO block `pio` (0, 1, or 2 on RP2350)."""
    return PIO0_BASE + PIO_BLOCK_STRIDE * pio


def rxf_addr(pio: int, sm: int) -> int:
    """Address of the RX FIFO register for state machine `sm` of PIO `pio`.

    This is the DMA read address for the sampler: PIO0 SM0 is 0x5020_0020.
    """
    return pio_base(pio) + PIO_RXF0_OFFSET + 4 * sm


def rx_dreq(pio: int, sm: int) -> int:
    """DMA data request line for the RX FIFO of `sm` on PIO `pio`.

    | pio | sm | DREQ |
    |-----|----|------|
    |  0  |  0 |   4  |
    |  0  |  3 |   7  |
    |  1  |  1 |  13  |
    |  2  |  0 |  20  |
    """
    return DREQ_PIO_STRIDE * pio + DREQ_PIO0_RX0 + sm


def fdebug_addr(pio: int) -> int:
    """Address of PIO `pio`'s FDEBUG register (RXSTALL in bits 0..3)."""
    return pio_base(pio) + PIO_FDEBUG_OFFSET


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
