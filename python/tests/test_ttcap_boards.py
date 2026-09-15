# SPDX-License-Identifier: Apache-2.0
import pytest

from ttcap.boards import RP2040_TT06, RP2350_DBV3, WELLAND, bridge_ws_url, daemon_url, profile_from_gpio_map

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
