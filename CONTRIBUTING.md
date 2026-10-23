# Contributing to Apeiron

Thanks for helping. This project values *evidence* over enthusiasm: a change is
ready when the tests that cover it were actually run.

## Before you start

- Read [`DISCLAIMER.md`](DISCLAIMER.md). Do not open changes that redistribute
  Minecraft game files, bypass licensing, or make offline profiles look like
  authenticated accounts.
- Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the layer boundaries and
  the Qt lifetime rules (they are there because they cost time to discover).
- For anything larger than a bug fix, open an issue first so we can agree on the
  approach.

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python setup.py build_ext --inplace
python -m apeiron
```

## Quality gates (must pass locally)

```bash
ruff check src tests tools
ruff format --check src tests tools
mypy src/apeiron
pytest -q
python tools/build_translations.py --check
QT_QPA_PLATFORM=offscreen python tools/gui_smoke.py /tmp/gui-root
```

If you touch the C core:

```bash
gcc -std=c11 -O2 -Wall -Wextra -pthread csrc/logcore.c csrc/test_logcore.c -o build/test_logcore
./build/test_logcore
```

If you touch the launch path, run the real end-to-end check as well:

```bash
xvfb-run -a python tools/e2e_minecraft.py --version 1.20.1 --data-dir /tmp/e2e
```

## Code conventions

- Python 3.10+, `from __future__ import annotations`, 100-column lines (ruff).
- No Qt imports outside `src/apeiron/ui/` and `src/apeiron/i18n/translator.py`.
- Domain code takes its dependencies (HTTP client, paths, repositories) as
  constructor arguments so it can be tested without a network or a real install.
- Errors are `ApeironError` subclasses with a stable `code`; user-facing text is
  translated, never composed from English fragments in the UI.
- Every new user-visible string needs an entry in the translation table
  (`tools/build_translations.py`) — `--check` enforces it.
- Log a secret never; the C core would mask it, but don't rely on that.

## QML conventions

- Material style comes from Qt Quick Controls; the design tokens in
  `Apeiron/Components/Theme.qml` fill the gaps between Qt's Material (M2) palette
  and Material 3 role names. Don't reference non-existent `Material.*` properties.
- No relative duplication of the components module: import `Apeiron.Components 1.0`.
- Give every interactive control an `Accessible.name` and keep keyboard focus
  order sensible (Ctrl+1…Ctrl+8 switch pages, Ctrl+L opens logs, Ctrl+Q quits).

## Commit and pull request

1. One logical change per commit; describe *why*, not only *what*.
2. Include the commands you ran and their results in the PR description.
3. State clearly what you could **not** verify (platform, account, network).
4. Do not claim a platform works if you only compiled for it.
5. Update `docs/` when behaviour, limitations or the support matrix changes.

## Licensing of contributions

By contributing you agree that your changes are licensed under the Mozilla
Public License 2.0. Add no code you cannot license this way.
