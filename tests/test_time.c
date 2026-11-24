// SPDX-License-Identifier: Apache-2.0
#include "harness.h"
#include "vgacap/time.h"

TEST(time_vectors) {
    ASSERT_EQ_U(vgacap_sample_time_ps(1, 2, 25175000), 19861);
    ASSERT_EQ_U(vgacap_sample_time_ps(2, 2, 25175000), 39722);
    ASSERT_EQ_U(vgacap_sample_time_ps(3, 2, 25175000), 59583);
    ASSERT_EQ_U(vgacap_sample_time_ps(12000000000ULL, 2, 25175000), 238331678252234ULL);
    ASSERT_EQ_U(vgacap_sample_time_ps(12000000001ULL, 2, 25175000), 238331678272095ULL);
    ASSERT_EQ_U(vgacap_sample_time_ps(1, 1, 25175000), 39722);
    ASSERT_EQ_U(vgacap_sample_time_ps(3, 2, 48000000), 31250);
    ASSERT_EQ_U(vgacap_sample_time_ps(0, 2, 25175000), 0);
    ASSERT_EQ_U(vgacap_sample_time_ps(5, 2, 0), 0);
}

TEST(time_halves_round_up) {
    // s * hz = 4000 * 500000000 = 2e12, so n = 1 is exactly 0.5 ps (-> 1)
    // and n = 3 is exactly 1.5 ps (-> 2). d >= 2^32 also exercises the bit loop.
    ASSERT_EQ_U(vgacap_sample_time_ps(1, 4000, 500000000), 1);
    ASSERT_EQ_U(vgacap_sample_time_ps(3, 4000, 500000000), 2);
}

TEST(no_drift) {
    uint64_t prev = vgacap_sample_time_ps(0, 2, 25175000);
    for (uint64_t n = 0; n < 1000000; n++) {
        uint64_t next = vgacap_sample_time_ps(n + 1, 2, 25175000);
        uint64_t d = next - prev;
        ASSERT_TRUE(d == 19860 || d == 19861);
        prev = next;
    }
}

static void check_fmt(double v, const char *want) {
    char buf[40];
    int n = vgacap_fmt_double(buf, sizeof buf, v);
    ASSERT_TRUE(n >= 0);
    ASSERT_EQ_U(n, strlen(want));
    ASSERT_TRUE(strcmp(buf, want) == 0);
}

TEST(fmt_double_vectors) {
    check_fmt(1.0, "1");
    check_fmt(1.90625, "1.90625");
    check_fmt(832.0 / 800, "1.04");
    check_fmt(800.0 / 840, "0.9523809523809523");
    check_fmt(25175000.0, "25175000");
}

TEST(fmt_double_small_buffer) {
    char buf[4];
    ASSERT_TRUE(vgacap_fmt_double(buf, sizeof buf, 0.9523809523809523) < 0);
}

int main(void) {
    RUN(time_vectors);
    RUN(time_halves_round_up);
    RUN(no_drift);
    RUN(fmt_double_vectors);
    RUN(fmt_double_small_buffer);
    RUN_TESTS_END();
}
