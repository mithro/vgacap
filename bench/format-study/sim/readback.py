#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.11"
# ///
"""Read back every trace a bench_tick set wrote, and check it against lean's.

    readback.py FSBENCH CSV DIR [DIR ...] [--ref-dir REFDIR]

Each DIR is one project's bench_tick output (<tag>/<project>/). Every
*.trace there is read with `fsbench read --patterns validate`: vgch2.trace
and lean.trace as VGCH v2, the others as study files. The reference is
lean.trace in the same DIR, else vgch2.trace there, else lean.trace in
REFDIR/<project> (a set with neither, such as raw's, against another set
of the same chunks: every set runs the same clocks from reset). A file
passes when its full hash, its sample count and its uo_out run count are
the reference's. A row per file goes to CSV; any failure, or a file that
cannot be read, exits 1. READBACK_JOBS files are read at once (default 10).
"""

import csv
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VGCH = {"vgch2.trace", "lean.trace"}


def validate(fsbench: str, path: Path, project: str) -> dict:
    kind = "vgch2" if path.name in VGCH else "stdy"
    p = subprocess.run(
        [fsbench, "read", str(path), "--kind", kind, "--project", project, "--patterns", "validate"],
        capture_output=True,
        text=True,
    )
    rows = {r["pattern"]: r for r in csv.DictReader(p.stdout.splitlines())}
    if p.returncode != 0 or "validate-full" not in rows or "validate-port0" not in rows:
        return {"ok": False, "error": f"exit {p.returncode}: {p.stderr.strip()}"}
    full, port0 = rows["validate-full"], rows["validate-port0"]
    return {
        "ok": True,
        "error": p.stderr.strip(),
        "hash": full["hash"],
        "runs": int(full["runs"]),
        "samples": int(full["samples"]),
        "uo_runs": int(port0["runs"]),
        "uo_hash": port0["hash"],
    }


def main() -> int:
    args = sys.argv[1:]
    ref_dir = None
    if "--ref-dir" in args:
        i = args.index("--ref-dir")
        ref_dir = Path(args[i + 1])
        del args[i : i + 2]
    if len(args) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    fsbench, out_csv, dirs = args[0], Path(args[1]), [Path(d) for d in args[2:]]
    fields = ["tag", "project", "file", "ref", "hash", "runs", "samples", "uo_runs", "uo_hash",
              "ref_hash", "ref_samples", "ref_uo_runs", "pass", "error"]
    # Every file's read, in parallel; the checks below then run in order.
    paths = sorted({t for d in dirs for t in d.glob("*.trace")} |
                   ({ref_dir / d.name / "lean.trace" for d in dirs} if ref_dir else set()))
    paths = [t for t in paths if t.exists()]
    with ThreadPoolExecutor(int(os.environ.get("READBACK_JOBS", "10"))) as ex:
        got = dict(zip(paths, ex.map(lambda t: validate(fsbench, t, t.parent.name), paths)))
    bad = 0
    with out_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for d in dirs:
            project, tag = d.name, d.parent.name
            files = sorted(d.glob("*.trace"))
            if not files:
                print(f"readback: {d}: no traces", file=sys.stderr)
                bad += 1
                continue
            ref = next((d / n for n in ("lean.trace", "vgch2.trace") if (d / n).exists()), None)
            if ref is None and ref_dir is not None and (ref_dir / project / "lean.trace").exists():
                ref = ref_dir / project / "lean.trace"
            if ref is None:
                print(f"readback: {d}: no reference (lean.trace, vgch2.trace or --ref-dir)", file=sys.stderr)
                bad += 1
                continue
            r = got[ref]
            if not r["ok"]:
                print(f"readback: {ref}: {r['error']}", file=sys.stderr)
                bad += 1
                continue
            for t in files:
                v = got[t]
                ok = v["ok"] and (v["hash"], v["samples"], v["uo_runs"]) == (r["hash"], r["samples"], r["uo_runs"])
                row = {"tag": tag, "project": project, "file": t.name, "ref": str(ref), "pass": int(ok),
                       "error": v["error"], "ref_hash": r["hash"], "ref_samples": r["samples"],
                       "ref_uo_runs": r["uo_runs"]}
                if v["ok"]:
                    row.update({k: v[k] for k in ("hash", "runs", "samples", "uo_runs", "uo_hash")})
                w.writerow(row)
                f.flush()
                msg = "ok" if ok else "FAILED"
                print(f"readback {tag} {project} {t.name}: {msg} (hash {v.get('hash')}, uo_out runs "
                      f"{v.get('uo_runs')}; reference {r['hash']}, {r['uo_runs']}) {v['error']}", file=sys.stderr)
                bad += not ok
    if bad:
        print(f"readback: {bad} failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
