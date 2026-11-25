# SPDX-License-Identifier: Apache-2.0
"""VgacapRawFramer: 8-bit samples in, a vgacap chunk byte stream out.

A leaf module for FPGA capture harnesses (see tt-vga-capture
`docs/design/2026-11-23-shared-rtl-capture.md`): no imports from the rest of
vgacap, Migen only. `vgacap_raw_framer.v` beside it is generated from this
file (`python rtl/vgacap_raw_framer.py > rtl/vgacap_raw_framer.v`) and is
never edited by hand; a test regenerates it and fails if it is stale.

Input: `sample` (8 bits, a Tiny VGA `uo_out`) with `sample_valid`, one per
project clock. Output: `out_data` bytes with `out_valid`/`out_ready`, which
are the bytes of vgacap chunks to append after a `VGCH` header the host
writes (it knows the signal map, the mode and the clock):

* `RAW ` chunks of exactly `samples_per_chunk` samples, four to a 32-bit
  word, first sample in bits 31:24 (header flag VGACAP_FLAG_FIRST_SAMPLE_MSB,
  `samples_per_word=4`, `sample_bits=8`), words little-endian. The chunk
  size is fixed, so the header (tag, length, sample count) is known before
  the data and goes out first: the framer streams through a FIFO of
  `fifo_words` words and never holds a whole chunk to learn its length.
* `TIME` chunks reporting overruns: a chunk is admitted at its first sample
  only if the FIFO has room for all of it; otherwise its samples are dropped
  and counted, and a `TIME` chunk follows the next RAW chunk to finish going
  out (or goes out at once if the output is idle). Its `dropped_samples` is
  the CUMULATIVE number of samples dropped as of the clock the `TIME` chunk
  starts, and its message is `overrun`. Only that total is meaningful, not
  where the `TIME` chunk sits: RAW chunks admitted before a drop can still
  come out after the `TIME` chunk that counts it. So a stalled sink never
  corrupts the stream and never makes a silent gap, but the stream does not
  say which chunks are missing. (Same convention as the RP2 board scripts:
  readers take the last value.)

No flush: the RAW header goes out as soon as a chunk is admitted, so if
`sample_valid` stops mid-chunk the stream ends in a truncated RAW chunk
(which `vgacap.stream.read_chunks` rejects as truncated), and a `TIME`
report still due behind it is never sent. Everything before that last chunk
parses; a capture should stop on a chunk boundary (a multiple of
`samples_per_chunk` samples) to end cleanly.

Throughput: one byte a clock out, at best. Each chunk costs its
samples_per_chunk data bytes, 12 header bytes and 4 FSM clocks with nothing
out (IDLE, the first WORD_LOAD and WORD_WAIT, and the last WORD_LOAD), so
256 samples take 272 clocks back to back. A sink that is always ready
therefore keeps up with samples on at most 256/272 (about 94.1%) of clocks
at 256 samples a chunk; anything faster, or a slower sink, is what the
overrun accounting is for.

Not here (by design): RLE (the compact chunk is undecided), hashing (done
host-side), and the sampler itself (`UoSampler`, contributed separately).
"""

from __future__ import annotations

from migen import Array, Case, Cat, Constant, If, Module, Mux, Signal
from migen.genlib.fifo import SyncFIFO
from migen.genlib.fsm import FSM, NextState, NextValue


def _le32(value: int) -> list[int]:
    return [(value >> (8 * i)) & 0xFF for i in range(4)]


