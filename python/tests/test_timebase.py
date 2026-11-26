# SPDX-License-Identifier: Apache-2.0
import math, pathlib, random, struct, subprocess
import pytest
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
    if not DUMP.exists():
        pytest.skip(f"{DUMP} not built: run cmake --build build")
    out = subprocess.run([str(DUMP)], input="\n".join(lines) + "\n",
                         capture_output=True, text=True, check=True).stdout
    return out.splitlines()

def _ints(lines):
    return [list(map(int, l.split())) for l in lines]

def _rnd(rng):
    return rng.choice([rng.randrange(1 << 64), rng.randrange(1 << 32),
                       rng.randrange(1 << rng.randrange(1, 65))])

def test_c_mul_vs_bigint():
    rng = random.Random(7)
    pairs = [(_rnd(rng), _rnd(rng)) for _ in range(10000)] + [(M64, M64), (0, M64), (1 << 63, 2)]
    for (a, b), (hi, lo) in zip(pairs, _ints(_run([f"mul {a} {b}" for a, b in pairs]))):
        assert (hi << 64) | lo == a * b

def test_c_div_vs_bigint_and_reference():
    rng = random.Random(8)
    edge_d = [1, 2, 3, (1 << 32) - 1, 1 << 32, (1 << 32) + 1, (1 << 63) - 1, 1 << 63, M64]
    divs = []
    for _ in range(100000):
        d = rng.choice(edge_d + [_rnd(rng)] * 6) or 1
        divs.append((rng.choice([0, 1, M64, _rnd(rng), _rnd(rng)]), _rnd(rng), d))
    divs += [(M64, M64, d) for d in edge_d] + [(0, 5, 1 << 63), (1, 0, (1 << 63) + 1)]
    new = _ints(_run([f"div {h} {l} {d}" for h, l, d in divs]))
    ref = _ints(_run([f"refdiv {h} {l} {d}" for h, l, d in divs]))
    assert new == ref
    for (h, l, d), (qh, ql, r) in zip(divs, new):
        x = (h << 64) | l
        assert ((qh << 64) | ql, r) == (x // d, x % d)

def test_c_time_vs_fraction():
    rng = random.Random(9)
    cases = [(rng.randrange(13_000_000_000), rng.choice([1, 2]), rng.randrange(1, 1 << 32))
             for _ in range(10000)]
    cases += [(n, 2, 25175000) for n in (0, 1, 12_000_000_000)]
    got = _run([f"time {n} {s} {hz}" for n, s, hz in cases])
    for (n, s, hz), g in zip(cases, got):
        assert int(g) == ref_ps(n, s, hz) == sample_time_ps(n, s, hz)

def test_c_fmt_double_parity():
    # NaN and inf are excluded: no caller formats them.
    rng = random.Random(10)
    vals = [1.0, 1.90625, 832.0 / 800, 800.0 / 840, 25175000.0, 0.0, -0.0,
            5e-324, 2.2250738585072009e-308, 1.7976931348623157e308]
    for k in range(-30, 31):
        p = 10.0 ** k
        vals += [p, -p, math.nextafter(p, 0), math.nextafter(p, math.inf), 9.5 * p, 1.5 * p]
    for _ in range(4000):
        vals.append(struct.unpack("<d", struct.pack("<Q", rng.randrange(1 << 64)))[0])
    for _ in range(3000):
        vals.append(struct.unpack("<d", struct.pack("<Q", rng.randrange(1 << 52)))[0])  # subnormals
    for _ in range(2000):
        vals.append(rng.uniform(-1e6, 1e6)); vals.append(rng.randrange(10**9) / rng.randrange(1, 10**5))
    vals = [v for v in vals if math.isfinite(v)]
    got = _run([f"fmt {struct.unpack('<Q', struct.pack('<d', v))[0]:x}" for v in vals])
    assert len(vals) >= 10000
    for v, g in zip(vals, got):
        assert g == fmt_double(v), (v.hex(), g, fmt_double(v))
