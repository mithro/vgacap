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
# --- frame: VGA picture reconstruction (src/frame) ---
add_library(vgacap_frame STATIC src/frame/modes.c src/frame/timing.c)
# --- tools: command-line utilities (src/tools) ---

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
