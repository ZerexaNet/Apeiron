/*
 * Standalone test harness for the Apeiron logging core.
 *
 * Builds and runs without Python so that the native module can be validated
 * independently of the interpreter:
 *
 *     cc -std=c11 -O1 -g -Wall -Wextra -pthread \
 *        csrc/logcore.c csrc/test_logcore.c -o build/test_logcore
 *     ./build/test_logcore            # logs to a temp dir, prints PASS/FAIL
 *
 * Exit code 0 means every check passed.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "logcore.h"

#ifdef _WIN32
#include <windows.h>
#include <process.h>
#define THREAD_RET unsigned
#else
#include <pthread.h>
#include <sys/stat.h>
#include <unistd.h>
#define THREAD_RET void *
#endif

static int g_failures;
static int g_checks;

#define CHECK(cond, msg)                                                                           \
    do {                                                                                           \
        g_checks++;                                                                                \
        if (!(cond)) {                                                                             \
            g_failures++;                                                                          \
            fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, msg);                          \
        }                                                                                          \
    } while (0)

static void join_path(char *out, size_t size, const char *dir, const char *name)
{
    snprintf(out, size, "%s/%s", dir, name);
}

static long file_size_of(const char *path)
{
    FILE *f = fopen(path, "rb");
    long n;
    if (f == NULL) return -1;
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fclose(f);
    return n;
}

static int read_all(const char *path, char *buf, size_t size)
{
    FILE *f = fopen(path, "rb");
    size_t n;
    if (f == NULL) return -1;
    n = fread(buf, 1, size - 1, f);
    buf[n] = '\0';
    fclose(f);
    return (int)n;
}

/* --- worker for the concurrency check --------------------------------- */

typedef struct {
    int id;
    int count;
} worker_arg;

#ifdef _WIN32
static THREAD_RET WINAPI worker(void *raw)
#else
static THREAD_RET worker(void *raw)
#endif
{
    worker_arg *a = (worker_arg *)raw;
    char msg[128];
    for (int i = 0; i < a->count; i++) {
        snprintf(msg, sizeof(msg), "thread-%d record-%d", a->id, i);
        if (apeiron_log_write(APEIRON_LOG_INFO, "c-test", msg, NULL) != APEIRON_OK) {
            return (THREAD_RET)(uintptr_t)1;
        }
    }
    return (THREAD_RET)0;
}

