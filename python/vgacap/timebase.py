# SPDX-License-Identifier: Apache-2.0
"""Sample index -> picoseconds, and the shortest round-trip double formatter.

Mirrors include/vgacap/time.h (src/stream/time.c); both must agree exactly.
"""


def sample_time_ps(n, samples_per_clock, clock_hz):
    """round(n * 10^12 / (s * hz)) ps, halves rounded up; 0 if s*hz == 0."""
    d = samples_per_clock * clock_hz
    if d == 0:
        return 0
    return (2 * n * 10**12 + d) // (2 * d)


def fmt_double(v):
    """Shortest '%.{p}g', p in 1..17, that reads back to the same double.

    '%g' goes to exponent form once the exponent reaches p (25175000.0 is
    '2.5175e+07' at p=5); plain decimal wins: the first round-tripping p whose
    text has no exponent, else the first round-tripping exponent form.
    """
    first_exp = None
    for p in range(1, 18):
        s = "%.*g" % (p, v)
        if p != 17 and float(s) != v:
            continue
        if "e" not in s:
            return s
        if first_exp is None:
            first_exp = s
    return first_exp
