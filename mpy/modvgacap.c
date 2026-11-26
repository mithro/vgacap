// SPDX-License-Identifier: Apache-2.0
// `vgacap`: the capture engines as a MicroPython module (mpy/README.md).
//
// A thin binding. The captures are firmware/core/fw_capture.c, the engines
// the pico-sdk capture firmware runs, and their arguments are the
// firmware's protocol arguments (firmware/PROTOCOL.md): the keyword
// arguments are turned back into that `key=value` text and parsed by the
// same tables, so the names, the ranges and every refusal are the
// firmware's. What differs is only what the firmware gets from owning the
// chip -- the project clock is the ttboard SDK's (tt.clock_project_PWM), so
// frame() is told it -- and where the bytes go: to a stream, sys.stdout.buffer
// unless told otherwise, which in the raw REPL is what the capture scripts
// (python/ttcap/mp/) write to.
#include <string.h>

#include "fw_capture.h"
#include "fw_clock.h"
#include "fw_hal.h"
#include "hal_mpy.h"
#include "py/mphal.h"
#include "py/runtime.h"
#include "py/stream.h"

#if VGACAP_MPY_SIM
#include <stdio.h>
#include <stdlib.h>

#include "sim.h"
#else
#include "hal_rp2.h"
#endif

// The interface version: what the host checks before it uses the module
// (python/ttcap/module.py).
#define VGACAP_API 1

// Ctrl-C, the keyboard interrupt the raw REPL runs code with.
#define CTRL_C 3

// The link's streams and a kept exception (hal_mpy.c); while a capture
// runs, a caller's buffer, or the heap blocks the module allocated (rooted
// from the first allocation on: a later one that fails collects first).
MP_REGISTER_ROOT_POINTER(mp_obj_t vgacap_link[3]);
MP_REGISTER_ROOT_POINTER(mp_obj_t vgacap_buf);
MP_REGISTER_ROOT_POINTER(void *vgacap_bufs[128]);

static int busy;

// The capture's buffers: a stream's one (both its halves), a frame
// capture's one or more (fw_hal.h). Static: a frame's 128 do not belong on
// the caller's stack.
static fw_span_t spans[FW_FRAME_MAX_BUFFERS];

// ---- arguments ---------------------------------------------------------------------

typedef struct own_args {
    mp_obj_t out, stop, buf;
} own_args_t;

// The protocol text for the keyword arguments, leaving out the module's own
// (out, stop, buf), which go to `own`. Integers of any size and lower-case
// words are what the protocol carries; anything else is refused here
// rather than mangled.
static void protocol_text(vstr_t *text, size_t n_args, mp_map_t *kw, own_args_t *own) {
    if (n_args) mp_raise_TypeError(MP_ERROR_TEXT("vgacap captures take keyword arguments only"));
    mp_print_t print;
    vstr_init_print(text, 128, &print);
    own->out = MP_OBJ_NULL;
    own->stop = MP_OBJ_NULL;
    own->buf = mp_const_none;
    for (size_t i = 0; i < kw->alloc; i++) {
        if (!mp_map_slot_is_filled(kw, i)) continue;
        mp_obj_t key = kw->table[i].key, value = kw->table[i].value;
        qstr name = mp_obj_str_get_qstr(key);
        // Keys must be names: one passed through **{...} must not carry
        // further key=value text into the protocol line.
        for (const char *k = qstr_str(name); *k; k++) {
            if (!((*k >= 'a' && *k <= 'z') || *k == '_')) {
                mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("'%q' is not a key"), name);
            }
        }
        if (name == MP_QSTR_out) {
            own->out = value;
            continue;
        }
        if (name == MP_QSTR_stop) {
            own->stop = value;
            continue;
        }
        if (name == MP_QSTR_buf) {
            own->buf = value;
            continue;
        }
        mp_printf(&print, " %q=", name);
        if (mp_obj_is_int(value)) {
            mp_obj_print_helper(&print, value, PRINT_REPR);
        } else if (mp_obj_is_str(value)) {
            size_t len;
            const char *s = mp_obj_str_get_data(value, &len);
            for (size_t j = 0; j < len; j++) {
                if (!((s[j] >= 'a' && s[j] <= 'z') || s[j] == '_')) len = 0;
            }
            if (!len) mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("%q: '%s' is not a value"), name, s);
            vstr_add_strn(text, s, len);
        } else {
            mp_raise_msg_varg(&mp_type_TypeError, MP_ERROR_TEXT("%q must be an int or a str"), name);
        }
    }
}

