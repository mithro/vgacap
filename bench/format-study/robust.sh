#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Truncation and a crash mid-write (run_all.sh, stage robust):
#   robust.sh FSBENCH TRACE FILES_DIR WORKDIR
# 1. Each format's complete file (from stage encode) cut to 10%, 50% and
#    all but its last 64 bytes, then read whole: the samples a reader gets back.
# 2. Each writer killed (SIGKILL) halfway through the trace: the samples a
#    reader gets back from what it left.
# CSV on stdout: project,case,format,cut,file_bytes,exit,samples_recovered,samples_written.
set -uo pipefail
FS=$1 TRACE=$2 FILES=$3 WORK=$4
mkdir -p "$WORK"
P=$(basename "$TRACE" _trace.vgch.zst)
INFO=$("$FS" info "$TRACE" | sed -n 2p)
CLOCKS=$(echo "$INFO" | cut -d, -f2)
HZ=$(echo "$INFO" | awk -F, '{print $NF}')
SAMPLES=$((2 * CLOCKS))
echo "project,case,format,cut,file_bytes,exit,samples_recovered,samples_written"

# samples a reader recovers from FILE of KIND (the scan's samples column), and its exit status
recover() {
    local kind=$1 f=$2 out rc
    out=$("$FS" read "$f" --kind "$kind" --project "$P" --patterns scan --reps 1 --clock-hz "$HZ" 2>> "$WORK/read.log")
    rc=$?
    local s; s=$(echo "$out" | awk -F, 'NR==2{print $8}')
    echo "$rc,${s:-0}"
}

declare -A KIND=([vgch2]=vgch2 [planar]=stdy [events]=stdy [raw]=stdy [fst]=fst)
declare -A FULL=([vgch2]=$TRACE [planar]=$FILES/$P.planar.identity.zstd1.stdy [events]=$FILES/$P.events.identity.zstd1.stdy
                 [raw]=$FILES/$P.raw.xor6.zstd1.stdy [fst]=$FILES/$P.fst.lz4.fst)
for fmt in vgch2 planar events raw fst; do
    src=${FULL[$fmt]}
    size=$(stat -c %s "$src")
    for cut in 10 50 tail64; do
        if [ "$cut" = tail64 ]; then n=$((size - 64)); else n=$((size * cut / 100)); fi
        f=$WORK/$P.$fmt.cut$cut
        head -c "$n" "$src" > "$f"
        echo "$P,truncate,$fmt,$cut,$n,$(recover "${KIND[$fmt]}" "$f"),$SAMPLES"
        rm -f "$f"
    done
done

HALF=$((CLOCKS / 2))
for fmt in vgch2 planar events raw fst; do
    d=$WORK/kill-$fmt
    mkdir -p "$d"
    case $fmt in
        vgch2) "$FS" encode "$TRACE" "$d" --writer vgch2 --kill-after "$HALF" >> "$WORK/kill.log" 2>&1; rc=$?; f=$d/$P.vgch2.trace.zst ;;
        fst)   "$FS" encode "$TRACE" "$d" --writer fst --fst-pack lz4 --kill-after "$HALF" >> "$WORK/kill.log" 2>&1; rc=$?; f=$d/$P.fst.lz4.fst ;;
        raw)   "$FS" encode "$TRACE" "$d" --writer raw --transforms xor6 --comps zstd1 --sample-every 1000000000 --primary xor6:zstd1 --kill-after "$HALF" >> "$WORK/kill.log" 2>&1; rc=$?; f=$d/$P.raw.xor6.zstd1.stdy ;;
        *)     "$FS" encode "$TRACE" "$d" --writer $fmt --comps zstd1 --sample-every 1000000000 --primary identity:zstd1 --kill-after "$HALF" >> "$WORK/kill.log" 2>&1; rc=$?; f=$d/$P.$fmt.identity.zstd1.stdy ;;
    esac
    # The writer's chunk loop checks the kill point after each 2^20-clock chunk.
    written=$(( 2 * ( (HALF + 1048575) / 1048576 * 1048576 ) ))
    echo "$P,kill(exit $rc),$fmt,half,$(stat -c %s "$f"),$(recover "${KIND[$fmt]}" "$f"),$written"
    rm -rf "$d"
done
