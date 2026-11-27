#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The lean writer writes vgacap's file (run_all.sh, stage lean):
#   test_lean.sh FSBENCH TRACE SCRATCH_DIR
# Replays the published TRACE through the lean writer (src/trace_write_lean.c)
# and through vgacap's own (vgch2), each compressed to a whole file, and
# compares both with TRACE byte for byte. CSV on stdout: one row a writer,
# with cmp's verdict. Exits 1 if either file differs.
set -euo pipefail
FS=$1 TRACE=$2 DIR=$3
mkdir -p "$DIR"
P=$(basename "$TRACE" _trace.vgch.zst)
rc=0
echo "project,writer,clocks,run_thread_ns,wall_ns,bytes,published_bytes,cmp"
for w in lean vgch2; do
    out=$DIR/$P.$w.copy.vgch.zst
    row=$("$FS" copy "$TRACE" "$out" --writer "$w" | sed -n 2p)
    if cmp "$out" "$TRACE" > "$DIR/$P.$w.cmp.log" 2>&1; then v=identical; else v=DIFFERENT; rc=1; fi
    echo "$row,$(stat -c %s "$out"),$(stat -c %s "$TRACE"),$v"
    rm -f "$out"
done
exit $rc
