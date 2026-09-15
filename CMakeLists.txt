cmake_minimum_required(VERSION 3.16)
project(vgacap C)
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
add_library(vgacap_stream STATIC src/stream/writer.c src/stream/reader.c)
# --- frame: VGA picture reconstruction (src/frame) ---
add_library(vgacap_frame STATIC src/frame/modes.c src/frame/timing.c src/frame/frame.c)
# --- tools: command-line utilities (src/tools) ---
add_executable(vgacap-dump src/tools/vgacap_dump.c)
target_link_libraries(vgacap-dump PRIVATE vgacap_stream)
add_executable(vgacap-frames src/tools/vgacap_frames.c)
target_link_libraries(vgacap-frames PRIVATE vgacap_stream vgacap_frame)

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
vgacap_add_test(test_stream_header)
vgacap_add_test(test_stream_raw)
vgacap_add_test(test_stream_chunks)
