// SPDX-License-Identifier: Apache-2.0
// The MicroPython module's host link (hal_mpy.c), for its binding
// (modvgacap.c).
#ifndef VGACAP_HAL_MPY_H
#define VGACAP_HAL_MPY_H

#include "py/obj.h"

// Before a capture: where its chunks go (a stream) and where stop bytes
// come from (a stream, or None for no stop but the capture's own limits).
void vgacap_link_begin(mp_obj_t out, mp_obj_t stop);
// After it: the first exception MicroPython raised during the capture (a
// failed write, a scheduled callback that raised), or MP_OBJ_NULL.
mp_obj_t vgacap_link_end(void);

#endif
