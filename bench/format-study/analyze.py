# /// script
# requires-python = ">=3.11"
# ///
# SPDX-License-Identifier: Apache-2.0
"""The trace format study's tables, from run_all.sh's and run_sim.sh's raw results.

    uv run --script analyze.py OUT > OUT/results/tables.md

OUT is run_all.sh's output directory. Every table of
docs/superpowers/2026-11-27-trace-format-study.md is printed here, under the same
heading, from the CSV files under OUT/results. Nothing is typed in by hand
except data/pilot_baseline.csv (the pilot's untraced simulation times).
"""
import contextlib
import csv
import glob
import io
import os
import random
import re
import statistics
import sys
from collections import defaultdict

OUT = sys.argv[1]
RES = os.path.join(OUT, "results")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

SHORT = {
    "tt05_tt_um_tomkeddie_a": "tomkeddie_a",
    "tt07_tt_um_tinytapeout_dvd_screensaver": "dvd_screensaver",
    "tt08_tt_um_MichaelBell_mandelbrot": "mandelbrot",
    "tt08_tt_um_a1k0n_nyancat": "nyancat",
    "tt08_tt_um_johshoff_metaballs": "metaballs",
    "tt08_tt_um_nyan": "nyan",
    "ttihp25a_tt_um_MichaelBell_rle_vga": "rle_vga",
    "ttihp25a_tt_um_a1k0n_vgadonut": "vgadonut",
    "ttihp25a_tt_um_cfib_demo": "cfib_demo",
    "ttihp25a_tt_um_rebeccargb_colorbars": "colorbars",
    "ttihp25a_tt_um_rejunity_atari2600": "atari2600",
}
# The five the brief puts first, then the rest.
ORDER = ["nyancat", "rle_vga", "dvd_screensaver", "colorbars", "mandelbrot",
         "atari2600", "vgadonut", "cfib_demo", "nyan", "metaballs", "tomkeddie_a"]
LONG = {v: k for k, v in SHORT.items()}
random.seed(20261127)


def rows(path):
    with open(path) as f:
        return [r for r in csv.reader(f) if r and not r[0].startswith("#")]


def table(head, body):
    print("| " + " | ".join(head) + " |")
    print("|" + "|".join("---" if i == 0 else "---:" for i in range(len(head))) + "|")
    for r in body:
        print("| " + " | ".join(str(x) for x in r) + " |")
    print()


def f(x, d=1):
    return f"{x:,.{d}f}"


def pct(x, d=1):
    return f"{100 * x:.{d}f}"


# ---- inputs ------------------------------------------------------------------------------
base = {}
for r in csv.DictReader(open(os.path.join(HERE, "data", "pilot_baseline.csv"))):
    base[SHORT[r["project"]]] = r

info = {}
for p in glob.glob(os.path.join(RES, "info", "*.csv")):
    rr = list(csv.DictReader(open(p)))
    if rr:
        info[SHORT[rr[0]["project"]]] = rr[0]

projects = [p for p in ORDER if p in info]


def clocks(p):
    return int(info[p]["clocks"])


def sim_ns_per_clock(p):
    """The pilot's untraced simulation, wall ns a clock."""
    return float(base[p]["wall_without_s"]) * 1e9 / int(base[p]["clocks"])


# ---- encode results ------------------------------------------------------------------------
enc = defaultdict(dict)    # (project, writer) -> {(transform, comp): row}
passes = {}                # (project, job) -> pass dict
for path in glob.glob(os.path.join(RES, "encode", "*.csv")):
    job = os.path.basename(path)[:-4]
    proj_long, kind = job.rsplit(".", 1)
    p = SHORT[proj_long]
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("#pass,"):
                parts = line.split(",")
                d = {"writer": parts[2]}
                for kv in parts[4:]:
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        d[k] = v
                passes[(p, kind)] = d
            elif line and not line.startswith("project,"):
                c = line.split(",")
                hdr = ["project", "writer", "transform", "comp", "blocks", "blocks_measured", "samples",
                       "samples_measured", "raw_bytes_measured", "comp_bytes_measured", "transform_ns_measured",
                       "comp_ns_measured", "est_comp_bytes", "est_bg_ns", "clk_errors"]
                d = dict(zip(hdr, c))
                enc[(p, kind)][(d["transform"], d["comp"])] = d

# ---- 1. the traces and their code paths ---------------------------------------------------------
print("## Inputs: the 11 traces and the code paths they take\n")
print("### Trace properties\n")
body = []
for p in projects:
    i = info[p]
    c, s = clocks(p), int(i["samples"])
    body.append([p, f(c / 1e6, 0), f(int(info[p]["clock_hz"]) / 1e6, 3), f(int(i["runs"]) / 1e6, 1),
                 f(int(i["runs"]) / c, 3), f(os.path.getsize(os.path.join(OUT, "traces", LONG[p] + "_trace.vgch.zst")) / 1e6, 2)])
table(["project", "clocks (M)", "clock (MHz)", "runs (M)", "runs per clock", "published trace (MB)"], body)

print("### Samples that change each port\n")
names = ["uo_out", "uio_out", "uio_oe", "ui_in", "uio_in", "rst_n", "ena"]
body = []
for p in projects:
    i = info[p]
    s = int(i["samples"])
    body.append([p] + [pct(int(i[f"changes_port{k}"]) / s, 2) for k in range(7)] +
                [pct(int(i["multi_port_samples"]) / s, 2)])
table(["project"] + [f"{n} (%)" for n in names] + ["two or more ports (%)"], body)

print("### vgch2's code paths, per clock, whole trace\n")
body = []
for p in projects:
    c = clocks(p)
    ch = int(info[p]["changed_clocks"])
    e = enc.get((p, "vgch2"), {})
    blocks = int(next(iter(e.values()))["blocks"]) if e else 0
    # fast: tb.cpp's raw compare equal and vgacap_trace_repeat2's one test;
    # slow change: raw_slow, pack, push2; block boundary: a block closed
    # (by clocks or size) or sample 0, inside repeat2_slow or the push slow path.
    body.append([p, pct((c - ch) / c, 3), pct(ch / c, 3), f"{blocks / c * 1e6:.3f}", blocks])
table(["project", "fast run-extend (%)", "slow change (%)", "block boundary (per million clocks)", "blocks"], body)


# ---- 2. writer cost: the replay -------------------------------------------------------------------
def load_writer(dirname):
    """(project, writer) -> {chunk: {rep: ns}}, and chunk clocks, and changed clocks, and loads."""
    data = defaultdict(lambda: defaultdict(dict))
    meta = {}
    loads = defaultdict(list)
    for path in glob.glob(os.path.join(RES, dirname, "*.csv")):
        for r in csv.DictReader(open(path)):
            p = SHORT[r["project"]]
            ch = int(r["chunk"])
            data[(p, r["writer"])][ch][int(r["rep"])] = float(r["ns"])
            meta[(p, ch)] = (int(r["clocks"]), int(r["changed_clocks"]))
            loads[p].append(float(r["load1"]))
    return data, meta, loads


def paired(data, meta, p, w, nullw="null"):
    """ns a clock of writer w over null, paired by chunk and round: the
    median over rounds of each chunk's difference, summed over chunks; a
    bootstrap over chunks for the 95% interval; and the spread of the
    per-round totals."""
    a, z = data.get((p, w)), data.get((p, nullw))
    if not a or not z:
        return None
    chunks = sorted(set(a) & set(z))
    med, clk = [], []
    per_round = defaultdict(float)
    for ch in chunks:
        reps = sorted(set(a[ch]) & set(z[ch]))
        d = [a[ch][r] - z[ch][r] for r in reps]
        med.append(statistics.median(d))
        clk.append(meta[(p, ch)][0])
        for r in reps:
            per_round[r] += a[ch][r] - z[ch][r]
    tot_clk = sum(clk)
    est = sum(med) / tot_clk
    boots = []
    n = len(chunks)
    for _ in range(2000):
        idx = [random.randrange(n) for _ in range(n)]
        boots.append(sum(med[i] for i in idx) / sum(clk[i] for i in idx))
    boots.sort()
    rounds = sorted(v / tot_clk for v in per_round.values())
    return est, boots[49], boots[1949], rounds[0], rounds[-1], n


wdir = "writer2" if os.path.isdir(os.path.join(RES, "writer2")) else "writer"
wdata, wmeta, wloads = load_writer(wdir)
odata, ometa, oloads = load_writer("writer")   # the first pass: fst, fst_clk, lxt2, vcd
WRITERS = ["vgch2", "runs", "planar", "events", "changelog", "raw", "fst", "vcd", "lxt2", "fst_clk"]

print("## Writer cost on the simulation thread (replay)\n")
print(f"Source: `results/{wdir}` (vgch2, runs, planar, events, changelog, raw) and `results/writer` (fst, vcd, lxt2, fst_clk).\n")
print("### ns per clock over the null writer: estimate [95% interval]\n")
body = []
est_cache = {}
for p in projects:
    row = [p]
    for w in WRITERS:
        src = (wdata, wmeta) if w in ("vgch2", "runs", "planar", "events", "changelog", "raw") else (odata, ometa)
        r = paired(src[0], src[1], p, w)
        est_cache[(p, w)] = r
        row.append("-" if r is None else f"{r[0]:.2f} [{r[1]:.2f}, {r[2]:.2f}]")
    body.append(row)
table(["project"] + WRITERS, body)

print("### Spread of the five rounds' totals, ns per clock over null (min to max)\n")
body = []
for p in projects:
    row = [p]
    for w in WRITERS:
        r = est_cache[(p, w)]
        row.append("-" if r is None else f"{r[3]:.2f} to {r[4]:.2f}")
    body.append(row)
table(["project"] + WRITERS, body)

print("### As a share of the untraced simulation (pilot wall time), %; the budget is 5%\n")
body = []
for p in projects:
    row = [p, f(sim_ns_per_clock(p), 1)]
    for w in WRITERS:
        r = est_cache[(p, w)]
        row.append("-" if r is None else pct(r[0] / sim_ns_per_clock(p), 2))
    body.append(row)
table(["project", "untraced ns per clock"] + WRITERS, body)

