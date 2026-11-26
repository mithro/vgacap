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
  # --- trace: the simulation trace writer (src/trace), host only ---
  find_package(Threads REQUIRED)
  add_library(vgacap_trace STATIC src/trace/trace_write.c src/trace/blocks.c src/trace/trace_read.c)
  target_link_libraries(vgacap_trace PUBLIC vgacap_stream vgacap_zstd Threads::Threads)
  # The slow path reads fields the inline push has just stored one by one;
  # SLP vectorisation would read two of them as one 16-byte load, which
  # cannot be forwarded from those stores and stalls on every call.
  set_source_files_properties(src/trace/trace_write.c PROPERTIES COMPILE_OPTIONS
    "$<$<C_COMPILER_ID:GNU>:-fno-tree-slp-vectorize>;$<$<C_COMPILER_ID:Clang,AppleClang>:-fno-slp-vectorize>")
  # vgacap-dump reads traces too (a .trace.zst prints its blocks' chunks).
  # Without the host extras it reads plain streams only and refuses a zstd
  # file with a message saying so.
  target_link_libraries(vgacap-dump PRIVATE vgacap_trace)
  target_compile_definitions(vgacap-dump PRIVATE VGACAP_DUMP_TRACES)
  # Test-only helper for python/tests/test_roundtrip_c.py: writes a trace.
  add_executable(trace-make tests/trace_make.c)
  target_link_libraries(trace-make PRIVATE vgacap_trace)
  # --- sample: the shared calibration and frame clock (src/sample), host only ---
  add_library(vgacap_sample STATIC src/sample/calib.c src/sample/frameclock.c src/sample/tbv1.c src/sample/centrev1.c src/sample/hashes.c src/sample/outputs.c)
  target_link_libraries(vgacap_sample PUBLIC vgacap_frame vgacap_trace vgacap_stream vgacap_sha256 vgacap_zstd m)
  # Test-only helper for python/tests/test_calib.py: calibration and frame
  # clock of a trace.
  add_executable(vgacap-calib-dump tests/calib_dump.c)
  target_link_libraries(vgacap-calib-dump PRIVATE vgacap_sample)
  # Test-only helper for python/tests/test_samplers.py: tb-v1's frames of a
  # trace.
  add_executable(vgacap-tbv1-dump tests/tbv1_dump.c)
  target_link_libraries(vgacap-tbv1-dump PRIVATE vgacap_sample)
  # Test-only helper for python/tests/test_samplers.py: centre-v1's frames,
  # raw clock-resolution frames and diagnostics of a trace.
  add_executable(vgacap-centre-dump tests/centre_dump.c)
  target_link_libraries(vgacap-centre-dump PRIVATE vgacap_sample)
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
vgacap_add_test(test_frame_raw tests/synth.c)
vgacap_add_test(test_timing_sim tests/synth.c)
# The fast path against the a26c942 push path kept in tests/fastpath_ref/.
vgacap_add_test(test_frame_fastpath tests/synth.c tests/fastpath_ref/ref.c)
vgacap_add_test(test_stream_header)
vgacap_add_test(test_stream_raw)
vgacap_add_test(test_stream_chunks)
vgacap_add_test(test_stream_v2)
if(VGACAP_HOST_EXTRAS)
  vgacap_add_test(test_third_party)
  target_link_libraries(test_third_party PRIVATE vgacap_zstd vgacap_zlib vgacap_fst vgacap_sha256)
  # The trace writer: a zstd seekable file of VGCH v2 blocks (src/trace).
  vgacap_add_test(test_trace_write)
  target_link_libraries(test_trace_write PRIVATE vgacap_trace)
  set_tests_properties(test_trace_write PROPERTIES WORKING_DIRECTORY ${CMAKE_BINARY_DIR})
  # The trace reader: ranges, a rebuilt seek table, plain VGCH streams.
  vgacap_add_test(test_trace_read)
  target_link_libraries(test_trace_read PRIVATE vgacap_trace vgacap_zlib)
  target_include_directories(test_trace_read PRIVATE src/trace)
  target_compile_definitions(test_trace_read PRIVATE
    VGACAP_DUMP_PATH="$<TARGET_FILE:vgacap-dump>" VGACAP_FIXTURES="${CMAKE_SOURCE_DIR}/tests/fixtures")
  add_dependencies(test_trace_read vgacap-dump)
  set_tests_properties(test_trace_read PROPERTIES WORKING_DIRECTORY ${CMAKE_BINARY_DIR})
  # The shared calibration (port of tb.cpp) and the frame clock.
  vgacap_add_test(test_calib tests/synth.c)
  target_link_libraries(test_calib PRIVATE vgacap_sample)
  # tb-v1, the port of tb.cpp's frame decoding.
  vgacap_add_test(test_tbv1 tests/synth.c)
  target_link_libraries(test_tbv1 PRIVATE vgacap_sample)
  # centre-v1: libvgaframe's lines and frames, its own mid-pixel pick.
  vgacap_add_test(test_centrev1 tests/synth.c)
  target_link_libraries(test_centrev1 PRIVATE vgacap_sample)
  # frames.txt and the hash files.
  vgacap_add_test(test_outputs)
  target_link_libraries(test_outputs PRIVATE vgacap_sample)
  # Test-only helper for python/tests/test_framesfile.py: writes the text
  # outputs of a fixed run.
  add_executable(vgacap-outputs-dump tests/outputs_dump.c)
  target_link_libraries(vgacap-outputs-dump PRIVATE vgacap_sample)
  # The frame hash methods.
  vgacap_add_test(test_hashes)
  target_link_libraries(test_hashes PRIVATE vgacap_sample)
  # Timing, meaningful only on big-storage: labelled perf, kept out of CI
  # (ctest -LE perf) and run by hand.
  add_test(NAME test_trace_push_speed COMMAND test_trace_write --speed)
  set_tests_properties(test_trace_push_speed PROPERTIES
    LABELS perf WORKING_DIRECTORY ${CMAKE_BINARY_DIR})
else()
  # Without the host extras vgacap-dump cannot read a trace and must say so.
  # PASS_REGULAR_EXPRESSION replaces the exit-code check, so the wrapper
  # prints the exit code after the message, and the pattern needs both.
  add_test(NAME vgacap_dump_refuses_zstd COMMAND sh -c
    "printf '\\050\\265\\057\\375' > dump_refuses_zstd.zst && \"$<TARGET_FILE:vgacap-dump>\" dump_refuses_zstd.zst 2>&1; echo \"exit=$?\""
    WORKING_DIRECTORY ${CMAKE_BINARY_DIR})
  set_tests_properties(vgacap_dump_refuses_zstd PROPERTIES PASS_REGULAR_EXPRESSION
    "vgacap-dump: built without VGACAP_HOST_EXTRAS; decompress with zstd -d first\nexit=1\n")
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
# Test-only helper for python/tests/test_calib.py: one frame of
# synth_frame_hvsync, to compare with synthtrace's testpatterns phase.
add_executable(synth-hvsync-dump tests/synth_hvsync_dump.c tests/synth.c)
target_include_directories(synth-hvsync-dump PRIVATE tests)
target_link_libraries(synth-hvsync-dump PRIVATE vgacap_frame)
