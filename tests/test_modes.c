// SPDX-License-Identifier: Apache-2.0
#include "harness.h"
#include "vgacap/frame.h"
TEST(table_has_640x480) { size_t n; const vgaframe_mode_t *m = vgaframe_modes(&n); ASSERT_TRUE(n >= 7); ASSERT_TRUE(strcmp(m[0].name, "640x480@60") == 0); }
TEST(match_640x480) { const vgaframe_mode_t *m = vgaframe_mode_match(800, 525); ASSERT_TRUE(m != NULL); ASSERT_EQ_U(m->h_active, 640); ASSERT_EQ_U(m->v_back, 33); }
TEST(match_800x600) { const vgaframe_mode_t *m = vgaframe_mode_match(1056, 628); ASSERT_TRUE(m != NULL); ASSERT_EQ_U(m->h_sync_positive, 1); }
TEST(no_match) { ASSERT_TRUE(vgaframe_mode_match(801, 525) == NULL); }
TEST(match_cpl_640x480) { const vgaframe_mode_t *m = vgaframe_mode_match_cpl(800); ASSERT_TRUE(m != NULL); ASSERT_TRUE(strcmp(m->name, "640x480@60") == 0); }
TEST(match_cpl_no_match) { ASSERT_TRUE(vgaframe_mode_match_cpl(801) == NULL); }

static const char *const base_names[7] = { "640x480@60", "640x480@72", "640x480@75", "720x400@70",
                                           "800x600@56", "800x600@60", "1024x768@60" };

TEST(table_has_12) {
    size_t n; const vgaframe_mode_t *m = vgaframe_modes(&n);
    ASSERT_EQ_U(n, 12);
    for (size_t i = 0; i < 7; i++) { ASSERT_TRUE(strcmp(m[i].name, base_names[i]) == 0); ASSERT_EQ_U(m[i].extended, 0); }
    static const char *const added[5] = { "800x600@72", "800x600@75", "1024x768@70", "1280x720@60", "1280x1024@60" };
    for (size_t i = 0; i < 5; i++) { ASSERT_TRUE(strcmp(m[7 + i].name, added[i]) == 0); ASSERT_EQ_U(m[7 + i].extended, 1); }
}

// tb.cpp:45-50: line_px, lines, hpulse, hback, vpulse, vback.
TEST(added_totals_match_tb_cpp) {
    static const struct { unsigned cpl, lpf, hpulse, hback, vpulse, vback; } tb[5] = {
        { 1040, 666, 120,  64, 6, 23 }, { 1056, 625,  80, 160, 3, 21 }, { 1328, 806, 136, 144, 6, 29 },
        { 1650, 750,  40, 220, 5, 20 }, { 1688, 1066, 112, 248, 3, 38 },
    };
    size_t n; const vgaframe_mode_t *m = vgaframe_modes(&n);
    ASSERT_EQ_U(n, 12);
    for (size_t i = 0; i < 5; i++) {
        const vgaframe_mode_t *e = &m[7 + i];
        ASSERT_EQ_U(vgaframe_mode_line_px(e), tb[i].cpl);
        ASSERT_EQ_U(vgaframe_mode_frame_lines(e), tb[i].lpf);
        ASSERT_EQ_U(e->h_sync, tb[i].hpulse); ASSERT_EQ_U(e->h_back, tb[i].hback);
        ASSERT_EQ_U(e->v_sync, tb[i].vpulse); ASSERT_EQ_U(e->v_back, tb[i].vback);
    }
}

TEST(pairs_distinct) {
    size_t n; const vgaframe_mode_t *m = vgaframe_modes(&n);
    for (size_t i = 0; i < n; i++) for (size_t j = i + 1; j < n; j++)
        ASSERT_TRUE(vgaframe_mode_line_px(&m[i]) != vgaframe_mode_line_px(&m[j])
                    || vgaframe_mode_frame_lines(&m[i]) != vgaframe_mode_frame_lines(&m[j]));
}

TEST(match_cpl_unchanged) {
    size_t n; const vgaframe_mode_t *m = vgaframe_modes(&n);
    for (size_t i = 0; i < n; i++) if (!m[i].extended)
        ASSERT_TRUE(vgaframe_mode_match_cpl(vgaframe_mode_line_px(&m[i])) == &m[i]);
    const vgaframe_mode_t *r = vgaframe_mode_match_cpl(1056);
    ASSERT_TRUE(r != NULL); ASSERT_TRUE(strcmp(r->name, "800x600@60") == 0);
    ASSERT_TRUE(vgaframe_mode_match_cpl(1040) == NULL);   // only an extended mode has 1040
}

TEST(match_sees_added) {
    const vgaframe_mode_t *m = vgaframe_mode_match(1040, 666);
    ASSERT_TRUE(m != NULL); ASSERT_TRUE(strcmp(m->name, "800x600@72") == 0);
}

static int dump(void) {
    size_t n; const vgaframe_mode_t *m = vgaframe_modes(&n);
    for (size_t i = 0; i < n; i++)
        printf("%s,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u\n", m[i].name, m[i].h_active, m[i].h_front, m[i].h_sync, m[i].h_back,
               m[i].v_active, m[i].v_front, m[i].v_sync, m[i].v_back, m[i].h_sync_positive, m[i].v_sync_positive, m[i].extended);
    return 0;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--dump") == 0) return dump();
    RUN(table_has_640x480); RUN(match_640x480); RUN(match_800x600); RUN(no_match); RUN(match_cpl_640x480); RUN(match_cpl_no_match);
    RUN(table_has_12); RUN(added_totals_match_tb_cpp); RUN(pairs_distinct); RUN(match_cpl_unchanged); RUN(match_sees_added);
    RUN_TESTS_END();
}
