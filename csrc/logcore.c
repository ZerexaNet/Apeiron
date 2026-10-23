/*
 * Apeiron native logging core.
 *
 * Implements a bounded, thread-safe, asynchronously flushed log sink with
 * rotating files, console echo, UTF-8 sanitisation and credential redaction.
 * The writer runs on a dedicated OS thread so that callers (the Qt event loop
 * in particular) never block on disk I/O.
 *
 * Build:  C11 (see setup.py).  Portable across MSVC and POSIX toolchains.
 * Copyright (c) Zerexa Ltd.  MPL-2.0.
 */

#ifndef _GNU_SOURCE
#define _GNU_SOURCE /* syscall() and SYS_gettid on Linux */
#endif

#include <ctype.h>
#include <errno.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "logcore.h"

#ifdef _WIN32
#include <windows.h>
#include <io.h>
#else
#include <pthread.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#if defined(__linux__)
#include <sys/syscall.h>
#endif
#endif

#define APEIRON_MAX_MODULE 128
#define APEIRON_PATH_MAX 4096

/* ------------------------------------------------------------------ */
/* Portability: threads, mutexes, condition variables, file handling   */
/* ------------------------------------------------------------------ */

#ifdef _WIN32

typedef CRITICAL_SECTION apeiron_mutex;
typedef CONDITION_VARIABLE apeiron_cond;
typedef HANDLE apeiron_thread;

static int mutex_init(apeiron_mutex *m) { InitializeCriticalSection(m); return 0; }
static void mutex_destroy(apeiron_mutex *m) { DeleteCriticalSection(m); }
static void mutex_lock(apeiron_mutex *m) { EnterCriticalSection(m); }
static void mutex_unlock(apeiron_mutex *m) { LeaveCriticalSection(m); }
static int cond_init(apeiron_cond *c) { InitializeConditionVariable(c); return 0; }
static void cond_destroy(apeiron_cond *c) { (void)c; }
static void cond_wait(apeiron_cond *c, apeiron_mutex *m) { SleepConditionVariableCS(c, m, INFINITE); }
static void cond_signal(apeiron_cond *c) { WakeConditionVariable(c); }
static void cond_broadcast(apeiron_cond *c) { WakeAllConditionVariable(c); }

static DWORD WINAPI writer_entry(LPVOID arg);
static int thread_start(HANDLE *out)
{
    *out = CreateThread(NULL, 0, writer_entry, NULL, 0, NULL);
    return (*out == NULL) ? -1 : 0;
}
static void thread_join(HANDLE *t)
{
    WaitForSingleObject(*t, INFINITE);
    CloseHandle(*t);
    *t = NULL;
}
static unsigned long current_tid(void) { return (unsigned long)GetCurrentThreadId(); }
static int file_rename(const char *from, const char *to)
{
    DeleteFileA(to);
    return MoveFileExA(from, to, MOVEFILE_REPLACE_EXISTING) ? 0 : -1;
}
static FILE *file_open(const char *path)
{
    /* Paths arrive as UTF-8; convert so that non-ASCII user directories work. */
    wchar_t wide[APEIRON_PATH_MAX];
    int n = MultiByteToWideChar(CP_UTF8, 0, path, -1, wide, APEIRON_PATH_MAX);
    if (n <= 0) return NULL;
    return _wfopen(wide, L"ab");
}
static int path_exists(const char *path)
{
    wchar_t wide[APEIRON_PATH_MAX];
    if (MultiByteToWideChar(CP_UTF8, 0, path, -1, wide, APEIRON_PATH_MAX) <= 0) return 0;
    DWORD attr = GetFileAttributesW(wide);
    return (attr != INVALID_FILE_ATTRIBUTES) ? 1 : 0;
}

#else /* POSIX */

typedef pthread_mutex_t apeiron_mutex;
typedef pthread_cond_t apeiron_cond;
typedef pthread_t apeiron_thread;

