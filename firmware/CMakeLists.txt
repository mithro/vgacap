# SPDX-License-Identifier: Apache-2.0
#
# vgacap capture firmware for the Tiny Tapeout demo board v3 (RP2350B),
# built with pico-sdk. A separate CMake project from the host build in the
# repository root, which never needs the ARM toolchain; see README.md:
#
#   cmake -S firmware -B firmware/build -G Ninja -DPICO_SDK_PATH=<pico-sdk 2.2.0>
#   cmake --build firmware/build
#   -> firmware/build/vgacap_fw.uf2 (no_flash: loads into SRAM, runs, and a
#      reset returns the board to its stock firmware)
cmake_minimum_required(VERSION 3.13)

# Pinned: pico-sdk 2.2.0, commit a1438dff1d38bd9c65dbd693f0e5db4b9ae91779,
# with lib/tinyusb at 86ad6e56c1700e85f1c5678607a762cfe3aa2f47 (its
# submodule pin). Another SDK is refused rather than silently used.
set(VGACAP_PICO_SDK_VERSION 2.2.0)

set(PICO_BOARD tt_demoboard_v3)
set(PICO_BOARD_HEADER_DIRS ${CMAKE_CURRENT_LIST_DIR}/pico/boards)
set(PICO_PLATFORM rp2350-arm-s)

include(pico_sdk_import.cmake)
project(vgacap_fw C CXX ASM)
set(CMAKE_C_STANDARD 11)
pico_sdk_init()

if (NOT PICO_SDK_VERSION_STRING VERSION_EQUAL VGACAP_PICO_SDK_VERSION)
  message(FATAL_ERROR "this firmware is pinned to pico-sdk ${VGACAP_PICO_SDK_VERSION}, "
                      "found ${PICO_SDK_VERSION_STRING} at ${PICO_SDK_PATH}")
endif()

set(VGACAP_ROOT ${CMAKE_CURRENT_LIST_DIR}/..)
set(FW_SOURCES
  ${CMAKE_CURRENT_LIST_DIR}/core/fw.c
  ${CMAKE_CURRENT_LIST_DIR}/core/fw_args.c
  ${CMAKE_CURRENT_LIST_DIR}/core/fw_board.c
  ${CMAKE_CURRENT_LIST_DIR}/core/fw_clock.c
  ${CMAKE_CURRENT_LIST_DIR}/core/fw_pio.c
  ${CMAKE_CURRENT_LIST_DIR}/pico/main.c
  ${CMAKE_CURRENT_LIST_DIR}/pico/hal_pico.c
  ${CMAKE_CURRENT_LIST_DIR}/pico/usb_descriptors.c
  ${VGACAP_ROOT}/src/stream/writer.c)

add_executable(vgacap_fw ${FW_SOURCES})
target_include_directories(vgacap_fw PRIVATE
  ${CMAKE_CURRENT_LIST_DIR}/core ${CMAKE_CURRENT_LIST_DIR}/pico ${VGACAP_ROOT}/include)
target_link_libraries(vgacap_fw PRIVATE
  pico_stdlib pico_unique_id pico_bootrom
  hardware_pio hardware_dma hardware_pwm hardware_clocks hardware_watchdog
  tinyusb_device tinyusb_board)
# Our sources only: the SDK's are compiled into the same target and are
# not ours to hold to -Werror.
set_source_files_properties(${FW_SOURCES} PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra;-Werror")

# Loaded into SRAM by the bootrom and run from there; flash is never
# written, so a reset or power cycle brings back the stock firmware.
pico_set_binary_type(vgacap_fw no_flash)
# Nothing of the SDK's stdio: GPIO 0/1 (its default UART) are the board's
# control lines, and the USB port is ours (pico/usb_descriptors.c).
pico_enable_stdio_uart(vgacap_fw 0)
pico_enable_stdio_usb(vgacap_fw 0)
pico_add_extra_outputs(vgacap_fw)

# The PIO sources (pio/*.pio) are assembled by pioasm here and compared
# with the checked-in output (pio/*.hex) that the host tests read; the
# build fails if they differ. After changing a .pio file, copy the new
# .hex from the build directory into pio/.
pico_init_pioasm()
foreach(prog vgacap_stream vgacap_frame)
  set(src ${CMAKE_CURRENT_LIST_DIR}/pio/${prog}.pio)
  set(hex ${CMAKE_CURRENT_BINARY_DIR}/${prog}.hex)
  add_custom_command(OUTPUT ${hex}.checked
    COMMAND pioasm -o hex ${src} ${hex}
    COMMAND ${CMAKE_COMMAND} -E compare_files ${hex} ${CMAKE_CURRENT_LIST_DIR}/pio/${prog}.hex
    COMMAND ${CMAKE_COMMAND} -E touch ${hex}.checked
    DEPENDS ${src} ${CMAKE_CURRENT_LIST_DIR}/pio/${prog}.hex
    COMMENT "pioasm ${prog}.pio, and compare with pio/${prog}.hex"
    VERBATIM)
  list(APPEND pio_checked ${hex}.checked)
endforeach()
add_custom_target(vgacap_fw_pio_check ALL DEPENDS ${pio_checked})
add_dependencies(vgacap_fw vgacap_fw_pio_check)
