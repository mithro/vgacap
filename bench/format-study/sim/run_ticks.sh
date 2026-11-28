#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The in-process sets (docs/superpowers/2026-11-27-trace-format-study.md,
# Method), one command:
#
#   bench/format-study/sim/run_ticks.sh [main] [raw] [scope] [bg] [bgscope] [readback_raw] [lean]
#
# Fix round 1's sets: main, raw and scope. Fix round 2's: bg and bgscope,
# the background plans again after the shared-buffer and stream-order fixes.
# After every set, each trace it wrote is read back (readback.py): its full
# hash, sample count and uo_out run count must be lean's (or vgch2's, the
# same file), or the script fails.
#
# main: 4 processes a project (fx1a-fx1d), the variants none, vgch2, lean,
#   events, evplanar, planar and changelog, each process in its own order,
#   two CPUs a process (the simulation thread pinned to the first). Two
#   processes a project at once (10 processes, 20 CPUs), in two waves.
# raw: raw alone with the untraced model (fx1ra-fx1rd), so that raw's
#   background threads share no process with another variant.
# scope: one process a project (fx1s), unpinned in a systemd scope with
#   CPUQuota=200%, as a job runs.
# bg: 4 processes a project (fx2a-fx2d), the variants none, vgch2, events,
#   evplanar, planar and changelog, as main.
# bgscope: one process a project (fx2s), those variants, as scope.
# lean: 4 processes a project (fx2la-fx2ld), none, vgch2 and lean: their R1
#   from sets whose files all read back (round 1's main set's did not).
# readback_raw: reads back fix round 1's raw sets (raw's processes held one
#   background writer each), against fx2a's vgch2.
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
FSBENCH=${FSBENCH:-$OUT/build/fsbench}

# Every trace of the tags' project directories against lean's; fails loudly.
readback() {   # tags...
    local dirs=() t
    for t in "$@"; do dirs+=("$OUT/sim/$t"/*/); done
    if "$HERE/readback.py" "$FSBENCH" "$RES/readback-$1.csv" "${dirs[@]%/}" ${REF_TAG:+--ref-dir "$OUT/sim/$REF_TAG"} \
        >> "$OUT/readback.log" 2>&1; then
        log "readback $*: every trace matches"
    else
        log "readback $*: FAILED (readback.log, $RES/readback-$1.csv)"
        exit 1
    fi
}

# Logs the rows written so far against the rows expected, with an ETA.
watch_rows() {   # tags... (each: TAG:NVARIANTS)
    local start; start=$(date +%s)
    local total=0
    for t in "$@"; do total=$((total + CHUNKS * ${t##*:} * NPROJ)); done
    while sleep 60; do
        local done=0
        for t in "$@"; do
            for f in "$RES/${t%%:*}"-*.csv; do
                [ -f "$f" ] && done=$((done + $(awk '/^[0-9]+,[0-9]+,/ { n++ } END { print n + 0 }' "$f")))
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
    local rc=0
    wait "$a" || rc=1
    wait "$b" || rc=1
    stop_watch "$w"
    [ "$rc" = 0 ] || { log "ticks $1 / $3: a set failed"; exit 1; }
    readback "$1" "$3"
}

# The watcher only ever sleeps and logs: it must still be there to stop.
stop_watch() {
    kill "$1"
    local st=0
    wait "$1" || st=$?
    [ "$st" = 143 ] || { log "ticks: the progress watcher ended by itself (status $st)"; exit 1; }
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
    local rc=0
    TICK_TAG=fx1s TICK_SCOPE=1 TICK_VARIANTS=vgch2,none,lean,evplanar,events,planar,changelog "$HERE/run_sim.sh" tick || rc=1
    stop_watch "$w"
    [ "$rc" = 0 ] || { log "ticks fx1s: failed"; exit 1; }
    readback fx1s
}
stage_bg() {
    pair fx2a none,vgch2,events,evplanar,planar,changelog fx2b changelog,planar,evplanar,events,vgch2,none
    pair fx2c events,none,planar,vgch2,changelog,evplanar fx2d evplanar,changelog,vgch2,none,planar,events
}
stage_bgscope() {
    watch_rows "fx2s:6" &
    local w=$!
    local rc=0
    TICK_TAG=fx2s TICK_SCOPE=1 TICK_VARIANTS=vgch2,none,evplanar,events,planar,changelog "$HERE/run_sim.sh" tick || rc=1
    stop_watch "$w"
    [ "$rc" = 0 ] || { log "ticks fx2s: failed"; exit 1; }
    readback fx2s
}
stage_lean() {
    pair fx2la none,vgch2,lean fx2lb lean,vgch2,none
    pair fx2lc vgch2,none,lean fx2ld lean,none,vgch2
}
stage_readback_raw() {
    REF_TAG=fx2a readback fx1ra fx1rb fx1rc fx1rd
}
"$HERE/load_logger.sh" "$OUT" $$ &   # the host load every 30 s into load.log while this runs
for s in ${*:-main raw scope}; do "stage_$s"; done