static int mutex_init(apeiron_mutex *m) { return pthread_mutex_init(m, NULL); }
static void mutex_destroy(apeiron_mutex *m) { pthread_mutex_destroy(m); }
static void mutex_lock(apeiron_mutex *m) { pthread_mutex_lock(m); }
static void mutex_unlock(apeiron_mutex *m) { pthread_mutex_unlock(m); }
static int cond_init(apeiron_cond *c) { return pthread_cond_init(c, NULL); }
static void cond_destroy(apeiron_cond *c) { pthread_cond_destroy(c); }
static void cond_wait(apeiron_cond *c, apeiron_mutex *m) { pthread_cond_wait(c, m); }
static void cond_signal(apeiron_cond *c) { pthread_cond_signal(c); }
static void cond_broadcast(apeiron_cond *c) { pthread_cond_broadcast(c); }

static void *writer_entry(void *arg);
static int thread_start(apeiron_thread *out)
{
    return pthread_create(out, NULL, writer_entry, NULL);
}
static void thread_join(apeiron_thread *t)
{
    pthread_join(*t, NULL);
    memset(t, 0, sizeof(*t));
}
static unsigned long current_tid(void)
{
#if defined(__APPLE__)
    uint64_t tid = 0;
    pthread_threadid_np(NULL, &tid);
    return (unsigned long)tid;
#elif defined(__linux__)
    return (unsigned long)syscall(SYS_gettid);
#else
    return (unsigned long)(uintptr_t)pthread_self();
#endif
}
static int file_rename(const char *from, const char *to) { return rename(from, to); }
static FILE *file_open(const char *path) { return fopen(path, "ab"); }
static int path_exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0;
}

#endif /* _WIN32 */



/* ------------------------------------------------------------------ */
/* Internal state                                                      */
/* ------------------------------------------------------------------ */

typedef struct log_record {
    int level;
    long long ts_ms;
    unsigned long pid;
    unsigned long tid;
    char *module;
    char *message;
    char *exception; /* may be NULL */
    struct log_record *next;
} log_record;

typedef struct {
    int initialized;
    apeiron_log_config cfg;
    char path[APEIRON_PATH_MAX];

    FILE *fp;
    size_t file_size;

    log_record *head;
    log_record *tail;
    int depth;
    int capacity;

    int stop;
    int writing;
    int flush_requested;
    int flush_ack;

    apeiron_log_stats stats;
    apeiron_mutex mutex;
    apeiron_cond cond;
    apeiron_thread thread;
    int thread_started;
    int mutex_ready;
} log_state;

static log_state g;
static char g_last_error[4608];

static void set_error(const char *fmt, const char *arg)
{
    snprintf(g_last_error, sizeof(g_last_error), fmt, arg ? arg : "");
}

const char *apeiron_log_last_error(void) { return g_last_error; }

const char *apeiron_log_strerror(apeiron_error code)
{
    switch (code) {
    case APEIRON_OK: return "ok";
    case APEIRON_ERR_ARG: return "invalid-argument";
    case APEIRON_ERR_STATE: return "invalid-state";
    case APEIRON_ERR_IO: return "io-error";
    case APEIRON_ERR_MEMORY: return "out-of-memory";
    case APEIRON_ERR_THREAD: return "thread-error";
    case APEIRON_ERR_ROTATE: return "rotation-error";
    case APEIRON_ERR_ENCODING: return "encoding-error";
    default: return "unknown-error";
    }
}

static const char *level_name(int level)
{
    if (level >= APEIRON_LOG_CRITICAL) return "CRITICAL";
    if (level >= APEIRON_LOG_ERROR) return "ERROR";
    if (level >= APEIRON_LOG_WARNING) return "WARNING";
    if (level >= APEIRON_LOG_INFO) return "INFO";
    return "DEBUG";
}

/* Strict-ish UTF-8 validation: rejects overlong encodings and lone
 * continuation bytes so that the log file is always valid UTF-8. */
