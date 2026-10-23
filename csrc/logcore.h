/*
 * Apeiron native logging core — public C interface.
 *
 * This header is the single source of truth for the C-side API of the
 * logging subsystem.  The Python extension module (apeiron._logcore)
 * is a thin wrapper around this API.
 *
 * Copyright (c) Zerexa Ltd.  Licensed under the MPL-2.0 (see LICENSE).
 */

#ifndef APEIRON_LOGCORE_H
#define APEIRON_LOGCORE_H

#include <stddef.h>
#include <stdint.h>

#define APEIRON_MAX_MESSAGE 32768  /* longest single message, bytes of UTF-8 */
#define APEIRON_DEFAULT_QUEUE 4096 /* default bounded queue length */

#ifdef __cplusplus
extern "C" {
#endif

/* Log levels.  Values intentionally match the Python `logging` module so
 * that the Python wrapper can forward numeric levels without translation. */
typedef enum {
    APEIRON_LOG_DEBUG = 10,
    APEIRON_LOG_INFO = 20,
    APEIRON_LOG_WARNING = 30,
    APEIRON_LOG_ERROR = 40,
    APEIRON_LOG_CRITICAL = 50
} apeiron_log_level;

/* Error codes returned by the C API.  Every function in this header that can
 * fail returns one of these values; 0 is success. */
typedef enum {
    APEIRON_OK = 0,
    APEIRON_ERR_ARG = -1,      /* invalid argument (NULL pointer, bad range) */
    APEIRON_ERR_STATE = -2,    /* call not valid in current state */
    APEIRON_ERR_IO = -3,       /* file could not be opened/written */
    APEIRON_ERR_MEMORY = -4,   /* allocation failure */
    APEIRON_ERR_THREAD = -5,   /* thread or mutex initialisation failure */
    APEIRON_ERR_ROTATE = -6,   /* rotation failed */
    APEIRON_ERR_ENCODING = -7  /* input was not valid UTF-8 */
} apeiron_error;

/* Configuration for apeiron_log_init(). */
typedef struct {
    const char *file_path;   /* UTF-8 path of the log file (may be NULL for console only) */
    int level;               /* minimum level to record */
    size_t max_bytes;        /* rotate when the file would exceed this size (0 = no rotation) */
    int backup_count;        /* number of rotated files to keep (0 = no rotation) */
    int console;             /* non-zero to echo records to stderr */
    int queue_capacity;      /* bounded queue size; 0 selects the default (4096) */
} apeiron_log_config;

/* Statistics for diagnostics and tests. */
typedef struct {
    uint64_t written;      /* records written to the sink */
    uint64_t dropped;      /* records dropped because the queue was full */
    uint64_t filtered;     /* records discarded because below the active level */
    uint64_t rotations;    /* number of rotations performed */
    uint64_t redactions;   /* number of records that had secrets masked */
    uint64_t errors;       /* write errors observed by the writer thread */
    int queue_depth;       /* records currently queued (snapshot) */
    int initialized;       /* 1 when the core is initialised */
} apeiron_log_stats;

/* Initialise the logging core.  Safe to call again to reconfigure; the
 * previous sink is flushed and closed first.  Thread-safe. */
apeiron_error apeiron_log_init(const apeiron_log_config *config);

/* Write one record.  `module` and `message` are UTF-8.  `exception` may be
 * NULL.  The call copies its inputs and returns immediately; the actual I/O
 * happens on the writer thread, so a slow disk cannot block the caller. */
apeiron_error apeiron_log_write(int level,
                                const char *module,
                                const char *message,
                                const char *exception);

/* Flush all queued records to the sink and wait until they are durable. */
apeiron_error apeiron_log_flush(void);

/* Flush, stop the writer thread and release all resources.  Idempotent. */
apeiron_error apeiron_log_shutdown(void);

/* Update the minimum level at runtime. */
apeiron_error apeiron_log_set_level(int level);

/* Read the active level (0 when not initialised). */
int apeiron_log_get_level(void);

/* Copy current statistics into `out`. */
apeiron_error apeiron_log_get_stats(apeiron_log_stats *out);

/* Copy the last error message produced by the core (never contains log
 * payloads or credentials).  Returns a pointer to a static, thread-local
 * buffer valid until the next call on the same thread. */
const char *apeiron_log_last_error(void);

/* Mask credentials and session tokens inside `in`.  The result is written to
 * `out` (NUL terminated) and the number of masked spans is returned through
 * `redactions` when non-NULL.  Returns APEIRON_ERR_ARG when the buffer is too
 * small or inputs are NULL. */
apeiron_error apeiron_log_redact(const char *in,
                                 char *out,
                                 size_t out_size,
                                 int *redactions);

/* Human readable name for an error code ("ok", "io", ...). */
const char *apeiron_log_strerror(apeiron_error code);

#ifdef __cplusplus
}
#endif

#endif /* APEIRON_LOGCORE_H */
