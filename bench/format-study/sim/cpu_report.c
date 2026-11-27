// SPDX-License-Identifier: Apache-2.0
// LD_PRELOAD'ed into every simulation-check run: at exit, the CPU time of
// the process's first (simulation) thread and of the whole process, so that
// the trace writer's background thread is their difference, and the wall
// time since load. One line on stderr:
//   cpu-report: main_thread_s=... process_s=... wall_s=...
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <sys/resource.h>
#include <time.h>

static clockid_t main_cpu;
static struct timespec t0;

__attribute__((constructor)) static void start(void) {
    pthread_getcpuclockid(pthread_self(), &main_cpu);
    clock_gettime(CLOCK_MONOTONIC, &t0);
}

__attribute__((destructor)) static void stop(void) {
    struct timespec ts, t1;
    clock_gettime(main_cpu, &ts);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    const double proc = (double)(ru.ru_utime.tv_sec + ru.ru_stime.tv_sec) +
                        (double)(ru.ru_utime.tv_usec + ru.ru_stime.tv_usec) / 1e6;
    fprintf(stderr, "cpu-report: main_thread_s=%.3f process_s=%.3f wall_s=%.3f\n",
            (double)ts.tv_sec + (double)ts.tv_nsec / 1e9, proc,
            (double)(t1.tv_sec - t0.tv_sec) + (double)(t1.tv_nsec - t0.tv_nsec) / 1e9);
}