static int utf8_valid(const char *s, size_t len)
{
    const unsigned char *p = (const unsigned char *)s;
    size_t i = 0;
    while (i < len) {
        unsigned char c = p[i];
        size_t need;
        unsigned int cp;
        if (c < 0x80) { i++; continue; }
        if ((c & 0xE0) == 0xC0) { need = 1; cp = c & 0x1F; }
        else if ((c & 0xF0) == 0xE0) { need = 2; cp = c & 0x0F; }
        else if ((c & 0xF8) == 0xF0) { need = 3; cp = c & 0x07; }
        else return 0;
        if (i + need >= len) return 0; /* truncated multi-byte sequence */
        for (size_t k = 1; k <= need; k++) {
            if ((p[i + k] & 0xC0) != 0x80) return 0;
            cp = (cp << 6) | (p[i + k] & 0x3F);
        }
        if (need == 1 && cp < 0x80) return 0;
        if (need == 2 && cp < 0x800) return 0;
        if (need == 3 && cp < 0x10000) return 0;
        if (cp > 0x10FFFF) return 0;
        if (cp >= 0xD800 && cp <= 0xDFFF) return 0;
        i += need + 1;
    }
    return 1;
}

/* ------------------------------------------------------------------ */
/* Redaction                                                           */
/* ------------------------------------------------------------------ */

static int is_token_char(unsigned char c)
{
    return isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~' || c == '+' ||
           c == '/' || c == '=';
}

static int starts_with_ci(const char *hay, size_t hay_len, const char *needle)
{
    size_t n = strlen(needle);
    if (hay_len < n) return 0;
    for (size_t i = 0; i < n; i++) {
        if (tolower((unsigned char)hay[i]) != tolower((unsigned char)needle[i])) return 0;
    }
    return 1;
}

/* Keywords whose *value* is sensitive.  Ordered longest-first so that
 * "access_token" wins over "token". */
static const char *const kSensitiveKeys[] = {
    "client_secret", "refresh_token", "access_token", "sessiontoken",
    "x-xbl-contract-version", "authorization", "set-cookie", "id_token",
    "password", "passwd", "secret", "session", "cookie", "bearer",
    "signature", "token", "xsts", "xbl",
    NULL,
};

static size_t mask_span(char *buf, size_t start, size_t end)
{
    static const char replacement[] = "***";
    size_t span = end - start;
    if (span == 0) return 0;
    if (span >= sizeof(replacement) - 1) {
        memcpy(buf + start, replacement, sizeof(replacement) - 1);
        if (span > sizeof(replacement) - 1) {
            memset(buf + start + sizeof(replacement) - 1, ' ', span - (sizeof(replacement) - 1));
        }
    } else {
        memset(buf + start, '*', span);
    }
    return 1;
}

static int redact_into(const char *in, char *out, size_t out_size, int *redactions)
{
    size_t len = strlen(in);
    size_t o = 0;
    int count = 0;

    if (out_size == 0) return APEIRON_ERR_ARG;
    if (len + 1 > out_size) {
        /* Clamp instead of failing: logs must never be dropped because a
         * payload was long.  Truncation happens before redaction so that a
         * truncated secret cannot leak its remainder. */
        len = out_size - 1;
    }
    memcpy(out, in, len);
    out[len] = '\0';

    /* Pass 1: keyword = value patterns. */
    size_t i = 0;
    while (i < len) {
        const char *matched = NULL;
        for (int k = 0; kSensitiveKeys[k] != NULL; k++) {
            if (starts_with_ci(out + i, len - i, kSensitiveKeys[k])) {
                /* Require a word boundary before the keyword. */
                if (i > 0 && is_token_char((unsigned char)out[i - 1])) continue;
                matched = kSensitiveKeys[k];
                break;
            }
        }
        if (matched != NULL && strcmp(matched, "bearer") == 0) {
            size_t j = i + strlen(matched);
            while (j < len && (out[j] == ' ' || out[j] == '"' || out[j] == ':')) j++;
            size_t start = j;
            size_t n = 0;
            while (j < len && is_token_char((unsigned char)out[j]) && n < 1024) { j++; n++; }
            if (j > start) count += (int)mask_span(out, start, j);
            i = j;
            continue;
        }
        if (matched != NULL) {
            size_t j = i + strlen(matched);
            /* Skip trailing quote characters then require a separator. */
            while (j < len && (out[j] == '"' || out[j] == '\'')) j++;
            while (j < len && (out[j] == ' ' || out[j] == '\t')) j++;
            if (j < len && (out[j] == ':' || out[j] == '=')) {
                j++;
                while (j < len && (out[j] == ' ' || out[j] == '\t' || out[j] == '"' ||
                                   out[j] == '\'' || out[j] == ':'))
                    j++;
                size_t start = j;
                size_t n = 0;
                while (j < len && is_token_char((unsigned char)out[j]) && n < 1024) { j++; n++; }
                if (j > start) {
                    count += (int)mask_span(out, start, j);
                }
                /* "Authorization: Bearer <token>" — the scheme is masked above,
                 * the credential that follows must be masked as well. */
                if (strcmp(matched, "authorization") == 0) {
                    size_t k = j;
                    while (k < len && (out[k] == ' ' || out[k] == '"')) k++;
                    size_t start2 = k;
                    size_t n2 = 0;
                    while (k < len && is_token_char((unsigned char)out[k]) && n2 < 1024) {
                        k++;
                        n2++;
                    }
                    if (k > start2) count += (int)mask_span(out, start2, k);
                    j = k;
                }
                i = j;
                continue;
            }
        }
        i++;
    }

    /* Pass 2: standalone JWT-ish runs (header.payload.signature), which are
     * the shape of Microsoft/Xbox bearer tokens even without a label. */
    i = 0;
    while (i < len) {
        if (is_token_char((unsigned char)out[i])) {
            size_t start = i;
            int dots = 0;
            while (i < len && is_token_char((unsigned char)out[i])) {
                if (out[i] == '.') dots++;
                i++;
            }
            size_t span = i - start;
            if (span >= 40 && dots >= 2) {
                /* Only mask if it is not already redacted. */
                if (memcmp(out + start, "***", 3) != 0) count += (int)mask_span(out, start, i);
            }
        } else {
            i++;
        }
    }

    if (redactions != NULL) *redactions = count;
    (void)o;
    return APEIRON_OK;
}

