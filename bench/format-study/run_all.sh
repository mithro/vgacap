#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Reproduces every number of docs/superpowers/2026-11-27-trace-format-study.md:
#
#   bench/format-study/run_all.sh [STAGE...]
#
# Stages, in order (default: all of them): build traces info lean writer writer2
# encode encode2 read read2 validate robust interop ctr analyze. The simulation check (the Verilator loop) is
# sim/run_sim.sh, separately, because it needs the projects' sources.
#
# Everything goes under $OUT (default: <checkout>/tmp/format-study):
# results/<stage>/*.csv are the raw results, logs/ the stderr of each job,
# progress.log one line a minute with an ETA, load.log the host load
# (/proc/loadavg and vmstat) every 30 s while a stage runs.
#
# Environment: TRACES (directory holding the published *_trace.vgch.zst,
# searched recursively; default the published frames subtree, read only),
# CPUS (the logical CPUs jobs are pinned to, one job a CPU; default 2-23,
# physical cores of socket 0 and 1 without their hyperthread siblings).
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
OUT=${OUT:-$ROOT/tmp/format-study}
TRACES=${TRACES:-/srv/data.wafer.space/tinytapeout-vga-videos/frames}
CPUS=${CPUS:-2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23}
BUILD=$OUT/build
FS=$BUILD/fsbench
TR=$OUT/traces
RES=$OUT/results
LOGS=$OUT/logs
FILES=$OUT/files
mkdir -p "$RES" "$LOGS" "$FILES" "$TR"

# The 11 pilot projects (tinytapeout-vga-videos docs/reports/2026-11-pilot).
PROJECTS="tt05_tt_um_tomkeddie_a tt07_tt_um_tinytapeout_dvd_screensaver tt08_tt_um_MichaelBell_mandelbrot
tt08_tt_um_a1k0n_nyancat tt08_tt_um_johshoff_metaballs tt08_tt_um_nyan ttihp25a_tt_um_MichaelBell_rle_vga
ttihp25a_tt_um_a1k0n_vgadonut ttihp25a_tt_um_cfib_demo ttihp25a_tt_um_rebeccargb_colorbars
ttihp25a_tt_um_rejunity_atari2600"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$OUT/progress.log" >&2; }

# ---- a pool of jobs, one a CPU ----------------------------------------------------------
# Jobs are lines of a file: "NAME<TAB>COMMAND". Each runs pinned with
# taskset, stdout to results, stderr to logs. A monitor loop logs progress
# and an ETA (jobs done / total, extrapolated from the elapsed time) every
# 60 s and the load every 30 s.
run_pool() {
    local stage=$1 jobs=$2
    local total; total=$(wc -l < "$jobs")
    local start; start=$(date +%s)
    mkdir -p "$RES/$stage" "$LOGS/$stage"
    log "stage $stage: $total jobs on CPUs $CPUS"
    (
        while sleep 30; do
            { date -u +%FT%TZ; cat /proc/loadavg; vmstat 1 2 | tail -1; } >> "$OUT/load.log" 2>&1
        done
    ) &
    local loadpid=$!
    (
        while sleep 60; do
            local done; done=$(find "$RES/$stage" -name '*.done' | wc -l)
            local now; now=$(date +%s)
            local el=$((now - start))
            if [ "$done" -gt 0 ]; then
                local eta=$((el * (total - done) / done))
                log "stage $stage: $done/$total jobs done, ${el}s elapsed, ETA ${eta}s (about $(date -u -d "@$((now + eta))" +%H:%MZ))"
            else
                log "stage $stage: 0/$total jobs done, ${el}s elapsed, no ETA yet"
            fi
        done
    ) &
    local progpid=$!
    local -a cpus=($CPUS)
    local -A busy=()
    local i=0
    while IFS=$'\t' read -r name cmd; do
        # Wait for a free CPU.
        while :; do
            local cpu=""
            for c in "${cpus[@]}"; do
                if [ -z "${busy[$c]:-}" ] || [ ! -d "/proc/${busy[$c]}" ]; then cpu=$c; break; fi
            done
            [ -n "$cpu" ] && break
            wait -n || true
        done
        (
            if taskset -c "$cpu" bash -c "$cmd" > "$RES/$stage/$name.csv" 2> "$LOGS/$stage/$name.log"; then
                touch "$RES/$stage/$name.done"
            else
                echo "FAILED: $cmd" >> "$LOGS/$stage/$name.log"
                touch "$RES/$stage/$name.done"
                echo "$name" >> "$RES/$stage/FAILED"
            fi
        ) &
        busy[$cpu]=$!
        i=$((i + 1))
    done < "$jobs"
    wait "${busy[@]}" || true
    kill "$loadpid" "$progpid" || true
    log "stage $stage: done in $(( $(date +%s) - start ))s; failed: $(cat "$RES/$stage/FAILED" 2>/dev/null | tr '\n' ' ')"
}

