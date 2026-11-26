// SPDX-License-Identifier: Apache-2.0
//
// vgacap-rp1: capture a Tiny Tapeout design's uo_out with the Raspberry Pi 5's
// RP1 PIO, into a vgacap stream.
//
// The sampler is the RP2's (ttcap/mp/capture_rp2.py) unchanged: wait for the
// project-clock edge, `in pins, 8`, autopush 4 samples a word, first sample in
// the top byte (VGACAP_FLAG_FIRST_SAMPLE_MSB). piolib's DMA moves the words
// into DRAM; the file gets the vgacap header and RAW chunks, which every
// vgacap reader already understands.
//
// --gen FILE adds a test fixture: a second state machine on the same PIO
// drives FILE's samples (one byte each, looped) onto the same 8 data pins with
// a clock it makes itself on the clock pin -- so the capture path can be
// proven on a Pi 5 with nothing wired to its header, and the picture compared
// pixel for pixel. The generator changes the data with the clock low and
// raises the clock D+1 PIO clocks later, so a rising-edge sampler reads data
// that has been stable for at least that long.
//
// Build on the Pi (32- or 64-bit userland) against raspberrypi/utils' piolib:
//   make -C capture-rp1 PIOLIB=/path/to/utils/piolib
//
// Usage:
//   vgacap-rp1 --out F --samples N [--data-base 4] [--clk 12] [--edge rising]
//              [--clock-hz HZ [--unproven-rate]] [--desc TEXT]
//              [--gen FILE [--gen-div D] [--gen-delay K]]

#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "hardware/pio.h"
#include "programs.h"
#include "vgacap/stream.h"

// One DMA transfer, and one RAW chunk. The rp1-pio driver's transfer size is
// a uint16_t (struct rp1_pio_sm_config_xfer_args.buf_size), so 64 KB is 0:
// with 65536 here the receive DMA ran unpaced, re-reading the RX FIFO and
// filling the capture with copies of one word (pi-sw2-p4, 2026-11-24).
#ifndef CHUNK_WORDS
#define CHUNK_WORDS 8192u
#endif
#define CHUNK_BYTES (4u * CHUNK_WORDS)
#ifndef RX_BUFFERS
#define RX_BUFFERS 4u                // queued DMA buffers: the rp1-pio driver refuses 8 (-EINVAL)
#endif
#define GEN_CHUNK_BYTES 32768u
// The fastest project clock proven lossless with the stock rp1-pio driver:
// the counter loopback stepped by exactly -1 for 4,194,304 samples at
// 500 k samples/s, and lost ~150-320 samples at every 32 KB buffer boundary
// from 1.25 M samples/s up (pi-sw2-p4, 2026-11-24; capture-rp1/explanation-capture.md).
// Nothing between the two was measured.
#define PROVEN_MAX_HZ 500000u
// TIME's dropped_samples when the capture cannot know it: the counter's
// largest value, so that a check for "nothing dropped" fails rather than
// passes. The stream format has no "unknown"; the message says it in words.
#define DROPPED_UNKNOWN UINT32_MAX
// RX DMACTRL: the rp1-pio driver's 0x80000101 (measured with rp1sm) with the
// request threshold raised from 1 word to the DMA's burst of 8.
#define RX_DMACTRL 0x80000108u
// TX needs no DMACTRL change, only the joined 8-deep FIFO: the driver's own
// threshold (0x40000104) on the default 4-deep FIFO let 8-word bursts land
// with too little room, losing half of each (the generated picture came back
// with 400 clocks a line instead of 800), and writing 8 or 1 into it wedged
// the channel ("dma2chan1 failed to stop"; a reboot to recover). Measured on
// pi-sw2-p4, 2026-11-24.
_Static_assert(CHUNK_WORDS * 4u <= 65535u, "rp1-pio transfer sizes are 16-bit");
_Static_assert(GEN_CHUNK_BYTES <= 65535u, "rp1-pio transfer sizes are 16-bit");

struct options {
    const char *out;
    uint64_t samples;
    uint data_base, clk;
    bool rising;
    uint32_t clock_hz;
    const char *desc;
    const char *gen;
    bool gen_counter;
    bool poll;
    bool unproven_rate;      // allow --clock-hz above PROVEN_MAX_HZ
    float gen_div;
    uint gen_delay;
};

struct generator {
    PIO pio;
    uint sm;
    const uint8_t *data;
    size_t len;              // bytes, a multiple of 4
    uint chunk;              // bytes per transfer; divides len exactly
    volatile bool stop;
    int error;               // the failing transfer's result; read after the join
};

