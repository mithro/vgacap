// SPDX-License-Identifier: Apache-2.0
// Transforms and compressors of the study's background part, and the
// container its files use (bg.c writes, rd.c reads).
#ifndef FS_CODEC_H
#define FS_CODEC_H
#include <stddef.h>
#include <stdint.h>

// ---- compressors -------------------------------------------------------------------
enum {
    C_NONE, C_ZSTD_M5, C_ZSTD_M1, C_ZSTD_1, C_ZSTD_3, C_ZSTD_9, C_ZSTD_1_LONG, C_LZ4, C_LZ4HC_9, C_DEFLATE_1,
    C_DEFLATE_6, C_ZSTD_1_NOCHK, C_COUNT
};
extern const char *const fs_comp_name[C_COUNT];
int fs_comp_by_name(const char *name);
// Compresses src[0, n) into dst (fs_comp_bound(n) bytes). Returns the size, or 0 on error.
size_t fs_compress(int comp, const uint8_t *src, size_t n, uint8_t *dst, size_t cap);
size_t fs_comp_bound(size_t n);
// Decompresses into dst[0, n) exactly. Returns 0, or -1.
int fs_decompress(int comp, const uint8_t *src, size_t csize, uint8_t *dst, size_t n);

// ---- transforms of the raw candidate's blocks (two raw images a clock) ---------------
enum { T_PACK6, T_XOR6, T_BSHUF, T_XBSHUF, T_XBITSHUF, T_PACK6CLK, T_COUNT, T_IDENTITY = 100 };
extern const char *const fs_transform_name[T_COUNT];
int fs_transform_by_name(const char *name);
// raw[0, n) -> streams. Returns the number of streams (1 or 7); lens set.
// Each out[i] must hold 8 * n + 64 bytes. Counts clock-bit errors (a sample
// whose clk byte is not first_sample + i's parity) into *clk_errors.
int fs_transform(int t, const uint64_t *raw, uint64_t n, uint64_t first_sample, uint8_t **out, size_t *lens,
                 uint64_t *clk_errors);
// The inverse, for the streams of one port (-1: every port) into raw
// images with clk regenerated. For one port only, the other bytes are 0.
int fs_untransform(int t, uint8_t *const *in, uint64_t n, uint64_t first_sample, int port, uint64_t *raw);

// ---- the container ("STDY") ---------------------------------------------------------------
// file:   "FSTUDY01" u8 writer u8 transform u8 comp u8 nstreams u32 reserved, then blocks, then
//         an index and a footer.
// block:  u32 "BLK1", u64 first_sample, u64 nsamples, then per stream u32 rawlen, u32 complen,
//         then the compressed streams in order (zstd frames carry XXH64 checksums;
//         deflate its Adler-32; LZ4 blocks nothing).
// index:  per block u64 offset, u64 first_sample; footer u64 index_offset, u32 nblocks, "FIDX".
#define FS_FILE_MAGIC "FSTUDY01"
#define FS_BLOCK_MAGIC 0x314b4c42u   // "BLK1"
#define FS_INDEX_MAGIC 0x58444946u   // "FIDX"

#endif
