#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The host load every 30 s into OUT/load.log, in run_all.sh's format, while
# PID runs: load_logger.sh OUT PID. run_ticks.sh starts it for its sets.
OUT=$1 PID=$2
while kill -0 "$PID" 2>&1 > /dev/null; do
    { date -u +%FT%TZ; cat /proc/loadavg; vmstat 1 2 | tail -1; } >> "$OUT/load.log" 2>&1
    sleep 30
done
