// SPDX-License-Identifier: Apache-2.0
#ifndef FS_VGCH_H
#define FS_VGCH_H
// The vgch2 candidate copies the source trace's VGCH header and META texts,
// so that its output can be compared byte for byte with the published file.
void fs_vgch_set_source(const char *trace_path);
extern int fs_hook_skip;
#endif
