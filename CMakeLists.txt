cmake_minimum_required(VERSION 3.16)
project(vgacap VERSION 0.0.1 LANGUAGES C)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()
add_compile_options(-Wall -Wextra -Werror -Wshadow -Wconversion)
include_directories(include)
enable_testing()

# Libraries. Each section is owned by one component; add sources in place.
# --- stream: chunked capture stream encoder/decoder (src/stream) ---
add_library(vgacap_stream STATIC src/stream/writer.c src/stream/reader.c src/stream/time.c)
# --- frame: VGA picture reconstruction (src/frame) ---
add_library(vgacap_frame STATIC src/frame/modes.c src/frame/timing.c src/frame/frame.c)
# Benchmark (not a test): libvgaframe on run-length input.
add_executable(bench-runs bench/bench_runs.c tests/synth.c)
target_include_directories(bench-runs PRIVATE tests)
target_link_libraries(bench-runs PRIVATE vgacap_frame vgacap_stream)
# --- tools: command-line utilities (src/tools) ---
add_executable(vgacap-dump src/tools/vgacap_dump.c)
target_link_libraries(vgacap-dump PRIVATE vgacap_stream)
add_executable(vgacap-frames src/tools/vgacap_frames.c)
target_link_libraries(vgacap-frames PRIVATE vgacap_stream vgacap_frame)

# --- gst: GStreamer plugin (gst/), built only when GStreamer is installed ---
add_subdirectory(gst)

# --- third_party: vendored zstd, zlib, libfst and SHA-256 for the host-only
# tools (simulation traces). Not used by the board builds. OFF skips them
# and test_third_party.
option(VGACAP_HOST_EXTRAS "Build the vendored host-only libraries" ON)
if(VGACAP_HOST_EXTRAS)
  add_subdirectory(third_party)
endif()

function(vgacap_add_test name)
  add_executable(${name} tests/${name}.c ${ARGN})
  target_include_directories(${name} PRIVATE tests)
  foreach(lib vgacap_stream vgacap_frame)
    if(TARGET ${lib})
      target_link_libraries(${name} PRIVATE ${lib})
    endif()
  endforeach()
  add_test(NAME ${name} COMMAND ${name})
endfunction()

# Tests.
vgacap_add_test(test_harness_smoke)
vgacap_add_test(test_modes)
vgacap_add_test(test_timing tests/synth.c)
vgacap_add_test(test_frame tests/synth.c)
vgacap_add_test(test_frame_partial tests/synth.c)
# The fast path against the a26c942 push path kept in tests/fastpath_ref/.
vgacap_add_test(test_frame_fastpath tests/synth.c tests/fastpath_ref/ref.c)
vgacap_add_test(test_stream_header)
vgacap_add_test(test_stream_raw)
vgacap_add_test(test_stream_chunks)
if(VGACAP_HOST_EXTRAS)
  vgacap_add_test(test_third_party)
  target_link_libraries(test_third_party PRIVATE vgacap_zstd vgacap_zlib vgacap_fst vgacap_sha256)
endif()

# --- firmware: the pico-sdk capture firmware's portable core (firmware/core),
# built here for its host simulator and its tests. The firmware itself is
# firmware/CMakeLists.txt, a separate pico-sdk project that needs the ARM
# toolchain (firmware/README.md); nothing here does.
add_library(vgacap_fw_core STATIC
  firmware/core/fw.c firmware/core/fw_capture.c firmware/core/fw_args.c firmware/core/fw_board.c
  firmware/core/fw_clock.c firmware/core/fw_pio.c)
target_include_directories(vgacap_fw_core PUBLIC firmware/core)
target_link_libraries(vgacap_fw_core PUBLIC vgacap_stream)
add_executable(vgacap-fw-sim firmware/sim/sim_main.c firmware/sim/hal_sim.c)
target_include_directories(vgacap-fw-sim PRIVATE firmware/sim)
target_link_libraries(vgacap-fw-sim PRIVATE vgacap_fw_core m)
vgacap_add_test(test_fw_units)
target_link_libraries(test_fw_units PRIVATE vgacap_fw_core)
vgacap_add_test(test_fw_core firmware/sim/hal_sim.c)
target_include_directories(test_fw_core PRIVATE firmware/sim)
target_link_libraries(test_fw_core PRIVATE vgacap_fw_core m)

# --- time: sample-to-ps rule and shortest round-trip double formatting
# (src/stream/time.c is host-only: the board builds compile writer.c alone).
vgacap_add_test(test_time)
# Test-only helper for python/tests/test_timebase.py; includes time.c to reach
# its static 128-bit helpers, so it links no library.
add_executable(test_time_dump tests/test_time_dump.c)