trace_of() { echo "$TR/$1_trace.vgch.zst"; }
clocks_of() { awk -F, 'NR==2{print $2}' "$RES/info/$1.csv"; }
size_of() { stat -c %s "$(trace_of "$1")"; }

stage_build() {
    log "build: $BUILD"
    cmake -S "$HERE" -B "$BUILD" -DCMAKE_BUILD_TYPE=RelWithDebInfo > "$LOGS/cmake.log" 2>&1
    cmake --build "$BUILD" -j 16 --target fsbench > "$LOGS/build.log" 2>&1
}

stage_traces() {
    # Copies of the published traces (the originals are never opened for writing).
    for p in $PROJECTS; do
        local src; src=$(find "$TRACES" -name "$p"_trace.vgch.zst -print -quit)
        [ -n "$src" ] || { log "traces: $p not found under $TRACES"; exit 1; }
        [ -s "$(trace_of "$p")" ] || cp --no-preserve=mode,ownership "$src" "$(trace_of "$p")"
    done
    sha256sum "$TR"/*.vgch.zst > "$RES/traces.sha256"
}

stage_info() {
    local jobs=$OUT/jobs-info.txt; : > "$jobs"
    for p in $PROJECTS; do printf '%s\t%s\n' "$p" "$FS info $(trace_of "$p")" >> "$jobs"; done
    run_pool info "$jobs"
}

# The replay: about 96 chunks of 2^20 clocks a project, every S-th chunk
# of the trace, 5 interleaved rounds each.
stage_writer() {
    local jobs=$OUT/jobs-writer.txt; : > "$jobs"
    local scratch=$OUT/scratch; mkdir -p "$scratch"
    for p in $PROJECTS; do
        local n=$(( $(clocks_of "$p") / 1048576 ))
        local s=$(( n / 96 )); [ "$s" -ge 1 ] || s=1
        printf '%s\t%s\n' "$p" "$FS writer $(trace_of "$p") $scratch --writers null,vgch2,planar,events,raw,fst,fst_clk,lxt2,vcd --chunk 1048576 --stride $s --reps 5" >> "$jobs"
    done
    run_pool writer "$jobs"
}

# The same replay for the writers whose code the simulation check shares
# (w_mine.h), and vgch2 and null for the pairing: the tables take these
# rows from writer2 and fst, vcd, lxt2 and fst_clk from writer.
stage_writer2() {
    local jobs=$OUT/jobs-writer2.txt; : > "$jobs"
    local scratch=$OUT/scratch; mkdir -p "$scratch"
    for p in $PROJECTS; do
        local n=$(( $(clocks_of "$p") / 1048576 ))
        local s=$(( n / 96 )); [ "$s" -ge 1 ] || s=1
        printf '%s\t%s\n' "$p" "$FS writer $(trace_of "$p") $scratch --writers null,vgch2,planar,events,runs,changelog,raw --chunk 1048576 --stride $s --reps 5" >> "$jobs"
    done
    run_pool writer2 "$jobs"
}

RUNCOMPS=zstd-5,zstd-1,zstd1,zstd3,zstd9,zstd1-long27,zstd1-nocheck,lz4,lz4hc9,deflate1,deflate6
stage_encode() {
    local jobs=$OUT/jobs-encode.txt; : > "$jobs"
    for p in $PROJECTS; do
        local t; t=$(trace_of "$p")
        # The compressor matrix on every block, or every 4th for the big traces.
        local every=1; [ "$(size_of "$p")" -lt 100000000 ] || every=4
        printf '%s\t%s\n' "$p.vgch2" "$FS encode $t $FILES --writer vgch2 --transforms identity --comps $RUNCOMPS --sample-every $every" >> "$jobs"
        for w in planar events; do
            printf '%s\t%s\n' "$p.$w" "$FS encode $t $FILES --writer $w --transforms identity --comps $RUNCOMPS --sample-every $every --primary identity:zstd1" >> "$jobs"
        done
        printf '%s\t%s\n' "$p.runs" "$FS encode $t $FILES --writer runs --transforms identity --comps zstd1"  >> "$jobs"
        printf '%s\t%s\n' "$p.raw" "$FS encode $t $FILES --writer raw --transforms pack6,xor6,xor-byteplanes,xor-bitplanes --comps zstd-1,zstd1,zstd3,lz4 --sample-every 16 --primary xor6:zstd1" >> "$jobs"
        printf '%s\t%s\n' "$p.changelog" "$FS encode $t $FILES --writer changelog --transforms changelog-planar --comps zstd1 --primary changelog-planar:zstd1" >> "$jobs"
        printf '%s\t%s\n' "$p.evplanar" "$FS encode $t $FILES --writer events --transforms events-planar --comps zstd1 --primary events-planar:zstd1" >> "$jobs"
        printf '%s\t%s\n' "$p.rawplanar" "$FS encode $t $FILES --writer raw --transforms planar-runs --comps zstd1 --sample-every 1 --primary planar-runs:zstd1" >> "$jobs"
        printf '%s\t%s\n' "$p.sigrok" "$FS encode $t $FILES --writer raw --transforms pack6clk --comps deflate1,deflate6 --sample-every 16" >> "$jobs"
        printf '%s\t%s\n' "$p.vcd" "$FS encode $t $FILES --writer vcd --transforms identity --comps zstd1,deflate6,none --stride 8" >> "$jobs"
        printf '%s\t%s\n' "$p.fst-lz4" "$FS encode $t $FILES --writer fst --fst-pack lz4" >> "$jobs"
        printf '%s\t%s\n' "$p.fst-zlib" "$FS encode $t $FILES --writer fst --fst-pack zlib --stride 4" >> "$jobs"
        printf '%s\t%s\n' "$p.fst_clk" "$FS encode $t $FILES --writer fst_clk --fst-pack lz4 --stride 16" >> "$jobs"
        printf '%s\t%s\n' "$p.lxt2" "$FS encode $t $FILES --writer lxt2 --stride 16" >> "$jobs"
    done
    # Longest first, so the pool ends together.
    sort -t. -k2,2 "$jobs" | awk -F'\t' '{print ($1 ~ /raw|sigrok/ ? 0 : 1) "\t" $0}' | sort -s -k1,1n | cut -f2- > "$jobs.sorted"
    run_pool encode "$jobs.sorted"
}

# Readers: each format's file, 5 rounds, the formats interleaved within a
# round (one fsbench call each, in a rotated order).
stage_read() {
    local jobs=$OUT/jobs-read.txt; : > "$jobs"
    for p in $PROJECTS; do
        printf '%s\t%s\n' "$p" "$HERE/read_rounds.sh $FS $(trace_of "$p") $FILES $p $(awk -F, 'NR==2{print $NF}' "$RES/info/$p.csv")" >> "$jobs"
    done
    run_pool read "$jobs"
}

# The readers of a job (derive.py) on each stored candidate's file, and
# vgch2 with a lazy-BLKH open (read2_rounds.sh).
stage_read2() {
    local jobs=$OUT/jobs-read2.txt; : > "$jobs"
    for p in $PROJECTS; do
        printf '%s\t%s\n' "$p" "$HERE/read2_rounds.sh $FS $(trace_of "$p") $FILES $p $(awk -F, 'NR==2{print $NF}' "$RES/info/$p.csv")" >> "$jobs"
    done
    run_pool read2 "$jobs"
}

# Background CPU again, 3 times, for its spread: each stored candidate's
# configuration at zstd 1 alone, every block, the repetitions apart in time
# (all of the first, then the second, then the third). No primary file;
# vgch2's real file goes to files2/ and is removed.
stage_encode2() {
    local jobs=$OUT/jobs-encode2.txt; : > "$jobs"
    local f2=$OUT/files2; mkdir -p "$f2"
    for k in 1 2 3; do
        for p in $PROJECTS; do
            local t; t=$(trace_of "$p")
            printf '%s\t%s\n' "$p.vgch2.r$k" "$FS encode $t $f2 --writer vgch2 --transforms identity --comps zstd1; rm -f $f2/$p.vgch2.trace.zst" >> "$jobs"
            printf '%s\t%s\n' "$p.planar.r$k" "$FS encode $t $f2 --writer planar --transforms identity --comps zstd1" >> "$jobs"
            printf '%s\t%s\n' "$p.events.r$k" "$FS encode $t $f2 --writer events --transforms identity --comps zstd1" >> "$jobs"
            printf '%s\t%s\n' "$p.evplanar.r$k" "$FS encode $t $f2 --writer events --transforms events-planar --comps zstd1" >> "$jobs"
            printf '%s\t%s\n' "$p.changelog.r$k" "$FS encode $t $f2 --writer changelog --transforms changelog-planar --comps zstd1" >> "$jobs"
            printf '%s\t%s\n' "$p.rawplanar.r$k" "$FS encode $t $f2 --writer raw --transforms planar-runs --comps zstd1" >> "$jobs"
            printf '%s\t%s\n' "$p.raw.r$k" "$FS encode $t $f2 --writer raw --transforms xor6 --comps zstd1" >> "$jobs"
        done
    done
    run_pool encode2 "$jobs"
}

# The converted files (changelog, events and raw stored as planar): their
# runs hash the same as vgacap's reader's (the read stage's validate rows).
stage_validate() {
    local jobs=$OUT/jobs-validate.txt; : > "$jobs"
    for p in $PROJECTS; do
        local hz; hz=$(awk -F, 'NR==2{print $NF}' "$RES/info/$p.csv")
        local cmd="set -e"
        for f in changelog.changelog-planar events.events-planar raw.planar-runs; do
            cmd+="; $FS read $FILES/$p.$f.zstd1.stdy --kind stdy --project $p --patterns validate --clock-hz $hz | sed 's/^/v,/'"
        done
        printf '%s\t%s\n' "$p" "$cmd" >> "$jobs"
    done
    run_pool validate "$jobs"
}

# Truncation and a crash mid-write, on three projects.
stage_robust() {
    local jobs=$OUT/jobs-robust.txt; : > "$jobs"
    local dir=$OUT/robust; mkdir -p "$dir"
    for p in ttihp25a_tt_um_rebeccargb_colorbars ttihp25a_tt_um_MichaelBell_rle_vga tt07_tt_um_tinytapeout_dvd_screensaver; do
        printf '%s\t%s\n' "$p" "$HERE/robust.sh $FS $(trace_of "$p") $FILES $dir/$p" >> "$jobs"
    done
    run_pool robust "$jobs"
}

stage_interop() {
    local jobs=$OUT/jobs-interop.txt; : > "$jobs"
    local dir=$OUT/interop; mkdir -p "$dir"
    for p in ttihp25a_tt_um_rebeccargb_colorbars ttihp25a_tt_um_MichaelBell_rle_vga; do
        printf '%s\t%s\n' "$p" "$HERE/interop.sh $FS $(trace_of "$p") $FILES $dir/$p" >> "$jobs"
    done
    run_pool interop "$jobs"
}

# The lean writer (src/trace_write_lean.c) must write vgacap's file: each
# published trace replayed through it, and through vgacap's own writer, is
# compared with the published file byte for byte (test_lean.sh).
stage_lean() {
    local jobs=$OUT/jobs-lean.txt; : > "$jobs"
    local dir=$OUT/lean-scratch; mkdir -p "$dir"
    for p in $PROJECTS; do printf '%s\t%s\n' "$p" "$HERE/test_lean.sh $FS $(trace_of "$p") $dir" >> "$jobs"; done
    run_pool lean "$jobs"
    [ ! -s "$RES/lean/FAILED" ] || { log "lean: files differ: $(tr '\n' ' ' < "$RES/lean/FAILED")"; exit 1; }
}

stage_ctr() {
    local jobs=$OUT/jobs-ctr.txt; : > "$jobs"
    for p in $PROJECTS; do printf '%s\t%s\n' "$p" "$FS ctr $(trace_of "$p") --block-clocks 18,20,22,24,26" >> "$jobs"; done
    run_pool ctr "$jobs"
}

stage_analyze() {
    (cd "$HERE" && uv run --script analyze.py "$OUT") > "$RES/tables.md" 2> "$LOGS/analyze.log"
    (cd "$HERE" && uv run --script splice_doc.py "$RES/tables.md" "$ROOT/docs/superpowers/2026-11-27-trace-format-study.md")
    log "analyze: $RES/tables.md, spliced into the study document"
}

STAGES=${*:-build traces info lean writer writer2 encode encode2 read read2 validate robust interop ctr analyze}
for s in $STAGES; do "stage_$s"; done