// sys.stdout.buffer / sys.stdin.buffer.
static mp_obj_t sys_stream(qstr which) {
    mp_obj_t sys = mp_import_name(MP_QSTR_sys, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
    return mp_load_attr(mp_load_attr(sys, which), MP_QSTR_buffer);
}

static void resolve_streams(own_args_t *own) {
    if (own->out == MP_OBJ_NULL) own->out = sys_stream(MP_QSTR_stdout);
    if (own->stop == MP_OBJ_NULL) own->stop = sys_stream(MP_QSTR_stdin);
    mp_get_stream_raise(own->out, MP_STREAM_OP_WRITE);
    if (own->stop != mp_const_none) mp_get_stream_raise(own->stop, MP_STREAM_OP_READ | MP_STREAM_OP_IOCTL);
}

// A caller's buffer as the capture buffer: writable, word aligned.
static uint32_t *given_buffer(mp_obj_t buf, size_t *bytes) {
    mp_buffer_info_t bi;
    mp_get_buffer_raise(buf, &bi, MP_BUFFER_WRITE);
    if ((uintptr_t)bi.buf & 3u) mp_raise_ValueError(MP_ERROR_TEXT("buf must be 4-byte aligned"));
    *bytes = bi.len & ~(size_t)3u;
    return bi.buf;
}

// ---- running a capture ---------------------------------------------------------------

typedef int (*engine_fn)(const void *args, unsigned count, char *why, size_t whylen);

static int run_stream(const void *a, unsigned count, char *why, size_t whylen) {
    (void)count;  // one: both halves of the ping-pong
    fw_capture_result_t res;
    return fw_stream_run(a, spans[0].buf, (size_t)spans[0].words * 4u, NULL, NULL, &res, why, whylen);
}

static int run_frame(const void *a, unsigned count, char *why, size_t whylen) {
    fw_capture_result_t res;
    return fw_frame_run(a, spans, count, NULL, NULL, &res, why, whylen);
}

// Give back the `count` blocks the module allocated (spans[]).
static void free_spans(unsigned count) {
    for (unsigned i = 0; i < count; i++) {
        m_del(uint32_t, spans[i].buf, spans[i].words);
        MP_STATE_PORT(vgacap_bufs)[i] = NULL;
    }
}

// The capture into spans[0..count), with Ctrl-C an ordinary byte for its
// whole length (the cooperative stop: prelude.py), and the buffers released
// after when the module allocated them. An exception MicroPython raised
// during it is raised once everything has been given back; a refusal is a
// ValueError with the firmware's `err` text.
static mp_obj_t run(engine_fn engine, const void *args, const own_args_t *own, unsigned count,
                    int allocated) {
    char why[FW_WHY_MAX] = "";
    MP_STATE_PORT(vgacap_buf) = own->buf;
    busy = 1;
#if VGACAP_MPY_SIM
    int saved_intr = CTRL_C;  // the unix port keeps its own; the REPL's is Ctrl-C
#else
    int saved_intr = mp_interrupt_char;  // the raw REPL runs code with Ctrl-C
#endif
    mp_hal_set_interrupt_char(-1);
    vgacap_link_begin(own->out, own->stop);
    int refused = engine(args, count, why, sizeof why);
    mp_obj_t exc = vgacap_link_end();
    // Last, as in the scripts' `finally`: from here a Ctrl-C interrupts again
    // (or whatever the caller had set before).
    mp_hal_set_interrupt_char(saved_intr);
    busy = 0;
    MP_STATE_PORT(vgacap_buf) = MP_OBJ_NULL;
    if (allocated) free_spans(count);
    if (exc != MP_OBJ_NULL) nlr_raise(exc);
    if (refused) mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("%s"), why);
    return mp_const_none;
}

