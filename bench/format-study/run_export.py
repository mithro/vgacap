#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.11"
# ///
"""A full-pin FST export of a planar trace against vgch2's (fix round 2).

    run_export.py [--reps 3] [--cpus 2-21]

On the three busy traces, each rep (interleaved, the order rotated):
  - `fsbench read --patterns export` of the vgch2 file (vgacap's reader);
  - the same of the planar file (planar writer, zstd 1; the study reader);
  - vgacap-export --no-clk --format fst of the vgch2 file, the real tool,
    which also writes the decoded signals: a calibration, not a pair.
Each job has two CPUs (the reader and libfst's compressor thread), as a
job's scope. The first rep's FST files are read back (`fsbench read --kind
fst --patterns validate`) and must hash as their source does. Results:
$OUT/results/export/export.csv; logs under $OUT/logs/export/.
"""

import csv
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = Path(os.environ.get("OUT", ROOT / "tmp" / "format-study"))
FSBENCH = OUT / "build" / "fsbench"
EXPORT = OUT / "sim" / "vgacap-build" / "vgacap-export"
FILES = OUT / "files"
PROJECTS = ["tt08_tt_um_a1k0n_nyancat", "ttihp25a_tt_um_MichaelBell_rle_vga", "ttihp25a_tt_um_rejunity_atari2600"]
SIMSRC = Path(os.environ.get("SIMSRC", ROOT / "tmp" / "sim" / "src"))


def hz(project: str) -> str:   # the project's clock, from its job's sim.log
    return re.search(r"--clock-hz (\S+)", (SIMSRC / project / "sim.log").read_text()).group(1)


def arg(name, default):
    a = sys.argv[1:]
    return a[a.index(name) + 1] if name in a else default


def validate(path: Path, kind: str, project: str) -> str:
    p = subprocess.run([str(FSBENCH), "read", str(path), "--kind", kind, "--project", project,
                        "--patterns", "validate", "--clock-hz", hz(project)], capture_output=True, text=True)
    rows = [r for r in csv.DictReader(p.stdout.splitlines()) if r.get("pattern") == "validate-full"]
    return rows[0]["hash"] if p.returncode == 0 and rows else f"error: {p.stderr.strip()}"


def job(j):
    project, tool, rep, cpus = j
    logs = OUT / "logs" / "export"
    scratch = OUT / "scratch" / "export"
    logs.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    fst = scratch / f"{project}.{tool}.r{rep}.fst"
    log = logs / f"{project}.{tool}.r{rep}.log"
    src = FILES / (f"{project}.vgch2.trace.zst" if tool != "planar" else f"{project}.planar.identity.zstd1.stdy")
    if tool == "vgacap-export":
        cmd = [str(EXPORT), str(src), "--format", "fst", "--out", str(fst), "--no-clk"]
    else:
        cmd = [str(FSBENCH), "read", str(src), "--kind", "vgch2" if tool == "vgch2" else "stdy", "--project", project,
               "--patterns", "export", "--reps", "1", "--out", str(fst), "--clock-hz", hz(project)]
    full = ["/usr/bin/time", "-f", "#time user_s=%U sys_s=%S wall_s=%e max_rss_kib=%M", "taskset", "-c", cpus] + cmd
    t0 = time.time()
    p = subprocess.run(full, capture_output=True, text=True)
    log.write_text(p.stdout + p.stderr)
    m = re.search(r"#time user_s=(\S+) sys_s=(\S+) wall_s=(\S+) max_rss_kib=(\S+)", p.stderr)
    t = re.search(r"#export,.*thread_ns=(\d+),process_ns=(\d+),wall_ns=(\d+),fst_bytes=(-?\d+)", p.stdout)
    row = {"project": project, "tool": tool, "rep": rep, "exit": p.returncode,
           "process_s": f"{float(m.group(1)) + float(m.group(2)):.2f}" if m else "",
           "wall_s": m.group(3) if m else "", "max_rss_kib": m.group(4) if m else "",
           "reader_thread_s": f"{int(t.group(1)) / 1e9:.2f}" if t else "",
           "fst_bytes": fst.stat().st_size if fst.exists() else "", "fst_hash": "", "source_hash": ""}
    if rep == 0 and p.returncode == 0 and tool != "vgacap-export":
        row["fst_hash"] = validate(fst, "fst", project)
        row["source_hash"] = validate(src, "vgch2" if tool == "vgch2" else "stdy", project)
    fst.unlink(missing_ok=True)
    print(f"export {project} {tool} r{rep}: exit {p.returncode}, {time.time() - t0:.0f} s", file=sys.stderr, flush=True)
    return row


def main() -> int:
    reps = int(arg("--reps", "3"))
    lo, hi = (int(x) for x in arg("--cpus", "2-21").split("-"))
    pairs = [f"{c},{c + 1}" for c in range(lo, hi, 2)]
    tools = ["vgch2", "planar", "vgacap-export"]
    jobs = []
    for rep in range(reps):
        for i, project in enumerate(PROJECTS):
            for k in range(3):
                jobs.append((project, tools[(k + rep + i) % 3], rep))
    jobs = [(p, t, r, pairs[n % len(pairs)]) for n, (p, t, r) in enumerate(jobs)]
    with ThreadPoolExecutor(len(pairs)) as ex:
        rows = list(ex.map(job, jobs))
    res = OUT / "results" / "export"
    res.mkdir(parents=True, exist_ok=True)
    with (res / "export.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    bad = [r for r in rows if r["exit"] != 0 or r["fst_hash"] != r["source_hash"]]
    print(f"run_export: {len(rows)} jobs, {len(bad)} failed", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
