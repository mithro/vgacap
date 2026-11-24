// SPDX-License-Identifier: Apache-2.0
// Test-only helper for python/tests/test_timebase.py: exposes time.c's static
// 128-bit helpers by including the source. Reads commands on stdin:
//   mul A B       -> "HI LO"
//   div HI LO D   -> "QHI QLO REM"
#include <stdio.h>
#include <string.h>
#include <inttypes.h>
#include "../src/stream/time.c"

int main(void) {
    char cmd[8];
    while (scanf("%7s", cmd) == 1) {
        if (strcmp(cmd, "mul") == 0) {
            uint64_t a, b, hi, lo;
            if (scanf("%" SCNu64 " %" SCNu64, &a, &b) != 2) return 2;
            mul64x64(a, b, &hi, &lo);
            printf("%" PRIu64 " %" PRIu64 "\n", hi, lo);
        } else if (strcmp(cmd, "div") == 0) {
            uint64_t hi, lo, d, qhi, qlo, rem;
            if (scanf("%" SCNu64 " %" SCNu64 " %" SCNu64, &hi, &lo, &d) != 3) return 2;
            divmod128(hi, lo, d, &qhi, &qlo, &rem);
            printf("%" PRIu64 " %" PRIu64 " %" PRIu64 "\n", qhi, qlo, rem);
        } else return 2;
    }
    return 0;
}