if os.path.isdir(os.path.join(RES, "writer2-run1")):
    print("### The replay run twice: ns per clock over null, first run (`writer2-run1`) and last (`writer2`)\n")
    r1data, r1meta, _ = load_writer("writer2-run1")
    body = []
    for p in projects:
        row = [p]
        for w in ("vgch2", "planar", "events", "raw"):
            a, b = paired(r1data, r1meta, p, w), est_cache.get((p, w))
            row += ["-", "-"] if not (a and b) else [f"{a[0]:.2f}", f"{b[0]:.2f}"]
        a, b = paired(r1data, r1meta, p, "vgch2"), paired(r1data, r1meta, p, "events")
        c, d = est_cache.get((p, "vgch2")), est_cache.get((p, "events"))
        ok = a and b and c and d and b[0] > 0 and d[0] > 0
        row += [f"{a[0] / b[0]:.2f}", f"{c[0] / d[0]:.2f}"] if ok else ["-", "-"]
        body.append(row)
    table(["project"] + [f"{w}, {r}" for w in ("vgch2", "planar", "events", "raw", "vgch2 over events")
                         for r in ("first", "last")], body)

print("### FST, VCD and LXT2 over vgch2, both from the same replay run (`writer`)\n")
print("The tables above take vgch2 from `writer2` and FST, VCD and LXT2 from `writer`. These ratios divide within "
      "the `writer` run alone, so a run's own drift cancels.\n")
body = []
for p in projects:
    v = paired(odata, ometa, p, "vgch2")
    row = [p]
    for w in ("fst", "vcd", "lxt2"):
        r = paired(odata, ometa, p, w)
        row.append("-" if not (r and v) or v[0] <= 0 else f"{r[0] / v[0]:.2f}")
    body.append(row)
table(["project", "FST over vgch2", "VCD over vgch2", "LXT2 over vgch2"], body)

print("### The replayed chunks against the whole trace\n")
body = []
for p in projects:
    chs = [k for k in wmeta if k[0] == p]
    cl = sum(wmeta[k][0] for k in chs)
    chg = sum(wmeta[k][1] for k in chs)
    ld = wloads.get(p, [0])
    body.append([p, len(chs), f(cl / 1e6, 0), pct(cl / clocks(p), 1), pct(chg / cl if cl else 0, 3),
                 pct(int(info[p]["changed_clocks"]) / clocks(p), 3), f"{min(ld) / 88:.2f} to {max(ld) / 88:.2f}"])
table(["project", "chunks", "clocks replayed (M)", "of the trace (%)", "changed clocks, replayed (%)",
       "changed clocks, whole trace (%)", "load per CPU during the run"], body)

# ---- 2a. the lean writer writes vgacap's file ----------------------------------------------------------
lean = []
for path in sorted(glob.glob(os.path.join(RES, "lean", "*.csv"))):
    lean += [r for r in csv.DictReader(open(path))]
if lean:
    print("## The lean writer writes vgacap's file\n")
    print("Each published trace replayed, whole and compressed, through the lean writer (vgacap's trace_write.c "
          "with push2's slow path replaced) and through vgacap's own (vgch2), then compared with the published file "
          "by `cmp` (test_lean.sh, run_all.sh stage lean). These runs check the bytes; their times are not a "
          "measurement (unpinned beside other work).\n")
    by = defaultdict(dict)
    for r in lean:
        by[SHORT[r["project"]]][r["writer"]] = r
    body = []
    for p in projects:
        if p not in by:
            continue
        a, b = by[p].get("lean"), by[p].get("vgch2")
        body.append([p, f"{int(a['published_bytes']):,}" if a else "-", f"{int(a['bytes']):,}" if a else "-",
                     a["cmp"] if a else "-", f"{int(b['bytes']):,}" if b else "-", b["cmp"] if b else "-"])
    table(["project", "published bytes", "lean bytes", "lean against published", "vgch2 bytes",
           "vgch2 against published"], body)

# ---- 3. the simulation check ---------------------------------------------------------------------
# ---- 3a. the in-process simulation check (bench_tick) -------------------------------------------
# Replicates: tick, tickB, tickC, tickD: separate processes, each with the
# variants in a different order (so their models sit at different heap
# addresses and run in a different turn).
TICKV = ["vgch2", "runs", "events", "changelog", "raw", "raw_small"]
FX1V = ["vgch2", "lean", "events", "evplanar", "planar", "changelog"]
BGV = ["events", "evplanar", "planar", "changelog"]   # the plans with a background writer of the study's
FX2V = ["vgch2"] + BGV
tickrep = defaultdict(dict)   # tag -> project -> variant -> {chunk: (ns, clocks)}
tickwall = defaultdict(dict)  # tag -> project -> variant -> {chunk: wall ns} (fix round 1's sets)
for path in glob.glob(os.path.join(RES, "sim", "tick*-*.csv")) + glob.glob(os.path.join(RES, "sim", "fx1*-*.csv")) + \
        glob.glob(os.path.join(RES, "sim", "fx2*-*.csv")):
    base_name = os.path.basename(path)[:-4]
    tag, proj = base_name.split("-", 1)
    p = SHORT[proj]
    d = defaultdict(dict)
    wl = defaultdict(dict)
    for c in rows(path):
        # The designs' own $display lines share stdout: keep only the bench's rows.
        if len(c) in (5, 6) and c[0].isdigit() and c[1].isdigit() and c[3].isdigit() and c[4].isdigit():
            d[c[2]][int(c[0])] = (float(c[3]), int(c[4]))
            if len(c) == 6 and c[5].isdigit():
                wl[c[2]][int(c[0])] = float(c[5])
    tickrep[tag][p] = d
    tickwall[tag][p] = wl


def tick_one(d, v):
    """ns a clock over none, paired by chunk, with a bootstrap interval; % of none."""
    base_c = d.get("none", {})
    ks = sorted(set(d.get(v, {})) & set(base_c))
    if len(ks) < 100:
        return None
    diff = [d[v][k][0] - base_c[k][0] for k in ks]
    clk = [base_c[k][1] for k in ks]
    est = sum(diff) / sum(clk)
    # Every chunk is the same length, so a resample's estimate is its mean difference over that length.
    m = len(diff)
    boots = sorted(sum(random.choices(diff, k=m)) / (clk[0] * m) for _ in range(1000))
    b0 = sum(base_c[k][0] for k in ks)
    return est, boots[24], boots[974], sum(diff) / b0, b0 / sum(clk), len(ks)


def tick_tables(title, note, tags, TICKV=TICKV, out_pct=None):
    """The in-process tables for one set of processes; returns (project, variant) -> median ns."""
    out_est = {}
    tp = [x for x in ORDER if any(x in tickrep[t] for t in tags)]
    est_all = defaultdict(list)
    for t in tags:
        for p in tp:
            if p not in tickrep[t]:
                continue
            for v in TICKV:
                r = tick_one(tickrep[t][p], v)
                if r:
                    est_all[(p, v)].append((t, r))
    print(f"### {title}\n")
    print(note + f" {len(tags)} processes ({', '.join(tags)}), each with the variants in another order. "
          "ns per clock over the untraced model, paired by chunk.\n")
    print("#### Across the processes: ns per clock, median of the processes' estimates [lowest, highest]\n")
    body, bodyp = [], []
    for p in tp:
        row, rowp = [p], [p]
        for v in TICKV:
            e = [r for _, r in est_all.get((p, v), [])]
            if not e:
                row.append("-"); rowp.append("-")
                continue
            ns = sorted(x[0] for x in e)
            pc = sorted(x[3] for x in e)
            out_est[(p, v)] = statistics.median(ns)
            if out_pct is not None:
                out_pct[(p, v)] = (statistics.median(pc), pc[0], pc[-1])
            row.append(f"{statistics.median(ns):.1f} [{ns[0]:.1f}, {ns[-1]:.1f}]")
            rowp.append(f"{pct(statistics.median(pc), 1)} [{pct(pc[0], 1)}, {pct(pc[-1], 1)}]")
        body.append(row); bodyp.append(rowp)
    table(["project"] + TICKV, body)
    print("#### The same as % of the untraced model's CPU: median [lowest, highest]; the budget is 5%\n")
    table(["project"] + TICKV, bodyp)
    print("#### Each process: estimate [95% bootstrap interval over its chunks], ns per clock\n")
    body = []
    for p in tp:
        for t in tags:
            if p not in tickrep[t]:
                continue
            row = [p, t]
            n0 = None
            for v in TICKV:
                r = next((r for tt, r in est_all.get((p, v), []) if tt == t), None)
                row.append("-" if r is None else f"{r[0]:.1f} [{r[1]:.1f}, {r[2]:.1f}]")
                if r:
                    n0 = r[4]
            row.insert(2, "-" if n0 is None else f"{n0:.1f}")
            body.append(row)
    table(["project", "process", "untraced ns per clock"] + TICKV, body)
    return out_est


# The sets. Round 1's main and scope sets (fx1a-fx1d, fx1s) and the first round's (tick*) held several
# background writers a process sharing one stdio buffer: their background plans' files failed read-back
# (readback-fx1.csv), so once fix round 2's sets (fx2a-fx2d for the background plans, fx2la-fx2ld for
# vgch2 and lean) are in, no figure is taken from them.
fx_main = sorted(t for t in tickrep if re.fullmatch(r"fx1[a-d]", t))
fx_raw = sorted(t for t in tickrep if re.fullmatch(r"fx1r[a-d]", t))
fx_scope = [t for t in tickrep if t == "fx1s"]
fx2_main = sorted(t for t in tickrep if re.fullmatch(r"fx2[a-d]", t))
fx2l_main = sorted(t for t in tickrep if re.fullmatch(r"fx2l[a-d]", t))
fx2_scope = [t for t in tickrep if t == "fx2s"]
WITHDRAWN = bool(fx2_main and fx2l_main)
tick_est = {}
if tickrep and not WITHDRAWN:
    print("## Simulation check: the Verilator loop, in one process\n")
    two = sorted(t for t in tickrep if t.startswith("tick2"))
    one = sorted(t for t in tickrep if t.startswith("tick") and not t.startswith("tick2"))
    note = ("Each variant's model in one process, stepped in turn by 65,536-clock chunks, 4,000 chunks "
            "(262 M clocks) a process (bench_tick.cpp).")
    if two:
        tick_est = tick_tables("First round, two CPUs a process: the simulation thread on one, the writers' threads on either",
                               note + " As in a job's two-CPU scope. Superseded for R1 by fix round 1's set below; "
                               "its runs writer is the first prototype, whose blocks split differently from vgch2's.", two)
    if one:
        e1 = tick_tables("First round, one CPU a process: the writers' threads share the simulation's core",
                         note + " The background threads share the simulation's core. The thread CPU clock does "
                         "not charge their time to the simulation thread; their cache traffic falls on whichever "
                         "variant is running.", one)
        if not tick_est:
            tick_est = e1
    print("### Replay against the in-process check, ns per clock\n")
    body = []
    tp = [x for x in ORDER if any(x in tickrep[t] for t in tickrep)]
    for p in tp:
        row = [p]
        for w in ("vgch2", "runs", "events", "changelog", "raw"):
            r = est_cache.get((p, w))
            row += ["-" if r is None else f"{r[0]:.2f}", "-" if (p, w) not in tick_est else f"{tick_est[(p, w)]:.2f}"]
        body.append(row)
    table(["project", "vgch2 replay", "vgch2 in-process", "runs replay", "runs in-process", "events replay",
           "events in-process", "changelog replay", "changelog in-process", "raw replay", "raw in-process"], body)

