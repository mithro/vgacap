// SPDX-License-Identifier: Apache-2.0
// Host-only (not compiled by the board builds). Portable: no __int128.
#include "vgacap/time.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// 64x64 -> 128 multiply from 32-bit halves.
static void mul64x64(uint64_t a, uint64_t b, uint64_t *hi, uint64_t *lo) {
    uint64_t a0 = a & 0xFFFFFFFFu, a1 = a >> 32;
    uint64_t b0 = b & 0xFFFFFFFFu, b1 = b >> 32;
    uint64_t p00 = a0 * b0, p01 = a0 * b1, p10 = a1 * b0, p11 = a1 * b1;
    uint64_t mid = (p00 >> 32) + (p01 & 0xFFFFFFFFu) + (p10 & 0xFFFFFFFFu);
    *lo = (p00 & 0xFFFFFFFFu) | (mid << 32);
    *hi = p11 + (p01 >> 32) + (p10 >> 32) + (mid >> 32);
}

// (hi:lo) / d, d != 0: 128-bit quotient and remainder.
// High word 0: one native 64-bit divide. Otherwise d < 2^32 (every real clock:
// d = s * clock_hz is below 2^33 in practice): schoolbook long division in
// 32-bit digits, at most four 64/32 divides, leading zero digits skipped.
// Larger d falls back to the bit loop below.
static void divmod128(uint64_t hi, uint64_t lo, uint64_t d,
                      uint64_t *qhi, uint64_t *qlo, uint64_t *rem) {
    if (hi == 0) { *qhi = 0; *qlo = lo / d; *rem = lo % d; return; }
    if (d >> 32 == 0 && hi < d) {           // quotient fits 64 bits: two divides
        uint64_t c1 = (hi << 32) | (lo >> 32), q1 = c1 / d, r1 = c1 - q1 * d;
        uint64_t c0 = (r1 << 32) | (lo & 0xFFFFFFFFu), q0 = c0 / d;
        *qhi = 0; *qlo = (q1 << 32) | q0; *rem = c0 - q0 * d;
        return;
    }
    if (d >> 32 == 0) {
        uint64_t digit[4] = { hi >> 32, hi & 0xFFFFFFFFu, lo >> 32, lo & 0xFFFFFFFFu };
        uint64_t q[4], r = 0;
        for (int i = 0; i < 4; i++) {
            if (r == 0 && digit[i] == 0) { q[i] = 0; continue; }
            uint64_t cur = (r << 32) | digit[i];    // r < d < 2^32: fits
            q[i] = cur / d;                         // < 2^32 because r < d
            r = cur - q[i] * d;
        }
        *qhi = (q[0] << 32) | q[1]; *qlo = (q[2] << 32) | q[3]; *rem = r;
        return;
    }
    uint64_t r = 0, qh = 0, ql = 0;
    for (int i = 127; i >= 0; i--) {
        uint64_t bit = i >= 64 ? (hi >> (i - 64)) & 1u : (lo >> i) & 1u;
        uint64_t carry = r >> 63;           // r < d, so r*2+bit may need a 65th bit
        r = (r << 1) | bit;
        qh <<= 1; qh |= ql >> 63; ql <<= 1;
        if (carry || r >= d) { r -= d; ql |= 1u; }
    }
    *qhi = qh; *qlo = ql; *rem = r;
}

uint64_t vgacap_sample_time_ps(uint64_t n, uint32_t samples_per_clock, uint32_t clock_hz) {
    uint64_t d = (uint64_t)samples_per_clock * (uint64_t)clock_hz;
    if (d == 0) return 0;
    uint64_t hi, lo, qhi, qlo, r;
    mul64x64(n, 1000000000000ULL, &hi, &lo);
    divmod128(hi, lo, d, &qhi, &qlo, &r);
    if (qhi) return UINT64_MAX;
    // round half up: bump when 2r >= d, written as r >= d - r to avoid overflow
    if (r >= d - r) {
        if (qlo == UINT64_MAX) return UINT64_MAX;
        qlo++;
    }
    return qlo;
}

// The %.{p}g (p = 1..17) that reads back exactly: see time.h for the rule. %g switches to an
// exponent once the exponent reaches p, so 25175000.0 would print as
// "2.5175e+07" at p = 5; plain decimal is preferred: the first round-tripping p
// whose text has no exponent wins, and only if none has (huge or tiny values)
// the first round-tripping exponent form is used. python/vgacap/timebase.py
// applies the identical rule.
int vgacap_fmt_double(char *buf, size_t len, double v) {
    char tmp[40], first_exp[40];
    int first_exp_n = -1;
    for (int p = 1; p <= 17; p++) {
        int n = snprintf(tmp, sizeof tmp, "%.*g", p, v);
        if (n < 0 || (size_t)n >= sizeof tmp) continue;
        if (p != 17 && strtod(tmp, NULL) != v) continue;
        if (strchr(tmp, 'e') == NULL) { first_exp_n = -2; break; }
        if (first_exp_n < 0) { memcpy(first_exp, tmp, (size_t)n + 1); first_exp_n = n; }
    }
    const char *out; int n;
    if (first_exp_n == -2) { out = tmp; n = (int)strlen(tmp); }
    else if (first_exp_n >= 0) { out = first_exp; n = first_exp_n; }
    else return -1;
    if ((size_t)n >= len) return -1;
    memcpy(buf, out, (size_t)n + 1);
    return n;
}
