#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Which existing tools read each candidate's file as it is (run_all.sh, stage interop):
#   interop.sh FSBENCH TRACE FILES_DIR WORKDIR
# GTKWave's own converters stand in for GTKWave: fst2vcd (FST), lxt2vcd
# (LXT2). zstd -d stands in for "a reader without seeking" (VGCH v2's
# container is one zstd stream). The VCD candidate's full text is written
# once to count its bytes against fst2vcd's output.
# CSV on stdout: project,file,tool,exit,bytes_out.
set -uo pipefail
FS=$1 TRACE=$2 FILES=$3 WORK=$4
mkdir -p "$WORK"
P=$(basename "$TRACE" _trace.vgch.zst)
echo "project,file,tool,exit,bytes_out"
run() {   # name tool args...: the tool's stdout counted, its stderr kept
    local file=$1 tool=$2; shift 2
    local n
    n=$("$tool" "$@" 2>> "$WORK/$tool.log" | wc -c)
    echo "$P,$file,$tool,${PIPESTATUS[0]},$n"
}
run "$(basename "$TRACE")" zstd -d -c "$TRACE"
run "$P.fst.lz4.fst" fst2vcd -f "$FILES/$P.fst.lz4.fst"
run "$P.fst_clk.lz4.fst" fst2vcd -f "$FILES/$P.fst_clk.lz4.fst"
run "$P.lxt2.gzip.lxt2" lxt2vcd "$FILES/$P.lxt2.gzip.lxt2"
# The study's VCD, whole, uncompressed.
"$FS" encode "$TRACE" "$WORK" --writer vcd --transforms identity --comps none > "$WORK/vcd.csv" 2>> "$WORK/vcd.log"
echo "$P,vcd candidate (whole trace),fsbench,$?,$(awk -F, '$3=="identity" && $4=="none"{print $9}' "$WORK/vcd.csv")"
# The candidates of the study container: no existing tool reads them.
for f in "$FILES/$P".planar.*.stdy "$FILES/$P".events.*.stdy "$FILES/$P".raw.*.stdy; do
    run "$(basename "$f")" zstd -d -c "$f"
done
