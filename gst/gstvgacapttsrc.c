/* SPDX-License-Identifier: Apache-2.0 */
/**
 * SECTION:element-vgacapttsrc
 *
 * Runs a live capture on a Tiny Tapeout board and pushes the capture stream
 * as `application/x-vgacap` buffers.
 *
 * ## Example
 * |[
 * gst-launch-1.0 vgacapttsrc link=serial:/dev/ttyACM0 \
 *     project=tt_um_rejunity_vga clock-hz=60000 ! vgadecode ! \
 *     videoconvert ! autovideosink
 * ]|
 *
 * ## Stopping
 *
 * This is the part worth reading. The board samples into a DMA double buffer
 * and can only stop at a buffer boundary, so a capture takes up to one buffer
 * to wind down - about 82 ms at 750 kHz, but 2.2 s at the RP2040's 60 kHz
 * project-clock ceiling, where a buffer is a long time. `ttcap capture
 * --out -` turns its first %SIGINT into a cooperative stop: the board
 * finishes the chunk it is filling, writes its closing `TIME` chunk (the only
 * overrun and RXSTALL report there is), and the process exits 0 with a
 * well-formed stream behind it. A capture that is merely killed ends mid
 * chunk with no trailer.
 *
 * So #GstBaseSrc's `stop()` here does, in order:
 *
 * 1. %SIGINT to the child's process group.
 * 2. Wait for the child, **while draining and discarding its stdout**. This
 *    matters as much as the signal: our read end of the pipe is 64 KiB, the
 *    streaming thread has already stopped reading, and a child blocked in
 *    `write()` on a full pipe never reaches the code that emits its trailer.
 *    Draining is what lets a patient stop actually be patient.
 * 3. If #GstVgaCapTtSrc:stop-timeout expires, close our read end. `ttcap`
 *    treats the resulting %EPIPE as a clean end of stream and still exits 0,
 *    and unlike a signal this needs no handler to be running - it is the
 *    stronger nudge, which is why it comes second rather than first. It is
 *    not first because it cannot be taken back: once the pipe is shut the
 *    trailer has nowhere to go, so the stream ends a chunk short of tidy.
 * 4. Still alive: %SIGKILL the group, and reap. The child is reaped in every
 *    path, so the element never leaves a zombie behind.
 *
 * ## Failures
 *
 * The child's stderr is read by a helper thread, logged line by line at
 * %GST_LEVEL_INFO, and the last few lines are kept. A child that exits
 * non-zero raises a %GST_ELEMENT_ERROR quoting them, so a capture that fails
 * on the board - no such device, no project of that name, a board that never
 * reached the REPL - surfaces as a pipeline error rather than an unexplained
 * end of stream.
 */
#include "gstvgacapttsrc.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

GST_DEBUG_CATEGORY_STATIC(vgacapttsrc_debug);
#define GST_CAT_DEFAULT vgacapttsrc_debug

enum {
    PROP_0,
    PROP_LINK,
    PROP_PROJECT,
    PROP_DESIGN,
    PROP_CLOCK_HZ,
    PROP_PROFILE,
    PROP_PIO,
    PROP_BUF_WORDS,
    PROP_SECONDS,
    PROP_TTCAP_COMMAND,
    PROP_STOP_TIMEOUT
};

#define DEFAULT_TTCAP_COMMAND "uv run --no-sync ttcap"
#define DEFAULT_PROFILE "auto"
#define DEFAULT_SECONDS 0.0
#define DEFAULT_STOP_TIMEOUT 15.0
/* -1 on the two board-tuning integers means "say nothing and let ttcap pick",
 * so its defaults are not copied into C where they would quietly drift. */
#define DEFAULT_PIO (-1)
#define DEFAULT_BUF_WORDS (-1)
#define DEFAULT_BLOCKSIZE 65536

/* The stderr tail kept for an error message. Fixed storage, written from the
 * stderr thread and read when something goes wrong: no allocation, and no
 * unbounded growth from a chatty child. */
#define ERR_TAIL_LINES 8
#define ERR_LINE_MAX 200

/* How often the stop sequence looks at the child while it is winding down.
 * Short enough that a prompt exit is noticed promptly, long enough that the
 * wait is not a spin. */
#define STOP_POLL_MS 20
/* Grace after the two escalations that are not supposed to need long. */
#define ESCALATION_GRACE_S 2.0

struct _GstVgaCapTtSrc {
    GstPushSrc parent;

    /* properties, guarded by the object lock */
    gchar *link;
    gchar *project;
    gchar *design;
    gchar *profile;
    gchar *ttcap_command;
    guint clock_hz;
    gint pio;
    gint buf_words;
    gdouble seconds;
    gdouble stop_timeout;