static void not_busy(void) {
    if (busy) mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("a capture is already running"));
}

// stream(*, buf_words, max_bytes, edge, pio, sm, out=sys.stdout.buffer,
//        stop=sys.stdin.buffer, buf=None)
static mp_obj_t vgacap_stream(size_t n_args, const mp_obj_t *pos, mp_map_t *kw) {
    (void)pos;
    not_busy();
    vstr_t text;
    own_args_t own;
    protocol_text(&text, n_args, kw, &own);
    resolve_streams(&own);
    size_t bytes = SIZE_MAX;
    uint32_t *arena = NULL;
    if (own.buf != mp_const_none) arena = given_buffer(own.buf, &bytes);
    fw_stream_args_t a;
    char why[FW_WHY_MAX];
    if (fw_stream_parse(vstr_null_terminated_str(&text), bytes, &a, why, sizeof why)) {
        mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("%s"), why);
    }
    vstr_clear(&text);
    if (arena) {
        spans[0] = (fw_span_t){ arena, (uint32_t)(bytes / 4u) };
        return run(run_stream, &a, &own, 1, 0);
    }
    // MemoryError if the heap cannot hold the two buffers.
    spans[0] = (fw_span_t){ m_new(uint32_t, (size_t)a.buf_words * 2u), a.buf_words * 2u };
    MP_STATE_PORT(vgacap_bufs)[0] = spans[0].buf;
    return run(run_stream, &a, &own, 1, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(vgacap_stream_obj, 0, vgacap_stream);

// Free heap a frame capture leaves MicroPython while it runs: what it
// allocates between buffers (the scheduler, the ttboard SDK's soft timers)
// must still fit. frame_rp2.py holds back 4 KB the same way.
#define HEAP_RESERVE_BYTES 4096u

// One heap block of `lines` lines as spans[n], rooted. 0 if it does not fit.
static int take_block(unsigned n, uint32_t lines, uint32_t line_words) {
    MP_STATIC_ASSERT(MP_ARRAY_SIZE(MP_STATE_PORT(vgacap_bufs)) == FW_FRAME_MAX_BUFFERS);
    uint32_t *p = m_new_maybe(uint32_t, (size_t)lines * line_words);
    if (!p) return 0;
    MP_STATE_PORT(vgacap_bufs)[n] = p;
    spans[n] = (fw_span_t){ p, lines * line_words };
    return 1;
}

// Heap blocks of whole lines for a window of `lines` (frame_rp2.py
// plan_buffers()), with HEAP_RESERVE_BYTES held back while planning, into
// spans[]; returns how many. The whole window in one block when the heap
// has one (one buffer, as the firmware's arena); otherwise blocks of 64,
// 16, 4 and 1 lines, biggest first, up to FW_FRAME_MAX_BUFFERS: a block
// that fits is kept, so each size fails only once, and a failure is what
// costs (MicroPython collects before giving up). On a fragmented heap the
// capture DMA chains them (control blocks, firmware/pico/hal_rp2.c), so one
// captured frame can still fill the whole region. The total may be less
// than `lines`; the engine then sweeps the region in windows.
static unsigned plan_buffers(uint32_t lines, uint32_t line_words) {
    static const uint32_t sizes[] = { 64, 16, 4, 1 };
    void *reserve = m_malloc_maybe(HEAP_RESERVE_BYTES);
    unsigned n = 0;
    if ((size_t)lines <= SIZE_MAX / 4u / line_words && take_block(0, lines, line_words)) {
        n = 1;
        lines = 0;
    }
    for (size_t k = 0; k < MP_ARRAY_SIZE(sizes); k++) {
        while (lines && n < FW_FRAME_MAX_BUFFERS) {
            uint32_t take = sizes[k] < lines ? sizes[k] : lines;
            if (!take_block(n, take, line_words)) break;
            n++;
            lines -= take;
        }
    }
    if (reserve) m_del(uint8_t, reserve, HEAP_RESERVE_BYTES);
    return n;
}

// frame(*, clock_hz, cpl, region_first, region_lines, window_lines,
//       hsync_active, vsync_active, max_frames, max_bytes, edge, sample_delay,
//       pio, sm, out=sys.stdout.buffer, stop=sys.stdin.buffer, buf=None)
static mp_obj_t vgacap_frame(size_t n_args, const mp_obj_t *pos, mp_map_t *kw) {
    (void)pos;
    not_busy();
    vstr_t text;
    own_args_t own;
    protocol_text(&text, n_args, kw, &own);
    resolve_streams(&own);
    fw_frame_args_t a;
    char why[FW_WHY_MAX];
    if (fw_frame_parse(vstr_null_terminated_str(&text), 1, &a, why, sizeof why)) {
        mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("%s"), why);
    }
    vstr_clear(&text);
#if !VGACAP_MPY_SIM
    // Before the engine reads the sync lines for their polarity.
    hal_rp2_uo_inputs();
#endif
    if (own.buf != mp_const_none) {
        size_t bytes;
        uint32_t *given = given_buffer(own.buf, &bytes);
        spans[0] = (fw_span_t){ given, (uint32_t)(bytes / 4u) };
        return run(run_frame, &a, &own, 1, 0);
    }
    const fw_board_t *b = hal_board();
    uint32_t spw = b->push_thresh / b->in_count;
    uint32_t lines = a.region_lines;
    if (a.window_lines && a.window_lines < lines) lines = a.window_lines;
    // Nothing allocated when the engine refuses the line length anyway, or
    // the heap holds not one line: it then says why, as the firmware does.
    unsigned count = a.cpl && a.cpl % spw == 0 ? plan_buffers(lines, a.cpl / spw) : 0;
    return run(run_frame, &a, &own, count, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(vgacap_frame_obj, 0, vgacap_frame);

// ---- the simulated board (unix test build) --------------------------------------------

#if VGACAP_MPY_SIM
// sim_board(path, *, bytes_per_s=1e6, rxstall=False): (re)start the
// simulated board on the design in the file `path` (one uo_out byte per
// project clock, repeated). Read with C stdio into C memory, not the
// MicroPython heap, so a test can give MicroPython a small heap.
static uint8_t *sim_uo;

static mp_obj_t vgacap_sim_board(size_t n_args, const mp_obj_t *pos, mp_map_t *kw) {
    enum { ARG_path, ARG_bytes_per_s, ARG_rxstall };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_path, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_bytes_per_s, MP_ARG_KW_ONLY | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_rxstall, MP_ARG_KW_ONLY | MP_ARG_BOOL, { .u_bool = false } },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos, kw, MP_ARRAY_SIZE(allowed), allowed, args);
    not_busy();
    mp_float_t rate = args[ARG_bytes_per_s].u_obj == MP_OBJ_NULL ? (mp_float_t)1e6
                                                                 : mp_obj_get_float(args[ARG_bytes_per_s].u_obj);
    if (!(rate > 0)) mp_raise_ValueError(MP_ERROR_TEXT("bytes_per_s must be positive"));
    const char *path = mp_obj_str_get_str(args[ARG_path].u_obj);
    FILE *f = fopen(path, "rb");
    if (!f) mp_raise_OSError(MP_ENOENT);
    size_t cap = (size_t)1 << 20, len = 0;
    uint8_t *uo = malloc(cap);
    while (uo) {
        len += fread(uo + len, 1, cap - len, f);
        if (len < cap) break;
        cap *= 2u;
        uint8_t *bigger = realloc(uo, cap);
        if (!bigger) free(uo);
        uo = bigger;
    }
    fclose(f);
    if (!uo) mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("sim_board: out of memory"));
    if (!len) {
        free(uo);
        mp_raise_ValueError(MP_ERROR_TEXT("the design file is empty"));
    }
    free(sim_uo);
    sim_uo = uo;
    sim_config_t cfg = { .uo = sim_uo, .uo_len = len, .seconds_per_byte = 1.0 / (double)rate,
                         .arena_bytes = 0 };
    sim_init(&cfg);
    sim_force_rxstall(args[ARG_rxstall].u_bool);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(vgacap_sim_board_obj, 1, vgacap_sim_board);

