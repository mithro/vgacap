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
10. [Reproducing](#reproducing)
11. [References](#references)

## Summary

- **The current format fails the recording budget on the busy designs, and the measurements show why.**
  - In the Verilator loop, VGCH v2's writer costs 13.9% of the simulation on nyancat and 21.3% on rle_vga. The budget is 5%. Those figures are the median of 4 interleaved processes, run with the writers' threads on a second CPU as in a job.
  - About half of that is vgacap's writer code. A leaner writer of the same `RUNS` bytes costs 7.5% and 13.0%.
  - The rest is the layout. Each change rewrites a 6-byte value and a run, and on these designs 77-100% of clocks change.
- **The `RUNS` layout is also 7.7-100 times larger than it needs to be on busy traces, and its compressor uses up nyancat's whole post-simulation budget.**
  - Planar storage, one run stream per port, gives these sizes:

    | Trace | Planar | VGCH v2 |
    |---|---:|---:|
    | nyancat | 54 MB | 416 MB |
    | atari2600 | 9.0 MB | 597 MB |
    | rle_vga | 0.62 MB | 18.4 MB |

  - The samplers need only `uo_out`, and planar decodes `uo_out` alone 2.4 times (nyancat) to over 1,000 times (rle_vga) faster than VGCH v2 does.
  - VGCH v2 keeps one advantage: the fastest scan of every run, 1.7-2.2 times planar's speed.
- **The recommendation: record with an "events" writer, store planar, and keep VGCH v2's container.**
  - The events writer is tb.cpp's raw compare plus one call per changed clock. That call writes the sample delta, a mask and only the ports that changed.
  - A background thread converts the events to planar streams.
  - Measured against VGCH v2. It is better on both measures on the busy designs, and on colorbars both are negligible:

    | Measure | nyancat | rle_vga | colorbars |
    |---|---:|---:|---:|
    | Simulation thread, events (%) | 6.2 | 7.5 | 2.0 |
    | Simulation thread, VGCH v2 (%) | 13.9 | 21.3 | 3.6 |
    | Trace's share of R2, events (%) | 5.7 | 5.3 | 0.3 |
    | Trace's share of R2, VGCH v2 (%) | 8.7 | 8.0 | 0.1 |

    The trace's share of R2 counts the background thread plus one decode pass for the samplers.
  - Files are planar's size.
  - It is still over the 5% recording budget on the two busiest designs.
    - Raw capture is the one measured way under it: 3.0-3.6% there.
    - But raw capture costs 4.3% even on quiet designs, and 10% of the CPU to convert.
- **Now, with no format change:** vgacap's writer can write the same bytes for about half the simulation-thread cost (the `runs` writer).
- **The existing formats lose on the simulation thread.**
  - FST written directly costs 2.4-6.4 times VGCH v2 on busy designs (replay). A one-frame seek takes 0.6-1.0 s.
  - A cut-short FST is fragile: files cut to 10% or 50% gave no samples or crashed libfst's reader.
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
| R2 | Everything after the simulation, plus the compressor thread, costs at most 5% of the simulation's CPU. The samplers, hashes, PNGs and copies share that 5%, so the trace's own share has to be small. This study counts the trace as passing at 1% or less, with the rest left to the derived outputs. | Performance budget, second bullet | high | the background thread's CPU, as a share of the pilot's untraced simulation CPU |
| R3 | The trace is the record. It keeps every pin at both halves of every clock, and the clock bit is regenerated exactly. A run can be longer than 2^32 samples. | What is recorded; The clock in a run-encoded trace; Size and format | must | every reader's runs hash the same as vgacap's reader's (validation table) |
| R4 | Size. The bulk volume has tens of TB. A raw trace of every run would be 8.5 TB, and runs cut that "by orders of magnitude". Size also sets how long the published archive takes to sync. | Size and format | medium | bytes per clock, and in total for each of the 11 traces |
| R5 | Read paths. One sequential pass feeds every sampler. That pass is "likely the largest post-simulation cost for busy, fast designs", and it counts against R2. A clock range has to seek to the right block (`--from/--to`, `frames compare`). An export of one pin group reads only that group's data. | Size and format; Reading the trace back; Viewing in GTKWave and Surfer | high (scan), medium (seek, one group) | ns per clock for a full scan to a no-op consumer; ms for a 420,000-clock window from a fresh open; ns per clock for `uo_out` alone |
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
- Raw has a set of its own. Its 32 MiB blocks and its background thread share no process, and so no L3 traffic, with another variant's. That removes the first round's bias (raw's background work fell partly on the other variants).
- **Stalls.** A thread-CPU clock cannot see a simulation thread that waits for its writer's thread, since a waiting thread uses no CPU. So each chunk also has its wall time. For each variant, the stall measure is the wall time minus the thread CPU, over the untraced model's same difference. The study writers also count their own hand-overs that waited, and the wall time those took. vgacap's writer keeps no such count.
- **Concurrency.** Two processes a project ran at once, `fx1a` with `fx1b` and then `fx1c` with `fx1d`: 10 processes on 20 CPUs of the second socket. The read and encode benchmarks of this round ran on the first socket at the same time. The paired design cancels most of this; the processes' spread shows what is left.
- **The first round's sets are kept, as "first round".**
  - Two CPUs a process: `tick2a` to `tick2d`, 4 processes on 3 projects, 12 processes at once.
  - One CPU a process: `tick` to `tickG`, 7 processes. There every background thread shared the simulation's core. With a thread-CPU clock that time-sharing is charged to no one; only its cache effects are, and they fall on whichever variant is running.
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

- **Spread.** The same compression work measured up to 2.8 times apart: vgch2 on rle_vga took 6.83 s in the first round and 14.5-17.2 s in encode2, for identical input and output bytes.
- **Not the cache.** [`warm_cache.sh`](../../bench/format-study/warm_cache.sh) ran zstd1 alone, and after zstd-5 and zstd-1 had read the same block, as the first round's matrix did. Both took the same CPU, and both were close to the first round's figure. So the first round's matrix did not flatter it.
- **What it tracks.** The spread follows what else ran on the socket. encode2 ran 20 whole-trace encodes at once on the first socket, the raw candidates' 16-bytes-a-clock passes among them, and the CPUs' hyperthread siblings were not reserved. A memory-bound compressor, such as zstd on rle_vga's 1,100-to-1 data, slows the most.
- **A third estimate.** The loop's own background threads ran beside a real simulation. Scaled to the whole trace, they agree with the whole-trace runs to within about 30% on the busy traces ([three ways table](#background-cpu-three-ways-s-for-the-whole-trace-the-loops-scaled-against-the-whole-trace-runs)).
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
- **Lazy block starts.** [`trace_read_lazy.c`](../../bench/format-study/src/trace_read_lazy.c) is vgacap's reader with one change. Its open reads only block 0's start; a seek's binary search reads the starts it needs, when it needs them. That needs no format change.
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

### Memory

Memory was not measured. The writers allocate this much, from the code ([`w_mine.c`](../../bench/format-study/src/w_mine.c), [`bg.c`](../../bench/format-study/src/bg.c), [`trace_write.c`](../../src/trace/trace_write.c)):

| Writer | Simulation side | Background side |
|---|---:|---:|
| vgch2, lean | 2 × 16 MiB | the zstd output bound, about 16 MiB |
| planar on the simulation thread | 7 streams × 16 MiB × 2 | the zstd output bound |
| events | 2 × 16 MiB | |
| events to planar | 2 × 16 MiB | 8 streams × (4 × 16 MiB), for the converter |
| changelog | 2 × 64 MiB | 8 streams × 64 MiB |
| raw | 2 × 32 MiB | 8 streams × 32 MiB |

These are allocations, not resident memory: a buffer's pages are resident only as far as it is filled. The converters' scratch is sized for the worst case the study allowed, not what a block needs. A production writer would size it to the block. At 22 jobs a host these figures matter, and they should be measured before a choice.

### Interop

[`interop.sh`](../../bench/format-study/interop.sh) runs GTKWave's own converters on the files: `fst2vcd` and `lxt2vcd`, from `/usr/bin`. It also runs `zstd -d`, the reader the spec names for streaming.

### Host

The host is big-storage:

- 88 logical CPUs (2x Xeon Gold 6152, Skylake-SP);
- Debian 13, GCC 14.2;
- Verilator 5.053 (`v5.052-80-g0bad0faca`), the pilot's.

Everything is compiled `-O2 -g` with vgacap's `-Wa,-mbranches-within-32B-boundaries`. The Verilator models use the pilot's flags (`-O3`, `OPT_FAST=-O2`).

Other sessions shared the host throughout, and no stage ran on an idle machine. [Host load during each stage](#host-load-during-each-stage) gives the load as `load.log` recorded it. It ranged from 0.4 to 2.3 a CPU, with as little as 1% idle.

- The first round's stages overlapped:
  - `tick2a` to `tick2d` ran together, 12 processes;
  - the last `writer2` run shared the first socket with three one-CPU loop processes and changelog encodes.
- So did this round's: the loop sets ran on the second socket while encode2, read2 and warm_cache.sh ran on the first.

## Results

The tables below are `analyze.py`'s output, unedited. Only their headings are one level deeper here. They follow these rules:

- Each table is about one metric, with a row per project and a column per candidate.
- "-" means that candidate was not measured there. For example, FST-with-clk and LXT2 were not read, and the in-process check ran on 3 projects.
- The projects come in the brief's order: the five it puts first, then the rest.
- A note under each group says how to read it.

How to read each group of tables:

- **Code paths.** Between 0.2% and 3.4% of clocks take the slow path on 7 of the 11 traces. The other four take it on 16% (vgadonut), 77% (nyancat), 90% (atari2600) and 100% (rle_vga). On those four, any format's cost is its change path's cost.
- **Replay.** The replay ran three times, on different cores at different times: `results/writer2-run1`, then `writer2-run2`, then `writer2`, which the tables use. The first and last are compared in their own table.
  - The absolute figures moved by up to 2 times between the runs: nyancat vgch2 37.8 ns, then 20.5 ns.
  - The ratios between the writers stayed within 10%: vgch2 over events was 2.7 then 2.7.
  - So the replay ranks the writers. It does not give their cost in a simulation.
- **In-process check.** Its absolute cost is the one to use for R1, from the two-CPU set (4 processes). The one-CPU set (7 processes) shares each core with the writers' threads, and is a second view.
  - The processes agree on the order but not on the offsets, so the median and the range are both given. The two-CPU set's ranges are the tighter: vgch2 13.6-14.2% on nyancat.
  - In every process, vgch2 cost the most on the busy designs, and one of the two raw variants the least.
  - One process (tickE) ran its untraced colorbars model about 15 ns a clock slow, which made every variant look negative there. The median is robust to that.
- **Sizes and background CPU.** These cover the whole trace. Sampled measurements are projected (see Method).
- **Readers.** These are trace decode only: no sampler and no hash.

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

#### The replay run twice: ns per clock over null, first run (`writer2-run1`) and second (`writer2`)

| project | vgch2 | planar | events | raw | vgch2 over events |
|---|---:|---:|---:|---:|---:|
| nyancat | 37.76 / 20.45 | 22.99 / 12.45 | 14.00 / 7.52 | 2.59 / 1.60 | 2.70 / 2.72 |
| rle_vga | 64.99 / 35.96 | 30.69 / 17.55 | 21.14 / 11.40 | 2.27 / 1.43 | 3.07 / 3.15 |
| dvd_screensaver | 1.82 / 1.53 | 0.82 / 0.69 | 0.88 / 0.67 | 2.57 / 1.85 | 2.07 / 2.28 |
| colorbars | 3.11 / 1.96 | 1.55 / 1.00 | 1.29 / 0.82 | 3.02 / 1.85 | 2.40 / 2.38 |
| mandelbrot | 1.63 / 1.72 | 0.77 / 0.79 | 0.72 / 0.76 | 2.44 / 1.95 | 2.27 / 2.27 |
| atari2600 | 44.44 / 51.03 | 27.99 / 32.13 | 17.03 / 19.26 | 2.89 / 1.99 | 2.61 / 2.65 |
| vgadonut | 9.18 / 5.30 | 4.86 / 2.80 | 3.20 / 1.93 | 3.54 / 1.75 | 2.86 / 2.74 |
| cfib_demo | 1.99 / 2.47 | 0.96 / 1.35 | 0.85 / 1.15 | 3.32 / 1.96 | 2.34 / 2.14 |
| nyan | 2.72 / 2.09 | 1.54 / 1.19 | 1.29 / 1.01 | 3.69 / 1.81 | 2.11 / 2.08 |
| metaballs | 1.84 / 2.46 | 0.80 / 1.17 | 0.76 / 1.15 | 2.60 / 2.19 | 2.41 / 2.14 |
| tomkeddie_a | 1.90 / 2.03 | 0.91 / 1.03 | 0.86 / 0.94 | 2.54 / 1.94 | 2.21 / 2.17 |

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

### Simulation check: the Verilator loop, in one process

#### Two CPUs a process: the simulation thread on one, the writers' threads on either

Each variant's model in one process, stepped in turn by 65,536-clock chunks, 4,000 chunks (262 M clocks) a process (bench_tick.cpp). As in a job's two-CPU scope. 4 processes (tick2a, tick2b, tick2c, tick2d), each with the variants in another order. ns per clock over the untraced model, paired by chunk.

##### Across the processes: ns per clock, median of the processes' estimates [lowest, highest]

| project | vgch2 | runs | events | changelog | raw | raw_small |
|---|---:|---:|---:|---:|---:|---:|
| nyancat | 29.0 [26.1, 31.1] | 15.5 [14.1, 18.5] | 12.9 [11.8, 13.8] | 15.8 [11.9, 20.9] | 7.4 [5.8, 9.7] | 8.1 [6.0, 9.3] |
| rle_vga | 55.8 [52.9, 61.3] | 34.1 [30.5, 38.4] | 19.7 [16.4, 23.4] | 15.4 [13.7, 19.9] | 7.8 [6.2, 11.3] | 7.9 [6.0, 10.7] |
| colorbars | 6.6 [5.4, 7.3] | 4.3 [3.5, 10.8] | 3.8 [1.5, 5.0] | 3.8 [2.3, 4.8] | 8.3 [6.3, 9.2] | 8.6 [5.5, 8.7] |

##### The same as % of the untraced model's CPU: median [lowest, highest]; the budget is 5%

| project | vgch2 | runs | events | changelog | raw | raw_small |
|---|---:|---:|---:|---:|---:|---:|
| nyancat | 13.9 [13.6, 14.2] | 7.5 [7.1, 8.5] | 6.2 [5.8, 6.6] | 7.1 [6.2, 11.0] | 3.6 [3.0, 4.2] | 3.7 [3.1, 4.9] |
| rle_vga | 21.3 [21.1, 21.6] | 13.0 [12.1, 13.6] | 7.5 [6.5, 8.3] | 5.9 [5.4, 7.0] | 3.0 [2.4, 4.0] | 3.0 [2.4, 3.8] |
| colorbars | 3.6 [3.0, 3.9] | 2.4 [1.9, 5.2] | 2.0 [0.8, 2.9] | 2.0 [1.2, 2.8] | 4.3 [3.4, 5.3] | 4.5 [3.0, 5.0] |

##### Each process: estimate [95% bootstrap interval over its chunks], ns per clock

| project | process | untraced ns per clock | vgch2 | runs | events | changelog | raw | raw_small |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | tick2a | 228.8 | 31.1 [28.8, 33.4] | 16.3 [13.9, 18.8] | 13.2 [10.6, 15.7] | 16.3 [13.9, 18.9] | 9.7 [7.3, 12.0] | 7.8 [5.8, 9.8] |
| nyancat | tick2b | 217.1 | 30.8 [29.0, 32.5] | 18.5 [16.7, 20.4] | 13.8 [12.3, 15.3] | 15.3 [13.7, 16.9] | 8.2 [6.8, 9.8] | 8.4 [6.8, 10.1] |
| nyancat | tick2c | 189.6 | 26.1 [25.1, 27.3] | 14.7 [13.4, 15.8] | 12.6 [11.3, 13.9] | 20.9 [19.5, 22.1] | 6.5 [5.2, 7.8] | 9.3 [8.2, 10.4] |
| nyancat | tick2d | 193.1 | 27.2 [25.8, 28.6] | 14.1 [12.6, 15.6] | 11.8 [10.6, 12.9] | 11.9 [10.4, 13.2] | 5.8 [4.7, 6.9] | 6.0 [4.6, 7.3] |
| rle_vga | tick2a | 269.3 | 57.1 [55.4, 58.9] | 34.2 [32.2, 36.0] | 21.9 [19.9, 23.9] | 16.4 [14.5, 18.2] | 9.2 [7.6, 11.0] | 8.2 [6.5, 9.9] |
| rle_vga | tick2b | 283.5 | 61.3 [59.4, 63.3] | 38.4 [36.4, 40.3] | 23.4 [22.0, 24.8] | 19.9 [18.2, 21.9] | 11.3 [9.8, 12.7] | 10.7 [9.1, 12.4] |
| rle_vga | tick2c | 254.7 | 54.4 [53.0, 55.8] | 34.1 [32.5, 35.6] | 17.6 [16.0, 19.2] | 14.4 [12.6, 16.1] | 6.2 [4.5, 7.8] | 7.6 [6.4, 8.8] |
| rle_vga | tick2d | 251.2 | 52.9 [50.9, 55.1] | 30.5 [28.2, 32.8] | 16.4 [14.8, 18.2] | 13.7 [11.8, 15.6] | 6.4 [4.8, 8.0] | 6.0 [3.7, 8.2] |
| colorbars | tick2a | 176.3 | 6.5 [5.6, 7.3] | 3.8 [2.6, 4.9] | 3.6 [2.4, 4.7] | 2.9 [1.8, 4.2] | 7.4 [6.4, 8.5] | 8.6 [7.7, 9.6] |
| colorbars | tick2b | 206.8 | 7.3 [5.2, 9.4] | 10.8 [8.9, 12.8] | 3.9 [2.0, 5.5] | 4.8 [2.7, 6.8] | 9.2 [7.4, 11.0] | 8.7 [6.6, 10.8] |
| colorbars | tick2c | 173.9 | 6.8 [6.1, 7.6] | 4.7 [3.7, 5.8] | 5.0 [4.0, 6.0] | 4.8 [3.7, 5.9] | 9.2 [8.2, 10.3] | 8.7 [7.9, 9.5] |
| colorbars | tick2d | 183.4 | 5.4 [3.8, 7.1] | 3.5 [1.8, 5.1] | 1.5 [0.3, 2.8] | 2.3 [0.7, 3.9] | 6.3 [5.0, 7.7] | 5.5 [3.9, 7.2] |

#### One CPU a process: the writers' threads share the simulation's core

Each variant's model in one process, stepped in turn by 65,536-clock chunks, 4,000 chunks (262 M clocks) a process (bench_tick.cpp). The background threads compete with the simulation on its core, so candidates with heavy background work (raw, changelog, vgch2) are penalised. 7 processes (tick, tickB, tickC, tickD, tickE, tickF, tickG), each with the variants in another order. ns per clock over the untraced model, paired by chunk.

##### Across the processes: ns per clock, median of the processes' estimates [lowest, highest]

| project | vgch2 | runs | events | changelog | raw | raw_small |
|---|---:|---:|---:|---:|---:|---:|
| nyancat | 34.9 [16.8, 43.8] | 16.5 [5.1, 21.3] | 15.1 [2.5, 29.6] | 12.9 [11.0, 15.4] | 6.7 [-2.9, 11.9] | 7.8 [-2.5, 12.2] |
| rle_vga | 52.0 [48.1, 71.0] | 33.9 [30.2, 46.9] | 17.1 [15.6, 26.4] | 15.0 [13.5, 15.1] | 9.8 [5.0, 15.5] | 6.8 [6.0, 17.3] |
| colorbars | 5.0 [-13.4, 10.1] | 2.9 [-15.9, 6.3] | 2.1 [-16.3, 9.4] | 2.0 [-16.0, 5.1] | 7.2 [-13.8, 11.3] | 7.2 [-12.4, 11.0] |

##### The same as % of the untraced model's CPU: median [lowest, highest]; the budget is 5%

| project | vgch2 | runs | events | changelog | raw | raw_small |
|---|---:|---:|---:|---:|---:|---:|
| nyancat | 17.4 [8.5, 19.1] | 8.4 [2.6, 9.1] | 7.8 [1.3, 11.7] | 6.6 [5.8, 7.9] | 3.4 [-1.5, 6.1] | 4.0 [-1.3, 4.8] |
| rle_vga | 20.2 [19.2, 22.0] | 13.2 [12.0, 14.2] | 6.4 [6.1, 8.0] | 5.6 [5.4, 5.8] | 3.4 [1.9, 5.8] | 2.7 [2.3, 5.2] |
| colorbars | 2.3 [-6.6, 5.3] | 1.3 [-7.8, 3.1] | 1.1 [-8.0, 4.6] | 1.1 [-7.8, 2.5] | 4.0 [-6.8, 5.9] | 4.1 [-6.1, 5.1] |

##### Each process: estimate [95% bootstrap interval over its chunks], ns per clock

| project | process | untraced ns per clock | vgch2 | runs | events | changelog | raw | raw_small |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | tick | 252.8 | 43.8 [41.6, 46.1] | 21.3 [18.7, 23.9] | 29.6 [27.0, 32.3] | - | 11.7 [9.1, 14.0] | 12.2 [10.1, 14.2] |
| nyancat | tickB | 195.1 | 23.9 [22.8, 24.9] | 11.8 [10.7, 13.0] | 8.6 [7.3, 9.8] | - | 3.4 [2.3, 4.5] | 4.1 [3.1, 5.1] |
| nyancat | tickC | 194.5 | 36.5 [35.1, 37.8] | 15.2 [14.0, 16.6] | 12.8 [11.8, 13.7] | - | 6.7 [5.7, 7.6] | 8.1 [6.9, 9.3] |
| nyancat | tickD | 197.7 | 16.8 [15.6, 18.0] | 5.1 [3.9, 6.2] | 2.5 [1.6, 3.5] | - | -2.9 [-4.0, -1.7] | -2.5 [-3.5, -1.5] |
| nyancat | tickE | 195.7 | 34.9 [33.3, 36.4] | 17.8 [16.0, 19.6] | 15.5 [13.8, 17.3] | 15.4 [13.8, 17.1] | 6.8 [5.2, 8.6] | 7.5 [6.2, 8.9] |
| nyancat | tickF | 196.7 | 34.2 [32.5, 36.0] | 18.0 [16.2, 19.8] | 15.3 [13.8, 16.6] | 12.9 [11.3, 14.6] | 11.9 [10.5, 13.2] | 7.8 [6.2, 9.6] |
| nyancat | tickG | 190.3 | 36.3 [34.8, 37.8] | 16.5 [14.9, 18.1] | 15.1 [13.4, 16.8] | 11.0 [9.3, 12.7] | 6.0 [4.2, 7.5] | 8.5 [7.1, 9.9] |
| rle_vga | tick | 330.3 | 71.0 [68.5, 73.3] | 46.9 [44.5, 49.2] | 26.4 [23.9, 28.7] | - | 11.3 [9.0, 13.7] | 17.3 [15.3, 19.4] |
| rle_vga | tickB | 254.3 | 54.7 [52.9, 56.4] | 34.8 [32.8, 36.7] | 19.6 [17.6, 21.5] | - | 14.3 [12.6, 16.0] | 6.8 [5.4, 8.3] |
| rle_vga | tickC | 254.9 | 56.1 [54.3, 58.1] | 33.9 [31.9, 35.7] | 17.9 [16.5, 19.5] | - | 7.6 [6.1, 8.8] | 6.7 [4.9, 8.6] |
| rle_vga | tickD | 256.9 | 52.0 [50.0, 54.0] | 31.6 [29.8, 33.4] | 15.8 [14.3, 17.3] | - | 5.0 [3.1, 6.9] | 6.0 [4.5, 7.4] |
| rle_vga | tickE | 259.8 | 50.7 [48.6, 52.8] | 32.5 [30.1, 34.8] | 15.7 [13.0, 18.4] | 15.1 [12.4, 17.8] | 9.8 [7.4, 12.2] | 11.3 [9.3, 13.5] |
| rle_vga | tickF | 250.9 | 48.1 [46.1, 50.1] | 30.2 [28.2, 32.2] | 15.6 [14.2, 17.0] | 13.5 [11.6, 15.3] | 5.9 [4.5, 7.3] | 7.9 [6.3, 9.7] |
| rle_vga | tickG | 267.3 | 51.3 [49.6, 53.0] | 35.4 [33.4, 37.4] | 17.1 [15.1, 19.1] | 15.0 [12.9, 17.2] | 15.5 [13.3, 17.5] | 6.7 [4.9, 8.2] |
| colorbars | tick | 264.3 | 5.0 [3.5, 6.7] | 3.4 [1.6, 5.3] | 2.3 [0.5, 4.2] | - | 11.1 [9.2, 13.1] | 11.0 [9.2, 12.9] |
| colorbars | tickB | 178.7 | 3.7 [2.9, 4.5] | 2.9 [1.9, 3.9] | 2.0 [0.9, 3.1] | - | 7.2 [6.1, 8.3] | 8.8 [7.9, 9.7] |
| colorbars | tickC | 176.4 | 4.0 [3.1, 5.0] | 2.0 [1.1, 2.9] | 1.9 [1.1, 2.7] | - | 6.3 [5.5, 7.0] | 7.2 [6.3, 8.1] |
| colorbars | tickD | 190.6 | 10.1 [8.8, 11.3] | 2.2 [0.9, 3.6] | 2.1 [1.0, 3.3] | - | 11.3 [9.9, 12.5] | 6.8 [5.6, 7.9] |
| colorbars | tickE | 203.7 | -13.4 [-14.8, -12.0] | -15.9 [-17.6, -14.2] | -16.3 [-18.1, -14.6] | -16.0 [-17.8, -14.2] | -13.8 [-15.6, -12.2] | -12.4 [-13.8, -11.0] |
| colorbars | tickF | 181.2 | 6.1 [4.6, 7.5] | 3.3 [1.8, 4.8] | 3.4 [2.2, 4.5] | 2.0 [0.5, 3.5] | 7.0 [5.8, 8.3] | 6.9 [5.5, 8.1] |
| colorbars | tickG | 205.2 | 9.8 [8.2, 11.2] | 6.3 [4.7, 8.1] | 9.4 [7.7, 11.0] | 5.1 [3.3, 6.9] | 10.1 [8.4, 11.9] | 10.5 [9.0, 11.9] |

#### Replay against the in-process check, ns per clock

| project | vgch2 replay | vgch2 in-process | runs replay | runs in-process | events replay | events in-process | changelog replay | changelog in-process | raw replay | raw in-process |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 20.45 | 29.00 | 10.69 | 15.50 | 7.52 | 12.90 | 4.67 | 15.80 | 1.60 | 7.37 |
| rle_vga | 35.96 | 55.76 | 21.57 | 34.13 | 11.40 | 19.73 | 5.11 | 15.39 | 1.43 | 7.81 |
| colorbars | 1.96 | 6.64 | 0.91 | 4.28 | 0.82 | 3.78 | 0.73 | 3.84 | 1.85 | 8.28 |

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

#### Background CPU, s for the whole trace, and as % of the untraced simulation's CPU

| project | untraced CPU (s) | vgch2 (zstd 1) | runs (zstd 1) | planar (zstd 1) | events (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw xor6 (zstd 1) | raw to planar (zstd 1) | sigrok-like (deflate 6) | VCD (zstd 1) | FST (LZ4) | FST (zlib) | FST with clk (LZ4) | LXT2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 406 | 19.62 (4.84%) | 20.72 (5.11%) | 3.83 (0.95%) | 7.55 (1.86%) | 15.32 (3.78%) | 18.26 (4.50%) | 58.69 (14.47%) | 34.71 (8.56%) | 224.83 (55.42%) | 111.53 (27.49%) | 243.16 (59.94%) | 289.41 (71.34%) | 210.69 (51.93%) | - |
| rle_vga | 453 | 6.83 (1.51%) | 8.25 (1.82%) | 2.39 (0.53%) | 4.43 (0.98%) | 24.14 (5.33%) | 25.23 (5.57%) | 36.21 (7.99%) | 47.01 (10.38%) | 166.46 (36.75%) | 158.92 (35.08%) | 128.86 (28.45%) | 215.15 (47.49%) | 170.31 (37.60%) | - |
| dvd_screensaver | 357 | 0.05 (0.02%) | 0.09 (0.03%) | 0.05 (0.01%) | 0.04 (0.01%) | 0.10 (0.03%) | 0.17 (0.05%) | 40.63 (11.38%) | 14.34 (4.02%) | 162.85 (45.63%) | 0.58 (0.16%) | 1.53 (0.43%) | 1.95 (0.55%) | 151.06 (42.32%) | - |
| colorbars | 356 | 0.13 (0.04%) | 0.23 (0.06%) | 0.08 (0.02%) | 0.09 (0.02%) | 0.89 (0.25%) | 0.72 (0.20%) | 41.59 (11.68%) | 13.09 (3.68%) | 142.80 (40.10%) | 8.41 (2.36%) | 8.16 (2.29%) | 9.55 (2.68%) | 182.84 (51.34%) | - |
| mandelbrot | 1,887 | 0.47 (0.02%) | 0.57 (0.03%) | 0.23 (0.01%) | 0.23 (0.01%) | 0.60 (0.03%) | 0.66 (0.03%) | 120.77 (6.40%) | 44.56 (2.36%) | 649.24 (34.41%) | 4.11 (0.22%) | 11.87 (0.63%) | 24.12 (1.28%) | 384.17 (20.36%) | - |
| atari2600 | 733 | 21.14 (2.88%) | 23.03 (3.14%) | 1.82 (0.25%) | 7.90 (1.08%) | 17.89 (2.44%) | 21.01 (2.87%) | 61.04 (8.32%) | 30.71 (4.19%) | 352.30 (48.05%) | 164.57 (22.45%) | 532.51 (72.63%) | 656.36 (89.52%) | 238.83 (32.57%) | - |
| vgadonut | 760 | 7.01 (0.92%) | 9.76 (1.28%) | 1.28 (0.17%) | 2.04 (0.27%) | 6.45 (0.85%) | 7.26 (0.96%) | 78.31 (10.31%) | 26.62 (3.50%) | 408.28 (53.73%) | 29.33 (3.86%) | 71.91 (9.46%) | 89.21 (11.74%) | 191.92 (25.26%) | - |
| cfib_demo | 624 | 0.63 (0.10%) | 1.06 (0.17%) | 0.14 (0.02%) | 0.38 (0.06%) | 0.85 (0.14%) | 1.13 (0.18%) | 75.16 (12.05%) | 23.04 (3.70%) | 428.08 (68.66%) | 6.35 (1.02%) | 9.58 (1.54%) | 11.05 (1.77%) | 207.48 (33.28%) | - |
| nyan | 337 | 1.24 (0.37%) | 2.21 (0.66%) | 0.06 (0.02%) | 0.81 (0.24%) | 0.70 (0.21%) | 0.60 (0.18%) | 43.35 (12.88%) | 18.97 (5.63%) | 244.48 (72.61%) | 7.35 (2.18%) | 20.82 (6.18%) | 18.99 (5.64%) | 128.80 (38.25%) | - |
| metaballs | 672 | 0.06 (0.01%) | 0.08 (0.01%) | 0.03 (0.00%) | 0.07 (0.01%) | 0.17 (0.02%) | 0.19 (0.03%) | 73.41 (10.92%) | 32.50 (4.83%) | 356.30 (53.00%) | 0.51 (0.08%) | 0.65 (0.10%) | 0.81 (0.12%) | 161.90 (24.08%) | - |
| tomkeddie_a | 385 | 0.03 (0.01%) | 0.06 (0.02%) | 0.03 (0.01%) | 0.02 (0.00%) | 0.14 (0.04%) | 0.24 (0.06%) | 42.57 (11.06%) | 12.71 (3.30%) | 195.19 (50.69%) | 1.10 (0.29%) | 1.97 (0.51%) | 2.06 (0.53%) | 119.74 (31.09%) | - |

#### Compressors on vgch2's blocks: MB, and background CPU s

| project | zstd-5 | zstd-1 | zstd1 | zstd1-nocheck | zstd3 | zstd9 | zstd1-long27 | lz4 | lz4hc9 | deflate1 | deflate6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 656.91 / 12.23 | 592.35 / 18.54 | 413.13 / 19.62 | 413.13 / 18.30 | 288.33 / 17.32 | 121.75 / 42.50 | 286.12 / 32.45 | 390.25 / 3.47 | 153.44 / 130.65 | 303.12 / 35.85 | 157.27 / 92.32 |
| rle_vga | 16.52 / 6.96 | 69.76 / 8.52 | 18.37 / 6.83 | 18.37 / 3.70 | 26.82 / 10.85 | 4.77 / 19.91 | 4.84 / 57.45 | 102.95 / 3.20 | 79.35 / 34.91 | 135.12 / 57.45 | 46.19 / 128.68 |
| dvd_screensaver | 4.95 / 0.05 | 1.51 / 0.05 | 1.31 / 0.05 | 1.31 / 0.05 | 1.31 / 0.07 | 1.47 / 0.36 | 1.49 / 0.11 | 4.73 / 0.03 | 2.77 / 1.18 | 3.54 / 0.25 | 2.34 / 0.59 |
| colorbars | 0.62 / 0.14 | 0.83 / 0.14 | 0.55 / 0.13 | 0.55 / 0.09 | 0.64 / 0.20 | 0.22 / 0.50 | 0.23 / 0.28 | 19.01 / 0.13 | 10.18 / 2.18 | 19.66 / 1.81 | 11.39 / 5.81 |
| mandelbrot | 17.99 / 0.39 | 17.28 / 0.45 | 14.71 / 0.47 | 14.71 / 0.44 | 12.05 / 0.55 | 7.92 / 2.26 | 12.42 / 0.65 | 22.34 / 0.18 | 10.08 / 7.59 | 24.19 / 1.59 | 15.35 / 4.90 |
| atari2600 | 776.57 / 14.24 | 832.67 / 20.45 | 594.67 / 21.14 | 594.67 / 19.58 | 417.73 / 20.92 | 146.94 / 40.99 | 92.38 / 27.81 | 945.37 / 6.94 | 348.52 / 171.14 | 1006.85 / 66.58 | 366.10 / 188.07 |
| vgadonut | 222.51 / 3.16 | 271.62 / 6.54 | 167.74 / 7.01 | 167.74 / 6.49 | 81.01 / 4.93 | 34.31 / 12.87 | 78.15 / 12.11 | 96.86 / 1.02 | 40.42 / 34.60 | 90.38 / 12.00 | 45.03 / 28.72 |
| cfib_demo | 25.60 / 0.55 | 23.94 / 0.60 | 20.59 / 0.63 | 20.59 / 0.57 | 17.27 / 0.72 | 8.68 / 2.43 | 11.80 / 1.71 | 45.07 / 0.35 | 18.84 / 7.73 | 40.83 / 2.73 | 27.46 / 9.70 |
| nyan | 58.12 / 1.10 | 55.81 / 1.22 | 51.78 / 1.24 | 51.78 / 1.17 | 41.58 / 1.64 | 35.81 / 6.92 | 47.69 / 1.57 | 83.71 / 0.54 | 48.89 / 13.63 | 64.09 / 3.94 | 45.60 / 18.07 |
| metaballs | 3.36 / 0.06 | 1.60 / 0.05 | 1.33 / 0.06 | 1.33 / 0.06 | 1.03 / 0.07 | 0.64 / 0.25 | 1.01 / 0.25 | 1.65 / 0.02 | 1.08 / 1.19 | 1.39 / 0.21 | 0.91 / 0.48 |
| tomkeddie_a | 0.19 / 0.03 | 0.12 / 0.03 | 0.11 / 0.03 | 0.11 / 0.02 | 0.09 / 0.04 | 0.13 / 0.14 | 0.11 / 0.14 | 0.45 / 0.01 | 0.37 / 0.22 | 1.09 / 0.27 | 0.61 / 0.59 |

Uncompressed, MB: nyancat 8,118.8, rle_vga 20,182.5, dvd_screensaver 46.7, colorbars 354.7, mandelbrot 187.2, atari2600 9,467.8, vgadonut 3,227.5, cfib_demo 508.6, nyan 264.6, metaballs 56.3, tomkeddie_a 73.2.

#### Compressors on events's blocks: MB, and background CPU s

| project | zstd-5 | zstd-1 | zstd1 | zstd1-nocheck | zstd3 | zstd9 | zstd1-long27 | lz4 | lz4hc9 | deflate1 | deflate6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 176.22 / 6.65 | 171.20 / 7.36 | 153.32 / 7.55 | 153.32 / 6.42 | 150.18 / 10.43 | 96.14 / 38.31 | 129.47 / 23.35 | 231.66 / 2.71 | 123.83 / 95.18 | 180.56 / 22.15 | 105.76 / 56.93 |
| rle_vga | 1.28 / 4.42 | 1.05 / 4.41 | 1.05 / 4.43 | 1.05 / 2.43 | 1.04 / 5.96 | 0.85 / 13.25 | 1.15 / 10.40 | 34.20 / 2.11 | 34.05 / 23.30 | 46.96 / 33.28 | 22.42 / 71.25 |
| dvd_screensaver | 2.46 / 0.04 | 1.33 / 0.04 | 1.13 / 0.04 | 1.13 / 0.04 | 1.24 / 0.07 | 1.38 / 0.32 | 1.19 / 0.06 | 3.09 / 0.02 | 2.39 / 0.67 | 2.80 / 0.15 | 1.86 / 0.49 |
| colorbars | 0.32 / 0.10 | 0.25 / 0.09 | 0.20 / 0.09 | 0.20 / 0.05 | 0.32 / 0.15 | 0.15 / 0.32 | 0.17 / 0.11 | 0.84 / 0.04 | 0.82 / 0.06 | 7.74 / 0.92 | 4.11 / 2.13 |
| mandelbrot | 13.09 / 0.22 | 9.88 / 0.22 | 9.06 / 0.23 | 9.05 / 0.22 | 8.56 / 0.37 | 6.73 / 1.34 | 8.93 / 0.30 | 14.08 / 0.13 | 8.88 / 3.05 | 16.88 / 1.00 | 7.72 / 3.01 |
| atari2600 | 418.25 / 8.24 | 283.63 / 8.42 | 228.55 / 7.90 | 228.55 / 6.82 | 169.47 / 9.46 | 66.28 / 26.80 | 59.13 / 18.22 | 500.47 / 4.52 | 206.96 / 123.00 | 507.95 / 42.91 | 223.06 / 117.93 |
| vgadonut | 79.66 / 1.90 | 55.55 / 2.10 | 44.39 / 2.04 | 44.39 / 1.81 | 43.68 / 2.69 | 25.73 / 11.27 | 35.31 / 7.89 | 62.93 / 0.72 | 30.46 / 27.15 | 55.07 / 7.66 | 30.69 / 18.89 |
| cfib_demo | 18.50 / 0.41 | 10.81 / 0.39 | 9.38 / 0.38 | 9.38 / 0.34 | 8.03 / 0.53 | 4.54 / 1.77 | 6.36 / 0.93 | 23.06 / 0.23 | 8.77 / 5.64 | 24.37 / 2.06 | 11.27 / 5.23 |
| nyan | 52.81 / 0.71 | 45.62 / 0.77 | 36.26 / 0.81 | 36.26 / 0.81 | 31.21 / 1.88 | 26.39 / 6.94 | 35.91 / 1.00 | 53.73 / 0.48 | 36.62 / 7.29 | 45.60 / 2.88 | 33.61 / 15.69 |
| metaballs | 1.31 / 0.06 | 1.07 / 0.06 | 0.95 / 0.07 | 0.95 / 0.07 | 0.96 / 0.14 | 0.62 / 0.30 | 0.84 / 0.17 | 1.33 / 0.02 | 0.96 / 1.01 | 1.11 / 0.17 | 0.69 / 0.42 |
| tomkeddie_a | 0.12 / 0.03 | 0.08 / 0.01 | 0.08 / 0.02 | 0.08 / 0.01 | 0.08 / 0.02 | 0.07 / 0.05 | 0.09 / 0.05 | 0.34 / 0.01 | 0.24 / 0.12 | 0.74 / 0.13 | 0.51 / 0.29 |

Uncompressed, MB: nyancat 3,815.3, rle_vga 8,653.2, dvd_screensaver 22.5, colorbars 155.0, mandelbrot 88.4, atari2600 4,528.9, vgadonut 1,404.7, cfib_demo 225.0, nyan 118.9, metaballs 28.0, tomkeddie_a 40.2.

#### Compressors on planar's blocks: MB, and background CPU s

| project | zstd-5 | zstd-1 | zstd1 | zstd1-nocheck | zstd3 | zstd9 | zstd1-long27 | lz4 | lz4hc9 | deflate1 | deflate6 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 126.17 / 3.15 | 61.57 / 3.60 | 53.43 / 3.83 | 53.43 / 3.08 | 48.22 / 4.35 | 22.89 / 10.93 | 32.67 / 9.17 | 68.58 / 1.01 | 37.53 / 37.31 | 58.77 / 13.12 | 30.28 / 30.26 |
| rle_vga | 0.77 / 2.41 | 0.59 / 2.40 | 0.59 / 2.39 | 0.58 / 1.32 | 0.59 / 3.25 | 0.54 / 10.36 | 0.63 / 20.10 | 22.69 / 1.13 | 22.69 / 1.46 | 28.07 / 21.15 | 5.68 / 45.13 |
| dvd_screensaver | 2.37 / 0.04 | 1.56 / 0.04 | 1.35 / 0.05 | 1.35 / 0.05 | 1.17 / 0.09 | 1.35 / 0.33 | 1.36 / 0.06 | 2.88 / 0.02 | 2.33 / 0.61 | 2.16 / 0.12 | 1.73 / 0.45 |
| colorbars | 0.71 / 0.10 | 0.68 / 0.09 | 0.66 / 0.08 | 0.66 / 0.06 | 0.66 / 0.10 | 0.14 / 0.28 | 0.08 / 0.14 | 0.56 / 0.03 | 0.54 / 0.06 | 1.94 / 0.52 | 0.96 / 1.14 |
| mandelbrot | 11.15 / 0.22 | 8.92 / 0.22 | 8.09 / 0.23 | 8.08 / 0.22 | 7.64 / 0.41 | 6.37 / 1.44 | 8.04 / 0.29 | 11.87 / 0.13 | 8.38 / 1.82 | 11.26 / 0.75 | 6.85 / 2.26 |
| atari2600 | 10.24 / 1.82 | 9.59 / 1.84 | 9.04 / 1.82 | 9.04 / 1.13 | 4.26 / 2.34 | 2.34 / 6.57 | 2.83 / 7.12 | 47.85 / 1.04 | 22.19 / 22.52 | 107.70 / 17.19 | 32.90 / 35.34 |
| vgadonut | 53.85 / 1.21 | 40.45 / 1.26 | 35.30 / 1.28 | 35.29 / 1.15 | 35.13 / 1.83 | 22.50 / 7.72 | 30.35 / 4.33 | 51.72 / 0.52 | 27.65 / 17.46 | 41.61 / 4.45 | 24.20 / 11.73 |
| cfib_demo | 5.28 / 0.16 | 2.63 / 0.13 | 2.43 / 0.14 | 2.43 / 0.11 | 2.29 / 0.16 | 1.49 / 0.79 | 1.68 / 0.46 | 4.91 / 0.06 | 2.06 / 2.30 | 4.76 / 0.74 | 2.33 / 1.67 |
| nyan | 2.69 / 0.08 | 0.57 / 0.06 | 0.50 / 0.06 | 0.50 / 0.04 | 0.50 / 0.07 | 0.39 / 0.22 | 0.51 / 0.15 | 3.91 / 0.04 | 1.04 / 0.47 | 3.53 / 0.47 | 1.27 / 0.89 |
| metaballs | 1.35 / 0.03 | 0.95 / 0.03 | 0.70 / 0.03 | 0.70 / 0.03 | 0.90 / 0.07 | 0.60 / 0.17 | 0.68 / 0.05 | 1.25 / 0.01 | 0.93 / 0.51 | 0.95 / 0.09 | 0.67 / 0.25 |
| tomkeddie_a | 0.22 / 0.03 | 0.17 / 0.02 | 0.15 / 0.03 | 0.15 / 0.02 | 0.14 / 0.03 | 0.13 / 0.09 | 0.16 / 0.13 | 0.43 / 0.01 | 0.32 / 0.22 | 1.01 / 0.18 | 0.47 / 0.40 |

Uncompressed, MB: nyancat 3,007.0, rle_vga 5,777.2, dvd_screensaver 16.4, colorbars 120.3, mandelbrot 63.8, atari2600 3,620.1, vgadonut 944.8, cfib_demo 160.9, nyan 91.8, metaballs 20.9, tomkeddie_a 41.3.

#### Raw candidate: transforms x compressors, MB / background CPU s (projected)

| project | pack6 zstd-1 | pack6 zstd1 | pack6 zstd3 | pack6 lz4 | xor6 zstd-1 | xor6 zstd1 | xor6 zstd3 | xor6 lz4 | xor-byteplanes zstd-1 | xor-byteplanes zstd1 | xor-byteplanes zstd3 | xor-byteplanes lz4 | xor-bitplanes zstd-1 | xor-bitplanes zstd1 | xor-bitplanes zstd3 | xor-bitplanes lz4 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 1,076.5 / 80.6 | 685.7 / 78.0 | 463.6 / 66.0 | 6,183.7 / 53.9 | 689.5 / 57.9 | 409.4 / 58.7 | 303.1 / 57.2 | 1,013.8 / 34.0 | 67.5 / 53.1 | 62.5 / 52.9 | 62.1 / 55.6 | 242.5 / 41.2 | 72.3 / 110.0 | 59.1 / 110.2 | 53.2 / 112.8 | 177.7 / 102.1 |
| rle_vga | 19.2 / 35.9 | 18.7 / 35.9 | 24.3 / 39.7 | 90.4 / 29.4 | 14.4 / 36.2 | 13.3 / 36.2 | 18.6 / 38.7 | 89.9 / 29.3 | 12.3 / 49.7 | 6.6 / 49.8 | 1.6 / 52.2 | 98.4 / 39.5 | 10.0 / 105.9 | 3.8 / 106.0 | 1.2 / 107.7 | 75.7 / 97.6 |
| dvd_screensaver | 19.9 / 40.5 | 16.9 / 40.8 | 16.7 / 42.3 | 107.4 / 32.9 | 27.0 / 40.8 | 21.8 / 40.6 | 24.4 / 42.8 | 101.6 / 32.5 | 20.4 / 52.1 | 10.9 / 52.3 | 14.8 / 53.9 | 112.4 / 40.4 | 18.5 / 107.4 | 13.4 / 107.5 | 6.7 / 110.0 | 111.0 / 99.1 |
| colorbars | 113.8 / 44.1 | 105.2 / 44.9 | 86.0 / 55.4 | 205.0 / 34.3 | 5.7 / 41.4 | 5.5 / 41.6 | 4.8 / 43.4 | 79.4 / 33.2 | 76.8 / 54.8 | 43.3 / 55.0 | 46.5 / 58.1 | 134.3 / 42.5 | 141.2 / 112.3 | 42.2 / 112.5 | 22.3 / 113.9 | 155.7 / 101.7 |
| mandelbrot | 78.2 / 118.5 | 73.4 / 118.6 | 72.7 / 128.2 | 399.1 / 96.4 | 107.0 / 119.8 | 90.2 / 120.8 | 75.8 / 129.9 | 367.1 / 96.7 | 81.5 / 168.6 | 52.9 / 169.1 | 43.3 / 178.8 | 400.1 / 135.1 | 133.3 / 398.9 | 59.4 / 398.9 | 75.3 / 408.0 | 463.1 / 373.3 |
| atari2600 | 2,076.5 / 95.1 | 1,352.8 / 92.4 | 911.4 / 99.5 | 2,431.1 / 43.4 | 765.0 / 58.9 | 515.0 / 61.0 | 454.3 / 63.9 | 1,355.6 / 36.4 | 100.6 / 52.8 | 51.8 / 53.0 | 33.4 / 54.7 | 166.8 / 41.7 | 126.9 / 111.8 | 61.4 / 111.8 | 40.9 / 112.7 | 107.1 / 101.3 |
| vgadonut | 434.7 / 81.5 | 267.6 / 82.8 | 267.8 / 90.2 | 573.0 / 56.3 | 399.7 / 75.9 | 246.5 / 78.3 | 231.7 / 84.8 | 604.8 / 55.4 | 88.8 / 97.5 | 73.5 / 97.0 | 72.9 / 102.8 | 227.0 / 76.0 | 141.4 / 210.3 | 118.7 / 210.4 | 114.4 / 216.1 | 356.8 / 193.7 |
| cfib_demo | 145.3 / 75.2 | 103.4 / 77.1 | 103.3 / 82.3 | 590.2 / 59.7 | 218.6 / 73.9 | 115.6 / 75.2 | 118.3 / 80.4 | 445.4 / 57.6 | 50.0 / 95.6 | 31.6 / 95.4 | 27.1 / 100.6 | 220.2 / 75.7 | 75.3 / 212.3 | 40.3 / 212.6 | 37.6 / 215.9 | 247.1 / 197.2 |
| nyan | 95.5 / 45.4 | 68.1 / 45.9 | 75.5 / 48.2 | 281.7 / 35.2 | 107.1 / 42.6 | 75.0 / 43.4 | 80.5 / 47.3 | 255.2 / 33.1 | 47.5 / 56.0 | 27.5 / 56.3 | 26.4 / 59.0 | 204.3 / 44.6 | 56.5 / 121.0 | 35.5 / 121.4 | 33.0 / 123.2 | 222.6 / 111.8 |
| metaballs | 25.4 / 72.8 | 25.3 / 72.8 | 25.2 / 78.9 | 186.1 / 59.7 | 36.9 / 72.8 | 33.1 / 73.4 | 34.3 / 80.0 | 176.8 / 59.3 | 22.2 / 102.3 | 13.8 / 102.5 | 15.9 / 109.1 | 199.3 / 81.5 | 14.8 / 227.6 | 9.5 / 227.9 | 10.5 / 232.5 | 184.8 / 212.6 |
| tomkeddie_a | 35.4 / 41.9 | 26.5 / 42.0 | 13.2 / 44.7 | 76.7 / 33.6 | 33.5 / 42.1 | 30.9 / 42.6 | 36.1 / 44.9 | 123.7 / 33.8 | 47.8 / 55.0 | 25.5 / 55.1 | 12.6 / 57.0 | 159.0 / 43.4 | 17.4 / 114.5 | 14.7 / 114.0 | 10.4 / 116.2 | 156.6 / 105.7 |

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

### The trace's share of R2: background CPU plus one decode pass for the samplers

Background CPU (File size table) plus the median read time of one pass: the full scan for vgch2, runs, events and raw xor6, which cannot read uo_out alone any faster; uo_out alone (port0) for the planar files, which the samplers' uo_out-only use allows. % of the untraced simulation CPU. Reads of the planar file stand for every planar-stored candidate (the same streams).

| project | vgch2 (zstd 1) | events (zstd 1) | raw xor6 (zstd 1) | planar (zstd 1) | events to planar (zstd 1) | changelog to planar (zstd 1) | raw to planar (zstd 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| nyancat | 8.7 (19.6 + 15.7 s) | 8.3 (7.5 + 26.1 s) | 26.8 (58.7 + 50.2 s) | 2.9 (3.8 + 7.7 s) | 5.7 (15.3 + 7.7 s) | 6.4 (18.3 + 7.7 s) | 10.5 (34.7 + 7.7 s) |
| rle_vga | 8.0 (6.8 + 29.6 s) | 13.7 (4.4 + 57.6 s) | 19.2 (36.2 + 50.8 s) | 0.5 (2.4 + 0.0 s) | 5.3 (24.1 + 0.0 s) | 5.6 (25.2 + 0.0 s) | 10.4 (47.0 + 0.0 s) |
| dvd_screensaver | 0.0 (0.1 + 0.1 s) | 0.1 (0.0 + 0.1 s) | 21.7 (40.6 + 36.8 s) | 0.0 (0.0 + 0.1 s) | 0.0 (0.1 + 0.1 s) | 0.1 (0.2 + 0.1 s) | 4.0 (14.3 + 0.1 s) |
| colorbars | 0.1 (0.1 + 0.4 s) | 0.3 (0.1 + 1.0 s) | 21.6 (41.6 + 35.5 s) | 0.1 (0.1 + 0.1 s) | 0.3 (0.9 + 0.1 s) | 0.2 (0.7 + 0.1 s) | 3.7 (13.1 + 0.1 s) |
| mandelbrot | 0.0 (0.5 + 0.4 s) | 0.0 (0.2 + 0.6 s) | 14.1 (120.8 + 144.9 s) | 0.0 (0.2 + 0.3 s) | 0.0 (0.6 + 0.3 s) | 0.1 (0.7 + 0.3 s) | 2.4 (44.6 + 0.3 s) |
| atari2600 | 5.1 (21.1 + 15.9 s) | 5.3 (7.9 + 30.7 s) | 15.1 (61.0 + 49.6 s) | 0.6 (1.8 + 2.9 s) | 2.8 (17.9 + 2.9 s) | 3.3 (21.0 + 2.9 s) | 4.6 (30.7 + 2.9 s) |
| vgadonut | 1.7 (7.0 + 5.7 s) | 1.6 (2.0 + 9.8 s) | 20.5 (78.3 + 77.5 s) | 0.7 (1.3 + 4.1 s) | 1.4 (6.4 + 4.1 s) | 1.5 (7.3 + 4.1 s) | 4.0 (26.6 + 4.1 s) |
| cfib_demo | 0.2 (0.6 + 0.7 s) | 0.3 (0.4 + 1.6 s) | 24.0 (75.2 + 74.6 s) | 0.1 (0.1 + 0.5 s) | 0.2 (0.8 + 0.5 s) | 0.3 (1.1 + 0.5 s) | 3.8 (23.0 + 0.5 s) |
| nyan | 0.6 (1.2 + 0.7 s) | 0.5 (0.8 + 0.9 s) | 23.8 (43.4 + 36.9 s) | 0.1 (0.1 + 0.1 s) | 0.2 (0.7 + 0.1 s) | 0.2 (0.6 + 0.1 s) | 5.7 (19.0 + 0.1 s) |
| metaballs | 0.0 (0.1 + 0.1 s) | 0.0 (0.1 + 0.2 s) | 21.4 (73.4 + 70.6 s) | 0.0 (0.0 + 0.1 s) | 0.0 (0.2 + 0.1 s) | 0.0 (0.2 + 0.1 s) | 4.8 (32.5 + 0.1 s) |
| tomkeddie_a | 0.0 (0.0 + 0.1 s) | 0.1 (0.0 + 0.2 s) | 22.4 (42.6 + 43.8 s) | 0.0 (0.0 + 0.1 s) | 0.1 (0.1 + 0.1 s) | 0.1 (0.2 + 0.1 s) | 3.3 (12.7 + 0.1 s) |

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

### Implementation size

| code | non-comment lines |
|---|---:|
| vgch2 writer (vgacap src/trace/trace_write.c, blocks.c, trace.h; stream writer) | 633 |
| vgch2 reader (vgacap src/trace/trace_read.c; stream reader) | 1082 |
| the study writers: planar, events, runs, changelog, raw (study src/w_mine.c, w_mine.h) | 528 |
| study container writer + transforms (study src/bg.c, codec.c) | 657 |
| the study readers: planar, events, raw, and vgch2 and FST drivers (study src/rd.c) | 517 |

Spec text, words: VGCH v2 trace 4056 (docs/trace-format.md + src/stream/reference-stream-format.md).

<!-- analyze.py tables: end -->

## VGCH v2's own design choices: what each costs and buys

Each of VGCH v2's choices is judged the way the candidates are, by what it does in use. The numbers are from the [Results](#results) tables.

| Choice | What it costs | What it buys | Verdict |
|---|---|---|---|
| **One run stream of whole 43-bit values** (`RUNS`, 6-byte value + LEB128 run) | Every change rewrites all 7 ports' bytes. On busy traces that makes the file 7.7 (nyancat), 66 (atari2600) and 30 (rle_vga) times planar's. The bytes to compress are 2.7 (nyancat) to 3.5 (rle_vga) times planar's: 8.1 against 3.0 GB, and 20.2 against 5.8 GB. On the simulation thread, the `runs` writer costs 1.4-1.9 times events' and 2.3-4.2 times changelog's in the replay. | The fastest full scan: 10.4 ns a clock on nyancat against planar's 18.1. One stream, so the simplest reader. | The wrong unit for busy designs. It is right only if a reader needs every pin, every time. |
| **Packing to 43 bits on the simulation thread** (tb.cpp's `pack_raw` on each changed clock) | One gather of 8 bytes per changed sample, inside the measured slow path. | A file whose values are independent of Verilator's port layout. Any candidate gets that as well by recording the layout once in the header. | Not needed on the simulation thread. The changelog and raw candidates pack in the background. |
| **The clock bit regenerated from the sample's parity** | A rule in the spec and the reader. | It makes runs possible: without it, no two samples are equal. FST-with-clk, which records `clk`, costs 230-310 ns a clock even on quiet designs (replay). | Keep, in any format. |
| **Run lengths unbounded** (LEB128 up to 64 bits) | 1-10 bytes a run. | R3: a run can be longer than 2^32 samples. | Keep. |
| **vgacap's push API** (`push2` as two pushes, each through `vgacap_trace_slow` and `slow_rare`) | About half of vgch2's simulation-thread cost. The `runs` writer writes the same bytes for 0.53 (nyancat) and 0.61 (rle_vga) of it in the Verilator loop (15.5 against 29.0 ns, 34.1 against 55.8). | One function per sample: flexible, and each push is checked. | Replace with one slow path a changed clock. That needs no format change. |
| **zstd level 1** | 413 MB on nyancat. | Level 3 gives 288 MB for 12% less CPU (17.3 against 19.6 s), and 418 against 595 MB on atari2600. Level −1 is 43% larger on nyancat and only 5% faster. The long window (2^27) gives 92 MB on atari2600 against 595, for 31% more CPU. | Level 3 is better on RUNS data. On planar data level 1 and 3 are within 10%. |
| **An XXH64 checksum in every frame** | Compression: 7% of the compressor's CPU on nyancat (19.6 against 18.3 s), 46% on rle_vga (6.8 against 3.7 s). Decompression: 10-14% on busy traces, more on quiet ones where little else is done. 4 bytes a block. | Corruption is detected. The truncated-trace rule leans on it: the reader drops a last frame whose checksum fails, so a frame cut so that it still looks whole is not kept. | Keep. The cost on rle_vga, 3.1 s of CPU, is real but small next to the payload's own. |
| **Blocks of 2^24 clocks, or 16 MiB** | A seek decompresses a whole block: 30-34 ms on busy traces, 0.4-7.5 ms on the others. A killed run loses up to 2 blocks (R6), each up to 0.73 s of simulated time. | Size on quiet designs: colorbars is 0.55 MB at 2^24 clocks against 11.9 MB at 2^20 and 21.9 MB at 2^18, because zstd frames cannot refer to each other. On busy traces the 16 MiB limit ends the blocks first, so block length changes nothing (nyancat 415-416 MB at every length). | Right for these traces. 2^26 would shrink colorbars to 0.17 MB for 4.5 times the cost of a seek. |
| **`BLKH` at every block's start, found by decompressing it** | Opening decompresses the start of every block: 111 ms (nyancat, 489 blocks) and 261 ms (rle_vga, 1,208 blocks), against under 2 ms for an index that holds each block's first sample. A one-frame seek is mostly this open. | A block names its own position: a stream reader that reads the frames in order needs nothing else. | Keep the `BLKH`. Put the first sample in the index too, so opening need not decompress. |
| **`META` (open and close)** | Under 1 KB a trace. | The status, and a self-describing file. A trace with no `META(close)` reads as truncated, so a cut trace cannot pass as complete. | Keep. |
| **zstd's seekable format** (a seek table in a skippable frame) | 12 bytes a block. A reader has to rebuild the table if it is missing or does not agree with the frames, and that is a whole set of rules in the spec. | Plain `zstd -d` gives the whole stream: the interop table shows 20.2 GB of rle_vga and 355 MB of colorbars from `zstd -d`. Truncation is handled: a 50% cut keeps every whole block (robustness table). | Keep. It is the container's best property, and it does not depend on the payload. |
| **Runs split at block ends** | One extra pair a block. | Every block decodes on its own. | Keep. |
| **The double-buffered compressor thread** | Two 16 MiB buffers; the simulation waits if the thread falls behind. That was not seen in these runs: the in-process check's figures include it. | Compression off the simulation thread. | Keep. |
| **One chunk shared with the boards** (the format note's argument for `RUNS`) | It ties the simulation trace's layout to an 8-bit board capture, where one value is one port. | Nothing yet: no board writes `RUNS`. | It should not decide the simulation format. |

In short, the container is sound: the seekable zstd frames, the per-block start, `META`, the checksums and the block length all hold up under measurement. The payload is the problem. So are the writer's push path and the open's scan of every block. All three can change without touching the container.

**Complexity (R9).** The [Implementation size](#implementation-size) table counts non-comment lines:

| Code | Lines |
|---|---:|
| vgch2's writer, with the stream writer | 633 |
| vgch2's reader, with the stream reader | 1,082 |
| The study's planar, events, changelog, runs and raw writers together | 528 |
| The study's container writer and transforms | 657 |
| The study's readers | 517 |

The VGCH v2 spec ([`trace-format.md`](../trace-format.md) and [`reference-stream-format.md`](../../src/stream/reference-stream-format.md)) is 4,056 words. The format note lists:

- 17 reader fault cases for the three new chunks (its "Reader rules" table);
- 8 invalid-header cases;
- 6 rules for a trace cut short.

Most of those belong to the container (framing, `BLKH`, `META`, truncation), which the recommendation keeps. A planar payload replaces the `RUNS` rules: no adjacent-equal values in a stream, LEB128 minimal and non-zero, and runs that fill the block exactly. It adds one rule: one stream per port, each ending at the block's end. The study's prototypes are not production code. Their line counts bound how much code each layout needs, but they lack vgacap's error reporting.

## Pros and cons per candidate

Every item rests on a table and a number, or on a concrete property.

- **Busy** means nyancat, rle_vga and atari2600: 77-100% of their clocks change a pin.
- **Simulation-thread** figures are the two-CPU in-process medians unless they say "replay".
- **R2 share** is the trace's background CPU plus one decode pass for the samplers, as a share of the simulation CPU ([R2 table](#the-traces-share-of-r2-background-cpu-plus-one-decode-pass-for-the-samplers)).

### vgch2 (current)

| Pros | Cons |
|---|---|
| The fastest full scan of every run: 10.4 ns a clock on nyancat and 20.5 on rle_vga. Planar takes 18.1 and 44.6, events 17.2 and 40.0 (Full scan table). | The most expensive on the simulation thread: 13.9% of the simulation on nyancat and 21.3% on rle_vga, against the 5% budget. The `runs` writer writes the same bytes for 7.5% and 13.0%, so about half of vgch2's cost is vgacap's writer code. |
| One stream with one pair per run: the simplest reader to write against the spec. | Its compressor alone uses 4.8% of nyancat's simulation CPU, and its R2 share is 8.7% (nyancat), 8.0% (rle_vga) and 5.1% (atari2600). |
| Plain `zstd -d` gives the whole stream (R8, interop table). The container is zstd's own seekable format. | The largest file on every busy design: 416 MB nyancat, 597 MB atari2600, 18.4 MB rle_vga. Planar is 54, 9.0 and 0.62 MB (Total bytes table). |
| A truncated or killed trace keeps every finished block. A 50% cut kept 49.9% of the samples, and a kill kept 99.7-99.9% of what was written (robustness table). | Opening reads the start of every block: 111 ms on nyancat and 261 ms on rle_vga, so a one-frame seek costs 128-269 ms on busy traces. Planar's open takes under 2 ms. |
| Smaller than planar on the three quietest traces: tomkeddie_a 0.11 MB against 0.16, colorbars 0.56 against 0.67, dvd_screensaver 1.31 against 1.36. Events is smaller still on all three. | Exporting `uo_out` alone means decoding every run: 12.4 ns a clock on nyancat and 24.2 on rle_vga, against planar's 5.1 and 0.02. |
| | zstd level 1 is not its best level: level 3 is 30% smaller on nyancat, for 12% less CPU (vgch2 compressor table). |

### runs (VGCH v2's RUNS bytes, the study's writer code)

| Pros | Cons |
|---|---|
| The same file as vgch2 for 47% less simulation-thread cost on nyancat (15.5 ns against 29.0) and 39% less on rle_vga (34.1 against 55.8). That needs no format change. | Still 7.5% (nyancat) and 13.0% (rle_vga) of the simulation: over budget. |
| | Everything vgch2 costs in size, background CPU and reading. |

### planar (a run stream per port)

| Pros | Cons |
|---|---|
| The smallest file, or tied with the other planar-stored candidates, on 8 of the 11 traces. It is 7.7-100 times smaller than vgch2 on the busy ones and on nyan (Total bytes table). | Written on the simulation thread, it costs 61% of vgch2 in the replay on nyancat (12.5 ns against 20.5). That is more than any other study writer there (runs 10.7, events 7.5, changelog 4.7), so it is likely over budget. It was not run in the Verilator loop. |
| The lowest R2 share of all: 2.9% (nyancat), 0.5% (rle_vga), 0.6% (atari2600). Its compressor needs 0.95% of nyancat's simulation CPU. | A full scan of every run is 1.7-2.2 times vgch2's (nyancat 18.1 against 10.4 ns a clock), because seven streams are merged. |
| `uo_out` alone decodes only its own stream: 5.1 ns a clock on nyancat and 0.02 on rle_vga (uo_out table). That is all the samplers need. | Seven streams a block, where `RUNS` has one chunk. |
| Opens in under 2 ms on every trace. A one-frame seek takes 0.5-107 ms, against vgch2's 10-276. | |

### events (one time-ordered change stream)

| Pros | Cons |
|---|---|
| The cheapest on the simulation thread of the writers that keep the raw compare, on nyancat: 6.2%. rle_vga 7.5%, colorbars 2.0%. That is 0.45, 0.35 and 0.56 of vgch2. | Still over 5% on the two busiest designs. |
| Stored as is, it is the smallest on the quiet traces: 0.20 MB colorbars, 0.09 MB tomkeddie_a. Its compressor is cheap: 1.9% (nyancat), 1.0% (rle_vga). | Stored as is, it is 2.9 times planar on nyancat (154 against 54 MB) and 25 times on atari2600 (229 against 9.0). Its R2 share is high, because a sampler has to decode every event: 8.3% (nyancat), 13.7% (rle_vga). |
| Converted to planar in the background, its files are planar's (53.8 MB nyancat, 0.66 MB rle_vga, 9.4 MB atari2600), and its R2 share is 5.7%, 5.3% and 2.8%. All of those are below vgch2's. | The converter, written plainly, adds 11.5 s of background CPU on nyancat and 21.8 s on rle_vga to the compressor's. |

### changelog (raw compare; a changed clock stored unencoded; planar written in the background)

| Pros | Cons |
|---|---|
| The cheapest raw-compare writer on rle_vga: 5.9% against events' 7.5%. | On nyancat it costs more than events (7.1% against 6.2%), and it varies more (6.2-11.0% over 4 processes). |
| Its writer does the least on the simulation thread: three 8-byte stores a changed clock, and no encoding. | Its records are 24 bytes a changed clock, so converting them costs more than converting events: R2 6.4% (nyancat) and 5.6% (rle_vga), against events' 5.7% and 5.3%. |
| Stores planar's file, with all of planar's size and reads. | |

### raw (both raw images of every clock)

| Pros | Cons |
|---|---|
| With `raw_small`, the only candidate under 5% on all three projects: 3.6% (nyancat), 3.0% (rle_vga), 4.3% (colorbars). Its cost is the same whatever the design does. | It costs that even on quiet designs, where events costs 2.0% (colorbars). |
| No encoder on the simulation thread at all, so no corner cases there. | Its background thread reads 16 bytes a clock. Stored as `xor6` at zstd 1, its R2 share is 14-27%. Converted to planar, it is still 2.4-10.5%: 10.5% on nyancat, 10.4% on rle_vga. |
| | Stored as `xor6`, it is 10-280 times vgch2 on quiet traces (30.7 MB tomkeddie_a against 0.11). |
| | 2^21-clock blocks of 32 MiB, double-buffered: 64 MiB a writer. With 512 KiB blocks (`raw_small`) the simulation-thread cost was the same (3.7%, 3.0%, 4.5%). |

### sigrok-like (`.sr`)

| Pros | Cons |
|---|---|
| PulseView opens it natively. | Deflating at level 6 takes 34-73% of the simulation CPU (Background CPU table). Colorbars is 162 MB, 290 times vgch2. |
| | The simulation-thread cost is raw's. |

### FST

| Pros | Cons |
|---|---|
| GTKWave and Surfer read it with no export. `fst2vcd` reads the study's files (interop table). | Written directly, it costs 110 (nyancat), 231 (rle_vga) and 124 (atari2600) ns a clock in the replay. That is 2.4-6.4 times vgch2 there, and half to three quarters of the untraced simulation's own time again (228 and 300 ns a clock). |
| Smaller than vgch2 on the busiest traces: 212 MB (LZ4) or 161 MB (zlib) on nyancat, against 416. | Its compression thread used 28-73% of the simulation CPU on busy traces (LZ4). |
| | A one-frame seek takes 0.6-1.0 s. A full scan takes 110 ns a clock on atari2600. |
| | Recording `clk` as a signal (FST-with-clk) costs 234-270 ns a clock on every design (replay). |
| | A truncated file (10% or 50%) gave no samples or crashed libfst's reader (exit 139). A killed writer left 0% (dvd_screensaver), 77% (colorbars) and 99% (rle_vga) of its samples readable (robustness table). |

### LXT2 and VCD

| Pros | Cons |
|---|---|
| GTKWave reads both natively, and Surfer reads VCD. `lxt2vcd` read the study's LXT2 file (interop table). | LXT2: 1-2 µs a clock on busy designs (replay), and 13 GB for rle_vga. |
| VCD is text: everything before a cut still parses. | VCD: 80-180 ns a clock on busy designs (replay), and 3.3 GB at zstd 1 for nyancat. With no index, a seek parses from the start. |

## Recommendation

**Change the trace format.** On the evidence, VGCH v2's `RUNS` payload is the wrong layout for the designs that matter most:

- it makes busy traces 7.7-100 times larger than they need to be;
- its compressor spends nyancat's whole post-simulation budget;
- its writer costs 14-21% of the simulation, against a 5% budget.

Only its full scan of every run is faster than the alternatives'.

Measured candidates, two-CPU in-process figures for the simulation thread:

| Candidate | Simulation thread (% of the simulation), nyancat / rle_vga / colorbars | Trace's share of R2 (%), nyancat / rle_vga / atari2600 | Size, nyancat / atari2600 (MB) |
|---|---|---|---|
| vgch2 (current) | 13.9 / 21.3 / 3.6 | 8.7 / 8.0 / 5.1 | 416 / 597 |
| runs (the same bytes, a lean writer) | 7.5 / 13.0 / 2.4 | about vgch2's | 416 / 597 |
| events, stored as events | 6.2 / 7.5 / 2.0 | 8.3 / 13.7 / 5.3 | 154 / 229 |
| **events, stored as planar** | **6.2 / 7.5 / 2.0** | **5.7 / 5.3 / 2.8** | **54 / 9.4** |
| changelog, stored as planar | 7.1 / 5.9 / 2.0 | 6.4 / 5.6 / 3.3 | 54 / 9.8 |
| raw, stored as planar | 3.6 / 3.0 / 4.3 | 10.5 / 10.4 / 4.6 | 54 / 10.3 |
| planar written on the simulation thread | not measured in the loop; replay 0.61 of vgch2's (nyancat) | 2.9 / 0.5 / 0.6 | 54 / 9.0 |

What to adopt, in order:

| # | Change | Evidence | Status |
|---|---|---|---|
| 1 | **Now, with no format change:** replace vgacap's push path with one slow path a changed clock, as the `runs` writer does. | The same RUNS bytes for 47% less on nyancat (7.5% against 13.9%) and 39% less on rle_vga (13.0% against 21.3%). | measured; the files differ only in chunk headers |
| 2 | **Store each port's runs as a stream of its own (planar):** (u8 value, LEB128 run) pairs. Each stream is a zstd frame of its own, in a seekable container like VGCH v2's. | Planar against vgch2: 54 against 416 MB for nyancat, 9.0 against 597 for atari2600, and 0.62 against 18.4 for rle_vga. `uo_out` alone, which is all the samplers use, reads 2.4 times faster on nyancat and over 1,000 times on rle_vga. It opens in under 2 ms. | measured on all 11 traces; validated against vgacap's reader |
| 3 | **Record with the events writer**, and convert its stream to (2) on the background thread. | The lowest simulation-thread cost of the candidates that keep tb.cpp's raw compare on nyancat (6.2%), with changelog lower on rle_vga (5.9% against 7.5%). It is better than vgch2 on R2 on every busy design (5.7% against 8.7% on nyancat), even with the converter written plainly. | measured: the loop on 3 projects, sizes and background on 11 |
| 4 | **Take each block's first sample from the index**, not from decompressing every block's start. | vgch2's open takes 111 ms on nyancat and 261 ms on rle_vga, most of a one-frame seek (128 and 269 ms). The study container's index opens in under 2 ms. | measured |
| 5 | **Keep zstd level 1 for planar data.** Revisit it only if RUNS stays. | On RUNS data level 3 is 30% smaller than level 1 on nyancat, for 12% less CPU. On planar data level 3 is 10% smaller for 14% more CPU. | measured |

**What this still misses, stated plainly.** (3) is still over the 5% recording budget on the two busiest designs: 6.2% on nyancat and 7.5% on rle_vga. (2)'s converter, as written here, is a byte loop per changed port. The background thread spends 15.3 s of CPU on nyancat, and compressing the streams is only about 3.8 s of that (the planar writer's compressor cost).

Two pieces of work, neither of which changes the stored format, should close those gaps:

- **An optimised converter.** Its floor is the planar compressor's own cost. Planar written on the simulation thread has an R2 share of 2.9% on nyancat and 0.5% on rle_vga.
- **Raw capture for blocks where most clocks change.** On those designs raw capture costs 3.0-3.6%, because it never branches on the data. Choose raw or events capture per block, from the last block's change rate. That keeps events' 2.0% on quiet designs, where raw costs 4.3%.

Both have to be measured before they are relied on. Until then the honest status of (2)+(3) is:

- recording 2.2-2.8 times cheaper on the busy designs;
- the trace's R2 share about a third lower on the busy designs;
- busy traces 7.7-64 times smaller;
- still over budget on the two busiest designs.

**What would change this recommendation.**

| If | Then |
|---|---|
| The samplers come to need every pin, not `uo_out` alone. | A full scan of planar costs 1.7-2.2 times vgch2's: 7.7 ns a clock more on nyancat, about 2.9% of its simulation CPU. That erases most of (2)'s R2 gain on the busiest design. Then store events (smaller than vgch2, and as fast to scan as planar), or keep both streams. |
| An optimised converter still costs more than about 1% of the simulation CPU on busy designs. | Write planar on the simulation thread instead: its R2 share is the lowest measured (2.9% nyancat), but its simulation-thread cost in the replay is 0.61 of vgch2's, which is likely over budget. Measure it in the loop first. |
| Opening the trace directly in GTKWave or Surfer becomes a must. | FST is the only format both read. But it costs 2.4-6.4 times vgch2 on the simulation thread for busy designs (replay), seeks in 0.6-1.0 s, and a cut-short file gave no samples or crashed libfst's reader. Exporting is still the better route. |
| The boards adopt `RUNS` as the format note planned. | Keep `RUNS` for the boards' 8- and 12-bit captures, where one value is one port. The simulation trace has no reason to share their chunk. |
| A design is found whose ports all change together on most clocks. | Planar then stores up to seven pairs where RUNS stores one, and its size advantage can turn into a loss. None of the 11 traces does this: at most 13.5% of samples change two or more ports (atari2600, port table). |

## How the current format was chosen

**VGCH v2 was chosen without a comparison like this one.** The record:

1. **Rollout step 2 was to compare formats, and it did not.** The spec ("Size and format") names three candidates that "rollout step 2 measures", with "the owner picks one": sigrok `.sr`, FST, and VGCH with runs. The plan has the comparison as Task 21 step 6 ([`pilot_formats.py`](https://github.com/mithro/tinytapeout-vga-videos/blob/main/docs/superpowers/plans/2026-11-26-raw-traces-pipeline.md)). Its ruling, "Decision 2", was written in advance: "VGCH with runs, unless FST or sigrok is at most half its projected size". So the default was set before any measurement, and size was the only test. Recording cost, reading and robustness were not tests at all. When this study started, step 6 had not run, and no other comparison exists.
2. **The format note was decided before any trace existed.** The note is [`2026-11-25-format-v2-runs.md`](https://github.com/mithro/tt-vga-capture/blob/m10-format-note/docs/design/2026-11-25-format-v2-runs.md), in tt-vga-capture at [`e7d349b`](https://github.com/mithro/tt-vga-capture/commit/e7d349b). It weighed `RUNS` against two alternatives: B, 16-byte `RLE64` pairs, and A, planar. Its arguments:
   - B lost on arithmetic: 16 bytes against about 7 a pair.
   - A was set aside without measurement: "`RUNS` can ship first; planar is additive and can be added in a later version only if measured need appears".
   - A main reason for `RUNS` was the board: one chunk could serve the RP2350's compact RLE, the RP2040 and the simulation.
   - The note says plainly that its compact-RLE figures for the board are "an estimate, not a measurement".
3. **The pilot then found the need.** It measured recording over 5% on 6 of 11 projects (nyancat 14.8%), and the trace compressor alone took up to 72 s of CPU (nyancat). Its report named "the format note's planar alternative" as the next fix. This study is that measurement, extended to every alternative.

**The assumptions made then, against the data.**

| Assumption, as written | Where | What the data says | Verdict |
|---|---|---|---|
| A compare and a branch per sample, "a few nanoseconds" against the hundreds a clock takes | spec, "How it is written" | Busy designs never take the fast path: nyancat changes 77% of its clocks, rle_vga 100%. In the Verilator loop vgch2 costs 29-56 ns a clock on them: 14-21% of the simulation. | refuted |
| Busy designs produce up to about one run a clock, two with logic on both edges | spec | nyancat 0.77 runs a clock, atari2600 0.90, rle_vga 2.00 | confirmed |
| 35-70 MB/s of encoded data at the fastest simulation, compressed at "zstd's fast levels" for 4-14% of a core | spec | rle_vga writes 20.2 GB of RUNS bytes for 1.44 G clocks, 14 bytes a clock. Compressing them takes 6.8 s; nyancat's 8.1 GB take 19.6 s, 4.8% of its simulation CPU. | confirmed, and it is what spends the whole R2 budget |
| A run of one sample (7-7.5 bytes) is only slightly larger than a raw sample, so no raw fallback is needed | spec | Raw `xor6` at zstd 1 is the same size as `RUNS` on nyancat (417 against 416 MB) and smaller on rle_vga (13.2 against 18.4 MB). On quiet designs it is 10-300 times larger. | confirmed for size; raw's real advantage, the simulation thread, was not considered |
| A 43-bit value a pair is the natural unit | note | The value is rewritten whole when one port changes. Planar streams of each port are 7.7 times smaller on nyancat, 66 on atari2600 and 100 on nyan. | refuted for the busy designs |
| zstd level 1 is the right level | tb.cpp's `kTraceZstdLevel`, after the pilot's step 3 | On RUNS data level 3 is 30% smaller than level 1 on nyancat (288 against 413 MB) for about the same CPU (17.3 against 19.6 s). The pilot tried only 1, −1, −3 and −5, by wall time. | partly refuted |
| A block's first sample comes from its `BLKH`, read by decompressing the start of each frame | note, "Container" | Opening a trace decompresses the start of every block: 111 ms on nyancat, 261 ms on rle_vga. That is most of a one-frame seek (128 ms and 269 ms). | the cost was not weighed |
| The board and the simulation should share one chunk | note | No board writes `RUNS` today; the note's decision (b) put it off. Nothing in the simulation trace's performance depends on it. | not tested; it should not decide the simulation format |
| A trace is viewed by exporting to FST or VCD | spec | FST written directly costs 110-230 ns a clock on busy designs (replay), 2.4-6.4 times vgch2. Its seek takes 0.6-1.0 s a window, and a cut-short FST file gave no samples or crashed the reader (robustness table). Exporting stays the right way to interoperate. | confirmed |

## Reproducing

Every table in this document is the output of [`bench/format-study/analyze.py`](../../bench/format-study/analyze.py) over the raw results. Nothing in them is typed by hand except [`data/pilot_baseline.csv`](../../bench/format-study/data/pilot_baseline.csv), the pilot's untraced times.

The commands, from a checkout of this branch:

```
bench/format-study/run_all.sh          # build, traces, info, writer, writer2, encode, read, validate, robust, interop, ctr, analyze
bench/format-study/sim/run_sim.sh      # the Verilator check: build, run (side by side), tick (in one process)
```

`run_sim.sh tick` was run 11 times, each with `TICK_VARIANTS` in a different order:

- 7 times on one CPU a process, with `TICK_TAG` set to `tick` and to `tickB` through `tickG`;
- 4 times on two CPUs a process, with `TICK_TAG` set to `tick2a` through `tick2d` and `TICK_CPUS` given as pairs (`"24,25 26,27 28,29"`).

The orders are in each process's CSV, `results/sim/tick*-*.csv`, in its variant column.

Where things go:

- Output goes to `tmp/format-study/`, about 60 GB.
- Raw results go to `tmp/format-study/results/<stage>/`.
- The tables go to `tmp/format-study/results/tables.md`.
- Progress, with an ETA, goes to `progress.log`.
- The load goes to `load.log`.

Inputs and run time:

- `run_sim.sh` needs each project's sources and job logs, copied from `~ttvga/ttvga/work/<shuttle>/<macro>/` into `tmp/sim/src/`, and a Verilator.
- The whole run takes about 2.5 hours on big-storage, at 22 jobs at once.

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
