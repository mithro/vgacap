# SPDX-License-Identifier: Apache-2.0
import pytest

from vgacap import stream
from vgacap.stream import Header, unpack_words

from ttcap.boards import (
    FLAG_FIRST_SAMPLE_MSB,
    RP2040_TT06,
    RP2350_DBV3,
    WELLAND,
    bridge_ws_url,
    daemon_url,
    fdebug_addr,
    pio_base,
    profile_from_gpio_map,
    rx_dreq,
    rxf_addr,
)

# GPIOMap.all() as returned by the tt07 (RP2040) demo board, MicroPython 1.24.0.
TT07_GPIO_MAP = {
    "ui_in2": 11,
    "uio4": 25,
    "uio2": 23,
    "ui_in7": 20,
    "uio3": 24,
    "rpio29": 29,
    "nprojectrst": 1,
    "rp_projclk": 0,
    "cena": 4,
    "uo_out6": 15,
    "uio0": 21,
    "cinc": 2,
    "ncrst": 3,
    "uo_out7": 16,
    "uo_out4": 13,
    "uo_out5": 14,
    "uo_out2": 7,
    "uo_out3": 8,
    "uo_out0": 5,
    "uo_out1": 6,
    "uio6": 27,
    "ui_in1": 10,
    "ui_in0": 9,
    "uio5": 26,
    "ui_in3": 12,
    "ui_in5": 18,
    "ui_in4": 17,
    "uio1": 22,
    "ui_in6": 19,
    "uio7": 28,
}

# GPIOMap.all() as returned by the fpga-1 (RP2350) demo board, MicroPython 1.29.0-preview.
FPGA1_GPIO_MAP = {
    "mng01": 4,
    "mng03": 6,
    "mng00": 3,
    "mng02": 5,
    "mng04": 7,
    "mng05": 8,
    "mng06": 9,
    "mng07": 10,
    "adc3": 43,
    "adc4": 44,
    "uo_out6": 39,
    "uo_out7": 40,
    "uo_out4": 37,
    "rp_projclk": 16,
    "uo_out5": 38,
    "uo_out2": 35,
    "uo_out3": 36,
    "uo_out0": 33,
    "manual_project_clock": 15,
    "uo_out1": 34,
    "cinc": 2,
    "uio6": 31,
    "uio7": 32,
    "uio4": 29,
    "uio5": 30,
    "uio2": 27,
    "uio3": 28,
    "uio0": 25,
    "uio1": 26,
    "nprojectrst": 14,
    "analog_current_source": 12,
    "cena": 0,
    "adc1": 41,
    "adc2": 42,
    "ui_in1": 18,
    "ui_in0": 17,
    "ui_in3": 20,
    "ui_in2": 19,
    "ui_in5": 22,
    "ui_in4": 21,
    "ui_in7": 24,
    "ui_in6": 23,
    "adc5": 45,
    "rp_led": 11,
    "ncrst": 1,
}


def test_profile_from_gpio_map_rp2040_tt06():
    assert profile_from_gpio_map(TT07_GPIO_MAP) is RP2040_TT06


def test_profile_from_gpio_map_rp2350_dbv3():
    assert profile_from_gpio_map(FPGA1_GPIO_MAP) is RP2350_DBV3


def test_profile_from_gpio_map_unrecognized_raises():
    with pytest.raises(ValueError):
        profile_from_gpio_map({})


def test_welland_tt07():
    assert WELLAND["tt07"] == ("pi-sw2-p7", RP2040_TT06)


def test_welland_fpga1():
    assert WELLAND["fpga-1"] == ("pi-sw2-p33", RP2350_DBV3)


def test_daemon_url():
    assert daemon_url("tt07") == "http://10.21.2.7:8765"


def test_bridge_ws_url():
    assert bridge_ws_url("fpga-1") == "ws://10.21.2.33:8765/serial"


