<!-- SPDX-License-Identifier: Apache-2.0 -->
# The simulation trace format: a measured comparison

2026-11-27. Branch [`trace-format-study`](https://github.com/mithro/vgacap/tree/trace-format-study) of [vgacap](https://github.com/mithro/vgacap), off `origin/main` at [`f3f84ab`](https://github.com/mithro/vgacap/commit/f3f84ab). The benchmarks are in [`bench/format-study/`](../../bench/format-study/), and every number below comes from them.

**What this is.** It compares formats for the simulation trace: the record of every pin of a Tiny Tapeout design at both halves of every clock. It is the write-up, performance analysis and benchmarking that the owner asked for. In his words: "Do a proper write up, performance analysis and benchmarking of different potential formats (with pros and cons) and why the given format was chosen. A pro is *not* 'I've already implemented'." The current format, VGCH version 2, is judged here as one candidate among the others. Having been implemented gives it no credit.

**What this is not.** It covers the trace only. The frames and hash formats are out of scope, and so is the `fast-frame-hash` branch. No number here includes a sampler or a hash. Where reading is timed, the trace's runs go to a consumer that only counts them.

## Contents

1. [Summary](#summary)
2. [Requirements and their weights](#requirements-and-their-weights)
3. [The candidates](#the-candidates)
4. [Method](#method)
5. [Results](#results)
6. [VGCH v2's own design choices: what each costs and buys](#vgch-v2s-own-design-choices-what-each-costs-and-buys)
7. [Pros and cons per candidate](#pros-and-cons-per-candidate)
8. [Recommendation](#recommendation)
9. [How the current format was chosen](#how-the-current-format-was-chosen)
10. [Fix round 1: what changed](#fix-round-1-what-changed)
11. [Fix round 2: what changed](#fix-round-2-what-changed)
12. [Reproducing](#reproducing)
13. [References](#references)

## Summary

This is the study after its second round of review ("fix round 2"). Round 1 measured what the first round had estimated, and changed the recommendation. Round 2 found that the loop's background plans had written corrupt files, fixed the two causes, ran those plans again with every file read back, and measured memory and a full-pin export. [Fix round 1: what changed](#fix-round-1-what-changed) and [Fix round 2: what changed](#fix-round-2-what-changed) list each change.

- **No measured plan meets the budgets on the busy designs.** The busy designs are nyancat, rle_vga and atari2600, where 77-100% of clocks change a pin.
  - No plan keeps the simulation thread within R1's 5% on all three.
  - No plan keeps the trace's share of R2 within this study's 1% bar on any of them. The 1% bar is the study's own choice; the spec sets 5% for everything after the simulation.
  - Only planar written on the simulation thread stays within the whole 5% post-simulation budget there, and it misses R1 on all three.
  - [Pass or fail, per plan](#pass-or-fail-per-plan) is the verdict table.
- **R1, in the Verilator loop.** Each figure is % of the untraced simulation, the median of 4 processes. vgch2 and lean are from round 2's lean set, the background plans from its background set, raw from 4 processes of its own. Every file of every set read back right. The budget is 5%:

  | Plan | nyancat | rle_vga | atari2600 | colorbars |
  |---|---:|---:|---:|---:|
  | vgch2 (current) | 14.0 | 19.1 | 7.7 | −0.2 |
  | lean (vgch2's file) | 14.1 | 17.5 | 7.7 | −0.8 |
  | events, stored as events | 8.3 | 7.5 | 3.9 | 3.6 |
  | events, stored as planar | 8.8 | 8.1 | 4.1 | 3.3 |
  | changelog, stored as planar | 7.8 | 5.4 | 3.1 | 2.7 |
  | planar on the simulation thread | 13.0 | 10.3 | 6.0 | 2.8 |
  | raw | 6.0 | 3.0 | 2.6 | 4.8 |

  - Round 2 moved the background plans by −1.3 to +2.5 points on the busy designs. vgch2, run beside them as the check, moved by −0.6 to +1.3. Every R1 verdict is the same as round 1's ([round 1 against round 2](#round-1-against-round-2-of-the-untraced-model-median-of-the-processes)).
  - Raw's sets ran at about twice the host's normal load (1.8 and 2.3 a CPU, median). Its figures may be high.
  - colorbars is quiet: its figures are within the noise of zero, and vgch2's and lean's median is below it.
- **The lean writer writes vgacap's file, and its saving in the loop is small.**
  - It is byte-identical to the published trace on all 11 traces (`cmp`).
  - In the loop, lean over vgch2: 0.99 [0.93, 1.50] on nyancat, 0.91 [0.88, 0.92] on rle_vga and 0.97 [0.95, 1.03] on atari2600: each process's ratio, median of 4 [lowest, highest].
  - Only rle_vga shows a clear saving, 9%. On nyancat and atari2600 the processes disagree, and the median is within their spread. Round 1's set gave 0.90, 0.90 and 0.94.
  - On the two quiet designs the ratio (0.80, 1.01) is a per-variant offset of about 1-2 ns a clock, within the noise.
  - It is small, safe and not urgent. The format owner found that `vgacap_trace_block_pair` is not inlined; forcing it inline gave 0.88 (nyancat) and 0.77 (rle_vga) in one replay run. That is a direction only, not a loop figure.
- **R2 fails plainly.** A job reads its trace three times: `tracetool info`, `vgacap-sample` and `tracetool uohash`. The trace's share of R2 (%, background at encode2's median), with `tracetool info` as a scan and as a header-only read:

  | Plan | nyancat | rle_vga | atari2600 | nyancat, no scan | rle_vga, no scan | atari2600, no scan |
  |---|---:|---:|---:|---:|---:|---:|
  | vgch2 (current) | 17.6 | 25.0 | 11.1 | 10.1 | 11.1 | 5.9 |
  | events, stored as planar | 7.4 | 8.2 | 3.3 | 6.3 | 6.6 | 2.7 |
  | changelog, stored as planar | 8.8 | 7.9 | 4.1 | 7.8 | 6.5 | 3.6 |
  | planar on the simulation thread | 4.5 | 2.5 | 1.3 | 3.5 | 1.0 | 0.8 |

  - The job uses none of `tracetool info`'s change counts: it reads only `blocks`, `clocks` and `open.slice_*`. A header-only `tracetool info` removes the scan for every format with no format change. For vgch2 that is the largest free saving found: 30 s (nyancat), 63 s (rle_vga) and 38 s (atari2600) a job.
  - Planar still has the lowest share without the scan, by less: 3.5% against vgch2's 10.1% on nyancat.
- **Planar's size and `uo_out` gains stand.**
  - Planar is 54 MB against vgch2's 416 MB on nyancat, 9.0 against 597 on atari2600, and 0.62 against 18.4 on rle_vga.
  - `vgacap-sample`'s pass, which reads `uo_out` and `rst_n`, takes 10.1 s on nyancat against vgch2's 18.3, and 0.04 s on rle_vga against 33.0.
  - vgch2's merged full scan of every port is faster than planar's on 10 of the 11 traces, and ties on nyan: 28.3 s against 49.6 on nyancat.
  - A full-pin FST export takes the same CPU from either file on nyancat (448 s) and atari2600 (758 against 766 s), and 12% more from planar on rle_vga (579 against 516 s). Writing the FST is most of it.
- **Background CPU is noisy, and the verdicts do not depend on it.** Identical compression work measured up to 2.5 times apart, with what else ran on the socket. R2 is given at each plan's lowest measured figure and at its median, and every pass or fail is the same at both.
- **Lazy block starts cut the open by more than half on the busy traces, with no format change.** vgch2's open falls from 111 to 29 ms on nyancat and from 242 to 97 ms on rle_vga, and a one-frame seek on nyancat from 129 to 51 ms. vgadonut still opens in 112 ms. The block-order check moves from open to read time, and a corrupt `BLKH` can deliver runs before the error.
- **The existing formats lose on the simulation thread.**
  - FST written directly costs 4.1-5.1 times vgch2 on the busy designs, measured in the same replay run. A one-frame seek takes 0.6-1.0 s.
  - A cut-short FST is fragile: files cut to 10% or 50% gave no samples, or crashed libfst's reader.
  - LXT2 and VCD are worse still.
  - Exporting to FST, as `vgacap-export` does now, stays the way into GTKWave and Surfer.
- **VGCH v2 was chosen without a comparison.**
  - The spec's own three-way comparison (sigrok, FST, VGCH) was planned for the pilot and never run.
  - The planar alternative was deferred "until measured need appears". The pilot then measured that need.
  - [How the current format was chosen](#how-the-current-format-was-chosen) lists each assumption and what the data says about it.

## Requirements and their weights

The requirements come from the design spec, [`2026-11-24-raw-frames-and-hash-methods-design.md`](https://github.com/mithro/tinytapeout-vga-videos/blob/main/docs/superpowers/specs/2026-11-24-raw-frames-and-hash-methods-design.md) at [`97e74b1`](https://github.com/mithro/tinytapeout-vga-videos/commit/97e74b1). Its sections are "Layer 1: the trace" and "Performance budget". The weights are this study's. They say how a failure counts:

- **Must.** A candidate that fails it is out, unless the failure can be fixed without changing the format.
- **High** and **medium.** Ranked trade-offs.

| # | Requirement | Source in the spec | Weight | How it is measured here |
|---|---|---|---|---|
| R1 | Recording costs at most 5% of simulation wall time, on the simulation thread. That is about 10 ns a clock at the fastest project, so the busiest designs leave little room. | Performance budget, first bullet | must | ns per clock over an untraced run: the replay, and the Verilator loop in one process |
| R2 | Everything after the simulation, plus the compressor thread, costs at most 5% of the simulation's CPU. The samplers, hashes, PNGs and copies share that 5%, so the trace's own share has to be small. This study counts the trace as passing at 1% or less, with the rest left to the derived outputs. That 1% bar is the study's own choice; the spec sets only the 5%. | Performance budget, second bullet | high | the background thread's CPU plus every read of the trace a job makes (`tracetool info`, `vgacap-sample`, `tracetool uohash`), as a share of the pilot's untraced simulation CPU |
| R3 | The trace is the record. It keeps every pin at both halves of every clock, and the clock bit is regenerated exactly. A run can be longer than 2^32 samples. | What is recorded; The clock in a run-encoded trace; Size and format | must | every reader's runs hash the same as vgacap's reader's (validation table) |
| R4 | Size. The bulk volume has tens of TB. A raw trace of every run would be 8.5 TB, and runs cut that "by orders of magnitude". Size also sets how long the published archive takes to sync. | Size and format | medium | bytes per clock, and in total for each of the 11 traces |
| R5 | Read paths. One sequential pass feeds every sampler. That pass is "likely the largest post-simulation cost for busy, fast designs", and it counts against R2. A clock range has to seek to the right block (`--from/--to`, `frames compare`). An export of one pin group reads only that group's data. | Size and format; Reading the trace back; Viewing in GTKWave and Surfer | high (scan), medium (seek, one group) | a full scan to a no-op consumer; each read a job makes; a 420,000-clock window from a fresh open; `uo_out` alone |
| R6 | A trace cut short (by a kill after the grace period) loses at most its last two blocks. The reader rebuilds the missing index. | Size and format | must | each file truncated, and each writer killed halfway: the samples a reader recovers |
| R7 | Traces open in GTKWave and Surfer. That can be through `vgacap-export`, since the spec only requires the trace to be "viewed by exporting to FST or VCD". The format is documented precisely enough to read without vgacap's code. | Why (owner's decisions); Size and format; Viewing | must for the documentation; medium for opening it directly in a viewer | which tools read the file as it is, checked with GTKWave's `fst2vcd` and `lxt2vcd` |
| R8 | Streaming. A reader without seek support can still stream the blocks, for example with plain `zstd -d`. | Size and format | low | a property of the container |
| R9 | Implementation and maintenance cost: lines of code, corner cases, spec length. | (the owner's brief) | medium | non-comment lines; the spec's words; its listed error cases |

R1 and R2 are the requirements the pilot found missed. Recording was over 5% on 6 of the 11 projects. Post-simulation CPU was over 5% on 10 of them, and on those most of the excess is SHA-256 in the samplers, which is outside this study. So R1 decides the most here, R2 comes next, and then R5's scan.

## The candidates

Every candidate gets the same input. That input is what tb.cpp has at each half clock: the model's 8 port bytes, read as one 8-byte "raw image" ([`tb.cpp`'s `RawPorts`](https://github.com/mithro/tinytapeout-vga-videos/blob/e4608cf65880978024bca53fb77ed60dc320b5ef/ttvga/harness/tb.cpp#L277-L331)).

Every candidate with a fast path keeps tb.cpp's raw compare. A clock whose two images equal the last sample, with `clk` low and then high, costs one compare and one predicted branch. Only a clock where a pin changed reaches the encoder. The candidates differ in what that encoder writes, and in what a background thread then does with it.

"Port" below means one of the seven non-clock ports:

- `uo_out`
- `uio_out`
- `uio_oe`
- `ui_in`
- `uio_in`
- `rst_n`
- `ena`

`clk` is never stored, except in FST-with-clk and the sigrok-like layout. It is the sample's parity, as in VGCH v2.

| Candidate | Stored | Simulation thread, per changed clock | Background thread | Code |
|---|---|---|---|---|
| **vgch2** (current) | `RUNS` pairs | two pushes | zstd 1 | [`trace_write.c`](../../src/trace/trace_write.c) |
| **lean** | `RUNS` pairs, vgch2's file | one call | zstd 1 | [`trace_write_lean.c`](../../bench/format-study/src/trace_write_lean.c) |
| **planar** | 7 run streams | one call, a pair per changed port | zstd 1 per stream | [`w_mine.c`](../../bench/format-study/src/w_mine.c) `fs_planar_slow` |
| **events** | 1 change stream | one call | zstd 1 | `fs_events_slow` |
| **events, stored as planar** (evplanar) | 7 run streams | as events | convert, then zstd 1 per stream | [`codec.c`](../../bench/format-study/src/codec.c) `fs_events_to_planar` |
| **changelog** | 7 run streams | one call, 24 bytes stored | convert, then zstd 1 per stream | `fs_clog_slow`, `fs_clog_to_planar` |
| **raw** | both raw images of every clock | none: two stores every clock | a transform, then a compressor | `fs_raw_clock`, `codec.c` |
| **sigrok-like** | 6-byte samples with `clk` | as raw | deflate | as raw |
| **FST** | libfst's | libfst's calls | libfst's | [`w_ext.c`](../../bench/format-study/src/w_ext.c) |
| **LXT2** | GTKWave's | `lxt2_wr_*` calls | none | [`third_party/lxt2`](../../bench/format-study/third_party/lxt2) |
| **VCD** | text | a formatter | zstd 1, deflate 6 or none | `w_ext.c` |

The details of each:

- **vgch2**, [VGCH v2](../trace-format.md).
  - Each block holds one `RUNS` chunk of pairs: a 6-byte tt-um-43 value (43 bits, clock bit cleared), then a LEB128 run in samples. The block also has `BLKH` and `META`.
  - A block closes at 2^24 clocks, or at 16 MiB − 64 KiB of payload.
  - On a changed clock, tb.cpp's `raw_slow` packs both samples and calls `vgacap_trace_push2`. That makes two pushes, and writes a pair for each sample that changed.
  - In the background: zstd level 1 with an XXH64 checksum, one zstd frame a block, and a seek table at the end.
  - The code is vgacap's writer, unchanged, compiled with its one `ZSTD_compress2` call renamed ([`vgch_hook.h`](../../bench/format-study/src/vgch_hook.h)). Its output is byte-identical to all 11 published traces (`cmp`).
- **lean**: vgacap's writer with `push2`'s slow path replaced by one call a changed clock ([The lean writer](#the-lean-writer)). Its file is vgch2's, byte for byte, on all 11 traces.
- **planar**.
  - Seven streams a block, one a port. Each is (u8 value, LEB128 run in samples) pairs covering the block, compressed on its own.
  - On a changed clock, it writes a pair for each port that changed.
  - A per-signal value-change list with delta-coded times (FST's model) holds the same information as (Δt, value) pairs instead of (value, run). So planar stands for it too.
- **events**.
  - One time-ordered stream a block. It starts with the 7 port bytes at the block's first sample.
  - Each changed sample then adds a LEB128 Δ (samples since the last event), a mask byte (bit k: port k changed) and the changed ports' new bytes. This is VCD's ordering in binary.
- **events, stored as planar**: the events writer on the simulation thread. The background thread converts each block's events to planar's streams, then compresses them.
- **changelog**.
  - On a changed clock, it stores the sample number and both raw images, 24 bytes, with no encoding.
  - The background thread converts the records to planar's streams (`changelog-planar`), then compresses each stream.
  - A block holds up to 64 MiB of records.
- **raw**.
  - Both raw images of every clock, 16 bytes, with no comparison at all. Two 8-byte stores a clock, changed or not. There is no slow path, only the block end.
  - The background thread packs the images and checks the clock bit. Then it applies a transform:
    - `pack6`: the 42-bit value, 6 bytes;
    - `xor6`: pack6 XOR the previous sample, a Gorilla-style delta;
    - `byteplanes`: Blosc's byte shuffle, 7 planes;
    - `xor-byteplanes`;
    - `xor-bitplanes`: Blosc's bitshuffle after the XOR, the bit planes of each port;
    - `planar-runs`: planar's streams.
  - Then it compresses with zstd −1, 1 or 3, or LZ4.
  - A block is 2^21 clocks (32 MiB); `raw_small` has 2^15 (512 KiB).
- **sigrok-like**: the spec's candidate 1, sigrok `.sr`. Samples of 6 bytes with `clk`, deflated. It is measured as `pack6clk` deflated in 2^21-clock pieces, without the zip directory. Deflate levels are 1 and 6; zip's default is 6.
- **FST**: written directly through libfst, the copy vendored in vgacap, with the same calls as `vgacap-export`.
  - LZ4 packing, parallel mode, times in ps, 7 signals.
  - On a changed clock: libfst's time change, and a value change for each changed port.
  - **FST-with-clk** adds `clk` as an eighth signal.
- **LXT2**: GTKWave's `lxt2_write`, at gtkwave `e40ba79`, with its defaults, the 7 ports, times in ps. It compresses on the calling thread.
- **VCD**.
  - Text: `b` and 8 digits for vectors, one digit for scalars, as GTKWave writes it.
  - On a changed clock, a hand-written formatter writes `#time` and the changed values.
  - It is compressed in 16 MiB pieces, as a `.vcd.zst` or a `.vcd.gz`.
  - GTKWave's `fst2vcd` of the FST file gives a VCD within 79 bytes of this one's size: 1,445,756,933 against 1,445,756,854 bytes, on colorbars.

The compressors are measured on every byte stream above that has a background part:

- zstd at levels −5, −1, 1, 3 and 9;
- zstd 1 with a 2^27 long-distance window;
- zstd 1 without the checksum;
- LZ4, from the copy vendored in libfst;
- LZ4-HC at level 9, from [lz4 v1.10.0](https://github.com/lz4/lz4/tree/v1.10.0/lib), added for the study;
- deflate at levels 1 and 6.

**The study container.** The planar, events, changelog and raw candidates are written in a container of the study's own.

- It has a file header. Each block then has a header (magic, first sample, sample count, and the length of each stream), followed by its compressed streams. Each stream is a zstd frame of its own. An index and a footer end the file.
- A reader with no footer walks the block headers.
- It is seekable and survives truncation.
- It is not VGCH v2's container:
  - `zstd -d` cannot read it (the interop table);
  - its index holds each block's first sample, which zstd's seek table cannot.
- Every planar-stored candidate writes the same canonical runs, and each converted file matches vgacap's reader on all 11 traces (the validation table). The files are not byte-identical. Each writer ends a block when its own buffer fills, so the block boundaries differ: in the loop, nyancat's first 262 M clocks made 21 blocks from planar, 40 from events to planar and 72 from changelog. So atari2600 is 9.02 MB from the planar writer, 9.38 MB from events to planar and 9.83 MB from changelog to planar. The read benchmarks read each file.

**Considered and not measured.**

- GHW (GHDL's own format).
- FSDB and WLF: proprietary, with no writer available here.
- VZT: GTKWave's, superseded by FST.
- Columnar stores (Parquet, Arrow, HDF5). Applied to these 7 columns, they are planar or byteplanes with a different container and much more code.

## Method

### Inputs

The inputs are the 11 traces of the pilot, as published under `/srv/data.wafer.space/tinytapeout-vga-videos/frames/`. The study reads copies of them; their SHA-256 values are in `results/traces.sha256`. Every candidate sees every clock of every trace, and no input is synthetic.

The traces were written by vgacap at [`c7176da`](https://github.com/mithro/vgacap/commit/c7176da). In `src/trace` and `src/stream` that commit is identical to `f3f84ab`, apart from docs.

They are replayed by reading them with vgacap's own reader. Their runs are expanded back into the two raw images a clock that the testbench had. Replayed whole through vgacap's writer, every one of the 11 traces gives back a file that is byte-identical to the published one ([the lean writer's table](#the-lean-writer-writes-vgacaps-file), its vgch2 column). So the replay feeds the writers exactly what the simulation fed them.

[Inputs: the 11 traces](#inputs-the-11-traces-and-the-code-paths-they-take) gives the share of clocks that take each of vgch2's code paths:

- the fast run-extend;
- the slow change, a pin changed in that clock;
- the block boundary.

Those shares range from 0.2% slow clocks (metaballs) to 100% (rle_vga, whose QSPI clock changes `uio_out` on every sample). The bench this replaces, [`bench_runs.c`](../../bench/bench_runs.c), replayed one synthetic frame, so it never had the expensive case at the rates the real designs have.

### The lean writer

The lean writer is [`trace_write_lean.c`](../../bench/format-study/src/trace_write_lean.c): vgacap's [`trace_write.c`](../../src/trace/trace_write.c), included unchanged, with one function replaced, `push2`'s slow path.

- In vgacap, `push2`'s slow path is two `vgacap_trace_push` calls. A changed clock goes through it, then through `vgacap_trace_slow` once per changed sample.
- In the lean writer, a changed clock is one out-of-line call that does both samples. It makes every check the two pushes make: the clock bits, the bits above `sample_bits`, a failed writer, and a block end.
- Any case it does not handle goes to the original two pushes, so the block split is vgch2's own:
  - a block end by clocks at sample n or n + 1;
  - a block that could end by size inside the pair;
  - sample 0;
  - a bad sample;
  - a failed writer.
- [`test_lean.sh`](../../bench/format-study/test_lean.sh) (stage `lean`) writes each of the 11 published traces whole through the lean writer and through vgacap's, and compares each result with the published file by `cmp`. It fails on any difference.
- In the Verilator loop the lean writer is called through tb.cpp's own glue ([`lean_api.h`](../../bench/format-study/src/lean_api.h)), as the vgch2 variant is.

The first round's `runs` writer was the study's own writer of the same pairs. It split blocks by its own rule, so its files were not vgch2's on any trace whose blocks end by size. This round does not use it for any claim.

### The simulation thread: two measurements

1. **Replay** (`fsbench writer`, [`fsbench.c`](../../bench/format-study/src/fsbench.c)).
   - Each project's trace is cut into chunks of 2^20 clocks. Every S-th chunk is used, about 96 a project: 1.7-7.2% of each trace, spread over all of it. The replayed chunks change pins on the same share of clocks as the whole trace, to within 0.2 points (replayed chunks table).
   - A chunk is expanded once into memory. Then each writer runs over it in 5 rounds, the writers in a rotated order, each timed by `CLOCK_THREAD_CPUTIME_ID`. Writers keep their state from chunk to chunk.
   - The figure is the paired difference to a null writer (the same loop and loads, nothing recorded): the median over the 5 rounds for each chunk, summed over the chunks, over the clocks.
   - The 95% interval is a bootstrap over the chunks (2,000 resamples). The tables also give the spread of the 5 rounds.
   - The job is pinned with `taskset`, one job a physical core, and the load is logged with each chunk.
   - The background parts are off here. A block is handed over and dropped (vgch2's thread still runs, compressing nothing). So this measures the simulation thread alone, and runs 10-30 times faster than a real simulation.
2. **The Verilator loop, in one process** ([`sim/bench_tick.cpp`](../../bench/format-study/sim/bench_tick.cpp)). This is the authoritative number for R1.
   - One process holds one Verilated model per variant, for one project. Each model is built with the pilot's own Verilator command, taken from the job's `build.log`. It runs from the project's `src` directory, as `job.py` runs it.
   - All models start from reset. They are stepped in turn in 65,536-clock chunks, with tb.cpp's own tick loop, raw compare and trace calls. 4,000 chunks make 262 million clocks a model, about 10 simulated seconds.
   - Each chunk is timed twice: by the thread CPU clock, and by the wall clock.
   - The writers run with their real background threads, double-buffered.
   - Every variant runs the same design state at the same moment on the same core, so host drift cancels in the paired difference to the untraced model.
   - One process still has a layout of its own: where its models and buffers sit in memory, and which turn each takes. So each set is run as several processes, each with the variants in another order. The tables give the median over the processes, and the lowest and highest.

The fix round's loop sets, run by [`sim/run_ticks.sh`](../../bench/format-study/sim/run_ticks.sh):

| Set | Processes a project | Variants | CPUs a process |
|---|---:|---|---|
| main (`fx1a` to `fx1d`) | 4 | none, vgch2, lean, events, evplanar, planar, changelog | 2, pinned, the simulation thread on the first |
| raw (`fx1ra` to `fx1rd`) | 4 | none, raw | 2, pinned |
| scope (`fx1s`) | 1 | the main set's | not pinned: a systemd scope with `CPUQuota=200%`, as a job runs |
| bg, fix round 2 (`fx2a` to `fx2d`) | 4 | none, vgch2, events, evplanar, planar, changelog | 2, pinned, as main |
| bgscope, fix round 2 (`fx2s`) | 1 | the bg set's | as scope |
| lean, fix round 2 (`fx2la` to `fx2ld`) | 4 | none, vgch2, lean | 2, pinned, as main |

- The projects are nyancat, rle_vga, colorbars, atari2600 and vgadonut.
- In the main set, the variants' orders are:
  - `fx1a`: none, vgch2, lean, events, evplanar, planar, changelog;
  - `fx1b`: the reverse;
  - `fx1c`: events, none, planar, vgch2, changelog, lean, evplanar;
  - `fx1d`: lean, changelog, vgch2, evplanar, none, planar, events.
- The variants' background threads:
  - vgch2 and lean: vgacap's compressor;
  - events: zstd 1 of the events stream;
  - evplanar: the events stream converted to planar by the converter the encode stage uses, then zstd 1 of each stream;
  - planar: planar written on the simulation thread, then zstd 1 of each stream;
  - changelog: its records converted to planar, then zstd 1;
  - raw: `xor6`, then zstd 1.
- **Fix round 2 re-ran the background plans.** Round 1's main and scope sets wrote corrupt files for events, evplanar, planar and changelog, for two reasons:
  - one stdio buffer was shared by every background writer of a process ([`bg.c`](../../bench/format-study/src/bg.c) had it `static`), so their bytes mixed;
  - the converters wrote the streams in raw byte order, which is port order only in the replay's layout, not in Verilator's.
  Both are fixed. The bg sets run those plans again, with vgch2 beside them as the reference. Their R1 figures replace round 1's for those four plans.
- **Fix round 2 re-ran vgch2 and lean too.** Their files in round 1's main and scope sets read back right, but those sets' other files did not, so no figure is taken from them. The lean set (`fx2la` to `fx2ld`) runs vgch2 and lean alone with the untraced model, every file read back. Its orders are none, vgch2, lean; the reverse; vgch2, none, lean; and lean, none, vgch2.
- **Every file is read back.** After each pair of sets, [`sim/readback.py`](../../bench/format-study/sim/readback.py) reads every trace written. Each file's full hash, samples and `uo_out` run count must equal lean's (or vgch2's, the same file) from the same process, or [`run_ticks.sh`](../../bench/format-study/sim/run_ticks.sh) fails. Raw's sets, run in round 1 with one background writer a process, were read back against `fx2a`'s vgch2.
- Raw has a set of its own. Its 32 MiB blocks and its background thread share no process, and so no L3 traffic, with another variant's. That removes the first round's bias (raw's background work fell partly on the other variants).
- **Stalls.** A thread-CPU clock cannot see a simulation thread that waits for its writer's thread, since a waiting thread uses no CPU. So each chunk also has its wall time. For each variant, the stall measure is the wall time minus the thread CPU, over the untraced model's same difference. The study writers also count their own hand-overs that waited, and the wall time those took. vgacap's writer keeps no such count.
- **Concurrency.** Two processes a project ran at once, `fx1a` with `fx1b` and then `fx1c` with `fx1d`: 10 processes on 20 CPUs of the second socket. The read and encode benchmarks of this round ran on the first socket at the same time. Fix round 2's bg sets ran the same way, with the FST export timing on the first socket beside them. The paired design cancels most of this; the processes' spread shows what is left.
- **The first round's sets are no longer in the tables.** They were `tick2a` to `tick2d` (two CPUs a process) and `tick` to `tickG` (one CPU a process). Each process held several of the study's background writers, which shared the one stdio buffer, so their files were corrupt as round 1's were. Their raw results stay in `results/sim/`.
- **Per-process intervals.** The per-process 95% intervals are a bootstrap over chunks that follow each other, and so are correlated. They are too narrow. The range across the processes is the interval to use.

A third method was tried and dropped. Six whole testbench runs started at once, side by side, each pinned to its own core, with the cores rotated by round ([`sim/run_sim.sh`](../../bench/format-study/sim/run_sim.sh) `run`). The same untraced binary took between 50.9 s and 103.5 s in the same rounds. That spread is ±35%, larger than every effect being measured. It confirms the pilot review's warning. Its raw results are kept (`results/sim/sim.csv`), but no conclusion rests on them.

### Background CPU and size

`fsbench encode` ([`bg.c`](../../bench/format-study/src/bg.c)) runs each writer over the whole trace, every clock. Each block goes to the background part on the same thread, and the background part is timed by the thread CPU clock:

- transforms, raw only;
- each compressor of the matrix;
- the file.

The first round ran the matrix on every block, except where measuring it all would take hours:

| Candidate | Blocks measured | Sizes and CPU |
|---|---|---|
| Run formats, traces of 100 MB and over (nyancat, vgadonut, atari2600) | every 4th block | projected by blocks or samples |
| Raw | every 16th block of 2^21 clocks | projected by samples |
| VCD | every 8th chunk | projected by clocks |
| FST with zlib | every 4th chunk | projected by clocks |
| FST with clk, LXT2 | every 16th chunk | projected by clocks |

Each candidate's main configuration is written whole as a real file: vgch2 by vgacap's writer, the others in the study container, FST with LZ4 by libfst. Those files are what the readers read.

The first round measured each background figure once. This round adds three more measurements of the configurations R2 depends on (stage `encode2`): each alone, at zstd 1, every block, three times, apart in time. The R2 tables use encode2's median, and also the lowest figure of all four measurements.

- **Spread.** The same compression work measured up to 2.5 times apart: vgch2 on rle_vga took 6.83 s in the first round and 14.5-17.2 s in encode2, for identical input and output bytes.
- **Not the cache.** [`warm_cache.sh`](../../bench/format-study/warm_cache.sh) ran zstd1 alone, and after zstd-5 and zstd-1 had read the same block, as the first round's matrix did. Both took the same CPU, and both were close to the first round's figure. So the first round's matrix did not flatter it.
- **What it tracks.** The spread follows what else ran on the socket. encode2 ran 20 whole-trace encodes at once on the first socket, the raw candidates' 16-bytes-a-clock passes among them, and the CPUs' hyperthread siblings were not reserved. A memory-bound compressor, such as zstd on rle_vga's 1,100-to-1 data, slows the most.
- **A third estimate.** The loop's own background threads ran beside a real simulation. Scaled to the whole trace, they come out at 0.50-1.04 of encode2's median on the busy traces, lower in all but one case. The furthest are rle_vga's events and planar, about half ([three ways table](#background-cpu-three-ways-s-for-the-whole-trace-the-loops-scaled-against-the-whole-trace-runs)).
- **Provenance.** Four of the first round's encode sets were run by hand, outside `run_pool`: the `runs`, changelog, events-to-planar and raw-to-planar encodes. `run_all.sh` contains them now, but those results were not produced by it. They have no `.done` files, and the events-to-planar `#pass` lines name their files by relative paths. encode2 was run by `run_all.sh`.

Background CPU is compared with the pilot's untraced simulation CPU (`data/pilot_baseline.csv`, from the pilot's [`measurements.md`](https://github.com/mithro/tinytapeout-vga-videos/blob/t20-pilot/docs/reports/2026-11-pilot/measurements.md)). The simulation here ran 60 s per project.

### Reading

`fsbench read` ([`rd.c`](../../bench/format-study/src/rd.c)) gives every reader the same output as vgacap's `vgacap_trace_read_runs`: runs of a 43-bit value, with the clock bit of their first sample, in batches of 1,024. The consumer only counts them. No sampler runs and no hash is taken, so SHA-256 does not appear in any read figure.

The first round's patterns:

| Pattern | What is timed |
|---|---|
| open | opening the file |
| scan | every run of the trace: a full decode |
| port0 | the runs of `uo_out` alone, merged where `uo_out` did not change: an export of one pin group |
| seek | 20 windows of 420,000 clocks (one 640x480 frame at 1 clock a pixel), at fixed pseudo-random clocks, each from a fresh open |

Each project's files were read in 5 rounds, the formats in a rotated order within each round, pinned.

A job reads its trace more than once. This round times each read the derive step makes ([`derive.py`](https://github.com/mithro/tinytapeout-vga-videos/blob/main/ttvga/harness/derive.py)), as a pattern of its own (stage `read2`, [`read2_rounds.sh`](../../bench/format-study/read2_rounds.sh)):

| Pattern | The job's read | What it decodes |
|---|---|---|
| info | `tracetool info` (derive.py line 213), with each planar stream counted on its own | every port: the count of changes per port |
| infoscan | `tracetool info` as it is today: one merged full scan | every port, merged |
| sample | `vgacap-sample`'s one pass | `uo_out` and `rst_n` (`rst_n` for calibration, [`vgacap_sample.c`](../../src/tools/vgacap_sample.c) line 282) |
| uohash | `tracetool uohash` | `uo_out`, over the first 2^25 clocks |
| open | opening the file | |
| seek | 10 windows, as above | every port |

- Three rounds, the formats rotated within each round, one job a trace, pinned.
- `vgacap-export` always reads all pins. Its cost is the full scan, which the first round's scan table gives.
- **Formats.** The formats read are:
  - vgch2;
  - vgch2 with block starts read lazily;
  - planar;
  - events to planar;
  - changelog to planar;
  - raw to planar;
  - events;
  - raw `xor6`.
- **Lazy block starts.** [`trace_read_lazy.c`](../../bench/format-study/src/trace_read_lazy.c) is vgacap's reader with one change. Its open reads only block 0's start; a seek's binary search reads the starts it needs, when it needs them. That needs no format change. It changes when faults are found:
  - vgacap's open rejects a file whose block starts are out of order. The lazy reader checks a start only against the neighbours it has read, so the same file now fails at seek or read time, not at open.
  - A seek's binary search can trust a wrong start it has not checked. The next block's check then fails, but only after that block's runs have reached the caller. So a corrupt `BLKH` can deliver runs before the error.
  - The gain depends on the design: vgadonut's open still takes 112 ms (the open table).
- **Validation.** Before any timing, a validate pass hashes every reader's canonical runs (adjacent equal values merged) and its `uo_out` runs. Every format has to match vgacap's reader on the real trace. The `info` pattern's counts also have to match across the formats.
- **The study's readers check less than vgacap's.** [`rd.c`](../../bench/format-study/src/rd.c) maps the file and decodes LEB128 without bounds checks. It checks that a stream's runs sum to the block only under `FS_CHECK_PLANAR`. vgacap's reader enforces every fault case of the spec. That flatters the study formats' scan figures somewhat. It does not touch the structural gain of reading `uo_out` alone.

### Robustness

[`robust.sh`](../../bench/format-study/robust.sh) checks two cases.

1. **Truncation.** Each format's complete file is cut to 10%, to 50%, and to all but its last 64 bytes, then read whole.
2. **A crash.** Each writer is killed with SIGKILL halfway through the trace, by `fsbench encode --kill-after`, without its finish. What it left is then read.

Both report the samples a reader recovers. The checks have gaps:

- They cover 3 projects.
- The kill test runs the study writers synchronously, through `fsbench encode`. It never kills the double-buffered writer, nor the events-to-planar converter. So R6's "at most two blocks" is not tested for the converter.
- No test corrupts a file, so the checksums' detection is not exercised.
- None of these gaps was measured in fix round 2 either. They remain unmeasured.

### Memory

Memory is measured in the loop, not taken from the code. [`sim/run_mem.py`](../../bench/format-study/sim/run_mem.py) runs bench_tick twice for each plan on the three busy designs:

- once with the untraced model alone;
- once with the untraced model and that plan.

Each run is 600 chunks (39 M clocks), so every plan hands over both buffer sets more than once. The figure is the second run's peak RSS (getrusage) minus the first's. [Memory: each plan's peak resident memory over the untraced model's](#memory-each-plans-peak-resident-memory-over-the-untraced-models-mib) is the table.

- vgch2, lean and events take 26-52 MiB a job.
- Planar and evplanar take 38-64 MiB.
- Changelog takes 128-155 MiB, and raw 82-106 MiB. Their converters' scratch is sized for the worst case the study allowed, not for what a block needs.
- At 22 jobs a host, on the design where each takes most, changelog's peak is about 3.4 GB (rle_vga) against vgch2's 1.1 GB (atari2600).

The full-pin FST export's peak RSS is measured too ([A full-pin export to FST](#a-full-pin-export-to-fst-vgch2s-file-against-planars-median-of-the-reps)). It is 315-369 MiB from planar against 321-867 MiB from vgch2.

The readers' memory in a job (`tracetool info`, `vgacap-sample`, `tracetool uohash`) is not measured.

### Interop

[`interop.sh`](../../bench/format-study/interop.sh) runs GTKWave's own converters on the files: `fst2vcd` and `lxt2vcd`, from `/usr/bin`. It also runs `zstd -d`, the reader the spec names for streaming.

### Host

The host is big-storage:

- 88 logical CPUs (2x Xeon Gold 6152, Skylake-SP);
- Debian 13, GCC 14.2;
- Verilator 5.053 (`v5.052-80-g0bad0faca`), the pilot's.

Everything is compiled `-O2 -g` with vgacap's `-Wa,-mbranches-within-32B-boundaries`. The Verilator models use the pilot's flags (`-O3`, `OPT_FAST=-O2`).

Other sessions shared the host throughout, and no stage ran on an idle machine. [Host load during each stage](#host-load-during-each-stage) gives the load as `load.log` recorded it. It ranged from 0.4 to 2.4 a CPU, with as little as 1% idle. The raw loop sets ran at the round's highest load, a median of 1.8 and 2.3 a CPU.

- The first round's stages overlapped:
  - `tick2a` to `tick2d` ran together, 12 processes;
  - the last `writer2` run shared the first socket with three one-CPU loop processes and changelog encodes.
- So did this round's: the loop sets ran on the second socket while encode2, read2 and warm_cache.sh ran on the first.

## Results

The tables below are `analyze.py`'s output, unedited, put in place by `splice_doc.py`. Only their headings are one level deeper here. They follow these rules:

- Each table is about one metric, with a row per project and a column per candidate.
- "-" means that candidate was not measured there. For example, FST-with-clk and LXT2 were not read, and the loop ran on 5 projects.
- The projects come in the brief's order: the five it puts first, then the rest.

How to read each group of tables:

- **Code paths.** Between 0.2% and 3.4% of clocks take the slow path on 7 of the 11 traces. The other four take it on 16% (vgadonut), 77% (nyancat), 90% (atari2600) and 100% (rle_vga). On those four, any format's cost is its change path's cost.
- **Replay.** The replay ran three times, on different cores at different times: `results/writer2-run1`, then `writer2-run2`, then `writer2`, which the tables use.
  - The absolute figures moved by up to 2 times between the runs: nyancat vgch2 37.8 ns, then 20.5 ns.
  - The ratios between the writers stayed within 10%: vgch2 over events was 2.7 both times.
  - So the replay ranks the writers. It does not give their cost in a simulation.
  - FST, VCD and LXT2 ran in the `writer` run only. Their ratios to vgch2 are given within that run.
- **The lean writer.** One table: its file against the published one, by `cmp`, for every trace.
- **The loop.** These are R1's figures, all from sets whose every file reads back right (the read-back table).
  - Fix round 2's background set (fx2a-fx2d) has 4 processes a project: vgch2, events, evplanar, planar and changelog.
  - Its lean set (fx2la-fx2ld) has 4 processes a project: vgch2 and lean.
  - Raw's set (fx1ra-fx1rd, from fix round 1) has raw alone in each process; its files read back right too.
  - The scope set (fx2s), one unpinned process a project in a systemd scope, follows.
  - The first round's sets and fix round 1's main and scope sets are gone from the tables. Their background writers shared one stdio buffer, and their background plans' files fail read-back. Round 1's published figures appear once, in the round 1 against round 2 table.
  - The processes agree on the order but not on the offsets, so the median and the range are both given.
  - vgadonut's loop window is quieter than its whole trace (the window table), so its loop figures understate it.
  - The stall, finish, background and window tables follow the same sets. The memory and FST export tables come after them.
- **Sizes and background CPU.** These cover the whole trace. Sampled measurements are projected (Method). The first round's background figures are single runs; encode2's three runs, the cache test and the loop's own figures follow them.
- **Readers.** These are trace decode only: no sampler and no hash. The first round's patterns come first, then the readers of a job (read2), then R2 and the per-plan verdicts.

<!-- analyze.py tables: begin (bench/format-study/splice_doc.py writes this part) -->

### Inputs: the 11 traces and the code paths they take

#### Trace properties

| project | clocks (M) | clock (MHz) | runs (M) | runs per clock | published trace (MB) |
|---|---:|---:|---:|---:|---:|
| nyancat | 1,512 | 25.175 | 1,165.5 | 0.771 | 416.43 |
| rle_vga | 1,442 | 24.000 | 2,883.2 | 2.000 | 18.39 |
| dvd_screensaver | 1,512 | 25.175 | 6.0 | 0.004 | 1.31 |
| colorbars | 1,512 | 25.175 | 50.7 | 0.034 | 0.56 |
| mandelbrot | 6,007 | 100.000 | 24.7 | 0.004 | 14.72 |
| atari2600 | 1,512 | 25.175 | 1,356.9 | 0.897 | 596.59 |
| vgadonut | 2,884 | 48.000 | 457.0 | 0.158 | 167.33 |
| cfib_demo | 3,004 | 50.000 | 70.9 | 0.024 | 20.59 |
| nyan | 1,502 | 25.000 | 36.5 | 0.024 | 51.78 |
| metaballs | 3,003 | 50.000 | 7.1 | 0.002 | 1.33 |
| tomkeddie_a | 1,512 | 25.175 | 9.8 | 0.006 | 0.11 |

#### Samples that change each port

| project | uo_out (%) | uio_out (%) | uio_oe (%) | ui_in (%) | uio_in (%) | rst_n (%) | ena (%) | two or more ports (%) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 32.92 | 16.43 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 10.82 |
| rle_vga | 0.13 | 100.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.13 |
| dvd_screensaver | 0.20 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| colorbars | 0.53 | 1.25 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.10 |
| mandelbrot | 0.21 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| atari2600 | 11.90 | 42.45 | 1.99 | 0.00 | 3.31 | 0.00 | 0.00 | 13.53 |
| vgadonut | 7.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| cfib_demo | 0.98 | 0.20 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| nyan | 0.45 | 0.78 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.01 |
| metaballs | 0.12 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| tomkeddie_a | 0.32 | 0.20 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.20 |

#### vgch2's code paths, per clock, whole trace

| project | fast run-extend (%) | slow change (%) | block boundary (per million clocks) | blocks |
|---|---:|---:|---:|---:|
| nyancat | 22.939 | 77.061 | 0.323 | 489 |
| rle_vga | 0.000 | 100.000 | 0.838 | 1208 |
| dvd_screensaver | 99.600 | 0.400 | 0.060 | 91 |
| colorbars | 96.650 | 3.350 | 0.060 | 91 |
| mandelbrot | 99.589 | 0.411 | 0.060 | 359 |
| atari2600 | 10.256 | 89.744 | 0.376 | 569 |
| vgadonut | 84.151 | 15.849 | 0.068 | 195 |
| cfib_demo | 97.639 | 2.361 | 0.060 | 180 |
| nyan | 97.572 | 2.428 | 0.060 | 90 |
| metaballs | 99.765 | 0.235 | 0.060 | 180 |
| tomkeddie_a | 99.353 | 0.647 | 0.060 | 91 |

### Writer cost on the simulation thread (replay)

Source: `results/writer2` (vgch2, runs, planar, events, changelog, raw) and `results/writer` (fst, vcd, lxt2, fst_clk).

#### ns per clock over the null writer: estimate [95% interval]

| project | vgch2 | runs | planar | events | changelog | raw | fst | vcd | lxt2 | fst_clk |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 20.45 [19.35, 21.77] | 10.69 [10.16, 11.33] | 12.45 [11.79, 13.16] | 7.52 [7.13, 8.00] | 4.67 [4.40, 5.00] | 1.60 [1.52, 1.69] | 110.53 [105.67, 115.47] | 79.55 [76.52, 82.69] | 994.50 [955.57, 1033.72] | 261.85 [251.61, 272.21] |
| rle_vga | 35.96 [34.69, 37.56] | 21.57 [20.96, 22.44] | 17.55 [16.95, 18.30] | 11.40 [11.07, 11.89] | 5.11 [4.97, 5.27] | 1.43 [1.37, 1.51] | 231.20 [220.71, 242.33] | 179.34 [172.84, 186.20] | 1923.02 [1844.84, 2001.03] | 269.52 [258.07, 282.22] |
| dvd_screensaver | 1.53 [1.48, 1.59] | 0.71 [0.65, 0.77] | 0.69 [0.63, 0.75] | 0.67 [0.61, 0.73] | 0.70 [0.64, 0.76] | 1.85 [1.80, 1.91] | 1.02 [0.86, 1.17] | 1.05 [0.98, 1.12] | 8.06 [7.75, 8.35] | 240.02 [234.33, 245.53] |
| colorbars | 1.96 [1.88, 2.04] | 0.91 [0.86, 0.97] | 1.00 [0.94, 1.05] | 0.82 [0.77, 0.88] | 0.73 [0.67, 0.79] | 1.85 [1.79, 1.90] | 4.30 [4.08, 4.51] | 3.26 [3.11, 3.43] | 51.06 [49.33, 52.81] | 233.98 [227.21, 241.66] |
| mandelbrot | 1.72 [1.68, 1.76] | 0.75 [0.69, 0.80] | 0.79 [0.74, 0.84] | 0.76 [0.70, 0.81] | 0.73 [0.67, 0.79] | 1.95 [1.90, 2.01] | 1.29 [1.12, 1.47] | 1.08 [0.99, 1.17] | 8.16 [7.73, 8.58] | 237.51 [231.32, 243.64] |
| atari2600 | 51.03 [50.36, 51.66] | 27.47 [27.04, 27.90] | 32.13 [31.62, 32.64] | 19.26 [18.71, 19.78] | 10.84 [10.58, 11.11] | 1.99 [1.91, 2.09] | 123.79 [118.17, 129.76] | 88.38 [85.49, 91.39] | 1219.94 [1171.88, 1268.29] | 252.33 [242.16, 263.03] |
| vgadonut | 5.30 [5.03, 5.62] | 2.76 [2.58, 2.93] | 2.80 [2.63, 2.96] | 1.93 [1.81, 2.06] | 1.48 [1.39, 1.59] | 1.75 [1.69, 1.81] | 24.51 [23.23, 25.86] | 17.70 [16.79, 18.57] | 218.36 [208.26, 228.71] | 249.77 [243.56, 255.46] |
| cfib_demo | 2.47 [2.40, 2.54] | 1.27 [1.21, 1.33] | 1.35 [1.29, 1.42] | 1.15 [1.09, 1.22] | 1.03 [0.99, 1.09] | 1.96 [1.91, 2.02] | 3.88 [3.67, 4.09] | 2.58 [2.45, 2.71] | 33.75 [32.65, 34.87] | 248.86 [241.34, 256.55] |
| nyan | 2.09 [1.95, 2.23] | 1.06 [0.97, 1.15] | 1.19 [1.10, 1.29] | 1.01 [0.92, 1.10] | 0.84 [0.77, 0.92] | 1.81 [1.75, 1.87] | 3.76 [3.52, 3.97] | 2.61 [2.46, 2.75] | 38.20 [36.75, 39.52] | 240.30 [232.75, 248.08] |
| metaballs | 2.46 [2.40, 2.53] | 1.21 [1.14, 1.29] | 1.17 [1.09, 1.24] | 1.15 [1.09, 1.21] | 1.15 [1.09, 1.22] | 2.19 [2.10, 2.28] | 0.78 [0.66, 0.91] | 0.82 [0.74, 0.91] | 4.60 [4.41, 4.79] | 242.87 [235.21, 251.29] |
| tomkeddie_a | 2.03 [1.91, 2.16] | 0.95 [0.87, 1.03] | 1.03 [0.94, 1.13] | 0.94 [0.85, 1.02] | 0.96 [0.87, 1.05] | 1.94 [1.84, 2.04] | 1.78 [1.60, 1.95] | 1.31 [1.20, 1.41] | 15.13 [14.66, 15.60] | 258.53 [252.91, 264.04] |

#### Spread of the five rounds' totals, ns per clock over null (min to max)

| project | vgch2 | runs | planar | events | changelog | raw | fst | vcd | lxt2 | fst_clk |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 20.08 to 21.47 | 10.70 to 11.20 | 12.51 to 13.28 | 7.56 to 7.98 | 4.54 to 4.89 | 1.54 to 1.67 | 109.08 to 114.12 | 79.61 to 82.79 | 984.02 to 1011.33 | 260.47 to 268.26 |
| rle_vga | 35.61 to 37.48 | 21.96 to 22.57 | 17.78 to 18.49 | 11.39 to 12.10 | 5.21 to 5.72 | 1.41 to 1.82 | 229.27 to 243.10 | 180.87 to 186.30 | 1926.18 to 1961.15 | 269.12 to 279.83 |
| dvd_screensaver | 1.48 to 1.56 | 0.65 to 0.88 | 0.65 to 0.81 | 0.64 to 0.79 | 0.61 to 0.85 | 1.80 to 1.95 | 1.00 to 1.29 | 1.00 to 1.21 | 8.15 to 8.59 | 238.46 to 242.80 |
| colorbars | 1.88 to 1.99 | 0.88 to 0.94 | 0.93 to 1.06 | 0.79 to 0.90 | 0.67 to 0.77 | 1.84 to 1.87 | 4.17 to 4.70 | 2.98 to 3.56 | 50.81 to 51.59 | 233.07 to 238.27 |
| mandelbrot | 1.63 to 1.71 | 0.71 to 0.78 | 0.73 to 0.81 | 0.64 to 0.76 | 0.68 to 0.77 | 1.90 to 2.02 | 1.12 to 1.61 | 0.84 to 1.25 | 7.95 to 8.48 | 236.23 to 242.00 |
| atari2600 | 50.71 to 51.06 | 26.67 to 27.34 | 30.88 to 31.80 | 18.54 to 19.39 | 10.04 to 11.07 | 1.84 to 2.12 | 121.81 to 129.71 | 88.79 to 93.26 | 1182.96 to 1243.29 | 251.49 to 262.75 |
| vgadonut | 5.31 to 5.47 | 2.64 to 2.81 | 2.75 to 2.93 | 1.87 to 2.02 | 1.47 to 1.52 | 1.74 to 1.81 | 23.39 to 25.31 | 17.40 to 17.94 | 213.16 to 220.63 | 243.81 to 250.32 |
| cfib_demo | 2.43 to 2.49 | 1.22 to 1.28 | 1.29 to 1.35 | 1.08 to 1.17 | 1.01 to 1.10 | 1.93 to 2.01 | 3.49 to 4.08 | 2.42 to 2.84 | 33.44 to 35.17 | 247.51 to 254.25 |
| nyan | 2.00 to 2.11 | 0.96 to 1.08 | 1.12 to 1.19 | 0.91 to 1.05 | 0.73 to 0.90 | 1.70 to 1.99 | 3.52 to 4.18 | 2.55 to 2.78 | 37.89 to 39.42 | 238.99 to 244.88 |
| metaballs | 2.29 to 2.43 | 0.99 to 1.21 | 1.07 to 1.18 | 1.04 to 1.20 | 1.02 to 1.10 | 2.16 to 2.18 | 0.60 to 1.17 | 0.67 to 0.98 | 4.48 to 5.43 | 240.16 to 248.39 |
| tomkeddie_a | 1.83 to 2.13 | 0.88 to 0.97 | 0.96 to 1.05 | 0.86 to 0.94 | 0.85 to 0.94 | 1.83 to 2.13 | 1.72 to 2.02 | 1.11 to 1.44 | 15.01 to 15.67 | 254.61 to 258.31 |

#### As a share of the untraced simulation (pilot wall time), %; the budget is 5%

| project | untraced ns per clock | vgch2 | runs | planar | events | changelog | raw | fst | vcd | lxt2 | fst_clk |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 227.7 | 8.98 | 4.70 | 5.47 | 3.30 | 2.05 | 0.70 | 48.54 | 34.93 | 436.72 | 114.99 |
| rle_vga | 300.5 | 11.97 | 7.18 | 5.84 | 3.79 | 1.70 | 0.48 | 76.94 | 59.68 | 639.94 | 89.69 |
| dvd_screensaver | 226.5 | 0.68 | 0.31 | 0.30 | 0.30 | 0.31 | 0.82 | 0.45 | 0.46 | 3.56 | 105.96 |
| colorbars | 224.5 | 0.87 | 0.41 | 0.44 | 0.37 | 0.33 | 0.82 | 1.91 | 1.45 | 22.74 | 104.20 |
| mandelbrot | 307.9 | 0.56 | 0.24 | 0.26 | 0.25 | 0.24 | 0.63 | 0.42 | 0.35 | 2.65 | 77.13 |
| atari2600 | 433.6 | 11.77 | 6.34 | 7.41 | 4.44 | 2.50 | 0.46 | 28.55 | 20.38 | 281.35 | 58.20 |
| vgadonut | 244.9 | 2.17 | 1.13 | 1.14 | 0.79 | 0.61 | 0.71 | 10.01 | 7.23 | 89.17 | 101.99 |
| cfib_demo | 194.2 | 1.27 | 0.65 | 0.70 | 0.59 | 0.53 | 1.01 | 2.00 | 1.33 | 17.38 | 128.15 |
| nyan | 213.0 | 0.98 | 0.50 | 0.56 | 0.47 | 0.40 | 0.85 | 1.76 | 1.22 | 17.93 | 112.80 |
| metaballs | 210.9 | 1.17 | 0.58 | 0.55 | 0.55 | 0.55 | 1.04 | 0.37 | 0.39 | 2.18 | 115.17 |
| tomkeddie_a | 243.4 | 0.83 | 0.39 | 0.43 | 0.38 | 0.40 | 0.80 | 0.73 | 0.54 | 6.22 | 106.20 |

#### The replay run twice: ns per clock over null, first run (`writer2-run1`) and last (`writer2`)

| project | vgch2, first | vgch2, last | planar, first | planar, last | events, first | events, last | raw, first | raw, last | vgch2 over events, first | vgch2 over events, last |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 37.76 | 20.45 | 22.99 | 12.45 | 14.00 | 7.52 | 2.59 | 1.60 | 2.70 | 2.72 |
| rle_vga | 64.99 | 35.96 | 30.69 | 17.55 | 21.14 | 11.40 | 2.27 | 1.43 | 3.07 | 3.15 |
| dvd_screensaver | 1.82 | 1.53 | 0.82 | 0.69 | 0.88 | 0.67 | 2.57 | 1.85 | 2.07 | 2.28 |
| colorbars | 3.11 | 1.96 | 1.55 | 1.00 | 1.29 | 0.82 | 3.02 | 1.85 | 2.40 | 2.38 |
| mandelbrot | 1.63 | 1.72 | 0.77 | 0.79 | 0.72 | 0.76 | 2.44 | 1.95 | 2.27 | 2.27 |
| atari2600 | 44.44 | 51.03 | 27.99 | 32.13 | 17.03 | 19.26 | 2.89 | 1.99 | 2.61 | 2.65 |
| vgadonut | 9.18 | 5.30 | 4.86 | 2.80 | 3.20 | 1.93 | 3.54 | 1.75 | 2.86 | 2.74 |
| cfib_demo | 1.99 | 2.47 | 0.96 | 1.35 | 0.85 | 1.15 | 3.32 | 1.96 | 2.34 | 2.14 |
| nyan | 2.72 | 2.09 | 1.54 | 1.19 | 1.29 | 1.01 | 3.69 | 1.81 | 2.11 | 2.08 |
| metaballs | 1.84 | 2.46 | 0.80 | 1.17 | 0.76 | 1.15 | 2.60 | 2.19 | 2.41 | 2.14 |
| tomkeddie_a | 1.90 | 2.03 | 0.91 | 1.03 | 0.86 | 0.94 | 2.54 | 1.94 | 2.21 | 2.17 |

#### FST, VCD and LXT2 over vgch2, both from the same replay run (`writer`)

The tables above take vgch2 from `writer2` and FST, VCD and LXT2 from `writer`. These ratios divide within the `writer` run alone, so a run's own drift cancels.

| project | FST over vgch2 | VCD over vgch2 | LXT2 over vgch2 |
|---|---:|---:|---:|
| nyancat | 4.05 | 2.91 | 36.40 |
| rle_vga | 5.10 | 3.95 | 42.39 |
| dvd_screensaver | 0.72 | 0.74 | 5.70 |
| colorbars | 1.91 | 1.45 | 22.66 |
| mandelbrot | 0.86 | 0.72 | 5.42 |
| atari2600 | 4.14 | 2.96 | 40.83 |
| vgadonut | 3.08 | 2.22 | 27.42 |
| cfib_demo | 1.82 | 1.21 | 15.78 |
| nyan | 1.71 | 1.19 | 17.41 |
| metaballs | 0.61 | 0.63 | 3.56 |
| tomkeddie_a | 1.11 | 0.82 | 9.47 |

#### The replayed chunks against the whole trace

| project | chunks | clocks replayed (M) | of the trace (%) | changed clocks, replayed (%) | changed clocks, whole trace (%) | load per CPU during the run |
|---|---:|---:|---:|---:|---:|---:|
| nyancat | 97 | 102 | 6.7 | 76.903 | 77.061 | 0.57 to 0.59 |
| rle_vga | 99 | 104 | 7.2 | 99.998 | 100.000 | 0.57 to 0.59 |
| dvd_screensaver | 97 | 102 | 6.7 | 0.400 | 0.400 | 0.57 to 0.58 |
| colorbars | 97 | 102 | 6.7 | 3.351 | 3.350 | 0.57 to 0.58 |
| mandelbrot | 98 | 103 | 1.7 | 0.414 | 0.411 | 0.57 to 0.58 |
| atari2600 | 97 | 102 | 6.7 | 89.744 | 89.744 | 0.57 to 0.71 |
| vgadonut | 99 | 104 | 3.6 | 15.821 | 15.849 | 0.57 to 0.59 |
| cfib_demo | 99 | 104 | 3.5 | 2.369 | 2.361 | 0.57 to 0.59 |
| nyan | 103 | 108 | 7.2 | 2.428 | 2.428 | 0.57 to 0.58 |
| metaballs | 99 | 104 | 3.5 | 0.234 | 0.235 | 0.57 to 0.59 |
| tomkeddie_a | 97 | 102 | 6.7 | 0.647 | 0.647 | 0.57 to 0.58 |

### The lean writer writes vgacap's file

Each published trace replayed, whole and compressed, through the lean writer (vgacap's trace_write.c with push2's slow path replaced) and through vgacap's own (vgch2), then compared with the published file by `cmp` (test_lean.sh, run_all.sh stage lean). These runs check the bytes; their times are not a measurement (unpinned beside other work).

| project | published bytes | lean bytes | lean against published | vgch2 bytes | vgch2 against published |
|---|---:|---:|---:|---:|---:|
| nyancat | 416,434,073 | 416,434,073 | identical | 416,434,073 | identical |
| rle_vga | 18,388,738 | 18,388,738 | identical | 18,388,738 | identical |
| dvd_screensaver | 1,306,540 | 1,306,540 | identical | 1,306,540 | identical |
| colorbars | 556,030 | 556,030 | identical | 556,030 | identical |
| mandelbrot | 14,719,301 | 14,719,301 | identical | 14,719,301 | identical |
| atari2600 | 596,590,941 | 596,590,941 | identical | 596,590,941 | identical |
| vgadonut | 167,330,036 | 167,330,036 | identical | 167,330,036 | identical |
| cfib_demo | 20,589,332 | 20,589,332 | identical | 20,589,332 | identical |
| nyan | 51,778,941 | 51,778,941 | identical | 51,778,941 | identical |
| metaballs | 1,332,139 | 1,332,139 | identical | 1,332,139 | identical |
| tomkeddie_a | 113,342 | 113,342 | identical | 113,342 | identical |

### Simulation check, fix round 2: the background plans again, every file read back

#### The background plans: R1's figures for them

As fix round 1's main set, after the two fixes (each background writer its own stdio buffer; the converters' streams in port order). Variants: vgch2 (the reference, and the read-back's), events, evplanar, planar and changelog. After each pair of sets every trace written is read back (sim/readback.py) and must match lean's (the readback table). 4 processes (fx2a, fx2b, fx2c, fx2d), each with the variants in another order. ns per clock over the untraced model, paired by chunk.

##### Across the processes: ns per clock, median of the processes' estimates [lowest, highest]

| project | vgch2 | events | evplanar | planar | changelog |
|---|---:|---:|---:|---:|---:|
| nyancat | 36.2 [34.0, 43.2] | 18.5 [15.5, 19.3] | 18.9 [14.9, 21.6] | 28.9 [24.5, 30.6] | 16.8 [14.2, 18.4] |
| rle_vga | 58.3 [54.5, 60.5] | 21.8 [20.0, 24.5] | 23.6 [20.2, 25.5] | 29.6 [26.3, 31.4] | 15.6 [12.7, 17.1] |
| colorbars | 6.6 [4.1, 9.5] | 7.4 [5.4, 8.5] | 6.8 [5.1, 8.6] | 5.8 [3.5, 6.8] | 5.6 [2.9, 7.2] |
| atari2600 | 38.1 [35.5, 39.1] | 18.1 [16.0, 19.4] | 19.6 [16.3, 24.0] | 28.0 [26.8, 29.9] | 14.7 [13.2, 16.5] |
| vgadonut | 2.7 [-0.0, 5.2] | 1.5 [-3.8, 6.4] | 1.2 [-3.5, 5.7] | 1.4 [-1.0, 3.5] | 1.8 [-0.6, 4.2] |

##### The same as % of the untraced model's CPU: median [lowest, highest]; the budget is 5%

| project | vgch2 | events | evplanar | planar | changelog |
|---|---:|---:|---:|---:|---:|
| nyancat | 16.7 [16.1, 18.6] | 8.3 [7.2, 8.8] | 8.8 [7.0, 9.3] | 13.0 [11.5, 14.0] | 7.8 [6.6, 7.9] |
| rle_vga | 19.9 [19.9, 21.7] | 7.5 [7.3, 8.8] | 8.1 [7.4, 9.1] | 10.3 [9.6, 10.8] | 5.4 [4.6, 5.9] |
| colorbars | 3.2 [2.2, 4.5] | 3.6 [2.8, 4.0] | 3.3 [2.7, 4.2] | 2.8 [1.8, 3.2] | 2.7 [1.5, 3.5] |
| atari2600 | 8.1 [7.7, 8.2] | 3.9 [3.5, 4.1] | 4.1 [3.5, 5.2] | 6.0 [5.8, 6.3] | 3.1 [2.9, 3.5] |
| vgadonut | 1.0 [-0.0, 1.9] | 0.6 [-1.3, 2.4] | 0.5 [-1.2, 2.1] | 0.5 [-0.3, 1.3] | 0.7 [-0.2, 1.5] |

##### Each process: estimate [95% bootstrap interval over its chunks], ns per clock

| project | process | untraced ns per clock | vgch2 | events | evplanar | planar | changelog |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | fx2a | 214.2 | 35.6 [33.5, 37.8] | 15.5 [13.2, 17.9] | 14.9 [12.4, 17.4] | 24.5 [22.1, 26.9] | 14.2 [12.5, 16.0] |
| nyancat | fx2b | 218.4 | 36.8 [34.8, 38.9] | 19.3 [16.9, 21.8] | 20.1 [17.3, 22.8] | 30.6 [28.1, 33.3] | 16.9 [14.7, 19.0] |
| nyancat | fx2c | 232.6 | 43.2 [40.8, 46.0] | 19.0 [16.8, 21.1] | 21.6 [18.9, 24.4] | 29.8 [27.6, 32.1] | 18.4 [15.9, 20.8] |
| nyancat | fx2d | 211.4 | 34.0 [32.1, 35.8] | 17.9 [15.9, 20.0] | 17.6 [15.5, 19.8] | 28.0 [26.2, 29.9] | 16.6 [14.5, 18.8] |
| rle_vga | fx2a | 273.9 | 54.5 [52.1, 56.9] | 20.0 [17.5, 22.4] | 20.2 [17.2, 22.8] | 26.3 [23.9, 28.9] | 12.7 [10.6, 14.8] |
| rle_vga | fx2b | 278.9 | 60.5 [57.8, 63.0] | 24.5 [21.9, 27.2] | 25.5 [22.6, 28.4] | 30.2 [27.6, 32.8] | 15.4 [13.2, 17.7] |
| rle_vga | fx2c | 294.4 | 58.8 [55.5, 62.1] | 22.6 [19.6, 25.5] | 23.3 [20.1, 26.5] | 31.4 [28.6, 34.2] | 15.8 [12.5, 19.0] |
| rle_vga | fx2d | 290.8 | 57.8 [55.2, 60.6] | 21.1 [18.0, 24.2] | 23.8 [20.7, 27.0] | 29.0 [26.3, 31.7] | 17.1 [13.8, 20.1] |
| colorbars | fx2a | 212.2 | 9.5 [7.6, 11.5] | 8.5 [6.5, 10.8] | 8.0 [5.8, 10.2] | 6.8 [4.6, 9.0] | 6.2 [4.3, 8.1] |
| colorbars | fx2b | 201.1 | 5.2 [3.5, 6.9] | 6.9 [5.1, 8.6] | 5.7 [3.6, 7.5] | 4.9 [3.0, 6.7] | 4.9 [3.3, 6.6] |
| colorbars | fx2c | 189.1 | 4.1 [2.6, 5.6] | 5.4 [3.9, 6.7] | 5.1 [3.5, 6.8] | 3.5 [2.2, 4.7] | 2.9 [1.2, 4.6] |
| colorbars | fx2d | 208.0 | 8.0 [6.2, 9.9] | 8.0 [5.7, 10.0] | 8.6 [6.4, 10.9] | 6.7 [4.9, 8.4] | 7.2 [5.1, 9.4] |
| atari2600 | fx2a | 475.6 | 39.1 [35.8, 42.2] | 19.1 [15.7, 22.5] | 18.0 [14.6, 21.6] | 29.9 [26.3, 33.5] | 16.5 [13.4, 19.8] |
| atari2600 | fx2b | 476.2 | 39.0 [35.5, 42.5] | 19.4 [16.0, 22.8] | 21.3 [17.5, 24.9] | 27.8 [24.0, 31.7] | 15.0 [11.4, 18.3] |
| atari2600 | fx2c | 461.7 | 37.3 [33.2, 41.2] | 17.1 [13.9, 20.5] | 24.0 [20.1, 27.9] | 28.2 [24.8, 31.6] | 13.2 [9.7, 17.1] |
| atari2600 | fx2d | 463.5 | 35.5 [32.0, 38.7] | 16.0 [12.6, 19.8] | 16.3 [12.2, 19.8] | 26.8 [23.5, 30.0] | 14.4 [10.7, 18.0] |
| vgadonut | fx2a | 268.5 | 5.2 [2.8, 7.4] | 6.4 [3.7, 9.2] | 5.7 [3.0, 8.5] | 3.5 [0.9, 6.0] | 2.7 [0.6, 4.8] |
| vgadonut | fx2b | 263.2 | 4.4 [2.6, 6.3] | 3.0 [0.8, 5.4] | 1.7 [-0.9, 4.0] | 1.2 [-1.2, 3.4] | 0.9 [-0.9, 2.7] |
| vgadonut | fx2c | 289.2 | -0.0 [-2.6, 2.7] | -3.8 [-6.2, -1.5] | -3.5 [-6.0, -0.9] | -1.0 [-3.2, 1.2] | -0.6 [-3.1, 2.1] |
| vgadonut | fx2d | 280.6 | 0.9 [-1.4, 3.4] | -0.0 [-2.9, 2.8] | 0.8 [-2.2, 4.1] | 1.6 [-0.8, 4.0] | 4.2 [1.4, 7.0] |

#### Round 1 against round 2: % of the untraced model, median of the processes

Round 1: the figures fix round 1 published, from its main set (fx1a-fx1d), whose background plans wrote corrupt files (the read-back table); shown only for the comparison. Round 2: fx2a-fx2d, every file read back right. vgch2 ran in both, as the check that the host was alike.

| project | variant | round 1 (%) | round 2 (%) | round 2 lowest (%) | round 2 highest (%) | round 2 minus round 1 (points) |
|---|---:|---:|---:|---:|---:|---:|
| nyancat | vgch2 | 15.4 | 16.7 | 16.1 | 18.6 | 1.3 |
| nyancat | events | 6.3 | 8.3 | 7.2 | 8.8 | 2.1 |
| nyancat | evplanar | 7.3 | 8.8 | 7.0 | 9.3 | 1.5 |
| nyancat | planar | 10.5 | 13.0 | 11.5 | 14.0 | 2.5 |
| nyancat | changelog | 8.0 | 7.8 | 6.6 | 7.9 | -0.2 |
| rle_vga | vgch2 | 20.5 | 19.9 | 19.9 | 21.7 | -0.6 |
| rle_vga | events | 7.9 | 7.5 | 7.3 | 8.8 | -0.4 |
| rle_vga | evplanar | 8.3 | 8.1 | 7.4 | 9.1 | -0.2 |
| rle_vga | planar | 11.5 | 10.3 | 9.6 | 10.8 | -1.2 |
| rle_vga | changelog | 6.1 | 5.4 | 4.6 | 5.9 | -0.6 |
| colorbars | vgch2 | 2.7 | 3.2 | 2.2 | 4.5 | 0.5 |
| colorbars | events | 1.8 | 3.6 | 2.8 | 4.0 | 1.8 |
| colorbars | evplanar | 1.7 | 3.3 | 2.7 | 4.2 | 1.6 |
| colorbars | planar | 2.4 | 2.8 | 1.8 | 3.2 | 0.4 |
| colorbars | changelog | 2.2 | 2.7 | 1.5 | 3.5 | 0.5 |
| atari2600 | vgch2 | 7.9 | 8.1 | 7.7 | 8.2 | 0.2 |
| atari2600 | events | 3.6 | 3.9 | 3.5 | 4.1 | 0.3 |
| atari2600 | evplanar | 3.8 | 4.1 | 3.5 | 5.2 | 0.3 |
| atari2600 | planar | 5.6 | 6.0 | 5.8 | 6.3 | 0.3 |
| atari2600 | changelog | 4.4 | 3.1 | 2.9 | 3.5 | -1.3 |
| vgadonut | vgch2 | 0.4 | 1.0 | -0.0 | 1.9 | 0.6 |
| vgadonut | events | -0.1 | 0.6 | -1.3 | 2.4 | 0.6 |
| vgadonut | evplanar | -0.1 | 0.5 | -1.2 | 2.1 | 0.6 |
| vgadonut | planar | -0.6 | 0.5 | -0.3 | 1.3 | 1.1 |
| vgadonut | changelog | -0.1 | 0.7 | -0.2 | 1.5 | 0.8 |

### Simulation check, fix round 2: vgch2 and lean, every file read back

#### vgch2 and lean: R1's figures for them

As the background plans' set, with the variants none, vgch2 and lean only: vgch2's and lean's R1 from sets whose files all read back (round 1's main set, where they ran beside the corrupt background writers, did not). 4 processes (fx2la, fx2lb, fx2lc, fx2ld), each with the variants in another order. ns per clock over the untraced model, paired by chunk.

##### Across the processes: ns per clock, median of the processes' estimates [lowest, highest]

| project | vgch2 | lean |
|---|---:|---:|
| nyancat | 24.9 [18.5, 28.3] | 26.1 [17.2, 38.2] |
| rle_vga | 44.8 [42.0, 46.8] | 40.8 [36.9, 43.0] |
| colorbars | -0.6 [-6.6, 3.8] | -1.6 [-7.9, 3.3] |
| atari2600 | 31.9 [29.7, 34.7] | 31.9 [29.0, 32.9] |
| vgadonut | 2.0 [1.3, 4.7] | 2.5 [0.9, 3.5] |

##### The same as % of the untraced model's CPU: median [lowest, highest]; the budget is 5%

| project | vgch2 | lean |
|---|---:|---:|
| nyancat | 14.0 [9.0, 14.7] | 14.1 [8.4, 21.3] |
| rle_vga | 19.1 [18.1, 19.7] | 17.5 [15.9, 17.9] |
| colorbars | -0.2 [-3.6, 2.3] | -0.8 [-4.3, 2.0] |
| atari2600 | 7.7 [7.6, 8.4] | 7.7 [7.4, 8.1] |
| vgadonut | 0.9 [0.6, 2.0] | 1.1 [0.4, 1.5] |

##### Each process: estimate [95% bootstrap interval over its chunks], ns per clock

| project | process | untraced ns per clock | vgch2 | lean |
|---|---:|---:|---:|---:|
| nyancat | fx2la | 192.5 | 28.3 [26.9, 29.7] | 27.3 [26.0, 28.5] |
| nyancat | fx2lb | 205.9 | 18.5 [16.7, 20.3] | 17.2 [15.4, 18.9] |
| nyancat | fx2lc | 177.4 | 24.4 [23.2, 25.5] | 24.9 [23.7, 26.0] |
| nyancat | fx2ld | 179.9 | 25.5 [24.1, 27.1] | 38.2 [36.7, 39.6] |
| rle_vga | fx2la | 236.4 | 45.2 [43.6, 47.0] | 41.2 [39.6, 42.9] |
| rle_vga | fx2lb | 244.2 | 46.8 [44.9, 48.8] | 43.0 [40.9, 44.8] |
| rle_vga | fx2lc | 225.7 | 44.5 [42.8, 46.2] | 40.3 [38.7, 41.9] |
| rle_vga | fx2ld | 231.7 | 42.0 [40.5, 43.8] | 36.9 [35.1, 38.5] |
| colorbars | fx2la | 185.7 | -5.0 [-6.4, -3.7] | -6.0 [-7.4, -4.6] |
| colorbars | fx2lb | 182.4 | -6.6 [-8.1, -5.2] | -7.9 [-9.2, -6.6] |
| colorbars | fx2lc | 164.3 | 3.7 [2.5, 4.8] | 2.7 [1.7, 3.8] |
| colorbars | fx2ld | 165.9 | 3.8 [2.6, 5.0] | 3.3 [2.1, 4.4] |
| atari2600 | fx2la | 393.9 | 31.0 [29.2, 32.9] | 31.9 [29.9, 33.9] |
| atari2600 | fx2lb | 411.8 | 34.7 [32.4, 37.1] | 32.9 [30.3, 35.2] |
| atari2600 | fx2lc | 392.4 | 29.7 [28.0, 31.6] | 29.0 [27.0, 31.0] |
| atari2600 | fx2ld | 431.9 | 32.9 [29.9, 35.9] | 32.0 [28.9, 34.8] |
| vgadonut | fx2la | 228.9 | 2.7 [1.3, 4.0] | 3.5 [2.1, 4.9] |
| vgadonut | fx2lb | 231.2 | 4.7 [3.1, 6.3] | 3.1 [1.5, 4.6] |
| vgadonut | fx2lc | 219.8 | 1.3 [-0.0, 2.6] | 1.8 [0.5, 3.1] |
| vgadonut | fx2ld | 226.9 | 1.3 [-0.3, 2.8] | 0.9 [-0.4, 2.2] |

#### The lean writer against vgacap's, in the loop: lean over vgch2, each process's ratio, median [lowest, highest]

Both write the same file (the lean writer's table). Each process's lean estimate over its vgch2 estimate, ns per clock over the untraced model.

| project | lean over vgch2 |
|---|---:|
| nyancat | 0.99 [0.93, 1.50] |
| rle_vga | 0.91 [0.88, 0.92] |
| colorbars | 0.80 [0.75, 0.86] |
| atari2600 | 0.97 [0.95, 1.03] |
| vgadonut | 1.01 [0.65, 1.35] |

Only the busy designs' ratios measure a saving. On the quiet ones a changed clock is rare (colorbars: 3.35% of clocks) and both writers cost a few ns a clock, so the ratio is a per-variant offset of about 1-2 ns a clock, within the noise; vgadonut's per-process estimates include negative ones.

#### Read-back of every trace the loop wrote

`sim/readback.py`: each file read with `fsbench read --patterns validate` (vgch2 and lean with vgacap's reader, the others with the study's), its full hash, samples and uo_out run count against lean's (or vgch2's, the same file) from the same process; raw's sets, which have neither, against fx2a's vgch2. Round 1's main and scope sets (fx1a-fx1d, fx1s) are read back too, as an audit: their background plans' files are the corrupt ones round 2 replaced, and no figure is taken from them. Files that pass, of those written.

| set | variant | files | pass |
|---|---:|---:|---:|
| fx1a | changelog | 5 | 0 |
| fx1a | events | 5 | 0 |
| fx1a | evplanar | 5 | 0 |
| fx1a | lean | 5 | 5 |
| fx1a | planar | 5 | 3 |
| fx1a | vgch2 | 5 | 5 |
| fx1b | changelog | 5 | 0 |
| fx1b | events | 5 | 0 |
| fx1b | evplanar | 5 | 0 |
| fx1b | lean | 5 | 5 |
| fx1b | planar | 5 | 0 |
| fx1b | vgch2 | 5 | 5 |
| fx1c | changelog | 5 | 0 |
| fx1c | events | 5 | 0 |
| fx1c | evplanar | 5 | 0 |
| fx1c | lean | 5 | 5 |
| fx1c | planar | 5 | 2 |
| fx1c | vgch2 | 5 | 5 |
| fx1d | changelog | 5 | 0 |
| fx1d | events | 5 | 0 |
| fx1d | evplanar | 5 | 0 |
| fx1d | lean | 5 | 5 |
| fx1d | planar | 5 | 0 |
| fx1d | vgch2 | 5 | 5 |
| fx1ra | raw | 5 | 5 |
| fx1rb | raw | 5 | 5 |
| fx1rc | raw | 5 | 5 |
| fx1rd | raw | 5 | 5 |
| fx1s | changelog | 5 | 0 |
| fx1s | events | 5 | 0 |
| fx1s | evplanar | 5 | 0 |
| fx1s | lean | 5 | 5 |
| fx1s | planar | 5 | 2 |
| fx1s | vgch2 | 5 | 5 |
| fx2a | changelog | 5 | 5 |
| fx2a | events | 5 | 5 |
| fx2a | evplanar | 5 | 5 |
| fx2a | planar | 5 | 5 |
| fx2a | vgch2 | 5 | 5 |
| fx2b | changelog | 5 | 5 |
| fx2b | events | 5 | 5 |
| fx2b | evplanar | 5 | 5 |
| fx2b | planar | 5 | 5 |
| fx2b | vgch2 | 5 | 5 |
| fx2c | changelog | 5 | 5 |
| fx2c | events | 5 | 5 |
| fx2c | evplanar | 5 | 5 |
| fx2c | planar | 5 | 5 |
| fx2c | vgch2 | 5 | 5 |
| fx2d | changelog | 5 | 5 |
| fx2d | events | 5 | 5 |
| fx2d | evplanar | 5 | 5 |
| fx2d | planar | 5 | 5 |
| fx2d | vgch2 | 5 | 5 |
| fx2la | lean | 5 | 5 |
| fx2la | vgch2 | 5 | 5 |
| fx2lb | lean | 5 | 5 |
| fx2lb | vgch2 | 5 | 5 |
| fx2lc | lean | 5 | 5 |
| fx2lc | vgch2 | 5 | 5 |
| fx2ld | lean | 5 | 5 |
| fx2ld | vgch2 | 5 | 5 |
| fx2s | changelog | 5 | 5 |
| fx2s | events | 5 | 5 |
| fx2s | evplanar | 5 | 5 |
| fx2s | planar | 5 | 5 |
| fx2s | vgch2 | 5 | 5 |

#### Raw, in processes of its own

Raw capture with the untraced model only, so that no other variant shares its process, its L3 traffic or its turn. 4 processes (fx1ra, fx1rb, fx1rc, fx1rd), each with the variants in another order. ns per clock over the untraced model, paired by chunk.

##### Across the processes: ns per clock, median of the processes' estimates [lowest, highest]

| project | raw |
|---|---:|
| nyancat | 15.0 [12.8, 19.9] |
| rle_vga | 9.6 [6.6, 13.2] |
| colorbars | 11.4 [10.8, 13.1] |
| atari2600 | 13.6 [10.4, 19.4] |
| vgadonut | 9.5 [8.9, 11.6] |

##### The same as % of the untraced model's CPU: median [lowest, highest]; the budget is 5%

| project | raw |
|---|---:|
| nyancat | 6.0 [4.5, 6.3] |
| rle_vga | 3.0 [2.2, 3.4] |
| colorbars | 4.8 [4.5, 5.2] |
| atari2600 | 2.6 [1.9, 3.8] |
| vgadonut | 3.2 [2.8, 3.7] |

##### Each process: estimate [95% bootstrap interval over its chunks], ns per clock

| project | process | untraced ns per clock | raw |
|---|---:|---:|---:|
| nyancat | fx1ra | 247.3 | 15.3 [13.4, 17.3] |
| nyancat | fx1rb | 250.4 | 14.6 [13.4, 15.8] |
| nyancat | fx1rc | 281.5 | 12.8 [10.3, 15.4] |
| nyancat | fx1rd | 317.1 | 19.9 [17.4, 22.5] |
| rle_vga | fx1ra | 312.6 | 9.2 [6.5, 12.0] |
| rle_vga | fx1rb | 306.2 | 6.6 [4.1, 9.0] |
| rle_vga | fx1rc | 384.8 | 13.2 [9.5, 17.0] |
| rle_vga | fx1rd | 331.6 | 10.0 [7.4, 12.8] |
| colorbars | fx1ra | 250.4 | 13.1 [10.5, 15.8] |
| colorbars | fx1rb | 218.8 | 11.2 [9.3, 13.2] |
| colorbars | fx1rc | 240.5 | 10.8 [8.7, 12.8] |
| colorbars | fx1rd | 256.7 | 11.5 [9.2, 13.8] |
| atari2600 | fx1ra | 520.2 | 11.1 [7.6, 14.3] |
| atari2600 | fx1rb | 508.6 | 19.4 [16.3, 22.5] |
| atari2600 | fx1rc | 542.7 | 16.2 [12.9, 19.3] |
| atari2600 | fx1rd | 546.8 | 10.4 [7.2, 13.8] |
| vgadonut | fx1ra | 287.8 | 8.9 [6.8, 10.9] |
| vgadonut | fx1rb | 294.4 | 9.6 [7.4, 11.7] |
| vgadonut | fx1rc | 339.6 | 9.4 [6.4, 12.6] |
| vgadonut | fx1rd | 308.4 | 11.6 [9.4, 13.7] |

#### Fix round 2, unpinned, in a systemd scope with CPUQuota=200%

One process a project, fix round 2's variants, not pinned, every file read back. 1 processes (fx2s), each with the variants in another order. ns per clock over the untraced model, paired by chunk.

##### Across the processes: ns per clock, median of the processes' estimates [lowest, highest]

| project | vgch2 | events | evplanar | planar | changelog |
|---|---:|---:|---:|---:|---:|
| nyancat | 31.1 [31.1, 31.1] | 14.5 [14.5, 14.5] | 24.7 [24.7, 24.7] | 24.6 [24.6, 24.6] | 13.7 [13.7, 13.7] |
| rle_vga | 55.5 [55.5, 55.5] | 20.0 [20.0, 20.0] | 20.2 [20.2, 20.2] | 27.7 [27.7, 27.7] | 15.4 [15.4, 15.4] |
| colorbars | 5.6 [5.6, 5.6] | 5.6 [5.6, 5.6] | 6.6 [6.6, 6.6] | 4.2 [4.2, 4.2] | 7.5 [7.5, 7.5] |
| atari2600 | 35.1 [35.1, 35.1] | 15.7 [15.7, 15.7] | 16.0 [16.0, 16.0] | 23.5 [23.5, 23.5] | 16.1 [16.1, 16.1] |
| vgadonut | -0.3 [-0.3, -0.3] | 1.9 [1.9, 1.9] | 1.0 [1.0, 1.0] | 0.4 [0.4, 0.4] | -0.9 [-0.9, -0.9] |

##### The same as % of the untraced model's CPU: median [lowest, highest]; the budget is 5%

| project | vgch2 | events | evplanar | planar | changelog |
|---|---:|---:|---:|---:|---:|
| nyancat | 15.6 [15.6, 15.6] | 7.3 [7.3, 7.3] | 12.4 [12.4, 12.4] | 12.4 [12.4, 12.4] | 6.9 [6.9, 6.9] |
| rle_vga | 20.6 [20.6, 20.6] | 7.4 [7.4, 7.4] | 7.5 [7.5, 7.5] | 10.3 [10.3, 10.3] | 5.7 [5.7, 5.7] |
| colorbars | 3.0 [3.0, 3.0] | 3.0 [3.0, 3.0] | 3.5 [3.5, 3.5] | 2.2 [2.2, 2.2] | 3.9 [3.9, 3.9] |
| atari2600 | 8.7 [8.7, 8.7] | 3.9 [3.9, 3.9] | 4.0 [4.0, 4.0] | 5.8 [5.8, 5.8] | 4.0 [4.0, 4.0] |
| vgadonut | -0.1 [-0.1, -0.1] | 0.8 [0.8, 0.8] | 0.4 [0.4, 0.4] | 0.1 [0.1, 0.1] | -0.4 [-0.4, -0.4] |

##### Each process: estimate [95% bootstrap interval over its chunks], ns per clock

| project | process | untraced ns per clock | vgch2 | events | evplanar | planar | changelog |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | fx2s | 199.0 | 31.1 [29.4, 33.0] | 14.5 [12.6, 16.4] | 24.7 [23.2, 26.2] | 24.6 [22.4, 26.7] | 13.7 [11.8, 15.7] |
| rle_vga | fx2s | 268.8 | 55.5 [53.3, 57.7] | 20.0 [17.6, 22.3] | 20.2 [18.3, 22.2] | 27.7 [25.3, 30.4] | 15.4 [12.6, 18.1] |
| colorbars | fx2s | 189.6 | 5.6 [4.1, 7.3] | 5.6 [3.9, 7.5] | 6.6 [5.1, 8.1] | 4.2 [2.3, 6.0] | 7.5 [5.7, 9.2] |
| atari2600 | fx2s | 403.6 | 35.1 [32.8, 37.4] | 15.7 [13.2, 18.1] | 16.0 [13.8, 18.3] | 23.5 [21.0, 26.1] | 16.1 [13.3, 18.6] |
| vgadonut | fx2s | 244.4 | -0.3 [-1.7, 1.3] | 1.9 [0.0, 3.7] | 1.0 [-0.4, 2.5] | 0.4 [-1.6, 2.5] | -0.9 [-2.9, 1.1] |

#### Stalls: wall time the simulation thread spent not running, over the untraced model's

Per process, the sum over chunks of (wall - thread CPU) for the variant minus the same for the untraced model, ns per clock: median of the processes [lowest, highest]. A stall (the simulation thread waiting for its writer's thread) shows here and nowhere in the CPU figures. The next table counts the waits themselves. This table and the next four take the background plans from fix round 2's set (fx2a-fx2d), vgch2 and lean from fix round 2's lean set (fx2la-fx2ld).

| project | vgch2 | lean | events | evplanar | planar | changelog |
|---|---:|---:|---:|---:|---:|---:|
| nyancat | 0.87 [0.78, 1.45] | 1.13 [0.02, 1.21] | 0.57 [-1.64, 1.94] | -0.25 [-2.32, 2.28] | -0.26 [-1.08, 0.05] | 0.07 [-1.89, 1.56] |
| rle_vga | 1.62 [0.45, 16.34] | 3.84 [0.82, 9.56] | 1.38 [-2.06, 4.46] | 0.65 [-0.96, 4.16] | -0.37 [-1.31, 3.85] | 1.03 [-0.77, 2.03] |
| colorbars | 0.01 [-0.10, 0.15] | -0.04 [-0.05, 0.02] | 0.28 [0.08, 0.63] | 0.22 [-0.29, 0.62] | 0.04 [-0.19, 0.17] | 0.11 [-0.36, 0.46] |
| atari2600 | 1.01 [0.01, 28.87] | 0.27 [0.09, 1.27] | 2.28 [-1.31, 2.73] | 1.01 [-0.72, 2.49] | -0.11 [-1.50, 3.00] | 1.20 [0.29, 2.80] |
| vgadonut | 0.05 [0.02, 0.11] | 0.06 [-0.08, 0.16] | -0.00 [-0.26, 0.27] | 0.09 [-0.28, 0.58] | -0.01 [-0.26, 0.31] | 0.05 [-0.16, 0.24] |

#### Hand-overs that waited: the study writers' own count, summed over the processes

A hand-over waits when the writer's thread still holds the other buffer. vgacap's writer (vgch2, lean) keeps no such count; the table above covers it. The wait's wall time, summed, is in the last column.

| project | events | evplanar | planar | changelog | wall s waited, all |
|---|---:|---:|---:|---:|---:|
| nyancat | 0 | 0 | 0 | 0 | 0.000 |
| rle_vga | 0 | 0 | 0 | 0 | 0.000 |
| colorbars | 0 | 0 | 0 | 0 | 0.000 |
| atari2600 | 0 | 0 | 0 | 0 | 0.000 |
| vgadonut | 0 | 0 | 0 | 0 | 0.000 |

#### Each writer's finish (last block, its thread joined), s wall: median of the processes

| project | vgch2 | lean | events | evplanar | planar | changelog |
|---|---:|---:|---:|---:|---:|---:|
| nyancat | 0.046 | 0.053 | 0.007 | 0.021 | 0.035 | 0.058 |
| rle_vga | 0.010 | 0.009 | 0.004 | 0.009 | 0.009 | 0.057 |
| colorbars | 0.003 | 0.003 | 0.001 | 0.009 | 0.001 | 0.007 |
| atari2600 | 0.031 | 0.027 | 0.028 | 0.068 | 0.017 | 0.028 |
| vgadonut | 0.003 | 0.003 | 0.001 | 0.002 | 0.001 | 0.003 |

#### Background threads in the loop: CPU s over the 262 M clocks, median of the processes

| project | events | evplanar | planar | changelog |
|---|---:|---:|---:|---:|
| nyancat | 1.02 | 2.65 | 0.62 | 3.05 |
| rle_vga | 0.59 | 5.13 | 0.41 | 4.94 |
| colorbars | 0.01 | 0.11 | 0.02 | 0.14 |
| atari2600 | 1.19 | 3.02 | 0.31 | 3.38 |
| vgadonut | 0.02 | 0.06 | 0.02 | 0.07 |

#### The loop's window against the whole trace: events bytes per clock (zstd 1)

The loop runs the first 262 M clocks from reset. Its events stream's compressed bytes per clock, against the whole trace's, show whether that window changes pins as the whole trace does.

| project | loop window (B a clock) | whole trace (B a clock) | ratio |
|---|---:|---:|---:|
| nyancat | 0.102 | 0.102 | 1.00 |
| rle_vga | 0.000741 | 0.000746 | 0.99 |
| colorbars | 0.000137 | 0.000134 | 1.03 |
| atari2600 | 0.155 | 0.151 | 1.02 |
| vgadonut | 0.00207 | 0.0153 | 0.14 |

#### Memory: each plan's peak resident memory over the untraced model's, MiB

`sim/run_mem.py`: bench_tick with the untraced model alone, and with the untraced model and one plan, 600 chunks (39 M clocks: every plan hands over both buffer sets more than once); peak RSS (getrusage) of the second run minus the first's. One process each, pinned to one CPU. A job holds one plan; at 22 jobs a host multiply by 22.

| project | untraced model alone | vgch2 | lean | events | evplanar | planar | changelog | raw |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 11 | 26 | 26 | 26 | 38 | 38 | 128 | 82 |
| rle_vga | 19 | 49 | 49 | 49 | 60 | 49 | 155 | 105 |
| atari2600 | 19 | 52 | 51 | 51 | 62 | 64 | 152 | 106 |

#### A full-pin export to FST: vgch2's file against planar's, median of the reps

`run_export.py`: the seven ports to FST (LZ4, libfst's parallel mode), as vgacap-export --no-clk writes them, by the study's export (`fsbench read --patterns export`) from vgch2's file with vgacap's reader and from planar's with the study's; and vgacap-export itself on vgch2's file, which also writes the decoded signals: a calibration, not a pair. Two CPUs a job, the reps interleaved. Process s counts libfst's compressor thread. Read back: the first rep's FST hashes as its source does.

| project | source | reps | process CPU s | wall s | reading thread s | max RSS MiB | FST MB | read back |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | vgch2 | 3 | 447.9 | 287.1 | 202.1 | 704 | 211.7 | same |
| nyancat | planar | 3 | 448.5 | 282.7 | 211.0 | 369 | 211.7 | same |
| nyancat | vgacap-export | 3 | 541.0 | 355.2 | - | 686 | 320.6 | - |
| rle_vga | vgch2 | 3 | 516.2 | 430.0 | 374.9 | 321 | 39.5 | same |
| rle_vga | planar | 3 | 579.0 | 500.1 | 443.6 | 304 | 39.5 | same |
| rle_vga | vgacap-export | 3 | 468.9 | 407.1 | - | 321 | 39.5 | - |
| atari2600 | vgch2 | 3 | 757.5 | 626.3 | 220.6 | 867 | 317.5 | same |
| atari2600 | planar | 3 | 766.4 | 611.4 | 241.4 | 315 | 317.5 | same |
| atari2600 | vgacap-export | 3 | 791.3 | 641.8 | - | 864 | 475.5 | - |

### Simulation check, side by side: whole testbench runs (too noisy, not used)

#### Untraced runs

| project | rounds | untraced sim thread ns per clock (median) | study binary over prod binary, untraced (%) |
|---|---:|---:|---:|
| nyancat | 5 | 231.0 | 2.75 [-21.26, 12.22] |
| rle_vga | 5 | 401.2 | -0.87 [-40.16, 43.60] |
| colorbars | 5 | 245.0 | 0.81 [-20.70, 20.55] |

#### Simulation-thread CPU over the same binary's untraced run, same round, %: median [min, max]

| project | prod-vgch2 over prod-none | study-vgch2 over study-none | study-events over study-none | study-raw over study-none |
|---|---:|---:|---:|---:|
| nyancat | 18.61 [-11.60, 24.98] | 20.69 [1.05, 26.69] | 12.32 [7.93, 32.42] | 11.05 [7.83, 55.30] |
| rle_vga | 16.68 [-29.31, 33.12] | 21.17 [-5.81, 52.87] | 11.05 [-22.13, 37.60] | 7.15 [-27.85, 27.93] |
| colorbars | 7.81 [-15.05, 18.86] | 16.31 [-32.67, 33.29] | 16.72 [-43.67, 19.95] | 7.56 [-28.82, 22.08] |

#### The same in ns per clock: median [min, max]

| project | prod-vgch2 over prod-none | study-vgch2 over study-none | study-events over study-none | study-raw over study-none |
|---|---:|---:|---:|---:|
| nyancat | 42.97 [-34.47, 53.85] | 44.40 [3.26, 60.06] | 28.41 [18.55, 72.95] | 26.34 [17.61, 124.24] |
| rle_vga | 58.13 [-133.86, 132.86] | 100.73 [-26.81, 144.50] | 34.17 [-102.16, 127.35] | 19.54 [-128.58, 116.91] |
| colorbars | 15.69 [-36.88, 46.45] | 33.01 [-133.34, 64.70] | 38.32 [-178.24, 51.87] | 14.69 [-117.63, 50.60] |

#### Background threads' CPU (process CPU minus the simulation thread), s a run, median

| project | prod-vgch2 over prod-none | study-vgch2 over study-none | study-events over study-none | study-raw over study-none |
|---|---:|---:|---:|---:|
| nyancat | 2.78 | 2.77 | 0.75 | 6.95 |
| rle_vga | 1.36 | 1.27 | 0.57 | 4.75 |
| colorbars | 0.03 | 0.03 | 0.01 | 4.53 |

#### Replay against simulation, ns per clock over untraced

| project | vgch2 replay | vgch2 sim | events replay | events sim | raw replay | raw sim |
|---|---:|---:|---:|---:|---:|---:|
| nyancat | 20.45 | 44.40 | 7.52 | 28.41 | 1.60 | 26.34 |
| rle_vga | 35.96 | 100.73 | 11.40 | 34.17 | 1.43 | 19.54 |
| colorbars | 1.96 | 33.01 | 0.82 | 38.32 | 1.85 | 14.69 |

Load per CPU during the check: 0.53 to 1.54.

### File size and background CPU

#### Total bytes, MB (projected to the whole trace where sampled)

| project | vgch2 (zstd 1) | runs (zstd 1) | planar (zstd 1) | events (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw xor6 (zstd 1) | raw to planar (zstd 1) | sigrok-like (deflate 6) | VCD (zstd 1) | FST (LZ4) | FST (zlib) | FST with clk (LZ4) | LXT2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 416.43 | 416.35 | 53.76 | 154.13 | 53.78 | 53.92 | 416.93 | 54.05 | 249.48 | 3,330.34 | 211.69 | 160.84 | 121.50 | 6,103.70 |
| rle_vga | 18.39 | 19.09 | 0.62 | 1.08 | 0.66 | 0.67 | 13.15 | 0.71 | 43.89 | 824.85 | 39.50 | 22.49 | 50.88 | 13,283.69 |
| dvd_screensaver | 1.31 | 1.30 | 1.36 | 1.13 | 1.36 | 1.36 | 21.84 | 1.95 | 51.74 | 26.81 | 2.87 | 3.11 | 55.66 | 37.73 |
| colorbars | 0.56 | 0.55 | 0.67 | 0.20 | 0.67 | 0.67 | 5.50 | 1.13 | 162.61 | 221.17 | 2.05 | 3.69 | 54.67 | 304.67 |
| mandelbrot | 14.72 | 14.70 | 8.13 | 9.07 | 8.13 | 8.13 | 90.86 | 14.89 | 230.39 | 106.79 | 10.44 | 16.10 | 94.61 | 128.29 |
| atari2600 | 596.59 | 596.61 | 9.02 | 228.58 | 9.38 | 9.83 | 517.57 | 10.33 | 590.82 | 5,451.29 | 317.52 | 214.97 | 101.09 | 7,341.87 |
| vgadonut | 167.33 | 167.28 | 35.13 | 44.18 | 35.13 | 35.13 | 250.00 | 35.57 | 205.82 | 853.19 | 78.69 | 55.01 | 91.26 | 2,269.49 |
| cfib_demo | 20.59 | 20.58 | 2.45 | 9.39 | 2.45 | 2.45 | 115.68 | 3.23 | 192.13 | 178.81 | 22.14 | 14.11 | 40.60 | 324.15 |
| nyan | 51.78 | 51.77 | 0.51 | 36.26 | 0.51 | 0.51 | 75.17 | 1.51 | 167.79 | 171.28 | 47.58 | 39.46 | 26.84 | 191.75 |
| metaballs | 1.33 | 1.32 | 0.72 | 0.96 | 0.72 | 0.72 | 33.27 | 1.23 | 90.56 | 19.54 | 1.03 | 1.00 | 36.62 | 36.35 |
| tomkeddie_a | 0.11 | 0.11 | 0.16 | 0.09 | 0.16 | 0.16 | 30.74 | 0.60 | 60.14 | 49.86 | 0.67 | 1.14 | 53.09 | 61.76 |

#### Bytes per clock

| project | vgch2 (zstd 1) | runs (zstd 1) | planar (zstd 1) | events (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw xor6 (zstd 1) | raw to planar (zstd 1) | sigrok-like (deflate 6) | VCD (zstd 1) | FST (LZ4) | FST (zlib) | FST with clk (LZ4) | LXT2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 0.2753 | 0.2753 | 0.0355 | 0.1019 | 0.0356 | 0.0357 | 0.2757 | 0.0357 | 0.1650 | 2.2020 | 0.1400 | 0.1063 | 0.0803 | 4.0358 |
| rle_vga | 0.0128 | 0.0132 | 0.0004 | 0.0007 | 0.0005 | 0.0005 | 0.0091 | 0.0005 | 0.0304 | 0.5722 | 0.0274 | 0.0156 | 0.0353 | 9.2145 |
| dvd_screensaver | 0.0009 | 0.0009 | 0.0009 | 0.0007 | 0.0009 | 0.0009 | 0.0144 | 0.0013 | 0.0342 | 0.0177 | 0.0019 | 0.0021 | 0.0368 | 0.0249 |
| colorbars | 0.0004 | 0.0004 | 0.0004 | 0.0001 | 0.0004 | 0.0004 | 0.0036 | 0.0007 | 0.1075 | 0.1462 | 0.0014 | 0.0024 | 0.0361 | 0.2014 |
| mandelbrot | 0.0025 | 0.0024 | 0.0014 | 0.0015 | 0.0014 | 0.0014 | 0.0151 | 0.0025 | 0.0384 | 0.0178 | 0.0017 | 0.0027 | 0.0157 | 0.0214 |
| atari2600 | 0.3946 | 0.3946 | 0.0060 | 0.1512 | 0.0062 | 0.0065 | 0.3423 | 0.0068 | 0.3908 | 3.6053 | 0.2100 | 0.1422 | 0.0669 | 4.8557 |
| vgadonut | 0.0580 | 0.0580 | 0.0122 | 0.0153 | 0.0122 | 0.0122 | 0.0867 | 0.0123 | 0.0714 | 0.2959 | 0.0273 | 0.0191 | 0.0316 | 0.7870 |
| cfib_demo | 0.0069 | 0.0069 | 0.0008 | 0.0031 | 0.0008 | 0.0008 | 0.0385 | 0.0011 | 0.0640 | 0.0595 | 0.0074 | 0.0047 | 0.0135 | 0.1079 |
| nyan | 0.0345 | 0.0345 | 0.0003 | 0.0241 | 0.0003 | 0.0003 | 0.0500 | 0.0010 | 0.1117 | 0.1140 | 0.0317 | 0.0263 | 0.0179 | 0.1277 |
| metaballs | 0.0004 | 0.0004 | 0.0002 | 0.0003 | 0.0002 | 0.0002 | 0.0111 | 0.0004 | 0.0302 | 0.0065 | 0.0003 | 0.0003 | 0.0122 | 0.0121 |
| tomkeddie_a | 0.0001 | 0.0001 | 0.0001 | 0.0001 | 0.0001 | 0.0001 | 0.0203 | 0.0004 | 0.0398 | 0.0330 | 0.0004 | 0.0008 | 0.0351 | 0.0408 |

#### Background CPU, s for the whole trace

| project | untraced CPU (s) | vgch2 (zstd 1) | runs (zstd 1) | planar (zstd 1) | events (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw xor6 (zstd 1) | raw to planar (zstd 1) | sigrok-like (deflate 6) | VCD (zstd 1) | FST (LZ4) | FST (zlib) | FST with clk (LZ4) | LXT2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 406 | 19.62 | 20.72 | 3.83 | 7.55 | 15.32 | 18.26 | 58.69 | 34.71 | 224.83 | 111.53 | 243.16 | 289.41 | 210.69 | - |
| rle_vga | 453 | 6.83 | 8.25 | 2.39 | 4.43 | 24.14 | 25.23 | 36.21 | 47.01 | 166.46 | 158.92 | 128.86 | 215.15 | 170.31 | - |
| dvd_screensaver | 357 | 0.05 | 0.09 | 0.05 | 0.04 | 0.10 | 0.17 | 40.63 | 14.34 | 162.85 | 0.58 | 1.53 | 1.95 | 151.06 | - |
| colorbars | 356 | 0.13 | 0.23 | 0.08 | 0.09 | 0.89 | 0.72 | 41.59 | 13.09 | 142.80 | 8.41 | 8.16 | 9.55 | 182.84 | - |
| mandelbrot | 1,887 | 0.47 | 0.57 | 0.23 | 0.23 | 0.60 | 0.66 | 120.77 | 44.56 | 649.24 | 4.11 | 11.87 | 24.12 | 384.17 | - |
| atari2600 | 733 | 21.14 | 23.03 | 1.82 | 7.90 | 17.89 | 21.01 | 61.04 | 30.71 | 352.30 | 164.57 | 532.51 | 656.36 | 238.83 | - |
| vgadonut | 760 | 7.01 | 9.76 | 1.28 | 2.04 | 6.45 | 7.26 | 78.31 | 26.62 | 408.28 | 29.33 | 71.91 | 89.21 | 191.92 | - |
| cfib_demo | 624 | 0.63 | 1.06 | 0.14 | 0.38 | 0.85 | 1.13 | 75.16 | 23.04 | 428.08 | 6.35 | 9.58 | 11.05 | 207.48 | - |
| nyan | 337 | 1.24 | 2.21 | 0.06 | 0.81 | 0.70 | 0.60 | 43.35 | 18.97 | 244.48 | 7.35 | 20.82 | 18.99 | 128.80 | - |
| metaballs | 672 | 0.06 | 0.08 | 0.03 | 0.07 | 0.17 | 0.19 | 73.41 | 32.50 | 356.30 | 0.51 | 0.65 | 0.81 | 161.90 | - |
| tomkeddie_a | 385 | 0.03 | 0.06 | 0.03 | 0.02 | 0.14 | 0.24 | 42.57 | 12.71 | 195.19 | 1.10 | 1.97 | 2.06 | 119.74 | - |

#### Background CPU, as % of the untraced simulation's CPU

| project | untraced CPU (s) | vgch2 (zstd 1) | runs (zstd 1) | planar (zstd 1) | events (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw xor6 (zstd 1) | raw to planar (zstd 1) | sigrok-like (deflate 6) | VCD (zstd 1) | FST (LZ4) | FST (zlib) | FST with clk (LZ4) | LXT2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 406 | 4.84 | 5.11 | 0.95 | 1.86 | 3.78 | 4.50 | 14.47 | 8.56 | 55.42 | 27.49 | 59.94 | 71.34 | 51.93 | - |
| rle_vga | 453 | 1.51 | 1.82 | 0.53 | 0.98 | 5.33 | 5.57 | 7.99 | 10.38 | 36.75 | 35.08 | 28.45 | 47.49 | 37.60 | - |
| dvd_screensaver | 357 | 0.02 | 0.03 | 0.01 | 0.01 | 0.03 | 0.05 | 11.38 | 4.02 | 45.63 | 0.16 | 0.43 | 0.55 | 42.32 | - |
| colorbars | 356 | 0.04 | 0.06 | 0.02 | 0.02 | 0.25 | 0.20 | 11.68 | 3.68 | 40.10 | 2.36 | 2.29 | 2.68 | 51.34 | - |
| mandelbrot | 1,887 | 0.02 | 0.03 | 0.01 | 0.01 | 0.03 | 0.03 | 6.40 | 2.36 | 34.41 | 0.22 | 0.63 | 1.28 | 20.36 | - |
| atari2600 | 733 | 2.88 | 3.14 | 0.25 | 1.08 | 2.44 | 2.87 | 8.32 | 4.19 | 48.05 | 22.45 | 72.63 | 89.52 | 32.57 | - |
| vgadonut | 760 | 0.92 | 1.28 | 0.17 | 0.27 | 0.85 | 0.96 | 10.31 | 3.50 | 53.73 | 3.86 | 9.46 | 11.74 | 25.26 | - |
| cfib_demo | 624 | 0.10 | 0.17 | 0.02 | 0.06 | 0.14 | 0.18 | 12.05 | 3.70 | 68.66 | 1.02 | 1.54 | 1.77 | 33.28 | - |
| nyan | 337 | 0.37 | 0.66 | 0.02 | 0.24 | 0.21 | 0.18 | 12.88 | 5.63 | 72.61 | 2.18 | 6.18 | 5.64 | 38.25 | - |
| metaballs | 672 | 0.01 | 0.01 | 0.00 | 0.01 | 0.02 | 0.03 | 10.92 | 4.83 | 53.00 | 0.08 | 0.10 | 0.12 | 24.08 | - |
| tomkeddie_a | 385 | 0.01 | 0.02 | 0.01 | 0.00 | 0.04 | 0.06 | 11.06 | 3.30 | 50.69 | 0.29 | 0.51 | 0.53 | 31.09 | - |

#### Background CPU, measured again: three runs, s for the whole trace, median [lowest, highest]

`results/encode2`: each candidate's configuration alone (zstd 1, every block, no compressor matrix beside it), three times, the runs apart in time. The R2 tables use these medians.

| project | vgch2 (zstd 1) | planar (zstd 1) | events (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) | raw xor6 (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 22.07 [19.55, 22.75] | 4.07 [4.06, 4.18] | 6.27 [5.97, 6.53] | 15.99 [14.54, 16.05] | 22.52 [20.92, 23.13] | 36.62 [34.57, 38.60] | 53.35 [52.81, 54.54] |
| rle_vga | 16.09 [14.52, 17.18] | 4.41 [4.38, 4.64] | 6.43 [6.34, 6.62] | 30.06 [28.06, 31.97] | 29.56 [27.50, 31.68] | 55.92 [48.37, 58.46] | 34.19 [32.89, 36.03] |
| dvd_screensaver | 0.08 [0.07, 0.08] | 0.05 [0.05, 0.08] | 0.07 [0.05, 0.10] | 0.12 [0.10, 0.14] | 0.17 [0.14, 0.18] | 18.50 [18.21, 23.24] | 37.07 [36.80, 38.09] |
| colorbars | 0.34 [0.31, 0.35] | 0.11 [0.10, 0.13] | 0.13 [0.12, 0.13] | 0.55 [0.54, 0.56] | 0.78 [0.71, 0.78] | 19.49 [16.01, 20.92] | 33.63 [26.81, 42.88] |
| mandelbrot | 0.50 [0.44, 0.63] | 0.27 [0.22, 0.30] | 0.34 [0.27, 0.37] | 0.59 [0.49, 0.76] | 0.61 [0.60, 1.01] | 72.61 [69.77, 76.79] | 146.60 [134.87, 148.04] |
| atari2600 | 23.55 [18.96, 25.44] | 2.86 [1.87, 3.16] | 7.74 [5.90, 9.51] | 16.74 [14.22, 17.72] | 23.07 [19.27, 23.48] | 37.19 [26.01, 37.73] | 53.37 [34.74, 55.08] |
| vgadonut | 7.83 [7.68, 8.32] | 1.46 [1.44, 1.58] | 1.97 [1.95, 2.47] | 5.51 [5.44, 5.85] | 7.76 [6.99, 7.99] | 41.94 [38.52, 45.54] | 77.43 [64.50, 80.09] |
| cfib_demo | 0.89 [0.86, 0.94] | 0.19 [0.16, 0.23] | 0.37 [0.37, 0.43] | 0.98 [0.79, 1.05] | 1.20 [1.15, 1.23] | 36.02 [31.05, 39.36] | 76.44 [60.99, 78.00] |
| nyan | 1.29 [1.24, 1.31] | 0.08 [0.07, 0.09] | 0.61 [0.56, 0.77] | 0.47 [0.43, 0.48] | 0.62 [0.57, 0.73] | 17.37 [16.49, 18.12] | 35.56 [35.36, 37.74] |
| metaballs | 0.09 [0.08, 0.09] | 0.04 [0.04, 0.06] | 0.06 [0.06, 0.07] | 0.13 [0.10, 0.14] | 0.18 [0.12, 0.23] | 35.29 [33.04, 37.28] | 68.22 [67.45, 69.21] |
| tomkeddie_a | 0.05 [0.05, 0.07] | 0.05 [0.04, 0.05] | 0.03 [0.03, 0.05] | 0.16 [0.16, 0.20] | 0.26 [0.24, 0.26] | 19.51 [19.38, 19.63] | 41.49 [39.66, 41.90] |

#### zstd1's CPU with the block cold and warm in cache, s for the whole trace

`warm_cache.sh`: zstd1 alone on each block (cold, as encode2), and zstd1 after zstd-5 and zstd-1 have read the same block (warm, as the first round's matrix ran it). Run at once, each on its own CPU.

| project | writer | cold | warm | cold over warm |
|---|---:|---:|---:|---:|
| nyancat | planar | 2.86 | 2.61 | 1.10 |
| nyancat | vgch2 | 16.16 | 16.21 | 1.00 |
| rle_vga | planar | 2.21 | 2.41 | 0.92 |
| rle_vga | vgch2 | 5.80 | 5.71 | 1.02 |

#### Background CPU three ways, s for the whole trace: the loop's, scaled, against the whole-trace runs

The loop's figure is its background thread's CPU over its 262 M clocks, beside a running simulation, scaled to the trace's clocks (median of the processes); the window is representative (the loop's window table). The first round's and encode2's are the whole trace, in fsbench.

| project | candidate | loop, scaled | first round | encode2 lowest | encode2 median | encode2 highest |
|---|---:|---:|---:|---:|---:|---:|
| nyancat | events | 5.86 | 7.55 | 5.97 | 6.27 | 6.53 |
| nyancat | evplanar | 15.26 | 15.32 | 14.54 | 15.99 | 16.05 |
| nyancat | planar | 3.59 | 3.83 | 4.06 | 4.07 | 4.18 |
| nyancat | changelog | 17.63 | 18.26 | 20.92 | 22.52 | 23.13 |
| rle_vga | events | 3.23 | 4.43 | 6.34 | 6.43 | 6.62 |
| rle_vga | evplanar | 28.20 | 24.14 | 28.06 | 30.06 | 31.97 |
| rle_vga | planar | 2.25 | 2.39 | 4.38 | 4.41 | 4.64 |
| rle_vga | changelog | 27.16 | 25.23 | 27.50 | 29.56 | 31.68 |
| colorbars | events | 0.08 | 0.09 | 0.12 | 0.13 | 0.13 |
| colorbars | evplanar | 0.65 | 0.89 | 0.54 | 0.55 | 0.56 |
| colorbars | planar | 0.09 | 0.08 | 0.10 | 0.11 | 0.13 |
| colorbars | changelog | 0.80 | 0.72 | 0.71 | 0.78 | 0.78 |
| atari2600 | events | 6.87 | 7.90 | 5.90 | 7.74 | 9.51 |
| atari2600 | evplanar | 17.40 | 17.89 | 14.22 | 16.74 | 17.72 |
| atari2600 | planar | 1.81 | 1.82 | 1.87 | 2.86 | 3.16 |
| atari2600 | changelog | 19.50 | 21.01 | 19.27 | 23.07 | 23.48 |
| vgadonut | events | 0.25 | 2.04 | 1.95 | 1.97 | 2.47 |
| vgadonut | evplanar | 0.68 | 6.45 | 5.44 | 5.51 | 5.85 |
| vgadonut | planar | 0.21 | 1.28 | 1.44 | 1.46 | 1.58 |
| vgadonut | changelog | 0.73 | 7.26 | 6.99 | 7.76 | 7.99 |

#### Re-encoding a published trace to planar: encode2's planar pass, wall s, median of 3

The pass reads the published trace with vgacap's reader, expands it to the testbench's raw images and writes planar: a migration's re-encode, one CPU a trace.

| project | wall s |
|---|---:|
| nyancat | 62 |
| rle_vga | 86 |
| dvd_screensaver | 18 |
| colorbars | 20 |
| mandelbrot | 73 |
| atari2600 | 65 |
| vgadonut | 60 |
| cfib_demo | 39 |
| nyan | 20 |
| metaballs | 32 |
| tomkeddie_a | 20 |

#### The first round's single measurement of the same, s (the compressor matrix in the same pass)

| project | vgch2 (zstd 1) | planar (zstd 1) | events (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) | raw xor6 (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 19.62 | 3.83 | 7.55 | 15.32 | 18.26 | 34.71 | 58.69 |
| rle_vga | 6.83 | 2.39 | 4.43 | 24.14 | 25.23 | 47.01 | 36.21 |
| dvd_screensaver | 0.05 | 0.05 | 0.04 | 0.10 | 0.17 | 14.34 | 40.63 |
| colorbars | 0.13 | 0.08 | 0.09 | 0.89 | 0.72 | 13.09 | 41.59 |
| mandelbrot | 0.47 | 0.23 | 0.23 | 0.60 | 0.66 | 44.56 | 120.77 |
| atari2600 | 21.14 | 1.82 | 7.90 | 17.89 | 21.01 | 30.71 | 61.04 |
| vgadonut | 7.01 | 1.28 | 2.04 | 6.45 | 7.26 | 26.62 | 78.31 |
| cfib_demo | 0.63 | 0.14 | 0.38 | 0.85 | 1.13 | 23.04 | 75.16 |
| nyan | 1.24 | 0.06 | 0.81 | 0.70 | 0.60 | 18.97 | 43.35 |
| metaballs | 0.06 | 0.03 | 0.07 | 0.17 | 0.19 | 32.50 | 73.41 |
| tomkeddie_a | 0.03 | 0.03 | 0.02 | 0.14 | 0.24 | 12.71 | 42.57 |

#### Compressors on vgch2's blocks: MB

| project | zstd-5 | zstd-1 | zstd1 | zstd1-nocheck | zstd3 | zstd9 | zstd1-long27 | lz4 | lz4hc9 | deflate1 | deflate6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 656.91 | 592.35 | 413.13 | 413.13 | 288.33 | 121.75 | 286.12 | 390.25 | 153.44 | 303.12 | 157.27 |
| rle_vga | 16.52 | 69.76 | 18.37 | 18.37 | 26.82 | 4.77 | 4.84 | 102.95 | 79.35 | 135.12 | 46.19 |
| dvd_screensaver | 4.95 | 1.51 | 1.31 | 1.31 | 1.31 | 1.47 | 1.49 | 4.73 | 2.77 | 3.54 | 2.34 |
| colorbars | 0.62 | 0.83 | 0.55 | 0.55 | 0.64 | 0.22 | 0.23 | 19.01 | 10.18 | 19.66 | 11.39 |
| mandelbrot | 17.99 | 17.28 | 14.71 | 14.71 | 12.05 | 7.92 | 12.42 | 22.34 | 10.08 | 24.19 | 15.35 |
| atari2600 | 776.57 | 832.67 | 594.67 | 594.67 | 417.73 | 146.94 | 92.38 | 945.37 | 348.52 | 1006.85 | 366.10 |
| vgadonut | 222.51 | 271.62 | 167.74 | 167.74 | 81.01 | 34.31 | 78.15 | 96.86 | 40.42 | 90.38 | 45.03 |
| cfib_demo | 25.60 | 23.94 | 20.59 | 20.59 | 17.27 | 8.68 | 11.80 | 45.07 | 18.84 | 40.83 | 27.46 |
| nyan | 58.12 | 55.81 | 51.78 | 51.78 | 41.58 | 35.81 | 47.69 | 83.71 | 48.89 | 64.09 | 45.60 |
| metaballs | 3.36 | 1.60 | 1.33 | 1.33 | 1.03 | 0.64 | 1.01 | 1.65 | 1.08 | 1.39 | 0.91 |
| tomkeddie_a | 0.19 | 0.12 | 0.11 | 0.11 | 0.09 | 0.13 | 0.11 | 0.45 | 0.37 | 1.09 | 0.61 |

#### Compressors on vgch2's blocks: background CPU s

| project | zstd-5 | zstd-1 | zstd1 | zstd1-nocheck | zstd3 | zstd9 | zstd1-long27 | lz4 | lz4hc9 | deflate1 | deflate6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 12.23 | 18.54 | 19.62 | 18.30 | 17.32 | 42.50 | 32.45 | 3.47 | 130.65 | 35.85 | 92.32 |
| rle_vga | 6.96 | 8.52 | 6.83 | 3.70 | 10.85 | 19.91 | 57.45 | 3.20 | 34.91 | 57.45 | 128.68 |
| dvd_screensaver | 0.05 | 0.05 | 0.05 | 0.05 | 0.07 | 0.36 | 0.11 | 0.03 | 1.18 | 0.25 | 0.59 |
| colorbars | 0.14 | 0.14 | 0.13 | 0.09 | 0.20 | 0.50 | 0.28 | 0.13 | 2.18 | 1.81 | 5.81 |
| mandelbrot | 0.39 | 0.45 | 0.47 | 0.44 | 0.55 | 2.26 | 0.65 | 0.18 | 7.59 | 1.59 | 4.90 |
| atari2600 | 14.24 | 20.45 | 21.14 | 19.58 | 20.92 | 40.99 | 27.81 | 6.94 | 171.14 | 66.58 | 188.07 |
| vgadonut | 3.16 | 6.54 | 7.01 | 6.49 | 4.93 | 12.87 | 12.11 | 1.02 | 34.60 | 12.00 | 28.72 |
| cfib_demo | 0.55 | 0.60 | 0.63 | 0.57 | 0.72 | 2.43 | 1.71 | 0.35 | 7.73 | 2.73 | 9.70 |
| nyan | 1.10 | 1.22 | 1.24 | 1.17 | 1.64 | 6.92 | 1.57 | 0.54 | 13.63 | 3.94 | 18.07 |
| metaballs | 0.06 | 0.05 | 0.06 | 0.06 | 0.07 | 0.25 | 0.25 | 0.02 | 1.19 | 0.21 | 0.48 |
| tomkeddie_a | 0.03 | 0.03 | 0.03 | 0.02 | 0.04 | 0.14 | 0.14 | 0.01 | 0.22 | 0.27 | 0.59 |

Uncompressed, MB: nyancat 8,118.8, rle_vga 20,182.5, dvd_screensaver 46.7, colorbars 354.7, mandelbrot 187.2, atari2600 9,467.8, vgadonut 3,227.5, cfib_demo 508.6, nyan 264.6, metaballs 56.3, tomkeddie_a 73.2.

#### Compressors on events's blocks: MB

| project | zstd-5 | zstd-1 | zstd1 | zstd1-nocheck | zstd3 | zstd9 | zstd1-long27 | lz4 | lz4hc9 | deflate1 | deflate6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 176.22 | 171.20 | 153.32 | 153.32 | 150.18 | 96.14 | 129.47 | 231.66 | 123.83 | 180.56 | 105.76 |
| rle_vga | 1.28 | 1.05 | 1.05 | 1.05 | 1.04 | 0.85 | 1.15 | 34.20 | 34.05 | 46.96 | 22.42 |
| dvd_screensaver | 2.46 | 1.33 | 1.13 | 1.13 | 1.24 | 1.38 | 1.19 | 3.09 | 2.39 | 2.80 | 1.86 |
| colorbars | 0.32 | 0.25 | 0.20 | 0.20 | 0.32 | 0.15 | 0.17 | 0.84 | 0.82 | 7.74 | 4.11 |
| mandelbrot | 13.09 | 9.88 | 9.06 | 9.05 | 8.56 | 6.73 | 8.93 | 14.08 | 8.88 | 16.88 | 7.72 |
| atari2600 | 418.25 | 283.63 | 228.55 | 228.55 | 169.47 | 66.28 | 59.13 | 500.47 | 206.96 | 507.95 | 223.06 |
| vgadonut | 79.66 | 55.55 | 44.39 | 44.39 | 43.68 | 25.73 | 35.31 | 62.93 | 30.46 | 55.07 | 30.69 |
| cfib_demo | 18.50 | 10.81 | 9.38 | 9.38 | 8.03 | 4.54 | 6.36 | 23.06 | 8.77 | 24.37 | 11.27 |
| nyan | 52.81 | 45.62 | 36.26 | 36.26 | 31.21 | 26.39 | 35.91 | 53.73 | 36.62 | 45.60 | 33.61 |
| metaballs | 1.31 | 1.07 | 0.95 | 0.95 | 0.96 | 0.62 | 0.84 | 1.33 | 0.96 | 1.11 | 0.69 |
| tomkeddie_a | 0.12 | 0.08 | 0.08 | 0.08 | 0.08 | 0.07 | 0.09 | 0.34 | 0.24 | 0.74 | 0.51 |

#### Compressors on events's blocks: background CPU s

| project | zstd-5 | zstd-1 | zstd1 | zstd1-nocheck | zstd3 | zstd9 | zstd1-long27 | lz4 | lz4hc9 | deflate1 | deflate6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 6.65 | 7.36 | 7.55 | 6.42 | 10.43 | 38.31 | 23.35 | 2.71 | 95.18 | 22.15 | 56.93 |
| rle_vga | 4.42 | 4.41 | 4.43 | 2.43 | 5.96 | 13.25 | 10.40 | 2.11 | 23.30 | 33.28 | 71.25 |
| dvd_screensaver | 0.04 | 0.04 | 0.04 | 0.04 | 0.07 | 0.32 | 0.06 | 0.02 | 0.67 | 0.15 | 0.49 |
| colorbars | 0.10 | 0.09 | 0.09 | 0.05 | 0.15 | 0.32 | 0.11 | 0.04 | 0.06 | 0.92 | 2.13 |
| mandelbrot | 0.22 | 0.22 | 0.23 | 0.22 | 0.37 | 1.34 | 0.30 | 0.13 | 3.05 | 1.00 | 3.01 |
| atari2600 | 8.24 | 8.42 | 7.90 | 6.82 | 9.46 | 26.80 | 18.22 | 4.52 | 123.00 | 42.91 | 117.93 |
| vgadonut | 1.90 | 2.10 | 2.04 | 1.81 | 2.69 | 11.27 | 7.89 | 0.72 | 27.15 | 7.66 | 18.89 |
| cfib_demo | 0.41 | 0.39 | 0.38 | 0.34 | 0.53 | 1.77 | 0.93 | 0.23 | 5.64 | 2.06 | 5.23 |
| nyan | 0.71 | 0.77 | 0.81 | 0.81 | 1.88 | 6.94 | 1.00 | 0.48 | 7.29 | 2.88 | 15.69 |
| metaballs | 0.06 | 0.06 | 0.07 | 0.07 | 0.14 | 0.30 | 0.17 | 0.02 | 1.01 | 0.17 | 0.42 |
| tomkeddie_a | 0.03 | 0.01 | 0.02 | 0.01 | 0.02 | 0.05 | 0.05 | 0.01 | 0.12 | 0.13 | 0.29 |

Uncompressed, MB: nyancat 3,815.3, rle_vga 8,653.2, dvd_screensaver 22.5, colorbars 155.0, mandelbrot 88.4, atari2600 4,528.9, vgadonut 1,404.7, cfib_demo 225.0, nyan 118.9, metaballs 28.0, tomkeddie_a 40.2.

#### Compressors on planar's blocks: MB

| project | zstd-5 | zstd-1 | zstd1 | zstd1-nocheck | zstd3 | zstd9 | zstd1-long27 | lz4 | lz4hc9 | deflate1 | deflate6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 126.17 | 61.57 | 53.43 | 53.43 | 48.22 | 22.89 | 32.67 | 68.58 | 37.53 | 58.77 | 30.28 |
| rle_vga | 0.77 | 0.59 | 0.59 | 0.58 | 0.59 | 0.54 | 0.63 | 22.69 | 22.69 | 28.07 | 5.68 |
| dvd_screensaver | 2.37 | 1.56 | 1.35 | 1.35 | 1.17 | 1.35 | 1.36 | 2.88 | 2.33 | 2.16 | 1.73 |
| colorbars | 0.71 | 0.68 | 0.66 | 0.66 | 0.66 | 0.14 | 0.08 | 0.56 | 0.54 | 1.94 | 0.96 |
| mandelbrot | 11.15 | 8.92 | 8.09 | 8.08 | 7.64 | 6.37 | 8.04 | 11.87 | 8.38 | 11.26 | 6.85 |
| atari2600 | 10.24 | 9.59 | 9.04 | 9.04 | 4.26 | 2.34 | 2.83 | 47.85 | 22.19 | 107.70 | 32.90 |
| vgadonut | 53.85 | 40.45 | 35.30 | 35.29 | 35.13 | 22.50 | 30.35 | 51.72 | 27.65 | 41.61 | 24.20 |
| cfib_demo | 5.28 | 2.63 | 2.43 | 2.43 | 2.29 | 1.49 | 1.68 | 4.91 | 2.06 | 4.76 | 2.33 |
| nyan | 2.69 | 0.57 | 0.50 | 0.50 | 0.50 | 0.39 | 0.51 | 3.91 | 1.04 | 3.53 | 1.27 |
| metaballs | 1.35 | 0.95 | 0.70 | 0.70 | 0.90 | 0.60 | 0.68 | 1.25 | 0.93 | 0.95 | 0.67 |
| tomkeddie_a | 0.22 | 0.17 | 0.15 | 0.15 | 0.14 | 0.13 | 0.16 | 0.43 | 0.32 | 1.01 | 0.47 |

#### Compressors on planar's blocks: background CPU s

| project | zstd-5 | zstd-1 | zstd1 | zstd1-nocheck | zstd3 | zstd9 | zstd1-long27 | lz4 | lz4hc9 | deflate1 | deflate6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 3.15 | 3.60 | 3.83 | 3.08 | 4.35 | 10.93 | 9.17 | 1.01 | 37.31 | 13.12 | 30.26 |
| rle_vga | 2.41 | 2.40 | 2.39 | 1.32 | 3.25 | 10.36 | 20.10 | 1.13 | 1.46 | 21.15 | 45.13 |
| dvd_screensaver | 0.04 | 0.04 | 0.05 | 0.05 | 0.09 | 0.33 | 0.06 | 0.02 | 0.61 | 0.12 | 0.45 |
| colorbars | 0.10 | 0.09 | 0.08 | 0.06 | 0.10 | 0.28 | 0.14 | 0.03 | 0.06 | 0.52 | 1.14 |
| mandelbrot | 0.22 | 0.22 | 0.23 | 0.22 | 0.41 | 1.44 | 0.29 | 0.13 | 1.82 | 0.75 | 2.26 |
| atari2600 | 1.82 | 1.84 | 1.82 | 1.13 | 2.34 | 6.57 | 7.12 | 1.04 | 22.52 | 17.19 | 35.34 |
| vgadonut | 1.21 | 1.26 | 1.28 | 1.15 | 1.83 | 7.72 | 4.33 | 0.52 | 17.46 | 4.45 | 11.73 |
| cfib_demo | 0.16 | 0.13 | 0.14 | 0.11 | 0.16 | 0.79 | 0.46 | 0.06 | 2.30 | 0.74 | 1.67 |
| nyan | 0.08 | 0.06 | 0.06 | 0.04 | 0.07 | 0.22 | 0.15 | 0.04 | 0.47 | 0.47 | 0.89 |
| metaballs | 0.03 | 0.03 | 0.03 | 0.03 | 0.07 | 0.17 | 0.05 | 0.01 | 0.51 | 0.09 | 0.25 |
| tomkeddie_a | 0.03 | 0.02 | 0.03 | 0.02 | 0.03 | 0.09 | 0.13 | 0.01 | 0.22 | 0.18 | 0.40 |

Uncompressed, MB: nyancat 3,007.0, rle_vga 5,777.2, dvd_screensaver 16.4, colorbars 120.3, mandelbrot 63.8, atari2600 3,620.1, vgadonut 944.8, cfib_demo 160.9, nyan 91.8, metaballs 20.9, tomkeddie_a 41.3.

#### Raw candidate: transforms x compressors, MB (projected)

| project | pack6 zstd-1 | pack6 zstd1 | pack6 zstd3 | pack6 lz4 | xor6 zstd-1 | xor6 zstd1 | xor6 zstd3 | xor6 lz4 | xor-byteplanes zstd-1 | xor-byteplanes zstd1 | xor-byteplanes zstd3 | xor-byteplanes lz4 | xor-bitplanes zstd-1 | xor-bitplanes zstd1 | xor-bitplanes zstd3 | xor-bitplanes lz4 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 1,076.5 | 685.7 | 463.6 | 6,183.7 | 689.5 | 409.4 | 303.1 | 1,013.8 | 67.5 | 62.5 | 62.1 | 242.5 | 72.3 | 59.1 | 53.2 | 177.7 |
| rle_vga | 19.2 | 18.7 | 24.3 | 90.4 | 14.4 | 13.3 | 18.6 | 89.9 | 12.3 | 6.6 | 1.6 | 98.4 | 10.0 | 3.8 | 1.2 | 75.7 |
| dvd_screensaver | 19.9 | 16.9 | 16.7 | 107.4 | 27.0 | 21.8 | 24.4 | 101.6 | 20.4 | 10.9 | 14.8 | 112.4 | 18.5 | 13.4 | 6.7 | 111.0 |
| colorbars | 113.8 | 105.2 | 86.0 | 205.0 | 5.7 | 5.5 | 4.8 | 79.4 | 76.8 | 43.3 | 46.5 | 134.3 | 141.2 | 42.2 | 22.3 | 155.7 |
| mandelbrot | 78.2 | 73.4 | 72.7 | 399.1 | 107.0 | 90.2 | 75.8 | 367.1 | 81.5 | 52.9 | 43.3 | 400.1 | 133.3 | 59.4 | 75.3 | 463.1 |
| atari2600 | 2,076.5 | 1,352.8 | 911.4 | 2,431.1 | 765.0 | 515.0 | 454.3 | 1,355.6 | 100.6 | 51.8 | 33.4 | 166.8 | 126.9 | 61.4 | 40.9 | 107.1 |
| vgadonut | 434.7 | 267.6 | 267.8 | 573.0 | 399.7 | 246.5 | 231.7 | 604.8 | 88.8 | 73.5 | 72.9 | 227.0 | 141.4 | 118.7 | 114.4 | 356.8 |
| cfib_demo | 145.3 | 103.4 | 103.3 | 590.2 | 218.6 | 115.6 | 118.3 | 445.4 | 50.0 | 31.6 | 27.1 | 220.2 | 75.3 | 40.3 | 37.6 | 247.1 |
| nyan | 95.5 | 68.1 | 75.5 | 281.7 | 107.1 | 75.0 | 80.5 | 255.2 | 47.5 | 27.5 | 26.4 | 204.3 | 56.5 | 35.5 | 33.0 | 222.6 |
| metaballs | 25.4 | 25.3 | 25.2 | 186.1 | 36.9 | 33.1 | 34.3 | 176.8 | 22.2 | 13.8 | 15.9 | 199.3 | 14.8 | 9.5 | 10.5 | 184.8 |
| tomkeddie_a | 35.4 | 26.5 | 13.2 | 76.7 | 33.5 | 30.9 | 36.1 | 123.7 | 47.8 | 25.5 | 12.6 | 159.0 | 17.4 | 14.7 | 10.4 | 156.6 |

#### Raw candidate: transforms x compressors, background CPU s (projected)

| project | pack6 zstd-1 | pack6 zstd1 | pack6 zstd3 | pack6 lz4 | xor6 zstd-1 | xor6 zstd1 | xor6 zstd3 | xor6 lz4 | xor-byteplanes zstd-1 | xor-byteplanes zstd1 | xor-byteplanes zstd3 | xor-byteplanes lz4 | xor-bitplanes zstd-1 | xor-bitplanes zstd1 | xor-bitplanes zstd3 | xor-bitplanes lz4 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 80.6 | 78.0 | 66.0 | 53.9 | 57.9 | 58.7 | 57.2 | 34.0 | 53.1 | 52.9 | 55.6 | 41.2 | 110.0 | 110.2 | 112.8 | 102.1 |
| rle_vga | 35.9 | 35.9 | 39.7 | 29.4 | 36.2 | 36.2 | 38.7 | 29.3 | 49.7 | 49.8 | 52.2 | 39.5 | 105.9 | 106.0 | 107.7 | 97.6 |
| dvd_screensaver | 40.5 | 40.8 | 42.3 | 32.9 | 40.8 | 40.6 | 42.8 | 32.5 | 52.1 | 52.3 | 53.9 | 40.4 | 107.4 | 107.5 | 110.0 | 99.1 |
| colorbars | 44.1 | 44.9 | 55.4 | 34.3 | 41.4 | 41.6 | 43.4 | 33.2 | 54.8 | 55.0 | 58.1 | 42.5 | 112.3 | 112.5 | 113.9 | 101.7 |
| mandelbrot | 118.5 | 118.6 | 128.2 | 96.4 | 119.8 | 120.8 | 129.9 | 96.7 | 168.6 | 169.1 | 178.8 | 135.1 | 398.9 | 398.9 | 408.0 | 373.3 |
| atari2600 | 95.1 | 92.4 | 99.5 | 43.4 | 58.9 | 61.0 | 63.9 | 36.4 | 52.8 | 53.0 | 54.7 | 41.7 | 111.8 | 111.8 | 112.7 | 101.3 |
| vgadonut | 81.5 | 82.8 | 90.2 | 56.3 | 75.9 | 78.3 | 84.8 | 55.4 | 97.5 | 97.0 | 102.8 | 76.0 | 210.3 | 210.4 | 216.1 | 193.7 |
| cfib_demo | 75.2 | 77.1 | 82.3 | 59.7 | 73.9 | 75.2 | 80.4 | 57.6 | 95.6 | 95.4 | 100.6 | 75.7 | 212.3 | 212.6 | 215.9 | 197.2 |
| nyan | 45.4 | 45.9 | 48.2 | 35.2 | 42.6 | 43.4 | 47.3 | 33.1 | 56.0 | 56.3 | 59.0 | 44.6 | 121.0 | 121.4 | 123.2 | 111.8 |
| metaballs | 72.8 | 72.8 | 78.9 | 59.7 | 72.8 | 73.4 | 80.0 | 59.3 | 102.3 | 102.5 | 109.1 | 81.5 | 227.6 | 227.9 | 232.5 | 212.6 |
| tomkeddie_a | 41.9 | 42.0 | 44.7 | 33.6 | 42.1 | 42.6 | 44.9 | 33.8 | 55.0 | 55.1 | 57.0 | 43.4 | 114.5 | 114.0 | 116.2 | 105.7 |

#### Raw candidate: the transform's share of that CPU, s (projected)

| project | pack6 | xor6 | xor-byteplanes | xor-bitplanes |
|---|---:|---:|---:|---:|
| nyancat | 23.4 | 23.7 | 33.1 | 95.9 |
| rle_vga | 22.9 | 22.8 | 31.9 | 92.0 |
| dvd_screensaver | 25.1 | 25.3 | 31.8 | 92.5 |
| colorbars | 26.3 | 26.0 | 33.6 | 95.1 |
| mandelbrot | 76.2 | 76.2 | 112.1 | 355.3 |
| atari2600 | 23.5 | 23.1 | 33.7 | 95.3 |
| vgadonut | 43.1 | 42.1 | 61.8 | 182.6 |
| cfib_demo | 45.8 | 44.4 | 61.3 | 186.5 |
| nyan | 27.3 | 25.2 | 35.6 | 104.8 |
| metaballs | 47.1 | 46.5 | 66.3 | 201.3 |
| tomkeddie_a | 26.0 | 26.2 | 34.4 | 98.7 |

### Read and decode

#### Validation: every reader gives the same runs (canonical hash of full runs and of uo_out runs)

| project | vgch2 | planar | events | raw xor6 | FST (LZ4) | changelog to planar | events to planar | raw to planar |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | same | same | same | same | same | same | same | same |
| rle_vga | same | same | same | same | same | same | same | same |
| dvd_screensaver | same | same | same | same | same | same | same | same |
| colorbars | same | same | same | same | same | same | same | same |
| mandelbrot | same | same | same | same | same | same | same | same |
| atari2600 | same | same | same | same | same | same | same | same |
| vgadonut | same | same | same | same | same | same | same | same |
| cfib_demo | same | same | same | same | same | same | same | same |
| nyan | same | same | same | same | same | same | same | same |
| metaballs | same | same | same | same | same | same | same | same |
| tomkeddie_a | same | same | same | same | same | same | same | same |

#### Full scan to a no-op consumer (the samplers' trace decode), ns per clock: median of 5 rounds [min, max]

| project | vgch2 | planar | events | raw xor6 | FST (LZ4) |
|---|---:|---:|---:|---:|---:|
| nyancat | 10.37 [9.28, 10.74] | 18.06 [17.17, 19.61] | 17.23 [15.70, 19.99] | 33.18 [30.42, 36.63] | 90.88 [86.08, 97.00] |
| rle_vga | 20.53 [20.05, 22.48] | 44.61 [43.10, 51.13] | 39.98 [36.81, 40.18] | 35.26 [33.44, 40.31] | 182.84 [172.74, 218.00] |
| dvd_screensaver | 0.05 [0.04, 0.06] | 0.10 [0.09, 0.11] | 0.09 [0.09, 0.12] | 24.31 [23.31, 28.34] | 0.39 [0.37, 0.55] |
| colorbars | 0.23 [0.22, 0.49] | 0.79 [0.74, 0.81] | 0.68 [0.67, 0.90] | 23.44 [22.96, 27.82] | 3.24 [3.06, 4.36] |
| mandelbrot | 0.06 [0.06, 0.07] | 0.10 [0.10, 0.21] | 0.10 [0.09, 0.12] | 24.12 [23.21, 24.83] | 0.45 [0.37, 0.50] |
| atari2600 | 10.53 [10.05, 13.42] | 22.92 [20.47, 24.44] | 20.32 [19.29, 20.95] | 32.80 [31.91, 37.86] | 110.51 [108.58, 120.87] |
| vgadonut | 1.98 [1.94, 2.40] | 3.77 [3.50, 6.88] | 3.41 [3.36, 3.68] | 26.86 [26.18, 28.43] | 15.47 [14.52, 15.97] |
| cfib_demo | 0.25 [0.23, 0.38] | 0.54 [0.52, 0.55] | 0.52 [0.50, 0.75] | 24.82 [23.99, 26.36] | 2.14 [2.06, 2.28] |
| nyan | 0.48 [0.47, 0.74] | 0.59 [0.56, 0.70] | 0.60 [0.59, 1.02] | 24.56 [23.78, 27.09] | 2.82 [2.37, 3.19] |
| metaballs | 0.02 [0.02, 0.05] | 0.06 [0.06, 0.11] | 0.05 [0.05, 0.09] | 23.52 [23.23, 26.36] | 0.23 [0.22, 0.26] |
| tomkeddie_a | 0.05 [0.04, 0.10] | 0.16 [0.16, 0.33] | 0.15 [0.14, 0.24] | 28.95 [22.92, 32.69] | 1.28 [0.89, 1.69] |

#### uo_out alone (an export of one pin group), ns per clock: median of 5 rounds [min, max]

| project | vgch2 | planar | events | raw xor6 | FST (LZ4) |
|---|---:|---:|---:|---:|---:|
| nyancat | 12.41 [11.18, 15.06] | 5.11 [4.87, 5.29] | 12.60 [11.00, 13.50] | 31.73 [29.73, 35.10] | 65.75 [62.77, 70.38] |
| rle_vga | 24.24 [23.93, 26.96] | 0.02 [0.02, 0.02] | 17.51 [16.78, 20.54] | 21.27 [20.65, 23.90] | 35.60 [31.64, 36.01] |
| dvd_screensaver | 0.05 [0.05, 0.07] | 0.04 [0.04, 0.04] | 0.07 [0.07, 0.08] | 24.49 [23.16, 34.50] | 0.41 [0.38, 0.60] |
| colorbars | 0.31 [0.29, 0.58] | 0.09 [0.09, 0.10] | 0.38 [0.37, 0.61] | 22.60 [22.31, 32.49] | 1.33 [1.25, 1.76] |
| mandelbrot | 0.07 [0.07, 0.07] | 0.05 [0.05, 0.10] | 0.08 [0.07, 0.08] | 23.42 [23.35, 28.27] | 0.41 [0.39, 0.44] |
| atari2600 | 14.02 [11.90, 22.12] | 1.91 [1.83, 2.18] | 11.49 [11.38, 12.70] | 29.62 [26.82, 31.31] | 36.78 [36.38, 41.84] |
| vgadonut | 2.60 [2.31, 4.99] | 1.42 [1.31, 2.71] | 2.53 [2.49, 3.68] | 27.15 [26.47, 30.18] | 14.72 [13.97, 16.45] |
| cfib_demo | 0.31 [0.29, 0.58] | 0.16 [0.16, 0.18] | 0.37 [0.35, 0.61] | 24.31 [23.91, 25.67] | 1.94 [1.84, 2.91] |
| nyan | 0.58 [0.56, 1.08] | 0.07 [0.07, 0.11] | 0.41 [0.41, 0.62] | 24.47 [23.36, 27.98] | 1.47 [1.35, 2.11] |
| metaballs | 0.03 [0.03, 0.07] | 0.02 [0.02, 0.05] | 0.04 [0.04, 0.08] | 24.11 [23.15, 27.27] | 0.24 [0.22, 0.26] |
| tomkeddie_a | 0.06 [0.06, 0.14] | 0.05 [0.05, 0.12] | 0.10 [0.10, 0.22] | 23.78 [23.06, 28.11] | 0.76 [0.58, 1.11] |

#### Open, ms: median of 5 rounds [min, max]

| project | vgch2 | planar | events | raw xor6 | FST (LZ4) |
|---|---:|---:|---:|---:|---:|
| nyancat | 111.04 [98.31, 116.36] | 0.67 [0.64, 0.94] | 1.36 [1.30, 1.47] | 4.26 [3.83, 6.23] | 0.50 [0.45, 0.56] |
| rle_vga | 260.65 [239.35, 313.68] | 0.08 [0.08, 0.09] | 0.13 [0.12, 0.13] | 1.13 [1.00, 1.14] | 0.84 [0.76, 1.01] |
| dvd_screensaver | 10.34 [8.87, 11.14] | 0.15 [0.13, 0.17] | 0.15 [0.13, 0.21] | 1.96 [1.76, 2.38] | 0.11 [0.11, 0.14] |
| colorbars | 14.74 [14.30, 28.23] | 0.08 [0.08, 0.11] | 0.04 [0.04, 0.09] | 0.58 [0.46, 0.67] | 0.13 [0.12, 0.13] |
| mandelbrot | 58.06 [55.59, 96.41] | 0.81 [0.62, 1.12] | 0.93 [0.84, 1.27] | 7.08 [6.88, 7.73] | 0.11 [0.11, 0.12] |
| atari2600 | 143.52 [132.72, 169.56] | 0.78 [0.67, 0.98] | 1.65 [1.47, 2.64] | 4.23 [4.04, 4.27] | 0.60 [0.56, 0.80] |
| vgadonut | 141.45 [137.83, 151.17] | 1.00 [0.93, 1.75] | 1.16 [1.10, 1.49] | 7.57 [7.54, 7.81] | 0.24 [0.22, 0.49] |
| cfib_demo | 22.06 [21.12, 39.00] | 0.22 [0.21, 0.26] | 0.71 [0.71, 1.06] | 7.64 [7.09, 10.28] | 0.12 [0.12, 0.14] |
| nyan | 38.89 [37.40, 55.04] | 0.06 [0.06, 0.06] | 0.60 [0.51, 0.83] | 4.07 [3.78, 4.39] | 0.11 [0.11, 0.13] |
| metaballs | 14.49 [13.98, 32.99] | 0.09 [0.08, 0.15] | 0.12 [0.12, 0.23] | 2.91 [2.56, 3.63] | 0.11 [0.10, 0.13] |
| tomkeddie_a | 6.96 [5.81, 12.64] | 0.04 [0.03, 0.09] | 0.03 [0.03, 0.08] | 2.68 [2.40, 4.71] | 0.11 [0.10, 0.25] |

#### One window of 420,000 clocks from a fresh open (export --from/--to, slice), ms: median of 5 rounds [min, max]

| project | vgch2 | planar | events | raw xor6 | FST (LZ4) |
|---|---:|---:|---:|---:|---:|
| nyancat | 128.30 [118.63, 205.66] | 104.23 [41.93, 176.92] | 65.47 [27.63, 138.89] | 113.79 [86.31, 225.67] | 913.08 [799.34, 1984.57] |
| rle_vga | 268.80 [244.02, 375.49] | 108.51 [33.53, 171.58] | 48.88 [33.02, 99.43] | 103.92 [73.19, 182.94] | 891.20 [517.33, 2353.02] |
| dvd_screensaver | 9.86 [8.74, 16.74] | 0.70 [0.41, 1.36] | 0.69 [0.44, 1.81] | 96.61 [62.62, 206.67] | 582.25 [521.70, 920.79] |
| colorbars | 16.02 [14.12, 30.73] | 4.30 [1.83, 15.10] | 4.12 [1.78, 12.91] | 88.83 [60.46, 212.25] | 897.82 [157.26, 1769.51] |
| mandelbrot | 67.17 [54.17, 131.79] | 1.52 [0.97, 3.87] | 1.49 [1.12, 1.96] | 104.35 [69.85, 174.12] | 995.73 [318.03, 1646.32] |
| atari2600 | 191.94 [149.22, 380.53] | 94.93 [23.08, 317.06] | 61.07 [27.76, 125.43] | 116.76 [73.55, 250.85] | 868.97 [779.85, 2145.47] |
| vgadonut | 158.32 [145.00, 340.62] | 27.65 [8.58, 71.72] | 27.95 [10.11, 62.20] | 120.37 [93.75, 260.61] | 955.19 [828.75, 1657.72] |
| cfib_demo | 24.44 [22.98, 46.75] | 2.61 [1.34, 5.60] | 3.74 [2.41, 9.63] | 105.15 [72.50, 172.06] | 925.34 [740.63, 1623.43] |
| nyan | 44.92 [42.25, 105.47] | 5.03 [1.62, 12.62] | 6.99 [3.73, 13.36] | 95.52 [64.93, 226.87] | 1009.59 [501.66, 1750.08] |
| metaballs | 16.70 [13.67, 33.73] | 0.45 [0.17, 1.59] | 0.48 [0.24, 1.52] | 91.34 [67.36, 160.78] | 672.66 [588.97, 959.56] |
| tomkeddie_a | 6.25 [5.64, 14.00] | 1.36 [0.27, 5.27] | 1.32 [0.23, 3.97] | 120.36 [61.53, 219.82] | 937.56 [446.72, 1587.85] |

### The readers of a job

A job's derive step (tinytapeout-vga-videos `ttvga/harness/derive.py`) reads the trace three times: `tracetool info` (every port's change count: a full scan), `vgacap-sample` (all samplers in one pass; it reads `uo_out` and `rst_n`) and `tracetool uohash` (`uo_out` over the first 2^25 clocks). `vgacap-export` reads every pin when it is run (the full scan table). `results/read2`: 3 rounds, the formats rotated, each file its candidate's own. info on a planar file reads each stream on its own; infoscan is the same count over a merged full scan, as tracetool's code does it today.

#### Validation: the same counts and runs as vgacap's reader

| project | vgch2, lazy open | planar | events to planar | changelog to planar | raw to planar | events | raw xor6 |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | same | same | same | same | same | same | same |
| rle_vga | same | same | same | same | same | same | same |
| dvd_screensaver | same | same | same | same | same | same | same |
| colorbars | same | same | same | same | same | same | same |
| mandelbrot | same | same | same | same | same | same | same |
| atari2600 | same | same | same | same | same | same | same |
| vgadonut | same | same | same | same | same | same | same |
| cfib_demo | same | same | same | same | same | same | same |
| nyan | same | same | same | same | same | same | same |
| metaballs | same | same | same | same | same | same | same |
| tomkeddie_a | same | same | same | same | same | same | same |

#### tracetool info, s: median of the rounds [min, max]

| project | vgch2 | vgch2, lazy open | planar | events to planar | changelog to planar | raw to planar | events | raw xor6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 30.23 [28.43, 33.01] | 28.25 [26.78, 32.63] | 4.01 [3.93, 4.60] | 4.34 [3.76, 4.73] | 3.84 [3.56, 4.27] | 4.54 [3.68, 4.61] | 38.94 [37.05, 44.54] | 77.19 [75.46, 78.02] |
| rle_vga | 62.86 [54.20, 65.98] | 64.00 [56.35, 64.38] | 6.90 [5.75, 6.90] | 7.25 [6.45, 8.07] | 6.05 [5.69, 6.81] | 6.30 [6.13, 6.73] | 83.23 [79.29, 87.83] | 79.45 [73.38, 85.99] |
| dvd_screensaver | 0.14 [0.13, 0.15] | 0.14 [0.13, 0.34] | 0.03 [0.03, 0.07] | 0.03 [0.03, 0.06] | 0.03 [0.03, 0.06] | 0.03 [0.03, 0.05] | 0.20 [0.20, 0.37] | 37.00 [35.05, 38.68] |
| colorbars | 0.96 [0.96, 1.68] | 0.98 [0.96, 1.31] | 0.13 [0.13, 0.13] | 0.13 [0.12, 0.13] | 0.13 [0.12, 0.13] | 0.13 [0.13, 0.13] | 1.60 [1.56, 1.60] | 35.07 [34.82, 35.31] |
| mandelbrot | 0.61 [0.60, 1.28] | 0.78 [0.64, 1.46] | 0.17 [0.14, 0.26] | 0.15 [0.14, 0.28] | 0.16 [0.15, 0.27] | 0.19 [0.18, 0.36] | 0.84 [0.80, 1.46] | 139.95 [131.64, 145.74] |
| atari2600 | 37.87 [32.88, 39.02] | 32.41 [31.00, 33.07] | 3.54 [3.49, 3.82] | 3.90 [3.64, 4.21] | 3.96 [3.52, 4.18] | 3.62 [3.42, 4.51] | 50.46 [47.72, 50.87] | 80.03 [76.29, 82.45] |
| vgadonut | 10.30 [10.03, 10.74] | 10.68 [10.05, 11.25] | 1.32 [1.22, 1.33] | 1.31 [1.26, 1.38] | 1.35 [1.23, 1.39] | 1.31 [1.24, 1.31] | 14.53 [13.64, 14.80] | 85.29 [82.17, 88.37] |
| cfib_demo | 1.56 [1.54, 1.72] | 1.63 [1.38, 1.66] | 0.19 [0.19, 0.19] | 0.19 [0.18, 0.19] | 0.19 [0.19, 0.21] | 0.21 [0.20, 0.22] | 2.33 [2.32, 2.46] | 76.59 [74.87, 82.35] |
| nyan | 1.51 [1.20, 1.68] | 1.20 [1.18, 1.30] | 0.10 [0.09, 0.10] | 0.09 [0.09, 0.09] | 0.10 [0.09, 0.10] | 0.11 [0.10, 0.13] | 1.39 [1.33, 1.68] | 36.39 [35.85, 40.39] |
| metaballs | 0.16 [0.15, 0.17] | 0.15 [0.15, 0.16] | 0.03 [0.03, 0.03] | 0.03 [0.03, 0.03] | 0.03 [0.03, 0.03] | 0.03 [0.03, 0.06] | 0.26 [0.23, 0.35] | 69.68 [69.49, 71.61] |
| tomkeddie_a | 0.27 [0.20, 0.43] | 0.19 [0.18, 0.43] | 0.04 [0.04, 0.04] | 0.04 [0.04, 0.04] | 0.04 [0.04, 0.05] | 0.04 [0.04, 0.04] | 0.32 [0.31, 0.33] | 34.16 [34.08, 38.38] |

#### tracetool info as a merged full scan, s: median of the rounds [min, max]

| project | vgch2 | vgch2, lazy open | planar | events to planar | changelog to planar | raw to planar | events | raw xor6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 28.32 [26.66, 31.61] | 28.26 [26.41, 29.05] | 49.57 [37.96, 63.55] | 39.57 [38.55, 41.78] | 38.49 [38.05, 43.68] | 46.72 [38.07, 46.79] | 40.10 [38.95, 43.77] | 76.45 [76.18, 77.89] |
| rle_vga | 61.77 [58.30, 63.13] | 61.82 [57.88, 62.97] | 107.48 [89.14, 111.74] | 91.46 [84.86, 98.82] | 94.58 [85.65, 97.07] | 94.00 [92.96, 100.74] | 84.13 [81.04, 85.74] | 79.72 [73.39, 86.83] |
| dvd_screensaver | 0.13 [0.12, 0.13] | 0.13 [0.13, 0.34] | 0.21 [0.21, 0.48] | 0.21 [0.21, 0.40] | 0.21 [0.21, 0.40] | 0.22 [0.21, 0.45] | 0.20 [0.20, 0.40] | 34.68 [34.58, 35.06] |
| colorbars | 1.03 [0.95, 1.10] | 0.95 [0.92, 1.58] | 1.71 [1.70, 1.74] | 1.69 [1.67, 1.71] | 1.70 [1.69, 1.76] | 1.69 [1.69, 1.71] | 1.60 [1.56, 1.72] | 35.23 [34.64, 37.41] |
| mandelbrot | 0.60 [0.59, 1.32] | 0.70 [0.63, 1.47] | 1.12 [0.82, 1.77] | 0.95 [0.83, 1.84] | 0.88 [0.82, 1.82] | 1.12 [0.87, 1.89] | 0.84 [0.78, 1.55] | 139.95 [139.31, 149.35] |
| atari2600 | 32.58 [31.55, 36.92] | 32.15 [30.89, 32.68] | 47.18 [44.78, 48.06] | 48.33 [45.44, 50.37] | 49.54 [46.09, 49.56] | 49.58 [46.62, 53.70] | 47.74 [45.22, 49.46] | 81.02 [76.31, 81.20] |
| vgadonut | 10.73 [10.17, 17.28] | 10.78 [10.41, 10.81] | 15.24 [14.49, 15.32] | 15.29 [14.64, 16.38] | 15.27 [14.24, 15.62] | 15.15 [14.20, 15.31] | 14.37 [13.90, 14.62] | 87.13 [86.86, 87.53] |
| cfib_demo | 1.58 [1.54, 1.61] | 1.59 [1.41, 1.62] | 2.44 [2.41, 2.53] | 2.54 [2.44, 2.57] | 2.45 [2.37, 2.48] | 2.43 [2.41, 2.44] | 2.48 [2.33, 2.56] | 77.19 [75.72, 78.45] |
| nyan | 1.33 [1.23, 1.76] | 1.22 [1.18, 1.51] | 1.30 [1.29, 1.39] | 1.29 [1.27, 1.29] | 1.29 [1.28, 1.34] | 1.32 [1.29, 1.68] | 1.40 [1.36, 1.70] | 37.51 [36.11, 41.14] |
| metaballs | 0.15 [0.14, 0.17] | 0.20 [0.16, 0.20] | 0.24 [0.24, 0.34] | 0.25 [0.24, 0.31] | 0.29 [0.24, 0.30] | 0.27 [0.25, 0.30] | 0.25 [0.23, 0.35] | 70.08 [69.60, 78.09] |
| tomkeddie_a | 0.21 [0.19, 0.42] | 0.19 [0.18, 0.43] | 0.36 [0.35, 0.36] | 0.36 [0.35, 0.39] | 0.36 [0.35, 0.40] | 0.39 [0.35, 0.39] | 0.32 [0.31, 0.33] | 35.07 [34.73, 35.19] |

#### vgacap-sample's pass (uo_out and rst_n), s: median of the rounds [min, max]

| project | vgch2 | vgch2, lazy open | planar | events to planar | changelog to planar | raw to planar | events | raw xor6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 18.25 [18.12, 22.21] | 17.04 [16.27, 17.96] | 10.07 [8.71, 10.78] | 9.40 [8.64, 10.06] | 9.01 [8.34, 11.89] | 9.01 [8.30, 9.96] | 31.33 [29.91, 31.74] | 63.09 [61.60, 71.00] |
| rle_vga | 32.98 [31.07, 35.02] | 36.85 [32.09, 38.48] | 0.04 [0.03, 0.04] | 0.03 [0.03, 0.04] | 0.03 [0.03, 0.04] | 0.04 [0.03, 0.04] | 57.53 [55.38, 64.35] | 51.65 [48.19, 55.71] |
| dvd_screensaver | 0.07 [0.07, 0.08] | 0.08 [0.08, 0.22] | 0.10 [0.07, 0.18] | 0.07 [0.07, 0.14] | 0.07 [0.07, 0.13] | 0.07 [0.07, 0.17] | 0.15 [0.15, 0.25] | 39.09 [35.16, 39.40] |
| colorbars | 0.44 [0.43, 0.48] | 0.47 [0.43, 0.66] | 0.15 [0.15, 0.16] | 0.16 [0.15, 0.19] | 0.16 [0.15, 0.16] | 0.16 [0.16, 0.19] | 1.10 [1.08, 1.29] | 35.04 [33.69, 36.96] |
| mandelbrot | 0.40 [0.38, 0.85] | 0.66 [0.43, 0.93] | 0.33 [0.30, 0.64] | 0.44 [0.30, 0.63] | 0.33 [0.30, 0.67] | 0.42 [0.40, 0.77] | 0.67 [0.60, 1.12] | 138.18 [129.60, 144.43] |
| atari2600 | 18.81 [17.88, 21.40] | 18.99 [17.75, 19.06] | 3.15 [3.10, 3.54] | 3.32 [3.00, 3.89] | 3.14 [3.03, 3.52] | 3.25 [3.23, 4.22] | 33.77 [31.22, 34.58] | 65.73 [63.37, 65.76] |
| vgadonut | 6.82 [6.55, 7.23] | 6.92 [6.89, 6.96] | 4.50 [4.25, 4.74] | 4.51 [4.39, 4.79] | 4.52 [4.14, 4.68] | 4.54 [4.24, 4.62] | 10.46 [10.11, 10.94] | 81.47 [80.82, 87.29] |
| cfib_demo | 0.93 [0.92, 1.00] | 0.93 [0.90, 0.98] | 0.56 [0.56, 0.73] | 0.57 [0.55, 0.71] | 0.56 [0.56, 0.84] | 0.56 [0.55, 0.66] | 1.82 [1.68, 1.92] | 72.80 [69.42, 73.24] |
| nyan | 0.82 [0.81, 0.88] | 0.84 [0.84, 0.85] | 0.13 [0.13, 0.13] | 0.13 [0.13, 0.13] | 0.13 [0.12, 0.13] | 0.15 [0.13, 0.17] | 1.26 [1.00, 1.49] | 37.33 [35.27, 39.17] |
| metaballs | 0.09 [0.08, 0.10] | 0.10 [0.10, 0.10] | 0.08 [0.08, 0.09] | 0.08 [0.08, 0.10] | 0.08 [0.08, 0.09] | 0.11 [0.08, 0.11] | 0.19 [0.18, 0.22] | 69.76 [67.48, 78.69] |
| tomkeddie_a | 0.10 [0.09, 0.24] | 0.11 [0.10, 0.26] | 0.09 [0.09, 0.09] | 0.09 [0.09, 0.10] | 0.10 [0.09, 0.11] | 0.10 [0.09, 0.12] | 0.23 [0.23, 0.25] | 38.56 [38.51, 39.12] |

#### tracetool uohash (open + uo_out over 2^25 clocks), ms: median of the rounds [min, max]

| project | vgch2 | vgch2, lazy open | planar | events to planar | changelog to planar | raw to planar | events | raw xor6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 494.0 [459.0, 747.9] | 402.5 [370.1, 439.6] | 189.0 [185.3, 206.8] | 182.3 [182.1, 193.9] | 168.1 [166.3, 309.2] | 180.9 [169.3, 189.3] | 407.3 [401.3, 420.5] | 1112.1 [998.2, 1297.3] |
| rle_vga | 941.6 [934.7, 1054.3] | 800.5 [800.5, 937.5] | 1.1 [0.9, 1.3] | 0.8 [0.7, 0.9] | 0.8 [0.7, 0.9] | 0.9 [0.8, 0.9] | 566.1 [562.1, 689.7] | 755.0 [680.3, 793.9] |
| dvd_screensaver | 10.2 [10.0, 12.6] | 3.6 [2.8, 7.3] | 1.5 [1.5, 4.0] | 1.5 [1.5, 4.4] | 1.5 [1.5, 2.7] | 1.7 [1.4, 3.2] | 2.5 [2.4, 4.2] | 850.3 [841.2, 1382.9] |
| colorbars | 23.6 [23.3, 23.9] | 15.0 [14.3, 19.1] | 3.2 [3.2, 3.4] | 3.3 [3.2, 3.5] | 3.4 [3.2, 3.5] | 3.1 [3.0, 3.5] | 13.1 [12.8, 22.1] | 903.3 [788.6, 921.6] |
| mandelbrot | 57.5 [55.9, 131.4] | 5.3 [4.5, 7.7] | 2.4 [2.4, 2.8] | 2.4 [2.3, 5.3] | 2.4 [2.4, 2.8] | 3.3 [3.2, 3.4] | 5.7 [3.7, 8.2] | 788.2 [775.2, 904.8] |
| atari2600 | 537.7 [532.1, 560.3] | 445.3 [428.2, 464.0] | 66.3 [65.9, 72.4] | 65.4 [59.9, 90.7] | 71.6 [59.8, 88.2] | 65.8 [61.3, 74.6] | 392.6 [384.1, 561.3] | 1025.4 [1016.9, 1065.0] |
| vgadonut | 215.2 [210.7, 224.1] | 191.8 [189.0, 198.3] | 48.5 [44.6, 58.8] | 47.9 [45.5, 57.5] | 61.1 [53.7, 61.3] | 48.0 [46.9, 48.4] | 85.1 [83.0, 86.3] | 980.0 [958.1, 986.8] |
| cfib_demo | 32.0 [29.2, 32.4] | 12.9 [12.1, 13.8] | 6.7 [6.2, 7.0] | 6.8 [6.7, 7.0] | 6.6 [6.3, 11.7] | 5.5 [5.5, 6.7] | 15.2 [14.5, 23.3] | 886.0 [818.6, 912.4] |
| nyan | 55.7 [54.8, 58.9] | 34.3 [34.0, 34.6] | 2.7 [2.7, 2.8] | 2.7 [2.6, 2.7] | 2.6 [2.6, 2.6] | 2.7 [2.5, 3.1] | 23.8 [14.9, 24.8] | 884.8 [785.6, 937.6] |
| metaballs | 16.9 [14.5, 17.5] | 1.7 [1.6, 1.7] | 0.8 [0.8, 0.9] | 0.8 [0.8, 1.1] | 0.8 [0.8, 1.5] | 0.9 [0.9, 1.6] | 1.5 [1.4, 1.8] | 791.4 [788.1, 852.9] |
| tomkeddie_a | 8.8 [7.7, 17.5] | 3.4 [3.3, 7.7] | 1.9 [1.8, 1.9] | 1.8 [1.8, 1.9] | 1.9 [1.9, 3.4] | 1.9 [1.8, 2.3] | 3.7 [3.6, 3.7] | 894.6 [783.9, 895.6] |

#### Open, ms: median of the rounds [min, max]

| project | vgch2 | vgch2, lazy open | planar | events to planar | changelog to planar | raw to planar | events | raw xor6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 111.4 [102.1, 123.6] | 29.3 [29.3, 30.2] | 0.7 [0.7, 0.7] | 1.7 [1.6, 1.7] | 2.8 [2.7, 3.3] | 5.0 [4.8, 7.3] | 1.4 [1.3, 1.5] | 4.5 [4.1, 5.7] |
| rle_vga | 241.5 [241.4, 307.5] | 96.7 [82.2, 210.2] | 0.1 [0.1, 0.1] | 0.1 [0.1, 0.1] | 0.1 [0.1, 0.1] | 0.1 [0.1, 0.2] | 0.1 [0.1, 0.1] | 1.1 [1.0, 1.1] |
| dvd_screensaver | 9.6 [9.4, 11.8] | 1.3 [1.0, 2.3] | 0.1 [0.1, 0.3] | 0.2 [0.1, 0.2] | 0.3 [0.1, 0.3] | 0.3 [0.3, 0.4] | 0.1 [0.1, 0.2] | 1.8 [1.7, 2.5] |
| colorbars | 14.6 [14.3, 24.6] | 4.7 [4.6, 6.2] | 0.1 [0.1, 0.1] | 0.1 [0.1, 0.1] | 0.1 [0.1, 0.1] | 0.2 [0.2, 0.2] | 0.1 [0.0, 0.1] | 0.5 [0.5, 0.5] |
| mandelbrot | 72.4 [55.2, 120.3] | 3.0 [2.3, 3.9] | 0.8 [0.6, 1.1] | 0.9 [0.8, 1.3] | 1.0 [1.0, 1.1] | 1.7 [1.5, 2.6] | 1.1 [1.0, 1.1] | 10.0 [7.3, 11.6] |
| atari2600 | 179.7 [142.2, 261.2] | 55.4 [49.2, 57.0] | 0.8 [0.8, 1.0] | 0.9 [0.9, 1.0] | 1.2 [0.9, 1.3] | 1.2 [1.1, 1.2] | 1.8 [1.8, 1.8] | 4.9 [4.0, 5.5] |
| vgadonut | 136.1 [131.5, 195.4] | 112.2 [101.6, 153.1] | 1.0 [0.9, 1.0] | 1.2 [1.1, 1.3] | 1.3 [1.2, 1.3] | 4.0 [3.9, 4.1] | 1.2 [1.2, 1.3] | 8.1 [7.2, 8.2] |
| cfib_demo | 24.0 [21.0, 24.6] | 2.4 [2.0, 2.6] | 0.2 [0.2, 0.3] | 0.3 [0.3, 0.3] | 0.3 [0.3, 0.4] | 0.5 [0.4, 0.6] | 0.7 [0.7, 0.8] | 7.4 [7.1, 8.1] |
| nyan | 67.3 [41.9, 85.5] | 15.6 [15.5, 15.8] | 0.1 [0.1, 0.1] | 0.1 [0.1, 0.1] | 0.1 [0.1, 0.1] | 0.2 [0.2, 0.3] | 0.5 [0.5, 0.6] | 3.8 [3.7, 4.7] |
| metaballs | 14.4 [14.4, 16.9] | 0.6 [0.6, 0.6] | 0.1 [0.1, 0.1] | 0.1 [0.1, 0.2] | 0.1 [0.1, 0.1] | 0.3 [0.2, 0.3] | 0.1 [0.1, 0.2] | 2.6 [2.6, 2.9] |
| tomkeddie_a | 6.2 [6.1, 12.6] | 1.3 [1.2, 2.7] | 0.0 [0.0, 0.0] | 0.0 [0.0, 0.1] | 0.0 [0.0, 0.1] | 0.1 [0.1, 0.1] | 0.0 [0.0, 0.0] | 2.4 [2.4, 2.5] |

#### One window of 420,000 clocks from a fresh open, ms: median of the rounds [min, max]

| project | vgch2 | vgch2, lazy open | planar | events to planar | changelog to planar | raw to planar | events | raw xor6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 128.5 [116.8, 211.5] | 51.0 [42.3, 76.7] | 134.2 [68.4, 211.0] | 73.1 [28.6, 125.5] | 41.2 [21.8, 91.4] | 30.7 [20.2, 45.0] | 69.4 [32.9, 103.5] | 128.8 [94.5, 188.8] |
| rle_vga | 240.5 [226.7, 296.8] | 97.8 [92.4, 137.4] | 81.1 [38.9, 175.3] | 49.6 [38.0, 98.4] | 57.1 [29.2, 110.3] | 54.7 [25.0, 81.1] | 51.9 [37.1, 115.6] | 109.4 [82.4, 193.3] |
| dvd_screensaver | 9.1 [8.6, 16.7] | 1.8 [1.5, 5.0] | 1.1 [0.5, 3.0] | 1.0 [0.5, 2.6] | 0.9 [0.5, 1.9] | 0.3 [0.2, 0.7] | 0.9 [0.5, 1.7] | 137.2 [103.6, 253.7] |
| colorbars | 15.8 [14.6, 18.4] | 7.2 [5.9, 12.2] | 5.4 [2.3, 8.1] | 5.4 [2.3, 8.3] | 5.7 [2.3, 8.4] | 0.8 [0.6, 1.4] | 5.1 [2.3, 10.5] | 111.9 [69.7, 209.5] |
| mandelbrot | 54.8 [51.8, 126.2] | 6.2 [2.9, 9.8] | 1.6 [0.9, 4.0] | 1.6 [0.9, 4.5] | 1.4 [0.9, 3.4] | 1.6 [1.3, 3.0] | 1.9 [1.1, 3.2] | 119.1 [93.7, 168.2] |
| atari2600 | 153.1 [137.4, 176.9] | 75.4 [67.9, 117.9] | 72.8 [39.9, 149.8] | 78.4 [33.9, 134.7] | 33.3 [20.2, 60.8] | 29.3 [15.8, 50.0] | 84.6 [47.5, 142.0] | 139.7 [103.6, 242.6] |
| vgadonut | 149.9 [138.8, 176.4] | 125.2 [114.7, 148.6] | 25.0 [8.7, 40.5] | 25.3 [8.8, 43.2] | 23.6 [9.2, 43.7] | 8.2 [5.3, 13.2] | 25.8 [11.8, 38.5] | 116.9 [100.8, 194.4] |
| cfib_demo | 23.9 [20.8, 27.7] | 5.2 [4.1, 6.7] | 3.4 [1.5, 5.4] | 3.4 [1.6, 5.4] | 3.9 [1.5, 9.2] | 1.0 [0.6, 1.3] | 4.1 [2.5, 7.1] | 123.9 [99.8, 190.0] |
| nyan | 44.3 [42.0, 66.9] | 23.2 [22.0, 28.9] | 4.5 [2.5, 7.9] | 4.7 [2.6, 8.1] | 5.9 [2.6, 12.3] | 1.1 [0.5, 1.5] | 8.3 [4.8, 16.5] | 138.2 [84.3, 193.8] |
| metaballs | 16.7 [13.7, 27.7] | 1.1 [1.0, 1.9] | 0.3 [0.2, 0.6] | 0.4 [0.2, 1.0] | 0.3 [0.2, 0.8] | 0.2 [0.2, 0.4] | 0.4 [0.2, 0.6] | 111.1 [68.3, 169.8] |
| tomkeddie_a | 6.3 [5.5, 14.4] | 1.9 [1.3, 4.7] | 1.5 [0.3, 2.2] | 1.7 [0.3, 4.1] | 1.6 [0.3, 2.4] | 0.3 [0.2, 0.5] | 1.2 [0.2, 1.9] | 135.0 [81.5, 257.4] |

### The trace's share of R2: background CPU and every read of a job

Background CPU plus `tracetool info`, `vgacap-sample`'s pass and `tracetool uohash`, and two more opens (each of the three opens the file; uohash's time includes its own). % of the pilot's untraced simulation CPU: the trace's own share, which leaves the rest of the 5% to the samplers, hashes and PNGs. The same compression work measured up to 2.5 times apart from one run to another (the encode2 tables), so the share is given at encode2's median and at the lowest background figure measured (the first round's and encode2's three). The reads' part follows, in s.

#### With tracetool info counting each planar stream on its own, background at its median: the trace's share of R2, %

| project | vgch2 (zstd 1) | events (zstd 1) | raw xor6 (zstd 1) | planar (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 17.6 | 19.0 | 48.0 | 4.5 | 7.4 | 8.8 | 12.4 |
| rle_vga | 25.0 | 32.6 | 36.7 | 2.5 | 8.2 | 7.9 | 13.7 |
| dvd_screensaver | 0.1 | 0.1 | 31.9 | 0.1 | 0.1 | 0.1 | 5.2 |
| colorbars | 0.5 | 0.8 | 29.4 | 0.1 | 0.2 | 0.3 | 5.6 |
| mandelbrot | 0.1 | 0.1 | 22.6 | 0.0 | 0.1 | 0.1 | 3.9 |
| atari2600 | 11.1 | 12.6 | 27.3 | 1.3 | 3.3 | 4.1 | 6.0 |
| vgadonut | 3.3 | 3.6 | 32.3 | 1.0 | 1.5 | 1.8 | 6.3 |
| cfib_demo | 0.6 | 0.7 | 36.4 | 0.2 | 0.3 | 0.3 | 5.9 |
| nyan | 1.1 | 1.0 | 32.7 | 0.1 | 0.2 | 0.3 | 5.2 |
| metaballs | 0.1 | 0.1 | 31.0 | 0.0 | 0.0 | 0.0 | 5.3 |
| tomkeddie_a | 0.1 | 0.2 | 29.9 | 0.0 | 0.1 | 0.1 | 5.1 |

#### With tracetool info counting each planar stream on its own, background at its lowest: the trace's share of R2, %

| project | vgch2 (zstd 1) | events (zstd 1) | raw xor6 (zstd 1) | planar (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 16.9 | 18.9 | 47.9 | 4.5 | 7.0 | 7.7 | 11.9 |
| rle_vga | 23.0 | 32.2 | 36.4 | 2.1 | 6.9 | 6.9 | 11.8 |
| dvd_screensaver | 0.1 | 0.1 | 31.9 | 0.0 | 0.1 | 0.1 | 4.0 |
| colorbars | 0.4 | 0.8 | 27.5 | 0.1 | 0.2 | 0.3 | 3.8 |
| mandelbrot | 0.1 | 0.1 | 21.2 | 0.0 | 0.1 | 0.1 | 2.4 |
| atari2600 | 10.4 | 12.3 | 24.8 | 1.2 | 2.9 | 3.6 | 4.5 |
| vgadonut | 3.2 | 3.6 | 30.6 | 0.9 | 1.5 | 1.7 | 4.3 |
| cfib_demo | 0.5 | 0.7 | 33.9 | 0.1 | 0.2 | 0.3 | 3.8 |
| nyan | 1.1 | 1.0 | 32.7 | 0.1 | 0.2 | 0.2 | 5.0 |
| metaballs | 0.1 | 0.1 | 30.9 | 0.0 | 0.0 | 0.0 | 4.9 |
| tomkeddie_a | 0.1 | 0.1 | 29.4 | 0.0 | 0.1 | 0.1 | 3.3 |

#### With tracetool info counting each planar stream on its own: the reads alone, s for the whole trace

| project | vgch2 (zstd 1) | events (zstd 1) | raw xor6 (zstd 1) | planar (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 49.2 | 70.7 | 141.4 | 14.3 | 13.9 | 13.0 | 13.7 |
| rle_vga | 97.3 | 141.3 | 131.9 | 6.9 | 7.3 | 6.1 | 6.3 |
| dvd_screensaver | 0.2 | 0.4 | 76.9 | 0.1 | 0.1 | 0.1 | 0.1 |
| colorbars | 1.5 | 2.7 | 71.0 | 0.3 | 0.3 | 0.3 | 0.3 |
| mandelbrot | 1.2 | 1.5 | 278.9 | 0.5 | 0.6 | 0.5 | 0.6 |
| atari2600 | 57.6 | 84.6 | 146.8 | 6.8 | 7.3 | 7.2 | 6.9 |
| vgadonut | 17.6 | 25.1 | 167.8 | 5.9 | 5.9 | 5.9 | 5.9 |
| cfib_demo | 2.6 | 4.2 | 150.3 | 0.8 | 0.8 | 0.8 | 0.8 |
| nyan | 2.5 | 2.7 | 74.6 | 0.2 | 0.2 | 0.2 | 0.3 |
| metaballs | 0.3 | 0.5 | 140.2 | 0.1 | 0.1 | 0.1 | 0.1 |
| tomkeddie_a | 0.4 | 0.6 | 73.6 | 0.1 | 0.1 | 0.1 | 0.1 |

#### With tracetool info as it is: a merged full scan, background at its median: the trace's share of R2, %

| project | vgch2 (zstd 1) | events (zstd 1) | raw xor6 (zstd 1) | planar (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 17.6 | 19.0 | 48.0 | 15.7 | 16.1 | 17.3 | 22.8 |
| rle_vga | 25.0 | 32.6 | 36.7 | 24.7 | 26.8 | 27.4 | 33.1 |
| dvd_screensaver | 0.1 | 0.1 | 31.9 | 0.1 | 0.1 | 0.1 | 5.3 |
| colorbars | 0.5 | 0.8 | 29.4 | 0.6 | 0.7 | 0.7 | 6.0 |
| mandelbrot | 0.1 | 0.1 | 22.6 | 0.1 | 0.1 | 0.1 | 3.9 |
| atari2600 | 11.1 | 12.6 | 27.3 | 7.3 | 9.3 | 10.3 | 12.3 |
| vgadonut | 3.3 | 3.6 | 32.3 | 2.8 | 3.3 | 3.6 | 8.1 |
| cfib_demo | 0.6 | 0.7 | 36.4 | 0.5 | 0.7 | 0.7 | 6.3 |
| nyan | 1.1 | 1.0 | 32.7 | 0.4 | 0.6 | 0.6 | 5.6 |
| metaballs | 0.1 | 0.1 | 31.0 | 0.1 | 0.1 | 0.1 | 5.3 |
| tomkeddie_a | 0.1 | 0.2 | 29.9 | 0.1 | 0.2 | 0.2 | 5.2 |

#### With tracetool info as it is: a merged full scan, background at its lowest: the trace's share of R2, %

| project | vgch2 (zstd 1) | events (zstd 1) | raw xor6 (zstd 1) | planar (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 16.9 | 18.9 | 47.9 | 15.7 | 15.7 | 16.3 | 22.3 |
| rle_vga | 23.0 | 32.2 | 36.4 | 24.3 | 25.5 | 26.5 | 31.1 |
| dvd_screensaver | 0.1 | 0.1 | 31.9 | 0.1 | 0.1 | 0.1 | 4.1 |
| colorbars | 0.4 | 0.8 | 27.5 | 0.5 | 0.7 | 0.7 | 4.2 |
| mandelbrot | 0.1 | 0.1 | 21.2 | 0.1 | 0.1 | 0.1 | 2.4 |
| atari2600 | 10.4 | 12.3 | 24.8 | 7.1 | 9.0 | 9.8 | 10.8 |
| vgadonut | 3.2 | 3.6 | 30.6 | 2.8 | 3.3 | 3.5 | 6.1 |
| cfib_demo | 0.5 | 0.7 | 33.9 | 0.5 | 0.6 | 0.7 | 4.2 |
| nyan | 1.1 | 1.0 | 32.7 | 0.4 | 0.5 | 0.6 | 5.3 |
| metaballs | 0.1 | 0.1 | 30.9 | 0.1 | 0.1 | 0.1 | 4.9 |
| tomkeddie_a | 0.1 | 0.1 | 29.4 | 0.1 | 0.2 | 0.2 | 3.4 |

#### With tracetool info as it is: a merged full scan: the reads alone, s for the whole trace

| project | vgch2 (zstd 1) | events (zstd 1) | raw xor6 (zstd 1) | planar (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 49.2 | 70.7 | 141.4 | 59.8 | 49.2 | 47.7 | 55.9 |
| rle_vga | 97.3 | 141.3 | 131.9 | 107.5 | 91.5 | 94.6 | 94.0 |
| dvd_screensaver | 0.2 | 0.4 | 76.9 | 0.3 | 0.3 | 0.3 | 0.3 |
| colorbars | 1.5 | 2.7 | 71.0 | 1.9 | 1.9 | 1.9 | 1.9 |
| mandelbrot | 1.2 | 1.5 | 278.9 | 1.5 | 1.4 | 1.2 | 1.6 |
| atari2600 | 57.6 | 84.6 | 146.8 | 50.4 | 51.7 | 52.8 | 52.9 |
| vgadonut | 17.6 | 25.1 | 167.8 | 19.8 | 19.9 | 19.9 | 19.7 |
| cfib_demo | 2.6 | 4.2 | 150.3 | 3.0 | 3.1 | 3.0 | 3.0 |
| nyan | 2.5 | 2.7 | 74.6 | 1.4 | 1.4 | 1.4 | 1.5 |
| metaballs | 0.3 | 0.5 | 140.2 | 0.3 | 0.3 | 0.4 | 0.4 |
| tomkeddie_a | 0.4 | 0.6 | 73.6 | 0.5 | 0.5 | 0.5 | 0.5 |

#### With a header-only tracetool info (no scan; the job reads only blocks, clocks and open.slice_*), background at its median: the trace's share of R2, %

| project | vgch2 (zstd 1) | events (zstd 1) | raw xor6 (zstd 1) | planar (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 10.1 | 9.4 | 29.0 | 3.5 | 6.3 | 7.8 | 11.3 |
| rle_vga | 11.1 | 14.2 | 19.1 | 1.0 | 6.6 | 6.5 | 12.4 |
| dvd_screensaver | 0.1 | 0.1 | 21.6 | 0.0 | 0.1 | 0.1 | 5.2 |
| colorbars | 0.2 | 0.3 | 19.5 | 0.1 | 0.2 | 0.3 | 5.5 |
| mandelbrot | 0.1 | 0.1 | 15.1 | 0.0 | 0.1 | 0.0 | 3.9 |
| atari2600 | 5.9 | 5.7 | 16.4 | 0.8 | 2.7 | 3.6 | 5.5 |
| vgadonut | 2.0 | 1.6 | 21.0 | 0.8 | 1.3 | 1.6 | 6.1 |
| cfib_demo | 0.3 | 0.4 | 24.1 | 0.1 | 0.2 | 0.3 | 5.9 |
| nyan | 0.7 | 0.6 | 21.9 | 0.1 | 0.2 | 0.2 | 5.2 |
| metaballs | 0.0 | 0.0 | 20.6 | 0.0 | 0.0 | 0.0 | 5.3 |
| tomkeddie_a | 0.0 | 0.1 | 21.0 | 0.0 | 0.1 | 0.1 | 5.1 |

#### With a header-only tracetool info (no scan; the job reads only blocks, clocks and open.slice_*), background at its lowest: the trace's share of R2, %

| project | vgch2 (zstd 1) | events (zstd 1) | raw xor6 (zstd 1) | planar (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 9.5 | 9.3 | 28.8 | 3.5 | 5.9 | 6.8 | 10.8 |
| rle_vga | 9.1 | 13.8 | 18.8 | 0.5 | 5.3 | 5.6 | 10.4 |
| dvd_screensaver | 0.0 | 0.1 | 21.5 | 0.0 | 0.0 | 0.1 | 4.0 |
| colorbars | 0.2 | 0.3 | 17.6 | 0.1 | 0.2 | 0.2 | 3.7 |
| mandelbrot | 0.1 | 0.0 | 13.8 | 0.0 | 0.0 | 0.0 | 2.4 |
| atari2600 | 5.3 | 5.5 | 13.8 | 0.7 | 2.4 | 3.1 | 4.0 |
| vgadonut | 1.9 | 1.6 | 19.3 | 0.8 | 1.3 | 1.5 | 4.1 |
| cfib_demo | 0.3 | 0.4 | 21.6 | 0.1 | 0.2 | 0.3 | 3.8 |
| nyan | 0.7 | 0.5 | 21.9 | 0.1 | 0.2 | 0.2 | 4.9 |
| metaballs | 0.0 | 0.0 | 20.5 | 0.0 | 0.0 | 0.0 | 4.9 |
| tomkeddie_a | 0.0 | 0.1 | 20.5 | 0.0 | 0.1 | 0.1 | 3.3 |

#### With a header-only tracetool info (no scan; the job reads only blocks, clocks and open.slice_*): the reads alone, s for the whole trace

| project | vgch2 (zstd 1) | events (zstd 1) | raw xor6 (zstd 1) | planar (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 19.0 | 31.7 | 64.2 | 10.3 | 9.6 | 9.2 | 9.2 |
| rle_vga | 34.4 | 58.1 | 52.4 | 0.0 | 0.0 | 0.0 | 0.0 |
| dvd_screensaver | 0.1 | 0.2 | 39.9 | 0.1 | 0.1 | 0.1 | 0.1 |
| colorbars | 0.5 | 1.1 | 35.9 | 0.2 | 0.2 | 0.2 | 0.2 |
| mandelbrot | 0.6 | 0.7 | 139.0 | 0.3 | 0.4 | 0.3 | 0.4 |
| atari2600 | 19.7 | 34.2 | 66.8 | 3.2 | 3.4 | 3.2 | 3.3 |
| vgadonut | 7.3 | 10.5 | 82.5 | 4.5 | 4.6 | 4.6 | 4.6 |
| cfib_demo | 1.0 | 1.8 | 73.7 | 0.6 | 0.6 | 0.6 | 0.6 |
| nyan | 1.0 | 1.3 | 38.2 | 0.1 | 0.1 | 0.1 | 0.2 |
| metaballs | 0.1 | 0.2 | 70.6 | 0.1 | 0.1 | 0.1 | 0.1 |
| tomkeddie_a | 0.1 | 0.2 | 39.5 | 0.1 | 0.1 | 0.1 | 0.1 |

### Every plan against R1 and R2 together

R1: the simulation thread's cost in the loop (median of the processes; vgch2 and lean from fix round 2's lean set, the background plans from its background set, raw from its own processes), % of the untraced model; the spec's budget is 5%. R2: the trace's share, background at encode2's median, with tracetool info as a scan that counts planar streams on their own, and with a header-only info (no scan). The spec's R2 budget is 5% for everything after the simulation; the 1% bar for the trace's own share is this study's choice, not the spec's. Lean writes vgch2's file, so its R2 is vgch2's.

| plan | nyancat R1 (%) | nyancat R2 (%) | nyancat R2 no scan (%) | rle_vga R1 (%) | rle_vga R2 (%) | rle_vga R2 no scan (%) | colorbars R1 (%) | colorbars R2 (%) | colorbars R2 no scan (%) | atari2600 R1 (%) | atari2600 R2 (%) | atari2600 R2 no scan (%) | vgadonut R1 (%) | vgadonut R2 (%) | vgadonut R2 no scan (%) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| vgch2 (current) | 14.0 | 17.6 | 10.1 | 19.1 | 25.0 | 11.1 | -0.2 | 0.5 | 0.2 | 7.7 | 11.1 | 5.9 | 0.9 | 3.3 | 2.0 |
| lean (vgch2's file) | 14.1 | 17.6 | 10.1 | 17.5 | 25.0 | 11.1 | -0.8 | 0.5 | 0.2 | 7.7 | 11.1 | 5.9 | 1.1 | 3.3 | 2.0 |
| events, stored as events | 8.3 | 19.0 | 9.4 | 7.5 | 32.6 | 14.2 | 3.6 | 0.8 | 0.3 | 3.9 | 12.6 | 5.7 | 0.6 | 3.6 | 1.6 |
| events, stored as planar | 8.8 | 7.4 | 6.3 | 8.1 | 8.2 | 6.6 | 3.3 | 0.2 | 0.2 | 4.1 | 3.3 | 2.7 | 0.5 | 1.5 | 1.3 |
| changelog, stored as planar | 7.8 | 8.8 | 7.8 | 5.4 | 7.9 | 6.5 | 2.7 | 0.3 | 0.3 | 3.1 | 4.1 | 3.6 | 0.7 | 1.8 | 1.6 |
| planar written on the simulation thread | 13.0 | 4.5 | 3.5 | 10.3 | 2.5 | 1.0 | 2.8 | 0.1 | 0.1 | 6.0 | 1.3 | 0.8 | 0.5 | 1.0 | 0.8 |
| raw, stored as planar | 6.0 | 12.4 | 11.3 | 3.0 | 13.7 | 12.4 | 4.8 | 5.6 | 5.5 | 2.6 | 6.0 | 5.5 | 3.2 | 6.3 | 6.1 |

#### Pass or fail, per plan

Busy: nyancat, rle_vga, atari2600. Quiet: colorbars, vgadonut. R2 is checked at the background figure most in its favour (the lowest measured) and at encode2's median; tracetool info counts planar streams on their own, or (no scan) is header-only. The 1% bar is this study's own, not the spec's.

| plan | R1 at most 5%, busy | R1 at most 5%, quiet | R2 at most 1%, busy, lowest | R2 at most 1%, busy, median | R2 at most 5%, busy, lowest | R2 at most 5%, busy, median | R2 no scan at most 1%, busy | R2 no scan at most 5%, busy | R1 and R2 (1%, lowest), busy | R1 and R2 (5%, no scan), busy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| vgch2 (current) | no | yes | no | no | no | no | no | no | no | no |
| lean (vgch2's file) | no | yes | no | no | no | no | no | no | no | no |
| events, stored as events | no | yes | no | no | no | no | no | no | no | no |
| events, stored as planar | no | yes | no | no | no | no | no | no | no | no |
| changelog, stored as planar | no | yes | no | no | no | no | no | no | no | no |
| planar written on the simulation thread | no | yes | no | no | yes | yes | no | yes | no | no |
| raw, stored as planar | no | yes | no | no | no | no | no | no | no | no |

### Truncation and a crash mid-write

Samples a reader recovers, % of the samples the file held (truncation) or of the samples written before the kill.

| project | case | format | cut | bytes kept | reader exit | samples recovered (%) |
|---|---:|---:|---:|---:|---:|---:|
| dvd_screensaver | truncate | vgch2 | 10 | 130654 | 0 | 8.87 |
| dvd_screensaver | truncate | vgch2 | 50 | 653270 | 0 | 49.92 |
| dvd_screensaver | truncate | vgch2 | tail64 | 1306476 | 0 | 100.00 |
| dvd_screensaver | truncate | planar | 10 | 135898 | 0 | 8.87 |
| dvd_screensaver | truncate | planar | 50 | 679490 | 0 | 49.92 |
| dvd_screensaver | truncate | planar | tail64 | 1358917 | 0 | 100.00 |
| dvd_screensaver | truncate | events | 10 | 113170 | 0 | 8.87 |
| dvd_screensaver | truncate | events | 50 | 565853 | 0 | 49.92 |
| dvd_screensaver | truncate | events | tail64 | 1131643 | 0 | 100.00 |
| dvd_screensaver | truncate | raw | 10 | 2184082 | 0 | 9.98 |
| dvd_screensaver | truncate | raw | 50 | 10920412 | 0 | 49.92 |
| dvd_screensaver | truncate | raw | tail64 | 21840760 | 0 | 100.00 |
| dvd_screensaver | truncate | fst | 10 | 287430 | 0 | 0.00 |
| dvd_screensaver | truncate | fst | 50 | 1437150 | 0 | 0.00 |
| dvd_screensaver | truncate | fst | tail64 | 2874237 | 0 | 100.00 |
| dvd_screensaver | kill(exit 137) | vgch2 | half | 646447 | 0 | 99.72 |
| dvd_screensaver | kill(exit 137) | planar | half | 674671 | 0 | 99.72 |
| dvd_screensaver | kill(exit 137) | events | half | 561282 | 0 | 99.72 |
| dvd_screensaver | kill(exit 137) | raw | half | 10930485 | 0 | 100.00 |
| dvd_screensaver | kill(exit 137) | fst | half | 330 | 0 | 0.00 |
| rle_vga | truncate | vgch2 | 10 | 1838873 | 0 | 10.02 |
| rle_vga | truncate | vgch2 | 50 | 9194369 | 0 | 50.10 |
| rle_vga | truncate | vgch2 | tail64 | 18388674 | 0 | 100.00 |
| rle_vga | truncate | planar | 10 | 61978 | 0 | 9.85 |
| rle_vga | truncate | planar | 50 | 309894 | 0 | 50.43 |
| rle_vga | truncate | planar | tail64 | 619725 | 0 | 100.00 |
| rle_vga | truncate | events | 10 | 107517 | 0 | 10.04 |
| rle_vga | truncate | events | 50 | 537587 | 0 | 50.21 |
| rle_vga | truncate | events | tail64 | 1075111 | 0 | 100.00 |
| rle_vga | truncate | raw | 10 | 1315401 | 0 | 9.89 |
| rle_vga | truncate | raw | 50 | 6577006 | 0 | 49.90 |
| rle_vga | truncate | raw | tail64 | 13153948 | 0 | 100.00 |
| rle_vga | truncate | fst | 10 | 3950330 | 139 | unreadable |
| rle_vga | truncate | fst | 50 | 19751652 | 139 | unreadable |
| rle_vga | truncate | fst | tail64 | 39503241 | 0 | 100.00 |
| rle_vga | kill(exit 137) | vgch2 | half | 9170322 | 0 | 99.94 |
| rle_vga | kill(exit 137) | planar | half | 306116 | 0 | 99.61 |
| rle_vga | kill(exit 137) | events | half | 533713 | 0 | 99.95 |
| rle_vga | kill(exit 137) | raw | half | 6581057 | 0 | 100.00 |
| rle_vga | kill(exit 137) | fst | half | 19636176 | 0 | 99.33 |
| colorbars | truncate | vgch2 | 10 | 55603 | 0 | 11.09 |
| colorbars | truncate | vgch2 | 50 | 278015 | 0 | 51.03 |
| colorbars | truncate | vgch2 | tail64 | 555966 | 0 | 100.00 |
| colorbars | truncate | planar | 10 | 67284 | 0 | 9.98 |
| colorbars | truncate | planar | 50 | 336422 | 0 | 49.92 |
| colorbars | truncate | planar | tail64 | 672781 | 0 | 100.00 |
| colorbars | truncate | events | 10 | 20230 | 0 | 9.98 |
| colorbars | truncate | events | 50 | 101152 | 0 | 51.03 |
| colorbars | truncate | events | tail64 | 202240 | 0 | 100.00 |
| colorbars | truncate | raw | 10 | 549848 | 0 | 8.87 |
| colorbars | truncate | raw | 50 | 2749243 | 0 | 48.95 |
| colorbars | truncate | raw | tail64 | 5498422 | 0 | 100.00 |
| colorbars | truncate | fst | 10 | 204501 | 0 | 0.00 |
| colorbars | truncate | fst | 50 | 1022506 | 139 | unreadable |
| colorbars | truncate | fst | tail64 | 2044948 | 0 | 100.00 |
| colorbars | kill(exit 137) | vgch2 | half | 267043 | 0 | 99.72 |
| colorbars | kill(exit 137) | planar | half | 334986 | 0 | 99.72 |
| colorbars | kill(exit 137) | events | half | 98157 | 0 | 99.72 |
| colorbars | kill(exit 137) | raw | half | 2802799 | 0 | 100.00 |
| colorbars | kill(exit 137) | fst | half | 785912 | 0 | 76.82 |

### Interop: existing tools on each file

Exit 0 and a byte count: the tool read the whole file (its output's size). fst2vcd and lxt2vcd are GTKWave's own readers; zstd -d is the spec's reader without seeking.

| project | file | tool | exit | bytes out |
|---|---:|---:|---:|---:|
| rle_vga | _trace.vgch.zst | zstd | 0 | 20,182,479,796 |
| rle_vga | fst.lz4.fst | fst2vcd | 0 | 80,239,812,850 |
| rle_vga | fst_clk.lz4.fst | fst2vcd | 0 | 5,359,973,761 |
| rle_vga | lxt2.gzip.lxt2 | lxt2vcd | 0 | 4,637,725,146 |
| rle_vga | vcd candidate (whole trace) | fsbench | 0 | 80,239,812,771 |
| rle_vga | planar.identity.zstd1.stdy | zstd | 1 | 0 |
| rle_vga | events.identity.zstd1.stdy | zstd | 1 | 0 |
| rle_vga | raw.planar-runs.zstd1.stdy | zstd | 1 | 0 |
| rle_vga | raw.xor6.zstd1.stdy | zstd | 1 | 0 |
| colorbars | _trace.vgch.zst | zstd | 0 | 354,711,342 |
| colorbars | fst.lz4.fst | fst2vcd | 0 | 1,445,756,933 |
| colorbars | fst_clk.lz4.fst | fst2vcd | 0 | 3,419,841,748 |
| colorbars | lxt2.gzip.lxt2 | lxt2vcd | 0 | 80,157,260 |
| colorbars | vcd candidate (whole trace) | fsbench | 0 | 1,445,756,854 |
| colorbars | planar.identity.zstd1.stdy | zstd | 1 | 0 |
| colorbars | events.identity.zstd1.stdy | zstd | 1 | 0 |
| colorbars | raw.planar-runs.zstd1.stdy | zstd | 1 | 0 |
| colorbars | raw.xor6.zstd1.stdy | zstd | 1 | 0 |

### VGCH v2's container: block length and checksum

#### Size against block length (zstd 1), MB

| project | 2^18 clocks | 2^20 clocks | 2^22 clocks | 2^24 clocks | 2^26 clocks |
|---|---:|---:|---:|---:|---:|
| nyancat | 415.30 | 416.16 | 416.36 | 416.36 | 416.36 |
| rle_vga | 19.41 | 19.17 | 18.35 | 18.35 | 18.35 |
| dvd_screensaver | 3.12 | 2.09 | 1.48 | 1.31 | 1.26 |
| colorbars | 21.89 | 11.91 | 2.09 | 0.55 | 0.17 |
| mandelbrot | 27.17 | 24.93 | 17.76 | 14.71 | 13.92 |
| atari2600 | 602.25 | 597.79 | 596.84 | 596.84 | 596.84 |
| vgadonut | 168.29 | 167.41 | 167.22 | 167.32 | 167.26 |
| cfib_demo | 32.46 | 24.48 | 21.39 | 20.59 | 20.39 |
| nyan | 54.32 | 52.77 | 52.06 | 51.78 | 51.71 |
| metaballs | 2.09 | 1.43 | 1.32 | 1.33 | 1.33 |
| tomkeddie_a | 1.59 | 0.67 | 0.22 | 0.11 | 0.09 |

#### Cost of reading one block (decompress with checksum + decode the pairs), ms, mean over blocks

| project | 2^18 clocks | 2^20 clocks | 2^22 clocks | 2^24 clocks | 2^26 clocks |
|---|---:|---:|---:|---:|---:|
| nyancat | 4.79 | 11.77 | 39.72 | 34.16 | 33.51 |
| rle_vga | 6.39 | 28.19 | 29.09 | 29.70 | 27.90 |
| dvd_screensaver | 0.02 | 0.08 | 0.27 | 1.05 | 4.30 |
| colorbars | 0.18 | 0.59 | 1.73 | 6.52 | 29.19 |
| mandelbrot | 0.03 | 0.08 | 0.24 | 0.87 | 3.54 |
| atari2600 | 3.10 | 12.88 | 33.48 | 33.83 | 32.53 |
| vgadonut | 0.75 | 2.45 | 8.77 | 32.40 | 34.59 |
| cfib_demo | 0.08 | 0.27 | 1.04 | 4.21 | 18.65 |
| nyan | 0.25 | 0.94 | 2.36 | 7.49 | 29.97 |
| metaballs | 0.01 | 0.03 | 0.09 | 0.38 | 1.58 |
| tomkeddie_a | 0.02 | 0.06 | 0.21 | 0.84 | 3.58 |

#### The XXH64 checksum at 2^24 clocks: decompression time with it checked and ignored, s, whole trace

| project | decompress, checked (s) | decompress, ignored (s) | checksum share (%) | compress with (s) | compress without (s) | bytes the checksums add | decode the pairs (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 9.12 | 8.24 | 9.6 | 19.62 | 18.30 | 1956 | 7.59 |
| rle_vga | 16.26 | 14.01 | 13.8 | 6.83 | 3.70 | 4832 | 19.62 |
| dvd_screensaver | 0.04 | 0.03 | 25.1 | 0.05 | 0.05 | 364 | 0.05 |
| colorbars | 0.14 | 0.06 | 54.2 | 0.13 | 0.09 | 364 | 0.46 |
| mandelbrot | 0.16 | 0.13 | 15.3 | 0.47 | 0.44 | 1436 | 0.16 |
| atari2600 | 10.15 | 9.09 | 10.4 | 21.14 | 19.58 | 2276 | 9.10 |
| vgadonut | 3.32 | 2.98 | 10.1 | 7.01 | 6.49 | 780 | 3.00 |
| cfib_demo | 0.30 | 0.25 | 18.0 | 0.63 | 0.57 | 720 | 0.46 |
| nyan | 0.45 | 0.42 | 6.1 | 1.24 | 1.17 | 360 | 0.22 |
| metaballs | 0.02 | 0.02 | 27.2 | 0.06 | 0.06 | 720 | 0.05 |
| tomkeddie_a | 0.01 | 0.00 | 66.9 | 0.03 | 0.02 | 364 | 0.06 |

### Host load during each stage

From `load.log` (`/proc/loadavg` and `vmstat` every 30 s while a stage or a loop set runs) and `progress.log` (each stage's start and end). Load per CPU is the 1-minute load over 88; idle is vmstat's. Other sessions shared the host throughout; nothing here was run on an idle machine.

| stage | start (UTC) | end | samples | load per CPU, min | median | max | idle %, min | median |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| info | 06:26 | 06:28 | 3 | 1.73 | 2.06 | 2.08 | 1 | 2 |
| writer | 06:28 | 06:56 | 56 | 0.55 | 1.19 | 2.28 | 1 | 28 |
| writer2 | 06:44 | 06:45 | 5 | 0.83 | 0.91 | 1.00 | 22 | 34 |
| writer2 | 06:52 | 06:53 | 4 | 0.85 | 0.96 | 1.06 | 46 | 53 |
| encode | 06:56 | 07:12 | 30 | 0.56 | 1.39 | 1.66 | 5 | 26 |
| writer2 | 07:19 | 07:21 | 5 | 0.58 | 0.58 | 0.60 | 37 | 45 |
| interop | 07:31 | 07:47 | 81 | 0.53 | 0.71 | 1.24 | 4 | 46 |
| ctr | 07:37 | 07:47 | 58 | 0.59 | 0.72 | 1.16 | 4 | 44 |
| robust | 07:37 | 07:59 | 103 | 0.39 | 0.69 | 1.16 | 4 | 49 |
| validate | 08:29 | 08:33 | 8 | 0.91 | 1.11 | 1.45 | 50 | 54 |
| lean | 09:05 | 09:09 | 6 | 0.98 | 1.19 | 1.52 | 37 | 58 |
| loop fx1a+fx1b | 09:18 | 09:33 | 25 | 0.93 | 1.12 | 1.75 | 24 | 33 |
| encode2 | 09:18 | 09:31 | 18 | 0.93 | 1.09 | 1.45 | 24 | 30 |
| read2 | 09:31 | 10:30 | 195 | 0.66 | 1.46 | 2.43 | 9 | 47 |
| loop fx1c+fx1d | 09:35 | 09:49 | 55 | 0.93 | 1.19 | 1.80 | 9 | 44 |
| loop fx1ra+fx1rb | 09:50 | 09:53 | 11 | 1.27 | 1.80 | 2.09 | 24 | 43 |
| loop fx1rc+fx1rd | 09:55 | 09:59 | 16 | 1.46 | 2.25 | 2.43 | 10 | 37 |
| loop fx1s | 10:01 | 10:14 | 50 | 1.22 | 1.81 | 2.07 | 11 | 48 |
| loop fx2a+fx2b | 02:07 | 02:19 | 24 | 0.99 | 1.42 | 2.30 | 3 | 33 |
| loop fx2c+fx2d | 02:21 | 02:33 | 23 | 1.01 | 1.39 | 1.97 | 12 | 38 |
| loop fx2s | 02:36 | 02:46 | 20 | 0.72 | 1.28 | 1.85 | 18 | 48 |
| loop fx2la+fx2lb | 10:18 | 10:22 | 8 | 0.45 | 0.78 | 1.34 | 45 | 63 |
| loop fx2lc+fx2ld | 10:24 | 10:28 | 8 | 0.50 | 1.07 | 1.25 | 43 | 66 |

### Implementation size

| code | non-comment lines |
|---|---:|
| vgch2 writer (vgacap src/trace/trace_write.c, blocks.c, trace.h; stream writer) | 633 |
| vgch2 reader (vgacap src/trace/trace_read.c; stream reader) | 1082 |
| the study writers: planar, events, runs, changelog, raw (study src/w_mine.c, w_mine.h) | 553 |
| study container writer + transforms (study src/bg.c, codec.c) | 667 |
| the study readers: planar, events, raw, and vgch2 and FST drivers (study src/rd.c) | 726 |

Spec text, words: VGCH v2 trace 4056 (docs/trace-format.md + src/stream/reference-stream-format.md).

#### What a planar payload changes (R9): the code that reads or writes RUNS today

Each file's non-comment lines, whole: the code to review, not the lines that change. The published traces are re-encoded as well (the re-encode table, under the encode2 tables).

| repository | file | non-comment lines |
|---|---:|---:|
| vgacap | `src/trace/trace_write.c` | 282 |
| vgacap | `src/trace/trace_read.c` | 607 |
| vgacap | `src/trace/blocks.c` | 88 |
| vgacap | `src/stream/reader.c` | 475 |
| vgacap | `src/stream/writer.c` | 159 |
| vgacap | `src/tools/vgacap_dump.c` | 254 |
| vgacap | `python/vgacap/stream.py` | 370 |
| vgacap | `python/vgacap/synthtrace.py` | 155 |
| vgacap | `tests/test_trace_read.c` | 1104 |
| vgacap | `tests/test_trace_write.c` | 861 |
| vgacap | `tests/test_stream_v2.c` | 739 |
| vgacap | `python/tests/test_stream_v2.py` | 304 |
| vgacap | `python/tests/test_roundtrip_c.py` | 76 |
| tinytapeout-vga-videos | `ttvga/harness/tb.cpp` | 699 |
| tinytapeout-vga-videos | `ttvga/harness/tracetool.c` | 306 |
| tinytapeout-vga-videos | `tests/sim/vgch.py` | 50 |
| tinytapeout-vga-videos | `scripts/export_tbv1_vectors.py` | 104 |
| all | 17 files | 6,633 |

<!-- analyze.py tables: end -->

## VGCH v2's own design choices: what each costs and buys

Each of VGCH v2's choices is judged the way the candidates are, by what it does in use. The numbers are from the [Results](#results) tables.

| Choice | Verdict |
|---|---|
| One run stream of whole 43-bit values (`RUNS`) | wrong for busy designs |
| Packing to 43 bits on the simulation thread | not needed there |
| The clock bit regenerated from the sample's parity | keep |
| Run lengths unbounded (LEB128) | keep |
| vgacap's push API (`push2` as two pushes) | a small part of the cost |
| zstd level 1 | choose per measured set |
| An XXH64 checksum in every frame | keep |
| Blocks of 2^24 clocks, or 16 MiB | keep |
| `BLKH` at every block's start, read at open | keep, read lazily |
| `META` (open and close) | keep |
| zstd's seekable format | keep |
| Runs split at block ends | keep |
| The double-buffered compressor thread | keep |
| One chunk shared with the boards | should not decide the simulation format |

What each costs and buys:

- **One run stream of whole 43-bit values** (`RUNS`: a 6-byte value, then a LEB128 run).
  - Cost: every change rewrites all 7 ports' bytes. On the busy traces the file is 7.7 (nyancat), 66 (atari2600) and 30 (rle_vga) times planar's.
  - Cost: the bytes to compress are 2.7 (nyancat) to 3.5 (rle_vga) times planar's: 8.1 against 3.0 GB, and 20.2 against 5.8 GB.
  - Buys: the fastest merged scan of every port, 1.4-1.8 times planar's on the busy traces (`tracetool info` as it is: 28.3 against 49.6 s on nyancat). One stream, so the simplest reader.
  - It is right only if a reader needs every pin, every time.
- **Packing to 43 bits on the simulation thread** (tb.cpp's `pack_raw` on each changed clock).
  - Cost: one gather of 8 bytes per changed sample, inside the measured slow path.
  - Buys: values independent of Verilator's port layout. Any candidate gets that by recording the layout once in the header. The changelog and raw candidates pack in the background.
- **The clock bit regenerated from the sample's parity.**
  - Cost: a rule in the spec and the reader.
  - Buys: runs at all. Without it no two samples are equal: FST-with-clk, which records `clk`, costs 230-310 ns a clock even on quiet designs (replay).
- **Run lengths unbounded** (LEB128 up to 64 bits): 1-10 bytes a run, for R3 (a run longer than 2^32 samples).
- **vgacap's push API** (`push2` as two pushes, each through `vgacap_trace_slow` and `slow_rare`).
  - The lean writer replaces it with one call a changed clock and writes the same file. In the loop (lean over vgch2: 0.99 [0.93, 1.50] on nyancat, 0.91 [0.88, 0.92] on rle_vga and 0.97 [0.95, 1.03] on atari2600) it saves 9% on rle_vga; on nyancat and atari2600 the saving is within the processes' spread. On the two quiet ones the ratio (0.80, 1.01) is a per-variant offset of about 1-2 ns a clock, within the noise.
  - `vgacap_trace_block_pair` is `static inline` but compiled out of line, so every pair is a call and a switch. Forcing it inline in the lean writer gave 0.88 (nyancat) and 0.77 (rle_vga) of vgch2 in one replay run (the format owner's finding): a direction, not a loop figure.
  - So the push path is a small part of vgch2's cost. The first round's estimate, about half, was wrong.
- **zstd level 1.**
  - On `RUNS` data, level 3 is smaller than level 1 on nyancat (288 against 413 MB) for 12% less CPU (17.3 against 19.6 s), and on atari2600 (418 against 595 MB) for about the same CPU (20.9 against 21.1 s).
  - On rle_vga's `RUNS` data, level 3 is larger and slower: 26.8 MB in 10.9 s, against 18.4 MB in 6.8 s. It is also larger on colorbars: 0.64 against 0.55 MB.
  - On planar data, level 3 halves atari2600 (4.26 against 9.04 MB) for 0.5 s more CPU (2.34 against 1.82 s), and is within 10% of level 1 on nyancat and rle_vga.
  - Level −1 is 43% larger on nyancat and only 5% faster. The long window (2^27) gives 92 MB on atari2600 against 595, for 31% more CPU.
  - The level is chosen by the writer alone; readers do not depend on it. So it can be chosen per measured set, with no format change.
- **An XXH64 checksum in every frame.**
  - Cost, compressing: 7% of the compressor's CPU on nyancat (19.6 against 18.3 s) and 46% on rle_vga (6.8 against 3.7 s), in the first round's single measurement.
  - Cost, decompressing: 10-14% on busy traces, more on quiet ones where little else is done. 4 bytes a block.
  - Buys: corruption is detected. The truncated-trace rule leans on it: the reader drops a last frame whose checksum fails, so a frame cut so that it still looks whole is not kept. No test of this study corrupts a file, though (Method, Robustness).
- **Blocks of 2^24 clocks, or 16 MiB.**
  - Cost: a seek decompresses a whole block, 30-34 ms on busy traces and 0.4-7.5 ms on the others. A killed run loses up to 2 blocks (R6), each up to 0.73 s of simulated time.
  - Buys: size on quiet designs. Colorbars is 0.55 MB at 2^24 clocks, against 11.9 MB at 2^20 and 21.9 MB at 2^18, because zstd frames cannot refer to each other. On busy traces the 16 MiB limit ends the blocks first, so block length changes nothing (nyancat 415-416 MB at every length).
  - 2^26 would shrink colorbars to 0.17 MB, for 4.5 times the cost of a seek.
- **`BLKH` at every block's start, read at open.**
  - Cost: vgacap's open decompresses the start of every block: 111 ms on nyancat (489 blocks), 180 ms on atari2600 and 242 ms on rle_vga (1,208 blocks). A one-frame seek is mostly this open.
  - Buys: a block names its own position, so a stream reader that reads the frames in order needs nothing else.
  - Reading the starts lazily, only as a seek's binary search needs them, cuts the open to 29 ms on nyancat and 55 ms on atari2600, and a one-frame seek from 129 to 51 ms on nyancat. That is a reader change, with no format change.
  - Putting each block's first sample in the index would make the open cheaper still, but zstd's seek table has no field for it. That needs a skippable frame or chunk of its own: a container change.
- **`META` (open and close).**
  - Cost: under 1 KB a trace.
  - Buys: the status, and a self-describing file. A trace with no `META(close)` reads as truncated, so a cut trace cannot pass as complete.
- **zstd's seekable format** (a seek table in a skippable frame).
  - Cost: 12 bytes a block. A reader has to rebuild the table if it is missing or does not agree with the frames, and that is a whole set of rules in the spec.
  - Buys: plain `zstd -d` gives the whole stream. The interop table shows 20.2 GB of rle_vga and 355 MB of colorbars from `zstd -d`.
  - Buys: truncation is handled. A 50% cut keeps every whole block (robustness table).
  - Its value does not depend on the payload. With planar streams, though, a reader that streams in time order must hold all 7 streams of a block before it can give the block's first run.
- **Runs split at block ends**: one extra pair a block, and every block decodes on its own.
- **The double-buffered compressor thread.**
  - Cost: two 16 MiB buffers, and the simulation waits if the thread falls behind.
  - Measured: the simulation thread's wall time over its CPU time, against the untraced model's, is at most about 3 ns a clock for vgch2 on the busy designs. The study writers' hand-overs never waited. So no stall of note was seen.
  - Buys: compression off the simulation thread.
- **One chunk shared with the boards** (the format note's argument for `RUNS`).
  - It ties the simulation trace's layout to an 8-bit board capture, where one value is one port.
  - It buys nothing yet: no board writes `RUNS`.

In short, the container is sound. The seekable zstd frames, the per-block start, `META`, the checksums and the block length all hold up under measurement. The payload is the problem. The open's scan of every block can be made lazy without any format change. The push path costs little.

**Complexity and cost of change (R9).** [Implementation size](#implementation-size) counts non-comment lines of each writer and reader. The VGCH v2 spec ([`trace-format.md`](../trace-format.md) and [`reference-stream-format.md`](../../src/stream/reference-stream-format.md)) is 4,056 words. The format note lists:

- 17 reader fault cases for the three new chunks (its "Reader rules" table);
- 8 invalid-header cases;
- 6 rules for a trace cut short.

Most of those belong to the container (framing, `BLKH`, `META`, truncation).

- A planar payload replaces the `RUNS` rules: no adjacent-equal values in a stream, LEB128 minimal and non-zero, and runs that fill the block exactly.
- It adds one rule: one stream per port, each ending at the block's end.
- The study's prototypes are not production code. Their line counts bound how much code each layout needs, but they lack vgacap's error reporting.

Changing the payload also has a cost of its own, which counts against it under R9:

- **Code.** Every reader and writer of `RUNS` changes in the same change, so that only one layout is ever stored. [What a planar payload changes](#what-a-planar-payload-changes-r9-the-code-that-reads-or-writes-runs-today) lists the files, in vgacap and tinytapeout-vga-videos, and their sizes. The list includes:
  - the C and Python readers and writers;
  - `vgacap-dump`;
  - `tracetool` and tb.cpp;
  - the tests that hold `RUNS` bytes;
  - the export script.
- **Data.** Every published trace is re-encoded. The replay and the writers exist, and the canonical-run hashes check each result. The [re-encode table](#re-encoding-a-published-trace-to-planar-encode2s-planar-pass-wall-s-median-of-3) gives 18-86 s a trace on one CPU.
- **Docs.** The format note and the spec change with it.

## Pros and cons per candidate

Every item rests on a table and a number, or on a concrete property.

- **Busy** means nyancat, rle_vga and atari2600: 77-100% of their clocks change a pin.
- **Simulation-thread** figures are the loop's medians of 4 processes, unless they say "replay": vgch2 and lean from fix round 2's lean set, the background plans from its background set, raw from processes of its own.
- **R2 share** is the trace's background CPU plus every read a job makes, as a share of the simulation CPU, with `tracetool info` counting planar streams on their own and the background at encode2's median ([R2 tables](#the-traces-share-of-r2-background-cpu-and-every-read-of-a-job)).

### vgch2 (current)

Pros:

- The fastest merged scan of every port: `tracetool info` as it is takes 28.3 s on nyancat, against planar's 49.6.
- One stream with one pair per run: the simplest reader to write against the spec.
- Plain `zstd -d` gives the whole stream (R8, interop table). The container is zstd's own seekable format.
- A truncated or killed trace keeps every finished block. A 50% cut kept 49.9% of the samples, and a kill kept 99.7-99.9% of what was written (robustness table).
- Smaller than planar on the three quietest traces: tomkeddie_a 0.11 MB against 0.16, colorbars 0.56 against 0.67, dvd_screensaver 1.31 against 1.36. Events is smaller still on all three.

Cons:

- The most expensive on the simulation thread: 14.0% of the simulation on nyancat, 19.1% on rle_vga and 7.7% on atari2600.
- Its R2 share is 17.6% (nyancat), 25.0% (rle_vga) and 11.1% (atari2600): its compressor plus three full decodes a job.
- The largest file on every busy design: 416 MB nyancat, 597 MB atari2600, 18.4 MB rle_vga. Planar is 54, 9.0 and 0.62 MB (Total bytes table).
- Opening reads the start of every block: 111 ms on nyancat and 242 ms on rle_vga. Lazy reading of the starts fixes most of that without a format change.
- `vgacap-sample`'s pass decodes every run: 18.3 s on nyancat and 33.0 s on rle_vga, against planar's 10.1 and 0.04.

### lean (vgch2's file, one call a changed clock)

Pros:

- The same file as vgch2, byte for byte, on all 11 traces.
- No format change.

Cons:

- It saves 9% on rle_vga (17.5% against 19.1%). On nyancat (14.1% against 14.0%) and atari2600 (7.7% against 7.7%) the saving is within the processes' spread. On colorbars and vgadonut its ratio to vgch2 is an offset within the noise.
- Everything vgch2 costs in size, background CPU and reading.

### planar (a run stream per port, written on the simulation thread)

Pros:

- The smallest file, or tied with the other planar-stored candidates, on 8 of the 11 traces. It is 7.7-100 times smaller than vgch2 on the busy ones and on nyan (Total bytes table).
- The lowest R2 share of all: 4.5% (nyancat), 2.5% (rle_vga), 1.3% (atari2600). The only plan within the whole 5% post-simulation budget on all three busy designs.
- `uo_out` alone decodes only its own stream. `vgacap-sample`'s pass, which also reads `rst_n`, takes 10.1 s on nyancat and 0.04 s on rle_vga.
- Opens in about 1 ms on every trace.
- No converter: the background thread only compresses (2.9-4.4 s on the busy traces, encode2 median).

Cons:

- 13.0% (nyancat), 10.3% (rle_vga) and 6.0% (atari2600) of the simulation: over R1 on all three busy designs.
- A merged scan of every port is 1.4-1.8 times vgch2's on the busy traces, because seven streams are merged. With today's merged `tracetool info` planar's R2 gain is mostly lost (15.7% against vgch2's 17.6% on nyancat); a header-only `tracetool info` removes the question (3.5% against 10.1%).
- A full-pin FST export costs 12% more CPU than from vgch2's file on rle_vga (579 against 516 s), and the same on nyancat and atari2600.
- Seven streams a block, where `RUNS` has one chunk. Measured as seven zstd frames a block, which is a change of VGCH v2's container.
- Its buffers: 7 streams × 16 MiB × 2 allocated on the simulation side (the memory table gives what is resident).

### events (one time-ordered change stream)

Pros:

- On the simulation thread, close to changelog: 8.3% (nyancat), 7.5% (rle_vga), 3.9% (atari2600).
- Stored as is, the smallest on the quiet traces: 0.20 MB colorbars, 0.09 MB tomkeddie_a.

Cons:

- Over R1 on nyancat (8.3%) and rle_vga (7.5%), and dearer than changelog on all three busy designs.
- Stored as is, its R2 share is the highest of the run formats: 19.0% (nyancat), 32.6% (rle_vga), because every read decodes every event.
- Stored as is, it is 2.9 times planar on nyancat (154 against 54 MB) and 25 times on atari2600 (229 against 9.0).

### events, stored as planar (evplanar)

Pros:

- Planar's file: 53.8 MB nyancat, 0.66 MB rle_vga, 9.4 MB atari2600.
- Its simulation-thread cost is events' plus the hand-over: 8.8% (nyancat), 8.1% (rle_vga), 4.1% (atari2600).

Cons:

- Over R1 on nyancat and rle_vga.
- Its R2 share is 7.4% (nyancat), 8.2% (rle_vga) and 3.3% (atari2600): over this study's 1% bar on all three, and over the whole 5% on two.
- The converter, written plainly, costs 16-30 s of background CPU on the busy traces (encode2 median), against 2.9-4.4 s for compressing planar's streams.

### changelog (raw compare; a changed clock stored unencoded; planar written in the background)

Pros:

- The cheapest raw-compare writer on all three busy designs: 7.8% (nyancat), 5.4% (rle_vga), 3.1% (atari2600), against events' 8.3%, 7.5% and 3.9%.
- Its writer does the least on the simulation thread: three 8-byte stores a changed clock, and no encoding.
- Stores planar's file, with all of planar's size and reads.

Cons:

- Over R1 on nyancat and rle_vga, though rle_vga's lowest process is 4.6%.
- Its records are 24 bytes a changed clock, so converting them costs more than converting events: R2 8.8% (nyancat) and 7.9% (rle_vga).
- 2 × 64 MiB of records, and 8 × 64 MiB of converter scratch (Memory).

### raw (both raw images of every clock)

Pros:

- The cheapest capture on rle_vga (3.0%) and atari2600 (2.6%). On rle_vga it is the only capture under R1.
- No encoder on the simulation thread, so no corner cases there.

Cons:

- 6.0% on nyancat, over R1, and 4.8% on colorbars, where events costs 1.8%. Its cost is the same whatever the design does.
- In processes of its own it cost more on nyancat than the first round's shared processes suggested (3.6%).
- Its sets ran at about twice the host's normal load (1.8 and 2.3 a CPU, median), so its figures may be high.
- Its background thread reads 16 bytes a clock. Converted to planar, its R2 share is 12.4% (nyancat), 13.7% (rle_vga), and 3.9-6.3% even on the quiet traces. Stored as `xor6`, it is 23-48%.
- Stored as `xor6`, it is 10-280 times vgch2 on quiet traces (30.7 MB tomkeddie_a against 0.11).
- 2^21-clock blocks of 32 MiB, double-buffered: 64 MiB a writer.

### sigrok-like (`.sr`)

Pros:

- PulseView opens it natively.

Cons:

- Deflating at level 6 takes 34-73% of the simulation CPU (Background CPU table).
- Colorbars is 162 MB, 290 times vgch2.
- The simulation-thread cost is raw's.

### FST

Pros:

- GTKWave and Surfer read it with no export. `fst2vcd` reads the study's files (interop table).
- Smaller than vgch2 on the busiest traces: 212 MB (LZ4) or 161 MB (zlib) on nyancat, against 416.

Cons:

- Written directly, it costs 4.05 (nyancat), 5.10 (rle_vga) and 4.14 (atari2600) times vgch2, within the same replay run.
- Its compression thread used 28-73% of the simulation CPU on busy traces (LZ4).
- A one-frame seek takes 0.6-1.0 s. A full scan takes 110 ns a clock on atari2600.
- Recording `clk` as a signal (FST-with-clk) costs 234-270 ns a clock on every design (replay).
- A truncated file (10% or 50%) gave no samples or crashed libfst's reader (exit 139). A killed writer left 0% (dvd_screensaver), 77% (colorbars) and 99% (rle_vga) of its samples readable (robustness table).

### LXT2 and VCD

Pros:

- GTKWave reads both natively, and Surfer reads VCD. `lxt2vcd` read the study's LXT2 file (interop table).
- VCD is text: everything before a cut still parses.

Cons:

- LXT2 costs 36-42 times vgch2 on the busy designs (same replay run), and is 13 GB for rle_vga.
- VCD costs 2.9-4.0 times vgch2 on the busy designs (same replay run), and is 3.3 GB at zstd 1 for nyancat. With no index, a seek parses from the start.

## Recommendation

**The recommendation changed in fix round 1, and round 2 refines it.** The first round recommended three things: record with the events writer, store planar in VGCH v2's container, and adopt the lean writer now for about half the cost. Round 1 measured them, and two of the three did not hold:

- the lean writer saves 6-10% on the busy designs, not half (round 2's sets, with every file read back, put it at 9% on rle_vga and within the noise on nyancat and atari2600);
- events stored as planar fails R2. It is over the whole 5% budget on nyancat and rle_vga even at its lowest background figure, and it still misses R1 there.

Round 2 re-measured every plan in the loop with files that read back right. The background plans' R1 figures moved by at most 2.5 points, and no verdict changed. vgch2's and lean's were measured again in a set of their own, and lean's saving came out smaller. It added two findings from the format owner's re-review: the job never uses `tracetool info`'s scan, and `vgacap_trace_block_pair` is not inlined.

**What the data now supports, in order.**

1. **Make `tracetool info` header-only (no format change).**
   - The pipeline reads only `blocks`, `clocks` and `open.slice_*` from it (`job.py`, `derive.py`). Nothing reads the change counts that make it a full scan.
   - Without the scan, vgch2's R2 share falls from 17.6% to 10.1% on nyancat, from 25.0% to 11.1% on rle_vga and from 11.1% to 5.9% on atari2600: 30 s, 63 s and 38 s a job. It is the largest saving found that needs no format change.
   - That change is being made in tinytapeout-vga-videos, outside this study.
2. **Read block starts lazily (no format change).**
   - The open falls from 111 to 29 ms on nyancat, from 180 to 55 ms on atari2600 and from 242 to 97 ms on rle_vga. A one-frame seek falls from 129 to 51 ms on nyancat. vgadonut still opens in 112 ms.
   - It is a change to `trace_read.c` alone. The study's copy is [`trace_read_lazy.c`](../../bench/format-study/src/trace_read_lazy.c).
   - It moves the block-order check from open to read time, and a corrupt `BLKH` can deliver runs before the error. So merge it only with the reader's existing fault-case tests passing on it, and one more: a seek that reads a file with a corrupt mid-file `BLKH`.
   - Three opens a job save about 0.25 s (nyancat) and 0.43 s (rle_vga), against 49-97 s of reads. It is small.
3. **Choose the zstd level per measured set (no format change).**
   - Level 3 is smaller than level 1 on nyancat's and atari2600's `RUNS` data, for no more CPU. On rle_vga's it is larger and slower.
   - Readers do not depend on the level.
4. **The lean writer: small, safe, not urgent.**
   - It writes vgacap's file byte for byte. In the loop it saves 9% on rle_vga, and on nyancat and atari2600 a saving within the processes' spread (0.99 and 0.97 of vgch2).
   - So as it stands it does not meet the format owner's bar for merging, 10% on nyancat and rle_vga. The inline change below is what might.
   - Forcing `vgacap_trace_block_pair` inline as well gave 0.88 (nyancat) and 0.77 (rle_vga) of vgch2 in one replay run (the format owner's `inline-experiment.diff`). That shows the direction, not the size in a simulation.
   - The capture session takes it as its own pull request: lean's slow path plus `block_pair` inline, gated on `test_lean.sh` against all 11 traces, and measured in `bench_tick` against today's writer.
   - The file does not change, so it need not hold the 408-project re-run.
5. **If the format changes, store planar, and change it all at once.**
   - Planar is the only change measured that cuts both the size (7.7-66 times on the busy traces) and the trace's R2 share. Across the three planar-stored plans the share falls to 1.3-8.8% on the busy designs, from vgch2's 11.1-25.0%; without the info scan, to 0.8-7.8% from 5.9-11.1%.
   - It is a new format version, not a variant of VGCH v2:
     - a new header version;
     - every published trace re-encoded, checked by canonical-run hashes; the 408 traces of a full re-run would add 2-10 CPU-hours at 18-86 s each;
     - one stored layout in `trace_read.c`, with no dual `RUNS` and planar trace readers, and no "keep both streams" fallback;
     - every reader and writer listed under R9 updated in the same change.
   - "No dual readers" applies to `trace_read.c` only. VGCH's `version` field belongs to the stream, which board captures share, so the stream reader must still read versions 1 and 2.
   - Today a plain uncompressed VGCH v2 stream is also a valid trace. Planar has no plain form yet; the spec has to say whether it gets one.
   - The container that was measured stores each stream as a zstd frame of its own, seven a block. VGCH v2 stores one frame a block, so this is a container change too.
     - One frame a block, holding all seven streams, was not measured. It would keep the container, but every read would decompress all seven streams.
     - Seven frames a block keep the `uo_out` gain, at the cost of a reader that streams in time order holding a block's seven streams first.
     - The choice between the two has to be measured before the spec is written.
   - Each block's first sample in the index is optional. It is a container change as well, and with (2) it is not needed for speed.
6. **The capture path is not decided by these data.** No measured way of producing planar passes both requirements on the busy designs:

   | Capture | R1 nyancat (%) | R1 rle_vga (%) | R1 atari2600 (%) | R2 nyancat (%) | R2 rle_vga (%) | R2 atari2600 (%) |
   |---|---:|---:|---:|---:|---:|---:|
   | events, converted in the background | 8.8 | 8.1 | 4.1 | 7.4 | 8.2 | 3.3 |
   | changelog, converted in the background | 7.8 | 5.4 | 3.1 | 8.8 | 7.9 | 4.1 |
   | planar on the simulation thread | 13.0 | 10.3 | 6.0 | 4.5 | 2.5 | 1.3 |

   - The converter is the gap. It costs 16-30 s of background CPU on the busy traces, against 2.9-4.4 s for compressing planar's streams written directly.
   - A converter near the compressor's own cost would give the converted plans about planar's R2, while keeping their R1. That is the next thing to measure, not a result.
7. **R1 on the busy designs needs more than a format.**
   - On rle_vga only raw capture is under 5% (3.0%); changelog is next (5.4%). On atari2600 raw (2.6%), changelog (3.1%), events (3.9%) and events to planar (4.1%) are.
   - On nyancat nothing measured is under 5%: raw costs 6.0% and changelog 7.8%.
   - Raw's figures come from sets run at about twice the host's normal load, so they may be high. Raw's background then costs 12-14% of R2 on nyancat and rle_vga.

**What this still misses, stated plainly.**

- No measured plan keeps R1 within 5% on nyancat. On rle_vga only raw capture does.
- No measured plan keeps the trace's R2 share within this study's 1% on any busy design. Only planar on the simulation thread stays within the whole 5%.
- So on the busy designs, every measured plan breaks at least one of the two budgets with the trace alone, before any sampler or hash.
- The background figures behind R2 vary up to 2.5 times with the host's other work. The verdicts hold at both ends of that range.
- R2 leaves out derive's SHA-256 of the trace file, a fourth full read of its bytes.
- The robustness gaps in Method (the double-buffered writer and the converter never killed, no corruption test) were not measured.

**What would change this recommendation.**

- **The samplers come to need every pin, not `uo_out` and `rst_n`.** Then planar's merged scan, 1.4-1.8 times vgch2's on the busy traces, counts in full. A full-pin FST export shows how much: the same CPU from either file on nyancat and atari2600, and 12% more from planar on rle_vga.
- **A converter near the compressor's cost is measured.** Then a capture converted in the background is the one to use.
- **Opening the trace directly in GTKWave or Surfer becomes a must.** FST is the only format both read. It costs 4.1-5.1 times vgch2 on the simulation thread for busy designs (same replay run), seeks in 0.6-1.0 s, and a cut-short file gave no samples or crashed libfst's reader. Exporting is still the better route.
- **The boards adopt `RUNS` as the format note planned.** Keep `RUNS` for the boards' 8- and 12-bit captures, where one value is one port. The simulation trace has no reason to share their chunk.
- **A design is found whose ports all change together on most clocks.** Planar then stores up to seven pairs where `RUNS` stores one, and its size advantage can turn into a loss. None of the 11 traces does this: at most 13.5% of samples change two or more ports (atari2600, port table).

## How the current format was chosen

**VGCH v2 was chosen without a comparison like this one.** The record:

1. **Rollout step 2 was to compare formats, and it did not.**
   - The spec ("Size and format") names three candidates that "rollout step 2 measures", with "the owner picks one": sigrok `.sr`, FST, and VGCH with runs.
   - The plan has the comparison as Task 21 step 6 ([`pilot_formats.py`](https://github.com/mithro/tinytapeout-vga-videos/blob/main/docs/superpowers/plans/2026-11-26-raw-traces-pipeline.md)).
   - Its ruling, "Decision 2", was written in advance: "VGCH with runs, unless FST or sigrok is at most half its projected size". So the default was set before any measurement, and size was the only test. Recording cost, reading and robustness were not tests at all.
   - When this study started, step 6 had not run, and no other comparison exists.
2. **The format note was decided before any trace existed.** The note is [`2026-11-25-format-v2-runs.md`](https://github.com/mithro/tt-vga-capture/blob/m10-format-note/docs/design/2026-11-25-format-v2-runs.md), in tt-vga-capture at [`e7d349b`](https://github.com/mithro/tt-vga-capture/commit/e7d349b). It weighed `RUNS` against two alternatives: B, 16-byte `RLE64` pairs, and A, planar. Its arguments:
   - B lost on arithmetic: 16 bytes against about 7 a pair.
   - A was set aside without measurement: "`RUNS` can ship first; planar is additive and can be added in a later version only if measured need appears".
   - A main reason for `RUNS` was the board: one chunk could serve the RP2350's compact RLE, the RP2040 and the simulation.
   - The note says plainly that its compact-RLE figures for the board are "an estimate, not a measurement".
3. **The pilot then found the need.** It measured recording over 5% on 6 of 11 projects (nyancat 14.8%), and the trace compressor alone took up to 72 s of CPU (nyancat). Its report named "the format note's planar alternative" as the next fix. This study is that measurement, extended to every alternative.

**The assumptions made then, against the data.**

| Assumption | Where | Verdict |
|---|---|---|
| "A few nanoseconds" a sample: one compare and one branch | spec, "How it is written" | refuted |
| Up to about one run a clock on busy designs | spec | confirmed |
| 35-70 MB/s of encoded data at 4-14% of a core | spec | confirmed |
| No raw fallback needed | spec | confirmed for size |
| A 43-bit value a pair is the natural unit | note | refuted for the busy designs |
| zstd level 1 is the right level | tb.cpp's `kTraceZstdLevel` | partly refuted |
| Each block's first sample read from its `BLKH` at open | note, "Container" | its cost was not weighed |
| One chunk for the board and the simulation | note | not tested |
| A trace is viewed by exporting to FST or VCD | spec | confirmed |

What the data says about each:

- **"A few nanoseconds" a sample.** Busy designs rarely take the fast path: nyancat changes 77% of its clocks, rle_vga 100%. In the Verilator loop vgch2 costs 37-61 ns a clock on them, 15-21% of the simulation.
- **About one run a clock.** nyancat makes 0.77 runs a clock, atari2600 0.90 and rle_vga 2.00.
- **35-70 MB/s, 4-14% of a core.** rle_vga writes 20.2 GB of `RUNS` bytes for 1.44 G clocks, 14 bytes a clock. nyancat's 8.1 GB took 19.6 s to compress in the first round, 4.8% of its simulation CPU. With the job's three reads added, the trace's R2 share is 17.6%.
- **No raw fallback needed.** Raw `xor6` at zstd 1 is the same size as `RUNS` on nyancat (417 against 416 MB) and smaller on rle_vga (13.2 against 18.4 MB). On quiet designs it is 10-300 times larger. Raw's real advantage, the simulation thread on the busiest designs, was not considered.
- **A 43-bit value a pair.** The value is rewritten whole when one port changes. Planar streams of each port are 7.7 times smaller on nyancat, 66 on atari2600 and 100 on nyan.
- **zstd level 1.**
  - On `RUNS` data, level 3 is 30% smaller than level 1 on nyancat (288 against 413 MB) for less CPU (17.3 against 19.6 s).
  - On rle_vga, level 3 is larger and slower: 26.8 MB in 10.9 s, against 18.4 MB in 6.8 s.
  - The pilot tried only levels 1, −1, −3 and −5, by wall time.
- **`BLKH` read at open.** vgacap's open decompresses the start of every block: 111 ms on nyancat, 242 ms on rle_vga. That is most of a one-frame seek. Reading the starts lazily removes most of it with no format change.
- **One chunk with the board.** No board writes `RUNS` today; the note's decision (b) put it off. Nothing in the simulation trace's performance depends on it, and it should not decide the simulation format.
- **Viewing by export.** FST written directly costs 4.1-5.1 times vgch2 on busy designs (same replay run). Its seek takes 0.6-1.0 s a window, and a cut-short FST file gave no samples or crashed the reader (robustness table). Exporting stays the right way to interoperate.

## Fix round 1: what changed

Two reviews read the first round, at [`847a17f`](https://github.com/mithro/vgacap/commit/847a17f): an independent review, and the format owner's. This round answers them.

Each review item, and what this round did about it:

- **The lean writer's "same bytes" was untested** (independent review I2, owner a). The lean writer now uses vgch2's own block split. `cmp` checks it against all 11 traces, and the loop measures it.
- **The recommended configuration never ran in the loop** (I1). Events stored as planar now runs in the loop, with the real converter.
- **Planar on the simulation thread was not in the loop** (I4). It is now.
- **atari2600 and vgadonut were missing from the loop** (owner b5). The loop has 5 projects, 4 processes each.
- **A thread-CPU clock cannot see stalls** (I6, owner b5). Each chunk has its wall time, and the study writers count their waits.
- **Raw's background fell on the other variants** (I6). Raw runs in processes of its own.
- **Jobs run unpinned** (owner b5). One set runs in a systemd scope with `CPUQuota=200%`.
- **The R2 failure was not stated** (I3, owner b4). The Summary and the Recommendation state it, and a table gives every plan's pass or fail.
- **The reader list was incomplete** (owner b1). `tracetool info`, `vgacap-sample` with `rst_n`, and `tracetool uohash` are timed. `vgacap-export`'s cost is the full scan.
- **The container was contradictory** (owner b2, b3). Planar is a new format version: a new header version, a re-encode, one layout, no dual readers and no fallback. Its container change is stated.
- **Lazy block starts before an index** (owner c). Measured.
- **Noise and load were not reported as measured** (I5, Minor 1). Three more background runs, the cache test, and the host load table.
- **The zstd level verdicts went too far** (Minor 2, owner). Corrected.
- **The first sample in the index is a container change** (Minor 3). Stated.
- **The study container is not VGCH v2's** (Minor 4). Stated, with planar's streaming cost.
- **The study readers validate less** (Minor 5). Stated in Method.
- **Robustness gaps** (Minor 6). Stated in Method.
- **Memory** (Minor 7). The allocations, from the code; not measured.
- **End-of-run latency** (Minor 8). Each writer's finish in the loop, in its own table.
- **Full-pin readers** (Minor 9). `vgacap-export`'s cost is the full scan. It was not timed for planar.
- **The loop window** (Minor 10). The window table shows it is representative, except on vgadonut.
- **Bootstrap intervals** (Minor 11). Stated: use the range across the processes.
- **Wording** (Minor 12). Corrected.
- **R9's cost of change** (Minor 13). The files to change and the re-encode, measured.
- **Hand-run encode sets** (Minor 14). Stated in Method.
- **Prose in table cells, two values a cell, links** (Minor 15). Lists, one value a cell, and links.
- **The doc's place** (Minor 16). Moved to `docs/superpowers/`. The exclusion is narrowed to `bench/format-study/`, and the front matter is gone.
- **Behind `origin/main`** (Minor 17). `origin/main` merged.
- **The full-scan lead, and "the same streams"** (owner nits). Corrected.

What the round found that the first round had wrong:

- **The lean writer.** The first round's "about half" came from the study's own `runs` writer. vgacap's writer with one call a changed clock saves 6-10%.
- **R2.** With the job's three reads, vgch2's R2 share is 17.6% on nyancat, not 8.7%. Events stored as planar is 7.4%, not 5.7%.
- **Raw.** In processes of its own, raw costs 6.0% on nyancat, not 3.6%.
- **Background CPU.** It varies up to 2.5 times for identical work.

## Fix round 2: what changed

Two reviews read fix round 1, at [`5be4363`](https://github.com/mithro/vgacap/commit/5be4363): the independent re-review, and the format owner's re-review. This round answers them.

The independent re-review's items:

- **N1: one stdio buffer for every background writer of a process.** [`bg.c`](../../bench/format-study/src/bg.c) had the buffer `static`. Each writer now has its own, freed at close, and a write or close error exits with status 2.
- **N2: the converters' stream order.** The converters wrote planar's streams in raw byte order, not port order. They now take port order from the layout, as the writers do. The study's events files record their layout in the header, so a reader of a Verilator file does not assume the replay's.
- **N2: no read-back of the loop's files.** [`sim/readback.py`](../../bench/format-study/sim/readback.py) reads every trace a set wrote: full hash, samples and `uo_out` run count, against lean's from the same process. [`run_ticks.sh`](../../bench/format-study/sim/run_ticks.sh) fails if any file differs, and no `|| true` is left in it.
- **The background plans again.** events, evplanar, planar and changelog ran again: 5 projects, 4 processes each (`fx2a` to `fx2d`), and one unpinned process each (`fx2s`). Every file reads back right.
- **vgch2 and lean again.** Their files in round 1's sets read back right, but those sets' other files did not. So they ran again in a set of their own (`fx2la` to `fx2ld`), every file read back. No figure in this document now comes from a set with a failed read-back.
- **I1: evplanar's read-back.** It passes on all 5 projects in every process.
- **N3: lean's quiet-design ratios.** Stated as an offset within the noise.
- **N4: lazy open.** The order checks move to seek time, a corrupt `BLKH` can deliver runs before the error, and vgadonut still opens in 112 ms. All three are stated.
- **N5: `build-study/`.** Removed; the study builds under `tmp/build-study`.
- **Minor 6: robustness.** Still not tested; stated as unmeasured.
- **Minor 7: memory.** Measured in the loop ([Memory](#memory)).
- **Minor 9: a full-pin export of planar.** Timed against vgch2's ([A full-pin export to FST](#a-full-pin-export-to-fst-vgch2s-file-against-planars-median-of-the-reps)).
- **`origin/main`.** [`711d6b3`](https://github.com/mithro/vgacap/commit/711d6b3) merged.

The format owner's items:

- **The compact results.** The inputs `analyze.py` reads are about 102 MB, about 29 MB compressed: too large for the branch, so not committed ([Reproducing](#reproducing)).
- **28.3 s, not 27.5 s,** for vgch2's merged full scan on nyancat.
- **The third background estimate.** The loop's scaled figure is not "within about 30%" of encode2's median. Method now gives the measured range, 0.50-1.04 of it on the busy traces.
- **The lean writer is "small, safe and not urgent".** The owner's `block_pair` inline result (0.88 nyancat, 0.77 rle_vga, one replay run) is noted as a direction only. The capture session takes it as its own PR.
- **Lazy open's caveat.** A corrupt `BLKH` can deliver runs before the error.
- **"No dual readers"** applies to `trace_read.c` only: the stream reader keeps v1 and v2. Planar has no plain-stream equivalent yet. Re-encoding the 408 traces costs 2-10 CPU-hours.
- **`tracetool info`.** The pipeline uses only `blocks`, `clocks` and `open.slice_*`. R2 is given with and without the scan.
- **Raw's load.** Raw's sets ran at about twice normal load (1.8-2.25 a CPU), so its figures may be high.
- **The 1% bar** is the study's own, not the spec's.

What the round found:

- **No verdict changed.** Every R1 and R2 pass or fail is as round 1 had it.
- **The background plans' R1 moved by −1.3 to +2.5 points** on the busy designs. Changelog is now the cheapest of the raw-compare writers on all three busy designs.
- **vgch2 and lean.** vgch2's R1 is 14.0% (nyancat), 19.1% (rle_vga) and 7.7% (atari2600), within 1.4 points of round 1's. Lean's saving is smaller than round 1 measured: lean over vgch2: 0.99 [0.93, 1.50] on nyancat, 0.91 [0.88, 0.92] on rle_vga and 0.97 [0.95, 1.03] on atari2600, against round 1's 0.90, 0.90 and 0.94.
- **The background set's first tables counted one set too many.** `analyze.py` took every set whose name began `fx2` as the background set, which took in a two-project smoke set (`fx2smoke`). It now takes `fx2a` to `fx2d` exactly; that moved nyancat's and rle_vga's figures by up to 0.5 points.
- **R2 without the scan.** vgch2's share falls from 17.6% to 10.1% on nyancat. Planar's falls to 3.5% (nyancat), 1.0% (rle_vga) and 0.8% (atari2600): within the whole 5% budget, and on atari2600 within the 1% bar.
- **A full-pin FST export** takes the same CPU from either file on nyancat and atari2600, and 12% more from planar on rle_vga.
- **Raw's read-back first failed through a fault in the harness, not in a file.** Raw's sets have no lean or vgch2 file, and `readback.py` looked for a reference only in the reference set's lean file. It now takes that set's vgch2 file too. Every raw file then read back right.

## Reproducing

Every table in this document between the markers in [Results](#results) is the output of [`bench/format-study/analyze.py`](../../bench/format-study/analyze.py) over the raw results. [`splice_doc.py`](../../bench/format-study/splice_doc.py) puts it in place. Nothing in those tables is typed by hand except [`data/pilot_baseline.csv`](../../bench/format-study/data/pilot_baseline.csv), the pilot's untraced times.

The commands, from a checkout of this branch:

```
bench/format-study/run_all.sh          # build, traces, info, lean, writer, writer2, encode, encode2, warm, read, read2, validate, robust, interop, ctr, analyze
bench/format-study/sim/run_sim.sh      # the first round's Verilator check: build, run (side by side), tick (in one process)
bench/format-study/sim/run_ticks.sh    # the fix rounds' Verilator sets: main, raw, scope (round 1); bg, bgscope, readback_raw, lean (round 2)
bench/format-study/sim/run_mem.py      # memory per plan in the loop (round 2)
bench/format-study/run_export.py       # the full-pin FST export (round 2)
```

The first round's `run_sim.sh tick` was run 11 times, each with `TICK_VARIANTS` in a different order:

- 7 times on one CPU a process, with `TICK_TAG` set to `tick` and to `tickB` through `tickG`;
- 4 times on two CPUs a process, with `TICK_TAG` set to `tick2a` through `tick2d` and `TICK_CPUS` given as pairs (`"24,25 26,27 28,29"`).

Each process's order is in its CSV, `results/sim/<tag>-<project>.csv`, in the variant column.

Where things go:

- Output goes to `tmp/format-study/`, about 60 GB.
- The raw results are not in the branch. The inputs `analyze.py` reads (`results/`, `progress.log`, `load.log` and the loop's `stderr.log` files, without the traces) are about 102 MB as files, about 29 MB compressed, so they were left out rather than committed; they stay in the worktree's `tmp/format-study/`, to be published beside the traces if wanted.
- Raw results go to `tmp/format-study/results/<stage>/`.
- The tables go to `tmp/format-study/results/tables.md`, and into this document.
- Progress, with an ETA, goes to `progress.log`.
- The load goes to `load.log`.

Inputs and run time:

- `run_sim.sh` and `run_ticks.sh` need each project's sources and job logs, copied from `~ttvga/ttvga/work/<shuttle>/<macro>/` into `tmp/sim/src/`, and a Verilator.
- `run_all.sh` takes about 3.5 hours on big-storage, at 20-22 jobs at once. `run_ticks.sh` takes about an hour on 20 CPUs for round 1's sets, and about 55 minutes for round 2's, read-back included (the lean stage is 13 of them). `run_export.py` takes about 35 minutes on 20 CPUs, `run_mem.py` a few minutes.

## References

| What | Where |
|---|---|
| The design spec | [tinytapeout-vga-videos `docs/superpowers/specs/2026-11-24-raw-frames-and-hash-methods-design.md`](https://github.com/mithro/tinytapeout-vga-videos/blob/main/docs/superpowers/specs/2026-11-24-raw-frames-and-hash-methods-design.md), at [`97e74b1`](https://github.com/mithro/tinytapeout-vga-videos/commit/97e74b1) |
| The plan (Task 21 step 6, Task 22 Decision 2) | [tinytapeout-vga-videos `docs/superpowers/plans/2026-11-26-raw-traces-pipeline.md`](https://github.com/mithro/tinytapeout-vga-videos/blob/main/docs/superpowers/plans/2026-11-26-raw-traces-pipeline.md) |
| The format note | [tt-vga-capture `docs/design/2026-11-25-format-v2-runs.md`](https://github.com/mithro/tt-vga-capture/blob/m10-format-note/docs/design/2026-11-25-format-v2-runs.md), branch `m10-format-note`, [`e7d349b`](https://github.com/mithro/tt-vga-capture/commit/e7d349b) |
| VGCH v2, as specified | [`docs/trace-format.md`](../trace-format.md), [`src/stream/reference-stream-format.md`](../../src/stream/reference-stream-format.md) |
| VGCH v2, as implemented | [`include/vgacap/trace.h`](../../include/vgacap/trace.h), [`src/trace/trace_write.c`](../../src/trace/trace_write.c), [`src/trace/trace_read.c`](../../src/trace/trace_read.c), [`src/trace/blocks.c`](../../src/trace/blocks.c); merged in [#16](https://github.com/mithro/vgacap/pull/16) ([`9f53296`](https://github.com/mithro/vgacap/commit/9f53296)) and [#23](https://github.com/mithro/vgacap/pull/23) ([`22f2647`](https://github.com/mithro/vgacap/commit/22f2647)) |
| The pilot's measurements | [tinytapeout-vga-videos `docs/reports/2026-11-pilot/measurements.md`](https://github.com/mithro/tinytapeout-vga-videos/blob/t20-pilot/docs/reports/2026-11-pilot/measurements.md), branch `t20-pilot` at [`c65fa54`](https://github.com/mithro/tinytapeout-vga-videos/commit/c65fa54) |
| The testbench | [tinytapeout-vga-videos `ttvga/harness/tb.cpp`](https://github.com/mithro/tinytapeout-vga-videos/blob/e4608cf65880978024bca53fb77ed60dc320b5ef/ttvga/harness/tb.cpp) at [`e4608cf`](https://github.com/mithro/tinytapeout-vga-videos/commit/e4608cf65880978024bca53fb77ed60dc320b5ef) |
| The pilot's tb overhead investigation | `tinytapeout-vga-videos/.superpowers/sdd/2026-11-26-raw-traces-pipeline/trace-overhead-investigation.md`, local to the controller's checkout and not published |
| The study's code | [`bench/format-study/`](../../bench/format-study/): `src/` (fsbench, the writers, codecs, readers), `sim/` (the Verilator check), `run_all.sh`, `analyze.py`, `third_party/` ([README](../../bench/format-study/third_party/README.md)) |
| zstd seekable format | [zstd `contrib/seekable_format`](https://github.com/facebook/zstd/tree/v1.5.7/contrib/seekable_format), vendored in [`third_party/zstd`](../../third_party/zstd) |
| libfst | vendored in [`third_party/libfst`](../../third_party/libfst) |
| LZ4 and LZ4-HC | [lz4 v1.10.0](https://github.com/lz4/lz4/tree/v1.10.0/lib) |
| LXT2 writer | [gtkwave `lib/liblxt` at `e40ba79`](https://github.com/gtkwave/gtkwave/tree/e40ba7937336824f12f7db870ea36f6f090dd90d/lib/liblxt) |
| wellen (Surfer's reader: VCD, FST, GHW) | [github.com/ekiwi/wellen](https://github.com/ekiwi/wellen) |
| Blosc shuffle and bitshuffle | [github.com/Blosc/c-blosc2](https://github.com/Blosc/c-blosc2) (the idea; the study's transforms are its own, in `codec.c`) |
| Gorilla XOR encoding | Pelkonen et al., "Gorilla: A Fast, Scalable, In-Memory Time Series Database", VLDB 2015 |
