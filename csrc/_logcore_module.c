/*
 * Python bindings for the Apeiron logging core.
 * Kept separate from logcore.c so that the core remains a pure-C module with
 * its own standalone test harness (csrc/test_logcore.c).
 *
 * Copyright (c) Zerexa Ltd.  MPL-2.0.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "logcore.h"



/* The core keeps a single global state; the module therefore declares itself
 * unsafe for sub-interpreters.  Apeiron runs exactly one interpreter. */

static PyObject *error_to_exception(apeiron_error code)
{
    switch (code) {
    case APEIRON_ERR_ARG:
        PyErr_SetString(PyExc_ValueError, apeiron_log_strerror(code));
        return NULL;
    case APEIRON_ERR_IO:
    case APEIRON_ERR_ROTATE:
        PyErr_SetString(PyExc_OSError, apeiron_log_strerror(code));
        return NULL;
    case APEIRON_ERR_MEMORY:
        PyErr_SetString(PyExc_MemoryError, apeiron_log_strerror(code));
        return NULL;
    case APEIRON_ERR_ENCODING:
        PyErr_SetString(PyExc_UnicodeError, apeiron_log_strerror(code));
        return NULL;
    default:
        PyErr_SetString(PyExc_RuntimeError, apeiron_log_strerror(code));
        return NULL;
    }
}

static PyObject *py_init(PyObject *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"file_path",     "level", "max_bytes",       "backup_count",
                             "console",       "queue_capacity", NULL};
    PyObject *path_obj = Py_None;
    int level = APEIRON_LOG_INFO;
    Py_ssize_t max_bytes = 0;
    int backup_count = 0;
    int console = 0;
    int queue_capacity = 0;
    apeiron_log_config cfg;
    apeiron_error rc;

    (void)self;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|Oinipi", kwlist, &path_obj, &level, &max_bytes,
                                     &backup_count, &console, &queue_capacity)) {
        return NULL;
    }

    memset(&cfg, 0, sizeof(cfg));
    if (path_obj != Py_None) {
        if (!PyUnicode_Check(path_obj)) {
            PyErr_SetString(PyExc_TypeError, "file_path must be str or None");
            return NULL;
        }
        cfg.file_path = PyUnicode_AsUTF8(path_obj);
        if (cfg.file_path == NULL) return NULL;
        /* On Windows, convert '/' separators defensively; the C layer uses the
         * path verbatim, so reject embedded NULs which PyUnicode_AsUTF8 never
         * produces but a bytes-like object could. */
        if (strlen(cfg.file_path) == 0) {
            PyErr_SetString(PyExc_ValueError, "file_path must not be empty");
            return NULL;
        }
    }
    cfg.level = level;
    cfg.max_bytes = (size_t)max_bytes;
    cfg.backup_count = backup_count;
    cfg.console = console;
    cfg.queue_capacity = queue_capacity;

    Py_BEGIN_ALLOW_THREADS
    rc = apeiron_log_init(&cfg);
    Py_END_ALLOW_THREADS

    if (rc != APEIRON_OK) return error_to_exception(rc);
    Py_RETURN_NONE;
}

static PyObject *py_write(PyObject *self, PyObject *args)
{
    int level;
    const char *module;
    const char *message;
    const char *exception = NULL;
    apeiron_error rc;

    (void)self;
    if (!PyArg_ParseTuple(args, "iss|z", &level, &module, &message, &exception)) return NULL;
    Py_BEGIN_ALLOW_THREADS
    rc = apeiron_log_write(level, module, message, exception);
    Py_END_ALLOW_THREADS
    if (rc != APEIRON_OK) return error_to_exception(rc);
    Py_RETURN_NONE;
}

static PyObject *py_flush(PyObject *self, PyObject *unused)
{
    apeiron_error rc;
    (void)self;
    (void)unused;
    Py_BEGIN_ALLOW_THREADS
    rc = apeiron_log_flush();
    Py_END_ALLOW_THREADS
    if (rc != APEIRON_OK) return error_to_exception(rc);
    Py_RETURN_NONE;
}

static PyObject *py_shutdown(PyObject *self, PyObject *unused)
{
    apeiron_error rc;
    (void)self;
    (void)unused;
    Py_BEGIN_ALLOW_THREADS
    rc = apeiron_log_shutdown();
    Py_END_ALLOW_THREADS
    if (rc != APEIRON_OK) return error_to_exception(rc);
    Py_RETURN_NONE;
}

static PyObject *py_set_level(PyObject *self, PyObject *args)
{
    int level;
    apeiron_error rc;
    (void)self;
    if (!PyArg_ParseTuple(args, "i", &level)) return NULL;
    rc = apeiron_log_set_level(level);
    if (rc != APEIRON_OK) return error_to_exception(rc);
    Py_RETURN_NONE;
}