def test_push_thresh_is_a_full_fifo_word_of_samples():
    # 12 bits x 2 samples leaves bits 24..31 of the pushed word zero;
    # 8 bits x 4 samples fills all 32.
    assert RP2040_TT06.push_thresh == 24
    assert RP2350_DBV3.push_thresh == 32


def test_pio_base_steps_by_one_mib_per_block():
    assert pio_base(0) == 0x50200000
    assert pio_base(1) == 0x50300000
    assert pio_base(2) == 0x50400000


def test_rxf_addr_is_rxf0_plus_four_per_state_machine():
    assert [rxf_addr(0, sm) for sm in range(4)] == [0x50200020, 0x50200024, 0x50200028, 0x5020002C]


def test_rx_dreq_is_four_above_the_tx_dreq_of_the_same_block():
    assert [rx_dreq(0, sm) for sm in range(4)] == [4, 5, 6, 7]
    assert [rx_dreq(1, sm) for sm in range(4)] == [12, 13, 14, 15]


def test_fdebug_addr():
    assert fdebug_addr(0) == 0x50200008
    assert fdebug_addr(1) == 0x50300008


def _isr_shift_left(samples, sample_bits, push_thresh):
    """Simulate the PIO ISR with `in_shiftdir=SHIFT_LEFT` and autopush.

    RP2040 datasheet 3.4.4 (IN) / 3.5.4 (autopush): data enters at the LSB
    end and the bits already in the ISR move toward the MSB. The push is
    always the full 32-bit ISR; only the shift counter is compared with the
    threshold, and the ISR is zeroed afterwards.
    """
    isr = 0
    count = 0
    words = []
    for sample in samples:
        isr = ((isr << sample_bits) | (sample & ((1 << sample_bits) - 1))) & 0xFFFFFFFF
        count += sample_bits
        if count >= push_thresh:
            words.append(isr)
            isr = 0
            count = 0
    return words


@pytest.mark.parametrize(
    "profile,samples",
    [
        (RP2040_TT06, [0xABC, 0x123]),
        (RP2350_DBV3, [0xDE, 0xAD, 0xBE, 0xEF]),
    ],
    ids=lambda v: getattr(v, "name", "samples"),
)
def test_shift_left_layout_round_trips_through_unpack_words(profile, samples):
    words = _isr_shift_left(samples, profile.sample_bits, profile.push_thresh)
    assert len(words) == 1

    header = Header(
        sample_bits=profile.sample_bits,
        samples_per_word=profile.samples_per_word,
        flags=profile.flags,
    )
    assert unpack_words(header, words, len(samples)) == samples


def test_rp2040_shift_left_word_is_right_aligned():
    # sample 0 in bits 23:12, sample 1 in bits 11:0, bits 31:24 zero.
    assert _isr_shift_left([0xABC, 0x123], 12, 24) == [0x00ABC123]


def test_rp2350_shift_left_word_fills_all_32_bits():
    assert _isr_shift_left([0xDE, 0xAD, 0xBE, 0xEF], 8, 32) == [0xDEADBEEF]


def test_shift_right_would_left_justify_the_rp2040_pair():
    # Regression guard for the layout that was first shipped and rejected:
    # with SHIFT_RIGHT and push_thresh 24 the 24 valid bits sit in 31:8, so
    # unpack_words() cannot decode them with any flags value.
    isr = 0
    for sample in (0xABC, 0x123):
        isr = ((isr >> 12) | (sample << 20)) & 0xFFFFFFFF
    assert isr == 0x123ABC00
    assert unpack_words(Header(sample_bits=12, samples_per_word=2, flags=0), [isr], 2) != [
        0xABC,
        0x123,
    ]


def test_both_profiles_flag_first_sample_msb():
    assert RP2040_TT06.flags == FLAG_FIRST_SAMPLE_MSB == 1
    assert RP2350_DBV3.flags == FLAG_FIRST_SAMPLE_MSB
    assert FLAG_FIRST_SAMPLE_MSB == stream.FLAG_FIRST_SAMPLE_MSB
