"""Entry point for the Nuitka build.

Nuitka warns when a ``__main__.py`` is compiled directly (it expects a package
directory instead), so the frozen build compiles this thin script.
"""

from __future__ import annotations

import sys

from apeiron.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