    /* latched at start(), so a property set mid-capture cannot change the
     * command that is already running or the deadline a stop is measured by */
    gdouble active_stop_timeout;
    gchar *cmdline; /* the child's argv, joined, for log and error messages */

    /* the child; touched by the streaming thread and by start()/stop(), which
     * never run at the same time (GstBaseSrc stops its task before stop()) */
    GPid pid;
    gboolean have_child;
    gboolean reaped;
    gint exit_status; /* the reaped child's wait status, kept for later asks */
    gint out_fd;
    gint err_fd;
    gboolean out_eof;
    gboolean stopping; /* our own stop() is under way: an EOF is expected */
    gboolean reported; /* the child's exit has already been reported */

    /* unlock(): a self-pipe, so the blocking read in create() can be woken
     * for a flush or a state change without waiting for the board */
    gint wake_fds[2];

    GThread *err_thread;
    GMutex err_lock;
    gchar err_lines[ERR_TAIL_LINES][ERR_LINE_MAX];
    guint err_head;  /* next slot to write */
    guint err_count; /* lines ever written */

    GstBufferPool *pool;
    guint blocksize;
    guint64 offset;
};

G_DEFINE_TYPE(GstVgaCapTtSrc, gst_vgacapttsrc, GST_TYPE_PUSH_SRC)

