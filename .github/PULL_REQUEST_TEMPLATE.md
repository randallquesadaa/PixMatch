<!-- Describe what changes and why. Link any issue with "Closes #123". -->

## What & why

## Checklist

- [ ] `ruff format .` and `ruff check .` are clean
- [ ] `pytest` passes locally
- [ ] New behaviour has tests
- [ ] No file is modified / moved / renamed / deleted without an explicit,
      confirmed, undoable user action
- [ ] `app/core/*` still does not import PySide6
- [ ] If this is a release: `__version__` bumped in `app/__init__.py`