// sim_clock(hz): what tt.clock_project_PWM(hz) does on the board -- the
// system clock and PWM the stock firmware picks (fw_clock.h); 0 stops it.
// Returns (sysclk_hz, actual_hz).
static mp_obj_t vgacap_sim_clock(mp_obj_t hz_in) {
    mp_int_t hz = mp_obj_get_int(hz_in);
    const char *why = "";
    if (hz == 0) {
        hal_clock(NULL, &why);
        return mp_obj_new_tuple(2, (mp_obj_t[]){ mp_obj_new_int_from_uint(hal_sysclk_hz()), MP_OBJ_NEW_SMALL_INT(0) });
    }
    fw_clock_plan_t plan;
    if (hz < 0 || hz > 125000000 || fw_clock_plan((uint32_t)hz, &plan, &why) || hal_clock(&plan, &why)) {
        mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("clock %d: %s"), (int)hz, why);
    }
    return mp_obj_new_tuple(2, (mp_obj_t[]){ mp_obj_new_int_from_uint(hal_sysclk_hz()),
                                             mp_obj_new_int_from_uint(plan.actual_hz) });
}
static MP_DEFINE_CONST_FUN_OBJ_1(vgacap_sim_clock_obj, vgacap_sim_clock);

// sim_now(): the simulated board's time in seconds.
static mp_obj_t vgacap_sim_now(void) { return mp_obj_new_float((mp_float_t)sim_now()); }
static MP_DEFINE_CONST_FUN_OBJ_0(vgacap_sim_now_obj, vgacap_sim_now);

