// SPDX-License-Identifier: Apache-2.0
// The MicroPython module's half of the capture HAL: the host link is two
// MicroPython streams (chunks out to `out`, stop bytes in from `stop`), and
// idling runs MicroPython's own event handling.
//
// The capture half is someone else's: on the board firmware/pico/hal_rp2.c
// (PIO, DMA), in the unix test build firmware/sim/hal_sim.c (a simulated
// board, built with VGACAP_SIM_NO_LINK).
//
// Nothing raised by MicroPython may unwind through the capture engines:
// they hold the PIO and DMA, and only their own ending gives them back. So
// every call into MicroPython here is protected, and an exception is kept
// (vgacap_link_end() hands it back) and turned into what the engine
// understands: a failed write is a host that went away (nothing more is
// written), anything else is a stop request (the engine finishes the chunk
// it is on and writes its summary, as the scripts' `finally` does).
#include "hal_mpy.h"

#include "fw_capture.h"
#include "fw_hal.h"
#include "py/mphal.h"
#include "py/runtime.h"
#include "py/stream.h"

#if VGACAP_MPY_SIM
#include "sim.h"
#endif

#if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS
#include <poll.h>
#endif

static int write_failed;  // the out stream refused a write: the host is gone
static int stop_pending;  // something raised while idling: stop the capture

static void keep(mp_obj_t exc) {
    if (MP_STATE_PORT(vgacap_link)[2] == MP_OBJ_NULL) MP_STATE_PORT(vgacap_link)[2] = exc;
}

void vgacap_link_begin(mp_obj_t out, mp_obj_t stop) {
    MP_STATE_PORT(vgacap_link)[0] = out;
    MP_STATE_PORT(vgacap_link)[1] = stop;
    MP_STATE_PORT(vgacap_link)[2] = MP_OBJ_NULL;
    write_failed = 0;
    stop_pending = 0;
}

mp_obj_t vgacap_link_end(void) {
    mp_obj_t exc = MP_STATE_PORT(vgacap_link)[2];
    for (int i = 0; i < 3; i++) MP_STATE_PORT(vgacap_link)[i] = MP_OBJ_NULL;
    return exc;
}

// One byte from the stop stream, or -1. Never blocks: the stream is
// polled first, as prelude.py's stop_requested() polls stdin.
int hal_read_byte(void) {
    if (stop_pending) return FW_STOP_BYTE;
    mp_obj_t stop = MP_STATE_PORT(vgacap_link)[1];
    if (stop == MP_OBJ_NULL || stop == mp_const_none) return -1;
    int c = -1;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        const mp_stream_p_t *p = mp_get_stream(stop);
        int err;
        mp_uint_t ready = p->ioctl(stop, MP_STREAM_POLL, MP_STREAM_POLL_RD, &err);
#if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS
        // A port whose select polls file descriptors itself (unix) leaves
        // MP_STREAM_POLL out of its files: poll the descriptor here too.
        if (ready == MP_STREAM_ERROR) {
            mp_uint_t fd = p->ioctl(stop, MP_STREAM_GET_FILENO, 0, &err);
            struct pollfd pfd = { .fd = (int)fd, .events = POLLIN };
            ready = fd != MP_STREAM_ERROR && poll(&pfd, 1, 0) > 0 && (pfd.revents & POLLIN) ? MP_STREAM_POLL_RD : 0;
        }
#endif
        if (ready != MP_STREAM_ERROR && (ready & MP_STREAM_POLL_RD)) {
            uint8_t b;
            if (p->read(stop, &b, 1, &err) == 1) c = b;
        }
        nlr_pop();
    } else {
        keep(MP_OBJ_FROM_PTR(nlr.ret_val));
        stop_pending = 1;
        c = FW_STOP_BYTE;
    }
    return c;
}

int hal_write(const uint8_t *buf, size_t len) {
    if (write_failed) return -1;
    mp_obj_t out = MP_STATE_PORT(vgacap_link)[0];
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        int err = 0;
        mp_uint_t n = mp_stream_rw(out, (void *)buf, len, &err, MP_STREAM_RW_WRITE);
        nlr_pop();
        if (err || n != len) {
            keep(mp_obj_new_exception_arg1(&mp_type_OSError, MP_OBJ_NEW_SMALL_INT(err ? err : MP_EIO)));
            write_failed = 1;
            return -1;
        }
    } else {
        keep(MP_OBJ_FROM_PTR(nlr.ret_val));
        write_failed = 1;
        return -1;
    }
#if VGACAP_MPY_SIM
    sim_link_sent(len);
#endif
    return 0;
}

// The stream's flush, where it has one (a file does; the board's stdout
// sends as it goes and answers EINVAL, which is fine).
void hal_flush(void) {
    if (write_failed) return;
    mp_obj_t out = MP_STATE_PORT(vgacap_link)[0];
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        const mp_stream_p_t *p = mp_get_stream(out);
        int err;
        if (p->ioctl) p->ioctl(out, MP_STREAM_FLUSH, 0, &err);
        nlr_pop();
    } else {
        keep(MP_OBJ_FROM_PTR(nlr.ret_val));
        write_failed = 1;
    }
}

int hal_host_gone(void) { return write_failed; }

// The engines' command-loop hook; there is no command loop here.
void hal_wait_input(void) {}

#if !VGACAP_MPY_SIM
#include "hal_rp2.h"

// Between buffers: let MicroPython run what is pending (its scheduler,
// soft timers such as the ttboard SDK's, USB), as the scripts' wait loop
// does between bytecodes.
void hal_idle(void) {
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_event_handle_nowait();
        nlr_pop();
    } else {
        keep(MP_OBJ_FROM_PTR(nlr.ret_val));
        stop_pending = 1;
    }
}

// prelude.py new_state_machine(): clear MicroPython's programs from the
// block (never block 0: the stock firmware's FPGA loader lives there, and
// pio_set_gpio_base() then refuses the move, which is the refusal wanted),
// then take the state machine through rp2.StateMachine(id), which claims
// it for MicroPython or raises if something else has.
int hal_rp2_pio_prepare(unsigned pio, unsigned sm, const char **why) {
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_obj_t rp2 = mp_import_name(MP_QSTR__rp2, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
        if (pio != 0) {
            mp_obj_t block = mp_call_function_1(mp_load_attr(rp2, MP_QSTR_PIO), MP_OBJ_NEW_SMALL_INT(pio));
            mp_obj_t dest[2];
            mp_load_method(block, MP_QSTR_remove_program, dest);
            mp_call_method_n_kw(0, 0, dest);
        }
        mp_call_function_1(mp_load_attr(rp2, MP_QSTR_StateMachine), MP_OBJ_NEW_SMALL_INT(pio * 4u + sm));
        nlr_pop();
        return 0;
    }
    // The exception is the better message; the module raises it.
    keep(MP_OBJ_FROM_PTR(nlr.ret_val));
    *why = "MicroPython would not hand over that PIO state machine";
    return -1;
}
#endif