apeiron_error apeiron_log_redact(const char *in, char *out, size_t out_size, int *redactions)
{
    if (in == NULL || out == NULL || out_size < 4) return APEIRON_ERR_ARG;
    return redact_into(in, out, out_size, redactions);
}

/* ------------------------------------------------------------------ */
/* Timestamp                                                           */
/* ------------------------------------------------------------------ */

static void format_timestamp(long long ts_ms, char *buf, size_t size)
{
    time_t secs = (time_t)(ts_ms / 1000);
    int millis = (int)(ts_ms % 1000);
    struct tm tmv;
#ifdef _WIN32
    localtime_s(&tmv, &secs);
#else
    localtime_r(&secs, &tmv);
#endif
    size_t n = strftime(buf, size, "%Y-%m-%d %H:%M:%S", &tmv);
    if (n > 0 && n + 4 < size) {
        snprintf(buf + n, size - n, ".%03d", millis);
    }
}

static long long now_ms(void)
{
    struct timespec ts;
#ifdef _WIN32
    timespec_get(&ts, TIME_UTC);
#else
    clock_gettime(CLOCK_REALTIME, &ts);
#endif
    return (long long)ts.tv_sec * 1000LL + (ts.tv_nsec / 1000000L);
}

/* ------------------------------------------------------------------ */
/* Sink management (called with the mutex held by the writer or init)   */
/* ------------------------------------------------------------------ */

static int open_sink(void)
{
    if (g.path[0] == '\0') return 0;
    g.fp = file_open(g.path);
    if (g.fp == NULL) {
        set_error("cannot open log file: %s", g.path);
        return -1;
    }
    fseek(g.fp, 0, SEEK_END);
    long pos = ftell(g.fp);
    g.file_size = (pos > 0) ? (size_t)pos : 0;
    return 0;
}

static int rotate_locked(void)
{
    char from[APEIRON_PATH_MAX + 16];
    char to[APEIRON_PATH_MAX + 16];

    if (g.fp != NULL) {
        fflush(g.fp);
        fclose(g.fp);
        g.fp = NULL;
    }
    for (int i = g.cfg.backup_count - 1; i >= 1; i--) {
        snprintf(from, sizeof(from), "%s.%d", g.path, i);
        snprintf(to, sizeof(to), "%s.%d", g.path, i + 1);
        if (path_exists(from)) {
            if (file_rename(from, to) != 0) {
                g.stats.errors++;
                set_error("cannot rotate %s", from);
                return -1;
            }
        }
    }
    snprintf(to, sizeof(to), "%s.1", g.path);
    if (path_exists(g.path) && file_rename(g.path, to) != 0) {
        g.stats.errors++;
        set_error("cannot rotate %s", g.path);
        return -1;
    }
    g.file_size = 0;
    g.stats.rotations++;
    return open_sink();
}

