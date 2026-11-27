# /// script
# requires-python = ">=3.11"
# ///
# SPDX-License-Identifier: Apache-2.0
"""The trace format study's tables, from run_all.sh's and run_sim.sh's raw results.

    uv run --script analyze.py OUT > OUT/results/tables.md

OUT is run_all.sh's output directory. Every table of
docs/design/2026-11-27-trace-format-study.md is printed here, under the same
heading, from the CSV files under OUT/results. Nothing is typed in by hand
except data/pilot_baseline.csv (the pilot's untraced simulation times).
"""
import csv
import glob
import os
import random
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
    print("### The replay run twice: ns per clock over null, first run (`writer2-run1`) and second (`writer2`)\n")
    r1data, r1meta, _ = load_writer("writer2-run1")
    body = []
    for p in projects:
        row = [p]
        for w in ("vgch2", "planar", "events", "raw"):
            a, b = paired(r1data, r1meta, p, w), est_cache.get((p, w))
            row.append("-" if not (a and b) else f"{a[0]:.2f} / {b[0]:.2f}")
        a, b = paired(r1data, r1meta, p, "vgch2"), paired(r1data, r1meta, p, "events")
        c, d = est_cache.get((p, "vgch2")), est_cache.get((p, "events"))
        row.append("-" if not (a and b and c and d) or b[0] <= 0 or d[0] <= 0 else f"{a[0] / b[0]:.2f} / {c[0] / d[0]:.2f}")
        body.append(row)
    table(["project", "vgch2", "planar", "events", "raw", "vgch2 over events"], body)

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

# ---- 3. the simulation check ---------------------------------------------------------------------
# ---- 3a. the in-process simulation check (bench_tick) -------------------------------------------
# Replicates: tick, tickB, tickC, tickD: separate processes, each with the
# variants in a different order (so their models sit at different heap
# addresses and run in a different turn).
TICKV = ["vgch2", "runs", "events", "changelog", "raw", "raw_small"]
tickrep = defaultdict(dict)   # tag -> project -> variant -> {chunk: (ns, clocks)}
for path in glob.glob(os.path.join(RES, "sim", "tick*-*.csv")):
    base_name = os.path.basename(path)[:-4]
    tag, proj = base_name.split("-", 1)
    p = SHORT[proj]
    d = defaultdict(dict)
    for c in rows(path):
        if len(c) == 5 and c[0].isdigit() and c[3].isdigit() and c[4].isdigit():
            d[c[2]][int(c[0])] = (float(c[3]), int(c[4]))
    tickrep[tag][p] = d


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


def tick_tables(title, note, tags):
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


