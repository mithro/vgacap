// SPDX-License-Identifier: Apache-2.0
// The trace reader (stub: interface only).
#include "vgacap/trace.h"
#include <stdio.h>

vgacap_trace_reader_t *vgacap_trace_open(const char *path, char *err, size_t errlen) {
    snprintf(err, errlen, "%s: not implemented", path);
    return NULL;
}
const vgacap_trace_info_t *vgacap_trace_info(const vgacap_trace_reader_t *r) { (void)r; return NULL; }
int vgacap_trace_read(vgacap_trace_reader_t *r, uint64_t from, uint64_t to,
                      vgacap_trace_run_fn fn, void *user, char *err, size_t errlen) {
    (void)r; (void)from; (void)to; (void)fn; (void)user;
    snprintf(err, errlen, "not implemented");
    return -1;
}
int vgacap_trace_block_data(vgacap_trace_reader_t *r, uint32_t i, const uint8_t **data, size_t *len,
                            char *err, size_t errlen) {
    (void)r; (void)i; (void)data; (void)len;
    snprintf(err, errlen, "not implemented");
    return -1;
}
void vgacap_trace_close(vgacap_trace_reader_t *r) { (void)r; }