static void write_record_locked(log_record *rec)
{
    char ts[64];
    char line[APEIRON_MAX_MESSAGE + APEIRON_MAX_MODULE + 320];
    int n;

    format_timestamp(rec->ts_ms, ts, sizeof(ts));
    n = snprintf(line, sizeof(line), "%s %-5s [%lu:%lu] %s: %s", ts, level_name(rec->level),
                 rec->pid, rec->tid, rec->module, rec->message);
    if (n < 0) return;
    if ((size_t)n >= sizeof(line)) n = (int)sizeof(line) - 1;

    if (g.fp != NULL && g.cfg.max_bytes > 0 && g.cfg.backup_count > 0 &&
        g.file_size + (size_t)n + 2 > g.cfg.max_bytes) {
        if (rotate_locked() != 0) {
            /* Keep logging to console rather than silently losing records. */
            g.fp = NULL;
        }
    }

    if (g.fp != NULL) {
        size_t written = fwrite(line, 1, (size_t)n, g.fp);
        if (written != (size_t)n) {
            g.stats.errors++;
            set_error("short write to log file: %s", g.path);
        } else {
            g.file_size += written;
        }
        fputc('\n', g.fp);
        g.file_size++;
        if (rec->exception != NULL) {
            int m = snprintf(line, sizeof(line), "  ↳ exception: %s", rec->exception);
            if (m > 0) {
                size_t len = (size_t)m < sizeof(line) ? (size_t)m : sizeof(line) - 1;
                fwrite(line, 1, len, g.fp);
                fputc('\n', g.fp);
                g.file_size += len + 1;
            }
        }
        fflush(g.fp);
    }
    if (g.cfg.console) {
        fprintf(stderr, "%s\n", line);
        if (rec->exception != NULL) fprintf(stderr, "  ↳ exception: %s\n", rec->exception);
        fflush(stderr);
    }
    g.stats.written++;
}

static void free_record(log_record *rec)
{
    if (rec == NULL) return;
    free(rec->module);
    free(rec->message);
    free(rec->exception);
    free(rec);
}

static void *writer_loop(void *arg)
{
    (void)arg;
    mutex_lock(&g.mutex);
    for (;;) {
        if (g.stop && g.head == NULL) break;
        if (g.head == NULL) {
            if (g.flush_requested) {
                if (g.fp != NULL) fflush(g.fp);
                g.flush_requested = 0;
                g.flush_ack = 1;
                cond_broadcast(&g.cond);
            }
            cond_wait(&g.cond, &g.mutex);
            continue;
        }
        log_record *rec = g.head;
        g.head = rec->next;
        if (g.head == NULL) g.tail = NULL;
        g.depth--;
        g.writing = 1;
        mutex_unlock(&g.mutex);

        write_record_locked(rec);
        free_record(rec);

        mutex_lock(&g.mutex);
        g.writing = 0;
        g.stats.queue_depth = g.depth;
        if (g.flush_requested && g.head == NULL) {
            if (g.fp != NULL) fflush(g.fp);
            g.flush_requested = 0;
            g.flush_ack = 1;
            cond_broadcast(&g.cond);
        }
    }
    if (g.fp != NULL) {
        fflush(g.fp);
        fclose(g.fp);
        g.fp = NULL;
    }
    mutex_unlock(&g.mutex);
    return NULL;
}

#ifdef _WIN32
static DWORD WINAPI writer_entry(LPVOID arg) { return (DWORD)(uintptr_t)writer_loop(arg); }
#else
static void *writer_entry(void *arg) { return writer_loop(arg); }
#endif

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

