#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Why the first round's background CPU and encode2's differ: the first round
# ran the compressor matrix on each block (vgch2: zstd-5, zstd-1, zstd1, ...),
# so zstd1 met a block the two compressors before it had just read; encode2
# runs zstd1 alone. This runs both ways, at once, each pinned to its own CPU:
#
#   cold: --comps zstd1
#   warm: --comps zstd-5,zstd-1,zstd1   (the first round's order, cut after zstd1)
#
#   CPUS="18 19 20 13 15 17" bench/format-study/warm_cache.sh
#
# Output: $OUT/results/warm/<project>.<writer>.<cold|warm>.csv, logs in
# $OUT/logs/warm/. Run by run_all.sh's warm stage.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
OUT=${OUT:-$ROOT/tmp/format-study}
FS=$OUT/build/fsbench
RES=$OUT/results/warm
LOGS=$OUT/logs/warm
SCR=$OUT/files-warm
read -r -a CPUS <<< "${CPUS:-18 19 20 13 15 17}"
mkdir -p "$RES" "$LOGS" "$SCR"
jobs=()
for p in ttihp25a_tt_um_MichaelBell_rle_vga tt08_tt_um_a1k0n_nyancat; do
    t=$OUT/traces/${p}_trace.vgch.zst
    for w in vgch2 planar; do
        jobs+=("$p.$w.cold|$FS encode $t $SCR --writer $w --transforms identity --comps zstd1")
        jobs+=("$p.$w.warm|$FS encode $t $SCR --writer $w --transforms identity --comps zstd-5,zstd-1,zstd1")
    done
done
[ "${#jobs[@]}" -le "${#CPUS[@]}" ] || { echo "warm_cache.sh: ${#jobs[@]} jobs need as many CPUs, got ${#CPUS[@]}" >&2; exit 1; }
pids=()
for i in "${!jobs[@]}"; do
    name=${jobs[$i]%%|*} cmd=${jobs[$i]#*|}
    taskset -c "${CPUS[$i]}" bash -c "$cmd" > "$RES/$name.csv" 2> "$LOGS/$name.log" &
    pids+=($!)
done
rc=0
for i in "${!pids[@]}"; do
    wait "${pids[$i]}" || { echo "warm_cache.sh: ${jobs[$i]%%|*} failed (see $LOGS)" >&2; rc=1; }
done
rm -f "$SCR"/*.trace.zst "$SCR"/*.stdy
exit $rc