static double now_s(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + ts.tv_nsec / 1e9;
}

static int file_write(void *user, const uint8_t *buf, size_t len)
{
    return fwrite(buf, 1, len, (FILE *)user) == len ? 0 : -1;
}

// Feeds the generator's TX FIFO from the looped sample buffer until stopped.
static void *generator_thread(void *arg)
{
    struct generator *g = arg;
    size_t pos = 0;
    while (!g->stop) {
        size_t n = g->chunk;
        int ret = pio_sm_xfer_data(g->pio, g->sm, PIO_DIR_TO_SM, (uint)n, (void *)(g->data + pos));
        if (ret) {
            g->error = ret;
            break;
        }
        pos += n;
        if (pos >= g->len)
            pos = 0;
    }
    return NULL;
}

static uint8_t *read_file(const char *path, size_t *len)
{
    FILE *fp = fopen(path, "rb");
    if (!fp) {
        fprintf(stderr, "vgacap-rp1: %s: %s\n", path, strerror(errno));
        return NULL;
    }
    long size = fseek(fp, 0, SEEK_END) ? -1 : ftell(fp);
    // An empty pattern has no transfer size that divides it: the search
    // below would settle on the largest and DMA that from a 4-byte buffer.
    if (size <= 0 || fseek(fp, 0, SEEK_SET)) {
        fprintf(stderr, "vgacap-rp1: %s: %s\n", path, size == 0 ? "empty" : "cannot find its size");
        fclose(fp);
        return NULL;
    }
    // Whole words, and a multiple of the transfer size's granularity.
    size_t padded = ((size_t)size + 3u) & ~(size_t)3u;
    uint8_t *buf = calloc(1, padded);
    if (!buf || fread(buf, 1, (size_t)size, fp) != (size_t)size) {
        fprintf(stderr, "vgacap-rp1: cannot read %s\n", path);
        fclose(fp);
        free(buf);
        return NULL;
    }
    fclose(fp);
    *len = padded;
    return buf;
}

static int parse(int argc, char **argv, struct options *o)
{
    *o = (struct options){.data_base = 4, .clk = 12, .rising = true, .gen_div = 1.0f, .gen_delay = 3,
                          .desc = "vgacap-rp1"};
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--out") && v) o->out = argv[++i];
        else if (!strcmp(a, "--samples") && v) o->samples = strtoull(argv[++i], NULL, 0);
        else if (!strcmp(a, "--data-base") && v) o->data_base = (uint)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(a, "--clk") && v) o->clk = (uint)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(a, "--edge") && v) {
            const char *e = argv[++i];
            if (!strcmp(e, "rising")) o->rising = true;
            else if (!strcmp(e, "falling")) o->rising = false;
            else return -1;
        } else if (!strcmp(a, "--clock-hz") && v) o->clock_hz = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(a, "--desc") && v) o->desc = argv[++i];
        else if (!strcmp(a, "--gen") && v) o->gen = argv[++i];
        else if (!strcmp(a, "--gen-counter")) o->gen_counter = true;
        else if (!strcmp(a, "--poll")) o->poll = true;
        else if (!strcmp(a, "--unproven-rate")) o->unproven_rate = true;
        else if (!strcmp(a, "--gen-div") && v) o->gen_div = strtof(argv[++i], NULL);
        else if (!strcmp(a, "--gen-delay") && v) o->gen_delay = (uint)strtoul(argv[++i], NULL, 0);
        else return -1;
    }
    if (!o->out || !o->samples || o->data_base + 8 > 28 || o->clk > 27 ||
        (o->clk >= o->data_base && o->clk < o->data_base + 8) || o->gen_delay > 15 || o->gen_div < 1.0f ||
        (o->gen && o->gen_counter))
        return -1;
    return 0;
}

