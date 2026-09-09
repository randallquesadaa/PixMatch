# Contributing to PixMatch

Thanks for taking the time. This is a small, focused project; the bar is
"clear, tested, and safe for the user's files".

## Golden rule

**PixMatch never modifies, moves, renames or deletes a file on its own.** Every
destructive action requires an explicit, confirmed decision from the user and
must be undoable / logged. Any change that weakens this will be rejected.

## Development setup

```bash
git clone https://github.com/randallquesadaa/PixMatch
cd PixMatch
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Run the app: `python main.py` · Run the tests: `pytest`

Optional for the full test run: a system **FFmpeg** on `PATH` (video tests skip
without it).

## Before you open a pull request

```bash
ruff format .            # apply formatting
ruff check --fix .       # apply lint fixes
pytest                   # all tests green
bandit -c pyproject.toml -r app --severity-level medium
```

CI runs the same checks on Linux, macOS and Windows against Python 3.11–3.13.
A PR merges only when everything is green.

## Code style

- `ruff` is the single source of truth for formatting and linting (config in
  `pyproject.toml`). No separate black/isort/flake8.
- `app/core/*` must not import PySide6 — keep the engine headless and testable.
- Prefer standard library + Pillow. New third-party runtime dependencies need a
  good reason (bundle size, licensing, and the "only Pillow" promise all matter).
- Public functions get type hints and a one-line docstring.
- New behaviour comes with a test in `tests/`.

## Commits & versioning

- Small, focused commits with a descriptive message.
- To ship a release: bump `__version__` in `app/__init__.py` in your PR. When it
  lands on `main`, CI builds and publishes the Linux/macOS/Windows executables
  automatically. Don't create tags by hand.

## Reporting bugs

Use the issue templates. For anything security-related see
[`SECURITY.md`](SECURITY.md) instead.
