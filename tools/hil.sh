#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# The hardware-in-the-loop check, for an operator standing in front of a
# board. All it does is run tools/hil.py in this checkout's uv environment,
# from wherever the caller happens to be: the checks, the options and the
# report are all in hil.py, and `tools/hil.py --help` describes them.
#
#   tools/hil.sh --board fpga-1 --link ws://127.0.0.1:18034/serial \
#       --design tt_um_vgacal_bars --clock-hz 500000 docs/results/
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
exec uv run --project "$root" python "$root/tools/hil.py" "$@"
