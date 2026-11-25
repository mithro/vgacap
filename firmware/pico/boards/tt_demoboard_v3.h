// SPDX-License-Identifier: Apache-2.0
// pico-sdk board header: the Tiny Tapeout demo board v3.
//
// An RP2350B (48 GPIOs: uo_out is GPIO 33..40), so not the `pico2` board,
// whose RP2350A stops at GPIO 29. Deliberately no default UART, LED, I2C
// or SPI pins: on this board GPIO 0/1 are the project mux / FPGA control
// lines and GPIO 25 is uio[0], and nothing in the SDK may claim them.
// The firmware is built no_flash and never touches the flash; the size is
// the board's 16 MB only so that the SDK's flash defaults are sane.
#ifndef _BOARDS_TT_DEMOBOARD_V3_H
#define _BOARDS_TT_DEMOBOARD_V3_H

pico_board_cmake_set(PICO_PLATFORM, rp2350)

#define TT_DEMOBOARD_V3
#define PICO_RP2350A 0

#define PICO_BOOT_STAGE2_CHOOSE_W25Q080 1
#ifndef PICO_FLASH_SPI_CLKDIV
#define PICO_FLASH_SPI_CLKDIV 2
#endif
pico_board_cmake_set_default(PICO_FLASH_SIZE_BYTES, (16 * 1024 * 1024))
#ifndef PICO_FLASH_SIZE_BYTES
#define PICO_FLASH_SIZE_BYTES (16 * 1024 * 1024)
#endif

#endif
