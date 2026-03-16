"""CLI for distillrepo.

The CLI is intentionally explicit about trust boundaries:
- extracted facts such as symbols, signatures, paths, and static imports are
  higher confidence
- hotspots, root coverage, unused-code candidates, and compression choices are
  heuristic and should be treated as review guidance rather than truth
"""

from __future__ import annotations

import argparse
import ast
import tomllib
import warnings
from pathlib import Path

from . import __version__
from .api import write_outputs
from .models import Config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="distillrepo",
        description=(
            "Distill a repository into a compact LLM review bundle and a "
            "structured .distillrepo Intermediate Representation (IR)."
        ),
        epilog=(
            "Trust directly extracted facts more than heuristic judgments. "
            "Hotspots, root coverage, not-reached conclusions, unused-code "
            "candidates, and compression choices are guidance. "
            "Use --entry-point-module, --entry-point-function, exclusions, and "
            "--review-mode to override defaults when needed."
        ),
    )
    parser.add_argument("package_root", type=Path, help="Path to the package root to analyze.")
    parser.add_argument(
        "--entry-point-module",
        help="Override the inferred entry module path relative to the package root.",
    )
    parser.add_argument(
        "--entry-point-function",
        help="Override the inferred entry function or method name.",
    )
    parser.add_argument("--output", type=Path, help="Write the single-file LLM bundle to this path.")
    parser.add_argument("--stdout", action="store_true", help="Also print the generated LLM bundle to stdout.")
    parser.add_argument(
        "--review-mode",
        default="review",
        choices=["review", "architecture", "hotspots", "entrypath", "budgeted", "concat", "plain_concat", "full"],
        help=(
            "LLM bundle strategy. "
            "review=recommended balanced mix of analysis, selected full source, summaries, and signatures; "
            "architecture=compact structure-first map with lighter source detail; "
            "hotspots=prioritize complex or risky areas; "
            "entrypath=prioritize code closest to inferred roots; "
            "budgeted=more aggressive compression with less source retention; "
            "concat=cleaned source concat with lightweight static-analysis headers; "
            "plain_concat=cleaned source concat only, no added headers; "
            "full=largest review bundle with analysis sections plus broad full-source inclusion."
        ),
    )
    parser.add_argument("--call-graph-depth", type=int, default=4, help="Maximum call graph depth.")
    parser.add_argument(
        "--complexity-threshold",
        type=int,
        default=10,
        help="Threshold used by the hotspot heuristic; lower values surface more candidates.",
    )
    parser.add_argument("--max-tokens", type=int, help="Cap estimated tokens for LLM bundle source material.")
    parser.add_argument("--max-chars", type=int, help="Cap characters for LLM bundle source material.")
    parser.add_argument("--max-lines", type=int, help="Cap lines for LLM bundle source material.")
    parser.add_argument("--max-files", type=int, help="Cap included files in the LLM bundle source material.")
    parser.add_argument("--exclude-dir", action="append", default=[], help="Directory name to exclude.")
    parser.add_argument("--exclude-glob", action="append", default=[], help="Glob pattern to exclude.")
    parser.add_argument("--exclude-regex", action="append", default=[], help="Regex pattern to exclude.")
    parser.add_argument("--include-tests", action="store_true", help="Include tests in discovery.")
    parser.add_argument(
        "--no-jedi",
        action="store_true",
        help="Disable Jedi-based call resolution enrichment.",
    )
    parser.add_argument(
        "--no-radon",
        action="store_true",
        help="Disable Radon-based complexity and maintainability enrichment.",
    )
    parser.add_argument(
        "--no-vulture",
        action="store_true",
        help="Disable Vulture-based unused-code candidate detection.",
    )
    parser.add_argument(
        "--exclude-unreachable",
        action="store_true",
        help="Exclude modules not reached from the inferred root set from the LLM bundle.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main() -> int:
    warnings.filterwarnings("ignore", message=r"Tried to save a file to .*", module=r"parso\.cache")
    parser = build_parser()
    import sys

    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    args = parser.parse_args()
    project_input = args.package_root.resolve()
    project_root = _find_project_root(project_input)
    pyproject = _load_pyproject(project_root) if project_root is not None else {}
    raw_project_name = pyproject.get("project", {}).get("name") or project_input.name
    normalized_project_name = str(raw_project_name).replace("-", "_")
    script_target = _infer_script_target(pyproject, normalized_project_name)
    package_root = _infer_package_root(project_input, pyproject, script_target)
    package_name = package_root.name
    analysis_kind = "application" if script_target is not None else "library"
    entry_point_module = args.entry_point_module or _infer_entry_point_module(
        package_root, package_name, pyproject, script_target, analysis_kind
    )
    entry_point_function = args.entry_point_function or _infer_entry_point_function(
        package_root / entry_point_module, script_target, analysis_kind
    )

    default_excludes = Config.__dataclass_fields__["exclude_dirs"].default_factory()
    config = Config(
        package_root=package_root,
        package_name=package_name,
        entry_point_module=entry_point_module,
        entry_point_function=entry_point_function,
        output_path=args.output.resolve() if args.output else None,
        review_mode=args.review_mode,
        call_graph_depth=args.call_graph_depth,
        complexity_hotspot_threshold=args.complexity_threshold,
        use_jedi=not args.no_jedi,
        use_radon=not args.no_radon,
        use_vulture=not args.no_vulture,
        exclude_dirs=default_excludes | set(args.exclude_dir),
        exclude_globs=list(args.exclude_glob),
        exclude_regexes=list(args.exclude_regex),
        include_tests=args.include_tests,
        max_tokens=args.max_tokens,
        max_chars=args.max_chars,
        max_lines=args.max_lines,
        max_files=args.max_files,
        include_unreachable=not args.exclude_unreachable,
        analysis_kind=analysis_kind,
    )
    outputs = write_outputs(config)
    result = outputs["result"]
    bundle_path = outputs["bundle_path"]
    ir_dir = outputs["ir_dir"]
    _print_summary(result, bundle_path, ir_dir)
    if args.stdout:
        print("")
        print(bundle_path.read_text(encoding="utf-8"), end="")
    return 0


def _find_project_root(path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def _load_pyproject(project_root: Path) -> dict:
    return tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))


