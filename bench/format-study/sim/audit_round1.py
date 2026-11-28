#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.11"
# ///
"""Read back fix round 1's main and scope sets, as an audit.

    audit_round1.py

Fix round 1's main and scope sets (fx1a-fx1d, fx1s) ran several of the
study's background writers in one process, sharing one stdio buffer, so
their background plans' files are expected to fail. This reads every trace
of those sets with sim/readback.py into $OUT/results/sim/readback-fx1.csv
(the read-back table's fx1 rows) and checks the result is what the doc
says: every vgch2 and lean file passes, and every row has a file. It exits
0 when that holds, whatever the background plans' files give, and 1
otherwise. readback.py's log goes to $OUT/readback-round1-audit.log.
"""

import csv
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = Path(os.environ.get("OUT", ROOT / "tmp" / "format-study"))
FSBENCH = os.environ.get("FSBENCH", str(OUT / "build" / "fsbench"))
TAGS = ["fx1a", "fx1b", "fx1c", "fx1d", "fx1s"]
VARIANTS = {"vgch2", "lean", "events", "evplanar", "planar", "changelog"}


def main() -> int:
    dirs = sorted(str(d) for t in TAGS for d in (OUT / "sim" / t).iterdir() if d.is_dir())
    if len(dirs) != 5 * len(TAGS):
        print(f"audit_round1: {len(dirs)} project directories, expected {5 * len(TAGS)}", file=sys.stderr)
        return 1
    res = OUT / "results" / "sim" / "readback-fx1.csv"
    with (OUT / "readback-round1-audit.log").open("w") as log:
        rc = subprocess.run([str(HERE / "readback.py"), FSBENCH, str(res), *dirs], stdout=log, stderr=log).returncode
    rows = list(csv.DictReader(res.open()))
    seen = {(r["tag"], r["project"], r["file"].removesuffix(".trace")) for r in rows}
    want = {(Path(d).parent.name, Path(d).name, v) for d in dirs for v in VARIANTS}
    bad_ref = [r for r in rows if r["file"] in ("vgch2.trace", "lean.trace") and r["pass"] != "1"]
    fails = sum(r["pass"] != "1" for r in rows)
    print(f"audit_round1: readback.py exit {rc}; {len(rows)} files, {fails} fail; "
          f"vgch2 and lean failing: {len(bad_ref)}; missing: {len(want - seen)}; {res}", file=sys.stderr)
    return 0 if not bad_ref and want <= seen else 1


if __name__ == "__main__":
    sys.exit(main())