class VgacapRawFramer(Module):
    def __init__(self, samples_per_chunk: int = 256, fifo_words: int = 128) -> None:
        if samples_per_chunk <= 0 or samples_per_chunk % 4:
            raise ValueError("samples_per_chunk must be a positive multiple of 4")
        words = samples_per_chunk // 4
        if fifo_words < words:
            raise ValueError("fifo_words must hold at least one chunk (%d words)" % words)
        self.samples_per_chunk = samples_per_chunk
        self.fifo_words = fifo_words

        # -- ports ---------------------------------------------------------
        # Named, so the generated Verilog's ports are exactly these.
        self.sample = Signal(8, name="sample")
        self.sample_valid = Signal(name="sample_valid")
        self.out_data = Signal(8, name="out_data")
        self.out_valid = Signal(name="out_valid")
        self.out_ready = Signal(name="out_ready")
        #: Cumulative samples dropped by overruns (also reported in-stream).
        self.dropped = Signal(32, name="dropped")

        # -- input side: pack, admit or drop whole chunks ---------------------
        # Synchronous read (fwft=False): an asynchronous-read RAM cannot be
        # block RAM, and on an iCE40UP5K the 128-word FIFO then took 157% of
        # the logic cells and 0 of the 30 block RAMs. The FSM below absorbs
        # the one-clock read latency.
        fifo = SyncFIFO(32, fifo_words, fwft=False)
        self.submodules.fifo = fifo
        pack = Signal(24)
        slot = Signal(2)
        in_chunk = Signal(max=max(words, 2))  # words of the current chunk so far
        admitted = Signal()  # the chunk being packed is going into the FIFO
        chunks_pending = Signal(16)  # admitted chunks whose header is not out
        free_words = Signal(max=fifo_words + 1)
        self.comb += free_words.eq(fifo_words - fifo.level)
        chunk_start = Signal()
        self.comb += chunk_start.eq(self.sample_valid & (slot == 0) & (in_chunk == 0))
        admit_now = Signal()
        self.comb += admit_now.eq(free_words >= words)
        cur_admitted = Signal()
        self.comb += cur_admitted.eq(Mux(chunk_start, admit_now, admitted))
        word_done = Signal()
        self.comb += [
            word_done.eq(self.sample_valid & (slot == 3)),
            fifo.din.eq(Cat(self.sample, pack)),  # first sample in 31:24
            fifo.we.eq(word_done & cur_admitted),
        ]
        header_taken = Signal()
        self.sync += [
            If(self.sample_valid,
                slot.eq(slot + 1),
                pack.eq(Cat(self.sample, pack[0:16])),
                If(chunk_start, admitted.eq(admit_now)),
            ),
            If(word_done,
                If(in_chunk == words - 1, in_chunk.eq(0)).Else(in_chunk.eq(in_chunk + 1)),
            ),
            If(chunk_start & ~admit_now, self.dropped.eq(self.dropped + samples_per_chunk)),
            chunks_pending.eq(chunks_pending + (chunk_start & admit_now) - header_taken),
        ]

        # -- output side: serialise headers, words and TIME chunks -------------
        raw_header = list(b"RAW ") + _le32(4 + 4 * words) + _le32(samples_per_chunk)
        msg = list(b"overrun")
        time_fixed = list(b"TIME") + _le32(18 + len(msg)) + [0] * 12  # host_time_ns u64, clock_hz u32
        n_raw_hdr = len(raw_header)  # 12
        reported = Signal(32)
        report_due = Signal()
        self.comb += report_due.eq(reported != self.dropped)
        idx = Signal(5)
        words_left = Signal(max=words + 1)
        word_buf = Signal(32)
        byte_n = Signal(2)
        prefetched = Signal()  # the next word was read while this one went out
        snap = Signal(32)  # the dropped total this TIME chunk reports
        hdr_rom = Array(Constant(b, 8) for b in raw_header)
        tfix_rom = Array(Constant(b, 8) for b in time_fixed)
        msg_rom = Array(Constant(b, 8) for b in msg)

        fsm = FSM(reset_state="IDLE")
        self.submodules.fsm = fsm
        fsm.act("IDLE",
            If(chunks_pending != 0,
                NextValue(idx, 0),
                NextState("RAW_HEADER"),
            ).Elif(report_due,
                NextValue(idx, 0),
                NextValue(snap, self.dropped),
                NextState("TIME_FIXED"),
            ),
        )
        fsm.act("RAW_HEADER",
            self.out_valid.eq(1),
            self.out_data.eq(hdr_rom[idx]),
            If(self.out_ready,
                If(idx == n_raw_hdr - 1,
                    header_taken.eq(1),
                    NextValue(words_left, words),
                    NextState("WORD_LOAD"),
                ).Else(NextValue(idx, idx + 1)),
            ),
        )
        fsm.act("WORD_LOAD",
            If(words_left == 0,
                If(report_due,
                    NextValue(idx, 0),
                    NextValue(snap, self.dropped),
                    NextState("TIME_FIXED"),
                ).Else(NextState("IDLE")),
            ).Elif(fifo.readable,
                fifo.re.eq(1),
                NextState("WORD_WAIT"),
            ),
        )
        fsm.act("WORD_WAIT",  # dout is valid the clock after re
            NextValue(word_buf, fifo.dout),
            NextValue(byte_n, 0),
            NextState("WORD_BYTES"),
        )
        fsm.act("WORD_BYTES",
            self.out_valid.eq(1),
            Case(byte_n, {i: self.out_data.eq(word_buf[8 * i:8 * i + 8]) for i in range(4)}),
            If(self.out_ready,
                If(byte_n == 2,
                    # Read the next word now, so it is in dout by the time
                    # this one's last byte goes: one byte a clock, no bubble.
                    If((words_left > 1) & fifo.readable,
                        fifo.re.eq(1),
                        NextValue(prefetched, 1),
                    ),
                    NextValue(byte_n, 3),
                ).Elif(byte_n == 3,
                    NextValue(words_left, words_left - 1),
                    NextValue(prefetched, 0),
                    If(prefetched,
                        NextValue(word_buf, fifo.dout),
                        NextValue(byte_n, 0),
                    ).Else(NextState("WORD_LOAD")),
                ).Else(NextValue(byte_n, byte_n + 1)),
            ),
        )
        fsm.act("TIME_FIXED",
            self.out_valid.eq(1),
            self.out_data.eq(tfix_rom[idx]),
            If(self.out_ready,
                If(idx == len(time_fixed) - 1,
                    NextValue(idx, 0),
                    NextState("TIME_DROPPED"),
                ).Else(NextValue(idx, idx + 1)),
            ),
        )
        fsm.act("TIME_DROPPED",
            self.out_valid.eq(1),
            Case(idx, {i: self.out_data.eq(snap[8 * i:8 * i + 8]) for i in range(4)}),
            If(self.out_ready,
                If(idx == 3,
                    NextValue(idx, 0),
                    NextState("TIME_MSGLEN"),
                ).Else(NextValue(idx, idx + 1)),
            ),
        )
        fsm.act("TIME_MSGLEN",
            self.out_valid.eq(1),
            self.out_data.eq(Mux(idx == 0, len(msg), 0)),
            If(self.out_ready,
                If(idx == 1,
                    NextValue(idx, 0),
                    NextState("TIME_MSG"),
                ).Else(NextValue(idx, idx + 1)),
            ),
        )
        fsm.act("TIME_MSG",
            self.out_valid.eq(1),
            self.out_data.eq(msg_rom[idx]),
            If(self.out_ready,
                If(idx == len(msg) - 1,
                    NextValue(reported, snap),
                    NextState("IDLE"),
                ).Else(NextValue(idx, idx + 1)),
            ),
        )


def verilog(samples_per_chunk: int = 256, fifo_words: int = 128) -> str:
    from migen.fhdl.verilog import convert

    top = VgacapRawFramer(samples_per_chunk, fifo_words)
    ios = {top.sample, top.sample_valid, top.out_data, top.out_valid, top.out_ready, top.dropped}
    text = str(convert(top, ios, name="vgacap_raw_framer"))
    head = ("// SPDX-License-Identifier: Apache-2.0\n"
            "// GENERATED by rtl/vgacap_raw_framer.py (samples_per_chunk=%d, fifo_words=%d)\n"
            "// -- do not edit; regenerate with: python rtl/vgacap_raw_framer.py > rtl/vgacap_raw_framer.v\n"
            % (samples_per_chunk, fifo_words))
    return head + text


if __name__ == "__main__":
    import sys

    sys.stdout.write(verilog())
