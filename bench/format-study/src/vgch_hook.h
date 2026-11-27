// SPDX-License-Identifier: Apache-2.0
// Force-included (-include) when the study compiles vgacap's own
// src/trace/trace_write.c, unchanged, into its vgch2 candidate. It renames
// the writer's one ZSTD_compress2 call so the study can
//   - skip compression (timing the simulation thread alone, as for every
//     other candidate), or
//   - see each block's uncompressed bytes (the compressor matrix),
// while every other line of the writer is the production code.
#ifndef FS_VGCH_HOOK_H
#define FS_VGCH_HOOK_H
#define ZSTD_compress2 fs_hook_ZSTD_compress2
#endif
