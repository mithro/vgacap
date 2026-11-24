// SPDX-License-Identifier: Apache-2.0
// STUB (RED): the frame clock, not implemented yet.
#include "vgacap/sample.h"
#include <string.h>
void vgafc_init(vgafc_t *fc, const vgacal_t *c) { (void)c; memset(fc, 0, sizeof *fc); }
void vgafc_push(vgafc_t *fc, uint8_t uo_out, uint64_t first, uint64_t clocks) {
    (void)fc; (void)uo_out; (void)first; (void)clocks;
}
void vgafc_free(vgafc_t *fc) { (void)fc; }
