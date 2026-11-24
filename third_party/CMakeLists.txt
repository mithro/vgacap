# SPDX-License-Identifier: Apache-2.0
# Vendored host-only libraries (see README.md). Built as static libraries for
# the simulation-trace tools only; no board build references this directory.
#
# The top-level -Wall -Wextra -Werror -Wshadow -Wconversion are directory
# COMPILE_OPTIONS, so reset them here: they would fail on code we do not own.
set_directory_properties(PROPERTIES COMPILE_OPTIONS "")
add_compile_options(-O2 -w)

find_package(Threads REQUIRED)

# --- zstd, with the seekable format ---
set(ZSTD_LIB ${CMAKE_CURRENT_SOURCE_DIR}/zstd/lib)
set(ZSTD_SEEK ${CMAKE_CURRENT_SOURCE_DIR}/zstd/contrib/seekable_format)
file(GLOB ZSTD_SRC
  ${ZSTD_LIB}/common/*.c ${ZSTD_LIB}/compress/*.c ${ZSTD_LIB}/decompress/*.c)
# pool.c and threading.c are only used by multithreaded zstd, which is off.
list(FILTER ZSTD_SRC EXCLUDE REGEX "/(pool|threading|zstdmt_compress)\\.c$")
add_library(vgacap_zstd STATIC ${ZSTD_SRC}
  ${ZSTD_SEEK}/zstdseek_compress.c ${ZSTD_SEEK}/zstdseek_decompress.c)
target_include_directories(vgacap_zstd SYSTEM PUBLIC ${ZSTD_LIB} ${ZSTD_SEEK})
target_include_directories(vgacap_zstd PRIVATE
  ${ZSTD_LIB}/common ${ZSTD_LIB}/compress ${ZSTD_LIB}/decompress)
target_compile_definitions(vgacap_zstd PRIVATE
  ZSTD_LEGACY_SUPPORT=0 ZSTD_DISABLE_ASM=1)

# --- zlib (libfst needs compress2, uncompress and the gz* calls) ---
add_library(vgacap_zlib STATIC
  zlib/adler32.c zlib/compress.c zlib/crc32.c zlib/deflate.c zlib/gzclose.c
  zlib/gzlib.c zlib/gzread.c zlib/gzwrite.c zlib/infback.c zlib/inffast.c
  zlib/inflate.c zlib/inftrees.c zlib/trees.c zlib/uncompr.c zlib/zutil.c)
target_include_directories(vgacap_zlib SYSTEM PUBLIC zlib)
target_compile_definitions(vgacap_zlib PRIVATE _LARGEFILE64_SOURCE=1)

# --- libfst: no config.h; the feature macros it would define are set here ---
add_library(vgacap_fst STATIC
  libfst/fstapi.c libfst/fastlz.c libfst/lz4.c)
target_include_directories(vgacap_fst SYSTEM PUBLIC libfst)
target_compile_definitions(vgacap_fst PRIVATE
  HAVE_LIBPTHREAD FST_WRITER_PARALLEL HAVE_FSEEKO HAVE_REALPATH)
target_link_libraries(vgacap_fst PUBLIC vgacap_zlib Threads::Threads)

# --- SHA-256 (public domain) ---
add_library(vgacap_sha256 STATIC sha256/sha256.c)
target_include_directories(vgacap_sha256 SYSTEM PUBLIC sha256)
