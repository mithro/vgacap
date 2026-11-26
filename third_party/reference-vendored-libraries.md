---
type: reference
owner: vgacap maintainers
reader: anyone who needs the version, license or local changes of a library built into vgacap
review: 2027-05-27
---

# Vendored libraries

This page lists the libraries whose source vgacap carries in [`third_party/`](README.md), with their versions, licenses and local changes. It is for someone checking a license or updating a library. The libraries serve the host-only simulation trace tools, and no board build uses them: not the vgacap firmware, `vgacap-rp1` or the MicroPython module.

[`third_party/CMakeLists.txt`](CMakeLists.txt) builds them when `VGACAP_HOST_EXTRAS` is `ON`, the default. It builds them with `-O2 -w` and without vgacap's `-Werror` warning flags, since the code is not vgacap's. [`tests/test_third_party.c`](../tests/test_third_party.c) checks that each one does its job.

## Versions and licenses

Library is the directory under `third_party/`, and Upstream the repository it comes from. Tag and Commit are the upstream version taken. License is the license used, of zstd's two the BSD one, and License file its copy beside the code.

| Library | Upstream | Tag | Commit | License | License file |
|---|---|---|---|---|---|
| `zstd/` | [facebook/zstd](https://github.com/facebook/zstd) | `v1.5.7` | `f8745da6ff1ad1e7bab384bd1f9d742439278e99` | BSD-3-Clause | `zstd/LICENSE` |
| `zlib/` | [madler/zlib](https://github.com/madler/zlib) | `v1.3.1` | `51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf` | Zlib | `zlib/LICENSE` |
| `libfst/` | [gtkwave/libfst](https://github.com/gtkwave/libfst) | none | `b2681f927a092a21b42ba1f3b2a9af831deaf58d` | MIT | `libfst/LICENSE` |
| `libfst/lz4.c` and `libfst/lz4.h` | bundled in libfst | none | as libfst | BSD-2-Clause | `libfst/LICENSE.lz4` |
| `sha256/` | [B-Con/crypto-algorithms](https://github.com/B-Con/crypto-algorithms) | none | `cfbde48414baacf51fc7c74f275190881f037d32` | public domain | `sha256/LICENSE` |

## Files copied

Library is as above, and Upstream path each path copied, from the upstream root.

| Library | Upstream path |
|---|---|
| `zstd/` | `lib/zstd.h` |
| `zstd/` | `lib/zstd_errors.h` |
| `zstd/` | `lib/zdict.h` |
| `zstd/` | `lib/common/*` |
| `zstd/` | `lib/compress/*` |
| `zstd/` | `lib/decompress/*.c` |
| `zstd/` | `lib/decompress/*.h` |
| `zstd/` | `contrib/seekable_format/zstd_seekable.h` |
| `zstd/` | `contrib/seekable_format/zstdseek_compress.c` |
| `zstd/` | `contrib/seekable_format/zstdseek_decompress.c` |
| `zlib/` | `LICENSE` |
| `zlib/` | `adler32.c` |
| `zlib/` | `compress.c` |
| `zlib/` | `crc32.c` |
| `zlib/` | `crc32.h` |
| `zlib/` | `deflate.c` |
| `zlib/` | `deflate.h` |
| `zlib/` | `gzclose.c` |
| `zlib/` | `gzguts.h` |
| `zlib/` | `gzlib.c` |
| `zlib/` | `gzread.c` |
| `zlib/` | `gzwrite.c` |
| `zlib/` | `infback.c` |
| `zlib/` | `inffast.c` |
| `zlib/` | `inffast.h` |
| `zlib/` | `inffixed.h` |
| `zlib/` | `inflate.c` |
| `zlib/` | `inflate.h` |
| `zlib/` | `inftrees.c` |
| `zlib/` | `inftrees.h` |
| `zlib/` | `trees.c` |
| `zlib/` | `trees.h` |
| `zlib/` | `uncompr.c` |
| `zlib/` | `zconf.h` |
| `zlib/` | `zlib.h` |
| `zlib/` | `zutil.c` |
| `zlib/` | `zutil.h` |
| `libfst/` | `src/fstapi.c` |
| `libfst/` | `src/fstapi.h` |
| `libfst/` | `src/fastlz.c` |
| `libfst/` | `src/fastlz.h` |
| `libfst/` | `src/lz4.c` |
| `libfst/` | `src/lz4.h` |
| `sha256/` | `sha256.c` |
| `sha256/` | `sha256.h` |

## Files left out

Library is as above, and Path the upstream path. Copied and Built say whether the file is in `third_party/` and whether the build compiles it. `ZSTD_DISABLE_ASM=1` stands in for the assembly file, and the macros below for the `config.h` that libfst's own build generates and `fstapi.c` includes.

| Library | Path | Copied | Built |
|---|---|---|---|
| `zstd/` | `lib/decompress/huf_decompress_amd64.S` | no | no |
| `zstd/` | `lib/common/pool.c` | yes | no |
| `zstd/` | `lib/common/threading.c` | yes | no |
| `zstd/` | `lib/compress/zstdmt_compress.c` | yes | no |
| `libfst/` | generated `config.h` | no | no |

## Macros defined

Library is as above, and Macro each macro [`CMakeLists.txt`](CMakeLists.txt) defines for it.

| Library | Macro |
|---|---|
| `zstd/` | `ZSTD_LEGACY_SUPPORT=0` |
| `zstd/` | `ZSTD_DISABLE_ASM=1` |
| `zlib/` | `_LARGEFILE64_SOURCE=1` |
| `libfst/` | `HAVE_LIBPTHREAD` |
| `libfst/` | `FST_WRITER_PARALLEL` |
| `libfst/` | `HAVE_FSEEKO` |
| `libfst/` | `HAVE_REALPATH` |

## Local changes

Library is as above, Change the edit to the upstream code, and Why the reason for it. The other libraries are as upstream ships them.

| Library | Change | Why |
|---|---|---|
| `sha256/` | `sha256_transform` casts `data[j]` to `uint32_t` before `<< 24` | UBSan in `test_hashes` found the upstream shift of a promoted `int` undefined for bytes from 0x80 |
| `sha256/` | `sha256.c` includes `<stdint.h>` | the cast needs `uint32_t` |
