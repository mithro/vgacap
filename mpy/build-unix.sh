#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build MicroPython's unix port with the vgacap module on the simulated
# board (mpy/micropython.mk): build/mpy-unix/micropython, which
# python/tests/test_mpy_module.py drives. The tests skip without it.
#
#   mpy/build-unix.sh [MICROPYTHON_DIR]
#
# MICROPYTHON_DIR (default build/micropython) is cloned if it does not
# exist, and checked out at the pinned commit either way: the MicroPython
# the Tiny Tapeout demo boards run (1.29.0-preview b006887db6, read off
# fpga-4 on 2026-11-24).
set -euo pipefail

MICROPYTHON_REF=b006887db6e4b18bce967ea87c7e63398f60b296
ROOT=$(cd "$(dirname "$0")/.." && pwd)
MP=${1:-$ROOT/build/micropython}
OUT=$ROOT/build/mpy-unix

if [ ! -d "$MP/.git" ]; then
    git clone --filter=blob:none https://github.com/micropython/micropython.git "$MP"
fi
git -C "$MP" fetch --quiet origin "$MICROPYTHON_REF" || true
git -C "$MP" -c advice.detachedHead=false checkout --quiet "$MICROPYTHON_REF"

make -C "$MP/mpy-cross" -j"$(nproc)"
make -C "$MP/ports/unix" submodules
make -C "$MP/ports/unix" -j"$(nproc)" USER_C_MODULES="$ROOT" BUILD="$OUT" PROG=micropython
echo "built $OUT/micropython"
