// SPDX-License-Identifier: Apache-2.0
// RED stub: tb-v1 not implemented yet.
#include "vgacap/sample.h"
#include <stdlib.h>

struct tbv1 { vgasample_geometry_t geo; };

tbv1_t *tbv1_create(const vgacal_t *cal, const vgafc_t *fc, double clock_hz, double seconds,
                    vgasample_frame_fn fn, void *user) {
    (void)cal; (void)fc; (void)clock_hz; (void)seconds; (void)fn; (void)user;
    tbv1_t *s = calloc(1, sizeof *s);
    if (!s) abort();
    s->geo.status = "no-sync"; s->geo.mode = "unknown";
    return s;
}
const vgasample_geometry_t *tbv1_geometry(const tbv1_t *s) { return &s->geo; }
uint64_t tbv1_target_frames(const tbv1_t *s) { (void)s; return 0; }
uint64_t tbv1_clock_limit(const tbv1_t *s) { (void)s; return 0; }
uint64_t tbv1_frames(const tbv1_t *s) { (void)s; return 0; }
void tbv1_push(tbv1_t *s, uint8_t uo_out, uint64_t first, uint64_t clocks) {
    (void)s; (void)uo_out; (void)first; (void)clocks;
}
void tbv1_free(tbv1_t *s) { free(s); }
