# SPDX-License-Identifier: Apache-2.0
#
# The `vgacap` module for MicroPython's CMake-based ports: the rp2 port, on
# the Tiny Tapeout demo board v3 (RP2350). The capture engines on the chip's
# PIO and DMA (firmware/pico/hal_rp2.c), borrowed from MicroPython for the
# length of a capture. See README.md.
#
#   make -C ports/rp2 BOARD=... USER_C_MODULES=<vgacap>/mpy/micropython.cmake

set(VGACAP_ROOT ${CMAKE_CURRENT_LIST_DIR}/..)

add_library(usermod_vgacap INTERFACE)

set(VGACAP_MPY_SOURCES
  ${CMAKE_CURRENT_LIST_DIR}/modvgacap.c
  ${CMAKE_CURRENT_LIST_DIR}/hal_mpy.c
  ${VGACAP_ROOT}/firmware/core/fw_capture.c
  ${VGACAP_ROOT}/firmware/core/fw_args.c
  ${VGACAP_ROOT}/firmware/core/fw_board.c
  ${VGACAP_ROOT}/firmware/core/fw_clock.c
  ${VGACAP_ROOT}/firmware/core/fw_pio.c
  ${VGACAP_ROOT}/firmware/pico/hal_rp2.c
  ${VGACAP_ROOT}/src/stream/writer.c)

target_sources(usermod_vgacap INTERFACE ${VGACAP_MPY_SOURCES})

target_include_directories(usermod_vgacap INTERFACE
  ${CMAKE_CURRENT_LIST_DIR}
  ${VGACAP_ROOT}/firmware/core
  ${VGACAP_ROOT}/firmware/pico
  ${VGACAP_ROOT}/include)

# DMA_IRQ_0 is MicroPython's (rp2.DMA); a capture's channels raise DMA_IRQ_1
# through a shared handler (firmware/pico/hal_rp2.h).
target_compile_definitions(usermod_vgacap INTERFACE VGACAP_MPY_SIM=0 HAL_RP2_DMA_IRQ_INDEX=1)

# Ours are held to -Wall -Wextra -Werror, as in the firmware build; the
# port's own sources are not ours to.
set_source_files_properties(${VGACAP_MPY_SOURCES} PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra;-Werror")

target_link_libraries(usermod INTERFACE usermod_vgacap)
