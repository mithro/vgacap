# SPDX-License-Identifier: Apache-2.0
# The trace format study's benchmarks (docs/design/2026-11-27-trace-format-study.md).
# A project of its own that builds vgacap from this checkout as a subdirectory:
#   cmake -S bench/format-study -B build-study -DCMAKE_BUILD_TYPE=Release && cmake --build build-study
# run_all.sh does that.
cmake_minimum_required(VERSION 3.16)
project(vgacap_format_study LANGUAGES C)
set(CMAKE_C_STANDARD 11)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

set(VGACAP_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../..)
add_subdirectory(${VGACAP_ROOT} vgacap EXCLUDE_FROM_ALL)
find_package(Threads REQUIRED)
# As vgacap does (its CMakeLists.txt, VGACAP_JCC_PAD): pad jumps away from
# 32-byte boundaries, so that loop timings do not move with code placement.
string(APPEND CMAKE_C_FLAGS " -Wa,-mbranches-within-32B-boundaries")

# vgacap's trace writer, compiled from its unchanged source with one call
# renamed (src/vgch_hook.h), so the study can skip or tap its compression.
add_library(study_vgch_writer STATIC ${VGACAP_ROOT}/src/trace/trace_write.c ${VGACAP_ROOT}/src/trace/blocks.c
  ${VGACAP_ROOT}/src/trace/trace_read.c)
target_include_directories(study_vgch_writer PRIVATE ${VGACAP_ROOT}/include ${VGACAP_ROOT}/src/trace)
target_compile_options(study_vgch_writer PRIVATE -include ${CMAKE_CURRENT_SOURCE_DIR}/src/vgch_hook.h)
target_link_libraries(study_vgch_writer PUBLIC vgacap_stream vgacap_zstd Threads::Threads)

# The lean writer (src/trace_write_lean.c): the same source with push2's slow
# path replaced and the API renamed (src/lean.h), so both link into one binary.
add_library(study_lean_writer STATIC src/trace_write_lean.c)
target_include_directories(study_lean_writer PRIVATE src ${VGACAP_ROOT}/include ${VGACAP_ROOT}/src/trace)
target_compile_options(study_lean_writer PRIVATE -include ${CMAKE_CURRENT_SOURCE_DIR}/src/vgch_hook.h)
target_link_libraries(study_lean_writer PUBLIC study_vgch_writer)

# The lazy-BLKH reader (src/trace_read_lazy.c): vgacap's reader, copied, with
# block starts read when first needed; API renamed (src/lazy.h).
add_library(study_lazy_reader STATIC src/trace_read_lazy.c)
target_include_directories(study_lazy_reader PRIVATE src ${VGACAP_ROOT}/include ${VGACAP_ROOT}/src/trace)
target_link_libraries(study_lazy_reader PUBLIC study_vgch_writer)

add_library(study_lz4hc STATIC third_party/lz4hc/lz4hc.c)
target_include_directories(study_lz4hc SYSTEM PUBLIC third_party/lz4hc ${VGACAP_ROOT}/third_party/libfst)

add_library(study_lxt2 STATIC third_party/lxt2/lxt2_write.c)
target_include_directories(study_lxt2 SYSTEM PUBLIC third_party/lxt2)
target_compile_definitions(study_lxt2 PRIVATE _GNU_SOURCE)
target_compile_options(study_lxt2 PRIVATE -w)
target_link_libraries(study_lxt2 PUBLIC vgacap_zlib)
target_include_directories(study_lxt2 SYSTEM PUBLIC ${VGACAP_ROOT}/third_party/zlib)

add_executable(fsbench src/fsbench.c src/source.c src/w_vgch.c src/w_mine.c src/w_ext.c src/w_lean.c src/bg.c src/rd.c src/rd_lazy.c src/codec.c src/ctr.c)
target_include_directories(fsbench PRIVATE src ${VGACAP_ROOT}/include ${VGACAP_ROOT}/src/trace)
target_compile_options(fsbench PRIVATE -Wall -Wextra -Werror -Wno-sign-conversion)
target_link_libraries(fsbench PRIVATE study_lazy_reader study_lean_writer study_vgch_writer vgacap_fst study_lxt2 study_lz4hc vgacap_zstd vgacap_zlib m)

# The simulation check (sim/run_sim.sh) links the study's writers into a
# Verilator testbench: everything but fsbench's main, as one library.
add_library(study_sim STATIC src/source.c src/w_vgch.c src/w_lean.c src/trace_write_lean.c src/w_mine.c src/w_ext.c src/bg.c src/rd.c src/rd_lazy.c src/trace_read_lazy.c src/codec.c src/ctr.c)
target_include_directories(study_sim PRIVATE src ${VGACAP_ROOT}/include ${VGACAP_ROOT}/src/trace)
target_compile_options(study_sim PRIVATE -Wall -Wextra -Werror -Wno-sign-conversion)
# vgacap's code, inside the lean writer, is built as vgacap builds it: not under -Werror.
set_source_files_properties(src/trace_write_lean.c PROPERTIES COMPILE_OPTIONS
  "-include;${CMAKE_CURRENT_SOURCE_DIR}/src/vgch_hook.h;-Wno-error")
set_source_files_properties(src/trace_read_lazy.c PROPERTIES COMPILE_OPTIONS "-Wno-error")
target_link_libraries(study_sim PRIVATE vgacap_fst study_lxt2 study_lz4hc vgacap_zstd vgacap_zlib)
