from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from .analysis import estimate_tokens, normalize_source_whitespace
from .models import Config, SupplementalFile


MAX_TEXT_FILE_BYTES = 1_000_000
CODE_OR_CONFIG_SUFFIXES = {
    ".bash",
    ".cfg",
    ".conf",
    ".css",
    ".csv",
    ".env",
    ".html",
    ".ini",
    ".jinja",
    ".jinja2",
    ".j2",
    ".js",
    ".json",
    ".jsx",
    ".mako",
    ".py",
    ".pyi",
    ".rst",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}
CODE_OR_CONFIG_FILENAMES = {
    ".dockerignore",
    ".env",
    ".env.example",
    ".gitignore",
    "Dockerfile",
    "Makefile",
    "Procfile",
}
SKIP_SUFFIXES = {
    ".a",
    ".class",
    ".dll",
    ".dylib",
    ".exe",
    ".gif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".o",
    ".pdf",
    ".png",
    ".pyc",
    ".pyo",
    ".so",
    ".webp",
    ".zip",
}


@dataclass(slots=True)
class SelectedFiles:
    root: Path
    selection: str
    python_paths: list[Path]
    supplemental_files: list[SupplementalFile]
    skipped: list[str] = field(default_factory=list)


def discover_selected_files(config: Config) -> SelectedFiles:
    root = config.package_root.resolve()
    if config.include_git_tracked:
        paths, selection_root = _git_tracked_paths(root)
        selection = "git tracked files"
    else:
        selection_root = root
        paths = _selected_paths(root, config.include_paths)
        selection = "selected paths"

    declared_resources = _declared_resource_patterns(selection_root)
    python_paths: list[Path] = []
    supplemental_files: list[SupplementalFile] = []
    skipped: list[str] = []
    seen: set[Path] = set()
    for path in sorted(paths):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        rel = resolved.relative_to(selection_root).as_posix()
        skip_reason = _skip_reason(resolved, rel, config, declared_resources)
        if skip_reason:
            skipped.append(f"{rel}: {skip_reason}")
            continue
        if resolved.suffix.lower() == ".py":
            python_paths.append(resolved)
            continue
        supplemental = _read_text_file(resolved, rel)
        if supplemental is None:
            skipped.append(f"{rel}: binary or non-UTF-8")
            continue
        supplemental_files.append(supplemental)

    return SelectedFiles(
        root=selection_root,
        selection=selection,
        python_paths=python_paths,
        supplemental_files=supplemental_files,
        skipped=skipped,
    )


def _git_tracked_paths(root: Path) -> tuple[list[Path], Path]:
    git_root = _git_root(root)
    relative_root = root.resolve().relative_to(git_root)
    pathspec = "." if relative_root == Path(".") else relative_root.as_posix()
    command = ["git", "-C", str(git_root), "ls-files", "-z", "--", pathspec]
    try:
        result = subprocess.run(command, check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"Unable to list Git-tracked files under {root}") from exc
    rel_paths = [item for item in result.stdout.decode("utf-8").split("\0") if item]
    return [git_root / rel_path for rel_path in rel_paths], root.resolve()


def _git_root(root: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"Not a Git repository: {root}") from exc
    return Path(result.stdout.strip()).resolve()


def _selected_paths(root: Path, include_paths: list[Path]) -> list[Path]:
    if not include_paths:
        raise ValueError("At least one --include-path value is required unless --git is used.")
    paths: list[Path] = []
    for item in include_paths:
        selected = item if item.is_absolute() else root / item
        if not selected.exists():
            raise ValueError(f"Included path does not exist: {selected}")
        if selected.is_file():
            paths.append(selected)
            continue
        paths.extend(path for path in selected.rglob("*") if path.is_file())
    return paths


def _skip_reason(path: Path, relative_path: str, config: Config, declared_resources: set[str]) -> str | None:
    parts = set(Path(relative_path).parts)
    regexes = [re.compile(pattern) for pattern in config.exclude_regexes]
    if not path.exists():
        return "missing from working tree"
    if not path.is_file():
        return "not a regular file"
    if parts & config.exclude_dirs:
        return "excluded directory"
    if any(fnmatch(relative_path, pattern) for pattern in config.exclude_globs):
        return "excluded glob"
    if any(pattern.search(relative_path) for pattern in regexes):
        return "excluded regex"
    if config.include_git_tracked:
        return None
    if _is_declared_resource(relative_path, declared_resources):
        return None
    if path.suffix.lower() in SKIP_SUFFIXES:
        return "compiled, binary, or cache suffix"
    if not _looks_like_code_or_config(path):
        return "non-code file not declared in pyproject"
    if path.stat().st_size > MAX_TEXT_FILE_BYTES:
        return f"larger than {MAX_TEXT_FILE_BYTES} bytes"
    return None


def _looks_like_code_or_config(path: Path) -> bool:
    if path.name in CODE_OR_CONFIG_FILENAMES:
        return True
    return path.suffix.lower() in CODE_OR_CONFIG_SUFFIXES


def _declared_resource_patterns(root: Path) -> set[str]:
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        return set()
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return set()

    patterns: set[str] = set()
    project = pyproject.get("project", {})
    if isinstance(project, dict):
        readme = project.get("readme")
        if isinstance(readme, str):
            patterns.add(readme)
        elif isinstance(readme, dict) and isinstance(readme.get("file"), str):
            patterns.add(readme["file"])

        license_value = project.get("license")
        if isinstance(license_value, dict) and isinstance(license_value.get("file"), str):
            patterns.add(license_value["file"])

        license_files = project.get("license-files")
        if isinstance(license_files, list):
            patterns.update(item for item in license_files if isinstance(item, str))

    tool = pyproject.get("tool", {})
    if isinstance(tool, dict):
        hatch = tool.get("hatch", {})
        if isinstance(hatch, dict):
            build = hatch.get("build", {})
            if isinstance(build, dict):
                _collect_build_patterns(build, patterns)
                targets = build.get("targets", {})
                if isinstance(targets, dict):
                    for target in targets.values():
                        if isinstance(target, dict):
                            _collect_build_patterns(target, patterns)

        setuptools = tool.get("setuptools", {})
        if isinstance(setuptools, dict):
            package_data = setuptools.get("package-data", {})
            if isinstance(package_data, dict):
                for values in package_data.values():
                    if isinstance(values, list):
                        patterns.update(item for item in values if isinstance(item, str))

    return {pattern.replace("\\", "/") for pattern in patterns}


def _collect_build_patterns(table: dict, patterns: set[str]) -> None:
    for key in ("include", "artifacts"):
        values = table.get(key)
        if isinstance(values, list):
            patterns.update(item for item in values if isinstance(item, str))
    force_include = table.get("force-include")
    if isinstance(force_include, dict):
        for source, target in force_include.items():
            if isinstance(source, str):
                patterns.add(source)
            if isinstance(target, str):
                patterns.add(target)


def _is_declared_resource(relative_path: str, declared_resources: set[str]) -> bool:
    for pattern in declared_resources:
        normalized = pattern.rstrip("/")
        if not normalized:
            continue
        if relative_path == normalized or fnmatch(relative_path, normalized):
            return True
        if "/" not in normalized and fnmatch(Path(relative_path).name, normalized):
            return True
    return False


def _read_text_file(path: Path, relative_path: str) -> SupplementalFile | None:
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    normalized = normalize_source_whitespace(text.splitlines())
    return SupplementalFile(
        path=path,
        relative_path=relative_path,
        text=normalized,
        size_bytes=len(data),
        estimated_tokens=estimate_tokens(normalized),
    )
