from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

from .models import Config


def discover_python_files(config: Config) -> list[Path]:
    files: list[Path] = []
    root = config.package_root.resolve()
    regexes = [re.compile(pattern) for pattern in config.exclude_regexes]
    excluded_dirs = set(config.exclude_dirs)
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        rel_posix = rel.as_posix()
        parts = set(rel.parts)
        if parts & excluded_dirs:
            continue
        if any(fnmatch(rel_posix, pattern) for pattern in config.exclude_globs):
            continue
        if any(pattern.search(rel_posix) for pattern in regexes):
            continue
        if not config.include_tests and _looks_like_test(rel):
            continue
        files.append(path)
    return files


def _looks_like_test(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    if "tests" in parts or "test" in parts:
        return True
    name = path.name.lower()
    if name.startswith("distilled.") and name.endswith(".py"):
        return True
    return name.startswith("test_") or name.endswith("_test.py")