static PyObject *py_get_level(PyObject *self, PyObject *unused)
{
    (void)self;
    (void)unused;
    return PyLong_FromLong(apeiron_log_get_level());
}

static PyObject *py_stats(PyObject *self, PyObject *unused)
{
    apeiron_log_stats st;
    apeiron_error rc;
    (void)self;
    (void)unused;
    rc = apeiron_log_get_stats(&st);
    if (rc != APEIRON_OK) return error_to_exception(rc);
    return Py_BuildValue("{s:K,s:K,s:K,s:K,s:K,s:K,s:i,s:i}", "written", (unsigned long long)st.written,
                         "dropped", (unsigned long long)st.dropped, "filtered",
                         (unsigned long long)st.filtered, "rotations",
                         (unsigned long long)st.rotations, "redactions",
                         (unsigned long long)st.redactions, "errors",
                         (unsigned long long)st.errors, "queue_depth", st.queue_depth, "initialized",
                         st.initialized);
}

static PyObject *py_last_error(PyObject *self, PyObject *unused)
{
    (void)self;
    (void)unused;
    return PyUnicode_FromString(apeiron_log_last_error());
}

static PyObject *py_redact(PyObject *self, PyObject *args)
{
    const char *text;
    char *out;
    int count = 0;
    PyObject *result;

    (void)self;
    if (!PyArg_ParseTuple(args, "s", &text)) return NULL;
    out = (char *)PyMem_Malloc(APEIRON_MAX_MESSAGE);
    if (out == NULL) return PyErr_NoMemory();
    if (apeiron_log_redact(text, out, APEIRON_MAX_MESSAGE, &count) != APEIRON_OK) {
        PyMem_Free(out);
        PyErr_SetString(PyExc_ValueError, "redaction failed");
        return NULL;
    }
    result = Py_BuildValue("(si)", out, count);
    PyMem_Free(out);
    return result;
}

static PyObject *py_strerror(PyObject *self, PyObject *args)
{
    int code;
    (void)self;
    if (!PyArg_ParseTuple(args, "i", &code)) return NULL;
    return PyUnicode_FromString(apeiron_log_strerror((apeiron_error)code));
}

static PyMethodDef logcore_methods[] = {
    {"init", (PyCFunction)py_init, METH_VARARGS | METH_KEYWORDS,
     "init(file_path=None, level=20, max_bytes=0, backup_count=0, console=False, "
     "queue_capacity=0) -> None\n\n"
     "Initialise the native log core.  Re-initialising flushes and closes the\n"
     "previous sink.  Raises OSError when the file cannot be opened."},
    {"write", py_write, METH_VARARGS,
     "write(level, module, message, exception=None) -> None\n\n"
     "Queue one record.  Returns immediately; I/O happens on the writer thread."},
    {"flush", py_flush, METH_NOARGS, "flush() -> None\n\nBlock until queued records are durable."},
    {"shutdown", py_shutdown, METH_NOARGS,
     "shutdown() -> None\n\nFlush, stop the writer thread and release resources."},
    {"set_level", py_set_level, METH_VARARGS, "set_level(level) -> None"},
    {"get_level", py_get_level, METH_NOARGS, "get_level() -> int"},
    {"stats", py_stats, METH_NOARGS, "stats() -> dict"},
    {"last_error", py_last_error, METH_NOARGS, "last_error() -> str"},
    {"redact", py_redact, METH_VARARGS, "redact(text) -> (masked_text, span_count)"},
    {"strerror", py_strerror, METH_VARARGS, "strerror(code) -> str"},
    {NULL, NULL, 0, NULL},
};

static void logcore_free(void *module)
{
    (void)module;
    apeiron_log_shutdown();
}

static struct PyModuleDef logcore_module = {
    PyModuleDef_HEAD_INIT,
    "apeiron._logcore",
    "Native logging core for Apeiron (rotating async sink with redaction).",
    -1,
    logcore_methods,
    NULL,
    NULL,
    NULL,
    logcore_free,
};

PyMODINIT_FUNC PyInit__logcore(void)
{
    PyObject *m = PyModule_Create(&logcore_module);
    if (m == NULL) return NULL;
    PyModule_AddIntConstant(m, "DEBUG", APEIRON_LOG_DEBUG);
    PyModule_AddIntConstant(m, "INFO", APEIRON_LOG_INFO);
    PyModule_AddIntConstant(m, "WARNING", APEIRON_LOG_WARNING);
    PyModule_AddIntConstant(m, "ERROR", APEIRON_LOG_ERROR);
    PyModule_AddIntConstant(m, "CRITICAL", APEIRON_LOG_CRITICAL);
    PyModule_AddStringConstant(m, "__version__", "1.0.0");
    return m;
}
