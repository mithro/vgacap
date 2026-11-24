// SPDX-License-Identifier: Apache-2.0
// Stub (RED): the trace writer is not implemented yet.
#include "vgacap/trace.h"
#include <stdio.h>

vgacap_trace_writer_t *vgacap_trace_create(const char *path, const vgacap_header_t *h,
                                           const char *open_meta, int zstd_level,
                                           char *err, size_t errlen) {
    (void)path; (void)h; (void)open_meta; (void)zstd_level;
    snprintf(err, errlen, "not implemented");
    return NULL;
}
int vgacap_trace_slow(vgacap_trace_writer_t *t, uint64_t v) { (void)t; (void)v; return -1; }
int vgacap_trace_finish(vgacap_trace_writer_t *t, const char *close_meta, char *err, size_t errlen) {
    (void)t; (void)close_meta; snprintf(err, errlen, "not implemented"); return -1;
}
void vgacap_trace_abandon(vgacap_trace_writer_t *t) { (void)t; }
