// SPDX-License-Identifier: Apache-2.0
// centre-v1 (stub).
#include "vgacap/sample.h"
#include <stdlib.h>
#include <string.h>

struct centrev1 { vgasample_geometry_t geo; int dx, dy; };

centrev1_t *centrev1_create(int dx, int dy, vgasample_frame_fn fn, void *user) {
    (void)fn; (void)user;
    centrev1_t *s = calloc(1, sizeof *s);
    if (!s) abort();
    s->dx = dx; s->dy = dy;
    s->geo.status = "no-sync"; s->geo.mode = "unknown"; s->geo.cpp = 1.0;
    return s;
}
void centrev1_push(centrev1_t *s, uint8_t uo_out, uint64_t first, uint64_t clocks) {
    (void)s; (void)uo_out; (void)first; (void)clocks;
}
void centrev1_calibrated(centrev1_t *s, const vgacal_t *cal, const vgafc_t *fc) { (void)s; (void)cal; (void)fc; }
const vgasample_geometry_t *centrev1_geometry(const centrev1_t *s) { return &s->geo; }
void centrev1_set_raw_observer(centrev1_t *s, void (*fn)(void *user, const vgaframe_raw_t *r), void *user) {
    (void)s; (void)fn; (void)user;
}
void centrev1_finish(centrev1_t *s, centrev1_diag_t *d) { memset(d, 0, sizeof *d); d->dx = s->dx; d->dy = s->dy; }
void centrev1_free(centrev1_t *s) { free(s); }
