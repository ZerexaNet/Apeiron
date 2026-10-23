"""Build configuration for the Apeiron native logging extension.

The Python packaging metadata lives in ``pyproject.toml``; this module only
declares the C extension so that ``pip install -e .`` and the packaging
pipeline can compile it in place.
"""

from __future__ import annotations

import sys

from setuptools import Extension, setup

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    extra_compile_args = ["/O2", "/W3"]
else:
    extra_compile_args = [
        "-std=c11",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Wno-unused-parameter",
        "-fno-strict-aliasing",
    ]

libraries: list[str] = []
if not IS_WINDOWS:
    libraries.append("pthread")

logcore = Extension(
    "apeiron._logcore",
    sources=["csrc/logcore.c", "csrc/_logcore_module.c"],
    include_dirs=["csrc"],
    extra_compile_args=extra_compile_args,
    libraries=libraries,
)

setup(ext_modules=[logcore])