// sim_frame_spans(): how many buffers the last frame capture's DMA walked.
static mp_obj_t vgacap_sim_frame_spans(void) { return MP_OBJ_NEW_SMALL_INT(sim_frame_spans()); }
static MP_DEFINE_CONST_FUN_OBJ_0(vgacap_sim_frame_spans_obj, vgacap_sim_frame_spans);
#endif

// ---- the module ----------------------------------------------------------------------

static const mp_rom_map_elem_t vgacap_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_vgacap) },
    { MP_ROM_QSTR(MP_QSTR_API), MP_ROM_INT(VGACAP_API) },
    { MP_ROM_QSTR(MP_QSTR_stream), MP_ROM_PTR(&vgacap_stream_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame), MP_ROM_PTR(&vgacap_frame_obj) },
#if VGACAP_MPY_SIM
    { MP_ROM_QSTR(MP_QSTR_sim_board), MP_ROM_PTR(&vgacap_sim_board_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_clock), MP_ROM_PTR(&vgacap_sim_clock_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_now), MP_ROM_PTR(&vgacap_sim_now_obj) },
    { MP_ROM_QSTR(MP_QSTR_sim_frame_spans), MP_ROM_PTR(&vgacap_sim_frame_spans_obj) },
#endif
};
static MP_DEFINE_CONST_DICT(vgacap_globals, vgacap_globals_table);

const mp_obj_module_t vgacap_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&vgacap_globals,
};

MP_REGISTER_MODULE(MP_QSTR_vgacap, vgacap_module);
