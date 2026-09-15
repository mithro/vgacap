// SPDX-License-Identifier: Apache-2.0
#include "synth.h"

size_t synth_frame(const vgaframe_mode_t *m, uint8_t (*pixel)(void *, uint16_t, uint16_t),
                   void *user, uint32_t *out, size_t out_len) {
    uint32_t cpl = (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
    uint32_t lpf = (uint32_t)m->v_active + m->v_front + m->v_sync + m->v_back;
    if ((size_t)cpl * lpf > out_len) return 0;
    size_t k = 0;
    for (uint32_t line = 0; line < lpf; line++) {
        // line 0 starts at the vsync pulse; active lines follow v_sync + v_back
        int v_pulse = line < m->v_sync;
        int y_active = line >= (uint32_t)m->v_sync + m->v_back &&
                       line < (uint32_t)m->v_sync + m->v_back + m->v_active;
        uint16_t y = (uint16_t)(line - m->v_sync - m->v_back);
        for (uint32_t clk = 0; clk < cpl; clk++) {
            int h_pulse = clk < m->h_sync;
            int x_active = clk >= (uint32_t)m->h_sync + m->h_back &&
                           clk < (uint32_t)m->h_sync + m->h_back + m->h_active;
            uint16_t x = (uint16_t)(clk - m->h_sync - m->h_back);
            uint8_t hs = (uint8_t)(m->h_sync_positive ? h_pulse : !h_pulse);
            uint8_t vs = (uint8_t)(m->v_sync_positive ? v_pulse : !v_pulse);
            uint8_t c = (uint8_t)((x_active && y_active) ? pixel(user, x, y) : 0);   // c = rr gg bb
            uint8_t r1 = (c >> 5) & 1, r0 = (c >> 4) & 1, g1 = (c >> 3) & 1;
            uint8_t g0 = (c >> 2) & 1, b1 = (c >> 1) & 1, b0 = c & 1;
            out[k++] = (uint32_t)((hs << 7) | (b0 << 6) | (g0 << 5) | (r0 << 4) |
                                  (vs << 3) | (b1 << 2) | (g1 << 1) | r1);
        }
    }
    return k;
}
