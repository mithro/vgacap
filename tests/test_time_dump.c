// SPDX-License-Identifier: Apache-2.0
// Test-only helper for python/tests/test_timebase.py. It includes time.c to
// reach the static 128-bit helpers. Reads commands on stdin, one result line each:
//   mul A B         -> "HI LO"
//   div HI LO D     -> "QHI QLO REM"   (divmod128, the production divide)
//   refdiv HI LO D  -> "QHI QLO REM"   (bit-loop reference divide, kept here)
//   time N S HZ     -> picoseconds     (vgacap_sample_time_ps)
//   fmt BITS        -> text            (vgacap_fmt_double of the double with
//                                       these 64 hex bits)
//   bench N S HZ K  -> ns per call, K calls of vgacap_sample_time_ps
#define _POSIX_C_SOURCE 199309L
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <inttypes.h>
#include "../src/stream/time.c"

// The original binary long division, the independent reference for divmod128.
static void refdiv128(uint64_t hi, uint64_t lo, uint64_t d,
                      uint64_t *qhi, uint64_t *qlo, uint64_t *rem) {
    uint64_t r = 0, qh = 0, ql = 0;
    for (int i = 127; i >= 0; i--) {
        uint64_t bit = i >= 64 ? (hi >> (i - 64)) & 1u : (lo >> i) & 1u;
        uint64_t carry = r >> 63;
        r = (r << 1) | bit;
        qh <<= 1; qh |= ql >> 63; ql <<= 1;
        if (carry || r >= d) { r -= d; ql |= 1u; }
    }
    *qhi = qh; *qlo = ql; *rem = r;
}

int main(void) {
    char cmd[8];
    while (scanf("%7s", cmd) == 1) {
        if (strcmp(cmd, "mul") == 0) {
            uint64_t a, b, hi, lo;
            if (scanf("%" SCNu64 " %" SCNu64, &a, &b) != 2) return 2;
            mul64x64(a, b, &hi, &lo);
            printf("%" PRIu64 " %" PRIu64 "\n", hi, lo);
        } else if (strcmp(cmd, "div") == 0 || strcmp(cmd, "refdiv") == 0) {
            uint64_t hi, lo, d, qhi, qlo, rem;
            if (scanf("%" SCNu64 " %" SCNu64 " %" SCNu64, &hi, &lo, &d) != 3) return 2;
            if (cmd[0] == 'r') refdiv128(hi, lo, d, &qhi, &qlo, &rem);
            else divmod128(hi, lo, d, &qhi, &qlo, &rem);
            printf("%" PRIu64 " %" PRIu64 " %" PRIu64 "\n", qhi, qlo, rem);
        } else if (strcmp(cmd, "time") == 0) {
            uint64_t n; uint32_t s, hz;
            if (scanf("%" SCNu64 " %" SCNu32 " %" SCNu32, &n, &s, &hz) != 3) return 2;
            printf("%" PRIu64 "\n", vgacap_sample_time_ps(n, s, hz));
        } else if (strcmp(cmd, "fmt") == 0) {
            uint64_t bits; double v; char buf[40];
            if (scanf("%" SCNx64, &bits) != 1) return 2;
            memcpy(&v, &bits, sizeof v);
            if (vgacap_fmt_double(buf, sizeof buf, v) < 0) return 3;
            printf("%s\n", buf);
        } else if (strcmp(cmd, "bench") == 0) {
            uint64_t n, k, acc = 0; uint32_t s, hz;
            if (scanf("%" SCNu64 " %" SCNu32 " %" SCNu32 " %" SCNu64, &n, &s, &hz, &k) != 4) return 2;
            struct timespec t0, t1;
            clock_gettime(CLOCK_MONOTONIC, &t0);
            for (uint64_t i = 0; i < k; i++) acc += vgacap_sample_time_ps(n + i, s, hz);
            clock_gettime(CLOCK_MONOTONIC, &t1);
            double ns = (double)(t1.tv_sec - t0.tv_sec) * 1e9 + (double)(t1.tv_nsec - t0.tv_nsec);
            printf("%.1f ns/call (checksum %" PRIu64 ")\n", ns / (double)k, acc);
        } else return 2;
    }
    return 0;
}