# ---- 3b. fix round 1: the in-process sets that decide R1 -------------------------------------------
fx_pct = {}      # (project, variant) -> (median %, low, high), the R1 figures
fx1_pct, fx2_pct, fx2l_pct = {}, {}, {}
fx_est = {}


def tags_for(v):
    """The sets a variant's figures come from: fix round 2's (fx2a-fx2d for the background plans,
    fx2la-fx2ld for vgch2 and lean) once they have run, else round 1's main set."""
    if WITHDRAWN:
        return fx2_main if v in BGV else fx2l_main
    return fx2_main if fx2_main and v in BGV else fx_main


def lean_ratio(tags):
    """Lean over vgch2, each process's ratio of its estimates, by project."""
    print("Both write the same file (the lean writer's table). Each process's lean estimate over its vgch2 estimate, "
          "ns per clock over the untraced model.\n")
    body = []
    for p in [x for x in ORDER if any(x in tickrep[t] for t in tags)]:
        rs = []
        for t in tags:
            a, b = tick_one(tickrep[t].get(p, {}), "lean"), tick_one(tickrep[t].get(p, {}), "vgch2")
            if a and b and b[0] > 0:
                rs.append(a[0] / b[0])
        rs.sort()
        body.append([p, "-" if not rs else f"{statistics.median(rs):.2f} [{rs[0]:.2f}, {rs[-1]:.2f}]"])
    table(["project", "lean over vgch2"], body)
    print("Only the busy designs' ratios measure a saving. On the quiet ones a changed clock is rare (colorbars: "
          "3.35% of clocks) and both writers cost a few ns a clock, so the ratio is a per-variant offset of about "
          "1-2 ns a clock, within the noise; vgadonut's per-process estimates include negative ones.\n")


if fx_main and WITHDRAWN:
    with contextlib.redirect_stdout(io.StringIO()):   # round 1's published figures, for the comparison only
        tick_tables("", "", fx_main, TICKV=FX1V, out_pct=fx1_pct)
elif fx_main:
    print("## Simulation check, fix round 1: the Verilator loop, every candidate\n")
    note = ("Each variant's model in one process, stepped in turn by 65,536-clock chunks, 4,000 chunks "
            "(262 M clocks) a process (bench_tick.cpp). Two CPUs a process, the simulation thread pinned to the "
            "first. Variants: vgch2 (vgacap's writer), lean (vgacap's writer with push2's slow path replaced), events "
            "(zstd 1 of the events stream), evplanar (events converted to planar by the real converter), planar "
            "(planar written on the simulation thread) and changelog (converted to planar).")
    fx_est = tick_tables("The main set: R1's figures", note, fx_main, TICKV=FX1V, out_pct=fx1_pct)
    fx_pct.update(fx1_pct)
    print("### The lean writer against vgacap's, in the loop: lean over vgch2, each process's ratio, median [lowest, highest]\n")
    lean_ratio(fx_main)
if fx2_main:
    print("## Simulation check, fix round 2: the background plans again, every file read back\n")
    note = ("As fix round 1's main set, after the two fixes (each background writer its own stdio buffer; the "
            "converters' streams in port order). Variants: vgch2 (the reference, and the read-back's), events, "
            "evplanar, planar and changelog. After each pair of sets every trace written is read back "
            "(sim/readback.py) and must match lean's (the readback table).")
    tick_tables("The background plans: R1's figures for them", note, fx2_main, TICKV=FX2V, out_pct=fx2_pct)
    for k, v in fx2_pct.items():
        if k[1] in BGV:
            fx_pct[k] = v
    print("### Round 1 against round 2: % of the untraced model, median of the processes\n")
    print("Round 1: the figures fix round 1 published, from its main set (fx1a-fx1d), whose background plans "
          "wrote corrupt files (the read-back table); shown only for the comparison. Round 2: fx2a-fx2d, every "
          "file read back right. vgch2 ran in both, as the check that the host was alike.\n")
    body = []
    for pj in [x for x in ORDER if any(x in tickrep[t] for t in fx2_main)]:
        for v in FX2V:
            a, b = fx1_pct.get((pj, v)), fx2_pct.get((pj, v))
            if not b:
                continue
            body.append([pj, v, "-" if not a else pct(a[0], 1), pct(b[0], 1), pct(b[1], 1), pct(b[2], 1),
                         "-" if not a else pct(b[0] - a[0], 1)])
    table(["project", "variant", "round 1 (%)", "round 2 (%)", "round 2 lowest (%)", "round 2 highest (%)",
           "round 2 minus round 1 (points)"], body)
if fx2l_main:
    print("## Simulation check, fix round 2: vgch2 and lean, every file read back\n")
    note = ("As the background plans' set, with the variants none, vgch2 and lean only: vgch2's and lean's R1 "
            "from sets whose files all read back (round 1's main set, where they ran beside the corrupt "
            "background writers, did not).")
    tick_tables("vgch2 and lean: R1's figures for them", note, fx2l_main, TICKV=["vgch2", "lean"],
                out_pct=fx2l_pct)
    if WITHDRAWN:
        for k, v in fx2l_pct.items():
            fx_pct[k] = v
    print("### The lean writer against vgacap's, in the loop: lean over vgch2, each process's ratio, median [lowest, highest]\n")
    lean_ratio(fx2l_main)
rb = []
for path in sorted(glob.glob(os.path.join(RES, "sim", "readback-*.csv"))):
    rb += list(csv.DictReader(open(path)))
if rb:
    print("### Read-back of every trace the loop wrote\n")
    print("`sim/readback.py`: each file read with `fsbench read --patterns validate` (vgch2 and lean with vgacap's "
          "reader, the others with the study's), its full hash, samples and uo_out run count against lean's (or "
          "vgch2's, the same file) from the same process; raw's sets, which have neither, against fx2a's vgch2. "
          "Round 1's main and scope sets (fx1a-fx1d, fx1s) are read back too, as an audit: their background "
          "plans' files are the corrupt ones round 2 replaced, and no figure is taken from them. "
          "Files that pass, of those written.\n")
    body = []
    for tag in sorted({r["tag"] for r in rb}):
        for fn in sorted({r["file"] for r in rb if r["tag"] == tag}):
            rs = [r for r in rb if r["tag"] == tag and r["file"] == fn]
            body.append([tag, fn.replace(".trace", ""), len(rs), sum(int(r["pass"]) for r in rs)])
    table(["set", "variant", "files", "pass"], body)
if fx_raw:
    tick_tables("Raw, in processes of its own", "Raw capture with the untraced model only, so that no other "
                "variant shares its process, its L3 traffic or its turn.", fx_raw, TICKV=["raw"], out_pct=fx_pct)
if fx_scope and not WITHDRAWN:
    tick_tables("Unpinned, in a systemd scope with CPUQuota=200%, as a job runs", "One process a project, "
                "the main set's variants, not pinned: the scheduler places its threads, as for a job." +
                (" Its background plans' files were corrupt, as in the main set; fix round 2's scope set follows."
                 if fx2_scope else ""), fx_scope, TICKV=FX1V)
if fx2_scope:
    tick_tables("Fix round 2, unpinned, in a systemd scope with CPUQuota=200%", "One process a project, fix round "
                "2's variants, not pinned, every file read back.", fx2_scope, TICKV=FX2V)

# Stalls and wall time, and the background threads, from each process's stderr.
def sim_stderr(tag, p):
    path = os.path.join(OUT, "sim", tag, LONG[p], "stderr.log")
    if not os.path.exists(path):
        return {}
    out = defaultdict(dict)
    cur = None
    names = {"events-planar": "evplanar"}
    for line in open(path):
        line = line.strip()
        if line.startswith("fs-sim: stalls "):
            k = line.split()[2].rstrip(":")
            cur = names.get(k, k)
            out[cur]["stalls"] = int(line.split()[3])
            out[cur]["stall_s"] = float(line.split()[6])
        elif line.startswith("fs-sim: background ") and cur:
            parts = line.replace(",", "").split()
            out[cur]["bg_s"] = float(parts[5])
            out[cur]["bg_bytes"] = int(parts[8])
        elif line.startswith("fs-sim: finish "):
            k = line.split()[2].rstrip(":")
            out[names.get(k, k)]["finish_s"] = float(line.split()[3])
            cur = None
        elif line.startswith("bench_tick: finish ") and len(line.split()) == 6:
            out[line.split()[2].rstrip(":")]["finish_s"] = float(line.split()[3])
    return out


