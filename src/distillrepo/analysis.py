from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

from .models import CallSite, Config, FileInfo, FunctionInfo


def analyze_files(paths: list[Path], config: Config) -> dict[str, FileInfo]:
    files: dict[str, FileInfo] = {}
    for path in paths:
        file_info = _analyze_file(path, config)
        files[file_info.module_path] = file_info

    known_modules = set(files)
    for file_info in files.values():
        normalized: set[str] = set()
        for target in file_info.imports:
            module = _normalize_to_known_module(target, known_modules)
            if module is not None:
                normalized.add(module)
        file_info.imports = sorted(normalized)
    return files


def _analyze_file(path: Path, config: Config) -> FileInfo:
    source = path.read_text(encoding="utf-8")
    cleaned_source = clean_source(source, config.header_strip_pattern)
    rel = path.resolve().relative_to(config.package_root.resolve())
    module_path = module_path_for_file(rel, config.package_name)
    file_info = FileInfo(
        path=path.resolve(),
        relative_path=rel.as_posix(),
        module_path=module_path,
        source=source,
        cleaned_source=cleaned_source,
        lines=len(source.splitlines()),
        sloc=count_sloc(source),
        module_docstring=None,
    )

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        file_info.parse_error = str(error)
        file_info.estimated_tokens = estimate_tokens(cleaned_source)
        file_info.maintainability_index = 0.0
        return file_info

    file_info.module_docstring = ast.get_docstring(tree)
    visitor = ModuleAnalyzer(file_info)
    visitor.visit(tree)
    compute_basic_metrics(file_info)
    file_info.estimated_tokens = estimate_tokens(cleaned_source)
    return file_info


class ModuleAnalyzer(ast.NodeVisitor):
    def __init__(self, file_info: FileInfo) -> None:
        self.file_info = file_info
        self.module_path = file_info.module_path
        self.class_stack: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            target = alias.name
            self.file_info.imports.append(target)
            local_name = alias.asname or alias.name.split(".")[0]
            self.file_info.import_bindings[local_name] = target

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base_module = resolve_import_from(self.module_path, node.level, node.module)
        if base_module:
            self.file_info.imports.append(base_module)
        for alias in node.names:
            if alias.name == "*":
                continue
            if base_module:
                symbol_target = f"{base_module}.{alias.name}"
                self.file_info.import_bindings[alias.asname or alias.name] = symbol_target
                self.file_info.imports.append(symbol_target)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.file_info.classes.append(node.name)
        self.file_info.class_bases[node.name] = [safe_unparse(base) for base in node.bases]
        self.class_stack.append(node.name)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.visit(child)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node)

    def _record_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        class_name = self.class_stack[-1] if self.class_stack else None
        qualified_name = f"{class_name}.{node.name}" if class_name else node.name
        function = FunctionInfo(
            name=node.name,
            qualified_name=qualified_name,
            file_path=self.file_info.path,
            module_path=self.module_path,
            lineno=node.lineno,
            end_lineno=getattr(node, "end_lineno", node.lineno),
            is_method=class_name is not None,
            class_name=class_name,
            docstring=ast.get_docstring(node),
            decorators=[safe_unparse(item) for item in node.decorator_list],
            is_async=isinstance(node, ast.AsyncFunctionDef),
            signature_text=render_signature(node, class_name),
        )
        collector = FunctionCallCollector()
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            collector.visit(child)
        function.call_sites = collector.calls
        function.raw_calls = [call.raw_name for call in collector.calls]
        function.cyclomatic_complexity = estimate_complexity(node)
        self.file_info.functions[qualified_name] = function


class FunctionCallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[CallSite] = []

    def visit_Call(self, node: ast.Call) -> None:
        expr = node.func
        self.calls.append(
            CallSite(
                raw_name=call_name(expr),
                lineno=getattr(expr, "lineno", node.lineno),
                col_offset=getattr(expr, "col_offset", node.col_offset),
                end_lineno=getattr(expr, "end_lineno", getattr(node, "end_lineno", None)),
                end_col_offset=getattr(expr, "end_col_offset", getattr(node, "end_col_offset", None)),
                expr_text=safe_unparse(expr),
            )
        )
        self.generic_visit(node)


def module_path_for_file(relative_path: Path, package_name: str) -> str:
    parts = list(relative_path.parts)
    if parts[-1] == "__init__.py":
        module_parts = parts[:-1]
    else:
        module_parts = parts[:-1] + [relative_path.stem]
    return ".".join([package_name, *module_parts]).rstrip(".")


def clean_source(source: str, header_pattern: str) -> str:
    pattern = re.compile(header_pattern)
    lines = source.splitlines()
    index = 0
    while index < len(lines) and pattern.match(lines[index]):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return "\n".join(lines[index:]).strip() + ("\n" if lines[index:] else "")


def count_sloc(source: str) -> int:
    total = 0
    for line in source.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            total += 1
    return total


def resolve_import_from(module_path: str, level: int, module: str | None) -> str | None:
    if level == 0:
        return module
    package_context = module_path if module_path.endswith("__init__") else module_path.rsplit(".", 1)[0]
    suffix = module or ""
    relative_name = "." * level + suffix
    try:
        return importlib.util.resolve_name(relative_name, package_context)
    except (ImportError, ValueError):
        return None


def _normalize_to_known_module(target: str, known_modules: set[str]) -> str | None:
    candidate = target
    while candidate:
        if candidate in known_modules:
            return candidate
        if "." not in candidate:
            break
        candidate = candidate.rsplit(".", 1)[0]
    return None


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{call_name(node.value)}.{node.attr}"
    return safe_unparse(node)


def safe_unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return node.__class__.__name__


def render_signature(node: ast.FunctionDef | ast.AsyncFunctionDef, class_name: str | None) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    try:
        args_text = ast.unparse(node.args)
    except Exception:
        args_text = "..."
    return_annotation = ""
    if node.returns is not None:
        return_annotation = f" -> {safe_unparse(node.returns)}"
    name = node.name if class_name is None else f"{class_name}.{node.name}"
    return f"{prefix} {name}({args_text}){return_annotation}"


def estimate_complexity(node: ast.AST) -> int:
    complexity = 1
    branch_nodes = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        ast.ExceptHandler,
        ast.BoolOp,
        ast.IfExp,
        ast.Match,
        ast.comprehension,
    )
    for child in ast.walk(node):
        if isinstance(child, branch_nodes):
            complexity += 1
    return complexity


def compute_basic_metrics(file_info: FileInfo) -> None:
    complexities = [function.cyclomatic_complexity for function in file_info.functions.values()]
    if complexities:
        file_info.avg_complexity = sum(complexities) / len(complexities)
        file_info.max_complexity = max(complexities)
        for function in file_info.functions.values():
            if function.cyclomatic_complexity == file_info.max_complexity:
                file_info.max_complexity_func = function.qualified_name
                break
    size_penalty = min(file_info.sloc / 300, 35)
    complexity_penalty = min(file_info.max_complexity * 2.5, 45)
    file_info.maintainability_index = max(0.0, round(100 - size_penalty - complexity_penalty, 1))


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)
