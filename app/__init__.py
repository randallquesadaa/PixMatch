"""PixMatch - a desktop tool for finding duplicate and near-duplicate images
and videos, plus a date-based rename tool.

The application never modifies, moves, renames or deletes files on its own.
Every destructive action (delete, rename) requires an explicit, confirmed
decision from the user and is written to an undoable operation history.
"""

__version__ = "0.7.0"  # Phases 1-6 + rename-by-date tool
APP_NAME = "PixMatch"
ORG_NAME = "PixMatch"
