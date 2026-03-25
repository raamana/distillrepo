from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class CallSite:
    raw_name: str
    lineno: int
    col_offset: int
    end_lineno: int | None = None
    end_col_offset: int | None = None
    expr_text: str | None = None


@dataclass(slots=True)
class ResolvedCall:
    source_raw_name: str
    target_module_path: str | None
    target_qualified_name: str | None
    resolution_kind: str
    confidence: float


@dataclass(slots=True)
class FunctionInfo:
    name: str
    qualified_name: str
    file_path: Path
    module_path: str
    lineno: int
    end_lineno: int
    is_method: bool
    class_name: str | None
    docstring: str | None
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    raw_calls: list[str] = field(default_factory=list)
    call_sites: list[CallSite] = field(default_factory=list)
    resolved_calls: list[ResolvedCall] = field(default_factory=list)
    cyclomatic_complexity: int = 1
    hotspot_score: float = 0.0
    signature_text: str | None = None


@dataclass(slots=True)
class UnusedCandidate:
    name: str
    typ: str
    path: str
    first_lineno: int
    last_lineno: int | None
    size: int | None
    confidence: int | None
    message: str | None
    module_path: str | None = None
    qualified_name: str | None = None
    symbol_id: str | None = None


@dataclass(slots=True)
class FileInfo:
    path: Path
    relative_path: str
    module_path: str
    source: str
    cleaned_source: str
    lines: int
    sloc: int
    module_docstring: str | None
    classes: list[str] = field(default_factory=list)
    functions: dict[str, FunctionInfo] = field(default_factory=dict)
    imports: list[str] = field(default_factory=list)
    imported_by: list[str] = field(default_factory=list)
    import_bindings: dict[str, str] = field(default_factory=dict)
    class_bases: dict[str, list[str]] = field(default_factory=dict)
    avg_complexity: float = 0.0
    max_complexity: int = 0
    max_complexity_func: str = ""
    maintainability_index: float = 100.0
    fan_in: int = 0
    fan_out: int = 0
    depth_from_entry: int = -1
    reached_by_roots: list[str] = field(default_factory=list)
    importance_score: float = 0.0
    inclusion_mode: str = "full"
    estimated_tokens: int = 0
    why_important: list[str] = field(default_factory=list)
    parse_error: str | None = None


@dataclass(slots=True)
class Config:
    package_root: Path
    package_name: str
    entry_point_module: str
    entry_point_function: str | None
    output_path: Path | None = None
    write_ir: bool = True
    review_mode: str = "review"
    call_graph_depth: int = 4
    complexity_hotspot_threshold: int = 10
    use_jedi: bool = True
    use_radon: bool = True
    use_vulture: bool = True
    exclude_dirs: set[str] = field(
        default_factory=lambda: {
            "__pycache__",
            ".git",
            ".venv",
            "venv",
            "build",
            "dist",
            ".distillrepo",
            ".mypy_cache",
            ".pytest_cache",
        }
    )
    exclude_globs: list[str] = field(default_factory=list)
    exclude_regexes: list[str] = field(default_factory=list)
    include_tests: bool = False
    max_tokens: int | None = None
    max_chars: int | None = None
    max_lines: int | None = None
    max_files: int | None = None
    include_unreachable: bool = True
    output_format: str = "py"
    header_strip_pattern: str = r"^\s*#\s*=+\s*FILE:.*$"
    analysis_kind: str = "application"


@dataclass(slots=True)
class AnalysisResult:
    config: Config
    files: dict[str, FileInfo]
    import_graph: dict[str, list[str]]
    call_graph_lines: list[str]
    import_tree_lines: list[str]
    cycles: list[list[str]]
    ordered_modules: list[str]
    root_modules: list[str]
    root_kinds: dict[str, str]
    root_reachability: dict[str, int]
    hotspots: list[FunctionInfo]
    observations: list[str]
    total_lines: int
    total_sloc: int
    reachable_count: int
    unreachable_count: int
    warnings: list[str] = field(default_factory=list)
    original_tokens: int = 0
    bundle_tokens: int = 0
    unused_candidates: list[UnusedCandidate] = field(default_factory=list)
