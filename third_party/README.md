# Vendored third-party code

Host-only libraries for the simulation-trace tools, built when
`VGACAP_HOST_EXTRAS` is ON (the default). Nothing here is used by the board
builds (`firmware/`, `capture-rp1/`, `mpy/`). The code is upstream source,
unmodified except for the one local patch listed under SHA-256;
`CMakeLists.txt` builds it with `-O2 -w` and without vgacap's
`-Werror` flags.

## zstd 1.5.7 (`zstd/`)

- Source: https://github.com/facebook/zstd, tag `v1.5.7`
  (`f8745da6ff1ad1e7bab384bd1f9d742439278e99`).
- Licence: BSD-3-Clause (`zstd/LICENSE`; zstd is dual BSD/GPLv2, the BSD
  option is used).
- Files: `lib/zstd.h`, `lib/zstd_errors.h`, `lib/zdict.h`;
  `lib/common/*`, `lib/compress/*`, `lib/decompress/*.c` and `*.h`
  (`huf_decompress_amd64.S` is not taken; `ZSTD_DISABLE_ASM=1`);
  `contrib/seekable_format/{zstd_seekable.h,zstdseek_compress.c,zstdseek_decompress.c}`.
- Built without multithreading (`pool.c`, `threading.c`, `zstdmt_compress.c`
  are left out of the library) and without legacy format support.

## zlib 1.3.1 (`zlib/`)

- Source: https://github.com/madler/zlib, tag `v1.3.1`
  (`51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf`).
- Licence: zlib licence (`zlib/LICENSE`).
- Files: `adler32.c compress.c crc32.c deflate.c gzclose.c gzlib.c gzread.c
  gzwrite.c infback.c inffast.c inflate.c inftrees.c trees.c uncompr.c
  zutil.c` and all top-level `*.h` (`crc32.h deflate.h gzguts.h inffast.h
  inffixed.h inflate.h inftrees.h trees.h zconf.h zlib.h zutil.h`), `LICENSE`.
- Needed by libfst (`compress2`, `uncompress`, `gz*`). No bzip2.

## libfst (`libfst/`)

- Source: https://github.com/gtkwave/libfst at commit
  `b2681f927a092a21b42ba1f3b2a9af831deaf58d`.
- Licence: MIT (`libfst/LICENSE`); the bundled `lz4.{c,h}` are BSD-2-Clause,
  licence text in `libfst/LICENSE.lz4` (copied from the `lz4.c` header).
- Files, from `src/`: `fstapi.c fstapi.h fastlz.c fastlz.h lz4.c lz4.h`.
- Built with `HAVE_LIBPTHREAD FST_WRITER_PARALLEL HAVE_FSEEKO HAVE_REALPATH`
  and no `config.h`.

## SHA-256 (`sha256/`)

- Source: https://github.com/B-Con/crypto-algorithms, commit
  `cfbde48414baacf51fc7c74f275190881f037d32`, by Brad Conte.
- Licence: public domain (statement in `sha256/LICENSE`, quoted from the
  upstream README).
- Files: `sha256.c sha256.h`.
- Local patch: `sha256_transform` casts `data[j]` to `uint32_t` before
  `<< 24`, and `sha256.c` includes `<stdint.h>` for it. Upstream shifts the
  promoted `int`, which is undefined behaviour for bytes of 0x80 and above
  (found by UBSan in `test_hashes`).
