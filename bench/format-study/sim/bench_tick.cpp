// SPDX-License-Identifier: Apache-2.0
// The simulation check, in one process: one Verilated model per variant,
// each with its own trace writer, stepped in turn in chunks of --chunk
// clocks (the order rotated every round), each chunk timed by the thread
// CPU clock. All models run the same clocks from reset, so every chunk
// compares the variants on the same design state, on the same core,
// milliseconds apart: the host's drift, which wrecks runs one after
// another or side by side, cancels in the paired differences.
//
// The clock loop is tb.cpp's tick() (tinytapeout-vga-videos
// ttvga/harness/tb.cpp at e4608cf) without its ui_in/gamepad events and
// without the testbench's own frame decoding, which every variant would
// pay alike: clk low eval, the raw load, clk high eval, the QSPI model,
// the raw load and the trace step. The vgch2 variant's trace step is
// tb.cpp's own code (raw compare, raw_slow, vgacap_trace_repeat2); events
// and raw are the study's (w_mine.h), with their compressor threads.
//
//   bench_tick --out DIR [--variants none,vgch2,events,raw] [--chunk 65536]
//              [--chunks 4000] [--qspi SPEC] [--clock-hz HZ]
// CSV on stdout: chunk,round_slot,variant,ns,clocks.
#include "Vtop.h"
#include "Vtop___024root.h"
#include "qspi.h"
#include "verilated.h"
#include "vgacap/stream.h"
#include "vgacap/trace.h"
#include "w_mine.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <time.h>
#include <vector>

namespace {

double thread_ns() {
    timespec ts;
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
    return ts.tv_sec * 1e9 + ts.tv_nsec;
}

// tb.cpp's RawPorts, load_raw, pack_raw and raw_slow, unchanged.
struct RawPorts {
    const uint8_t* base = nullptr;
    int off[8] = {};
    uint64_t clk_raw = 0;
    bool init(const Vtop___024root* r) {
        const uint8_t* p[8] = {&r->uo_out, &r->uio_out, &r->uio_oe, &r->ui_in,
                               &r->uio_in, &r->rst_n,   &r->ena,    &r->clk};
        const uint8_t* lo = *std::min_element(p, p + 8);
        unsigned seen = 0;
        for (int i = 0; i < 8; i++) {
            const std::ptrdiff_t d = p[i] - lo;
            if (d < 0 || d > 7 || (seen & (1u << d))) return false;
            seen |= 1u << d;
            off[i] = static_cast<int>(d);
        }
        uint8_t b[8] = {};
        b[off[7]] = 1;
        std::memcpy(&clk_raw, b, 8);
        base = lo;
        return true;
    }
};
inline uint64_t load_raw(const uint8_t* b) {
    uint64_t x;
    std::memcpy(&x, b, 8);
    return x;
}
uint64_t pack_raw(uint64_t raw, const RawPorts& rp) {
    uint8_t b[8];
    std::memcpy(b, &raw, 8);
    const int* o = rp.off;
    return static_cast<uint64_t>(b[o[0]]) | static_cast<uint64_t>(b[o[1]]) << 8 |
           static_cast<uint64_t>(b[o[2]]) << 16 | static_cast<uint64_t>(b[o[3]]) << 24 |
           static_cast<uint64_t>(b[o[4]]) << 32 | static_cast<uint64_t>(b[o[5]] & 1) << 40 |
           static_cast<uint64_t>(b[o[6]] & 1) << 41 | static_cast<uint64_t>(b[o[7]] & 1) << 42;
}
__attribute__((noinline)) int raw_slow(vgacap_trace_writer_t* t, const RawPorts& rp, uint64_t r0, uint64_t r1,
                                       uint64_t* want_raw) {
    *want_raw = r1 & ~rp.clk_raw;
    return vgacap_trace_push2(t, pack_raw(r0, rp), pack_raw(r1, rp));
}

struct Sim {
    std::string name;
    int kind = 0;   // 0 none, 1 vgch2, 2 events, 3 raw, 4 raw with 2^15-clock blocks (512 KiB)
    std::unique_ptr<VerilatedContext> ctx;
    std::unique_ptr<Vtop> top;
    ttvga::QspiBus qspi;
    bool use_qspi = false;
    RawPorts raw;
    uint64_t want_raw = ~0ull;
    vgacap_trace_writer_t* tw = nullptr;
    void* sw = nullptr;
    int failed = 0;
};

// tb.cpp's tick(), as it is but for inputs and the frame decoding.
template <int KIND>
void run_chunk(Sim& s, uint64_t clocks) {
    Vtop& top = *s.top;
    VerilatedContext& ctx = *s.ctx;
    const uint8_t* const raw_base = s.raw.base;
    const uint64_t clk_raw = s.raw.clk_raw;
    for (uint64_t c = 0; c < clocks; c++) {
        top.clk = 0; top.eval(); ctx.timeInc(1);
        uint64_t r0 = 0;
        if (KIND) r0 = load_raw(raw_base);
        top.clk = 1; top.eval(); ctx.timeInc(1);
        if (s.use_qspi) {
            const uint8_t v = s.qspi.step(top.uio_out, top.uio_oe, top.uio_in);
            if (v != top.uio_in) { top.uio_in = v; top.eval(); }
        }
        if (KIND) {
            const uint64_t r1 = load_raw(raw_base);
            if (KIND == 2) {
                fs_events_clock(s.sw, r0, r1, clk_raw);
            } else if (KIND == 5) {
                fs_runs_clock(s.sw, r0, r1, clk_raw);
            } else if (KIND == 3 || KIND == 4) {
                fs_raw_clock(s.sw, r0, r1);
            } else if (__builtin_expect(((r0 ^ s.want_raw) | (r1 ^ s.want_raw ^ clk_raw)) != 0, 0)) {
                if (raw_slow(s.tw, s.raw, r0, r1, &s.want_raw)) s.failed = 1;
            } else if (vgacap_trace_repeat2(s.tw)) {
                s.failed = 1;
            }
        }
    }
}

void run(Sim& s, uint64_t clocks) {
    switch (s.kind) {
    case 0: run_chunk<0>(s, clocks); break;
    case 1: run_chunk<1>(s, clocks); break;
    case 2: run_chunk<2>(s, clocks); break;
    case 5: run_chunk<5>(s, clocks); break;
    default: run_chunk<3>(s, clocks); break;   // raw and raw_small: the same step
    }
}

}  // namespace