if fx_main:
    tp = [x for x in ORDER if any(x in tickrep[t] for t in fx_main)]
    print("### Stalls: wall time the simulation thread spent not running, over the untraced model's\n")
    print("Per process, the sum over chunks of (wall - thread CPU) for the variant minus the same for the untraced "
          "model, ns per clock: median of the processes [lowest, highest]. A stall (the simulation thread waiting "
          "for its writer's thread) shows here and nowhere in the CPU figures. The next table counts the waits "
          "themselves." + (" This table and the next four take the background plans from fix round 2's set "
                           "(fx2a-fx2d), vgch2 and lean from fix round 2's lean set (fx2la-fx2ld)." if WITHDRAWN else
                           " This table and the next four take the background plans from fix round 2's set, "
                           "vgch2 and lean from round 1's." if fx2_main else "") + "\n")
    body = []
    for p in tp:
        row = [p]
        for v in FX1V:
            ds = []
            for t in tags_for(v):
                d, w = tickrep[t].get(p, {}), tickwall[t].get(p, {})
                if v not in w or "none" not in w:
                    continue
                ks = sorted(set(w[v]) & set(w["none"]) & set(d[v]) & set(d["none"]))
                if not ks:
                    continue
                gap = sum((w[v][k] - d[v][k][0]) - (w["none"][k] - d["none"][k][0]) for k in ks)
                ds.append(gap / sum(d["none"][k][1] for k in ks))
            row.append("-" if not ds else f"{statistics.median(ds):.2f} [{min(ds):.2f}, {max(ds):.2f}]")
        body.append(row)
    table(["project"] + FX1V, body)
    SV = [v for v in FX1V if v not in ("vgch2", "lean")]
    print("### Hand-overs that waited: the study writers' own count, summed over the processes\n")
    print("A hand-over waits when the writer's thread still holds the other buffer. vgacap's writer (vgch2, lean) "
          "keeps no such count; the table above covers it. The wait's wall time, summed, is in the last column.\n")
    body = []
    for p in tp:
        row, secs = [p], 0.0
        for v in SV:
            st = [sim_stderr(t, p).get(v, {}) for t in tags_for(v)]
            row.append(str(sum(x["stalls"] for x in st if "stalls" in x)) if any("stalls" in x for x in st) else "-")
            secs += sum(x.get("stall_s", 0.0) for x in st)
        row.append(f"{secs:.3f}")
        body.append(row)
    table(["project"] + SV + ["wall s waited, all"], body)
    print("### Each writer's finish (last block, its thread joined), s wall: median of the processes\n")
    body = []
    for p in tp:
        row = [p]
        for v in FX1V:
            fs_ = [sim_stderr(t, p).get(v, {}).get("finish_s") for t in tags_for(v)]
            fs_ = [x for x in fs_ if x is not None]
            row.append("-" if not fs_ else f"{statistics.median(fs_):.3f}")
        body.append(row)
    table(["project"] + FX1V, body)
    print("### Background threads in the loop: CPU s over the 262 M clocks, median of the processes\n")
    body = []
    for p in tp:
        row = [p]
        for v in ["events", "evplanar", "planar", "changelog"]:
            b = [sim_stderr(t, p).get(v, {}).get("bg_s") for t in tags_for(v)]
            b = [x for x in b if x is not None]
            row.append("-" if not b else f"{statistics.median(b):.2f}")
        body.append(row)
    table(["project", "events", "evplanar", "planar", "changelog"], body)
    print("### The loop's window against the whole trace: events bytes per clock (zstd 1)\n")
    print("The loop runs the first 262 M clocks from reset. Its events stream's compressed bytes per clock, against "
          "the whole trace's, show whether that window changes pins as the whole trace does.\n")
    body = []
    for p in tp:
        b = [sim_stderr(t, p).get("events", {}).get("bg_bytes") for t in tags_for("events")]
        b = [x for x in b if x]
        ps = passes.get((p, "events"))
        if not b or not ps:
            continue
        loop_bpc = statistics.median(b) / (4000 * 65536)
        whole = int(ps["primary_bytes"]) / clocks(p)
        body.append([p, f"{loop_bpc:.3g}", f"{whole:.3g}", f"{loop_bpc / whole:.2f}"])
    table(["project", "loop window (B a clock)", "whole trace (B a clock)", "ratio"], body)

# Memory per plan in the loop (sim/run_mem.py).
memcsv = os.path.join(RES, "sim", "mem.csv")
if os.path.exists(memcsv):
    mem = {(SHORT[r["project"]], r["variants"]): r for r in csv.DictReader(open(memcsv))}
    MV = ["vgch2", "lean", "events", "evplanar", "planar", "changelog", "raw"]
    print("### Memory: each plan's writer, peak resident memory over a second untraced model's, MiB\n")
    print("`sim/run_mem.py`: bench_tick, 600 chunks (39 M clocks: every plan hands over both buffer sets more than "
          "once), with the untraced model alone, with two untraced models, and with the untraced model and one plan. "
          "bench_tick gives every variant a model of its own, so a plan's run holds two models: its writer's figure "
          "is that run's peak RSS (getrusage) minus the two-model run's. The second model's own memory (two models "
          "minus one) is given beside it. One process each, pinned to one CPU. A job holds one model and one plan; "
          "at 22 jobs a host multiply by 22.\n")
    body = []

    def kib(pj, v):
        r = mem.get((pj, v))
        return int(r["max_rss_kib"]) if r and r["exit"] == "0" and r["max_rss_kib"] else None

    for pj in [x for x in ORDER if any(k[0] == x for k in mem)]:
        one, two = kib(pj, "none"), kib(pj, "none,none")
        row = [pj, "-" if one is None else f"{one / 1024:.0f}",
               "-" if one is None or two is None else f"{(two - one) / 1024:.0f}"]
        for v in MV:
            r = kib(pj, f"none,{v}")
            row.append("-" if r is None or two is None else f"{(r - two) / 1024:.0f}")
        body.append(row)
    table(["project", "untraced model alone", "a second untraced model"] + MV, body)

# A full-pin export to FST (run_export.py).
expcsv = os.path.join(RES, "export", "export.csv")
if os.path.exists(expcsv):
    ex = defaultdict(list)
    for r in csv.DictReader(open(expcsv)):
        ex[(SHORT[r["project"]], r["tool"])].append(r)
    print("### A full-pin export to FST: vgch2's file against planar's, median of the reps\n")
    print("`run_export.py`: the seven ports to FST (LZ4, libfst's parallel mode), as vgacap-export --no-clk writes "
          "them, by the study's export (`fsbench read --patterns export`) from vgch2's file with vgacap's reader and "
          "from planar's with the study's; and vgacap-export itself on vgch2's file, which also writes the decoded "
          "signals: a calibration, not a pair. Two CPUs a job, the reps interleaved. Process s counts libfst's "
          "compressor thread. Read back: the first rep's FST hashes as its source does.\n")
    body = []
    for pj in [x for x in ORDER if any(k[0] == x for k in ex)]:
        for tool in ("vgch2", "planar", "vgacap-export"):
            rs = [r for r in ex.get((pj, tool), []) if r["exit"] == "0"]
            if not rs:
                continue
            med = lambda k: statistics.median(float(r[k]) for r in rs if r[k])
            rb0 = [r for r in rs if r["fst_hash"]]
            ps_ = sorted(float(r["process_s"]) for r in rs)
            body.append([pj, tool, len(rs), f"{med('process_s'):.1f}", f"{ps_[0]:.1f}", f"{ps_[-1]:.1f}",
                         f"{med('wall_s'):.1f}",
                         "-" if tool == "vgacap-export" else f"{med('reader_thread_s'):.1f}",
                         f"{med('max_rss_kib') / 1024:.0f}", f"{med('fst_bytes') / 1e6:.1f}",
                         "-" if not rb0 else ("same" if all(r["fst_hash"] == r["source_hash"] for r in rb0) else "DIFFERENT")])
    table(["project", "source", "reps", "process CPU s", "process CPU s, lowest rep", "process CPU s, highest rep",
           "wall s", "reading thread s", "max RSS MiB", "FST MB", "read back"], body)
    print("### The same export, planar over vgch2 in each rep: process CPU, median [lowest, highest]\n")
    print("Each rep's planar job over the same rep's vgch2 job, which ran beside it.\n")
    body = []
    for pj in [x for x in ORDER if any(k[0] == x for k in ex)]:
        a = {r["rep"]: float(r["process_s"]) for r in ex.get((pj, "planar"), []) if r["exit"] == "0"}
        b = {r["rep"]: float(r["process_s"]) for r in ex.get((pj, "vgch2"), []) if r["exit"] == "0"}
        rs = sorted(a[k] / b[k] for k in a if k in b and b[k] > 0)
        if rs:
            body.append([pj, len(rs), f"{statistics.median(rs):.2f} [{rs[0]:.2f}, {rs[-1]:.2f}]"])
    table(["project", "reps", "planar over vgch2"], body)

