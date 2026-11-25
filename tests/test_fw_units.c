// SPDX-License-Identifier: Apache-2.0
// The capture firmware's portable pieces: argument parsing, the project
// clock plan and the PIO program words (firmware/core).
#include "harness.h"
#include "fw_args.h"
#include "fw_clock.h"
#include "fw_pio.h"

// ---- arguments ---------------------------------------------------------------

static const char *const EDGES[] = { "falling", "rising", NULL };
static const fw_arg_spec_t SPECS[] = {
    { "buf_words", 1, 1000, NULL },
    { "edge", 0, 0, EDGES },
    { "delta", -1, 1, NULL },
};
static int64_t vals[3];
static char err[160];

static int parse(const char *text) { return fw_args_parse(text, SPECS, 3, vals, err, sizeof err); }

TEST(args_all_keys_any_order) {
    ASSERT_EQ_U(parse("  edge=rising\tdelta=-1 buf_words=1000 "), 0);
    ASSERT_EQ_U(vals[0], 1000);
    ASSERT_EQ_U(vals[1], 1);
    ASSERT_TRUE(vals[2] == -1);
}

TEST(args_missing_unknown_twice_and_range) {
    ASSERT_TRUE(parse("buf_words=1 edge=falling") == -1);
    ASSERT_TRUE(strcmp(err, "missing key 'delta'") == 0);
    ASSERT_TRUE(parse("buf_words=1 edge=falling delta=0 colour=red") == -1);
    ASSERT_TRUE(strcmp(err, "unknown key 'colour'") == 0);
    ASSERT_TRUE(parse("buf_words=1 buf_words=2 edge=falling delta=0") == -1);
    ASSERT_TRUE(strcmp(err, "key 'buf_words' given twice") == 0);
    ASSERT_TRUE(parse("buf_words=0 edge=falling delta=0") == -1);
    ASSERT_TRUE(strcmp(err, "buf_words must be an integer 1..1000, got '0'") == 0);
    ASSERT_TRUE(parse("buf_words=1 edge=both delta=0") == -1);
    ASSERT_TRUE(strcmp(err, "edge must be falling|rising, got 'both'") == 0);
}

TEST(args_rejects_malformed_values) {
    const char *bad[] = { "buf_words= edge=falling delta=0", "buf_words=+1 edge=falling delta=0",
                          "buf_words=1x edge=falling delta=0", "buf_words=0x10 edge=falling delta=0",
                          "buf_words=1 edge=falling delta=--1", "=1 buf_words=1 edge=falling delta=0",
                          "buf_words edge=falling delta=0", "buf_words=1234567890123 edge=falling delta=0" };
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) ASSERT_TRUE(parse(bad[i]) == -1);
}

TEST(args_no_specs_means_no_arguments) {
    ASSERT_EQ_U(fw_args_parse("   ", NULL, 0, NULL, err, sizeof err), 0);
    ASSERT_TRUE(fw_args_parse("x=1", NULL, 0, NULL, err, sizeof err) == -1);
}

// ---- project clock -------------------------------------------------------------

static fw_clock_plan_t plan_of(uint32_t hz) {
    fw_clock_plan_t p;
    memset(&p, 0, sizeof p);
    const char *why = "";
    if (fw_clock_plan(hz, &p, &why)) p.sysclk_hz = 0;
    return p;
}

// What the stock firmware picked, measured on fpga-4 (frame_rp2.py,
// PHASE-LOCKED SAMPLING): 4x at 25 and 31.5 MHz, 2x at 36 and 40 MHz, and
// 28.322 MHz given as 28.25 = 113 / 4.
TEST(clock_matches_the_stock_firmware_measurements) {
    fw_clock_plan_t p = plan_of(25000000);
    ASSERT_EQ_U(p.sysclk_hz, 100000000);
    ASSERT_EQ_U(p.div16, 16);
    ASSERT_EQ_U(p.top, 3);
    ASSERT_EQ_U(p.level, 2);
    ASSERT_EQ_U(p.actual_hz, 25000000);
    ASSERT_EQ_U(plan_of(31500000).sysclk_hz, 126000000);
    ASSERT_EQ_U(plan_of(36000000).sysclk_hz, 72000000);
    ASSERT_EQ_U(plan_of(36000000).top, 1);
    ASSERT_EQ_U(plan_of(36000000).level, 1);
    ASSERT_EQ_U(plan_of(40000000).sysclk_hz, 80000000);
    p = plan_of(28322000);
    ASSERT_EQ_U(p.sysclk_hz, 113000000);
    ASSERT_EQ_U(p.top, 3);
    ASSERT_EQ_U(p.actual_hz, 28250000);
}

