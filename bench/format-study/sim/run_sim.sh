#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The simulation check: the candidates in the real Verilator loop.
#
#   bench/format-study/sim/run_sim.sh [build] [run]
#
# For each project, two testbenches are built with the pilot's Verilator
# command (from the job's build.log): prod, tinytapeout-vga-videos' tb.cpp at
# $TB_COMMIT unchanged, linked with this checkout's vgacap; and study, the
# same tb.cpp through make_tb_study.py, which adds the events and raw
# writers (FS_TB_VARIANT). Then $ROUNDS rounds; in each, six runs start
# together, each pinned to its own core (the cores rotated by round):
#   prod without --trace, prod with (vgch2), and the study binary without
#   --trace, with vgch2, events and raw: one binary, so code layout is the
#   same for its four.
# cpu_report.so prints each run's simulation-thread and process CPU.
#
# Environment: OUT (default <checkout>/tmp/format-study), SIMSRC (a directory
# per project, <shuttle>_<macro>, holding repo/src, build.log and sim.log as
# copied from ~ttvga/ttvga/work/<shuttle>/<macro>/), TTVGA (a
# tinytapeout-vga-videos checkout), VERILATOR, SIM_SECONDS (simulated seconds,
# default 10), ROUNDS (default 5), CPUS (6 cores, default 24-29).
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
OUT=${OUT:-$ROOT/tmp/format-study}
SIMSRC=${SIMSRC:-$ROOT/tmp/sim/src}
TTVGA=${TTVGA:-$HOME/github/TinyTapeout/tinytapeout-vga-videos}
TB_COMMIT=${TB_COMMIT:-e4608cf65880978024bca53fb77ed60dc320b5ef}
VERILATOR=${VERILATOR:-$TTVGA/tmp/dev-tools/oss-cad-suite/bin/verilator}
SECONDS_SIM=${SIM_SECONDS:-10}
ROUNDS=${ROUNDS:-5}
read -r -a CPUS <<< "${CPUS:-24 25 26 27 28 29}"
PROJECTS=${PROJECTS:-"ttihp25a_tt_um_rebeccargb_colorbars ttihp25a_tt_um_MichaelBell_rle_vga tt08_tt_um_a1k0n_nyancat"}
S=$OUT/sim
RES=$OUT/results/sim
mkdir -p "$S" "$RES"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$OUT/progress.log" >&2; }

build() {
    log "sim build"
    # This checkout's vgacap (the production trace writer) and the study's writers.
    cmake -S "$ROOT" -B "$S/vgacap-build" -DCMAKE_BUILD_TYPE=RelWithDebInfo > "$S/vgacap-cmake.log" 2>&1
    cmake --build "$S/vgacap-build" -j 16 --target vgacap_trace vgacap_stream vgacap_zstd > "$S/vgacap-build.log" 2>&1
    cmake -S "$HERE/.." -B "$S/study-build" -DCMAKE_BUILD_TYPE=RelWithDebInfo > "$S/study-cmake.log" 2>&1
    cmake --build "$S/study-build" -j 16 --target study_sim > "$S/study-build.log" 2>&1
    gcc -O2 -shared -fPIC -o "$S/cpu_report.so" "$HERE/cpu_report.c" -lpthread
    mkdir -p "$S/harness"
    git -C "$TTVGA" show "$TB_COMMIT:ttvga/harness/tb.cpp" > "$S/harness/tb.cpp"
    git -C "$TTVGA" show "$TB_COMMIT:ttvga/harness/qspi.h" > "$S/harness/qspi.h"
    git -C "$TTVGA" show "$TB_COMMIT:ttvga/harness/cells/sky130_polyfill.v" > "$S/harness/sky130_polyfill.v"
    (cd "$HERE" && uv run --script make_tb_study.py "$S/harness/tb.cpp" "$S/harness/tb_study.cpp")
    local vb=$S/vgacap-build sb=$S/study-build
    local prodlibs="$vb/libvgacap_trace.a $vb/libvgacap_stream.a $vb/third_party/libvgacap_zstd.a -pthread"
    local studylibs="-Wl,--start-group $sb/libstudy_sim.a $vb/libvgacap_trace.a $vb/libvgacap_stream.a $sb/vgacap/third_party/libvgacap_fst.a $sb/libstudy_lxt2.a $sb/libstudy_lz4hc.a $sb/vgacap/third_party/libvgacap_zstd.a $sb/vgacap/third_party/libvgacap_zlib.a -Wl,--end-group -pthread"
    for p in $PROJECTS; do
        local d=$SIMSRC/$p
        local cmd; cmd=$(grep -o 'verilator --cc.*' "$d/build.log" | head -1)
        local top; top=$(echo "$cmd" | grep -o -- '--top-module [^ ]*' | cut -d' ' -f2)
        # The project's sources: the .v/.sv files after tb.cpp, now from the copy.
        local srcs; srcs=$(echo "$cmd" | sed 's/.*tb\.cpp //' | tr ' ' '\n' | sed "s|.*/repo/src/|$d/repo/src/|" | tr '\n' ' ')
        for v in ${VARIANTS_BUILD:-prod study tick}; do
            local tb=$S/harness/tb.cpp libs=$prodlibs
            [ "$v" = study ] && tb=$S/harness/tb_study.cpp && libs=$studylibs
            [ "$v" = tick ] && tb=$HERE/bench_tick.cpp && libs=$studylibs
            local m=$S/$p/$v
            rm -rf "${m:?}"; mkdir -p "$m"
            cp "$tb" "$m/tb.cpp"; cp "$S/harness/qspi.h" "$m/qspi.h"
            "$VERILATOR" --cc --exe --build -j 1 --prefix Vtop --top-module "$top" -Mdir "$m" -O3 --x-assign fast \
                --x-initial fast -Wno-fatal -Wno-lint -Wno-style -Wno-MULTIDRIVEN -Wno-UNOPTFLAT -CFLAGS -std=c++17 \
                -MAKEFLAGS OPT_FAST=-O2 -I"$d/repo/src" -y "$d/repo/src" --relative-includes -v "$S/harness/sky130_polyfill.v" \
                -CFLAGS "-I$ROOT/include -I$HERE/../src" -LDFLAGS "$libs" "$m/tb.cpp" $srcs > "$m/build.log" 2>&1
            log "sim build $p $v: $(ls -la "$m/Vtop" | awk '{print $5}') bytes"
        done
    done
}