simcsv = os.path.join(RES, "sim", "sim.csv")
if os.path.exists(simcsv):
    print("## Simulation check, side by side: whole testbench runs (too noisy, not used)\n")
    sim = defaultdict(lambda: defaultdict(dict))
    sload = []
    for r in csv.DictReader(open(simcsv)):
        if r["exit"] != "0" or not r["main_thread_s"]:
            continue
        p = SHORT[r["project"]]
        sim[p][r["variant"]][int(r["round"])] = r
        sload.append(float(r["load1"]))
    # Each traced run against the untraced run of the same binary in the same round.
    pairs = [("prod-vgch2", "prod-none"), ("study-vgch2", "study-none"), ("study-events", "study-none"),
             ("study-raw", "study-none")]
    simp = [x for x in ORDER if x in sim]
    body_pct, body_ns, body_bg, body_cmp = [], [], [], []
    for p in simp:
        rp, rn, rb = [p], [p], [p]
        for v, b in pairs:
            d, dn, bg = [], [], []
            for rnd, r in sim[p][v].items():
                q = sim[p][b].get(rnd)
                if not q:
                    continue
                t, t0 = float(r["main_thread_s"]), float(q["main_thread_s"])
                d.append(t / t0 - 1)
                if q.get("clocks"):
                    dn.append((t - t0) * 1e9 / int(q["clocks"]))
                bg.append(float(r["process_s"]) - t)
            if d:
                rp.append(f"{pct(statistics.median(d), 2)} [{pct(min(d), 2)}, {pct(max(d), 2)}]")
                rn.append(f"{statistics.median(dn):.2f} [{min(dn):.2f}, {max(dn):.2f}]" if dn else "-")
                rb.append(f"{statistics.median(bg):.2f}")
            else:
                rp.append("-"); rn.append("-"); rb.append("-")
        body_pct.append(rp); body_ns.append(rn); body_bg.append(rb)
        # The study binary's untraced run against prod's: what the binary change alone costs.
        d = [float(sim[p]["study-none"][k]["main_thread_s"]) / float(sim[p]["prod-none"][k]["main_thread_s"]) - 1
             for k in sim[p]["study-none"] if k in sim[p]["prod-none"]]
        n0 = [float(sim[p]["prod-none"][k]["main_thread_s"]) * 1e9 / int(sim[p]["prod-none"][k]["clocks"])
              for k in sim[p]["prod-none"] if sim[p]["prod-none"][k].get("clocks")]
        body_cmp.append([p, len(sim[p]["prod-none"]), f"{statistics.median(n0):.1f}" if n0 else "-",
                         f"{pct(statistics.median(d), 2)} [{pct(min(d), 2)}, {pct(max(d), 2)}]" if d else "-"])
    heads = [f"{v} over {b}" for v, b in pairs]
    print("### Untraced runs\n")
    table(["project", "rounds", "untraced sim thread ns per clock (median)", "study binary over prod binary, untraced (%)"], body_cmp)
    print("### Simulation-thread CPU over the same binary's untraced run, same round, %: median [min, max]\n")
    table(["project"] + heads, body_pct)
    print("### The same in ns per clock: median [min, max]\n")
    table(["project"] + heads, body_ns)
    print("### Background threads' CPU (process CPU minus the simulation thread), s a run, median\n")
    table(["project"] + heads, body_bg)
    print("### Replay against simulation, ns per clock over untraced\n")
    body = []
    for p in simp:
        row = [p]
        for w, v in (("vgch2", "study-vgch2"), ("events", "study-events"), ("raw", "study-raw")):
            r = est_cache.get((p, w))
            dn = [(float(sim[p][v][k]["main_thread_s"]) - float(sim[p]["study-none"][k]["main_thread_s"])) * 1e9 /
                  int(sim[p]["study-none"][k]["clocks"]) for k in sim[p][v] if k in sim[p]["study-none"]
                  and sim[p]["study-none"][k].get("clocks")]
            row.append("-" if r is None else f"{r[0]:.2f}")
            row.append(f"{statistics.median(dn):.2f}" if dn else "-")
        body.append(row)
    table(["project", "vgch2 replay", "vgch2 sim", "events replay", "events sim", "raw replay", "raw sim"], body)
    if sload:
        print(f"Load per CPU during the check: {min(sload) / 88:.2f} to {max(sload) / 88:.2f}.\n")

# ---- 4. sizes and background CPU ---------------------------------------------------------------
print("## File size and background CPU\n")


def cand_rows(p):
    """(candidate name, total bytes, background CPU s, note) for the primary configurations."""
    out = []
    e = enc.get((p, "vgch2"), {})
    ps = passes.get((p, "vgch2"))
    if ps and ("identity", "zstd1") in e:
        out.append(("vgch2 (zstd 1)", int(ps["file_bytes"]), float(e[("identity", "zstd1")]["est_bg_ns"]) / 1e9))
    for w in ("runs", "planar", "events"):
        e = enc.get((p, w), {})
        ps = passes.get((p, w))
        if ps and ("identity", "zstd1") in e:
            b = int(ps["primary_bytes"]) if w != "runs" else float(e[("identity", "zstd1")]["est_comp_bytes"])
            out.append((f"{w} (zstd 1)", b, float(e[("identity", "zstd1")]["est_bg_ns"]) / 1e9))
    e = enc.get((p, "raw"), {})
    ps = passes.get((p, "raw"))
    if ps and ("xor6", "zstd1") in e:
        out.append(("raw xor6 (zstd 1)", int(ps["primary_bytes"]), float(e[("xor6", "zstd1")]["est_bg_ns"]) / 1e9))
    for job, name in (("changelog", "changelog to planar (zstd 1)"), ("evplanar", "events to planar (zstd 1)"),
                      ("rawplanar", "raw to planar (zstd 1)")):
        e = enc.get((p, job), {})
        ps = passes.get((p, job))
        key = {"changelog": ("changelog-planar", "zstd1"), "evplanar": ("events-planar", "zstd1")}.get(job, ("planar-runs", "zstd1"))
        if ps and key in e:
            out.append((name, int(ps["primary_bytes"]), float(e[key]["est_bg_ns"]) / 1e9))
    e = enc.get((p, "sigrok"), {})
    if ("pack6clk", "deflate6") in e:
        out.append(("sigrok-like (deflate 6)", float(e[("pack6clk", "deflate6")]["est_comp_bytes"]),
                    float(e[("pack6clk", "deflate6")]["est_bg_ns"]) / 1e9))
    e = enc.get((p, "vcd"), {})
    ps = passes.get((p, "vcd"))
    if ps and ("identity", "zstd1") in e:
        k = clocks(p) / int(ps["processed_clocks"])
        out.append(("VCD (zstd 1)", float(e[("identity", "zstd1")]["est_comp_bytes"]) * k,
                    float(e[("identity", "zstd1")]["est_bg_ns"]) * k / 1e9))
    for job, name in (("fst-lz4", "FST (LZ4)"), ("fst-zlib", "FST (zlib)"), ("fst_clk", "FST with clk (LZ4)"),
                      ("lxt2", "LXT2")):
        ps = passes.get((p, job))
        if ps:
            k = clocks(p) / int(ps["processed_clocks"])
            bgs = (float(ps["process_ns"]) - float(ps["thread_ns"])) * k / 1e9
            out.append((name, int(ps["file_bytes"]) * k, bgs if job != "lxt2" else float("nan")))
    return out


print("### Total bytes, MB (projected to the whole trace where sampled)\n")
names_c = ["vgch2 (zstd 1)", "runs (zstd 1)", "planar (zstd 1)", "events (zstd 1)", "events to planar (zstd 1)",
           "changelog to planar (zstd 1)",
           "raw xor6 (zstd 1)", "raw to planar (zstd 1)", "sigrok-like (deflate 6)", "VCD (zstd 1)", "FST (LZ4)",
           "FST (zlib)", "FST with clk (LZ4)", "LXT2"]
body = []
for p in projects:
    cr = cand_rows(p)
    d = {c[0]: c for c in cr}
    body.append([p] + [f(d[n][1] / 1e6, 2) if n in d else "-" for n in (names_c or [])])
if names_c:
    table(["project"] + names_c, body)

print("### Bytes per clock\n")
body = []
for p in projects:
    d = {c[0]: c for c in cand_rows(p)}
    body.append([p] + [f"{d[n][1] / clocks(p):.4f}" if n in d else "-" for n in (names_c or [])])
if names_c:
    table(["project"] + names_c, body)

for as_pct in (False, True):
    print("### Background CPU, " + ("as % of the untraced simulation's CPU\n" if as_pct else "s for the whole trace\n"))
    body = []
    for p in projects:
        d = {c[0]: c for c in cand_rows(p)}
        cpu = float(base[p]["cpu_without_s"])
        row = [p, f(cpu, 0)]
        for n in (names_c or []):
            if n in d and d[n][2] == d[n][2]:
                row.append(pct(d[n][2] / cpu, 2) if as_pct else f"{d[n][2]:.2f}")
            else:
                row.append("-")
        body.append(row)
    if names_c:
        table(["project", "untraced CPU (s)"] + names_c, body)

# ---- background CPU, three more times (encode2) -----------------------------------------------------
enc2 = defaultdict(list)   # (project, candidate) -> [est_bg_ns s]
E2KEY = {"vgch2": ("identity", "zstd1"), "planar": ("identity", "zstd1"), "events": ("identity", "zstd1"),
         "evplanar": ("events-planar", "zstd1"), "changelog": ("changelog-planar", "zstd1"),
         "rawplanar": ("planar-runs", "zstd1"), "raw": ("xor6", "zstd1")}
E2NAME = {"vgch2": "vgch2 (zstd 1)", "planar": "planar (zstd 1)", "events": "events (zstd 1)",
          "evplanar": "events to planar (zstd 1)", "changelog": "changelog to planar (zstd 1)",
          "rawplanar": "raw to planar (zstd 1)", "raw": "raw xor6 (zstd 1)"}
for path in glob.glob(os.path.join(RES, "encode2", "*.csv")):
    job = os.path.basename(path)[:-4]
    proj_long, cand, _rep = job.rsplit(".", 2)
    for line in open(path):
        c = line.rstrip("\n").split(",")
        if len(c) == 15 and c[0] != "project" and (c[2], c[3]) == E2KEY[cand]:
            enc2[(SHORT[proj_long], cand)].append(float(c[13]) / 1e9)


def bg_all(p, name):
    """Every whole-trace measurement of a candidate's background CPU, s: the first round's and encode2's."""
    d = {c[0]: c for c in cand_rows(p)}
    v = [d[name][2]] if name in d and d[name][2] == d[name][2] else []
    for k, n in E2NAME.items():
        if n == name:
            v += enc2.get((p, k), [])
    return v


def bg_of(p, name, how="median"):
    """Background CPU s for a candidate: encode2's median (else the first round's), or the lowest of all."""
    v = bg_all(p, name)
    if how == "lowest":
        return min(v) if v else None
    for k, n in E2NAME.items():
        if n == name and enc2.get((p, k)):
            return statistics.median(enc2[(p, k)])
    return v[0] if v else None


