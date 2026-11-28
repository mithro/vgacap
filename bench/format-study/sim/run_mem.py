#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.11"
# ///
"""Peak memory of each plan in the Verilator loop (bench_tick's max_rss_kib).

    run_mem.py [--chunks N] [--cpus 2-21]

For each busy project, bench_tick runs with the untraced model alone, with
two untraced models, and with the untraced model and one plan, N chunks of
65,536 clocks (default 600: about 39 M clocks, so that every plan has
filled and handed over both buffer sets more than once). bench_tick gives
each variant a model of its own, so a plan's run holds two models: a
plan's memory is its run's peak RSS minus the two-model run's. Each run is pinned to one CPU of
--cpus; the runs go 20 at a time. Results to
$OUT/results/sim/mem.csv (project, variants, max_rss_kib, exit); the
outputs and stderr under $OUT/sim/mem/.
"""

import csv
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = Path(os.environ.get("OUT", ROOT / "tmp" / "format-study"))
SIMSRC = Path(os.environ.get("SIMSRC", ROOT / "tmp" / "sim" / "src"))
PROJECTS = ["tt08_tt_um_a1k0n_nyancat", "ttihp25a_tt_um_MichaelBell_rle_vga", "ttihp25a_tt_um_rejunity_atari2600"]
PLANS = ["vgch2", "lean", "events", "evplanar", "planar", "changelog", "raw"]


def arg(name: str, default: str) -> str:
    a = sys.argv[1:]
    return a[a.index(name) + 1] if name in a else default


def cpus(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def run(job):
    project, variants, cpu, chunks = job
    log = (SIMSRC / project / "sim.log").read_text()
    hz = re.search(r"--clock-hz (\S+)", log).group(1)
    q = re.search(r"--qspi (\S+)", log)
    o = OUT / "sim" / "mem" / project / variants.replace(",", "+")
    o.mkdir(parents=True, exist_ok=True)
    for f in o.glob("*.trace"):
        f.unlink()
    cmd = ["taskset", "-c", str(cpu), str(OUT / "sim" / project / "tick" / "Vtop"), "--out", str(o),
           "--clock-hz", hz, "--chunk", "65536", "--chunks", str(chunks), "--variants", variants]
    if q:
        cmd += ["--qspi", q.group(1)]
    with (o / "stdout.csv").open("w") as so, (o / "stderr.log").open("w") as se:
        rc = subprocess.run(cmd, cwd=SIMSRC / project / "repo" / "src", stdout=so, stderr=se).returncode
    m = re.search(r"max_rss_kib=(\d+)", (o / "stderr.log").read_text())
    for f in o.glob("*.trace"):   # the files are not needed, only the peak
        f.unlink()
    return project, variants, m.group(1) if m else "", rc


def main() -> int:
    chunks = int(arg("--chunks", "600"))
    cl = cpus(arg("--cpus", "2-21"))
    jobs = [(p, v, 0, chunks) for p in PROJECTS for v in ["none", "none,none"] + [f"none,{x}" for x in PLANS]]
    jobs = [(p, v, cl[i % len(cl)], c) for i, (p, v, _, c) in enumerate(jobs)]
    res = OUT / "results" / "sim" / "mem.csv"
    with ThreadPoolExecutor(len(cl)) as ex:
        rows = list(ex.map(run, jobs))
    bad = 0
    with res.open("w", newline="") as f:
        w = csv.writer(f)   # variants hold commas: csv quotes them
        w.writerow(["project", "variants", "max_rss_kib", "exit"])
        for p, v, kib, rc in rows:
            w.writerow([p, v, kib, rc])
            bad += rc != 0 or not kib
    print(f"run_mem: {len(rows)} runs, {bad} failed; {res}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