def _infer_package_root(project_input: Path, pyproject: dict, script_target: str | None) -> Path:
    if (project_input / "__init__.py").is_file():
        return project_input

    if script_target:
        module_name, _, _ = script_target.partition(":")
        match = _find_module_file(project_input, module_name)
        if match is not None:
            return _package_root_from_module_file(match, module_name)

    candidates: list[Path] = []
    project_name = pyproject.get("project", {}).get("name")
    if isinstance(project_name, str):
        normalized = project_name.replace("-", "_")
        candidates.append(project_input / normalized)
    candidates.append(project_input / project_input.name.replace("-", "_"))

    for candidate in candidates:
        if (candidate / "__init__.py").is_file():
            return candidate

    package_children = sorted(
        child for child in project_input.iterdir() if child.is_dir() and (child / "__init__.py").is_file()
    )
    if len(package_children) == 1:
        return package_children[0]

    return project_input


def _infer_entry_point_module(
    package_root: Path, package_name: str, pyproject: dict, script_target: str | None, analysis_kind: str
) -> str:
    if script_target is not None:
        module_name, _, _ = script_target.partition(":")
        for module_path in _module_name_candidates(module_name, package_name):
            if (package_root / module_path).is_file():
                return module_path
    if analysis_kind == "library" and (package_root / "__init__.py").is_file():
        return "__init__.py"

    for candidate in ("cli.py", "__main__.py", "__init__.py"):
        if (package_root / candidate).is_file():
            return candidate
    python_files = sorted(path.relative_to(package_root).as_posix() for path in package_root.rglob("*.py"))
    if python_files:
        return python_files[0]
    raise ValueError(f"No Python files found under package root: {package_root}")


def _infer_entry_point_function(entry_module_path: Path, script_target: str | None, analysis_kind: str) -> str | None:
    if analysis_kind == "library" and script_target is None:
        return None
    if not entry_module_path.is_file():
        raise ValueError(f"Entry module not found: {entry_module_path}")
    if script_target and ":" in script_target:
        _, _, callable_path = script_target.partition(":")
        callable_name = callable_path.split(".")[-1]
        if callable_name:
            return callable_name
    source = entry_module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(entry_module_path))
    top_level_names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_level_names.append(node.name)
    for candidate in ("main", "run", "cli"):
        if candidate in top_level_names:
            return candidate
    if top_level_names:
        return top_level_names[0]
    raise ValueError(f"No top-level functions found in inferred entry module: {entry_module_path}")


