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

// The generator (hvsync_generator.v lines 67-99) registers its outputs from the
// position *after* the edge:
//   hpos_n  = position the counters take at this edge (h_last ? 0 : hpos + 1)
//   vpos_n  = vpos, or vpos + 1 (wrapping at V_TOTAL) when h_last
//   vline_n = (hpos_n >= H_SYNC_START) ? (vpos_n + 1, wrapping at V_TOTAL) : vpos_n
//   h_pulse = H_SYNC_START <= hpos_n < H_SYNC_START + H_SYNC
//   v_pulse = V_ACTIVE + V_FRONT <= vline_n < V_ACTIVE + V_FRONT + V_SYNC
//   hsync <= H_POL ? h_pulse : ~h_pulse;  vsync <= V_POL ? v_pulse : ~v_pulse
//   hpos <= hpos_n; vpos <= vpos_n
// hsync, vsync, hpos and vpos are all registers loaded at the same edge, so
// the sample taken in the clock where the position registers read
// (hpos, vpos) carries hsync and vsync computed from that very position:
// hpos_n and vpos_n at the edge that loaded it are (hpos, vpos), and vline_n
// is the vline of (hpos, vpos) below. Hence H_SYNC_START = H_ACTIVE + H_FRONT:
// hsync starts on hpos == H_SYNC_START, and from there vline is one line
// ahead of vpos, so vsync starts on the same clock as hsync, on the line
// before vpos == V_ACTIVE + V_FRONT, and ends on the same clock hsync starts
// V_SYNC lines later.
size_t synth_frame_hvsync(const vgaframe_mode_t *m, uint8_t (*pixel)(void *, uint16_t, uint16_t),
                          void *user, uint32_t *out, size_t out_len) {
    uint32_t cpl = (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
    uint32_t lpf = (uint32_t)m->v_active + m->v_front + m->v_sync + m->v_back;
    if ((size_t)cpl * lpf > out_len) return 0;
    uint32_t h_sync_start = (uint32_t)m->h_active + m->h_front;
    size_t k = 0;
    for (uint32_t vpos = 0; vpos < lpf; vpos++) {
        for (uint32_t hpos = 0; hpos < cpl; hpos++) {
            uint32_t vline = hpos >= h_sync_start ? (vpos == lpf - 1 ? 0 : vpos + 1) : vpos;
            int h_pulse = hpos >= h_sync_start && hpos < h_sync_start + m->h_sync;
            int v_pulse = vline >= (uint32_t)m->v_active + m->v_front &&
                          vline < (uint32_t)m->v_active + m->v_front + m->v_sync;
            uint8_t hs = (uint8_t)(m->h_sync_positive ? h_pulse : !h_pulse);
            uint8_t vs = (uint8_t)(m->v_sync_positive ? v_pulse : !v_pulse);
            uint8_t c = (uint8_t)((hpos < m->h_active && vpos < m->v_active)
                                  ? pixel(user, (uint16_t)hpos, (uint16_t)vpos) : 0);
            uint8_t r1 = (c >> 5) & 1, r0 = (c >> 4) & 1, g1 = (c >> 3) & 1;
            uint8_t g0 = (c >> 2) & 1, b1 = (c >> 1) & 1, b0 = c & 1;
            out[k++] = (uint32_t)((hs << 7) | (b0 << 6) | (g0 << 5) | (r0 << 4) |
                                  (vs << 3) | (b1 << 2) | (g1 << 1) | r1);
        }
    }
    return k;
}
