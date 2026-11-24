# SPDX-License-Identifier: Apache-2.0
import pathlib, random, subprocess
from fractions import Fraction
from vgacap.timebase import sample_time_ps, fmt_double

DUMP = pathlib.Path(__file__).resolve().parents[2] / "build" / "test_time_dump"
M64 = (1 << 64) - 1

def ref_ps(n, s, hz):
    x = Fraction(n * 10**12, s * hz)
    return int(x + Fraction(1, 2)) if x >= 0 else None  # floor(x + 1/2): halves up

def test_time_vectors():
    for n, s, hz, want in [
        (1, 2, 25175000, 19861), (2, 2, 25175000, 39722), (3, 2, 25175000, 59583),
        (12000000000, 2, 25175000, 238331678252234),
        (12000000001, 2, 25175000, 238331678272095),
        (1, 1, 25175000, 39722), (3, 2, 48000000, 31250), (0, 2, 25175000, 0),
        (5, 2, 0, 0), (1, 4000, 500000000, 1), (3, 4000, 500000000, 2),
    ]:
        assert sample_time_ps(n, s, hz) == want

def test_time_random_vs_fraction():
    rng = random.Random(6)
    for _ in range(10000):
        n = rng.randrange(13_000_000_000)
        assert sample_time_ps(n, 2, 25175000) == ref_ps(n, 2, 25175000)

def test_no_drift():
    prev = sample_time_ps(0, 2, 25175000)
    for n in range(100000):
        nxt = sample_time_ps(n + 1, 2, 25175000)
        assert nxt - prev in (19860, 19861)
        prev = nxt

def test_fmt_double_vectors():
    assert fmt_double(1.0) == "1"
    assert fmt_double(1.90625) == "1.90625"
    assert fmt_double(832.0 / 800) == "1.04"
    assert fmt_double(800.0 / 840) == "0.9523809523809523"
    assert fmt_double(25175000.0) == "25175000"

def _run(lines):
    out = subprocess.run([str(DUMP)], input="\n".join(lines) + "\n",
                         capture_output=True, text=True, check=True).stdout
    return [list(map(int, l.split())) for l in out.splitlines()]

def test_c_helpers_vs_fraction():
    rng = random.Random(7)
    def rnd():
        return rng.choice([rng.randrange(1 << 64), rng.randrange(1 << 32), rng.randrange(1 << rng.randrange(1, 65))])
    pairs = [(rnd(), rnd()) for _ in range(10000)]
    pairs += [(M64, M64), (0, M64), (1 << 63, 2)]
    got = _run([f"mul {a} {b}" for a, b in pairs])
    for (a, b), (hi, lo) in zip(pairs, got):
        assert (hi << 64) | lo == a * b
    divs = []
    for _ in range(10000):
        d = rnd() or 1
        divs.append((rng.randrange(1 << 64), rnd(), d))
    divs += [(M64, M64, M64), (M64, M64, 1), (0, 5, 1 << 63), (1, 0, (1 << 63) + 1)]
    got = _run([f"div {h} {l} {d}" for h, l, d in divs])
    for (h, l, d), (qh, ql, r) in zip(divs, got):
        x = (h << 64) | l
        assert ((qh << 64) | ql, r) == (x // d, x % d)
        assert Fraction(x, d) == ((qh << 64) | ql) + Fraction(r, d)