def _infer_script_target(pyproject: dict, package_name: str) -> str | None:
    project = pyproject.get("project", {})
    scripts = project.get("scripts")
    if not isinstance(scripts, dict) or not scripts:
        return None
    normalized_package_name = package_name.replace("-", "_")
    if normalized_package_name in scripts and isinstance(scripts[normalized_package_name], str):
        return scripts[normalized_package_name]
    for _, target in scripts.items():
        if isinstance(target, str) and target.startswith(f"{normalized_package_name}."):
            return target
    for _, target in scripts.items():
        if isinstance(target, str):
            return target
    return None


def _module_name_candidates(module_name: str, package_name: str) -> list[str]:
    normalized_package_name = package_name.replace("-", "_")
    if module_name == normalized_package_name:
        return ["__init__.py"]
    prefix = f"{normalized_package_name}."
    if not module_name.startswith(prefix):
        return []
    suffix = module_name[len(prefix) :]
    rel = suffix.replace(".", "/")
    return [f"{rel}.py", f"{rel}/__init__.py"]


def _find_module_file(project_root: Path, module_name: str) -> Path | None:
    module_parts = module_name.split(".")
    suffixes = [
        Path(*module_parts).with_suffix(".py"),
        Path(*module_parts) / "__init__.py",
    ]
    matches: list[Path] = []
    for path in project_root.rglob("*.py"):
        rel = path.relative_to(project_root)
        for suffix in suffixes:
            if len(rel.parts) >= len(suffix.parts) and rel.parts[-len(suffix.parts) :] == suffix.parts:
                matches.append(path)
                break
    matches = sorted(dict.fromkeys(matches))
    if len(matches) == 1:
        return matches[0]
    return None


def _package_root_from_module_file(module_file: Path, module_name: str) -> Path:
    top_package = module_name.split(".")[0]
    current = module_file.parent if module_file.name != "__init__.py" else module_file.parent
    while current.name != top_package and current.parent != current:
        current = current.parent
    return current


def _print_summary(result, bundle_path: Path, ir_dir: Path) -> None:
    original_tokens = result.original_tokens
    bundle_tokens = result.bundle_tokens
    saved_tokens = max(0, original_tokens - bundle_tokens)
    saved_percent = (saved_tokens / original_tokens * 100.0) if original_tokens else 0.0
    retained_percent = (bundle_tokens / original_tokens * 100.0) if original_tokens else 0.0
    compression = (original_tokens / bundle_tokens) if bundle_tokens else 0.0
    print(
        f"Analyzed: {len(result.files)} files, "
        f"{sum(len(file_info.functions) for file_info in result.files.values())} symbols, "
        f"{len(result.files)} modules"
    )
    print(f"Analysis kind: {result.config.analysis_kind}")
    print(f"Roots analyzed: {len(result.root_modules)}")
    print(f"Reached from roots: {result.reachable_count}, not reached: {result.unreachable_count}")
    print(f"Cycles: {len(result.cycles)}")
    if result.unused_candidates:
        print(f"Possible unused symbols: {len(result.unused_candidates)}")
    if result.hotspots:
        top = result.hotspots[0]
        print(
            f"Top hotspot: {top.module_path}:{top.qualified_name} "
            f"(score={top.hotspot_score:.1f}, cc={top.cyclomatic_complexity})"
        )
    print("")
    print(f"Original size: {original_tokens:,} tokens")
    print(f"Distilled size: {bundle_tokens:,} tokens")
    print(f"Saved: {saved_tokens:,} tokens ({saved_percent:.1f}%)")
    print(f"Retained: {retained_percent:.1f}% of estimated original tokens")
    if compression:
        print(f"Compression: {compression:.1f}x")
    print("")
    print(f"LLM bundle: {bundle_path}")
    print(f"IR: {ir_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
