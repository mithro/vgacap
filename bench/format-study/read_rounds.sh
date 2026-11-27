#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# One project's reader benchmark (run_all.sh, stage read):
#   read_rounds.sh FSBENCH TRACE FILES_DIR PROJECT CLOCK_HZ
# First a validate pass of every format's file (the same runs, hashed), then
# 5 rounds; in each, every format is read once (open, scan, port0, 20 seeks),
# the formats in an order rotated by the round. CSV on stdout, the round first.
set -euo pipefail
FS=$1 TRACE=$2 FILES=$3 P=$4 HZ=$5
KINDS=(vgch2 stdy stdy stdy fst)
PATHS=("$TRACE" "$FILES/$P.planar.identity.zstd1.stdy" "$FILES/$P.events.identity.zstd1.stdy"
       "$FILES/$P.raw.xor6.zstd1.stdy" "$FILES/$P.fst.lz4.fst")
echo "round,project,kind,file,pattern,rep,ns,runs,samples,hash"
for i in 0 1 2 3 4; do
    "$FS" read "${PATHS[$i]}" --kind "${KINDS[$i]}" --project "$P" --patterns validate --clock-hz "$HZ" | sed 's/^/v,/'
done
for r in 0 1 2 3 4; do
    for j in 0 1 2 3 4; do
        i=$(( (j + r) % 5 ))
        "$FS" read "${PATHS[$i]}" --kind "${KINDS[$i]}" --project "$P" --patterns open,scan,port0,seek --reps 1 \
            --seeks 20 --clock-hz "$HZ" | sed "s/^/$r,/"
    done
done