apeiron_error apeiron_log_init(const apeiron_log_config *config)
{
    if (config == NULL) return APEIRON_ERR_ARG;
    if (config->level < APEIRON_LOG_DEBUG || config->level > APEIRON_LOG_CRITICAL) {
        return APEIRON_ERR_ARG;
    }
    if (config->backup_count < 0 || config->queue_capacity < 0) return APEIRON_ERR_ARG;

    if (g.initialized) {
        apeiron_log_shutdown();
    }

    memset(&g.stats, 0, sizeof(g.stats));
    g.cfg = *config;
    g.cfg.queue_capacity = (config->queue_capacity > 0) ? config->queue_capacity
                                                       : APEIRON_DEFAULT_QUEUE;
    g.path[0] = '\0';
    if (config->file_path != NULL) {
        size_t len = strlen(config->file_path);
        if (len == 0 || len >= sizeof(g.path)) return APEIRON_ERR_ARG;
        memcpy(g.path, config->file_path, len + 1);
    }
    g.head = g.tail = NULL;
    g.depth = 0;
    g.capacity = g.cfg.queue_capacity;
    g.stop = 0;
    g.writing = 0;
    g.flush_requested = 0;
    g.flush_ack = 0;
    g.fp = NULL;
    g.file_size = 0;

    if (mutex_init(&g.mutex) != 0) return APEIRON_ERR_THREAD;
    if (cond_init(&g.cond) != 0) {
        mutex_destroy(&g.mutex);
        return APEIRON_ERR_THREAD;
    }
    g.mutex_ready = 1;

    if (g.path[0] != '\0' && open_sink() != 0) {
        cond_destroy(&g.cond);
        mutex_destroy(&g.mutex);
        g.mutex_ready = 0;
        return APEIRON_ERR_IO;
    }

    if (thread_start(&g.thread) != 0) {
        if (g.fp != NULL) { fclose(g.fp); g.fp = NULL; }
        cond_destroy(&g.cond);
        mutex_destroy(&g.mutex);
        g.mutex_ready = 0;
        return APEIRON_ERR_THREAD;
    }
    g.thread_started = 1;
    g.initialized = 1;
    g.stats.initialized = 1;
    return APEIRON_OK;
}

apeiron_error apeiron_log_write(int level, const char *module, const char *message,
                               const char *exception)
{
    log_record *rec;
    char *redacted_msg = NULL;
    char *redacted_exc = NULL;
    int redactions = 0;

    if (!g.initialized) return APEIRON_ERR_STATE;
    if (module == NULL || message == NULL) return APEIRON_ERR_ARG;
    if (level < APEIRON_LOG_DEBUG) level = APEIRON_LOG_DEBUG;
    if (level > APEIRON_LOG_CRITICAL) level = APEIRON_LOG_CRITICAL;

    if (!utf8_valid(module, strlen(module))) return APEIRON_ERR_ENCODING;
    if (!utf8_valid(message, strlen(message))) return APEIRON_ERR_ENCODING;
    if (exception != NULL && !utf8_valid(exception, strlen(exception))) return APEIRON_ERR_ENCODING;

    if (level < g.cfg.level) {
        mutex_lock(&g.mutex);
        g.stats.filtered++;
        mutex_unlock(&g.mutex);
        return APEIRON_OK;
    }

    redacted_msg = (char *)malloc(APEIRON_MAX_MESSAGE);
    if (redacted_msg == NULL) return APEIRON_ERR_MEMORY;
    if (redact_into(message, redacted_msg, APEIRON_MAX_MESSAGE, &redactions) != APEIRON_OK) {
        free(redacted_msg);
        return APEIRON_ERR_ARG;
    }
    if (exception != NULL) {
        int exc_redactions = 0;
        redacted_exc = (char *)malloc(4096);
        if (redacted_exc == NULL) {
            free(redacted_msg);
            return APEIRON_ERR_MEMORY;
        }
        if (redact_into(exception, redacted_exc, 4096, &exc_redactions) != APEIRON_OK) {
            free(redacted_msg);
            free(redacted_exc);
            return APEIRON_ERR_ARG;
        }
        redactions += exc_redactions;
    }

    rec = (log_record *)calloc(1, sizeof(log_record));
    if (rec == NULL) {
        free(redacted_msg);
        free(redacted_exc);
        return APEIRON_ERR_MEMORY;
    }
    rec->level = level;
    rec->ts_ms = now_ms();
#ifdef _WIN32
    rec->pid = (unsigned long)GetCurrentProcessId();
#else
    rec->pid = (unsigned long)getpid();
#endif
    rec->tid = current_tid();
    rec->module = (char *)malloc(strlen(module) + 1);
    if (rec->module == NULL) {
        free_record(rec);
        free(redacted_msg);
        free(redacted_exc);
        return APEIRON_ERR_MEMORY;
    }
    memcpy(rec->module, module, strlen(module) + 1);
    rec->message = redacted_msg;
    rec->exception = redacted_exc;
    rec->next = NULL;

    mutex_lock(&g.mutex);
    if (g.depth >= g.capacity) {
        g.stats.dropped++;
        mutex_unlock(&g.mutex);
        free_record(rec);
        return APEIRON_OK; /* dropped, but not an error: back-pressure is bounded */
    }
    if (g.tail == NULL) {
        g.head = g.tail = rec;
    } else {
        g.tail->next = rec;
        g.tail = rec;
    }
    g.depth++;
    g.stats.queue_depth = g.depth;
    if (redactions > 0) g.stats.redactions++;
    cond_signal(&g.cond);
    mutex_unlock(&g.mutex);
    return APEIRON_OK;
}

