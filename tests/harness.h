// SPDX-License-Identifier: Apache-2.0
// Minimal test harness: no dependencies, one executable per test file.
#ifndef VGACAP_TEST_HARNESS_H
#define VGACAP_TEST_HARNESS_H
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static int harness_failures = 0;
static int harness_tests = 0;

#define ASSERT_TRUE(x) do { if (!(x)) { harness_failures++; \
    fprintf(stderr, "  FAIL %s:%d: %s\n", __FILE__, __LINE__, #x); return; } } while (0)
#define ASSERT_EQ_U(a, b) do { unsigned long long _a = (unsigned long long)(a), _b = (unsigned long long)(b); \
    if (_a != _b) { harness_failures++; \
    fprintf(stderr, "  FAIL %s:%d: %s == %llu, expected %s == %llu\n", __FILE__, __LINE__, #a, _a, #b, _b); return; } } while (0)
#define ASSERT_EQ_MEM(a, b, n) do { if (memcmp((a), (b), (n)) != 0) { harness_failures++; \
    fprintf(stderr, "  FAIL %s:%d: memory differs: %s vs %s (%zu bytes)\n", __FILE__, __LINE__, #a, #b, (size_t)(n)); return; } } while (0)

#define TEST(name) static void name(void)
#define RUN(name) do { harness_tests++; fprintf(stderr, "RUN  %s\n", #name); name(); } while (0)
#define RUN_TESTS_END() do { fprintf(stderr, "%d tests, %d failures\n", harness_tests, harness_failures); \
    return harness_failures ? 1 : 0; } while (0)
#endif