// Writes the captured words as a vgacap stream; 0 on success.
// rate_proven: whether the declared clock is one the capture is known to
// keep up with; only then does the TIME chunk say nothing was dropped.
static int write_stream(const struct options *o, const uint32_t *buf, uint64_t skip_words, double secs,
                        bool rate_proven)
{
    FILE *fp = fopen(o->out, "wb");
    if (!fp) {
        fprintf(stderr, "vgacap-rp1: %s: %s\n", o->out, strerror(errno));
        return 1;
    }
    vgacap_header_t h;
    vgacap_header_init_tinyvga(&h, 8, 4, VGACAP_FLAG_FIRST_SAMPLE_MSB);
    h.mode = VGACAP_MODE_EXTCLK;
    h.clock_hz = o->clock_hz;
    snprintf(h.desc, sizeof h.desc, "%s", o->desc);
    h.desc_len = (uint8_t)strlen(h.desc);
    vgacap_writer_t w;
    int bad = vgacap_writer_init(&w, file_write, fp, &h);
    uint64_t left = o->samples;
    for (uint64_t pos = skip_words; !bad && left; pos += CHUNK_WORDS) {
        uint32_t n = left < 4u * CHUNK_WORDS ? (uint32_t)left : 4u * CHUNK_WORDS;
        bad = vgacap_writer_raw(&w, buf + pos, n);
        left -= n;
    }
    if (!bad) {
        char msg[128];
        snprintf(msg, sizeof msg, "rp1 samples=%llu seconds=%.3f samples_per_s=%.0f dropped=%s",
                 (unsigned long long)o->samples, secs, (double)o->samples / secs,
                 rate_proven ? "0" : "unknown");
        bad = vgacap_writer_time(&w, 0, o->clock_hz, rate_proven ? 0 : DROPPED_UNKNOWN, msg);
        printf("%s\n", msg);
    }
    if (fclose(fp) || bad) {
        fprintf(stderr, "vgacap-rp1: writing %s failed\n", o->out);
        return 1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    struct options o;
    if (parse(argc, argv, &o)) {
        fprintf(stderr,
                "usage: vgacap-rp1 --out F --samples N [--data-base 4] [--clk 12] "
                "[--edge rising|falling] [--clock-hz HZ [--unproven-rate]]\n"
                "                  [--desc TEXT] [--gen FILE | --gen-counter] [--gen-div D] [--gen-delay K<=15]\n");
        return 2;
    }
    // Above the proven rate samples are lost between buffers and nothing
    // here counts them, so such a capture is refused unless asked for, and
    // then its stream does not claim that nothing was dropped. The rate
    // checked is the one declared: --clock-hz is not measured.
    if (o.clock_hz > PROVEN_MAX_HZ && !o.unproven_rate) {
        fprintf(stderr,
                "vgacap-rp1: --clock-hz %u is above %u, the fastest rate proven lossless with the stock\n"
                "  rp1-pio driver: above it ~150-320 samples were lost at every 32 KB buffer, uncounted\n"
                "  (capture-rp1/explanation-capture.md). --unproven-rate captures anyway, recording dropped as unknown.\n",
                (unsigned)o.clock_hz, PROVEN_MAX_HZ);
        return 2;
    }
    bool rate_proven = o.clock_hz && o.clock_hz <= PROVEN_MAX_HZ;
    if (!rate_proven)
        fprintf(stderr,
                "vgacap-rp1: warning: %s, so samples may be lost between buffers uncounted;\n"
                "  the stream records its dropped count as unknown\n",
                o.clock_hz ? "--clock-hz is above the rate proven lossless"
                           : "no --clock-hz: the rate is not known to be one proven lossless");

    // The pattern is read, and the capture buffer allocated, before anything
    // is claimed, so neither failing costs a teardown.
    size_t gen_len = 0;
    uint8_t *gen_data = NULL;
    if (o.gen && !(gen_data = read_file(o.gen, &gen_len)))
        return 1;
    // The first DMA burst (8 words) of a capture is stale even after the
    // FIFOs are cleared -- measured with the counter generator: the first 32
    // samples broke the count, nothing after them did. So one burst more is
    // captured and dropped.
    const uint64_t skip_words = 8;
    // Whole buffers of whole words. Sized in uint64_t, then refused before
    // the size_t that malloc takes can wrap (a 32-bit userland's 4 GB).
    uint64_t words = o.samples / 4 + (o.samples % 4 != 0);
    if (words > SIZE_MAX / 4u - skip_words - CHUNK_WORDS) {
        fprintf(stderr, "vgacap-rp1: --samples %llu is more than this process can hold\n",
                (unsigned long long)o.samples);
        return 1;
    }
    words += skip_words;
    uint64_t chunks = (words + CHUNK_WORDS - 1) / CHUNK_WORDS;
    size_t buf_bytes = (size_t)chunks * CHUNK_BYTES;
    uint32_t *buf = malloc(buf_bytes);
    if (!buf) {
        fprintf(stderr, "vgacap-rp1: cannot allocate %zu bytes\n", buf_bytes);
        return 1;
    }

    PIO pio = pio0;
    if (PIO_IS_ERR(pio)) {
        fprintf(stderr, "vgacap-rp1: cannot open the RP1 PIO (/dev/pio0): %d\n", PIO_ERR_VAL(pio));
        return 1;
    }
    pio_select(pio);

    // Everything claimed from here on is released by the one teardown at the
    // end, whichever step fails: a state machine, DMA channel or program left
    // claimed by an early exit leaves the rp1-pio driver unusable until a
    // reboot (capture-rp1/explanation-capture.md). So every failure after this point sets
    // its message and jumps to `teardown`, and teardown undoes only what was
    // done, in the order below.
    int status = 1;                  // the exit status; 0 only once the stream is written
    bool captured = false;
    int ret = 0;                     // the receive DMA's (or --poll's) result
    double secs = 0;
    int cap_sm = -1, gen_sm = -1, cnt_sm = -1;
    bool cap_loaded = false, gen_loaded = false, cnt_loaded = false;
    bool gen_pins = false, cnt_pins = false, gen_started = false;
    uint cap_off = 0, gen_off = 0, cnt_off = 0;
    struct generator gen = {0};
    pthread_t gen_thread;
    uint16_t gen_insns[RP1_GENERATOR_LEN], cnt_insns[RP1_COUNTER_LEN];
    pio_program_t gen_prog = {.instructions = gen_insns, .length = RP1_GENERATOR_LEN, .origin = -1};
    pio_program_t cnt_prog = {.instructions = cnt_insns, .length = RP1_COUNTER_LEN, .origin = -1};

    // ---- the sampler: the RP2's program, word for word ------------------
    uint16_t cap_insns[RP1_SAMPLER_LEN];
    rp1_sampler_program(cap_insns, o.rising, o.clk);
    pio_program_t cap_prog = {.instructions = cap_insns, .length = RP1_SAMPLER_LEN, .origin = -1};
    if (getenv("VGACAP_RP1_DEBUG"))
        fprintf(stderr, "sampler: %04x %04x %04x\n", cap_insns[0], cap_insns[1], cap_insns[2]);
    cap_sm = pio_claim_unused_sm(pio, true);
    cap_off = pio_add_program(pio, &cap_prog);
    cap_loaded = true;
    pio_sm_config cc = pio_get_default_sm_config();
    sm_config_set_wrap(&cc, cap_off, cap_off + 2);
    sm_config_set_in_pins(&cc, o.data_base);
    sm_config_set_in_shift(&cc, false, true, 32);  // shift left: first sample in the top byte
    sm_config_set_fifo_join(&cc, PIO_FIFO_JOIN_RX);
    sm_config_set_clkdiv(&cc, 1.0f);
    // --poll: no receive DMA, read the FIFO word by word (slow; for checking
    // the programs without DMA in the way -- a DMA wait that times out
    // leaves the rp1-pio driver unusable until a reboot).
    int xret = o.poll ? 0 : pio_sm_config_xfer(pio, (uint)cap_sm, PIO_DIR_FROM_SM, CHUNK_BYTES, RX_BUFFERS);
    if (xret) {
        fprintf(stderr, "vgacap-rp1: cannot set up receive DMA (%u buffers of %u bytes): %d\n",
                RX_BUFFERS, CHUNK_BYTES, xret);
        goto teardown;
    }
    if (!o.poll) {
        // The rp1-pio driver sets the RX DMA request threshold to 1 word
        // (SM DMACTRL 0x80000101) while its DMA moves bursts of 8: each
        // request then read 1 real word and 7 stale copies of the FIFO
        // register, and the capture ran at 8x the signal's rate with every
        // word repeated (counting-pattern loopback, pi-sw2-p4, 2026-11-24).
        // Ask only when a whole burst is there: the 8-deep joined FIFO,
        // threshold 8.
        // The driver's own value with only the threshold (bits 4:0)
        // changed; this kernel's driver has no GET_DMACTRL ("PANIC: Failed
        // to get DMACTRL"), so it is written, not read-modified.
        pio_sm_set_dmactrl(pio, (uint)cap_sm, false, RX_DMACTRL);
    }
    pio_sm_init(pio, (uint)cap_sm, cap_off, &cc);

    // ---- the optional generator (test fixture) --------------------------
    if (o.gen) {
        rp1_generator_program(gen_insns, o.gen_delay);
        if (getenv("VGACAP_RP1_DEBUG"))
            fprintf(stderr, "generator: %04x %04x\n", gen_insns[0], gen_insns[1]);
        gen_sm = pio_claim_unused_sm(pio, true);
        gen_off = pio_add_program(pio, &gen_prog);
        gen_loaded = true;
        pio_sm_config gc = pio_get_default_sm_config();
        sm_config_set_wrap(&gc, gen_off, gen_off + 1);
        sm_config_set_out_pins(&gc, o.data_base, 8);
        sm_config_set_sideset(&gc, 1, false, false);
        sm_config_set_sideset_pins(&gc, o.clk);
        sm_config_set_out_shift(&gc, true, true, 32);  // shift right: byte 0 goes out first
        sm_config_set_fifo_join(&gc, PIO_FIFO_JOIN_TX);
        sm_config_set_clkdiv(&gc, o.gen_div);
        for (uint p = 0; p < 8; p++)
            pio_gpio_init(pio, o.data_base + p);
        pio_gpio_init(pio, o.clk);
        pio_sm_set_consecutive_pindirs(pio, (uint)gen_sm, o.data_base, 8, true);
        pio_sm_set_consecutive_pindirs(pio, (uint)gen_sm, o.clk, 1, true);
        gen_pins = true;
        // Every transfer is exactly the configured size: transfers shorter
        // than it (a 4 KB pattern on a 32 KB configuration) came out of the
        // PIO as a scrambled mix of words (pi-sw2-p4, 2026-11-24). So the
        // size is the largest multiple of 4 up to GEN_CHUNK_BYTES that
        // divides the pattern, and the pattern is sent in whole transfers.
        uint chunk = 0;
        for (uint c = GEN_CHUNK_BYTES; c >= 4; c -= 4)
            if (gen_len % c == 0) {
                chunk = c;
                break;
            }
        xret = chunk ? pio_sm_config_xfer(pio, (uint)gen_sm, PIO_DIR_TO_SM, chunk, 4) : -1;
        if (xret) {
            fprintf(stderr, "vgacap-rp1: cannot set up transmit DMA: %d\n", xret);
            goto teardown;
        }
        pio_sm_init(pio, (uint)gen_sm, gen_off, &gc);
        gen = (struct generator){.pio = pio, .sm = (uint)gen_sm, .data = gen_data, .len = gen_len, .chunk = chunk};
        int terr = pthread_create(&gen_thread, NULL, generator_thread, &gen);
        if (terr) {
            fprintf(stderr, "vgacap-rp1: cannot start the generator thread: %s\n", strerror(terr));
            goto teardown;
        }
        gen_started = true;
        pio_sm_set_enabled(pio, (uint)gen_sm, true);
    }

    // A generator that needs no host at all: the pins count down, one value
    // per clock. RP1's firmware interface serialises blocking calls ("Known
    // issues" in piolib's README), and feeding a generator from a second
    // thread while capturing interfered with the capture; this isolates the
    // capture path. A correct capture steps down by exactly 1 every sample.
    if (o.gen_counter) {
        cnt_sm = pio_claim_unused_sm(pio, true);
        // The jump target is program-relative (0); pio_add_program relocates
        // jmp instructions of a program loaded with origin -1, as the SDK's
        // does. (Loading, unloading and reloading at a fixed origin to learn
        // the offset first left the state machine running another
        // program's `pull block` -- rp1sm showed pc 0x18, outside it.)
        rp1_counter_program(cnt_insns, o.gen_delay);
        cnt_off = pio_add_program(pio, &cnt_prog);
        cnt_loaded = true;
        pio_sm_config kc = pio_get_default_sm_config();
        sm_config_set_wrap(&kc, cnt_off, cnt_off + 1);
        sm_config_set_out_pins(&kc, o.data_base, 8);
        sm_config_set_sideset(&kc, 1, false, false);
        sm_config_set_sideset_pins(&kc, o.clk);
        sm_config_set_clkdiv(&kc, o.gen_div);
        for (uint pin = 0; pin < 8; pin++)
            pio_gpio_init(pio, o.data_base + pin);
        pio_gpio_init(pio, o.clk);
        pio_sm_set_consecutive_pindirs(pio, (uint)cnt_sm, o.data_base, 8, true);
        pio_sm_set_consecutive_pindirs(pio, (uint)cnt_sm, o.clk, 1, true);
        cnt_pins = true;
        pio_sm_init(pio, (uint)cnt_sm, cnt_off, &kc);
        pio_sm_set_enabled(pio, (uint)cnt_sm, true);
    }

    // ---- capture --------------------------------------------------------
    // Nothing captured before this run may lead it: words left in the FIFO
    // came out as the first 8 of the stream (pi-sw2-p4, 2026-11-24).
    pio_sm_clear_fifos(pio, (uint)cap_sm);
    pio_sm_restart(pio, (uint)cap_sm);
    double t0 = now_s();
    pio_sm_set_enabled(pio, (uint)cap_sm, true);
    if (o.poll) {
        double deadline = now_s() + 10.0;
        for (uint64_t i = 0; i < words && !ret; ) {
            if (pio_sm_get_rx_fifo_level(pio, (uint)cap_sm)) {
                buf[i++] = pio_sm_get(pio, (uint)cap_sm);
            } else if (now_s() > deadline) {
                fprintf(stderr, "vgacap-rp1: --poll: only %llu of %llu words in 10 s\n",
                        (unsigned long long)i, (unsigned long long)words);
                ret = -1;
            }
        }
    } else {
        // One buffer per call. (piolib's rxsource example queues buffers
        // ahead with NULL transfers; this kernel's rp1-pio returns -EINVAL
        // for those.) The DMA is re-armed per buffer, and the 8-word FIFO
        // covers that gap only up to a rate: measured on pi-sw2-p4.
        for (uint64_t i = 0; i < chunks && !ret; i++)
            ret = pio_sm_xfer_data(pio, (uint)cap_sm, PIO_DIR_FROM_SM, CHUNK_BYTES, buf + i * CHUNK_WORDS);
    }
    secs = now_s() - t0;
    if (ret)
        fprintf(stderr, "vgacap-rp1: receive DMA failed: %d\n", ret);
    else
        captured = true;

teardown:
    // In an order that never leaves a DMA transfer waiting on a state
    // machine that has stopped: the generator thread is asked to stop and
    // finishes the transfer it is in (its state machine still running), and
    // only then are the state machines disabled and everything released.
    // Cancelling the thread inside the transfer instead left the kernel with
    // "DMA wait timed out ... dma2chan1 failed to stop", and the next run
    // could not talk to the RP1 at all (measured on pi-sw2-p4, 2026-11-24).
    if (gen_started) {
        gen.stop = true;
        pthread_join(gen_thread, NULL);
        // A generator that stopped feeding the pins mid-capture makes the
        // capture not the pattern it claims to be: fail the run.
        if (gen.error) {
            fprintf(stderr, "vgacap-rp1: generator transmit DMA failed: %d\n", gen.error);
            captured = false;
        }
        // The thread's last transfer has returned, but the DMA may still be
        // feeding the FIFO: wait until the state machine has taken it all,
        // so no transfer is in flight when the channel is released (one that
        // was left "dma2chan1 failed to stop" and the driver unusable).
        double until = now_s() + 2.0;
        while (!pio_sm_is_tx_fifo_empty(pio, (uint)gen_sm) && now_s() < until)
            ;
    }
    if (gen_sm >= 0) {
        pio_sm_set_enabled(pio, (uint)gen_sm, false);
        if (gen_pins) {
            // Leave the pins as found: inputs.
            pio_sm_set_consecutive_pindirs(pio, (uint)gen_sm, o.data_base, 8, false);
            pio_sm_set_consecutive_pindirs(pio, (uint)gen_sm, o.clk, 1, false);
        }
        if (gen_loaded)
            pio_remove_program(pio, &gen_prog, gen_off);
        pio_sm_unclaim(pio, (uint)gen_sm);
    }
    if (cnt_sm >= 0) {
        pio_sm_set_enabled(pio, (uint)cnt_sm, false);
        if (cnt_pins) {
            pio_sm_set_consecutive_pindirs(pio, (uint)cnt_sm, o.data_base, 8, false);
            pio_sm_set_consecutive_pindirs(pio, (uint)cnt_sm, o.clk, 1, false);
        }
        if (cnt_loaded)
            pio_remove_program(pio, &cnt_prog, cnt_off);
        pio_sm_unclaim(pio, (uint)cnt_sm);
    }
    if (cap_sm >= 0) {
        pio_sm_set_enabled(pio, (uint)cap_sm, false);
        if (cap_loaded)
            pio_remove_program(pio, &cap_prog, cap_off);
        pio_sm_unclaim(pio, (uint)cap_sm);
    }
    pio_close(pio);

    // ---- the vgacap stream ----------------------------------------------
    if (captured)
        status = write_stream(&o, buf, skip_words, secs, rate_proven);
    free(buf);
    free(gen_data);
    return status;
}