run() {
    local n=0 total=$((ROUNDS * $(echo $PROJECTS | wc -w)))
    local start; start=$(date +%s)
    echo "project,round,variant,cpu,exit,main_thread_s,process_s,wall_s,trace_bytes,load1,clocks" > "$RES/sim.csv"
    for p in $PROJECTS; do
        local args; args=$(grep -m1 -o 'Vtop --clock-hz.*' "$SIMSRC/$p/sim.log" | sed 's/ --meta .*//; s/ --trace [^ ]*//; s/ --out [^ ]*//; s/ --ffmpeg [^ ]*//; s/^Vtop //; s/--seconds [^ ]*//')
        for r in $(seq 1 "$ROUNDS"); do
            local -a vs=(prod:none prod:vgch2 study:none study:vgch2 study:events study:raw)
            local pids=() l; l=$(cut -d' ' -f1 /proc/loadavg)
            for i in 0 1 2 3 4 5; do
                local v=${vs[$i]} cpu=${CPUS[$(( (i + r) % 6 ))]}
                local bin=${v%%:*} var=${v##*:}
                local o=$S/run/$p/r$r-$bin-$var; rm -rf "${o:?}"; mkdir -p "$o"
                local tr=(); [ "$var" = none ] || tr=(--trace "$o/trace")
                ( cd "$SIMSRC/$p/repo/src" && FS_TB_VARIANT=$var LD_PRELOAD=$S/cpu_report.so taskset -c "$cpu" "$S/$p/$bin/Vtop" $args \
                    --seconds "$SECONDS_SIM" --out "$o" --ffmpeg true --quiet --meta "project=$p" "${tr[@]}" > "$o/stdout.log" 2> "$o/stderr.log";
                  echo $? > "$o/exit" ) &
                pids+=($!)
                echo "$cpu $l" > "$o/cpu"
            done
            wait "${pids[@]}" || true
            for i in 0 1 2 3 4 5; do
                local v=${vs[$i]} bin=${v%%:*} var=${v##*:}
                local o=$S/run/$p/r$r-$bin-$var
                local rep; rep=$(grep -o 'cpu-report: .*' "$o/stderr.log" | tail -1)
                local tb=0; [ -f "$o/trace" ] && tb=$(stat -c %s "$o/trace")
                echo "$p,$r,$bin-$var,$(cut -d' ' -f1 "$o/cpu"),$(cat "$o/exit"),$(echo "$rep" | sed 's/.*main_thread_s=\([^ ]*\) process_s=\([^ ]*\) wall_s=\([^ ]*\).*/\1,\2,\3/'),$tb,$(cut -d' ' -f2 "$o/cpu"),$(sed -n 's/.*"clocks_simulated": \([0-9]*\).*/\1/p' "$o/timing.json")" >> "$RES/sim.csv"
                rm -f "$o/trace"
            done
            n=$((n + 1))
            local el=$(( $(date +%s) - start )); local eta=$(( el * (total - n) / n ))
            log "sim run: $n/$total rounds, ${el}s elapsed, ETA ${eta}s (about $(date -u -d "@$(( $(date +%s) + eta ))" +%H:%MZ))"
        done
    done
}

# The in-process check (bench_tick.cpp): every variant's model in one
# process, interleaved by chunk; one process a project, pinned, at once.
tick() {
    local pids=() i=0
    read -r -a TCPUS <<< "${TICK_CPUS:-41 42 43}"
    mkdir -p "$RES"
    for p in $PROJECTS; do
        local o=$S/${TICK_TAG:-tick}/$p; rm -rf "${o:?}"; mkdir -p "$o"
        local q; q=$(grep -m1 -o -- '--qspi [^ ]*' "$SIMSRC/$p/sim.log" | cut -d' ' -f2 || true)
        local hz; hz=$(grep -m1 -o -- '--clock-hz [^ ]*' "$SIMSRC/$p/sim.log" | cut -d' ' -f2)
        local qa=(); [ -n "$q" ] && qa=(--qspi "$q")
        ( cd "$SIMSRC/$p/repo/src" && taskset -c "${TCPUS[$i]}" "$S/$p/tick/Vtop" --out "$o" --clock-hz "$hz" --chunk 65536 \
            --chunks "${TICK_CHUNKS:-4000}" --variants "${TICK_VARIANTS:-none,vgch2,runs,events,changelog,raw,raw_small}" "${qa[@]}" > "$RES/${TICK_TAG:-tick}-$p.csv" 2> "$o/stderr.log"; echo $? > "$o/exit" ) &
        pids+=($!)
        i=$((i + 1))
    done
    log "sim tick: ${#pids[@]} projects on CPUs ${TCPUS[*]}"
    wait "${pids[@]}" || true
    log "sim tick: done"
}

for s in ${*:-build run tick}; do "$s"; done
