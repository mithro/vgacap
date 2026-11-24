// SPDX-License-Identifier: Apache-2.0
// Sample index -> picoseconds, and the one double formatter all text outputs use.
#ifndef VGACAP_TIME_H
#define VGACAP_TIME_H
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Time of sample n: round(n * 10^12 / (samples_per_clock * clock_hz)) ps,
// halves rounded up, computed exactly in 128-bit integer arithmetic (no
// per-sample accumulation, so no drift). clock_hz == 0 (or samples_per_clock
// == 0) returns 0; a result above UINT64_MAX saturates to UINT64_MAX.
uint64_t vgacap_sample_time_ps(uint64_t n, uint32_t samples_per_clock, uint32_t clock_hz);

// Writes the shortest "%.{p}g" (p = 1..17) that strtod reads back to exactly v,
// NUL-terminated. Returns its length, or -1 if it does not fit in len bytes.
int vgacap_fmt_double(char *buf, size_t len, double v);

#ifdef __cplusplus
}
#endif
#endif
