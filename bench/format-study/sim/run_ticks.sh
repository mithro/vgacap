#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Fix round 1's in-process sets (docs/superpowers/2026-11-27-trace-format-study.md,
# Method), one command:
#
#   bench/format-study/sim/run_ticks.sh [main] [raw] [scope]
#
# main: 4 processes a project (fx1a-fx1d), the variants none, vgch2, lean,
#   events, evplanar, planar and changelog, each process in its own order,
#   two CPUs a process (the simulation thread pinned to the first). Two
#   processes a project at once (10 processes, 20 CPUs), in two waves.
# raw: raw alone with the untraced model (fx1ra-fx1rd), so that raw's
#   background threads share no process with another variant.
# scope: one process a project (fx1s), unpinned in a systemd scope with
#   CPUQuota=200%, as a job runs.
# Progress, with an ETA, goes to progress.log once a minute.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
OUT=${OUT:-$ROOT/tmp/format-study}
RES=$OUT/results/sim
CPUS_X=${CPUS_X:-"36,37 38,39 40,41 42,43 34,35"}
CPUS_Y=${CPUS_Y:-"24,25 26,27 28,29 30,31 32,33"}
CHUNKS=${TICK_CHUNKS:-4000}
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$OUT/progress.log" >&2; }
NPROJ=5

# Logs the rows written so far against the rows expected, with an ETA.
watch_rows() {   # tags... (each: TAG:NVARIANTS)
    local start; start=$(date +%s)
    local total=0
    for t in "$@"; do total=$((total + CHUNKS * ${t##*:} * NPROJ)); done
    while sleep 60; do
        local done=0
        for t in "$@"; do
            for f in "$RES/${t%%:*}"-*.csv; do
                [ -f "$f" ] && done=$((done + $(grep -c -E '^[0-9]+,[0-9]+,' "$f" || true)))
            done
        done
        local el=$(( $(date +%s) - start ))
        if [ "$done" -gt 0 ]; then
            local eta=$(( el * (total - done) / done ))
            log "ticks $*: $done/$total chunks, ${el}s elapsed, ETA ${eta}s (about $(date -u -d "@$(( $(date +%s) + eta ))" +%H:%MZ))"
        else
            log "ticks $*: 0/$total chunks, ${el}s elapsed"
        fi
    done
}

# Two tags at once, on the two CPU sets.
pair() {   # tagA orderA tagB orderB
    local n; n=$(echo "$2" | tr ',' '\n' | wc -l)
    watch_rows "$1:$n" "$3:$n" &
    local w=$!
    TICK_TAG=$1 TICK_VARIANTS=$2 TICK_CPUS=$CPUS_X "$HERE/run_sim.sh" tick &
    local a=$!
    TICK_TAG=$3 TICK_VARIANTS=$4 TICK_CPUS=$CPUS_Y "$HERE/run_sim.sh" tick &
    local b=$!
    wait "$a" "$b" || true
    kill "$w" || true
}

stage_main() {
    pair fx1a none,vgch2,lean,events,evplanar,planar,changelog fx1b changelog,planar,evplanar,events,lean,vgch2,none
    pair fx1c events,none,planar,vgch2,changelog,lean,evplanar fx1d lean,changelog,vgch2,evplanar,none,planar,events
}
stage_raw() {
    pair fx1ra none,raw fx1rb raw,none
    pair fx1rc none,raw fx1rd raw,none
}
stage_scope() {
    local n=7
    watch_rows "fx1s:$n" &
    local w=$!
    TICK_TAG=fx1s TICK_SCOPE=1 TICK_VARIANTS=vgch2,none,lean,evplanar,events,planar,changelog "$HERE/run_sim.sh" tick || true
    kill "$w" || true
}
"$HERE/load_logger.sh" "$OUT" $$ &   # the host load every 30 s into load.log while this runs
for s in ${*:-main raw scope}; do "stage_$s"; done