if enc2:
    print("### Background CPU, measured again: three runs, s for the whole trace, median [lowest, highest]\n")
    print("`results/encode2`: each candidate's configuration alone (zstd 1, every block, no compressor matrix "
          "beside it), three times, the runs apart in time. The R2 tables use these medians.\n")
    body = []
    for p in projects:
        row = [p]
        for k in E2KEY:
            v = sorted(enc2.get((p, k), []))
            row.append("-" if not v else f"{statistics.median(v):.2f} [{v[0]:.2f}, {v[-1]:.2f}]")
        body.append(row)
    table(["project"] + [E2NAME[k] for k in E2KEY], body)
    warm = {}
    for path in sorted(glob.glob(os.path.join(RES, "warm", "*.csv"))):
        name = os.path.basename(path)[:-4]
        proj_long, w, how = name.rsplit(".", 2)
        for line in open(path):
            c = line.rstrip("\n").split(",")
            if len(c) == 15 and c[0] != "project" and (c[2], c[3]) == ("identity", "zstd1"):
                warm[(SHORT[proj_long], w, how)] = float(c[13]) / 1e9
    if warm:
        print("### zstd1's CPU with the block cold and warm in cache, s for the whole trace\n")
        print("`warm_cache.sh`: zstd1 alone on each block (cold, as encode2), and zstd1 after zstd-5 and zstd-1 have "
              "read the same block (warm, as the first round's matrix ran it). Run at once, each on its own CPU.\n")
        body = []
        for (p, w) in sorted({(k[0], k[1]) for k in warm}, key=lambda x: (ORDER.index(x[0]), x[1])):
            c, h = warm.get((p, w, "cold")), warm.get((p, w, "warm"))
            body.append([p, w, "-" if c is None else f"{c:.2f}", "-" if h is None else f"{h:.2f}",
                         "-" if not (c and h) else f"{c / h:.2f}"])
        table(["project", "writer", "cold", "warm", "cold over warm"], body)
    if fx_main:
        LOOP_CLOCKS = 4000 * 65536
        print("### Background CPU three ways, s for the whole trace: the loop's, scaled, against the whole-trace runs\n")
        print("The loop's figure is its background thread's CPU over its 262 M clocks, beside a running simulation, "
              "scaled to the trace's clocks (median of the processes); the window is representative (the loop's "
              "window table). The first round's and encode2's are the whole trace, in fsbench.\n")
        body = []
        for p in [x for x in ORDER if any(x in tickrep[t] for t in fx_main)]:
            for v in ("events", "evplanar", "planar", "changelog"):
                b = [sim_stderr(t, p).get(v, {}).get("bg_s") for t in tags_for(v)]
                b = [x for x in b if x is not None]
                d = {c[0]: c for c in cand_rows(p)}
                e2 = sorted(enc2.get((p, v), []))
                fr = d.get(E2NAME[v])
                body.append([p, v, "-" if not b else f"{statistics.median(b) * clocks(p) / LOOP_CLOCKS:.2f}",
                             "-" if not fr else f"{fr[2]:.2f}",
                             "-" if not e2 else f"{e2[0]:.2f}", "-" if not e2 else f"{statistics.median(e2):.2f}",
                             "-" if not e2 else f"{e2[-1]:.2f}"])
        table(["project", "candidate", "loop, scaled", "first round", "encode2 lowest", "encode2 median",
               "encode2 highest"], body)
    print("### Re-encoding a published trace to planar: encode2's planar pass, wall s, median of 3\n")
    print("The pass reads the published trace with vgacap's reader, expands it to the testbench's raw images and "
          "writes planar: a migration's re-encode, one CPU a trace.\n")
    body = []
    for p in projects:
        w = []
        for path in glob.glob(os.path.join(RES, "encode2", f"{LONG[p]}.planar.r*.csv")):
            for line in open(path):
                if line.startswith("#pass,"):
                    kv = dict(x.split("=", 1) for x in line.strip().split(",")[4:] if "=" in x)
                    w.append(int(kv["wall_ns"]) / 1e9)
        body.append([p, "-" if not w else f"{statistics.median(w):.0f}"])
    table(["project", "wall s"], body)
    print("### The first round's single measurement of the same, s (the compressor matrix in the same pass)\n")
    body = []
    for p in projects:
        d = {c[0]: c for c in cand_rows(p)}
        body.append([p] + [f"{d[E2NAME[k]][2]:.2f}" if E2NAME[k] in d else "-" for k in E2KEY])
    table(["project"] + [E2NAME[k] for k in E2KEY], body)

# The compressor matrix.
COMPS = ["none", "zstd-5", "zstd-1", "zstd1", "zstd1-nocheck", "zstd3", "zstd9", "zstd1-long27", "lz4", "lz4hc9",
         "deflate1", "deflate6"]
for w in ("vgch2", "events", "planar"):
    for what, key, scale in (("MB", "est_comp_bytes", 1e6), ("background CPU s", "est_bg_ns", 1e9)):
        print(f"### Compressors on {w}'s blocks: {what}\n")
        body = []
        for p in projects:
            e = enc.get((p, w), {})
            row = [p]
            for c in COMPS[1:]:
                r = e.get(("identity", c))
                row.append("-" if r is None else f"{float(r[key]) / scale:.2f}")
            body.append(row)
        table(["project"] + COMPS[1:], body)
    print("Uncompressed, MB: " + ", ".join(
        f"{p} {sum(int(r['raw_bytes_measured']) for r in [enc[(p, w)][('identity', 'zstd1')]]) / 1e6 * (int(enc[(p, w)][('identity', 'zstd1')]['blocks']) / max(1, int(enc[(p, w)][('identity', 'zstd1')]['blocks_measured']))):,.1f}"
        for p in projects if (p, w) in enc and ("identity", "zstd1") in enc[(p, w)]) + ".\n")

TR = ["pack6", "xor6", "xor-byteplanes", "xor-bitplanes"]
RC = ["zstd-1", "zstd1", "zstd3", "lz4"]
for what, key, scale in (("MB", "est_comp_bytes", 1e6), ("background CPU s", "est_bg_ns", 1e9)):
    print(f"### Raw candidate: transforms x compressors, {what} (projected)\n")
    body = []
    for p in projects:
        e = enc.get((p, "raw"), {})
        row = [p]
        for t in TR:
            for c in RC:
                r = e.get((t, c))
                row.append("-" if r is None else f"{float(r[key]) / scale:,.1f}")
        body.append(row)
    table(["project"] + [f"{t} {c}" for t in TR for c in RC], body)
print("### Raw candidate: the transform's share of that CPU, s (projected)\n")
body = []
for p in projects:
    e = enc.get((p, "raw"), {})
    row = [p]
    for t in TR:
        r = e.get((t, "zstd1"))
        if r is None:
            row.append("-")
            continue
        k = int(r["samples"]) / max(1, int(r["samples_measured"]))
        row.append(f"{float(r['transform_ns_measured']) * k / 1e9:.1f}")
    body.append(row)
table(["project"] + TR, body)

# ---- 5. readers --------------------------------------------------------------------------------
print("## Read and decode\n")
rd = defaultdict(lambda: defaultdict(list))   # (project, format) -> pattern -> [ns]
rdv = {}
fmt_of = lambda kind, file: ("vgch2" if kind == "vgch2" else "FST (LZ4)" if kind == "fst" else
                             "changelog to planar" if ".changelog." in file else
                             "events to planar" if ".events-planar." in file else
                             "raw to planar" if ".planar-runs." in file else
                             "planar" if ".planar." in file else "events" if ".events." in file else
                             "raw xor6" if ".raw." in file else file)
for path in glob.glob(os.path.join(RES, "read", "*.csv")) + glob.glob(os.path.join(RES, "validate", "*.csv")):
    for c in rows(path):
        if len(c) < 10 or c[1] == "project":
            continue
        rnd, proj, kind, file, pat, rep, ns, runs, samples, h = c[:10]
        p = SHORT.get(proj)
        if p is None:
            continue
        fm = fmt_of(kind, file)
        if rnd == "v":
            rdv[(p, fm, pat)] = (h, int(samples), int(runs))
        else:
            rd[(p, fm)][pat].append(float(ns))
FMTS = ["vgch2", "planar", "events", "raw xor6", "FST (LZ4)"]
VFMTS = ["changelog to planar", "events to planar", "raw to planar"]
if rdv:
    print("### Validation: every reader gives the same runs (canonical hash of full runs and of uo_out runs)\n")
    body = []
    for p in projects:
        ref = (rdv.get((p, "vgch2", "validate-full")), rdv.get((p, "vgch2", "validate-port0")))
        row = [p]
        for fm in FMTS + VFMTS:
            a, b = rdv.get((p, fm, "validate-full")), rdv.get((p, fm, "validate-port0"))
            if a is None:
                row.append("-")
            else:
                row.append("same" if ref[0] and a[0] == ref[0][0] and b and b[0] == ref[1][0] and a[1] == ref[0][1]
                           else f"DIFFERENT {a[0]}")
        body.append(row)
    table(["project"] + FMTS + VFMTS, body)
for pat, title, unit in (("scan", "Full scan to a no-op consumer (the samplers' trace decode), ns per clock", "clk"),
                         ("port0", "uo_out alone (an export of one pin group), ns per clock", "clk"),
                         ("open", "Open, ms", "ms"),
                         ("seek", "One window of 420,000 clocks from a fresh open (export --from/--to, slice), ms", "ms")):
    print(f"### {title}: median of 5 rounds [min, max]\n")
    body = []
    for p in projects:
        row = [p]
        for fm in FMTS:
            v = rd.get((p, fm), {}).get(pat)
            if not v:
                row.append("-")
                continue
            if unit == "clk":
                vv = [x / clocks(p) for x in v]
                row.append(f"{statistics.median(vv):.2f} [{min(vv):.2f}, {max(vv):.2f}]")
            else:
                vv = [x / 1e6 for x in v]
                row.append(f"{statistics.median(vv):.2f} [{min(vv):.2f}, {max(vv):.2f}]")
        body.append(row)
    table(["project"] + FMTS, body)

# ---- 5a. the readers of a job (read2) ------------------------------------------------------------------
rd2 = defaultdict(lambda: defaultdict(list))   # (project, format) -> pattern -> [ns]
rd2v = {}
fmt2 = lambda kind, file: ("vgch2" if kind == "vgch2" else "vgch2, lazy open" if kind == "vgch2lazy" else
                           fmt_of(kind, file))
for path in glob.glob(os.path.join(RES, "read2", "*.csv")):
    for c in rows(path):
        if len(c) < 10 or c[1] == "project":
            continue
        rnd, proj, kind, file, pat, rep_, ns, runs, samples, h = c[:10]
        p = SHORT.get(proj)
        if p is None:
            continue
        fm = fmt2(kind, file)
        rd2[(p, fm)][pat].append(float(ns))
        if pat in ("info", "infoscan", "sample", "uohash"):
            rd2v[(p, fm, pat)] = (h, int(samples), int(runs))
F2 = ["vgch2", "vgch2, lazy open", "planar", "events to planar", "changelog to planar", "raw to planar", "events",
      "raw xor6"]