static GstStaticPadTemplate src_template = GST_STATIC_PAD_TEMPLATE(
    "src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS("application/x-vgacap"));

/* ------------------------------------------------------------- stderr tail */

static void remember_err_line(GstVgaCapTtSrc *self, const gchar *line)
{
    g_mutex_lock(&self->err_lock);
    g_strlcpy(self->err_lines[self->err_head], line, ERR_LINE_MAX);
    self->err_head = (self->err_head + 1) % ERR_TAIL_LINES;
    self->err_count++;
    g_mutex_unlock(&self->err_lock);
}

/* The kept lines, oldest first, as one newline-separated string. Only called
 * when something has already gone wrong, so allocating here is free. */
static gchar *err_tail(GstVgaCapTtSrc *self)
{
    GString *out = g_string_new(NULL);
    guint kept, i;

    g_mutex_lock(&self->err_lock);
    kept = MIN(self->err_count, ERR_TAIL_LINES);
    for (i = 0; i < kept; i++) {
        guint idx = (self->err_head + ERR_TAIL_LINES - kept + i) % ERR_TAIL_LINES;
        g_string_append_printf(out, "%s%s", i ? "\n" : "", self->err_lines[idx]);
    }
    g_mutex_unlock(&self->err_lock);

    if (out->len == 0)
        g_string_append(out, "(the child wrote nothing to stderr)");
    return g_string_free(out, FALSE);
}

static gpointer err_thread_func(gpointer data)
{
    GstVgaCapTtSrc *self = (GstVgaCapTtSrc *)data;
    gchar line[ERR_LINE_MAX];
    gchar chunk[512];
    gsize len = 0;
    gssize i;

    for (;;) {
        gssize got = read(self->err_fd, chunk, sizeof chunk);
        if (got < 0 && errno == EINTR)
            continue;
        if (got <= 0)
            break;
        for (i = 0; i < got; i++) {
            gchar c = chunk[i];
            gboolean overlong = (len + 1 >= sizeof line);
            if (c != '\n' && c != '\r' && !overlong) {
                line[len++] = c;
                continue;
            }
            if (len > 0) {
                line[len] = '\0';
                GST_INFO_OBJECT(self, "ttcap: %s", line);
                remember_err_line(self, line);
                len = 0;
            }
            /* An overlong line is split, not dropped: keep the character
             * that would not fit as the start of the next piece. */
            if (overlong && c != '\n' && c != '\r')
                line[len++] = c;
        }
    }
    if (len > 0) {
        line[len] = '\0';
        GST_INFO_OBJECT(self, "ttcap: %s", line);
        remember_err_line(self, line);
    }
    return NULL;
}

/* ------------------------------------------------------------- the child */

/* Runs in the child between fork and exec, so only async-signal-safe calls.
 * The child gets a process group of its own for two reasons: a signal sent to
 * the group reaches `ttcap` even though `uv run` is in between, and a Ctrl-C
 * on a shared terminal then goes to us alone, so the cooperative stop below
 * is the only thing ending the capture rather than racing a second SIGINT. */
static void child_setup(gpointer user_data)
{
    (void)user_data;
    (void)setpgid(0, 0);
}

static void argv_add(GPtrArray *argv, const gchar *flag, const gchar *value)
{
    g_ptr_array_add(argv, g_strdup(flag));
    g_ptr_array_add(argv, g_strdup(value));
}

static void argv_add_int(GPtrArray *argv, const gchar *flag, gint64 value)
{
    g_ptr_array_add(argv, g_strdup(flag));
    g_ptr_array_add(argv, g_strdup_printf("%" G_GINT64_FORMAT, value));
}

/* The child's argv, NULL-terminated, or NULL with @error set. */
static gchar **build_argv(GstVgaCapTtSrc *self, GError **error)
{
    GPtrArray *argv = g_ptr_array_new_with_free_func(g_free);
    gchar **words = NULL;
    gint word_count = 0, i;
    gchar seconds_text[G_ASCII_DTOSTR_BUF_SIZE];

    GST_OBJECT_LOCK(self);
    if (!self->link || !*self->link) {
        GST_OBJECT_UNLOCK(self);
        g_ptr_array_unref(argv);
        g_set_error_literal(error, GST_LIBRARY_ERROR, GST_LIBRARY_ERROR_SETTINGS,
                            "the link property is not set (serial:/dev/ttyACM0 or "
                            "ws://host:8765/serial)");
        return NULL;
    }
    if (self->clock_hz == 0) {
        GST_OBJECT_UNLOCK(self);
        g_ptr_array_unref(argv);
        g_set_error_literal(error, GST_LIBRARY_ERROR, GST_LIBRARY_ERROR_SETTINGS,
                            "the clock-hz property is not set; ttcap needs a project "
                            "clock to program");
        return NULL;
    }
    if (!g_shell_parse_argv(self->ttcap_command, &word_count, &words, error)) {
        GST_OBJECT_UNLOCK(self);
        g_ptr_array_unref(argv);
        return NULL;
    }
    for (i = 0; i < word_count; i++)
        g_ptr_array_add(argv, g_strdup(words[i]));

    g_ptr_array_add(argv, g_strdup("capture"));
    g_ptr_array_add(argv, g_strdup(self->link));
    argv_add(argv, "--out", "-");
    argv_add_int(argv, "--clock-hz", self->clock_hz);
    if (self->project && *self->project)
        argv_add(argv, "--project", self->project);
    if (self->design && *self->design)
        argv_add(argv, "--design", self->design);
    if (self->profile && *self->profile)
        argv_add(argv, "--profile", self->profile);
    if (self->pio >= 0)
        argv_add_int(argv, "--pio", self->pio);
    if (self->buf_words > 0)
        argv_add_int(argv, "--buf-words", self->buf_words);
    /* Always passed: 0 is meaningful (run until stopped) and is not ttcap's
     * own default. g_ascii_formatd, because a locale with a decimal comma
     * would otherwise hand argparse something it cannot parse. */
    g_ascii_formatd(seconds_text, sizeof seconds_text, "%g", self->seconds);
    argv_add(argv, "--seconds", seconds_text);
    GST_OBJECT_UNLOCK(self);

    g_strfreev(words);
    g_ptr_array_add(argv, NULL);
    return (gchar **)g_ptr_array_free(argv, FALSE);
}

/* waitpid() without blocking. TRUE once the child has been reaped. */
static gboolean try_reap(GstVgaCapTtSrc *self, gint *status)
{
    pid_t got;

    if (!self->have_child)
        return FALSE;
    if (self->reaped) {
        *status = self->exit_status;
        return TRUE;
    }
    do {
        got = waitpid(self->pid, status, WNOHANG);
    } while (got < 0 && errno == EINTR);
    if (got == self->pid) {
        self->reaped = TRUE;
        self->exit_status = *status;
        return TRUE;
    }
    if (got < 0) {
        /* Someone else reaped it (nobody should have) or it was never ours;
         * either way there is nothing left to wait for. */
        GST_WARNING_OBJECT(self, "waitpid(%d) failed: %s", (int)self->pid, g_strerror(errno));
        self->reaped = TRUE;
        self->exit_status = 0;
        *status = 0;
        return TRUE;
    }
    return FALSE;
}

/* Read and throw away whatever the child has written, so it is never blocked
 * on a full pipe while we are waiting for it to finish. Also the wait itself:
 * it blocks in poll() for at most @timeout_ms. */
static void drain_stdout(GstVgaCapTtSrc *self, gint timeout_ms)
{
    gchar scratch[4096];
    struct pollfd pfd;
    gssize got;

    if (self->out_fd < 0 || self->out_eof) {
        g_usleep((gulong)timeout_ms * 1000);
        return;
    }
    pfd.fd = self->out_fd;
    pfd.events = POLLIN;
    pfd.revents = 0;
    if (poll(&pfd, 1, timeout_ms) <= 0)
        return;
    do {
        got = read(self->out_fd, scratch, sizeof scratch);
    } while (got < 0 && errno == EINTR);
    if (got == 0)
        self->out_eof = TRUE;
    else if (got < 0 && errno != EAGAIN && errno != EWOULDBLOCK)
        self->out_eof = TRUE;
}

/* Wait up to @timeout_s for the child, draining its stdout meanwhile. */
static gboolean wait_for_child(GstVgaCapTtSrc *self, gdouble timeout_s, gint *status,
                               gboolean drain)
{
    gint64 deadline = g_get_monotonic_time() + (gint64)(timeout_s * G_TIME_SPAN_SECOND);

    for (;;) {
        gint64 left;
        if (try_reap(self, status))
            return TRUE;
        left = deadline - g_get_monotonic_time();
        if (left <= 0)
            return try_reap(self, status);
        if (drain)
            drain_stdout(self, STOP_POLL_MS);
        else
            g_usleep(STOP_POLL_MS * 1000);
    }
}

static void signal_child(GstVgaCapTtSrc *self, int sig)
{
    if (!self->have_child || self->reaped)
        return;
    /* The negative pid is the process group made in child_setup(): `uv run`
     * and the `ttcap` it execs are both in it, and a signal to only the
     * former would leave the latter holding the board. */
    if (kill(-self->pid, sig) < 0 && errno == ESRCH)
        (void)kill(self->pid, sig);
}

/* A one-line account of how a child ended, for a log or an error message. */
static gchar *describe_status(gint status)
{
    if (WIFEXITED(status))
        return g_strdup_printf("exit status %d", WEXITSTATUS(status));
    if (WIFSIGNALED(status))
        return g_strdup_printf("killed by signal %d (%s)", WTERMSIG(status),
                               g_strsignal(WTERMSIG(status)));
    return g_strdup_printf("wait status 0x%x", (unsigned)status);
}

static gboolean status_is_clean(gint status)
{
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

/* The stop sequence documented at the top of this file. */
static void stop_child(GstVgaCapTtSrc *self)
{
    gint status = 0;
    gint64 began;
    gboolean gone;
    gboolean killed = FALSE;

    if (!self->have_child)
        return;

    self->stopping = TRUE;
    began = g_get_monotonic_time();

    /* 1. Ask. 2. Wait, draining, so the board's last chunk and its trailer
     *    have somewhere to go. */
    signal_child(self, SIGINT);
    gone = wait_for_child(self, self->active_stop_timeout, &status, TRUE);

    /* 3. Shut the pipe. ttcap reads EPIPE as "the consumer has gone" and
     *    still exits 0, and this needs no signal handler to be running. */
    if (!gone) {
        GST_WARNING_OBJECT(self,
                           "the capture did not end within stop-timeout (%.1f s); "
                           "closing the pipe", self->active_stop_timeout);
        if (self->out_fd >= 0) {
            close(self->out_fd);
            self->out_fd = -1;
            self->out_eof = TRUE;
        }
        gone = wait_for_child(self, ESCALATION_GRACE_S, &status, FALSE);
    }

    /* 4. Kill, and reap regardless: no zombies. */
    if (!gone) {
        GST_WARNING_OBJECT(self, "killing the capture process group");
        killed = TRUE;
        signal_child(self, SIGKILL);
        gone = wait_for_child(self, ESCALATION_GRACE_S, &status, FALSE);
        if (!gone)
            GST_ERROR_OBJECT(self, "the capture process %d will not die",
                             (int)self->pid);
    }

    if (gone) {
        gchar *how = describe_status(status);
        gdouble took = (gdouble)(g_get_monotonic_time() - began) / G_TIME_SPAN_SECOND;
        GST_INFO_OBJECT(self, "capture stopped after %.2f s: %s", took, how);
        /* A bad status we caused ourselves is not news. One we did not is
         * reported, but only as a warning: by now the pipeline is on its way
         * down and an error message would arrive after the bus has stopped
         * being watched. The live path is create(), below. */
        if (!status_is_clean(status) && !killed && !self->reported) {
            gchar *tail = err_tail(self);
            GST_ELEMENT_WARNING(self, RESOURCE, READ,
                                ("the capture ended badly (%s): %s", how, tail), (NULL));
            g_free(tail);
        }
        self->reported = TRUE;
        g_free(how);
    }

    g_spawn_close_pid(self->pid);
    self->have_child = FALSE;
}

/* ------------------------------------------------------- start/stop/create */

static gboolean gst_vgacapttsrc_stop(GstBaseSrc *bsrc);

static void close_fd(gint *fd)
{
    if (*fd >= 0) {
        close(*fd);
        *fd = -1;
    }
}

static gboolean make_wake_pipe(GstVgaCapTtSrc *self)
{
    int i;

    if (pipe(self->wake_fds) != 0) {
        GST_ELEMENT_ERROR(self, RESOURCE, OPEN_READ_WRITE,
                          ("cannot create the wake-up pipe: %s", g_strerror(errno)),
                          (NULL));
        self->wake_fds[0] = self->wake_fds[1] = -1;
        return FALSE;
    }
    /* Close-on-exec so the child never inherits it, and non-blocking so
     * draining it in unlock_stop() cannot hang the streaming thread. */
    for (i = 0; i < 2; i++) {
        (void)fcntl(self->wake_fds[i], F_SETFD, FD_CLOEXEC);
        (void)fcntl(self->wake_fds[i], F_SETFL,
                    fcntl(self->wake_fds[i], F_GETFL, 0) | O_NONBLOCK);
    }
    return TRUE;
}

static gboolean gst_vgacapttsrc_start(GstBaseSrc *bsrc)
{
    GstVgaCapTtSrc *self = GST_VGACAPTTSRC(bsrc);
    GError *error = NULL;
    gchar **argv;
    GstCaps *caps;
    GstStructure *config;

    GST_OBJECT_LOCK(self);
    self->active_stop_timeout = self->stop_timeout;
    GST_OBJECT_UNLOCK(self);
    self->blocksize = gst_base_src_get_blocksize(bsrc);
    if (self->blocksize == 0)
        self->blocksize = DEFAULT_BLOCKSIZE;
    self->offset = 0;
    self->out_eof = FALSE;
    self->stopping = FALSE;
    self->reported = FALSE;
    self->reaped = FALSE;
    self->exit_status = 0;
    self->err_head = 0;
    self->err_count = 0;

    argv = build_argv(self, &error);
    if (!argv) {
        GST_ELEMENT_ERROR(self, LIBRARY, SETTINGS, ("%s", error->message), (NULL));
        g_clear_error(&error);
        return FALSE;
    }
    g_free(self->cmdline);
    self->cmdline = g_strjoinv(" ", argv);

    if (!make_wake_pipe(self)) {
        g_strfreev(argv);
        return FALSE;
    }

    GST_INFO_OBJECT(self, "spawning: %s", self->cmdline);
    /* g_spawn_async_with_pipes rather than GSubprocess: the stop sequence
     * needs the raw pid, to signal a process group and to waitpid() against a
     * deadline. GSubprocess owns the reaping (its wait is either blocking
     * with no timeout or asynchronous against a GMainContext nobody is
     * iterating in a streaming thread) and hands out no pid to signal a group
     * with. G_SPAWN_DO_NOT_REAP_CHILD keeps the status ours to read. */
    if (!g_spawn_async_with_pipes(NULL, argv, NULL,
                                  G_SPAWN_DO_NOT_REAP_CHILD | G_SPAWN_SEARCH_PATH,
                                  child_setup, NULL, &self->pid, NULL, &self->out_fd,
                                  &self->err_fd, &error)) {
        GST_ELEMENT_ERROR(self, RESOURCE, NOT_FOUND,
                          ("cannot run the capture command: %s", error->message),
                          ("argv: %s", self->cmdline));
        g_clear_error(&error);
        g_strfreev(argv);
        close_fd(&self->wake_fds[0]);
        close_fd(&self->wake_fds[1]);
        return FALSE;
    }
    g_strfreev(argv);
    self->have_child = TRUE;

    self->err_thread = g_thread_new("vgacapttsrc-stderr", err_thread_func, self);

    /* One pool of blocksize buffers, so the streaming path allocates nothing
     * per buffer: acquire, read into it, resize to what arrived, push. */
    caps = gst_caps_new_empty_simple("application/x-vgacap");
    self->pool = gst_buffer_pool_new();
    config = gst_buffer_pool_get_config(self->pool);
    gst_buffer_pool_config_set_params(config, caps, self->blocksize, 4, 0);
    gst_caps_unref(caps);
    if (!gst_buffer_pool_set_config(self->pool, config) ||
        !gst_buffer_pool_set_active(self->pool, TRUE)) {
        GST_ELEMENT_ERROR(self, RESOURCE, NO_SPACE_LEFT,
                          ("cannot configure the %u-byte buffer pool", self->blocksize),
                          (NULL));
        /* GstBaseSrc does not call stop() after a start() that failed, so a
         * failure this side of the spawn has to tidy the child away itself or
         * the capture would run on with nobody reading it. */
        gst_vgacapttsrc_stop(bsrc);
        return FALSE;
    }
    return TRUE;
}

static gboolean gst_vgacapttsrc_stop(GstBaseSrc *bsrc)
{
    GstVgaCapTtSrc *self = GST_VGACAPTTSRC(bsrc);

    stop_child(self);

    /* Joined only once the child is gone, so the write end of the pipe is
     * shut and the thread's read() has returned 0 of its own accord. */
    if (self->err_thread) {
        g_thread_join(self->err_thread);
        self->err_thread = NULL;
    }
    close_fd(&self->out_fd);
    close_fd(&self->err_fd);
    close_fd(&self->wake_fds[0]);
    close_fd(&self->wake_fds[1]);

    if (self->pool) {
        gst_buffer_pool_set_active(self->pool, FALSE);
        gst_object_unref(self->pool);
        self->pool = NULL;
    }
    return TRUE;
}

/* GstBaseSrc calls unlock() to get the streaming thread out of a blocking
 * create(); the self-pipe is what makes that possible without waiting for the
 * board to produce another byte. */
static gboolean gst_vgacapttsrc_unlock(GstBaseSrc *bsrc)
{
    GstVgaCapTtSrc *self = GST_VGACAPTTSRC(bsrc);
    const guint8 one = 1;
    gssize written;

    if (self->wake_fds[1] >= 0) {
        written = write(self->wake_fds[1], &one, 1);
        (void)written; /* a full wake pipe is already a pending wake-up */
    }
    return TRUE;
}

static gboolean gst_vgacapttsrc_unlock_stop(GstBaseSrc *bsrc)
{
    GstVgaCapTtSrc *self = GST_VGACAPTTSRC(bsrc);
    guint8 scratch[64];

    if (self->wake_fds[0] >= 0)
        while (read(self->wake_fds[0], scratch, sizeof scratch) > 0)
            ;
    return TRUE;
}

/* The child's stdout has ended. Reap it and say what that means. */
static GstFlowReturn child_finished(GstVgaCapTtSrc *self)
{
    gint status = 0;
    gchar *how, *tail;

    if (self->stopping)
        return GST_FLOW_FLUSHING;
    if (!wait_for_child(self, self->active_stop_timeout, &status, FALSE)) {
        /* stdout shut but the process lingers: end the stream and leave the
         * corpse to stop(), which kills and reaps it. */
        GST_WARNING_OBJECT(self, "the capture closed its output but is still running");
        return GST_FLOW_EOS;
    }
    if (status_is_clean(status)) {
        GST_INFO_OBJECT(self, "the capture finished after %" G_GUINT64_FORMAT " bytes",
                        self->offset);
        self->reported = TRUE;
        return GST_FLOW_EOS;
    }

    how = describe_status(status);
    tail = err_tail(self);
    GST_ELEMENT_ERROR(self, RESOURCE, READ,
                      ("the capture failed (%s): %s", how, tail),
                      ("argv: %s; %" G_GUINT64_FORMAT " bytes were captured",
                       self->cmdline, self->offset));
    g_free(how);
    g_free(tail);
    self->reported = TRUE;
    return GST_FLOW_ERROR;
}

static GstFlowReturn gst_vgacapttsrc_create(GstPushSrc *psrc, GstBuffer **outbuf)
{
    GstVgaCapTtSrc *self = GST_VGACAPTTSRC(psrc);
    GstBuffer *buf = NULL;
    GstMapInfo map;
    GstFlowReturn flow;
    gssize got;

    if (self->out_fd < 0)
        return GST_FLOW_EOS;

    /* Wait for bytes or for unlock(). One read per buffer, not a loop that
     * fills the whole 64 KiB: this is a live source and a chunk that has
     * arrived should be on its way downstream, not held back waiting for the
     * board's next DMA buffer. */
    for (;;) {
        struct pollfd pfd[2];
        gint ready;

        pfd[0].fd = self->wake_fds[0];
        pfd[0].events = POLLIN;
        pfd[0].revents = 0;
        pfd[1].fd = self->out_fd;
        pfd[1].events = POLLIN;
        pfd[1].revents = 0;
        ready = poll(pfd, 2, -1);
        if (ready < 0) {
            if (errno == EINTR)
                continue;
            GST_ELEMENT_ERROR(self, RESOURCE, READ,
                              ("cannot wait on the capture: %s", g_strerror(errno)),
                              (NULL));
            return GST_FLOW_ERROR;
        }
        if (pfd[0].revents)
            return GST_FLOW_FLUSHING;
        if (pfd[1].revents)
            break;
    }

    flow = gst_buffer_pool_acquire_buffer(self->pool, &buf, NULL);
    if (flow != GST_FLOW_OK)
        return flow;
    if (!gst_buffer_map(buf, &map, GST_MAP_WRITE)) {
        gst_buffer_unref(buf);
        return GST_FLOW_ERROR;
    }
    do {
        got = read(self->out_fd, map.data, map.size);
    } while (got < 0 && errno == EINTR);
    gst_buffer_unmap(buf, &map);

    if (got < 0) {
        gst_buffer_unref(buf);
        if (self->stopping)
            return GST_FLOW_FLUSHING;
        GST_ELEMENT_ERROR(self, RESOURCE, READ,
                          ("cannot read the capture: %s", g_strerror(errno)), (NULL));
        return GST_FLOW_ERROR;
    }
    if (got == 0) {
        gst_buffer_unref(buf);
        self->out_eof = TRUE;
        return child_finished(self);
    }

    gst_buffer_resize(buf, 0, got);
    GST_BUFFER_OFFSET(buf) = self->offset;
    self->offset += (guint64)got;
    GST_BUFFER_OFFSET_END(buf) = self->offset;
    GST_LOG_OBJECT(self, "pushing %" G_GSSIZE_FORMAT " bytes at %" G_GUINT64_FORMAT,
                   got, GST_BUFFER_OFFSET(buf));
    *outbuf = buf;
    return GST_FLOW_OK;
}

/* ------------------------------------------------------------- properties */

static void gst_vgacapttsrc_set_property(GObject *object, guint prop_id,
                                         const GValue *value, GParamSpec *pspec)
{
    GstVgaCapTtSrc *self = GST_VGACAPTTSRC(object);

    GST_OBJECT_LOCK(self);
    switch (prop_id) {
    case PROP_LINK:
        g_free(self->link);
        self->link = g_value_dup_string(value);
        break;
    case PROP_PROJECT:
        g_free(self->project);
        self->project = g_value_dup_string(value);
        break;
    case PROP_DESIGN:
        g_free(self->design);
        self->design = g_value_dup_string(value);
        break;
    case PROP_CLOCK_HZ:
        self->clock_hz = g_value_get_uint(value);
        break;
    case PROP_PROFILE:
        g_free(self->profile);
        self->profile = g_value_dup_string(value);
        break;
    case PROP_PIO:
        self->pio = g_value_get_int(value);
        break;
    case PROP_BUF_WORDS:
        self->buf_words = g_value_get_int(value);
        break;
    case PROP_SECONDS:
        self->seconds = g_value_get_double(value);
        break;
    case PROP_TTCAP_COMMAND:
        g_free(self->ttcap_command);
        self->ttcap_command = g_value_dup_string(value);
        break;
    case PROP_STOP_TIMEOUT:
        self->stop_timeout = g_value_get_double(value);
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
        break;
    }
    GST_OBJECT_UNLOCK(self);
}

static void gst_vgacapttsrc_get_property(GObject *object, guint prop_id, GValue *value,
                                         GParamSpec *pspec)
{
    GstVgaCapTtSrc *self = GST_VGACAPTTSRC(object);

    GST_OBJECT_LOCK(self);
    switch (prop_id) {
    case PROP_LINK:
        g_value_set_string(value, self->link);
        break;
    case PROP_PROJECT:
        g_value_set_string(value, self->project);
        break;
    case PROP_DESIGN:
        g_value_set_string(value, self->design);
        break;
    case PROP_CLOCK_HZ:
        g_value_set_uint(value, self->clock_hz);
        break;
    case PROP_PROFILE:
        g_value_set_string(value, self->profile);
        break;
    case PROP_PIO:
        g_value_set_int(value, self->pio);
        break;
    case PROP_BUF_WORDS:
        g_value_set_int(value, self->buf_words);
        break;
    case PROP_SECONDS:
        g_value_set_double(value, self->seconds);
        break;
    case PROP_TTCAP_COMMAND:
        g_value_set_string(value, self->ttcap_command);
        break;
    case PROP_STOP_TIMEOUT:
        g_value_set_double(value, self->stop_timeout);
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
        break;
    }
    GST_OBJECT_UNLOCK(self);
}

static void gst_vgacapttsrc_finalize(GObject *object)
{
    GstVgaCapTtSrc *self = GST_VGACAPTTSRC(object);

    g_clear_pointer(&self->link, g_free);
    g_clear_pointer(&self->project, g_free);
    g_clear_pointer(&self->design, g_free);
    g_clear_pointer(&self->profile, g_free);
    g_clear_pointer(&self->ttcap_command, g_free);
    g_clear_pointer(&self->cmdline, g_free);
    g_mutex_clear(&self->err_lock);
    G_OBJECT_CLASS(gst_vgacapttsrc_parent_class)->finalize(object);
}

static void gst_vgacapttsrc_class_init(GstVgaCapTtSrcClass *klass)
{
    GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
    GstElementClass *element_class = GST_ELEMENT_CLASS(klass);
    GstBaseSrcClass *basesrc_class = GST_BASE_SRC_CLASS(klass);
    GstPushSrcClass *pushsrc_class = GST_PUSH_SRC_CLASS(klass);
    const GParamFlags rw = G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS;

    gobject_class->set_property = gst_vgacapttsrc_set_property;
    gobject_class->get_property = gst_vgacapttsrc_get_property;
    gobject_class->finalize = gst_vgacapttsrc_finalize;

    g_object_class_install_property(gobject_class, PROP_LINK,
        g_param_spec_string("link", "Link",
                            "How to reach the board: serial:/dev/ttyACM0, or "
                            "ws://host:8765/serial for the bridge", "", rw));
    g_object_class_install_property(gobject_class, PROP_PROJECT,
        g_param_spec_string("project", "Project",
                            "tt.shuttle macro to enable, e.g. tt_um_rejunity_vga; "
                            "empty leaves whatever is already selected", "", rw));
    g_object_class_install_property(gobject_class, PROP_DESIGN,
        g_param_spec_string("design", "Design",
                            "FPGA boards: bitstream to enable, through the same "
                            "tt.shuttle", "", rw));
    g_object_class_install_property(gobject_class, PROP_CLOCK_HZ,
        g_param_spec_uint("clock-hz", "Project clock",
                          "Project clock to program, in Hz; required", 0, 200000000u, 0,
                          rw));
    g_object_class_install_property(gobject_class, PROP_PROFILE,
        g_param_spec_string("profile", "Board profile",
                            "Board profile, or auto to ask the board for its GPIOMap",
                            DEFAULT_PROFILE, rw));
    g_object_class_install_property(gobject_class, PROP_PIO,
        g_param_spec_int("pio", "PIO block",
                         "PIO block for the sampler; -1 leaves ttcap's default",
                         -1, 7, DEFAULT_PIO, rw));
    g_object_class_install_property(gobject_class, PROP_BUF_WORDS,
        g_param_spec_int("buf-words", "DMA buffer words",
                         "Words per DMA buffer, two are allocated; -1 leaves ttcap's "
                         "default. Also the stop latency: one buffer, which is 2.2 s "
                         "at a 60 kHz project clock",
                         -1, 1 << 24, DEFAULT_BUF_WORDS, rw));
    g_object_class_install_property(gobject_class, PROP_SECONDS,
        g_param_spec_double("seconds", "Capture seconds",
                            "How long to capture; 0 captures until the element is "
                            "stopped", 0.0, 86400.0, DEFAULT_SECONDS, rw));
    g_object_class_install_property(gobject_class, PROP_TTCAP_COMMAND,
        g_param_spec_string("ttcap-command", "ttcap command",
                            "The command that runs ttcap, split with shell quoting "
                            "rules; the capture arguments are appended to it",
                            DEFAULT_TTCAP_COMMAND, rw));
    g_object_class_install_property(gobject_class, PROP_STOP_TIMEOUT,
        g_param_spec_double("stop-timeout", "Stop timeout",
                            "How long a cooperative stop may take before the pipe is "
                            "closed and, failing that, the child is killed. Allow at "
                            "least one DMA buffer for the board to wind down",
                            0.0, 600.0, DEFAULT_STOP_TIMEOUT, rw));

    basesrc_class->start = GST_DEBUG_FUNCPTR(gst_vgacapttsrc_start);
    basesrc_class->stop = GST_DEBUG_FUNCPTR(gst_vgacapttsrc_stop);
    basesrc_class->unlock = GST_DEBUG_FUNCPTR(gst_vgacapttsrc_unlock);
    basesrc_class->unlock_stop = GST_DEBUG_FUNCPTR(gst_vgacapttsrc_unlock_stop);
    pushsrc_class->create = GST_DEBUG_FUNCPTR(gst_vgacapttsrc_create);

    gst_element_class_add_static_pad_template(element_class, &src_template);
    gst_element_class_set_static_metadata(element_class,
        "Tiny Tapeout VGA capture source", "Source/Video",
        "Captures a Tiny Tapeout board's VGA output by running ttcap",
        "vgacap contributors <https://github.com/mithro/vgacap>");

    GST_DEBUG_CATEGORY_INIT(vgacapttsrc_debug, "vgacapttsrc", 0,
                            "Tiny Tapeout VGA capture source");
}

static void gst_vgacapttsrc_init(GstVgaCapTtSrc *self)
{
    self->link = g_strdup("");
    self->project = g_strdup("");
    self->design = g_strdup("");
    self->profile = g_strdup(DEFAULT_PROFILE);
    self->ttcap_command = g_strdup(DEFAULT_TTCAP_COMMAND);
    self->clock_hz = 0;
    self->pio = DEFAULT_PIO;
    self->buf_words = DEFAULT_BUF_WORDS;
    self->seconds = DEFAULT_SECONDS;
    self->stop_timeout = DEFAULT_STOP_TIMEOUT;
    self->active_stop_timeout = DEFAULT_STOP_TIMEOUT;

    self->pid = 0;
    self->out_fd = -1;
    self->err_fd = -1;
    self->wake_fds[0] = self->wake_fds[1] = -1;
    self->blocksize = DEFAULT_BLOCKSIZE;
    g_mutex_init(&self->err_lock);

    gst_base_src_set_blocksize(GST_BASE_SRC(self), DEFAULT_BLOCKSIZE);
    gst_base_src_set_format(GST_BASE_SRC(self), GST_FORMAT_BYTES);
    /* A board producing samples in real time is a live source: it cannot be
     * asked to preroll a buffer and then wait. */
    gst_base_src_set_live(GST_BASE_SRC(self), TRUE);
}