int main(void)
{
    char dir[512];
    char logpath[600];
    char rotated[600];
    char buf[200000];
    apeiron_log_config cfg;
    apeiron_log_stats st;
    time_t now = time(NULL);

#ifdef _WIN32
    const char *tmp = getenv("TEMP");
    snprintf(dir, sizeof(dir), "%s\\apeiron-c-test-%lld", tmp ? tmp : ".",
             (long long)now);
    CreateDirectoryA(dir, NULL);
#else
    snprintf(dir, sizeof(dir), "/tmp/apeiron-c-test-%lld", (long long)now);
    mkdir(dir, 0700);
#endif
    join_path(logpath, sizeof(logpath), dir, "apeiron.log");
    join_path(rotated, sizeof(rotated), dir, "apeiron.log.1");

    /* 1. Not initialised: writes must be rejected with a state error. */
    CHECK(apeiron_log_write(APEIRON_LOG_INFO, "c-test", "before init", NULL) == APEIRON_ERR_STATE,
          "write before init must fail with ERR_STATE");

    /* 2. Basic init + write + flush. */
    memset(&cfg, 0, sizeof(cfg));
    cfg.file_path = logpath;
    cfg.level = APEIRON_LOG_DEBUG;
    cfg.max_bytes = 1024 * 1024;
    cfg.backup_count = 3;
    cfg.console = 0;
    CHECK(apeiron_log_init(&cfg) == APEIRON_OK, "init must succeed");
    CHECK(apeiron_log_write(APEIRON_LOG_INFO, "c-test", "hello world", NULL) == APEIRON_OK,
          "write must succeed");
    CHECK(apeiron_log_write(APEIRON_LOG_ERROR, "c-test", "boom", "ValueError: bad") == APEIRON_OK,
          "write with exception must succeed");
    CHECK(apeiron_log_flush() == APEIRON_OK, "flush must succeed");
    CHECK(apeiron_log_get_stats(&st) == APEIRON_OK, "stats must succeed");
    CHECK(st.written >= 2, "two records written");
    CHECK(read_all(logpath, buf, sizeof(buf)) > 0, "log file readable");
    CHECK(strstr(buf, "hello world") != NULL, "message present");
    CHECK(strstr(buf, "[INFO") != NULL || strstr(buf, "INFO ") != NULL, "level present");
    CHECK(strstr(buf, "ValueError: bad") != NULL, "exception context present");

    /* 3. Level filtering. */
    CHECK(apeiron_log_set_level(APEIRON_LOG_ERROR) == APEIRON_OK, "set_level");
    CHECK(apeiron_log_write(APEIRON_LOG_DEBUG, "c-test", "should-not-appear", NULL) == APEIRON_OK,
          "filtered write returns OK");
    apeiron_log_flush();
    CHECK(apeiron_log_get_stats(&st) == APEIRON_OK, "stats after filter");
    CHECK(st.filtered >= 1, "filtered counter incremented");

    /* 4. Invalid arguments. */
    CHECK(apeiron_log_write(APEIRON_LOG_INFO, NULL, "x", NULL) == APEIRON_ERR_ARG, "NULL module");
    CHECK(apeiron_log_write(APEIRON_LOG_INFO, "m", NULL, NULL) == APEIRON_ERR_ARG, "NULL message");
    CHECK(apeiron_log_write(APEIRON_LOG_INFO, "m", "\xff\xfe bad utf8", NULL) ==
              APEIRON_ERR_ENCODING,
          "invalid UTF-8 rejected");
    CHECK(apeiron_log_get_stats(NULL) == APEIRON_ERR_ARG, "NULL stats rejected");

    /* 5. UTF-8 payload integrity (Chinese text). */
    apeiron_log_set_level(APEIRON_LOG_DEBUG);
    CHECK(apeiron_log_write(APEIRON_LOG_INFO, "c-test", "中文日志 测试 — ✓", NULL) == APEIRON_OK,
          "unicode write");
    apeiron_log_flush();
    memset(buf, 0, sizeof(buf));
    read_all(logpath, buf, sizeof(buf));
    CHECK(strstr(buf, "中文日志 测试 — ✓") != NULL, "unicode preserved byte-for-byte");

    /* 6. Redaction. */
    {
        char out[512];
        int n = 0;
        CHECK(apeiron_log_redact("Authorization: Bearer abcdef123456", out, sizeof(out), &n) ==
                  APEIRON_OK,
              "redact authorization");
        CHECK(strstr(out, "abcdef123456") == NULL, "bearer token masked");
        CHECK(n >= 1, "redaction counted");

        n = 0;
        CHECK(apeiron_log_redact("{\"access_token\":\"eyJhbGciOiJSUzI1NiJ9.abc.def\"}", out,
                                 sizeof(out), &n) == APEIRON_OK,
              "redact json token");
        CHECK(strstr(out, "eyJhbGciOiJSUzI1NiJ9") == NULL, "json token masked");

        n = 0;
        CHECK(apeiron_log_redact("nothing secret here", out, sizeof(out), &n) == APEIRON_OK,
              "redact inert text");
        CHECK(strcmp(out, "nothing secret here") == 0, "inert text unchanged");

        /* Secrets passed through the writer must also be masked on disk. */
        apeiron_log_write(APEIRON_LOG_INFO, "auth", "refresh_token=SECRETVALUE123", NULL);
        apeiron_log_flush();
        memset(buf, 0, sizeof(buf));
        read_all(logpath, buf, sizeof(buf));
        CHECK(strstr(buf, "SECRETVALUE123") == NULL, "secret never reaches disk");
    }

    /* 7. Rotation. */
    {
        apeiron_log_config rot;
        char small[600];
        join_path(small, sizeof(small), dir, "rotate.log");
        memset(&rot, 0, sizeof(rot));
        rot.file_path = small;
        rot.level = APEIRON_LOG_DEBUG;
        rot.max_bytes = 512;
        rot.backup_count = 2;
        CHECK(apeiron_log_init(&rot) == APEIRON_OK, "re-init for rotation");
        for (int i = 0; i < 60; i++) {
            char m[200];
            snprintf(m, sizeof(m), "rotation filler record %04d padding padding padding padding", i);
            apeiron_log_write(APEIRON_LOG_INFO, "rot", m, NULL);
        }
        apeiron_log_flush();
        apeiron_log_get_stats(&st);
        CHECK(st.rotations >= 1, "rotation happened");
        CHECK(file_size_of(small) > 0, "active log exists after rotation");
        {
            char r1[700];
            snprintf(r1, sizeof(r1), "%s.1", small);
            CHECK(file_size_of(r1) > 0, "backup log created");
            CHECK(file_size_of(r1) <= 1024, "backup respects max_bytes plus one record");
        }
    }

    /* 8. Concurrency: 4 threads x 200 records, no loss, no corruption. */
    {
        char conc[600];
        apeiron_log_config c2;
        join_path(conc, sizeof(conc), dir, "concurrent.log");
        memset(&c2, 0, sizeof(c2));
        c2.file_path = conc;
        c2.level = APEIRON_LOG_DEBUG;
        c2.max_bytes = 0;
        c2.backup_count = 0;
        c2.queue_capacity = 8192;
        CHECK(apeiron_log_init(&c2) == APEIRON_OK, "re-init for concurrency");
#ifdef _WIN32
        HANDLE threads[4];
        worker_arg args[4];
        for (int i = 0; i < 4; i++) {
            args[i].id = i;
            args[i].count = 200;
            threads[i] = CreateThread(NULL, 0, worker, &args[i], 0, NULL);
        }
        for (int i = 0; i < 4; i++) {
            WaitForSingleObject(threads[i], INFINITE);
            CloseHandle(threads[i]);
        }
#else
        pthread_t threads[4];
        worker_arg args[4];
        for (int i = 0; i < 4; i++) {
            args[i].id = i;
            args[i].count = 200;
            pthread_create(&threads[i], NULL, worker, &args[i]);
        }
        for (int i = 0; i < 4; i++) pthread_join(threads[i], NULL);
#endif
        apeiron_log_flush();
        apeiron_log_get_stats(&st);
        CHECK(st.written == 800, "all concurrent records written");
        CHECK(st.dropped == 0, "no records dropped");
        memset(buf, 0, sizeof(buf));
        read_all(conc, buf, sizeof(buf));
        CHECK(strstr(buf, "thread-3 record-199") != NULL, "last record present");
    }

    /* 9. Shutdown is idempotent; writes after shutdown fail cleanly. */
    CHECK(apeiron_log_shutdown() == APEIRON_OK, "shutdown");
    CHECK(apeiron_log_shutdown() == APEIRON_OK, "shutdown is idempotent");
    CHECK(apeiron_log_write(APEIRON_LOG_INFO, "c", "after", NULL) == APEIRON_ERR_STATE,
          "write after shutdown rejected");
    CHECK(apeiron_log_flush() == APEIRON_ERR_STATE, "flush after shutdown rejected");

    /* 10. Unwritable path surfaces an IO error rather than crashing. */
    {
        apeiron_log_config bad;
        memset(&bad, 0, sizeof(bad));
        bad.file_path = "/this/directory/does/not/exist/apeiron.log";
        bad.level = APEIRON_LOG_INFO;
        CHECK(apeiron_log_init(&bad) == APEIRON_ERR_IO, "unwritable path -> ERR_IO");
        CHECK(strlen(apeiron_log_last_error()) > 0, "last_error populated");
        apeiron_log_shutdown();
    }

    printf("%d checks, %d failures\n", g_checks, g_failures);
    if (g_failures == 0) {
        printf("ALL C LOG CORE TESTS PASSED\n");
        return 0;
    }
    return 1;
}
