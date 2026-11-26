// SPDX-License-Identifier: Apache-2.0
#include "vgacap/frame.h"

static const vgaframe_mode_t table[] = {
    { "640x480@60",  640, 16,  96,  48, 480, 10, 2, 33, 0, 0, 0 },
    { "640x480@72",  640, 24,  40, 128, 480,  9, 3, 28, 0, 0, 0 },
    { "640x480@75",  640, 16,  64, 120, 480,  1, 3, 16, 0, 0, 0 },
    { "720x400@70",  720, 18, 108,  54, 400, 12, 2, 35, 0, 1, 0 },
    { "800x600@56",  800, 24,  72, 128, 600,  1, 2, 22, 1, 1, 0 },
    { "800x600@60",  800, 40, 128,  88, 600,  1, 4, 23, 1, 1, 0 },
    { "1024x768@60", 1024, 24, 136, 160, 768,  3, 6, 29, 0, 0, 0 },
    // Added from the simulation testbench (ttvga/harness/tb.cpp). Clocks per
    // line is no longer unique with these (800x600@60 and @75 are both 1056),
    // so vgaframe_mode_match_cpl skips them.
    { "800x600@72",  800,  56, 120,  64, 600, 37, 6, 23, 1, 1, 1 },
    { "800x600@75",  800,  16,  80, 160, 600,  1, 3, 21, 1, 1, 1 },
    { "1024x768@70", 1024, 24, 136, 144, 768,  3, 6, 29, 0, 0, 1 },
    { "1280x720@60", 1280, 110, 40, 220, 720,  5, 5, 20, 1, 1, 1 },
    { "1280x1024@60", 1280, 48, 112, 248, 1024, 1, 3, 38, 1, 1, 1 },
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

const vgaframe_mode_t *vgaframe_mode_match_cpl(uint32_t clocks_per_line) {
    for (size_t i = 0; i < sizeof table / sizeof table[0]; i++) {
        const vgaframe_mode_t *m = &table[i];
        if (m->extended) continue;
        uint32_t cpl = (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
        if (cpl == clocks_per_line) return m;
    }
    return NULL;
}

uint32_t vgaframe_mode_line_px(const vgaframe_mode_t *m) {
    return (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
}

uint32_t vgaframe_mode_frame_lines(const vgaframe_mode_t *m) {
    return (uint32_t)m->v_active + m->v_front + m->v_sync + m->v_back;
}
