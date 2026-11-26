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

Library is the directory under `third_party/`, Upstream the repository it comes from, and Version the tag or commit taken. License is the license used, and License file the copy kept beside the code.

| Library | Upstream | Version | License | License file |
|---|---|---|---|---|
| `zstd/` | [facebook/zstd](https://github.com/facebook/zstd) | tag `v1.5.7` (`f8745da6ff1ad1e7bab384bd1f9d742439278e99`) | BSD-3-Clause (the BSD half of its dual license) | `zstd/LICENSE` |
| `zlib/` | [madler/zlib](https://github.com/madler/zlib) | tag `v1.3.1` (`51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf`) | zlib | `zlib/LICENSE` |
| `libfst/` | [gtkwave/libfst](https://github.com/gtkwave/libfst) | commit `b2681f927a092a21b42ba1f3b2a9af831deaf58d` | MIT | `libfst/LICENSE` |
| `libfst/lz4.c` and `lz4.h` | bundled in libfst | as libfst | BSD-2-Clause | `libfst/LICENSE.lz4` |
| `sha256/` | [B-Con/crypto-algorithms](https://github.com/B-Con/crypto-algorithms) | commit `cfbde48414baacf51fc7c74f275190881f037d32` | public domain | `sha256/LICENSE` |

## Files taken

Library is as above, and Files the upstream files copied in, from the upstream root.

| Library | Files |
|---|---|
| `zstd/` | `lib/zstd.h` · `lib/zstd_errors.h` · `lib/zdict.h` · `lib/common/*` · `lib/compress/*` · `lib/decompress/*.c` · `lib/decompress/*.h` · `contrib/seekable_format/zstd_seekable.h` · `zstdseek_compress.c` · `zstdseek_decompress.c` |
| `zlib/` | the 15 `.c` files that `CMakeLists.txt` lists · every top-level `.h` file · `LICENSE` |
| `libfst/` | `src/fstapi.c` · `fstapi.h` · `fastlz.c` · `fastlz.h` · `lz4.c` · `lz4.h` |
| `sha256/` | `sha256.c` · `sha256.h` |

## How each is built

Library is as above, Left out what the build omits, and Defined the macros it sets.

| Library | Left out | Defined |
|---|---|---|
| `zstd/` | `huf_decompress_amd64.S` · `pool.c` · `threading.c` · `zstdmt_compress.c` | `ZSTD_LEGACY_SUPPORT=0` · `ZSTD_DISABLE_ASM=1` |
| `zlib/` | none | `_LARGEFILE64_SOURCE=1` |
| `libfst/` | `config.h` | `HAVE_LIBPTHREAD` · `FST_WRITER_PARALLEL` · `HAVE_FSEEKO` · `HAVE_REALPATH` |
| `sha256/` | none | none |

## Local changes

Library is as above, Change the edit to the upstream code, and Why the reason for it. The other libraries are as upstream ships them.

| Library | Change | Why |
|---|---|---|
| `sha256/` | `sha256_transform` casts `data[j]` to `uint32_t` before `<< 24` | upstream shifts a promoted `int` and UBSan in `test_hashes` found it undefined for bytes of 0x80 and above |
| `sha256/` | `sha256.c` includes `<stdint.h>` | the cast needs `uint32_t` |