apeiron_error apeiron_log_flush(void)
{
    if (!g.initialized) return APEIRON_ERR_STATE;
    mutex_lock(&g.mutex);
    if (g.head == NULL && !g.writing) {
        if (g.fp != NULL) fflush(g.fp);
        mutex_unlock(&g.mutex);
        return APEIRON_OK;
    }
    g.flush_requested = 1;
    g.flush_ack = 0;
    cond_signal(&g.cond);
    while (!g.flush_ack && g.initialized) {
        /* Wait for the writer to acknowledge; bounded by the writer draining
         * the queue, so this terminates as long as the thread is alive. */
        cond_wait(&g.cond, &g.mutex);
    }
    mutex_unlock(&g.mutex);
    return APEIRON_OK;
}

apeiron_error apeiron_log_shutdown(void)
{
    if (!g.initialized) return APEIRON_OK;
    mutex_lock(&g.mutex);
    g.stop = 1;
    cond_broadcast(&g.cond);
    mutex_unlock(&g.mutex);

    if (g.thread_started) {
        thread_join(&g.thread);
        g.thread_started = 0;
    }
    mutex_lock(&g.mutex);
    log_record *rec = g.head;
    while (rec != NULL) {
        log_record *next = rec->next;
        free_record(rec);
        rec = next;
    }
    g.head = g.tail = NULL;
    g.depth = 0;
    mutex_unlock(&g.mutex);

    if (g.fp != NULL) { fclose(g.fp); g.fp = NULL; }
    if (g.mutex_ready) {
        cond_destroy(&g.cond);
        mutex_destroy(&g.mutex);
        g.mutex_ready = 0;
    }
    g.initialized = 0;
    g.stats.initialized = 0;
    return APEIRON_OK;
}

apeiron_error apeiron_log_set_level(int level)
{
    if (!g.initialized) return APEIRON_ERR_STATE;
    if (level < APEIRON_LOG_DEBUG || level > APEIRON_LOG_CRITICAL) return APEIRON_ERR_ARG;
    mutex_lock(&g.mutex);
    g.cfg.level = level;
    mutex_unlock(&g.mutex);
    return APEIRON_OK;
}

int apeiron_log_get_level(void)
{
    if (!g.initialized) return 0;
    return g.cfg.level;
}

apeiron_error apeiron_log_get_stats(apeiron_log_stats *out)
{
    if (out == NULL) return APEIRON_ERR_ARG;
    if (!g.mutex_ready) return APEIRON_ERR_STATE;
    mutex_lock(&g.mutex);
    *out = g.stats;
    out->queue_depth = g.depth;
    out->initialized = g.initialized;
    mutex_unlock(&g.mutex);
    return APEIRON_OK;
}
