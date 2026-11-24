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
#include "sim.h"
#else
#include "hal_rp2.h"
#endif

// The interface version: what the host checks before it uses the module
// (python/ttcap/module.py).
#define VGACAP_API 1

// Ctrl-C, the keyboard interrupt the raw REPL runs code with.
#define CTRL_C 3

// The link's streams and a kept exception (hal_mpy.c), the capture buffer
// while a capture runs, and in the simulator build the design.
MP_REGISTER_ROOT_POINTER(mp_obj_t vgacap_link[3]);
MP_REGISTER_ROOT_POINTER(mp_obj_t vgacap_buf);
MP_REGISTER_ROOT_POINTER(void *vgacap_arena);
#if VGACAP_MPY_SIM
MP_REGISTER_ROOT_POINTER(mp_obj_t vgacap_sim_uo);
#endif

static int busy;

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

typedef int (*engine_fn)(const void *args, uint32_t *arena, size_t arena_bytes, char *why,
                         size_t whylen);

static int run_stream(const void *a, uint32_t *arena, size_t bytes, char *why, size_t whylen) {
    fw_capture_result_t res;
    return fw_stream_run(a, arena, bytes, NULL, NULL, &res, why, whylen);
}

static int run_frame(const void *a, uint32_t *arena, size_t bytes, char *why, size_t whylen) {
    fw_capture_result_t res;
    return fw_frame_run(a, arena, bytes, NULL, NULL, &res, why, whylen);
}

// The capture, with Ctrl-C an ordinary byte for its whole length (the
// cooperative stop: prelude.py), and the arena released after. An
// exception MicroPython raised during it is raised once everything has been
// given back; a refusal is a ValueError with the firmware's `err` text.
static mp_obj_t run(engine_fn engine, const void *args, const own_args_t *own, uint32_t *arena,
                    size_t bytes, int allocated) {
    char why[FW_WHY_MAX] = "";
    MP_STATE_PORT(vgacap_buf) = own->buf;
    MP_STATE_PORT(vgacap_arena) = allocated ? arena : NULL;
    busy = 1;
    mp_hal_set_interrupt_char(-1);
    vgacap_link_begin(own->out, own->stop);
    int refused = engine(args, arena, bytes, why, sizeof why);
    mp_obj_t exc = vgacap_link_end();
    // Last, as in the scripts' `finally`: from here a Ctrl-C interrupts again.
    mp_hal_set_interrupt_char(CTRL_C);
    busy = 0;
    MP_STATE_PORT(vgacap_buf) = MP_OBJ_NULL;
    MP_STATE_PORT(vgacap_arena) = NULL;
    if (allocated) m_del(uint8_t, arena, bytes);
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
    if (arena) return run(run_stream, &a, &own, arena, bytes, 0);
    bytes = (size_t)a.buf_words * 8u;
    arena = m_malloc(bytes);  // MemoryError if the heap cannot hold the two buffers
    return run(run_stream, &a, &own, arena, bytes, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(vgacap_stream_obj, 0, vgacap_stream);

// The largest block of whole lines, up to `wanted` bytes, the heap holds:
// halving down to one that fits, then a binary search back up. Each miss
// costs MicroPython a collection, so about 2 log2(lines) of them.
static uint32_t *largest_lines(size_t wanted, size_t line, size_t *bytes) {
    size_t hi = wanted / line, lo = 0;  // lo fits (0: nothing yet), hi + 1 does not
    void *p = NULL;
    for (size_t n = hi; n; n /= 2) {
        p = m_malloc_maybe(n * line);
        if (p) {
            lo = n;
            break;
        }
        hi = n - 1;
    }
    if (!lo) {
        *bytes = 0;
        return NULL;
    }
    while (lo < hi) {
        size_t mid = lo + (hi - lo + 1) / 2;
        void *q = m_malloc_maybe(mid * line);
        if (q) {
            m_del(uint8_t, p, lo * line);
            p = q;
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }
    *bytes = lo * line;
    return p;
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
    size_t bytes = 0;
    if (own.buf != mp_const_none) {
        uint32_t *given = given_buffer(own.buf, &bytes);
        return run(run_frame, &a, &own, given, bytes, 0);
    }
    const fw_board_t *b = hal_board();
    size_t wanted = fw_frame_wanted_bytes(&a);
    size_t line = (size_t)a.cpl / (b->push_thresh / b->in_count) * 4u;
    // Nothing allocated when the arguments are refused anyway, or the heap
    // holds not one line: the engine then says why, as the firmware does.
    uint32_t *arena = wanted ? largest_lines(wanted, line, &bytes) : NULL;
    return run(run_frame, &a, &own, arena, bytes, arena != NULL);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(vgacap_frame_obj, 0, vgacap_frame);

// ---- the simulated board (unix test build) --------------------------------------------

#if VGACAP_MPY_SIM
// sim_board(uo, *, bytes_per_s=1e6, rxstall=False): (re)start the simulated
// board on the design `uo` (one uo_out byte per project clock, repeated).
static mp_obj_t vgacap_sim_board(size_t n_args, const mp_obj_t *pos, mp_map_t *kw) {
    enum { ARG_uo, ARG_bytes_per_s, ARG_rxstall };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_uo, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_bytes_per_s, MP_ARG_KW_ONLY | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_rxstall, MP_ARG_KW_ONLY | MP_ARG_BOOL, { .u_bool = false } },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos, kw, MP_ARRAY_SIZE(allowed), allowed, args);
    not_busy();
    mp_buffer_info_t bi;
    mp_get_buffer_raise(args[ARG_uo].u_obj, &bi, MP_BUFFER_READ);
    if (!bi.len) mp_raise_ValueError(MP_ERROR_TEXT("uo is empty"));
    mp_obj_t uo = mp_obj_new_bytes(bi.buf, bi.len);  // ours: the simulator keeps a pointer
    mp_buffer_info_t ui;
    mp_get_buffer_raise(uo, &ui, MP_BUFFER_READ);
    mp_float_t rate = args[ARG_bytes_per_s].u_obj == MP_OBJ_NULL ? (mp_float_t)1e6
                                                                 : mp_obj_get_float(args[ARG_bytes_per_s].u_obj);
    if (!(rate > 0)) mp_raise_ValueError(MP_ERROR_TEXT("bytes_per_s must be positive"));
    MP_STATE_PORT(vgacap_sim_uo) = uo;
    sim_config_t cfg = { .uo = ui.buf, .uo_len = ui.len, .seconds_per_byte = 1.0 / (double)rate,
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
#endif
};
static MP_DEFINE_CONST_DICT(vgacap_globals, vgacap_globals_table);

const mp_obj_module_t vgacap_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&vgacap_globals,
};

MP_REGISTER_MODULE(MP_QSTR_vgacap, vgacap_module);