tick_est = {}
if tickrep:
    print("## Simulation check: the Verilator loop, in one process\n")
    two = sorted(t for t in tickrep if t.startswith("tick2"))
    one = sorted(t for t in tickrep if not t.startswith("tick2"))
    note = ("Each variant's model in one process, stepped in turn by 65,536-clock chunks, 4,000 chunks "
            "(262 M clocks) a process (bench_tick.cpp).")
    if two:
        tick_est = tick_tables("Two CPUs a process: the simulation thread on one, the writers' threads on either",
                               note + " As in a job's two-CPU scope.", two)
    if one:
        e1 = tick_tables("One CPU a process: the writers' threads share the simulation's core",
                         note + " The background threads compete with the simulation on its core, so candidates "
                         "with heavy background work (raw, changelog, vgch2) are penalised.", one)
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
    for job, name in (("changelog", "changelog to planar (zstd 1)"), ("rawplanar", "raw to planar (zstd 1)")):
        e = enc.get((p, job), {})
        ps = passes.get((p, job))
        key = ("changelog-planar", "zstd1") if job == "changelog" else ("planar-runs", "zstd1")
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
names_c = ["vgch2 (zstd 1)", "runs (zstd 1)", "planar (zstd 1)", "events (zstd 1)", "changelog to planar (zstd 1)",
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

print("### Background CPU, s for the whole trace, and as % of the untraced simulation's CPU\n")
body = []
for p in projects:
    d = {c[0]: c for c in cand_rows(p)}
    cpu = float(base[p]["cpu_without_s"])
    row = [p, f(cpu, 0)]
    for n in (names_c or []):
        if n in d and d[n][2] == d[n][2]:
            row.append(f"{d[n][2]:.2f} ({pct(d[n][2] / cpu, 2)}%)")
        else:
            row.append("-")
    body.append(row)
if names_c:
    table(["project", "untraced CPU (s)"] + names_c, body)

# The compressor matrix.
COMPS = ["none", "zstd-5", "zstd-1", "zstd1", "zstd1-nocheck", "zstd3", "zstd9", "zstd1-long27", "lz4", "lz4hc9",
         "deflate1", "deflate6"]
for w in ("vgch2", "events", "planar"):
    print(f"### Compressors on {w}'s blocks: MB, and background CPU s\n")
    body = []
    for p in projects:
        e = enc.get((p, w), {})
        row = [p]
        for c in COMPS[1:]:
            r = e.get(("identity", c))
            row.append("-" if r is None else f"{float(r['est_comp_bytes']) / 1e6:.2f} / {float(r['est_bg_ns']) / 1e9:.2f}")
        body.append(row)
    table(["project"] + COMPS[1:], body)
    print("Uncompressed, MB: " + ", ".join(
        f"{p} {sum(int(r['raw_bytes_measured']) for r in [enc[(p, w)][('identity', 'zstd1')]]) / 1e6 * (int(enc[(p, w)][('identity', 'zstd1')]['blocks']) / max(1, int(enc[(p, w)][('identity', 'zstd1')]['blocks_measured']))):,.1f}"
        for p in projects if (p, w) in enc and ("identity", "zstd1") in enc[(p, w)]) + ".\n")

print("### Raw candidate: transforms x compressors, MB / background CPU s (projected)\n")
TR = ["pack6", "xor6", "xor-byteplanes", "xor-bitplanes"]
RC = ["zstd-1", "zstd1", "zstd3", "lz4"]
body = []
for p in projects:
    e = enc.get((p, "raw"), {})
    row = [p]
    for t in TR:
        for c in RC:
            r = e.get((t, c))
            row.append("-" if r is None else f"{float(r['est_comp_bytes']) / 1e6:,.1f} / {float(r['est_bg_ns']) / 1e9:.1f}")
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
                             "planar" if ".planar." in file else "events" if ".events." in file else
                             "raw xor6" if ".raw." in file else file)
for path in glob.glob(os.path.join(RES, "read", "*.csv")):
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
if rdv:
    print("### Validation: every reader gives the same runs (canonical hash of full runs and of uo_out runs)\n")
    body = []
    for p in projects:
        ref = (rdv.get((p, "vgch2", "validate-full")), rdv.get((p, "vgch2", "validate-port0")))
        row = [p]
        for fm in FMTS:
            a, b = rdv.get((p, fm, "validate-full")), rdv.get((p, fm, "validate-port0"))
            if a is None:
                row.append("-")
            else:
                row.append("same" if ref[0] and a[0] == ref[0][0] and b and b[0] == ref[1][0] and a[1] == ref[0][1]
                           else f"DIFFERENT {a[0]}")
        body.append(row)
    table(["project"] + FMTS, body)
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

# ---- 8. complexity ---------------------------------------------------------------------------------
print("## Implementation size\n")


def loc(paths):
    n = 0
    for p in paths:
        for line in open(p):
            s = line.strip()
            if s and not s.startswith("//") and not s.startswith("*") and not s.startswith("/*"):
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
    ["planar + events + raw writers (study src/w_mine.c, w_mine.h)", loc([f"{S}/w_mine.c", f"{S}/w_mine.h"])],
    ["study container writer + transforms (study src/bg.c, codec.c)", loc([f"{S}/bg.c", f"{S}/codec.c"])],
    ["study readers for planar, events, raw (study src/rd.c, stdy part)", loc([f"{S}/rd.c"])],
]
table(["code", "non-comment lines"], body)
print("Spec text, words: VGCH v2 trace " + str(words([f"{ROOT}/docs/trace-format.md",
                                                      f"{ROOT}/src/stream/reference-stream-format.md"])) +
      " (docs/trace-format.md + src/stream/reference-stream-format.md).\n")
