# /// script
# requires-python = ">=3.11"
# ///
# SPDX-License-Identifier: Apache-2.0
"""Makes the simulation check's testbench from tinytapeout-vga-videos' tb.cpp.

    uv run --script make_tb_study.py TB_CPP OUT_CPP

TB_CPP is ttvga/harness/tb.cpp at a pinned commit (sim/run_sim.sh passes
e4608cf's). The output is the same testbench with exactly these changes,
each an exact text replacement that must match once:

1. FS_TB_VARIANT (environment) picks the trace writer: vgch2 (default: the
   unchanged vgacap path), events or raw (the study's writers, w_mine.h,
   with their own compressor thread). Without --trace nothing changes.
(The simulation thread's CPU time is printed by cpu_report.so, preloaded
into every run, the unchanged testbench's too.)

Nothing else in the testbench changes: the same model, eval order, ports,
RawPorts and raw compare.
"""
import sys

src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()


def rep(old: str, new: str) -> None:
    global s
    n = s.count(old)
    if n != 1:
        sys.exit(f"make_tb_study: expected one match, found {n}: {old[:80]!r}")
    s = s.replace(old, new)


rep('#include "vgacap/trace.h"\n',
    '#include "vgacap/trace.h"\n#include "w_mine.h"   // trace format study\n')

rep('int main(int argc, char** argv) {\n',
    '''namespace study {
const char* variant() {
    const char* v = std::getenv("FS_TB_VARIANT");
    return v && *v ? v : "vgch2";
}
bool is_vgch2() { return std::strcmp(variant(), "vgch2") == 0; }
}  // namespace study

int main(int argc, char** argv) {
''')

rep('    vgacap_trace_writer_t* tw = nullptr;\n    if (!opt.trace.empty()) {\n',
    '    vgacap_trace_writer_t* tw = nullptr;\n    void* sw = nullptr;   // the study writer\n'
    '    const int sw_kind = opt.trace.empty() || study::is_vgch2() ? 0 : std::strcmp(study::variant(), "raw") == 0 ? 2 : 1;\n'
    '    if (!opt.trace.empty() && sw_kind == 0) {\n')

rep('''    if (tw) {
#if defined(TTVGA_TEST_HOOKS) && defined(TTVGA_TEST_FORCE_FALLBACK)''',
    '''    if (tw || sw_kind) {
#if defined(TTVGA_TEST_HOOKS) && defined(TTVGA_TEST_FORCE_FALLBACK)''')

rep('''        if (!contiguous)
            std::fprintf(stderr, "tb: the model's 8 port bytes are not contiguous; the trace packs every sample\\n");
    }
''', '''        if (!contiguous)
            std::fprintf(stderr, "tb: the model's 8 port bytes are not contiguous; the trace packs every sample\\n");
        if (sw_kind) {
            if (!contiguous) { std::fprintf(stderr, "tb-study: the study writers need contiguous ports\\n"); return 2; }
            sw = fs_sim_create(study::variant(), opt.trace.c_str(), raw.off);
            if (!sw) { std::fprintf(stderr, "tb-study: no writer %s\\n", study::variant()); return 2; }
        }
    }
''')

rep('''    auto abandon_trace = [&]() {
        if (!tw) return;''', '''    auto abandon_trace = [&]() {
        if (sw) { fs_sim_finish(sw); sw = nullptr; std::remove(opt.trace.c_str()); return; }
        if (!tw) return;''')

rep('''                            const std::string& extra) -> bool {
        if (!tw) return true;''', '''                            const std::string& extra) -> bool {
        if (sw) { fs_sim_finish(sw); sw = nullptr; return true; }
        if (!tw) return true;''')

rep('''            const uint64_t r1 = load_raw(raw_base);
            if (__builtin_expect(((r0 ^ want_raw) | (r1 ^ want_raw ^ clk_raw)) != 0, 0)) {''',
    '''            const uint64_t r1 = load_raw(raw_base);
            if (sw_kind == 1) {
                fs_events_clock(sw, r0, r1, clk_raw);
            } else if (sw_kind == 2) {
                fs_raw_clock(sw, r0, r1);
            } else if (__builtin_expect(((r0 ^ want_raw) | (r1 ^ want_raw ^ clk_raw)) != 0, 0)) {''')

open(dst, "w").write(s)
