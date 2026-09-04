from __future__ import annotations

from pathlib import Path


SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}

DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


def classify_scope(path: Path, root: Path) -> str:
    try:
        parts = [part.lower() for part in path.relative_to(root).parts]
    except ValueError:
        parts = [part.lower() for part in path.parts]

    if any(part in {"test", "tests", "testing", "_archive"} for part in parts):
        return "test"
    if "prod" in parts:
        return "prod"
    return "root"


def discover_sqlite_files(
    root: Path,
    excluded_dirs: set[str] | None = None,
) -> list[Path]:
    excluded = DEFAULT_EXCLUDED_DIRS | (excluded_dirs or set())
    found: list[Path] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            relative_parts = path.parts
        if any(part in excluded for part in relative_parts[:-1]):
            continue
        if path.suffix.lower() in SQLITE_SUFFIXES:
            found.append(path)

    return sorted(found, key=lambda item: item.as_posix().lower())