if rd2:
    print("## The readers of a job\n")
    print("A job's derive step (tinytapeout-vga-videos `ttvga/harness/derive.py`) reads the trace three times: "
          "`tracetool info` (every port's change count: a full scan), `vgacap-sample` (all samplers in one pass; "
          "it reads `uo_out` and `rst_n`) and `tracetool uohash` (`uo_out` over the first 2^25 clocks). `vgacap-export` "
          "reads every pin when it is run (the full scan table). `results/read2`: 3 rounds, the formats rotated, "
          "each file its candidate's own. info on a planar file reads each stream on its own; infoscan is the "
          "same count over a merged full scan, as tracetool's code does it today.\n")
    print("### Validation: the same counts and runs as vgacap's reader\n")
    body = []
    for p in projects:
        ref = {pt: rd2v.get((p, "vgch2", pt)) for pt in ("info", "sample", "uohash")}
        row = [p]
        for fm in F2[1:]:
            ok = []
            for pt in ("info", "infoscan", "sample", "uohash"):
                a = rd2v.get((p, fm, pt))
                r0 = ref["info"] if pt == "infoscan" else ref[pt]
                if a is None or r0 is None:
                    continue
                same = a[0] == r0[0] if pt in ("info", "infoscan") else a[2] == r0[2]
                ok.append(same and a[1] == r0[1])
            row.append("-" if not ok else "same" if all(ok) else "DIFFERENT")
        body.append(row)
    table(["project"] + F2[1:], body)
    for pat, title, unit in (("info", "tracetool info, s", "s"), ("infoscan", "tracetool info as a merged full scan, s", "s"),
                             ("sample", "vgacap-sample's pass (uo_out and rst_n), s", "s"),
                             ("uohash", "tracetool uohash (open + uo_out over 2^25 clocks), ms", "ms"),
                             ("open", "Open, ms", "ms"),
                             ("seek", "One window of 420,000 clocks from a fresh open, ms", "ms")):
        print(f"### {title}: median of the rounds [min, max]\n")
        body = []
        for p in projects:
            row = [p]
            for fm in F2:
                v = rd2.get((p, fm), {}).get(pat)
                if not v:
                    row.append("-")
                    continue
                k = 1e9 if unit == "s" else 1e6
                vv = [x / k for x in v]
                d = 2 if unit == "s" else 1
                row.append(f"{statistics.median(vv):.{d}f} [{min(vv):.{d}f}, {max(vv):.{d}f}]")
            body.append(row)
        table(["project"] + F2, body)

# ---- 5b. the trace's whole post-simulation cost ------------------------------------------------------
# R2 counts the compressor thread and everything after the simulation. The
# trace's part of "after" is every read of it a job makes: tracetool info,
# vgacap-sample's pass, tracetool uohash, and their opens.
R2PLANS = [("vgch2 (zstd 1)", "vgch2"), ("events (zstd 1)", "events"), ("raw xor6 (zstd 1)", "raw xor6"),
           ("planar (zstd 1)", "planar"), ("events to planar (zstd 1)", "events to planar"),
           ("changelog to planar (zstd 1)", "changelog to planar"), ("raw to planar (zstd 1)", "raw to planar")]
r2 = {}   # (project, candidate, info variant, "median" or "lowest") -> (% , bg s, reads s)


def r2_of(p, name, fm, info_pat, how="median"):
    """info_pat "none": a header-only tracetool info, which is an open (the job reads no change count)."""
    bg = bg_of(p, name, how)
    d = rd2.get((p, fm), {})
    need = ("sample", "uohash", "open") + (() if info_pat == "none" else (info_pat,))
    if bg is None or not all(d.get(k) for k in need):
        return None
    info = 0 if info_pat == "none" else statistics.median(d[info_pat])
    reads = (info + statistics.median(d["sample"]) + statistics.median(d["uohash"]) +
             2 * statistics.median(d["open"])) / 1e9
    cpu = float(base[p]["cpu_without_s"])
    return (bg + reads) / cpu, bg, reads


if rd2:
    print("## The trace's share of R2: background CPU and every read of a job\n")
    print("Background CPU plus `tracetool info`, `vgacap-sample`'s pass and `tracetool uohash`, and two more opens "
          "(each of the three opens the file; uohash's time includes its own). % of the pilot's untraced simulation "
          "CPU: the trace's own share, which leaves the rest of the 5% to the samplers, hashes and PNGs. The same "
          "compression work measured up to 2.5 times apart from one run to another (the encode2 tables), so the share is "
          "given at encode2's median and at the lowest background figure measured (the first round's and encode2's "
          "three). The reads' part follows, in s.\n")
    for info_pat, title in (("info", "tracetool info counting each planar stream on its own"),
                            ("infoscan", "tracetool info as it is: a merged full scan"),
                            ("none", "a header-only tracetool info (no scan; the job reads only blocks, clocks "
                             "and open.slice_*)")):
        for how in ("median", "lowest"):
            print(f"### With {title}, background at its {how}: the trace's share of R2, %\n")
            body = []
            for p in projects:
                row = [p]
                for name, fm in R2PLANS:
                    ip = info_pat if info_pat == "none" or fm not in ("vgch2", "events", "raw xor6") else "info"
                    r = r2_of(p, name, fm, ip, how)
                    r2[(p, name, info_pat, how)] = r
                    row.append("-" if r is None else pct(r[0], 1))
                body.append(row)
            table(["project"] + [n for n, _ in R2PLANS], body)
        print(f"### With {title}: the reads alone, s for the whole trace\n")
        body = []
        for p in projects:
            row = [p]
            for name, fm in R2PLANS:
                r = r2.get((p, name, info_pat, "median"))
                row.append("-" if r is None else f"{r[2]:.1f}")
            body.append(row)
        table(["project"] + [n for n, _ in R2PLANS], body)

# ---- 5c. R1 and R2 together, for each plan -------------------------------------------------------------
if rd2 and fx_pct:
    print("## Every plan against R1 and R2 together\n")
    print("R1: the simulation thread's cost in the loop (median of the processes; " + (
          "vgch2 and lean from fix round 2's lean set, the background plans from its background set, "
          if WITHDRAWN else "vgch2 and lean from fix round 1's main set, the background plans from fix round 2's, ") +
          "raw from its own processes), % of the untraced "
          "model; the spec's budget is 5%. R2: the trace's share, background at encode2's median, with tracetool "
          "info as a scan that counts planar streams on their own, and with a header-only info (no scan). The "
          "spec's R2 budget is 5% for everything after the simulation; the 1% bar for the trace's own share is "
          "this study's choice, not the spec's. Lean writes vgch2's file, so its R2 is vgch2's.\n")
    PLANS = [("vgch2 (current)", "vgch2", "vgch2 (zstd 1)"), ("lean (vgch2's file)", "lean", "vgch2 (zstd 1)"),
             ("events, stored as events", "events", "events (zstd 1)"),
             ("events, stored as planar", "evplanar", "events to planar (zstd 1)"),
             ("changelog, stored as planar", "changelog", "changelog to planar (zstd 1)"),
             ("planar written on the simulation thread", "planar", "planar (zstd 1)"),
             ("raw, stored as planar", "raw", "raw to planar (zstd 1)")]
    lp = [x for x in ORDER if any((x, v) in fx_pct for _, v, _ in PLANS)]
    body = []
    for name, v, r2n in PLANS:
        row = [name]
        for p in lp:
            a = fx_pct.get((p, v))
            b = r2.get((p, r2n, "info", "median"))
            c = r2.get((p, r2n, "none", "median"))
            row += ["-" if a is None else pct(a[0], 1), "-" if b is None else pct(b[0], 1),
                    "-" if c is None else pct(c[0], 1)]
        body.append(row)
    table(["plan"] + [f"{p} {r} (%)" for p in lp for r in ("R1", "R2", "R2 no scan")], body)
    BUSY = [x for x in ("nyancat", "rle_vga", "atari2600") if x in lp]
    QUIET = [x for x in lp if x not in BUSY]
    print("### Pass or fail, per plan\n")
    print(f"Busy: {', '.join(BUSY)}. Quiet: {', '.join(QUIET)}. R2 is checked at the background figure most in its "
          "favour (the lowest measured) and at encode2's median; tracetool info counts planar streams on their own, "
          "or (no scan) is header-only. The 1% bar is this study's own, not the spec's.\n")

    def ok(vals, lim):
        return "yes" if vals and all(x is not None and x <= lim for x in vals) else "no"

    body = []
    for name, v, r2n in PLANS:
        r1b = [fx_pct[(p, v)][0] if (p, v) in fx_pct else None for p in BUSY]
        r1q = [fx_pct[(p, v)][0] if (p, v) in fx_pct else None for p in QUIET]
        lo = [r2[(p, r2n, "info", "lowest")][0] if r2.get((p, r2n, "info", "lowest")) else None for p in BUSY]
        md = [r2[(p, r2n, "info", "median")][0] if r2.get((p, r2n, "info", "median")) else None for p in BUSY]
        ns = [r2[(p, r2n, "none", "median")][0] if r2.get((p, r2n, "none", "median")) else None for p in BUSY]
        both = "yes" if ok(r1b, 0.05) == "yes" and ok(lo, 0.01) == "yes" else "no"
        both5 = "yes" if ok(r1b, 0.05) == "yes" and ok(ns, 0.05) == "yes" else "no"
        body.append([name, ok(r1b, 0.05), ok(r1q, 0.05), ok(lo, 0.01), ok(md, 0.01), ok(lo, 0.05), ok(md, 0.05),
                     ok(ns, 0.01), ok(ns, 0.05), both, both5])
    table(["plan", "R1 at most 5%, busy", "R1 at most 5%, quiet", "R2 at most 1%, busy, lowest",
           "R2 at most 1%, busy, median", "R2 at most 5%, busy, lowest", "R2 at most 5%, busy, median",
           "R2 no scan at most 1%, busy", "R2 no scan at most 5%, busy",
           "R1 and R2 (1%, lowest), busy", "R1 and R2 (5%, no scan), busy"], body)

# ---- 6. robustness -------------------------------------------------------------------------------
rob = []
for path in sorted(glob.glob(os.path.join(RES, "robust", "*.csv"))):
    rob += [r for r in csv.DictReader(open(path))]
