#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# One project's job-reader benchmark (run_all.sh, stage read2):
#   read2_rounds.sh FSBENCH TRACE FILES_DIR PROJECT CLOCK_HZ
# The readers of a job (derive.py: tracetool info, vgacap-sample, tracetool
# uohash) and a lazy-BLKH open, on every stored candidate's file: 3 rounds,
# the formats in an order rotated by the round, each read once a round
# (open, info, infoscan, sample, uohash, 10 seeks). CSV on stdout, the round first.
set -euo pipefail
FS=$1 TRACE=$2 FILES=$3 P=$4 HZ=$5
KINDS=(vgch2 vgch2lazy stdy stdy stdy stdy stdy stdy)
PATHS=("$TRACE" "$TRACE" "$FILES/$P.planar.identity.zstd1.stdy" "$FILES/$P.events.events-planar.zstd1.stdy"
       "$FILES/$P.changelog.changelog-planar.zstd1.stdy" "$FILES/$P.raw.planar-runs.zstd1.stdy"
       "$FILES/$P.events.identity.zstd1.stdy" "$FILES/$P.raw.xor6.zstd1.stdy")
N=${#KINDS[@]}
echo "round,project,kind,file,pattern,rep,ns,runs,samples,hash"
for r in 0 1 2; do
    for j in $(seq 0 $((N - 1))); do
        i=$(( (j + r) % N ))
        "$FS" read "${PATHS[$i]}" --kind "${KINDS[$i]}" --project "$P" --patterns open,info,infoscan,sample,uohash,seek --reps 1 \
            --seeks 10 --clock-hz "$HZ" | sed "s/^/$r,/"
    done
done