TEST(clock_stream_rates) {
    fw_clock_plan_t p = plan_of(100000);
    ASSERT_EQ_U(p.sysclk_hz, 133000000);
    ASSERT_EQ_U(p.top, 1329);
    ASSERT_EQ_U(p.level, 665);
    ASSERT_EQ_U(p.actual_hz, 100000);
    ASSERT_EQ_U(plan_of(750000).sysclk_hz, 132000000);
    // Fast clocks raise the cap to 2 x freq, as clock_project_PWM does.
    p = plan_of(70000000);
    ASSERT_EQ_U(p.sysclk_hz, 140000000);
    ASSERT_EQ_U(p.top, 1);
    // A slow clock needs the PWM divider.
    p = plan_of(3);
    ASSERT_EQ_U(p.sysclk_hz, 50000000);
    ASSERT_TRUE(p.div16 > 16 && p.div16 < 4096);
    ASSERT_EQ_U(p.actual_hz, 3);
}

TEST(clock_out_of_range) {
    ASSERT_EQ_U(plan_of(2).sysclk_hz, 0);
    ASSERT_EQ_U(plan_of(126000000).sysclk_hz, 0);
}

TEST(clock_period_is_what_frame_capture_accepts) {
    ASSERT_EQ_U(fw_clock_period(100000000, 25000000), 4);
    ASSERT_EQ_U(fw_clock_period(72000000, 36000000), 2);
    ASSERT_EQ_U(fw_clock_period(113000000, 28322000), 4);
    ASSERT_EQ_U(fw_clock_period(133000000, 100000), 0);    // > 32: stream mode's job
    ASSERT_EQ_U(fw_clock_period(150000000, 100000000), 0);  // 1.5: not whole
    ASSERT_EQ_U(fw_clock_period(100000000, 0), 0);
}

// ---- PIO programs -----------------------------------------------------------------

// The words MicroPython v1.24.0's rp2.asm_pio assembles for capture_rp2.py
// make_sampler(16, 8, 32, rising) and frame_rp2.py make_trigger(16, 40, 36,
// hs_act=0, vs_act=1, 8, 32, clk_act=1, delay=3, period=4) on the RP2350
// board (python/tests/test_fw_sim.py checks the same through the emulator).
TEST(pio_sampler_words) {
    fw_pio_program_t p;
    fw_pio_sampler(&p, 16, 8, 32, 0);
    const uint16_t falling[3] = { 0x2090, 0x2010, 0x4008 };
    ASSERT_EQ_U(p.length, 3);
    ASSERT_EQ_MEM(p.words, falling, sizeof falling);
    ASSERT_EQ_U(p.wrap_target, 0);
    ASSERT_EQ_U(p.wrap, 2);
    ASSERT_EQ_U(p.shiftctrl, 0x80010000u);
    ASSERT_EQ_U(p.execctrl, 0x00002000u);
    fw_pio_sampler(&p, 16, 8, 32, 1);
    const uint16_t rising[3] = { 0x2010, 0x2090, 0x4008 };
    ASSERT_EQ_MEM(p.words, rising, sizeof rising);
}

TEST(pio_frame_words) {
    fw_pio_program_t p;
    fw_pio_frame(&p, 16, 40, 36, 0, 1, 8, 32, 1, 3, 4);
    const uint16_t want[12] = { 0x80a0, 0xa027, 0x2004, 0x2084, 0x0046, 0x0009,
                                0x2088, 0x2008, 0x0004, 0x2010, 0x2390, 0x4308 };
    ASSERT_EQ_U(p.length, 12);
    ASSERT_EQ_MEM(p.words, want, sizeof want);
    ASSERT_EQ_U(p.wrap_target, 11);
    ASSERT_EQ_U(p.wrap, 11);
    ASSERT_EQ_U(p.shiftctrl, 0x00010000u);
    ASSERT_EQ_U(p.execctrl, 0x0000b580u);
    // Falling edge, no delay, two system clocks a sample, active-low syncs
    // both: every operand lands in its own field.
    fw_pio_frame(&p, 16, 40, 36, 1, 0, 8, 32, 0, 0, 2);
    ASSERT_EQ_U(p.words[2], 0x2084);   // wait 1 gpio 36: vsync idles high
    ASSERT_EQ_U(p.words[3], 0x2004);
    ASSERT_EQ_U(p.words[6], 0x2008);   // wait 0 gpio 40: hsync idles low
    ASSERT_EQ_U(p.words[7], 0x2088);
    ASSERT_EQ_U(p.words[9], 0x2090);
    ASSERT_EQ_U(p.words[10], 0x2010);
    ASSERT_EQ_U(p.words[11], 0x4108);  // in pins 8 [1]
}

int main(void) {
    RUN(args_all_keys_any_order);
    RUN(args_missing_unknown_twice_and_range);
    RUN(args_rejects_malformed_values);
    RUN(args_no_specs_means_no_arguments);
    RUN(clock_matches_the_stock_firmware_measurements);
    RUN(clock_stream_rates);
    RUN(clock_out_of_range);
    RUN(clock_period_is_what_frame_capture_accepts);
    RUN(pio_sampler_words);
    RUN(pio_frame_words);
    RUN_TESTS_END();
}
