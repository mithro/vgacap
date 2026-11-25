// SPDX-License-Identifier: Apache-2.0
// The Tiny Tapeout demo board v3 (RP2350): the GPIOs the firmware uses.
// tt-micropython-firmware src/ttboard/pins/gpio_map_dbv3.py
// (GPIOMapTTDBv3), and python/ttcap/boards.py RP2350_DBV3.
#include "fw_board.h"

const fw_board_t fw_board_dbv3 = {
    .chip = "rp2350",
    .clk_gpio = 16,      // RP_PROJCLK
    .rst_n_gpio = 14,    // PROJECT_nRST
    .ui_in_gpio = 17,    // UI_IN0..7 = 17..24
    .uo_gpio = 33,       // UO_OUT0..7 = 33..40
    .in_count = 8,
    .push_thresh = 32,   // 4 samples of 8 bits per FIFO word
    .pio_gpio_base = 16, // the window that reaches both GPIO 16 and GPIO 40
    .pio_count = 3,
};
