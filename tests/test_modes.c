// SPDX-License-Identifier: Apache-2.0
#include "harness.h"
#include "vgacap/frame.h"
TEST(table_has_640x480) { size_t n; const vgaframe_mode_t *m = vgaframe_modes(&n); ASSERT_TRUE(n >= 7); ASSERT_TRUE(strcmp(m[0].name, "640x480@60") == 0); }
TEST(match_640x480) { const vgaframe_mode_t *m = vgaframe_mode_match(800, 525); ASSERT_TRUE(m != NULL); ASSERT_EQ_U(m->h_active, 640); ASSERT_EQ_U(m->v_back, 33); }
TEST(match_800x600) { const vgaframe_mode_t *m = vgaframe_mode_match(1056, 628); ASSERT_TRUE(m != NULL); ASSERT_EQ_U(m->h_sync_positive, 1); }
TEST(no_match) { ASSERT_TRUE(vgaframe_mode_match(801, 525) == NULL); }
int main(void) { RUN(table_has_640x480); RUN(match_640x480); RUN(match_800x600); RUN(no_match); RUN_TESTS_END(); }
