// SPDX-License-Identifier: Apache-2.0
// STUB (RED): the calibration port, not implemented yet.
#include "vgacap/sample.h"
#include <string.h>
void vgacal_init(vgacal_t *c, double clock_hz, double calib_seconds) {
    memset(c, 0, sizeof *c); c->clock_hz = clock_hz; c->calib_seconds = calib_seconds;
}
uint64_t vgacal_push(vgacal_t *c, uint8_t uo_out, uint8_t rst_n, uint64_t first, uint64_t clocks) {
    (void)c; (void)uo_out; (void)rst_n; (void)first; return clocks;
}
void vgacal_end_of_trace(vgacal_t *c, uint64_t clocks_in_trace) { (void)c; (void)clocks_in_trace; }
void vgacal_free(vgacal_t *c) { (void)c; }
