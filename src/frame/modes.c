// SPDX-License-Identifier: Apache-2.0
#include "vgacap/frame.h"

static const vgaframe_mode_t table[] = {
    { "640x480@60",  640, 16,  96,  48, 480, 10, 2, 33, 0, 0 },
    { "640x480@72",  640, 24,  40, 128, 480,  9, 3, 28, 0, 0 },
    { "640x480@75",  640, 16,  64, 120, 480,  1, 3, 16, 0, 0 },
    { "720x400@70",  720, 18, 108,  54, 400, 12, 2, 35, 0, 1 },
    { "800x600@56",  800, 24,  72, 128, 600,  1, 2, 22, 1, 1 },
    { "800x600@60",  800, 40, 128,  88, 600,  1, 4, 23, 1, 1 },
    { "1024x768@60", 1024, 24, 136, 160, 768,  3, 6, 29, 0, 0 },
};

const vgaframe_mode_t *vgaframe_modes(size_t *count) {
    if (count) *count = sizeof table / sizeof table[0];
    return table;
}

const vgaframe_mode_t *vgaframe_mode_match(uint32_t clocks_per_line, uint32_t lines_per_frame) {
    for (size_t i = 0; i < sizeof table / sizeof table[0]; i++) {
        const vgaframe_mode_t *m = &table[i];
        uint32_t cpl = (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
        uint32_t lpf = (uint32_t)m->v_active + m->v_front + m->v_sync + m->v_back;
        if (cpl == clocks_per_line && lpf == lines_per_frame) return m;
    }
    return NULL;
}