if rob:
    print("## Truncation and a crash mid-write\n")
    print("Samples a reader recovers, % of the samples the file held (truncation) or of the samples written before the kill.\n")
    body = []
    for r in rob:
        p = SHORT[r["project"]]
        ok = r["exit"] == "0"
        rec = int(r["samples_recovered"] or 0)
        if r["case"] == "truncate":
            ref = int(r["samples_written"])
        else:
            ref = int(r["samples_written"])
        body.append([p, r["case"], r["format"], r["cut"], r["file_bytes"], r["exit"],
                     pct(rec / ref, 2) if ok and ref else "unreadable"])
    table(["project", "case", "format", "cut", "bytes kept", "reader exit", "samples recovered (%)"], body)

# ---- 6a. interop -----------------------------------------------------------------------------------
iop = []
for path in sorted(glob.glob(os.path.join(RES, "interop", "*.csv"))):
    iop += list(csv.DictReader(open(path)))
if iop:
    print("## Interop: existing tools on each file\n")
    print("Exit 0 and a byte count: the tool read the whole file (its output's size). fst2vcd and lxt2vcd are "
          "GTKWave's own readers; zstd -d is the spec's reader without seeking.\n")
    body = [[SHORT[r["project"]], r["file"].replace(LONG.get(SHORT[r["project"]], "") + ".", "").replace(
        LONG.get(SHORT[r["project"]], ""), ""), r["tool"], r["exit"], f"{int(r['bytes_out'] or 0):,}"] for r in iop]
    table(["project", "file", "tool", "exit", "bytes out"], body)

# ---- 7. the container ----------------------------------------------------------------------------
ctr = []
for path in glob.glob(os.path.join(RES, "ctr", "*.csv")):
    ctr += list(csv.DictReader(open(path)))
if ctr:
    print("## VGCH v2's container: block length and checksum\n")
    print("### Size against block length (zstd 1), MB\n")
    lgs = sorted({int(r["log2_block_clocks"]) for r in ctr})
    by = {(SHORT[r["trace"].replace("_trace.vgch.zst", "")], int(r["log2_block_clocks"])): r for r in ctr}
    body = []
    for p in projects:
        body.append([p] + [f(int(by[(p, lg)]["comp_bytes"]) / 1e6, 2) if (p, lg) in by else "-" for lg in lgs])
    table(["project"] + [f"2^{lg} clocks" for lg in lgs], body)
    print("### Cost of reading one block (decompress with checksum + decode the pairs), ms, mean over blocks\n")
    body = []
    for p in projects:
        row = [p]
        for lg in lgs:
            r = by.get((p, lg))
            row.append("-" if r is None else f"{(float(r['dec_check_ns']) + float(r['parse_ns'])) / int(r['blocks']) / 1e6:.2f}")
        body.append(row)
    table(["project"] + [f"2^{lg} clocks" for lg in lgs], body)
    print("### The XXH64 checksum at 2^24 clocks: decompression time with it checked and ignored, s, whole trace\n")
    body = []
    for p in projects:
        r = by.get((p, 24))
        e = enc.get((p, "vgch2"), {})
        c1, c0 = e.get(("identity", "zstd1")), e.get(("identity", "zstd1-nocheck"))
        if r is None:
            continue
        body.append([p, f"{float(r['dec_check_ns']) / 1e9:.2f}", f"{float(r['dec_ignore_ns']) / 1e9:.2f}",
                     pct(1 - float(r["dec_ignore_ns"]) / float(r["dec_check_ns"]), 1),
                     "-" if not (c1 and c0) else f"{float(c1['est_bg_ns']) / 1e9:.2f}",
                     "-" if not (c1 and c0) else f"{float(c0['est_bg_ns']) / 1e9:.2f}",
                     "-" if not (c1 and c0) else f"{int(float(c1['est_comp_bytes']) - float(c0['est_comp_bytes']))}",
                     f"{float(r['parse_ns']) / 1e9:.2f}"])
    table(["project", "decompress, checked (s)", "decompress, ignored (s)", "checksum share (%)",
           "compress with (s)", "compress without (s)", "bytes the checksums add", "decode the pairs (s)"], body)

# ---- 7a. host load per stage ----------------------------------------------------------------------------
import datetime
import re


def ts(x):
    return datetime.datetime.strptime(x, "%Y-%m-%dT%H:%M:%SZ")


plog, llog = os.path.join(OUT, "progress.log"), os.path.join(OUT, "load.log")
if os.path.exists(plog) and os.path.exists(llog):
    samples = []
    lines = open(llog).read().split("\n")
    for k in range(len(lines) - 2):
        if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", lines[k]):
            la = lines[k + 1].split()
            vm = lines[k + 2].split()
            if len(la) >= 3 and len(vm) >= 15:
                samples.append((ts(lines[k]), float(la[0]), int(vm[14])))
    wins = []   # (name, start, end)
    opened = {}
    for line in open(plog):
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        t, msg = ts(parts[0]), parts[1].strip()
        m = re.match(r"stage (\w+): (\d+) jobs on CPUs", msg)
        if m:
            opened[m.group(1)] = t
            continue
        m = re.match(r"stage (\w+): done in", msg)
        if m and m.group(1) in opened:
            wins.append((m.group(1), opened.pop(m.group(1)), t))
            continue
        m = re.match(r"ticks ((?:\S+:\d+ )*\S+:\d+): \d+/\d+ chunks", msg)
        if m:
            key = "loop " + "+".join(x.rsplit(":", 1)[0] for x in m.group(1).split())
            if key not in opened:
                opened[key] = t
            opened[key + "#end"] = t
    for k in list(opened):
        if k.startswith("loop ") and not k.endswith("#end"):
            wins.append((k, opened[k], opened[k + "#end"]))
    print("## Host load during each stage\n")
    print("From `load.log` (`/proc/loadavg` and `vmstat` every 30 s while a stage or a loop set runs) and "
          "`progress.log` (each stage's start and end). Load per CPU is the 1-minute load over 88; idle is vmstat's. "
          "Other sessions shared the host throughout; nothing here was run on an idle machine.\n")
    body = []
    for name, a, b in sorted(wins, key=lambda w: w[1]):
        ss = [x for x in samples if a <= x[0] <= b]
        if not ss:
            continue
        l = sorted(x[1] / 88 for x in ss)
        idl = sorted(x[2] for x in ss)
        body.append([name, a.strftime("%H:%M"), b.strftime("%H:%M"), len(ss), f"{l[0]:.2f}", f"{statistics.median(l):.2f}",
                     f"{l[-1]:.2f}", f"{idl[0]}", f"{statistics.median(idl):.0f}"])
    table(["stage", "start (UTC)", "end", "samples", "load per CPU, min", "median", "max", "idle %, min", "median"], body)

# ---- 8. complexity ---------------------------------------------------------------------------------
print("## Implementation size\n")


def loc(paths):
    n = 0
    for p in paths:
        py = p.endswith(".py")
        for line in open(p):
            s = line.strip()
            if s and not (py and s.startswith("#")) and not (not py and (s.startswith("//") or s.startswith("*") or
                                                                         s.startswith("/*"))):
                n += 1
    return n


def words(paths):
    return sum(len(open(p).read().split()) for p in paths)


S = os.path.join(HERE, "src")
body = [
    ["vgch2 writer (vgacap src/trace/trace_write.c, blocks.c, trace.h; stream writer)",
     loc([f"{ROOT}/src/trace/trace_write.c", f"{ROOT}/src/trace/blocks.c", f"{ROOT}/src/trace/blocks.h",
          f"{ROOT}/include/vgacap/trace.h", f"{ROOT}/src/stream/writer.c"])],
    ["vgch2 reader (vgacap src/trace/trace_read.c; stream reader)",
     loc([f"{ROOT}/src/trace/trace_read.c", f"{ROOT}/src/stream/reader.c"])],
    ["the study writers: planar, events, runs, changelog, raw (study src/w_mine.c, w_mine.h)", loc([f"{S}/w_mine.c", f"{S}/w_mine.h"])],
    ["study container writer + transforms (study src/bg.c, codec.c)", loc([f"{S}/bg.c", f"{S}/codec.c"])],
    ["the study readers: planar, events, raw, and vgch2 and FST drivers (study src/rd.c)", loc([f"{S}/rd.c"])],
]
table(["code", "non-comment lines"], body)
print("Spec text, words: VGCH v2 trace " + str(words([f"{ROOT}/docs/trace-format.md",
                                                      f"{ROOT}/src/stream/reference-stream-format.md"])) +
      " (docs/trace-format.md + src/stream/reference-stream-format.md).\n")

# R9's cost of change: what reads or writes the simulation trace's RUNS payload
# today, and so changes with a planar payload (the format owner's list, and
# the tests that hold RUNS bytes). tinytapeout-vga-videos from TTVGA.
TTVGA = os.environ.get("TTVGA", os.path.expanduser("~/github/TinyTapeout/tinytapeout-vga-videos"))
CHANGE = [("vgacap", ROOT, ["src/trace/trace_write.c", "src/trace/trace_read.c", "src/trace/blocks.c",
                            "src/stream/reader.c", "src/stream/writer.c", "src/tools/vgacap_dump.c",
                            "python/vgacap/stream.py", "python/vgacap/synthtrace.py", "tests/test_trace_read.c",
                            "tests/test_trace_write.c", "tests/test_stream_v2.c", "python/tests/test_stream_v2.py",
                            "python/tests/test_roundtrip_c.py"]),
          ("tinytapeout-vga-videos", TTVGA, ["ttvga/harness/tb.cpp", "ttvga/harness/tracetool.c",
                                             "tests/sim/vgch.py", "scripts/export_tbv1_vectors.py"])]
body = []
for repo, base_dir, files in CHANGE:
    for fn in files:
        fp = os.path.join(base_dir, fn)
        body.append([repo, f"`{fn}`", str(loc([fp])) if os.path.exists(fp) else "not found"])
body.append(["all", f"{len(body)} files", f"{sum(int(r[2]) for r in body if r[2].isdigit()):,}"])
print("### What a planar payload changes (R9): the code that reads or writes RUNS today\n")
print("Each file's non-comment lines, whole: the code to review, not the lines that change. The published "
      "traces are re-encoded as well (the re-encode table, under the encode2 tables).\n")
table(["repository", "file", "non-comment lines"], body)
