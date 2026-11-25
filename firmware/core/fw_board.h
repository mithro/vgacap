// SPDX-License-Identifier: Apache-2.0
#ifndef VGACAP_FW_BOARD_H
#define VGACAP_FW_BOARD_H

#include "fw_hal.h"

#ifdef __cplusplus
extern "C" {
#endif

// The one board this firmware is for: Tiny Tapeout demo board v3, RP2350.
extern const fw_board_t fw_board_dbv3;

// The capture buffer both HALs reserve unless told otherwise: what is left
// of the RP2350's 520 KB once the firmware itself (code, data, stacks and
// TinyUSB, all in SRAM in a no_flash build) has its share. A whole
// 640x480@60 frame region (481 lines x 800 bytes = 384,800) fits; 800x600
// is swept in two windows.
#ifndef FW_ARENA_BYTES
#define FW_ARENA_BYTES (440u * 1024u)
#endif

// GPIO 1 is the demo board's ctrl_sel_nrst. On the FPGA breakout it is the
// iCE40's CRESET_B (tt-micropython-firmware fabricfoxv2.py drives it low
// to start a configuration and leaves it high); on an ASIC carrier it is
// the project mux controller's reset. Low erases the FPGA or resets the
// mux. The stock firmware leaves it driven high in both cases; in the
// bootloader it is an input with the pad pull-down, and the board's
// pull-up has to hold it. hal_pico_init() drives it high at boot, level
// before direction so it never glitches low, and nothing drives it low.
// GPIO 0 and 2 (ctrl_ena, ctrl_sel_inc) are not driven (README.md,
// "Hardware notes").
#define FW_CTRL_RESET_N_GPIO 1u

#ifdef __cplusplus
}
#endif
#endif