int main(int argc, char** argv) {
    std::string out = ".", variants = "none,vgch2,events,raw", qspi_spec;
    uint64_t chunk = 65536, chunks = 4000;
    uint32_t clock_hz = 25175000;
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string a = argv[i], v = argv[i + 1];
        if (a == "--out") out = v;
        else if (a == "--variants") variants = v;
        else if (a == "--chunk") chunk = std::strtoull(v.c_str(), nullptr, 0);
        else if (a == "--chunks") chunks = std::strtoull(v.c_str(), nullptr, 0);
        else if (a == "--qspi") qspi_spec = v;
        else if (a == "--clock-hz") clock_hz = static_cast<uint32_t>(std::strtoul(v.c_str(), nullptr, 0));
        else { std::fprintf(stderr, "bench_tick: unknown option %s\n", a.c_str()); return 2; }
    }
    std::vector<std::unique_ptr<Sim>> sims;
    size_t pos = 0;
    variants += ",";
    while ((pos = variants.find(',')) != std::string::npos) {
        std::string v = variants.substr(0, pos);
        variants.erase(0, pos + 1);
        if (v.empty()) continue;
        auto s = std::make_unique<Sim>();
        s->name = v;
        s->kind = v == "none" ? 0 : v == "vgch2" ? 1 : v == "events" ? 2 : v == "raw" ? 3 : v == "raw_small" ? 4 : v == "runs" ? 5 : -1;
        if (s->kind < 0) { std::fprintf(stderr, "bench_tick: no variant %s\n", v.c_str()); return 2; }
        s->ctx = std::make_unique<VerilatedContext>();
        s->ctx->threads(1);
        s->top = std::make_unique<Vtop>(s->ctx.get());
        if (!qspi_spec.empty()) {
            s->use_qspi = true;
            std::string spec = qspi_spec + ",";
            size_t p2;
            while ((p2 = spec.find(',')) != std::string::npos) {
                std::string item = spec.substr(0, p2);
                spec.erase(0, p2 + 1);
                const size_t eq = item.find('=');
                if (eq == std::string::npos) continue;
                const std::string key = item.substr(0, eq);
                const int bit = std::atoi(item.c_str() + eq + 1);
                if (key == "cs0") s->qspi.pins.cs_flash = bit;
                else if (key == "sd0") s->qspi.pins.sd0 = bit;
                else if (key == "sd1") s->qspi.pins.sd1 = bit;
                else if (key == "sck") s->qspi.pins.sck = bit;
                else if (key == "sd2") s->qspi.pins.sd2 = bit;
                else if (key == "sd3") s->qspi.pins.sd3 = bit;
                else if (key == "cs1") s->qspi.pins.cs_ram_a = bit;
                else if (key == "cs2") s->qspi.pins.cs_ram_b = bit;
            }
            s->qspi.init("");
        }
        if (s->kind && !s->raw.init(s->top->rootp)) { std::fprintf(stderr, "bench_tick: ports not contiguous\n"); return 2; }
        const std::string path = out + "/" + v + ".trace";
        if (s->kind == 1) {
            vgacap_header_t h;
            vgacap_header_init_v2(&h, 43, 2, 42, 0);
            h.mode = 2;
            h.clock_hz = clock_hz;
            char err[512];
            s->tw = vgacap_trace_create(path.c_str(), &h,
                                        "meta=open\nproject=bench_tick\ncalib_seconds=0\nseconds=0\nlayout=tt-um-43\n", 1,
                                        err, sizeof err);
            if (!s->tw) { std::fprintf(stderr, "bench_tick: %s\n", err); return 2; }
        } else if (s->kind >= 2) {
            s->sw = fs_sim_create_blocks(s->kind == 2 ? "events" : s->kind == 5 ? "runs" : "raw", path.c_str(), s->raw.off,
                                         s->kind == 4 ? (1u << 15) : 0);
        }
        // tb.cpp's reset: ena 1, rst_n 0 for 10 clocks.
        Vtop& top = *s->top;
        top.ena = 1; top.rst_n = 0; top.ui_in = 0; top.uio_in = 0; top.clk = 0;
        top.eval();
        sims.push_back(std::move(s));
    }
    for (auto& s : sims) run(*s, 10);
    for (auto& s : sims) s->top->rst_n = 1;
    std::printf("chunk,slot,variant,ns,clocks\n");
    const size_t n = sims.size();
    for (uint64_t k = 0; k < chunks; k++) {
        for (size_t j = 0; j < n; j++) {
            Sim& s = *sims[(j + k) % n];
            const double t0 = thread_ns();
            run(s, chunk);
            const double t1 = thread_ns();
            std::printf("%llu,%zu,%s,%.0f,%llu\n", (unsigned long long)k, j, s.name.c_str(), t1 - t0,
                        (unsigned long long)chunk);
        }
    }
    // The models must have stayed in step: compare their outputs.
    for (auto& s : sims)
        if (s->top->uo_out != sims[0]->top->uo_out || s->top->uio_out != sims[0]->top->uio_out)
            std::fprintf(stderr, "bench_tick: %s's model diverged\n", s->name.c_str());
    const double f0 = thread_ns();
    for (auto& s : sims) {
        char err[512];
        if (s->tw && vgacap_trace_finish(s->tw, "meta=close\ncapture_after_clock=0\nstatus=ok\nframes=0\n", err, sizeof err))
            std::fprintf(stderr, "bench_tick: %s: %s\n", s->name.c_str(), err);
        if (s->sw) fs_sim_finish(s->sw);
        if (s->failed) std::fprintf(stderr, "bench_tick: %s: a push failed\n", s->name.c_str());
    }
    std::fprintf(stderr, "bench_tick: finish %.2f s\n", (thread_ns() - f0) / 1e9);
    return 0;
}
